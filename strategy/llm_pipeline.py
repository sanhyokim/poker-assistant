"""LLM pipeline for range estimation, exploit adjustment, and explanations.

OpenRouter API is used for all LLM calls. Every public method returns a
structured fallback instead of raising when the model is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
from pydantic import BaseModel, ValidationError

from core.game_state import ActionRecord, GameState
from strategy.llm_schemas import (
    ExploitAdjustmentResponse,
    MultiwayDecisionResponse,
    PreflopDeltaResponse,
    RangeEstimationResponse,
    ReasonGenerationResponse,
)


logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]
BASELINE_RANGES_PATH = Path(__file__).with_name("baseline_ranges.json")
ALLOWED_RANGE_RE = re.compile(r"^[A-Za-z0-9+\-,:. ]+$")
REASON_JP_INSTRUCTION = "reasonフィールドは日本語で簡潔に記述してください（1-2文）。"

RANGE_ESTIMATION_PROMPT = """You are a GTO poker range estimator for 6-max No-Limit Hold'em.

Given the game context below, estimate the opponent's hand range in PioSOLVER-compatible format.

## Game Context
- Board: {board}
- Street: {street}
- Hero Position: {hero_position} ({ip_or_oop})
- Opponent Position: {opponent_position}
- Pot: {pot} chips
- Effective Stack: {effective_stack} chips
- SPR: {spr:.1f}

## Action History
{action_history}

## Opponent Stats (from database)
- VPIP: {vpip}% (hands: {total_hands})
- PFR: {pfr}%
- 3-Bet%: {three_bet_pct}%
- C-Bet Flop%: {cbet_flop_pct}%
- Fold to 3-Bet%: {fold_to_three_bet}%
- Went to Showdown%: {wtsd}%
- Style: {long_term_style}
{freshness_warning}

## Baseline Range (default for this position/action)
OOP: {baseline_range_oop}
IP: {baseline_range_ip}

## Instructions
1. Start from the baseline range above.
2. Adjust based on the opponent's stats:
   - High VPIP/low PFR -> wider passive range (more suited connectors, weak aces)
   - Low VPIP/high PFR -> tighter aggressive range
   - High 3-bet% -> wider 3-bet range
   - High fold-to-3bet -> narrower continuing range
3. Adjust based on the action history on this street.
4. Output ONLY valid PioSOLVER range strings.

Respond with JSON only:
{{"range_oop": "<range>", "range_ip": "<range>", "adjustments_made": "<brief explanation>"}}"""

EXPLOIT_ADJUSTMENT_PROMPT = """You are a poker exploitation advisor for 6-max NLHE.

The GTO solver has produced the following optimal strategy.
Suggest minor adjustments to exploit the opponent's tendencies.

## Solver Output
- Actions: {actions}
- GTO Strategy: {average_strategy}
- Hero Hand: {hero_hand}
- Hero Equity: {hero_equity:.1f}%
- Hero EV: {hero_ev:.1f}

## Opponent Stats
- VPIP: {vpip}%, PFR: {pfr}%
- Fold to C-Bet: {fold_to_cbet}%
- Went to Showdown: {wtsd}%
- Style: {long_term_style}
{freshness_warning}

## Board: {board}
## Pot: {pot}, Effective Stack: {effective_stack}

## Instructions
1. The solver output is the baseline. Do NOT deviate drastically.
2. Suggest frequency shifts of at most +/-15% from GTO frequencies.
3. If opponent folds too much -> increase bluff frequency slightly.
4. If opponent calls too much -> increase value bet frequency, reduce bluffs.
5. If stats sample size < 10 hands -> make minimal adjustments.
6. {reason_jp_instruction}

Respond with JSON only:
{{"adjusted_action": "<action>", "adjusted_size": "<size or null>",
"confidence": "<high/medium/low>", "reasoning": "<1-2 sentences>"}}"""

MULTIWAY_DECISION_PROMPT = """You are a poker advisor for multiway pots in 6-max NLHE.
Solver cannot be used (3+ players). Use the equity data and opponent stats to recommend an action.

## Game Context
- Board: {board}
- Street: {street}
- Hero Hand: {hero_hand}
- Hero Position: {hero_position}
- Hero IP/OOP: {hero_ip_or_oop}
- Pot: {pot} chips
- Hero Stack: {hero_stack} chips
- Hero Current Bet: {hero_current_bet} chips
- Number of Players: {num_players}
- SPR: {spr:.1f}

