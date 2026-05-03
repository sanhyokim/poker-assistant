"""Chart-anchored preflop delta policy.

The chart remains the anchor policy. The LLM may return small probability
deltas only when enough opponent data is available.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


class PreflopDeltaPolicy:
    """Chart-anchored delta policy for preflop adjustments."""

    def __init__(
        self,
        llm_pipeline: Any | None,
        config: JsonDict,
    ) -> None:
        """Initialize delta policy.

        Args:
            llm_pipeline: LLMPipeline instance, or None when disabled.
            config: Parsed config.yaml dictionary.
        """
        delta_config = config.get("preflop_delta", {})
        self.llm_pipeline = llm_pipeline
        self.enabled = bool(delta_config.get("enabled", True))
        self.sample_threshold_low = int(delta_config.get("sample_threshold_low", 30))
        self.sample_threshold_high = int(delta_config.get("sample_threshold_high", 100))
        self.shift_cap_low = float(delta_config.get("shift_cap_low", 0.05))
        self.shift_cap_high = float(delta_config.get("shift_cap_high", 0.10))
        self.timeout_ms = int(delta_config.get("timeout_ms", 1000))

    def should_apply(self, villain_stats: JsonDict | None) -> bool:
        """Return whether delta policy should be applied."""
        if not self.enabled or self.llm_pipeline is None:
            return False
        if not villain_stats:
            return False
        total_hands = villain_stats.get("total_hands", 0)
        if not isinstance(total_hands, (int, float)):
            return False
        return total_hands >= self.sample_threshold_low

    def get_shift_cap(self, villain_stats: JsonDict) -> float:
        """Return the maximum allowed absolute delta."""
        total_hands = villain_stats.get("total_hands", 0)
        if isinstance(total_hands, (int, float)) and total_hands >= self.sample_threshold_high:
            return self.shift_cap_high
        return self.shift_cap_low

    def apply(
        self,
        hero_position: str,
        hero_hand: str,
        scenario: str,
        chart_probs: dict[str, float],
        villain_stats: JsonDict,
        effective_stack_bb: float = 100.0,
        action_prefix: list[str] | None = None,
    ) -> dict[str, float]:
        """Apply an LLM delta to chart probabilities when valid."""
        if not self.should_apply(villain_stats):
            return chart_probs

        shift_cap = self.get_shift_cap(villain_stats)
        started_at = time.perf_counter()

        try:
            delta = self._request_delta(
                hero_position=hero_position,
                hero_hand=hero_hand,
                scenario=scenario,
                chart_probs=chart_probs,
                villain_stats=villain_stats,
                effective_stack_bb=effective_stack_bb,
                action_prefix=action_prefix or [],
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            logger.warning(
                "Delta policy LLM request failed (%.0fms): %s",
                elapsed_ms,
                exc,
            )
            return chart_probs

        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        if elapsed_ms > self.timeout_ms:
            logger.warning(
                "Delta policy timed out (%.0fms > %dms), using chart only",
                elapsed_ms,
                self.timeout_ms,
            )
            return chart_probs

        validated = self._validate_delta(delta, chart_probs, shift_cap)
        if validated is None:
            logger.warning("Delta policy validation failed, using chart only")
            return chart_probs

        adjusted = self._apply_delta(chart_probs, validated)
        logger.info(
            "Delta policy applied: %s %s %s, delta=%s, adjusted=%s (%.0fms)",
            hero_position,
            hero_hand,
            scenario,
            validated,
            adjusted,
            elapsed_ms,
        )
        return adjusted

    def _request_delta(
        self,
        hero_position: str,
        hero_hand: str,
        scenario: str,
        chart_probs: dict[str, float],
        villain_stats: JsonDict,
        effective_stack_bb: float,
        action_prefix: list[str],
    ) -> dict[str, float] | None:
        """Request a delta probability map from the LLM pipeline."""
        if self.llm_pipeline is None:
            return None

        anonymize_stats = getattr(self.llm_pipeline, "_anonymize_stats", None)
        safe_stats = (
            anonymize_stats(villain_stats)
            if callable(anonymize_stats)
            else dict(villain_stats)
        )
        request = {
            "hero_position": hero_position,
            "hero_hand": hero_hand,
            "effective_stack_bb": effective_stack_bb,
            "action_prefix": action_prefix,
            "chart_anchor_probs": chart_probs,
            "villain_stats": safe_stats,
            "scenario": scenario,
        }
        response = self.llm_pipeline.request_preflop_delta(request)
        if response is None:
            return None

        delta_probs = response.get("delta_probs")
        if not isinstance(delta_probs, dict):
            return None

        return {str(action): float(delta) for action, delta in delta_probs.items()}

    def _validate_delta(
        self,
        delta: dict[str, float] | None,
        chart_probs: dict[str, float],
        shift_cap: float,
    ) -> dict[str, float] | None:
        """Validate and clamp LLM deltas."""
        if delta is None:
            return None

        clamped: dict[str, float] = {}
        for action in chart_probs:
            value = delta.get(action, 0.0)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return None
            clamped[action] = float(value)

        for action, value in clamped.items():
            if chart_probs.get(action, 0.0) == 0.0 and value > 0:
                logger.warning(
                    "Delta policy: positive delta for pure node '%s', rejecting",
                    action,
                )
                return None

        for action in clamped:
            clamped[action] = max(-shift_cap, min(shift_cap, clamped[action]))

        delta_sum = sum(clamped.values())
        if abs(delta_sum) > 0.01 and clamped:
            target = -delta_sum
            for _ in range(len(clamped) * 2):
                if abs(target) <= 0.01:
                    break
                candidates: list[tuple[str, float]] = []
                for action, value in clamped.items():
                    if target > 0 and chart_probs.get(action, 0.0) == 0.0:
                        continue
                    room = shift_cap - value if target > 0 else value + shift_cap
                    if room > 0:
                        candidates.append((action, room))
                if not candidates:
                    break

                share = abs(target) / len(candidates)
                moved = 0.0
                for action, room in candidates:
                    step = min(room, share)
                    signed_step = step if target > 0 else -step
                    clamped[action] += signed_step
                    moved += signed_step
                target -= moved

            if abs(sum(clamped.values())) > 0.01:
                return None

        for action, value in clamped.items():
            adjusted_value = chart_probs.get(action, 0.0) + value
            if adjusted_value < 0.0 or adjusted_value > 1.0:
                return None

        return clamped

    def _apply_delta(
        self,
        chart_probs: dict[str, float],
        delta: dict[str, float],
    ) -> dict[str, float]:
        """Apply validated delta to chart probabilities."""
        adjusted: dict[str, float] = {}
        for action, probability in chart_probs.items():
            adjusted[action] = max(0.0, min(1.0, probability + delta.get(action, 0.0)))
        return adjusted
