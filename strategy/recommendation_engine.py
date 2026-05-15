"""Unified strategy recommendation entry point."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.game_state import ActionRecord, GameState
from core.position_calculator import calculate_positions
from strategy.preflop_delta_policy import PreflopDeltaPolicy


logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


@dataclass
class Recommendation:
    """Recommended poker action.

    Attributes:
        action: Action type: FOLD, CHECK, CALL, BET, RAISE, or ALL_IN.
        amount: Bet or raise amount in chips. CHECK and FOLD use 0.
        reason: Short reason for HUD display.
        confidence: high for solver, medium for LLM, low for fallback.
        strategy_source: Strategy source identifier.
        action_probabilities: Action probability distribution.
        solver_exploitability: Solver exploitability when available.
        latency_breakdown: Per-stage latency in milliseconds.
        pot_percentage: Bet or call amount as a percentage of the pot.
        amount_bb: Bet or call amount in big blinds.
        preset_hint: Closest UI preset hint for the amount.
        raise_multiplier: Raise size as a multiplier of BB or current max bet.
        raise_multiplier_label: Display label for raise_multiplier.
    """

    action: str
    amount: int = 0
    reason: str = ""
    confidence: str = "low"
    strategy_source: str = "fallback"
    action_probabilities: dict[str, float] = field(default_factory=dict)
    solver_exploitability: float | None = None
    latency_breakdown: dict[str, float] = field(default_factory=dict)
    pot_percentage: float | None = None
    amount_bb: float | None = None
    preset_hint: str | None = None
    raise_multiplier: float | None = None
    raise_multiplier_label: str | None = None


class RecommendationEngine:
    """Unified engine that selects the correct strategy module for GameState."""

    def __init__(
        self,
        config: JsonDict,
        preflop_chart: Any,
        solver_bridge: Any | None,
        solver_request_builder: Any,
        llm_pipeline: Any | None,
        multiway_engine: Any,
    ) -> None:
        """Initialize the recommendation engine.

        Args:
            config: Parsed config.yaml dictionary.
            preflop_chart: PreflopChart instance.
            solver_bridge: PostflopSolverBridge instance, or None.
            solver_request_builder: SolverRequestBuilder instance.
            llm_pipeline: LLMPipeline instance, or None.
            multiway_engine: MultiwayEngine instance.
        """
        self.config = config
        self.preflop_chart = preflop_chart
        self.solver_bridge = solver_bridge
        self.solver_request_builder = solver_request_builder
        self.llm_pipeline = llm_pipeline
        self.multiway_engine = multiway_engine
        self.delta_policy = PreflopDeltaPolicy(llm_pipeline=llm_pipeline, config=config)
        self.logger = logger

    def generate(
        self,
        game_state: GameState,
        opponent_stats: JsonDict | None = None,
        preflop_actions: list[Any] | None = None,
    ) -> Recommendation:
        """Generate a Recommendation from the current GameState.

        Args:
            game_state: Current recognized game state.
            opponent_stats: Opponent stats keyed by seat string, or one stats dict.
            preflop_actions: Optional cumulative preflop actions excluding old frames.

        Returns:
            Recommended action with confidence, reason, and latency details.
        """
        started_at = time.perf_counter()
        logger.debug(
            "generate() called: phase=%s, active=%d, hero_cards=%s",
            game_state.phase,
            game_state.active_player_count,
            game_state.hero.cards,
        )
        try:
            if game_state.phase == "preflop":
                recommendation = self._generate_preflop(
                    game_state,
                    opponent_stats,
                    preflop_actions,
                )
            elif game_state.phase in {"flop", "turn", "river"}:
                logger.info(
                    "Postflop strategy routing: active_player_count=%d, phase=%s",
                    game_state.active_player_count,
                    game_state.phase,
                )
                if game_state.active_player_count >= 3:
                    logger.info("-> Using multiway engine (active >= 3)")
                    recommendation = self._generate_postflop_multiway(
                        game_state,
                        opponent_stats,
                    )
                elif game_state.active_player_count == 2:
                    logger.info("-> Using solver (headsup, active == 2)")
                    recommendation = self._generate_postflop_headsup(
                        game_state,
                        opponent_stats,
                    )
                else:
                    logger.info("-> Using fallback (active < 2)")
                    recommendation = self._generate_fallback(
                        game_state,
                        "Not enough active players",
                    )
            else:
                recommendation = self._generate_fallback(
                    game_state,
                    f"Unsupported phase: {game_state.phase}",
                )
        except Exception as error:
            self.logger.exception("Recommendation generation failed: %s", error)
            recommendation = self._generate_fallback(game_state, "Strategy error")

        recommendation.latency_breakdown.setdefault(
            "total_ms",
            self._elapsed_ms(started_at),
        )
        recommendation = self._cap_stack_sized_action(recommendation, game_state)
        recommendation = self._enrich_recommendation(recommendation, game_state)
        return self.apply_action_constraints(recommendation, game_state)

    def apply_action_constraints(
        self,
        recommendation: Recommendation,
        game_state: GameState,
    ) -> Recommendation:
        """Apply visible-button constraints to a recommendation."""
        return self._apply_action_constraints(recommendation, game_state)

    def _generate_preflop(
        self,
        game_state: GameState,
        opponent_stats: JsonDict | None = None,
        preflop_actions: list[Any] | None = None,
    ) -> Recommendation:
        """Generate a preflop recommendation from the chart."""
        started_at = time.perf_counter()
        hero_position = game_state.hero.position or "Unknown"
        hero_cards = game_state.hero.cards or []
        if len(hero_cards) != 2:
            return self._generate_fallback(game_state, "Hero cards unavailable")

        action_history = self._get_cumulative_preflop_actions(
            game_state,
            preflop_actions,
        )
        if hero_position == "BB" and not action_history:
            logger.debug("BB with no preflop actions yet, deferring recommendation")
            return Recommendation(
                action="CHECK",
                amount=0,
                reason="アクション履歴収集中",
                confidence="low",
                strategy_source="deferred",
                action_probabilities={"CHECK": 1.0},
                latency_breakdown={"preflop_deferred_ms": self._elapsed_ms(started_at)},
            )

        scenario = self._chart_scenario(hero_position, action_history)
        hand = "".join(hero_cards)
        chart_result = self.preflop_chart.get_recommendation(
            hero_position,
            hand,
            scenario,
            current_max_bet=self._current_max_bet(game_state),
            blind_bb=int(self.config.get("game", {}).get("blind_bb", 100)),
            effective_stack_bb=self._effective_stack_bb_for_all_in(game_state),
        )
        chart_action = str(chart_result.get("action", "fold")).lower()
        original_chart_action = chart_action
        action_probabilities = self._chart_action_to_probs(chart_result)
        villain_stats = self._get_villain_stats_for_delta(opponent_stats)
        if villain_stats and self.delta_policy.should_apply(villain_stats):
            adjusted_probabilities = self.delta_policy.apply(
                hero_position=hero_position,
                hero_hand=self._generic_preflop_hand(hero_cards),
                scenario=scenario,
                chart_probs=action_probabilities,
                villain_stats=villain_stats,
                effective_stack_bb=self._effective_stack_bb(game_state),
                action_prefix=[
                    str(action.get("action", ""))
                    for action in action_history
                    if action.get("action")
                ],
            )
            best_action = max(adjusted_probabilities, key=adjusted_probabilities.get)
            if adjusted_probabilities != action_probabilities:
                action_probabilities = adjusted_probabilities
            if best_action != chart_action:
                logger.info(
                    "Delta policy changed preflop action: %s -> %s",
                    chart_action,
                    best_action,
                )
                chart_action = best_action

        action = self._normalize_action(chart_action)
        amount_value = (
            chart_result.get("amount")
            if chart_action == original_chart_action
            else None
        )
        amount = self._parse_amount(
            amount_value,
            self._preflop_amount_for_action(chart_action, game_state),
        )
        reason = str(chart_result.get("reason") or f"チャート判断: {scenario}")

        recommendation = Recommendation(
            action=action,
            amount=amount,
            reason=reason,
            confidence=str(chart_result.get("confidence", "low")),
            strategy_source=str(chart_result.get("source", "preflop_chart")),
            action_probabilities=self._normalize_chart_probabilities(action_probabilities),
            latency_breakdown={"preflop_chart_ms": self._elapsed_ms(started_at)},
        )
        return self._apply_preflop_all_in_safety_guard(recommendation, game_state)

    def _generate_postflop_headsup(
        self,
        game_state: GameState,
        opponent_stats: JsonDict | None,
    ) -> Recommendation:
        """Generate a heads-up postflop recommendation using the solver."""
        started_at = time.perf_counter()
        latency: dict[str, float] = {}
        if self.solver_bridge is None or getattr(self.solver_bridge, "disabled", False):
            return self._llm_headsup_fallback(
                game_state,
                opponent_stats,
                "Solver unavailable",
                latency,
                started_at,
            )

        preflop_scenario = self._detect_preflop_scenario(game_state)
        baseline_oop = self._baseline_range("OOP", preflop_scenario)
        baseline_ip = self._baseline_range("IP", preflop_scenario)
        range_oop = baseline_oop
        range_ip = baseline_ip
        latency["range_estimation_ms"] = 0.0

        request_started = time.perf_counter()
        street_start_pot = self._compute_street_start_pot(game_state)
        street_start_effective_stack = (
            self._compute_street_start_effective_stack(game_state)
        )
        actions_played = self._build_actions_played(game_state)
        request = self.solver_request_builder.build_request(
            game_state,
            range_oop,
            range_ip,
            self._determine_hero_is_ip(game_state),
            street_start_pot=street_start_pot,
            street_start_effective_stack=street_start_effective_stack,
            actions_played=actions_played,
        )
        latency["request_build_ms"] = self._elapsed_ms(request_started)
        if request is None:
            return self._llm_headsup_fallback(
                game_state,
                opponent_stats,
                "Solver request unavailable",
                latency,
                started_at,
            )

        timeout_ms = int(request.get("timeout_ms", 12000))
        bridge_timeout_sec = max(timeout_ms / 1000.0 + 2.0, 12.0)
        effective_stack = int(request.get("effective_stack") or 0)
        starting_pot = int(request.get("starting_pot") or 0)
        spr = effective_stack / max(starting_pot, 1)
        logger.info(
            "HU solver request: phase=%s timeout_ms=%d bridge_timeout_sec=%.1f "
            "pot=%d effective_stack=%d spr=%.1f board_count=%d actions_played=%d",
            game_state.phase,
            timeout_ms,
            bridge_timeout_sec,
            game_state.pot,
            effective_stack,
            spr,
            len(game_state.board or []),
            len(request.get("actions_played") or []),
        )

        solve_started = time.perf_counter()
        solver_output = self.solver_bridge.solve(request, timeout=bridge_timeout_sec)
        latency["solver_ms"] = self._elapsed_ms(solve_started)
        if not solver_output.get("success"):
            logger.info(
                "HU solver failed: phase=%s elapsed_ms=%.0f timeout_ms=%d "
                "bridge_timeout_sec=%.1f error=%s",
                game_state.phase,
                latency["solver_ms"],
                timeout_ms,
                bridge_timeout_sec,
                solver_output.get("error", "Solver failed"),
            )
            return self._llm_headsup_fallback(
                game_state,
                opponent_stats,
                str(solver_output.get("error", "Solver failed")),
                latency,
                started_at,
            )

        logger.info(
            "HU solver success: phase=%s elapsed_ms=%.0f timeout_ms=%d "
            "bridge_timeout_sec=%.1f",
            game_state.phase,
            latency["solver_ms"],
            timeout_ms,
            bridge_timeout_sec,
        )

        parse_started = time.perf_counter()
        action, amount, probabilities = self._parse_solver_strategy(
            solver_output,
            game_state,
        )
        latency["solver_parse_ms"] = self._elapsed_ms(parse_started)

        reason = "HU solver recommendation"
        latency["exploit_adjustment_ms"] = 0.0
        latency["reason_generation_ms"] = 0.0
        first_stats = self._first_stats(opponent_stats)
        usable_stats = self._has_usable_stats(first_stats)
        if self.llm_pipeline is not None and usable_stats:
            logger.info(
                "HU exploit LLM enabled: total_hands=%s",
                first_stats.get("total_hands") if first_stats else None,
            )
            exploit_started = time.perf_counter()
            try:
                exploit = self.llm_pipeline.suggest_exploit(
                    solver_output,
                    game_state,
                    first_stats,
                )
                latency["exploit_adjustment_ms"] = self._elapsed_ms(exploit_started)
                adjusted_action = self._normalize_adjusted_action(
                    exploit.get("adjusted_action")
                )
                if adjusted_action is not None:
                    action = adjusted_action
                    amount = self._parse_amount(exploit.get("adjusted_size"), amount)
                reason = str(exploit.get("reasoning") or "DB stats exploit adjustment")
            except Exception as exc:
                latency["exploit_adjustment_ms"] = self._elapsed_ms(exploit_started)
                logger.warning("HU exploit LLM failed; using solver result: %s", exc)
        else:
            logger.info(
                "HU exploit LLM skipped: usable_stats=%s total_hands=%s threshold=%s",
                usable_stats,
                first_stats.get("total_hands") if first_stats else None,
                self._stats_sample_threshold_low(),
            )

        latency["headsup_total_ms"] = self._elapsed_ms(started_at)
        self._save_solver_debug(
            game_state=game_state,
            solver_request=request,
            solver_output=solver_output,
            recommendation_action=action,
            recommendation_amount=amount,
            recommendation_reason=reason,
            latency=latency,
        )
        return Recommendation(
            action=action,
            amount=amount,
            reason=reason,
            confidence="high",
            strategy_source="solver",
            action_probabilities=probabilities,
            solver_exploitability=self._optional_float(
                solver_output.get("exploitability"),
            ),
            latency_breakdown=latency,
        )

    def _generate_postflop_multiway(
        self,
        game_state: GameState,
        opponent_stats: JsonDict | None,
    ) -> Recommendation:
        """Generate a postflop multiway recommendation."""
        started_at = time.perf_counter()
        stats_list = self._stats_list(opponent_stats)
        full_street_actions = (
            list(game_state.current_street_actions)
            if hasattr(game_state, "current_street_actions")
            and game_state.current_street_actions
            else []
        )
        logger.info(
            "Multiway context: hero=%s, board=%s, pot=%d, phase=%s, "
            "hero_bet=%d, active_player_count=%d, "
            "full_street_actions_count=%d, full_street_actions=%s",
            game_state.hero.cards,
            game_state.board,
            game_state.pot,
            game_state.phase,
            game_state.hero.bet,
            game_state.active_player_count,
            len(full_street_actions),
            [
                {
                    "seat": a.seat,
                    "action": a.action,
                    "amount": a.amount,
                }
                for a in full_street_actions
            ],
        )
        result = self.multiway_engine.evaluate(game_state, stats_list)
        action = self._normalize_action(str(result.get("action", "check")))
        raw_amount = self._parse_amount(result.get("size"), 0)
        amount = self._ensure_multiway_amount(raw_amount, action, game_state)
        guard_applied = bool(result.get("guard_applied", False))

        logger.info(
            "Multiway result: action=%s, amount=%d, equity=%.4f, "
            "guard_applied=%s, source=%s, reasoning=%s",
            action,
            amount,
            float(result.get("equity", 0.0)),
            guard_applied,
            result.get("source", "unknown"),
            str(result.get("reasoning", ""))[:200],
        )

        return Recommendation(
            action=action,
            amount=amount,
            reason=str(result.get("reasoning", "")),
            confidence="medium",
            strategy_source="llm_multiway",
            action_probabilities={action: 1.0},
            latency_breakdown={"multiway_ms": self._elapsed_ms(started_at)},
        )

    def _ensure_multiway_amount(
        self,
        amount: int,
        action: str,
        game_state: GameState,
    ) -> int:
        """Ensure a non-zero amount for multiway actions that require one.

        Args:
            amount: Parsed amount from LLM output.
            action: Normalized action string.
            game_state: Current game state.

        Returns:
            Existing positive amount, or a computed default for sized actions.
        """
        if amount > 0:
            return amount

        pot = int(game_state.pot or 0)
        blind_bb = int(self.config.get("game", {}).get("blind_bb", 100))
        max_bet = self._current_max_bet(game_state)
        hero_bet = game_state.hero.bet
        hero_stack = int(game_state.hero.stack or 0)

        if action == "BET":
            default = max(int(pot * 0.6), blind_bb)
            self.logger.info(
                "Multiway BET amount was 0, using default: %d (60%% of pot %d)",
                default,
                pot,
            )
            return default

        if action == "CALL":
            raw_call_amount = max(0, max_bet - hero_bet)
            if hero_stack > 0:
                call_amount = min(raw_call_amount, hero_stack)
            else:
                call_amount = raw_call_amount
            if call_amount > 0:
                self.logger.info(
                    "Multiway CALL amount was 0, using effective call amount: %d "
                    "(raw_call_amount=%d, max_bet=%d, hero_bet=%d, hero_stack=%d)",
                    call_amount,
                    raw_call_amount,
                    max_bet,
                    hero_bet,
                    hero_stack,
                )
                return call_amount
            return 0

        if action == "RAISE":
            if max_bet > 0:
                default = max(int(max_bet * 2.5), pot)
            else:
                default = max(int(pot * 0.6), blind_bb)
            self.logger.info(
                "Multiway RAISE amount was 0, using default: %d "
                "(max_bet=%d, pot=%d)",
                default,
                max_bet,
                pot,
            )
            return default

        if action == "ALL_IN":
            if hero_stack > 0:
                self.logger.info(
                    "Multiway ALL_IN amount was 0, using hero stack: %d",
                    hero_stack,
                )
                return hero_stack
            return 0

        return 0

    def _save_solver_debug(
        self,
        game_state: GameState,
        solver_request: dict[str, Any] | None,
        solver_output: dict[str, Any],
        recommendation_action: str,
        recommendation_amount: int,
        recommendation_reason: str,
        latency: dict[str, float],
    ) -> None:
        """Save solver input/output debug JSON for post-hoc analysis.

        Saving failures are logged as warnings and never interrupt the
        recommendation pipeline.

        Args:
            game_state: Current GameState at the time of solver invocation.
            solver_request: JSON dictionary sent to the solver CLI.
            solver_output: JSON dictionary received from the solver CLI.
            recommendation_action: Final recommended action string.
            recommendation_amount: Final recommended amount in chips.
            recommendation_reason: Reason text for the recommendation.
            latency: Latency breakdown dictionary.
        """
        debug_config = self.config.get("debug", {})
        if not debug_config.get("save_solver_io", False):
            return

        try:
            now = datetime.now(timezone.utc)
            date_str = now.strftime("%Y-%m-%d")
            time_str = now.strftime("%H%M%S") + f"_{now.microsecond // 1000:03d}"
            hand_id = game_state.hand_id or 0
            phase = game_state.phase or "unknown"

            players_in_hand: dict[str, bool] = {"1": True}
            current_bets: dict[str, int] = {"1": game_state.hero.bet}
            folded_seats: list[str] = []
            for seat_key, player in game_state.players.items():
                players_in_hand[seat_key] = player.in_current_hand
                current_bets[seat_key] = player.bet
                if not player.in_current_hand:
                    folded_seats.append(seat_key)

            debug_data = {
                "timestamp": now.isoformat(),
                "hand_id": hand_id,
                "phase": phase,
                "street": phase,
                "hero_cards": list(game_state.hero.cards or []),
                "board": list(game_state.board or []),
                "pot": game_state.pot,
                "call_amount": self._compute_call_amount(game_state),
                "active_player_count": game_state.active_player_count,
                "players_in_hand": players_in_hand,
                "folded_seats": sorted(folded_seats),
                "current_bets": current_bets,
                "actions": [
                    {
                        "seat": action.seat,
                        "action": action.action,
                        "amount": action.amount,
                        "confidence": action.confidence,
                    }
                    for action in game_state.actions_since_last_frame
                ],
                "solver_request": solver_request,
                "solver_output": solver_output,
                "recommendation": {
                    "action": recommendation_action,
                    "amount": recommendation_amount,
                    "source": "solver",
                    "reason": recommendation_reason,
                },
                "latency": latency,
            }

            base_dir = str(debug_config.get("solver_io_dir", "debug/solver_io"))
            day_dir = os.path.join(base_dir, date_str)
            os.makedirs(day_dir, exist_ok=True)
            filename = f"hand_{hand_id:06d}_{phase}_{time_str}_solver.json"
            filepath = os.path.join(day_dir, filename)

            with open(filepath, "w", encoding="utf-8") as file:
                json.dump(debug_data, file, ensure_ascii=False, indent=2)

            self.logger.debug("Solver debug saved: %s", filepath)
        except Exception as exc:
            self.logger.warning("Solver debug save failed: %s", exc)

    def _generate_fallback(self, game_state: GameState, reason: str) -> Recommendation:
        """Generate the final low-confidence fallback recommendation."""
        action = "CHECK" if self._can_check(game_state) else "FOLD"
        return Recommendation(
            action=action,
            amount=0,
            reason=self._fallback_reason_jp(reason),
            confidence="low",
            strategy_source="fallback",
            action_probabilities={action: 1.0},
        )

    def _cap_stack_sized_action(
        self,
        recommendation: Recommendation,
        game_state: GameState,
    ) -> Recommendation:
        """Convert stack-covering bet or raise recommendations to ALL_IN."""
        hero_stack = game_state.hero.stack
        if (
            hero_stack is not None
            and hero_stack > 0
            and recommendation.action in {"RAISE", "BET"}
            and recommendation.amount is not None
            and recommendation.amount >= hero_stack
        ):
            return Recommendation(
                action="ALL_IN",
                amount=hero_stack,
                reason=recommendation.reason,
                confidence=recommendation.confidence,
                strategy_source=recommendation.strategy_source,
                action_probabilities=recommendation.action_probabilities,
                solver_exploitability=recommendation.solver_exploitability,
                latency_breakdown=recommendation.latency_breakdown,
                pot_percentage=recommendation.pot_percentage,
                amount_bb=recommendation.amount_bb,
                preset_hint=recommendation.preset_hint,
                raise_multiplier=recommendation.raise_multiplier,
                raise_multiplier_label=recommendation.raise_multiplier_label,
            )
        return recommendation

    def _enrich_recommendation(
        self,
        recommendation: Recommendation,
        game_state: GameState,
    ) -> Recommendation:
        """Add pot percentage, BB amount, and preset hints to a recommendation."""
        if recommendation.action in {"FOLD", "CHECK"} or recommendation.amount <= 0:
            recommendation.pot_percentage = None
            recommendation.amount_bb = None
            recommendation.preset_hint = None
            recommendation.raise_multiplier = None
            recommendation.raise_multiplier_label = None
            return recommendation

        blind_bb = int(self.config.get("game", {}).get("blind_bb", 100))
        pot = int(game_state.pot or 0)
        recommendation.amount_bb = (
            round(recommendation.amount / blind_bb, 1) if blind_bb > 0 else None
        )
        recommendation.pot_percentage = (
            round((recommendation.amount / pot) * 100, 0) if pot > 0 else None
        )
        recommendation.preset_hint = self._find_nearest_preset(
            recommendation.pot_percentage,
        )
        recommendation.raise_multiplier = None
        recommendation.raise_multiplier_label = None
        if recommendation.action == "RAISE":
            if game_state.phase == "preflop":
                base_amount = blind_bb
            else:
                base_amount = self._current_max_bet(game_state)
            if base_amount > 0:
                recommendation.raise_multiplier = round(
                    recommendation.amount / base_amount,
                    1,
                )
                recommendation.raise_multiplier_label = (
                    f"{recommendation.raise_multiplier}X"
                )
        return recommendation

    def _apply_action_constraints(
        self,
        recommendation: Recommendation,
        game_state: GameState,
    ) -> Recommendation:
        """Convert impossible recommendations based on visible button state."""
        buttons = game_state.buttons if game_state is not None else None
        call_or_check = self._button_call_or_check(buttons)
        phase = game_state.phase if game_state is not None else None
        self.logger.debug(
            "Action constraints check: rec.action=%s, buttons=%s, "
            "call_or_check=%s, phase=%s",
            recommendation.action,
            buttons,
            call_or_check,
            phase,
        )

        if buttons is None:
            return recommendation

        hero_bet = self._player_bet(getattr(game_state, "hero", None))
        max_opponent_bet = self._get_max_opponent_bet(game_state)

        if recommendation.action not in {"FOLD", "CHECK", "CALL"}:
            return recommendation

        if (
            recommendation.action == "CALL"
            and call_or_check == "call"
            and hero_bet > 0
            and max_opponent_bet > 0
            and hero_bet >= max_opponent_bet
        ):
            self.logger.info(
                "CALL -> CHECK conversion: hero_bet(%s) >= max_bet(%s), "
                "no additional cost",
                hero_bet,
                max_opponent_bet,
            )
            self._convert_to_check(recommendation)
            return recommendation

        if recommendation.action == "FOLD" and call_or_check == "check":
            self.logger.info("FOLD -> CHECK conversion: check button available")
            self._convert_to_check(recommendation)
            return recommendation

        if (
            recommendation.action == "FOLD"
            and
            call_or_check == "call"
            and hero_bet > 0
            and max_opponent_bet > 0
            and hero_bet >= max_opponent_bet
        ):
            self.logger.info(
                "FOLD -> CHECK conversion: hero_bet(%s) >= max_bet(%s), "
                "no additional cost",
                hero_bet,
                max_opponent_bet,
            )
            self._convert_to_check(recommendation)
            return recommendation

        if (
            recommendation.action == "CHECK"
            and call_or_check == "call"
            and hero_bet < max_opponent_bet
        ):
            self.logger.debug(
                "Constraint: CHECK -> FOLD (call required, "
                "hero_bet=%s < max_bet=%s)",
                hero_bet,
                max_opponent_bet,
            )
            constrained = Recommendation(
                action="FOLD",
                amount=0,
                reason=f"{recommendation.reason}（チェック不可のためフォールド推奨）",
                confidence="low",
                strategy_source=recommendation.strategy_source,
                action_probabilities=recommendation.action_probabilities,
                solver_exploitability=recommendation.solver_exploitability,
                latency_breakdown=recommendation.latency_breakdown,
            )
            return self._enrich_recommendation(constrained, game_state)
        return recommendation

    @staticmethod
    def _convert_to_check(recommendation: Recommendation) -> None:
        """Mutate a FOLD recommendation into CHECK and clear size metadata."""
        recommendation.action = "CHECK"
        recommendation.amount = 0
        recommendation.reason = "チェック可能（ベットなし）"
        recommendation.pot_percentage = None
        recommendation.amount_bb = None
        recommendation.preset_hint = None
        recommendation.raise_multiplier = None
        recommendation.raise_multiplier_label = None

    def _get_max_opponent_bet(self, game_state: GameState) -> int:
        """Return the maximum visible bet from non-hero players."""
        return self._max_opponent_bet(game_state)

    @staticmethod
    def _max_opponent_bet(game_state: GameState) -> int:
        """Return the maximum non-hero visible bet."""
        if game_state is None or not game_state.players:
            return 0
        return max(
            (
                RecommendationEngine._player_bet(player)
                for player in game_state.players.values()
            ),
            default=0,
        )

    @staticmethod
    def _player_bet(player: Any) -> int:
        """Return a player's bet from either a dataclass or dictionary."""
        if player is None:
            return 0
        if isinstance(player, dict):
            value = player.get("bet", 0)
        else:
            value = getattr(player, "bet", 0)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _button_call_or_check(buttons: Any) -> str | None:
        """Return normalized call/check button text from dict or dataclass buttons."""
        if buttons is None:
            return None
        if isinstance(buttons, dict):
            value = buttons.get("call_or_check")
        else:
            value = getattr(buttons, "call_or_check", None)
        return str(value).lower() if value is not None else None

    @staticmethod
    def _find_nearest_preset(pot_percentage: float | None) -> str | None:
        """Return the closest CoinPoker pot-size preset label."""
        if pot_percentage is None:
            return None

        pot_presets = [(33, "33%"), (50, "50%"), (75, "75%"), (100, "100%")]
        best_pct, best_label = min(
            pot_presets,
            key=lambda preset: abs(pot_percentage - preset[0]),
        )
        if abs(pot_percentage - best_pct) > 10:
            return f"{int(pot_percentage)}%"
        return best_label

    def _parse_solver_strategy(
        self,
        solver_output: JsonDict,
        game_state: GameState,
    ) -> tuple[str, int, dict[str, float]]:
        """Extract action, amount, and probabilities from solver output.

        Prefers node_strategy after solver tree navigation over root_strategy.
        """
        root_strategy = solver_output.get("node_strategy") or solver_output.get(
            "root_strategy"
        )
        if not isinstance(root_strategy, dict):
            return "CHECK", 0, {"CHECK": 1.0}

        actions = [str(action) for action in root_strategy.get("actions", [])]
        probabilities = self._hand_strategy_probabilities(root_strategy, game_state)
        if not probabilities:
            average_strategy = root_strategy.get("average_strategy", {})
            probabilities = {
                str(action): float(probability)
                for action, probability in average_strategy.items()
            }
        if not probabilities and actions:
            equal_probability = 1.0 / len(actions)
            probabilities = {action: equal_probability for action in actions}
        if not probabilities:
            return "CHECK", 0, {"CHECK": 1.0}

        selected_action = max(probabilities.items(), key=lambda item: item[1])[0]
        action, amount = self._parse_solver_action(selected_action, game_state)
        normalized_probabilities = {
            self._probability_key(action_text): float(probability)
            for action_text, probability in probabilities.items()
        }
        return action, amount, normalized_probabilities

    def _get_preflop_scenario(
        self,
        game_state: GameState,
        action_history: list[JsonDict],
    ) -> str:
        """Determine a simple preflop scenario from action history."""
        raises = [
            action
            for action in action_history
            if self._is_raise_action(str(action.get("action", "")))
        ]
        limps = [
            action
            for action in action_history
            if str(action.get("action", "")).upper() in {"CALL", "LIMP"}
        ]
        if not raises and not limps:
            return "RFI"
        if not raises and limps:
            return "vs_limp"

        hero_seat = getattr(game_state.hero, "seat", 1)
        opponent_all_in = any(
            str(action.get("action", "")).upper() == "ALL_IN"
            and not self._is_hero_preflop_action(
                action,
                hero_seat,
                game_state.hero.position,
            )
            for action in action_history
        )
        if opponent_all_in:
            return "vs_all_in"

        hero_raises = [
            action
            for action in raises
            if self._is_hero_preflop_action(
                action,
                hero_seat,
                game_state.hero.position,
            )
        ]
        opponent_raises = [action for action in raises if action not in hero_raises]

        if hero_raises and opponent_raises and raises[-1] in opponent_raises:
            return "vs_3bet"

        if not hero_raises and opponent_raises:
            first_raise_position = str(opponent_raises[0].get("position", ""))
            if game_state.hero.position in {"BB", "SB"} and first_raise_position:
                return f"vs_{first_raise_position}_raise"
            return "vs_raise"

        if hero_raises and not opponent_raises:
            return "RFI"

        return "unknown"

    def _determine_hero_is_ip(self, game_state: GameState) -> bool:
        """Return whether hero is likely in position postflop."""
        return game_state.hero.position in {"CO", "BTN"}

    def _compute_street_start_pot(self, game_state: GameState) -> int:
        """Compute pot size at the start of the current street.

        Args:
            game_state: Current recognized game state.

        Returns:
            Estimated street-start pot. Minimum is 1.
        """
        pot = int(game_state.pot or 0)
        total_current_bets = int(game_state.hero.bet or 0)
        for player in game_state.players.values():
            total_current_bets += int(player.bet or 0)
        return max(pot - total_current_bets, 1)

    def _compute_street_start_effective_stack(
        self,
        game_state: GameState,
    ) -> int | None:
        """Compute effective stack at the start of the current street.

        Args:
            game_state: Current recognized game state.

        Returns:
            Effective stack at street start, or None when not heads-up.
        """
        hero_stack = game_state.hero.stack
        hero_bet = int(game_state.hero.bet or 0)
        if hero_stack is None or hero_stack <= 0:
            if hero_bet <= 0:
                return None
            hero_start_stack = hero_bet
        else:
            hero_start_stack = hero_stack + hero_bet

        active_opponents = self.solver_request_builder._get_active_opponents(
            game_state
        )
        if len(active_opponents) != 1:
            return None

        opp_seat = str(active_opponents[0]["seat"])
        opp_stack = active_opponents[0]["stack"]
        opp_bet = (
            int(game_state.players[opp_seat].bet or 0)
            if opp_seat in game_state.players
            else 0
        )
        return min(hero_start_stack, opp_stack + opp_bet)

    def _build_actions_played(self, game_state: GameState) -> list[str] | None:
        """Build solver tree-navigation actions for the current street.

        Args:
            game_state: Current recognized game state.

        Returns:
            Solver action strings such as ["Bet 200"], or None.
        """
        hero_bet = int(game_state.hero.bet or 0)
        max_opponent_bet = self._max_opponent_bet(game_state)
        if max_opponent_bet <= 0 and hero_bet <= 0:
            return None

        actions: list[str] = []
        if max_opponent_bet > 0 and hero_bet <= 0:
            actions.append(f"Bet {max_opponent_bet}")
        elif max_opponent_bet > 0 and hero_bet > 0:
            if hero_bet < max_opponent_bet:
                actions.append(f"Bet {hero_bet}")
                actions.append(f"Raise {max_opponent_bet}")
            else:
                actions.append(f"Bet {max_opponent_bet}")
        elif hero_bet > 0 and max_opponent_bet <= 0:
            return None

        return actions or None

    def _llm_headsup_fallback(
        self,
        game_state: GameState,
        opponent_stats: JsonDict | None,
        reason: str,
        latency: dict[str, float],
        started_at: float,
    ) -> Recommendation:
        """Return a heads-up fallback when solver cannot produce a result."""
        fallback = self._generate_fallback(game_state, reason)
        first_stats = self._first_stats(opponent_stats)
        usable_stats = self._has_usable_stats(first_stats)
        latency.setdefault("range_estimation_ms", 0.0)
        latency.setdefault("reason_generation_ms", 0.0)
        latency["exploit_adjustment_ms"] = 0.0
        if self.llm_pipeline is not None and usable_stats:
            exploit_started = time.perf_counter()
            try:
                exploit = self.llm_pipeline.suggest_exploit(
                    {},
                    game_state,
                    first_stats,
                )
                latency["exploit_adjustment_ms"] = self._elapsed_ms(exploit_started)
                adjusted_action = self._normalize_adjusted_action(
                    exploit.get("adjusted_action")
                )
                if adjusted_action is not None:
                    fallback.action = adjusted_action
                    fallback.amount = self._parse_amount(exploit.get("adjusted_size"), 0)
                    fallback.reason = str(exploit.get("reasoning", reason))
                    fallback.confidence = "medium"
                    fallback.strategy_source = "llm_headsup_fallback"
            except Exception as exc:
                latency["exploit_adjustment_ms"] = self._elapsed_ms(exploit_started)
                logger.warning("HU fallback exploit LLM failed: %s", exc)
        else:
            logger.info(
                "HU fallback exploit LLM skipped: usable_stats=%s total_hands=%s "
                "threshold=%s",
                usable_stats,
                first_stats.get("total_hands") if first_stats else None,
                self._stats_sample_threshold_low(),
            )
        latency["headsup_total_ms"] = self._elapsed_ms(started_at)
        fallback.latency_breakdown.update(latency)
        return fallback

    def _chart_scenario(self, hero_position: str, action_history: list[JsonDict]) -> str:
        """Resolve the preflop scenario for chart lookup."""
        if hasattr(self.preflop_chart, "get_scenario"):
            scenario = self.preflop_chart.get_scenario(hero_position, action_history)
            if scenario != "unknown":
                return scenario
        pseudo_state = GameState()
        pseudo_state.hero.position = hero_position
        return self._get_preflop_scenario(pseudo_state, action_history)

    def _get_cumulative_preflop_actions(
        self,
        game_state: GameState,
        preflop_actions: list[Any] | None,
    ) -> list[JsonDict]:
        """Return preflop actions for chart scenario resolution."""
        actions: list[Any]
        if preflop_actions is None:
            actions = list(game_state.actions_since_last_frame)
        else:
            actions = list(preflop_actions)

        seat_positions = self._seat_positions(game_state)
        action_history: list[JsonDict] = []
        for action in actions:
            action_dict = self._action_to_dict(action)
            action_name = str(action_dict.get("action", "")).upper()
            if action_name in {"BLIND_SB", "BLIND_BB"}:
                continue
            seat = self._parse_seat(action_dict.get("seat"))
            if "position" not in action_dict and seat is not None:
                position = seat_positions.get(seat)
                if position is not None:
                    action_dict["position"] = position
            action_history.append(action_dict)
        return action_history

    @staticmethod
    def _action_to_dict(action: Any) -> JsonDict:
        """Convert an action-like object into a plain dictionary."""
        if isinstance(action, dict):
            return dict(action)
        return {
            "seat": getattr(action, "seat", None),
            "action": getattr(action, "action", ""),
            "amount": getattr(action, "amount", 0),
            "confidence": getattr(action, "confidence", "high"),
        }

    @staticmethod
    def _is_hero_preflop_action(
        action: JsonDict,
        hero_seat: int,
        hero_position: str | None,
    ) -> bool:
        """Return whether an action belongs to the hero."""
        seat = action.get("seat")
        if seat is not None:
            try:
                if int(seat) == hero_seat:
                    return True
            except (TypeError, ValueError):
                pass
        return hero_position is not None and str(action.get("position", "")) == hero_position

    @staticmethod
    def _parse_seat(value: Any) -> int | None:
        """Parse a seat value from an action dictionary."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _seat_positions(game_state: GameState) -> dict[int, str]:
        """Return seat-to-position mapping for the current preflop state."""
        if game_state.dealer_seat is None:
            return {}

        active_seats = [1]
        for seat_key, player in game_state.players.items():
            if player.is_seated or player.in_current_hand:
                active_seats.append(int(seat_key))
        return calculate_positions(game_state.dealer_seat, sorted(set(active_seats)))

    def _hand_strategy_probabilities(
        self,
        root_strategy: JsonDict,
        game_state: GameState,
    ) -> dict[str, float]:
        """Return hand-specific strategy probabilities when available."""
        hands = root_strategy.get("hands", [])
        matrix = root_strategy.get("strategy_matrix", [])
        actions = root_strategy.get("actions", [])
        hero_cards = game_state.hero.cards or []
        if len(hero_cards) != 2 or not isinstance(hands, list):
            return {}

        candidates = {"".join(hero_cards), "".join(reversed(hero_cards))}
        try:
            hand_index = next(
                index for index, hand in enumerate(hands) if str(hand) in candidates
            )
        except StopIteration:
            return {}

        if not isinstance(matrix, list) or hand_index >= len(matrix):
            return {}
        row = matrix[hand_index]
        if not isinstance(row, list):
            return {}

        return {
            str(actions[index]): float(probability)
            for index, probability in enumerate(row)
            if index < len(actions)
        }

    def _parse_solver_action(
        self,
        action_text: str,
        game_state: GameState,
    ) -> tuple[str, int]:
        """Parse a solver action string into normalized action and amount."""
        parts = action_text.strip().split()
        action_word = parts[0].lower() if parts else "check"
        amount = int(float(parts[1])) if len(parts) >= 2 and self._is_number(parts[1]) else 0
        if action_word == "check":
            return "CHECK", 0
        if action_word == "fold":
            return "FOLD", 0
        if action_word == "call":
            return "CALL", self._current_max_bet(game_state)
        if action_word == "bet":
            return "BET", amount
        if action_word == "raise":
            return "RAISE", amount
        if action_word == "allin":
            return "ALL_IN", amount
        return self._normalize_action(action_word), amount

    def _probability_key(self, action_text: str) -> str:
        """Return normalized probability key while preserving amount labels."""
        action, amount = self._parse_solver_action(action_text, GameState())
        if amount > 0 and action in {"BET", "RAISE", "ALL_IN"}:
            return f"{action} {amount}"
        return action

    def _chart_action_to_probs(self, chart_result: JsonDict) -> dict[str, float]:
        """Convert a deterministic chart action into anchor probabilities."""
        action = str(chart_result.get("action", "fold")).lower()
        if action in {"3bet", "4bet"}:
            actions = {"3bet", "4bet", "call", "fold"}
        elif action == "check":
            actions = {"raise", "call", "check"}
        else:
            actions = {"raise", "call", "fold"}
        actions.add(action)
        probabilities = {candidate: 0.0 for candidate in actions}
        probabilities[action] = 1.0
        return probabilities

    def _normalize_chart_probabilities(self, probs: dict[str, float]) -> dict[str, float]:
        """Normalize chart action probability keys to Recommendation actions."""
        normalized: dict[str, float] = {}
        for action, probability in probs.items():
            normalized_action = self._normalize_action(action)
            normalized[normalized_action] = (
                normalized.get(normalized_action, 0.0) + float(probability)
            )
        return normalized

    def _preflop_amount_for_action(self, action: str, game_state: GameState) -> int:
        """Return a default preflop amount for a chart action key."""
        normalized = self._normalize_action(action)
        if normalized == "CALL":
            return self._current_max_bet(game_state)
        if normalized in {"FOLD", "CHECK"}:
            return 0
        return self._default_preflop_amount(normalized)

    def _get_villain_stats_for_delta(
        self,
        opponent_stats: JsonDict | None,
    ) -> JsonDict | None:
        """Return the most relevant stats dictionary for preflop delta policy."""
        return self._first_stats(opponent_stats)

    def _effective_stack_bb(self, game_state: GameState) -> float:
        """Return hero effective stack in BB units for delta prompts."""
        blind_bb = float(self.config.get("game", {}).get("blind_bb", 100) or 100)
        hero_stack = float(getattr(game_state.hero, "stack", 0) or 0)
        if blind_bb <= 0 or hero_stack <= 0:
            return 100.0
        return hero_stack / blind_bb

    def _effective_stack_bb_for_all_in(self, game_state: GameState) -> float:
        """Return effective stack in BB for all-in scenario evaluation.

        For all-in scenarios, uses ``min(hero_stack, max_opponent_bet)`` as
        the effective stack because the all-in player's stack is already in
        their visible bet. For non-all-in scenarios, this naturally falls back
        to the hero stack in BB when no opponent bet is visible.

        Args:
            game_state: Current game state.

        Returns:
            Effective stack in BB units. Defaults to 100.0 when unavailable.
        """
        blind_bb = float(self.config.get("game", {}).get("blind_bb", 100) or 100)
        if blind_bb <= 0:
            return 100.0

        hero_stack = float(getattr(game_state.hero, "stack", 0) or 0)
        max_opponent_bet = float(self._max_opponent_bet(game_state))
        if hero_stack <= 0:
            return max_opponent_bet / blind_bb if max_opponent_bet > 0 else 100.0
        if max_opponent_bet <= 0:
            return hero_stack / blind_bb
        return min(hero_stack, max_opponent_bet) / blind_bb

    def _generic_preflop_hand(self, hero_cards: list[str]) -> str:
        """Return generic preflop hand notation when chart helper is available."""
        if len(hero_cards) != 2:
            return "".join(hero_cards)
        converter = getattr(self.preflop_chart, "hand_to_generic", None)
        if callable(converter):
            try:
                return str(converter(hero_cards[0], hero_cards[1]))
            except (IndexError, ValueError, TypeError):
                return "".join(hero_cards)
        return "".join(hero_cards)

    def _apply_preflop_all_in_safety_guard(
        self,
        recommendation: Recommendation,
        game_state: GameState,
    ) -> Recommendation:
        """Block non-premium stack-off recommendations facing a huge bet."""
        if recommendation.strategy_source != "preflop_chart":
            return recommendation
        if recommendation.action not in {"RAISE", "ALL_IN", "CALL"}:
            return recommendation

        hero_stack = game_state.hero.stack or 0
        max_opponent_bet = self._get_max_opponent_bet(game_state)
        facing_bet = max_opponent_bet
        if hero_stack <= 0 or facing_bet <= hero_stack * 0.5:
            return recommendation

        hero_hand = self._generic_preflop_hand(game_state.hero.cards or [])
        premium_hands = {"AA", "KK", "QQ", "JJ", "TT", "AKs", "AKo", "AQs"}
        if hero_hand in premium_hands:
            return recommendation

        logger.info(
            "Safety guard: %s is not premium enough for stack-off facing bet %d "
            "(stack %d)",
            hero_hand,
            facing_bet,
            hero_stack,
        )
        reason = (
            f"Large opponent bet ({facing_bet}) with {hero_hand}; "
            "folding for safety"
        )
        return Recommendation(
            action="FOLD",
            amount=0,
            reason=reason,
            confidence="medium",
            strategy_source=recommendation.strategy_source,
            action_probabilities={"FOLD": 1.0},
            latency_breakdown=recommendation.latency_breakdown,
        )

    def _default_preflop_amount(self, action: str) -> int:
        """Return a simple default amount for chart raises."""
        blind_bb = int(self.config.get("game", {}).get("blind_bb", 100))
        return blind_bb * 3 if action in {"RAISE", "BET"} else 0

    def _baseline_range(
        self,
        position: str,
        scenario: str = "single_raised_pot",
    ) -> str:
        """Return a baseline range based on position and preflop scenario.

        Args:
            position: "OOP" or "IP".
            scenario: Preflop scenario key.

        Returns:
            PioSOLVER-compatible range string.
        """
        ranges = self._load_baseline_ranges()
        scenario_ranges = ranges.get(scenario)
        if isinstance(scenario_ranges, dict):
            value = scenario_ranges.get(position)
            if value:
                return str(value)

        cbet = ranges.get("cbet_defend", {})
        if isinstance(cbet, dict):
            value = cbet.get(position)
            if value:
                return str(value)
        return "22+,A2s+,KTs+,QTs+,JTs"

    def _load_baseline_ranges(self) -> JsonDict:
        """Load baseline_ranges.json, caching the result."""
        cached = getattr(self, "_cached_baseline_ranges", None)
        if cached:
            return cached
        try:
            from pathlib import Path

            path = Path(__file__).with_name("baseline_ranges.json")
            with path.open("r", encoding="utf-8") as json_file:
                self._cached_baseline_ranges = json.load(json_file)
        except Exception:
            self._cached_baseline_ranges = {}
        return self._cached_baseline_ranges

    def _detect_preflop_scenario(self, game_state: GameState) -> str:
        """Detect preflop scenario for postflop range selection.

        Args:
            game_state: Current recognized game state.

        Returns:
            One of single_raised_pot, 3bet_pot, 4bet_pot, or limp_pot.
        """
        preflop_actions = getattr(game_state, "_preflop_actions", None)
        if not preflop_actions:
            pot = int(game_state.pot or 0)
            blind_bb = int(self.config.get("game", {}).get("blind_bb", 100))
            if blind_bb <= 0:
                return "single_raised_pot"
            pot_bb = pot / blind_bb
            if pot_bb >= 40:
                return "4bet_pot"
            if pot_bb >= 15:
                return "3bet_pot"
            if pot_bb <= 4:
                return "limp_pot"
            return "single_raised_pot"

        raise_count = 0
        has_limp = False
        for action in preflop_actions:
            if isinstance(action, dict):
                action_name = str(action.get("action", "")).upper()
            else:
                action_name = str(getattr(action, "action", "")).upper()

            if action_name in {"RAISE", "3BET", "4BET", "ALL_IN"}:
                raise_count += 1
            elif action_name in {"CALL", "LIMP"}:
                has_limp = True

        if raise_count >= 3:
            return "4bet_pot"
        if raise_count >= 2:
            return "3bet_pot"
        if raise_count == 0 and has_limp:
            return "limp_pot"
        return "single_raised_pot"

    @staticmethod
    def _action_history_to_dicts(actions: list[ActionRecord]) -> list[JsonDict]:
        """Convert ActionRecord instances to plain dictionaries."""
        return [
            {
                "seat": action.seat,
                "action": action.action,
                "amount": action.amount,
                "confidence": action.confidence,
            }
            for action in actions
        ]

    @staticmethod
    def _normalize_action(action: str) -> str:
        """Normalize action text to Recommendation action constants."""
        normalized = action.strip().upper().replace("-", "_").replace(" ", "_")
        mapping = {
            "BET": "BET",
            "RAISE": "RAISE",
            "CALL": "CALL",
            "CHECK": "CHECK",
            "FOLD": "FOLD",
            "ALLIN": "ALL_IN",
            "ALL_IN": "ALL_IN",
            "3BET": "RAISE",
            "4BET": "RAISE",
        }
        return mapping.get(normalized, "CHECK")

    @staticmethod
    def _parse_amount(value: Any, default: int) -> int:
        """Parse chip amount text, returning default for percentages or nulls."""
        if value is None:
            return default
        if isinstance(value, int):
            return value
        text = str(value).replace("%", "").strip()
        if not RecommendationEngine._is_number(text):
            return default
        return int(float(text))

    @staticmethod
    def _normalize_adjusted_action(value: Any) -> str | None:
        """Normalize an optional LLM exploit action without inventing CHECK."""
        if value is None:
            return None
        normalized = str(value).strip().upper().replace("-", "_").replace(" ", "_")
        mapping = {
            "BET": "BET",
            "RAISE": "RAISE",
            "CALL": "CALL",
            "CHECK": "CHECK",
            "FOLD": "FOLD",
            "ALLIN": "ALL_IN",
            "ALL_IN": "ALL_IN",
            "3BET": "RAISE",
            "4BET": "RAISE",
        }
        return mapping.get(normalized)

    def _stats_sample_threshold_low(self) -> int:
        """Return the minimum sample size required for opponent stats."""
        return int(self.config.get("preflop_delta", {}).get("sample_threshold_low", 50))

    def _has_usable_stats(self, stats: JsonDict | None) -> bool:
        """Return whether stats have enough hands for LLM exploit adjustment."""
        if not stats:
            return False

        total_hands = stats.get("total_hands", 0)
        if not isinstance(total_hands, (int, float)):
            return False

        return total_hands >= self._stats_sample_threshold_low()

    @staticmethod
    def _can_check(game_state: GameState) -> bool:
        """Return whether fallback should choose CHECK instead of FOLD."""
        max_bet = RecommendationEngine._current_max_bet(game_state)
        return game_state.hero.bet >= max_bet

    @staticmethod
    def _current_max_bet(game_state: GameState) -> int:
        """Return the current maximum visible bet."""
        bets = [game_state.hero.bet]
        bets.extend(player.bet for player in game_state.players.values())
        return max(bets) if bets else 0

    @staticmethod
    def _compute_call_amount(game_state: GameState) -> int:
        """Compute the amount hero needs to call.

        Args:
            game_state: Current game state.

        Returns:
            Call amount in chips. 0 if hero already matches the max bet.
        """
        max_bet = RecommendationEngine._current_max_bet(game_state)
        return max(0, max_bet - game_state.hero.bet)

    @staticmethod
    def _first_stats(opponent_stats: JsonDict | None) -> JsonDict | None:
        """Return the first opponent stats dictionary from flexible input."""
        if opponent_stats is None:
            return None
        if all(isinstance(value, dict) for value in opponent_stats.values()):
            first = next(iter(opponent_stats.values()), None)
            return first if isinstance(first, dict) else None
        return opponent_stats

    @staticmethod
    def _stats_list(opponent_stats: JsonDict | None) -> list[JsonDict | None]:
        """Convert flexible opponent stats input to a list."""
        if opponent_stats is None:
            return []
        if all(isinstance(value, dict) for value in opponent_stats.values()):
            return list(opponent_stats.values())
        return [opponent_stats]

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        """Convert value to float when possible."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_raise_action(action: str) -> bool:
        """Return whether action text means raise."""
        lowered = action.strip().lower().replace("-", "_")
        return lowered in {
            "raise",
            "bet",
            "3bet",
            "4bet",
            "all_in",
            "allin",
        } or "raise" in lowered

    @staticmethod
    def _fallback_reason_jp(reason: str) -> str:
        """Return a concise Japanese fallback reason."""
        if reason.startswith("Unsupported phase:"):
            phase = reason.split(":", 1)[1].strip()
            return f"対応外フェーズ: {phase}"
        mapping = {
            "Not enough active players": "参加人数不足のため安全策",
            "Strategy error": "戦略計算エラーのため安全策",
            "Solver unavailable": "ソルバー利用不可のため安全策",
            "Solver request unavailable": "ソルバー入力不足のため安全策",
        }
        if reason in mapping:
            return mapping[reason]
        if reason.startswith("Solver failed"):
            return "ソルバー失敗のため安全策"
        return reason

    @staticmethod
    def _is_number(value: str) -> bool:
        """Return whether text can be parsed as a number."""
        try:
            float(value)
        except ValueError:
            return False
        return True

    @staticmethod
    def _elapsed_ms(started_at: float) -> float:
        """Return elapsed milliseconds since started_at."""
        return (time.perf_counter() - started_at) * 1000.0