## Betting Situation
- Facing Bet: {facing_bet} chips
- Call Amount: {call_amount} chips
- Raw Call Amount: {raw_call_amount} chips
- Effective Call Amount: {effective_call_amount} chips
- Hero Call Is All-In: {hero_call_is_all_in}
- Pot After Call: {pot_after_call} chips
- Required Equity To Call: {required_equity:.1f}%

## Hero Equity (Monte Carlo, 10000 simulations)
- Equity vs all opponents: {equity:.1f}%

## Preflop Action History
{preflop_action_history}

## Current Street Action History
{action_history}

## Opponent Profiles
{opponent_profiles}

## Instructions
1. "Conservative in multiway" means avoiding thin bluffs and thin value-raises,
   NOT folding strong made hands that have clear pot odds.
2. When facing a bet, compare Hero equity with required equity:
   - If Hero equity significantly exceeds required equity, a FOLD needs strong justification.
   - Made hands with 40-60% equity that comfortably beat required equity should lean CALL.
3. Equity thresholds (guidelines, adjust based on position and action):
   - Equity > 60%: Bet/Raise for value
   - Equity 40-60%: Check/Call (pot control, but CALL when facing a bet and equity > required)
   - Equity < 40%: Check/Fold (unless good bluff spot or pot odds justify)
4. With multiple opponents, bluffing is less effective.
5. {reason_jp_instruction}

Respond with JSON only:
{{"action": "<fold/check/call/bet/raise/allin>", "size": "<size or null>",
"confidence": "medium", "reasoning": "<1-2 sentences>"}}"""

REASON_GENERATION_PROMPT = """Summarize the poker decision in one short sentence for HUD display.

Action: {action}
Reason: {reasoning}
Hero Hand: {hero_hand}
Board: {board}

Output a single sentence in Japanese, max 40 characters. Example: "相手のフォールド率が高いためブラフベット推奨"
"""

PREFLOP_DELTA_PROMPT = """You are a poker GTO expert.
Adjust the preflop chart probabilities based on opponent statistics.
The chart probabilities are the anchor. Return only small deltas.

Hero position: {hero_position}
Hero hand: {hero_hand}
Scenario: {scenario}
Chart base probabilities: {chart_anchor_probs}
Opponent statistics: {villain_stats}
Effective stack: {effective_stack_bb}BB
Action prefix: {action_prefix}

Return JSON only:
{{"delta_probs": {{"raise": 0.0, "call": 0.0, "fold": 0.0}},
  "confidence": 0.5,
  "reason": "brief explanation"}}

Rules:
- delta_probs must use only actions from chart base probabilities.
- Deltas must sum to exactly 0.
- Do not add positive delta to actions with 0% base probability.
- Each delta should be small, max +/-0.10.
- No player names or private identifiers.
"""


class LLMPipeline:
    """OpenRouter API経由のLLM呼び出しパイプライン。"""

    def __init__(self, config: JsonDict) -> None:
        """Initialize the LLM pipeline from config and environment variables.

        Args:
            config: Full config.yaml dictionary with llm and game sections.
        """
        self._logger = logger
        llm_config = config.get("llm", {})
        self.timeout_sec: float = float(llm_config.get("timeout_sec", 15))
        self.total_timeout_sec: float = float(llm_config.get("total_timeout_sec", 15.0))
        self.retry_count: int = int(llm_config.get("retry_count", 0))
        self.blind_bb: int = int(config.get("game", {}).get("blind_bb", 100))

        self.api_key: str | None = os.environ.get("OPENROUTER_API_KEY")
        self.model_default: str = os.environ.get(
            "LLM_MODEL_DEFAULT",
            "anthropic/claude-sonnet-4",
        )
        self.model_premium: str = os.environ.get(
            "LLM_MODEL_PREMIUM",
            "anthropic/claude-opus-4",
        )
        self.baseline_ranges: JsonDict = self._load_baseline_ranges()
        self._validation_total = 0
        self._validation_success = 0
        self._logger.info(
            "LLMPipeline initialized: model=%s, timeout=%ss",
            self.model_default,
            self.timeout_sec,
        )

    def estimate_ranges(
        self,
        game_state: GameState,
        opponent_stats: JsonDict | None,
        baseline_range_oop: str,
        baseline_range_ip: str,
    ) -> JsonDict:
        """Estimate OOP and IP ranges, falling back to baseline ranges on errors.

        Args:
            game_state: Current game state.
            opponent_stats: Opponent statistics, or None when unavailable.
            baseline_range_oop: Baseline OOP range.
            baseline_range_ip: Baseline IP range.

        Returns:
            Dictionary with range_oop, range_ip, adjustments_made, and source.
        """
        method_start = time.perf_counter()
        safe_stats = self._anonymize_stats(opponent_stats)
        stats = self._format_opponent_stats(safe_stats)
        effective_stack = self._effective_stack(game_state)
        spr = effective_stack / game_state.pot if game_state.pot > 0 else 0.0
        prompt = RANGE_ESTIMATION_PROMPT.format(
            board=self._board_to_str(game_state),
            street=game_state.phase,
            hero_position=game_state.hero.position or "Unknown",
            ip_or_oop="IP",
            opponent_position=self._opponent_position(safe_stats),
            pot=game_state.pot,
            effective_stack=effective_stack,
            spr=spr,
            action_history=self._format_action_history(game_state),
            baseline_range_oop=baseline_range_oop,
            baseline_range_ip=baseline_range_ip,
            **stats,
        )

        text = self._call_api(
            prompt, max_tokens=400, model=self._select_model(game_state),
            task_name="range_estimation",
        )
        parsed = self._parse_json_response(text) if text is not None else None
        if parsed is None:
            total_ms = int((time.perf_counter() - method_start) * 1000)
            self._logger.info(
                "LLM task complete: task=range_estimation total_ms=%d "
                "parsed=false validated=false fallback=true",
                total_ms,
            )
            return self._range_fallback(baseline_range_oop, baseline_range_ip)

        validated = self._validate_llm_response(
            "range_estimation",
            RangeEstimationResponse,
            parsed,
        )
        if validated is not None:
            parsed = {
                "range_oop": validated.range_oop,
                "range_ip": validated.range_ip,
                "adjustments_made": (
                    validated.adjustments_made or validated.reasoning
                ),
            }

        range_oop = str(parsed.get("range_oop", ""))
        range_ip = str(parsed.get("range_ip", ""))
        if not self._validate_range(range_oop) or not self._validate_range(range_ip):
            total_ms = int((time.perf_counter() - method_start) * 1000)
            self._logger.info(
                "LLM task complete: task=range_estimation total_ms=%d "
                "parsed=true validated=false fallback=true",
                total_ms,
            )
            return self._range_fallback(baseline_range_oop, baseline_range_ip)

        total_ms = int((time.perf_counter() - method_start) * 1000)
        self._logger.info(
            "LLM task complete: task=range_estimation total_ms=%d "
            "parsed=true validated=true fallback=false",
            total_ms,
        )
        return {
            "range_oop": range_oop,
            "range_ip": range_ip,
            "adjustments_made": str(parsed.get("adjustments_made", "")),
            "source": "llm",
        }

    def suggest_exploit(
        self,
        solver_output: JsonDict,
        game_state: GameState,
        opponent_stats: JsonDict | None,
    ) -> JsonDict:
        """Suggest a small exploitative adjustment from solver output.

        Args:
            solver_output: Solver response containing root_strategy.
            game_state: Current game state.
            opponent_stats: Opponent statistics, or None.

        Returns:
            Adjustment dictionary for downstream action selection.
        """
        if not opponent_stats:
            return self._no_solver_output_response()

        root_strategy = solver_output.get("root_strategy") if solver_output else None
        if not isinstance(root_strategy, dict):
            return self._no_solver_output_response()

        method_start = time.perf_counter()
        safe_stats = self._anonymize_stats(opponent_stats)
        stats = self._format_opponent_stats(safe_stats)
        prompt = EXPLOIT_ADJUSTMENT_PROMPT.format(
            actions=root_strategy.get("actions", []),
            average_strategy=root_strategy.get("average_strategy", {}),
            hero_hand=self._hero_hand(game_state),
            hero_equity=self._first_float(root_strategy.get("equity", []), 0.0) * 100.0,
            hero_ev=self._first_float(root_strategy.get("ev", []), 0.0),
            board=self._board_to_str(game_state),
            pot=game_state.pot,
            effective_stack=self._effective_stack(game_state),
            reason_jp_instruction=REASON_JP_INSTRUCTION,
            **stats,
        )
        text = self._call_api(
            prompt, max_tokens=250, model=self._select_model(game_state),
            task_name="exploit_adjustment",
        )
        parsed = self._parse_json_response(text) if text is not None else None
        if parsed is None:
            total_ms = int((time.perf_counter() - method_start) * 1000)
            self._logger.info(
                "LLM task complete: task=exploit_adjustment total_ms=%d "
                "parsed=false validated=false fallback=true",
                total_ms,
            )
            return self._no_solver_output_response()

        validated = self._validate_llm_response(
            "exploit_adjustment",
            ExploitAdjustmentResponse,
            parsed,
        )
        if validated is not None:
            parsed = {
                "adjusted_action": validated.adjusted_action,
                "adjusted_size": validated.adjusted_size,
                "confidence": validated.confidence,
                "reasoning": validated.reasoning,
            }

        total_ms = int((time.perf_counter() - method_start) * 1000)
        self._logger.info(
            "LLM task complete: task=exploit_adjustment total_ms=%d "
            "parsed=true validated=%s fallback=false",
            total_ms,
            str(validated is not None).lower(),
        )
        return {
            "adjusted_action": parsed.get("adjusted_action"),
            "adjusted_size": parsed.get("adjusted_size"),
            "confidence": parsed.get("confidence", "low"),
            "reasoning": parsed.get("reasoning", ""),
        }

    def decide_multiway(
        self,
        game_state: GameState,
        hero_equity: float,
        opponent_profiles: list[JsonDict],
        call_amount: int = 0,
        facing_bet: int = 0,
        pot_after_call: int = 0,
        required_equity: float = 0.0,
        raw_call_amount: int = 0,
        effective_call_amount: int = 0,
        hero_call_is_all_in: bool = False,
        spr: float = 0.0,
        hero_ip_or_oop: str = "Unknown",
        preflop_actions: list[ActionRecord] | None = None,
        current_street_actions: list[ActionRecord] | None = None,
    ) -> JsonDict:
        """Recommend an action for multiway pots where solver is unavailable.

        Args:
            game_state: Current game state.
            hero_equity: Hero equity from 0.0 to 1.0.
            opponent_profiles: List of opponent profile dictionaries.
            call_amount: Amount hero must call (0 when no bet to face).
            facing_bet: Maximum opponent bet hero is facing.
            pot_after_call: Pot size after hero calls.
            required_equity: Required equity to justify a call.
            raw_call_amount: Uncapped amount needed to match the facing bet.
            effective_call_amount: Amount Hero can actually call.
            hero_call_is_all_in: Whether calling puts Hero all-in.
            spr: Stack-to-pot ratio before Hero acts.
            hero_ip_or_oop: Simple position hint for multiway decisions.
            preflop_actions: Preflop action history for this hand.
            current_street_actions: Full current street action history.

        Returns:
            Action recommendation dictionary.
        """
        method_start = time.perf_counter()
        if raw_call_amount <= 0:
            raw_call_amount = call_amount
        if effective_call_amount <= 0:
            effective_call_amount = call_amount
        preflop_history_actions = preflop_actions
        if preflop_history_actions is None:
            preflop_history_actions = list(game_state.preflop_actions or [])
        prompt = MULTIWAY_DECISION_PROMPT.format(
            board=self._board_to_str(game_state),
            street=game_state.phase,
            hero_hand=self._hero_hand(game_state),
            hero_position=game_state.hero.position or "Unknown",
            hero_ip_or_oop=hero_ip_or_oop,
            pot=game_state.pot,
            hero_stack=game_state.hero.stack or 0,
            hero_current_bet=game_state.hero.bet or 0,
            facing_bet=facing_bet,
            call_amount=call_amount,
            raw_call_amount=raw_call_amount,
            effective_call_amount=effective_call_amount,
            hero_call_is_all_in=hero_call_is_all_in,
            pot_after_call=pot_after_call,
            required_equity=required_equity * 100.0,
            num_players=game_state.active_player_count,
            spr=spr,
            equity=hero_equity * 100.0,
            preflop_action_history=self._format_action_history(
                game_state,
                preflop_history_actions,
            ),
            action_history=self._format_action_history(
                game_state,
                current_street_actions,
            ),
            opponent_profiles=json.dumps(
                self._anonymize_opponent_profiles(opponent_profiles),
                ensure_ascii=False,
            ),
            reason_jp_instruction=REASON_JP_INSTRUCTION,
        )
        text = self._call_api(
            prompt,
            max_tokens=250,
            model=self._select_model(game_state),
            task_name="multiway_decision",
        )
        parsed = self._parse_json_response(text) if text is not None else None
        if parsed is None:
            total_ms = int((time.perf_counter() - method_start) * 1000)
            self._logger.info(
                "LLM task complete: task=multiway_decision phase=%s active=%d "
                "total_ms=%d parsed=false validated=false fallback=true",
                game_state.phase,
                game_state.active_player_count,
                total_ms,
            )
            return {
                "action": "check",
                "size": None,
                "confidence": "low",
                "reasoning": "LLM利用不可のため安全にチェック",
                "raw_response": (text or ""),
            }

        validated = self._validate_llm_response(
            "multiway_decision",
            MultiwayDecisionResponse,
            parsed,
        )
        parsed_valid = validated is not None
        if validated is not None:
            parsed = {
                "action": parsed.get("action"),
                "size": parsed.get("size", parsed.get("amount")),
                "confidence": parsed.get("confidence", "low"),
                "reasoning": parsed.get("reasoning", parsed.get("reason", "")),
            }

        total_ms = int((time.perf_counter() - method_start) * 1000)
        self._logger.info(
            "LLM task complete: task=multiway_decision phase=%s active=%d "
            "total_ms=%d parsed=true validated=%s fallback=false",
            game_state.phase,
            game_state.active_player_count,
            total_ms,
            str(parsed_valid).lower(),
        )
        return {
            "action": parsed.get("action"),
            "size": parsed.get("size"),
            "confidence": parsed.get("confidence", "low"),
            "reasoning": parsed.get("reasoning", ""),
            "raw_response": (text or ""),
        }

    def generate_reason(
        self,
        action: str,
        reasoning: str,
        hero_hand: str,
        board: str,
    ) -> str:
        """Generate a short Japanese HUD reason for a recommended action.

        Args:
            action: Recommended action text.
            reasoning: Detailed English reasoning.
            hero_hand: Hero hand string.
            board: Board string.

        Returns:
            Japanese sentence for HUD display, or a fallback string.
        """
        method_start = time.perf_counter()
        prompt = REASON_GENERATION_PROMPT.format(
            action=action,
            reasoning=reasoning,
            hero_hand=hero_hand,
            board=board,
        )
        text = self._call_api(prompt, max_tokens=80, task_name="reason_generation")
        if text is None or not text.strip():
            total_ms = int((time.perf_counter() - method_start) * 1000)
            self._logger.info(
                "LLM task complete: task=reason_generation total_ms=%d "
                "parsed=false fallback=true",
                total_ms,
            )
            return f"GTO推奨: {action}"

        reason = text.strip().splitlines()[0][:40]
        validated = self._validate_llm_response(
            "reason_generation",
            ReasonGenerationResponse,
            {"reason": reason},
        )
        total_ms = int((time.perf_counter() - method_start) * 1000)
        self._logger.info(
            "LLM task complete: task=reason_generation total_ms=%d "
            "parsed=true validated=%s fallback=false",
            total_ms,
            str(validated is not None).lower(),
        )
        if validated is not None:
            return validated.reason[:40]
        return reason

    def request_preflop_delta(self, request: JsonDict) -> JsonDict | None:
        """Request preflop delta adjustment from the LLM."""
        method_start = time.perf_counter()
        prompt = self._build_delta_prompt(request)
        text = self._call_api(prompt, max_tokens=250, task_name="preflop_delta")
        parsed = self._parse_json_response(text) if text is not None else None
        if parsed is None:
            total_ms = int((time.perf_counter() - method_start) * 1000)
            self._logger.info(
                "LLM task complete: task=preflop_delta total_ms=%d "
                "parsed=false fallback=true",
                total_ms,
            )
            return None

        validated = self._validate_llm_response(
            "preflop_delta",
            PreflopDeltaResponse,
            parsed,
        )
        total_ms = int((time.perf_counter() - method_start) * 1000)
        self._logger.info(
            "LLM task complete: task=preflop_delta total_ms=%d "
            "parsed=true validated=%s fallback=false",
            total_ms,
            str(validated is not None).lower(),
        )
        if validated is not None and isinstance(validated, PreflopDeltaResponse):
            return validated.model_dump()

        if isinstance(parsed.get("delta_probs"), dict):
            return parsed
        return None

    def _build_delta_prompt(self, request: JsonDict) -> str:
        """Build the preflop delta policy prompt."""
        safe_stats = self._anonymize_stats(request.get("villain_stats"))
        return PREFLOP_DELTA_PROMPT.format(
            hero_position=request.get("hero_position"),
            hero_hand=request.get("hero_hand"),
            scenario=request.get("scenario"),
            chart_anchor_probs=request.get("chart_anchor_probs"),
            villain_stats=safe_stats,
            effective_stack_bb=request.get("effective_stack_bb"),
            action_prefix=request.get("action_prefix", []),
        )

    def _validate_llm_response(
        self,
        task_name: str,
        model: type[BaseModel],
        raw_result: JsonDict,
    ) -> BaseModel | None:
        """Validate parsed LLM output and preserve existing behavior on failure."""
        self._validation_total += 1
        val_start = time.perf_counter()
        try:
            validated = model.model_validate(raw_result)
        except ValidationError as exc:
            elapsed_ms = int((time.perf_counter() - val_start) * 1000)
            self._logger.warning(
                "LLM validation failed (%s): %s elapsed_ms=%d - using raw result",
                task_name,
                str(exc)[:200],
                elapsed_ms,
            )
            return None

        self._validation_success += 1
        elapsed_ms = int((time.perf_counter() - val_start) * 1000)
        self._logger.info(
            "LLM validation passed (%s): %d/%d elapsed_ms=%d",
            task_name,
            self._validation_success,
            self._validation_total,
            elapsed_ms,
        )
        return validated

    def _call_api(
        self,
        prompt: str,
        max_tokens: int,
        model: str | None = None,
        task_name: str = "unknown",
    ) -> str | None:
        """Call the OpenRouter chat completions API.

        Args:
            prompt: Prompt text.
            max_tokens: Maximum response tokens.
            model: Model name. Uses default model when None.
            task_name: Human-readable task label for logging.

        Returns:
            Response content text, or None on failure.
        """
        if self.api_key is None:
            self._logger.warning("OpenRouter API key is not configured")
            return None

        selected_model = model or self.model_default
        provider = self.openrouter_provider_config()
        self._logger.info(
            "LLM request start: task=%s model=%s provider=%s prompt_chars=%d "
            "max_tokens=%d timeout=%ss",
            task_name,
            selected_model,
            provider,
            len(prompt),
            max_tokens,
            self.timeout_sec,
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": selected_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            # OpenRouter reasoning models can spend all completion tokens on
            # hidden reasoning and return empty content unless this is disabled.
            # If a provider rejects this, try reasoning_effort="none" manually.
            "reasoning": {"effort": "none"},
        }
        if provider is not None:
            payload["provider"] = provider

        call_start = time.perf_counter()
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=(5, self.timeout_sec),
            )
            elapsed = time.perf_counter() - call_start
            elapsed_ms = int(elapsed * 1000)
            self._logger.info(
                "LLM API response: task=%s model=%s elapsed_ms=%d status=%d",
                task_name,
                selected_model,
                elapsed_ms,
                response.status_code,
            )
            if response.status_code >= 400:
                self._logger.warning(
                    "LLM API error response: task=%s model=%s status=%d body=%s",
                    task_name,
                    selected_model,
                    response.status_code,
                    response.text[:500],
                )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"].get("content")
            if not isinstance(content, str) or not content.strip():
                usage = data.get("usage", {})
                self._logger.warning(
                    "OpenRouter response content was empty (usage=%s)",
                    usage,
                )
                return None
            return content
        except (
            requests.RequestException,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as error:
            elapsed = time.perf_counter() - call_start
            elapsed_ms = int(elapsed * 1000)
            self._logger.warning(
                "LLM API failed: task=%s model=%s elapsed_ms=%d error=%s",
                task_name,
                selected_model,
                elapsed_ms,
                error,
            )
            return None

    @staticmethod
    def openrouter_provider_config() -> dict[str, Any] | None:
        """Return OpenRouter provider routing config from environment."""
        provider: dict[str, Any] = {}

        provider_order = os.environ.get("OPENROUTER_PROVIDER_ORDER")
        if provider_order:
            order = [
                item.strip()
                for item in provider_order.split(",")
                if item.strip()
            ]
            if order:
                provider["order"] = order

        allow_fallbacks = os.environ.get("OPENROUTER_ALLOW_FALLBACKS")
        if allow_fallbacks is not None:
            provider["allow_fallbacks"] = allow_fallbacks.lower() != "false"

        require_parameters = os.environ.get("OPENROUTER_REQUIRE_PARAMETERS")
        if require_parameters is not None:
            provider["require_parameters"] = require_parameters.lower() == "true"

        return provider or None

    def _openrouter_provider_config(self) -> dict[str, Any] | None:
        """Return OpenRouter provider routing config from environment."""
        return self.openrouter_provider_config()

    def _select_model(self, game_state: GameState) -> str:
        """Return premium model for important spots, otherwise default model."""
        if game_state.pot > 0 and game_state.hero.stack is not None:
            spr = game_state.hero.stack / game_state.pot
            if spr < 3:
                return self.model_premium

        if game_state.pot > 50 * self.blind_bb:
            return self.model_premium

        return self.model_default

    @staticmethod
    def _validate_range(range_str: str) -> bool:
        """Return whether a range string is basically valid.

        This intentionally performs shallow validation only and rejects empty,
        overly long, or obviously unsafe strings.
        """
        if not range_str or len(range_str) > 1000:
            return False
        return ALLOWED_RANGE_RE.fullmatch(range_str) is not None

    def _parse_json_response(self, text: str) -> JsonDict | None:
        """Extract a JSON object from a potentially wrapped LLM response.

        Args:
            text: Raw model response text.

        Returns:
            Parsed dictionary, or None when no valid object is found.
        """
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match is None:
            return None

        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

        return parsed if isinstance(parsed, dict) else None

    def _load_baseline_ranges(self) -> JsonDict:
        """Load baseline ranges from strategy/baseline_ranges.json."""
        if not BASELINE_RANGES_PATH.exists():
            self._logger.warning("Baseline ranges file not found: %s", BASELINE_RANGES_PATH)
            return {}

        with BASELINE_RANGES_PATH.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
        return loaded if isinstance(loaded, dict) else {}

    def get_baseline_range(self, position: str, scenario: str = "RFI") -> str:
        """Return a baseline range for position and scenario.

        Args:
            position: Position such as UTG, MP, CO, BTN, SB, BB, IP, or OOP.
            scenario: Baseline scenario key.

        Returns:
            Range string, or an empty string when not found.
        """
        scenario_ranges = self.baseline_ranges.get(scenario)
        if not isinstance(scenario_ranges, dict):
            return ""
        value = scenario_ranges.get(position, "")
        return value if isinstance(value, str) else ""

    def _format_opponent_stats(self, stats: JsonDict | None) -> JsonDict:
        """Format opponent stats for prompt interpolation with defaults."""
        if stats is None:
            return {
                "vpip": "N/A",
                "pfr": "N/A",
                "three_bet_pct": "N/A",
                "cbet_flop_pct": "N/A",
                "fold_to_three_bet": "N/A",
                "fold_to_cbet": "N/A",
                "wtsd": "N/A",
                "long_term_style": "Unknown",
                "total_hands": 0,
                "freshness_warning": "",
            }

        total_hands = int(stats.get("total_hands", 0) or 0)
        freshness_parts: list[str] = []
        if total_hands < 10:
            freshness_parts.append(
                "WARNING: Small sample size (<10 hands). Make minimal adjustments."
            )
        if stats.get("freshness_note"):
            freshness_parts.append(str(stats["freshness_note"]))

        return {
            "vpip": stats.get("vpip", "N/A"),
            "pfr": stats.get("pfr", "N/A"),
            "three_bet_pct": stats.get("three_bet_pct", "N/A"),
            "cbet_flop_pct": stats.get("cbet_flop_pct", "N/A"),
            "fold_to_three_bet": stats.get("fold_to_three_bet", "N/A"),
            # fold_to_cbet is not persisted yet; keep explicit N/A unless provided.
            "fold_to_cbet": stats.get("fold_to_cbet", "N/A"),
            "wtsd": stats.get("wtsd", stats.get("went_to_showdown", "N/A")),
            "long_term_style": stats.get("long_term_style", "Unknown"),
            "total_hands": total_hands,
            "freshness_warning": " ".join(freshness_parts),
        }

    @staticmethod
    def _anonymize_stats(
        opponent_stats: JsonDict | None,
        seat: int | str | None = None,
    ) -> JsonDict:
        """Remove player identifiers from opponent stats before LLM calls.

        Args:
            opponent_stats: Stats dictionary that may contain player names.
            seat: Optional seat number or seat label for anonymous identifier.

        Returns:
            A copied dictionary without name fields.
        """
        if not opponent_stats:
            return {}

        anonymized = dict(opponent_stats)
        for key in ("player_name", "name", "player"):
            anonymized.pop(key, None)

        if seat is not None:
            seat_text = str(seat)
            anonymized["identifier"] = (
                seat_text if seat_text.startswith("seat_") else f"seat_{seat_text}"
            )
        return anonymized

    @staticmethod
    def _anonymize_game_state_for_llm(game_state: GameState) -> JsonDict:
        """Create an anonymized player summary from GameState for LLM prompts."""
        players_info: JsonDict = {}
        if not getattr(game_state, "players", None):
            return players_info

        for seat_key, player in game_state.players.items():
            seat_id = f"seat_{seat_key}"
            if isinstance(player, dict):
                player_data = {
                    key: value
                    for key, value in player.items()
                    if key not in {"name", "player_name", "player"}
                }
            else:
                player_data = {
                    key: value
                    for key, value in vars(player).items()
                    if key not in {"name", "player_name", "player"}
                }
            players_info[seat_id] = player_data
        return players_info

    @staticmethod
    def _anonymize_opponent_profiles(
        opponent_profiles: list[JsonDict],
    ) -> list[JsonDict]:
        """Remove player identifiers from multiway opponent profiles."""
        anonymized_profiles: list[JsonDict] = []
        for index, profile in enumerate(opponent_profiles):
            seat = profile.get("identifier", f"seat_{index + 2}")
            anonymized = LLMPipeline._anonymize_stats(profile, seat=seat)
            anonymized_profiles.append(anonymized)
        return anonymized_profiles

    @staticmethod
    def _board_to_str(game_state: GameState) -> str:
        """Return board cards concatenated for prompts."""
        return "".join(game_state.board)

    @staticmethod
    def _hero_hand(game_state: GameState) -> str:
        """Return hero hand cards concatenated for prompts."""
        return "".join(game_state.hero.cards or [])

    @staticmethod
    def _format_action_history(
        game_state: GameState,
        current_street_actions: list[ActionRecord] | None = None,
    ) -> str:
        """Format actions for prompt insertion, preferring full street history."""
        if current_street_actions is not None:
            actions = current_street_actions
        else:
            actions = (
                game_state.current_street_actions
                if hasattr(game_state, "current_street_actions")
                and game_state.current_street_actions
                else None
            )
        if actions:
            action_dicts = []
            for action in actions:
                d = (
                    action.__dict__
                    if hasattr(action, "__dict__")
                    else dict(action)
                )
                action_dicts.append(d)
            return json.dumps(action_dicts, ensure_ascii=False)
        if current_street_actions is not None:
            return "[]"
        if game_state.actions_since_last_frame:
            return json.dumps(
                [action.__dict__ for action in game_state.actions_since_last_frame],
                ensure_ascii=False,
            )
        return "No recent actions."

    @staticmethod
    def _effective_stack(game_state: GameState) -> int:
        """Return a prompt-safe effective stack approximation."""
        return int(game_state.hero.stack or 0)

    @staticmethod
    def _opponent_position(opponent_stats: JsonDict | None) -> str:
        """Return opponent position from stats when available."""
        if opponent_stats is None:
            return "Unknown"
        return str(opponent_stats.get("position", "Unknown"))

    @staticmethod
    def _first_float(values: Any, default: float) -> float:
        """Return the first numeric item from a list-like value."""
        if isinstance(values, list) and values:
            try:
                return float(values[0])
            except (TypeError, ValueError):
                return default
        return default

    @staticmethod
    def _range_fallback(baseline_range_oop: str, baseline_range_ip: str) -> JsonDict:
        """Return the baseline range fallback response."""
        return {
            "range_oop": baseline_range_oop,
            "range_ip": baseline_range_ip,
            "adjustments_made": "LLM unavailable or invalid; using baseline ranges",
            "source": "baseline_fallback",
        }

    @staticmethod
    def _no_solver_output_response() -> JsonDict:
        """Return the default exploit fallback response."""
        return {
            "adjusted_action": None,
            "adjusted_size": None,
            "confidence": "low",
            "reasoning": "ソルバー出力がないためGTO基準を維持",
        }
