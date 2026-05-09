"""Main polling loop that builds GameState objects from captured frames."""

import inspect
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from capture.base_capture import BaseCapture
from core.game_state import ActionRecord, ButtonState, GameState, PlayerState
from core.game_state import create_empty_game_state
from core.hand_manager import HandManager
from core.position_calculator import calculate_positions, get_hero_position
from recognition.action_estimator import ActionEstimator
from recognition.button_recognizer import ButtonRecognizer
from recognition.card_recognizer import CardRecognizer
from recognition.dealer_recognizer import DealerRecognizer
from recognition.diff_detector import DiffDetector
from recognition.fold_badge_detector import FoldBadgeDetector
from recognition.name_recognizer import NameRecognizer
from recognition.number_recognizer import NumberRecognizer
from recognition.seat_card_detector import SeatCardDetector
from strategy.recommendation_engine import Recommendation, RecommendationEngine

logger = logging.getLogger(__name__)


class GameLoop:
    """Synchronous game polling loop for Phase 10a.

    Args:
        capture: Capture source.
        config: Parsed config.yaml dictionary.
        profile: Coordinate profile dictionary.
        hand_manager: HandManager instance.
        on_game_state: Optional callback invoked after each GameState.
    """

    def __init__(
        self,
        capture: BaseCapture,
        config: dict[str, Any],
        profile: dict[str, Any],
        hand_manager: HandManager,
        on_game_state: Callable[[GameState], None] | None = None,
        enable_strategy: bool = True,
        hud_callback: Callable[[Recommendation | None], None] | None = None,
        hud_computing_callback: Callable[[], None] | None = None,
    ) -> None:
        self._capture = capture
        self._config = config
        self._profile = profile
        self._hand_manager = hand_manager
        self._on_game_state = on_game_state
        self._hud_callback = hud_callback
        self._hud_computing_callback = hud_computing_callback

        self._card_recognizer = CardRecognizer(profile, config)
        self._number_recognizer = NumberRecognizer(profile, config)
        self._button_recognizer = ButtonRecognizer(profile, config)
        self._dealer_recognizer = DealerRecognizer(profile, config)
        self._name_recognizer = NameRecognizer(profile, config)
        self._diff_detector = DiffDetector(config)
        self._action_estimator = ActionEstimator(config)
        self._fold_badge_detector = FoldBadgeDetector(profile, config)
        self._seat_card_detector = SeatCardDetector(profile, config)
        recognition_config = config.get("recognition", {})
        self._seat_no_card_streak: dict[int, int] = {}
        self._seat_card_fold_latched: set[int] = set()
        self._seat_card_fold_confirm_frames = int(
            recognition_config.get("fold_confirm_frames", 3)
        )
        self._hand_start_grace_sec = float(
            recognition_config.get("hand_start_grace_sec", 1.5)
        )
        self._table_visible = False
        self._table_inactive_streak = 0
        self._table_active_streak = 0
        self._table_inactive_confirm_frames = int(
            recognition_config.get("table_inactive_confirm_frames", 3)
        )
        self._table_active_confirm_frames = int(
            recognition_config.get("table_active_confirm_frames", 1)
        )

        self._running = False
        self._prev_state: GameState | None = None
        self._frame_number = 0
        self._polling_interval = float(
            config.get("capture", {}).get("polling_interval_sec", 0.5)
        )
        self._cached_hero_cards: list[str] | None = None
        self._partial_hero_cards: list[str | None] | None = None
        self._cached_hand_id: int | None = None
        self._cached_player_names: dict[str, str | None] = {}
        self._player_names_captured_for_hand: int | None = None
        self._cached_dealer_seat: int | None = None
        self._last_detected_dealer_seat: int | None = None
        self._hand_positions: dict[int, str] | None = None
        self._hand_dealer_seat: int | None = None
        self._recommendation_engine: RecommendationEngine | None = None

        self._consecutive_capture_failures = 0
        self._capture_failed = False
        self._last_strategy_is_my_turn = False
        self._last_waiting_log: str | None = None
        self._last_recommendation_log: str | None = None
        self._waiting_for_card_clear = False
        self._hero_cards_missing_since_hand_end = False
        self._last_ended_hero_cards: list[str | None] | None = None
        self._last_hand_manager_phase: str | None = None
        self._previous_recommendation: Recommendation | None = None
        self._last_strategy_phase: str | None = None

        if enable_strategy:
            self._init_strategy_modules()

    def start(self) -> None:
        """Start the blocking polling loop."""
        self._running = True
        logger.info("Game loop started")

        while self._running:
            loop_start = time.perf_counter()
            try:
                game_state = self.process_one_frame()
                if game_state is not None:
                    self._hand_manager.process_frame(game_state)
                    self._sync_game_state_with_hand_manager(game_state)
                    self._update_hand_position_lock(game_state)
                    self._handle_strategy(game_state)
                    if self._on_game_state is not None:
                        self._on_game_state(game_state)
            except Exception:
                logger.exception("Error in game loop")

            elapsed = time.perf_counter() - loop_start
            sleep_time = max(0.0, self._polling_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def stop(self) -> None:
        """Request polling loop stop."""
        self._running = False

        if self._recommendation_engine is not None:
            solver_bridge = self._recommendation_engine.solver_bridge
            if solver_bridge is not None:
                try:
                    solver_bridge.stop()
                except Exception:
                    logger.warning("SolverBridge stop failed during shutdown")
        if self._hand_manager is not None:
            try:
                self._hand_manager.close()
            except Exception:
                logger.warning("HandManager close failed during shutdown")
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                logger.warning("Capture release failed during shutdown")
        logger.info("Game loop stop requested")

    @property
    def current_recommendation(self) -> Recommendation | None:
        """Return the currently pending recommendation for HUD display."""
        return self._previous_recommendation

    @property
    def capture_failed(self) -> bool:
        """Return whether capture was lost after reconnect attempts failed."""
        return self._capture_failed

    def process_one_frame(self) -> GameState | None:
        """Process one captured frame.

        Returns:
            Constructed GameState, or None when frame capture failed.
        """
        frame = self._capture.get_frame()
        if frame is None:
            self._handle_capture_failure()
            return None

        self._consecutive_capture_failures = 0

        self._frame_number += 1
        game_state = self._build_game_state(frame, time.time())
        seat_card_results = self._seat_card_detector.detect_all(frame)
        self._apply_seat_card_visibility(game_state, seat_card_results)

        if self._prev_state is not None:
            estimation = self._action_estimator.estimate(
                self._prev_state,
                game_state,
            )
            game_state.game_event = estimation.get("game_event")
            if game_state.game_event == "NEW_HAND":
                if self._hand_manager.phase not in {"preflop", "flop", "turn", "river"}:
                    logger.info(
                        "NEW_HAND filter skipped: phase=%s (not active)",
                        self._hand_manager.phase,
                    )
                elif (
                    self._cached_hero_cards is None
                    or len(self._cached_hero_cards) != 2
                ):
                    logger.info(
                        "NEW_HAND filter skipped: cached_hero_cards=%s",
                        self._cached_hero_cards,
                    )
                else:
                    hero_cards_now = self._card_recognizer.recognize_hero_cards(
                        frame,
                        log_info=False,
                    )
                    cards_missing = self._hero_cards_missing(hero_cards_now)
                    logger.info(
                        "NEW_HAND filter check: hero_cards_now=%s, missing=%s, "
                        "cached=%s, phase=%s, pot %d -> %d",
                        hero_cards_now,
                        cards_missing,
                        self._cached_hero_cards,
                        self._hand_manager.phase,
                        self._prev_state.pot,
                        game_state.pot,
                    )
                    if not cards_missing:
                        logger.info(
                            "NEW_HAND suppressed: hero cards still visible (%s), "
                            "phase=%s, pot %d -> %d",
                            hero_cards_now,
                            self._hand_manager.phase,
                            self._prev_state.pot,
                            game_state.pot,
                        )
                        game_state.game_event = None
            game_state.actions_since_last_frame = [
                self._dict_to_action_record(action)
                if isinstance(action, dict)
                else action
                for action in estimation.get("actions", [])
            ]
            filtered_pot = estimation.get("filtered_pot")
            if filtered_pot is not None:
                logger.debug(
                    "Applying filtered pot: %d -> %d (spike held)",
                    game_state.pot,
                    filtered_pot,
                )
                game_state.pot = filtered_pot
        else:
            game_state.game_event = None
            game_state.actions_since_last_frame = []

        if self._hand_manager.hand_just_started:
            self._fold_badge_detector.reset()
            self._seat_card_detector.reset()
            self._seat_no_card_streak.clear()
            self._seat_card_fold_latched.clear()
            self._hero_cards_missing_since_hand_end = False
            self._last_ended_hero_cards = None
            logger.debug(
                "Fold badge and seat-card states cleared on hand start (hand_id=%s)",
                self._hand_manager.hand_id,
            )

        if self._hand_manager.phase in {"preflop", "flop", "turn", "river"}:
            fold_results = self._fold_badge_detector.detect_all(frame)
            self._process_fold_badge_detection(game_state, fold_results)
            if self._is_seat_card_detection_allowed():
                self._process_seat_card_detection(game_state, seat_card_results)
            else:
                logger.debug(
                    "SeatCardDetector skipped during hand-start grace period "
                    "(hand_id=%s)",
                    self._hand_manager.hand_id,
                )

        self._manage_hero_card_cache(game_state)
        self._prev_state = game_state
        return game_state

    def reset(self) -> None:
        """Reset internal loop state."""
        self._prev_state = None
        self._frame_number = 0
        self._cached_hero_cards = None
        self._partial_hero_cards = None
        self._cached_hand_id = None
        self._cached_dealer_seat = None
        self._last_detected_dealer_seat = None
        self._hand_positions = None
        self._hand_dealer_seat = None
        self._last_waiting_log = None
        self._waiting_for_card_clear = False
        self._hero_cards_missing_since_hand_end = False
        self._last_ended_hero_cards = None
        self._last_hand_manager_phase = None
        self._previous_recommendation = None
        self._last_strategy_phase = None
        self._diff_detector.reset()
        self._action_estimator.reset()
        self._fold_badge_detector.reset()
        self._seat_card_detector.reset()
        self._seat_no_card_streak.clear()
        self._seat_card_fold_latched.clear()
        self._table_visible = False
        self._table_inactive_streak = 0
        self._table_active_streak = 0
        logger.info("Game loop reset")

    def _init_strategy_modules(self) -> None:
        """Initialize strategy modules, leaving unavailable modules as None."""
        preflop_chart = None
        solver_bridge = None
        solver_request_builder = None
        llm_pipeline = None
        multiway_engine = None

        try:
            from strategy.preflop_chart import PreflopChart

            chart_path = self._config.get("preflop_chart", {}).get(
                "path",
                "preflop_charts/6max_gto.json",
            )
            preflop_chart = PreflopChart(chart_path, config=self._config)
        except Exception:
            logger.warning(
                "PreflopChart initialization failed, preflop will use fallback",
            )

        try:
            from solver.solver_bridge import PostflopSolverBridge

            cli_path = self._config.get("solver", {}).get(
                "cli_path",
                "solver/bin/postflop_cli.exe",
            )
            solver_bridge = PostflopSolverBridge(cli_path=cli_path)
            solver_bridge.start()
        except Exception:
            logger.warning("SolverBridge initialization failed, solver will be disabled")

        try:
            from strategy.solver_request_builder import SolverRequestBuilder

            solver_request_builder = SolverRequestBuilder(self._config)
        except Exception:
            logger.warning("SolverRequestBuilder initialization failed")

        try:
            from strategy.llm_pipeline import LLMPipeline

            llm_pipeline = LLMPipeline(self._config)
        except Exception:
            logger.warning("LLMPipeline initialization failed, will use baseline ranges")

        try:
            from strategy.multiway_engine import MultiwayEngine

            if llm_pipeline is not None:
                multiway_engine = MultiwayEngine(llm_pipeline, self._config)
        except Exception:
            logger.warning("MultiwayEngine initialization failed")

        self._recommendation_engine = RecommendationEngine(
            config=self._config,
            preflop_chart=preflop_chart,
            solver_bridge=solver_bridge,
            solver_request_builder=solver_request_builder,
            llm_pipeline=llm_pipeline,
            multiway_engine=multiway_engine,
        )

    def _handle_capture_failure(self) -> None:
        """Attempt capture reconnection and stop after repeated failures."""
        self._consecutive_capture_failures += 1
        max_attempts = int(
            self._config.get("capture", {}).get("max_reconnect_attempts", 3)
        )

        if self._consecutive_capture_failures <= max_attempts:
            logger.warning(
                "Capture frame lost (%d/%d), attempting reconnect",
                self._consecutive_capture_failures,
                max_attempts,
            )
            reconnect = getattr(self._capture, "reconnect", None)
            if callable(reconnect) and reconnect():
                self._consecutive_capture_failures = 0
                logger.info("Capture reconnected, resuming")
            else:
                logger.warning(
                    "Reconnect attempt %d failed",
                    self._consecutive_capture_failures,
                )
            return

        logger.error(
            "Capture lost after %d reconnect attempts, stopping",
            max_attempts,
        )
        self._capture_failed = True
        self.stop()

    def _handle_strategy(self, game_state: GameState) -> None:
        """Manage strategy calculation on hero's turn only."""
        strategy_started_at = time.perf_counter()
        if self._recommendation_engine is None:
            self._save_human_action_to_hand_manager(game_state)
            return

        phase = self._hand_manager.phase if self._hand_manager is not None else None

        # Phase が非アクティブなら何もしない
        if phase in (None, "waiting", "hand_end"):
            self._last_recommendation_log = None
            self._previous_recommendation = None
            self._last_strategy_phase = phase
            self._last_strategy_is_my_turn = False
            self._notify_hud(None)
            self._save_human_action_to_hand_manager(game_state)
            return

        # hand_just_started フレームはスキップ
        if (
            phase == "preflop"
            and getattr(self._hand_manager, "hand_just_started", False) is True
        ):
            logger.debug("Skipping preflop recommendation on hand-start frame")
            self._last_strategy_phase = phase
            self._last_strategy_is_my_turn = game_state.hero.is_my_turn
            self._save_human_action_to_hand_manager(game_state)
            return

        players_in_hand = self._hand_manager.get_players_in_hand()
        if 1 not in players_in_hand:
            if (
                self._previous_recommendation is not None
                or self._last_recommendation_log is not None
            ):
                logger.info("Strategy skipped: hero is no longer in current hand")
            self._last_recommendation_log = None
            self._previous_recommendation = None
            self._last_strategy_is_my_turn = False
            self._notify_hud(None)
            self._save_human_action_to_hand_manager(game_state)
            return

        # NEW_HAND / ストリート変化時にリセット
        if game_state.game_event == "NEW_HAND":
            self._last_recommendation_log = None
            self._previous_recommendation = None

        if phase == "preflop" and self._last_strategy_phase != "preflop":
            self._previous_recommendation = None

        if game_state.game_event == "NEW_STREET":
            self._last_recommendation_log = None
            if self._hand_manager is not None:
                game_state.phase = self._hand_manager.phase

        # ヒーローのターンでなければ推奨をクリアして終了
        if not game_state.hero.is_my_turn:
            # ターン終了時（True→False）にクリア
            if self._last_strategy_is_my_turn:
                self._last_recommendation_log = None
                self._previous_recommendation = None
                self._notify_hud(None)
            self._save_human_action_to_hand_manager(game_state)
            self._last_strategy_phase = phase
            self._last_strategy_is_my_turn = False
            return

        # === ここから is_my_turn=True のみ ===

        # プリフロップ
        if phase == "preflop":
            # 前フレームもis_my_turnだった場合（継続表示）、制約のみ再適用
            if (
                self._last_strategy_is_my_turn
                and self._previous_recommendation is not None
            ):
                changed = self._apply_action_constraints_to_recommendation(
                    self._previous_recommendation, game_state
                )
                if changed:
                    self._log_recommendation(
                        "Preflop recommendation",
                        self._previous_recommendation,
                    )
                    self._save_recommendation_to_hand_manager(
                        self._previous_recommendation,
                        strategy_started_at,
                    )
                self._notify_hud(self._previous_recommendation)
            else:
                self._notify_hud_computing()
                recommendation = self._generate_recommendation(
                    game_state,
                    preflop_actions=self._get_preflop_actions_for_strategy(),
                )
                self._log_recommendation("Preflop recommendation", recommendation)
                self._log_recommendation_change(recommendation)
                self._save_recommendation_to_hand_manager(
                    recommendation, strategy_started_at
                )
                self._previous_recommendation = recommendation
                self._notify_hud(recommendation)

        # ポストフロップ（flop / turn / river）
        elif phase in {"flop", "turn", "river"}:
            log_decision = logger.info
            if self._last_strategy_is_my_turn:
                log_decision = logger.debug
            log_decision(
                "Strategy decision point: phase=%s, active=%d, is_my_turn=%s",
                phase,
                game_state.active_player_count,
                game_state.hero.is_my_turn,
            )

            # 前フレームもis_my_turnだった場合（継続表示）、制約のみ再適用
            if (
                self._last_strategy_is_my_turn
                and self._previous_recommendation is not None
            ):
                changed = self._apply_action_constraints_to_recommendation(
                    self._previous_recommendation, game_state
                )
                if changed:
                    self._log_recommendation(
                        "Postflop recommendation",
                        self._previous_recommendation,
                    )
                    self._save_recommendation_to_hand_manager(
                        self._previous_recommendation,
                        strategy_started_at,
                    )
                self._notify_hud(self._previous_recommendation)
            else:
                # 初回計算（自分のターンが来た最初のフレーム）
                self._notify_hud_computing()
                recommendation = self._generate_recommendation(game_state)
                guarded = self._guard_postflop_recommendation_source(
                    recommendation, game_state, phase, "Synchronous"
                )
                if guarded is None:
                    self._save_human_action_to_hand_manager(game_state)
                    self._last_strategy_phase = phase
                    self._last_strategy_is_my_turn = True
                    return
                recommendation = guarded
                self._log_recommendation("Postflop recommendation", recommendation)
                self._log_recommendation_change(recommendation)
                self._save_recommendation_to_hand_manager(
                    recommendation, strategy_started_at
                )
                self._previous_recommendation = recommendation
                self._notify_hud(recommendation)

        self._save_human_action_to_hand_manager(game_state)
        self._last_strategy_phase = phase
        self._last_strategy_is_my_turn = game_state.hero.is_my_turn

    def _log_recommendation(
        self,
        prefix: str,
        recommendation: Recommendation | None,
        street: str | None = None,
    ) -> None:
        """Log a recommendation in a consistent format."""
        if recommendation is None:
            return
        if prefix == "Using pre-computed recommendation":
            log_key = (
                f"{prefix}:{recommendation.action}:"
                f"{recommendation.amount}:{recommendation.strategy_source}:"
                f"{recommendation.amount_bb}:{recommendation.preset_hint}:"
                f"{recommendation.pot_percentage}:"
                f"{recommendation.raise_multiplier_label}"
            )
            if log_key == self._last_recommendation_log:
                return
            self._last_recommendation_log = log_key
        recommendation_text = self._format_recommendation_log(recommendation)
        if street is None:
            logger.info("%s: %s", prefix, recommendation_text)
            return
        logger.info(
            "%s: %s (street=%s)",
            prefix,
            recommendation_text,
            street,
        )

    @staticmethod
    def _format_recommendation_log(recommendation: Recommendation) -> str:
        """Return a compact recommendation string for logs."""
        if recommendation.action in {"FOLD", "CHECK"} or recommendation.amount <= 0:
            parts = [
                f"{recommendation.action} {int(recommendation.amount)}",
            ]
        else:
            parts = [
                f"{recommendation.action} {int(recommendation.amount)}",
            ]
            if recommendation.amount_bb is not None:
                parts.append(f"({recommendation.amount_bb}BB)")
            if (
                recommendation.action == "RAISE"
                and recommendation.raise_multiplier_label is not None
            ):
                parts.append(f"[{recommendation.raise_multiplier_label}]")
            elif recommendation.action == "BET" and recommendation.preset_hint is not None:
                parts.append(f"[{recommendation.preset_hint}pot]")
            elif recommendation.action == "BET" and recommendation.pot_percentage is not None:
                parts.append(f"[{int(recommendation.pot_percentage)}%pot]")
        parts.append(f"(source={recommendation.strategy_source})")
        return " ".join(parts)

    @staticmethod
    def _format_recommendation_for_replay(recommendation: Recommendation) -> str:
        """Return a recommendation string for replay JSON."""
        if recommendation.action in {"FOLD", "CHECK"} or recommendation.amount <= 0:
            return recommendation.action
        amount = int(recommendation.amount)
        return f"{recommendation.action} {amount}"

    @staticmethod
    def _format_human_action_for_replay(action: ActionRecord) -> str:
        """Return a hero action string for replay JSON."""
        if action.amount <= 0:
            return action.action
        return f"{action.action} {action.amount}"

    def _guard_postflop_recommendation_source(
        self,
        recommendation: Recommendation,
        game_state: GameState,
        phase: str,
        context: str,
        elapsed_sec: float | None = None,
    ) -> Recommendation | None:
        """Reject preflop recommendations produced for postflop streets."""
        if phase not in {"flop", "turn", "river"}:
            return recommendation
        source = recommendation.strategy_source or ""
        if "preflop" not in source:
            return recommendation
        if elapsed_sec is None:
            logger.warning(
                "%s generate() produced preflop result for %s "
                "(source=%s), using fallback",
                context,
                phase,
                source,
            )
            if self._recommendation_engine is None:
                return None
            return self._recommendation_engine._generate_fallback(
                game_state,
                "Preflop result in postflop",
            )
        logger.warning(
            "%s produced preflop result for %s street "
            "(source=%s, elapsed=%.3fs), discarding",
            context,
            phase,
            source,
            elapsed_sec,
        )
        return None

    def _save_recommendation_to_hand_manager(
        self,
        recommendation: Recommendation | None,
        started_at: float,
    ) -> None:
        """Persist the displayed recommendation on the current hand street."""
        if recommendation is None or self._hand_manager is None:
            return
        if self._hand_manager.phase in {"waiting", "hand_end"}:
            return

        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        latency_breakdown: dict[str, float] = {}
        if recommendation.latency_breakdown:
            latency_breakdown = {
                str(key): float(value)
                for key, value in recommendation.latency_breakdown.items()
            }
        else:
            latency_breakdown = {"total_ms": elapsed_ms}

        recommendation_text = self._format_recommendation_for_replay(recommendation)
        self._hand_manager.set_recommendation(
            recommendation=recommendation_text,
            time_to_recommend_ms=elapsed_ms,
            latency_breakdown=latency_breakdown,
        )
        logger.info(
            "Recommendation saved to hand_manager: %s",
            recommendation_text,
        )

    def _save_human_action_to_hand_manager(self, game_state: GameState) -> None:
        """Persist an explicit hero action from GameState when present."""
        if self._hand_manager is None or game_state.hero_action is None:
            return
        if self._hand_manager.phase in {"waiting", "hand_end"}:
            return
        human_action = self._format_human_action_for_replay(game_state.hero_action)
        self._hand_manager.set_human_action(human_action)

    def _apply_action_constraints_to_recommendation(
        self, recommendation: Recommendation, game_state: GameState
    ) -> bool:
        """Re-apply current button constraints to a cached recommendation.

        Args:
            recommendation: The recommendation to constrain.
            game_state: Current game state with button information.

        Returns:
            True if the recommendation was changed by constraints.
        """
        if self._recommendation_engine is None:
            return False

        before = (recommendation.action, recommendation.amount)
        constrained = self._recommendation_engine.apply_action_constraints(
            recommendation,
            game_state,
        )
        if not isinstance(constrained, Recommendation):
            constrained = recommendation
        self._previous_recommendation = constrained
        after = (constrained.action, constrained.amount)
        changed = before != after
        if changed:
            logger.info(
                "Cached recommendation updated by button constraints: %s -> %s",
                before[0],
                after[0],
            )
        return changed

    def _log_recommendation_change(
        self,
        recommendation: Recommendation | None,
    ) -> None:
        if recommendation is None:
            return
        if (
            self._previous_recommendation is not None
            and self._previous_recommendation.action != recommendation.action
        ):
            logger.info(
                "Recommendation changed: %s -> %s "
                "(opponent action changed the scenario)",
                self._previous_recommendation.action,
                recommendation.action,
            )
        self._previous_recommendation = recommendation

    def _notify_hud(self, recommendation: Recommendation | None) -> None:
        """Notify the HUD callback with the latest recommendation.

        Args:
            recommendation: Recommendation to display, or None for waiting.
        """
        if self._hud_callback is None:
            return
        try:
            self._hud_callback(recommendation)
        except Exception:
            logger.warning("HUD callback failed", exc_info=True)

    def _notify_hud_computing(self) -> None:
        """Notify the HUD callback that computation is in progress."""
        if self._hud_computing_callback is None:
            return
        try:
            self._hud_computing_callback()
        except Exception:
            logger.warning("HUD computing callback failed", exc_info=True)

    def _get_preflop_actions_for_strategy(self) -> list[ActionRecord]:
        """Return cumulative preflop actions from HandManager when available."""
        if self._hand_manager is None:
            return []
        get_preflop_actions = getattr(self._hand_manager, "get_preflop_actions", None)
        if not callable(get_preflop_actions):
            return []
        return list(get_preflop_actions())

    def _generate_recommendation(
        self,
        game_state: GameState,
        preflop_actions: list[ActionRecord] | None = None,
    ) -> Recommendation:
        """Generate a recommendation while preserving legacy test doubles."""
        if self._recommendation_engine is None:
            raise RuntimeError("Recommendation engine is not initialized")
        generate_method = self._recommendation_engine.generate
        if not self._recommendation_generate_accepts_keywords(generate_method):
            return generate_method(game_state)
        opponent_stats = self._get_opponent_stats_for_strategy(game_state)
        try:
            return generate_method(
                game_state,
                opponent_stats=opponent_stats,
                preflop_actions=preflop_actions,
            )
        except TypeError as exc:
            if "opponent_stats" in str(exc):
                return self._generate_recommendation_without_stats(
                    game_state,
                    preflop_actions,
                )
            if "preflop_actions" not in str(exc):
                raise
            return self._recommendation_engine.generate(
                game_state,
                opponent_stats=opponent_stats,
            )

    @staticmethod
    def _recommendation_generate_accepts_keywords(generate_method: Any) -> bool:
        """Return whether a recommendation generate callable accepts kwargs."""
        target = getattr(generate_method, "side_effect", None) or generate_method
        try:
            signature = inspect.signature(target)
        except (TypeError, ValueError):
            return True
        return any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            or parameter.name in {"opponent_stats", "preflop_actions"}
            for parameter in signature.parameters.values()
        )

    def _generate_recommendation_without_stats(
        self,
        game_state: GameState,
        preflop_actions: list[ActionRecord] | None,
    ) -> Recommendation:
        """Generate a recommendation for legacy test doubles without stats kwargs."""
        try:
            return self._recommendation_engine.generate(
                game_state,
                preflop_actions=preflop_actions,
            )
        except TypeError as exc:
            if "preflop_actions" not in str(exc):
                raise
            return self._recommendation_engine.generate(game_state)

    def _get_opponent_stats_for_strategy(self, game_state: GameState) -> dict[str, Any]:
        """Fetch seat-keyed opponent stats for strategy generation."""
        if self._hand_manager is None:
            return {}
        try:
            stats = self._hand_manager.get_opponent_stats(game_state)
        except Exception:
            logger.warning(
                "Failed to fetch opponent stats, continuing without stats",
                exc_info=True,
            )
            return {}
        return stats if isinstance(stats, dict) else {}

    def _process_fold_badge_detection(
        self,
        game_state: GameState,
        fold_results: dict[int, bool],
    ) -> None:
        """Generate FOLD actions from latched fold badge detection.

        Hero folds clear the hero-card cache immediately, then HandManager
        consumes the generated FOLD action while table-hand observation continues.

        Args:
            game_state: Current frame state after action estimation.
            fold_results: Mapping of seat number to fold-badge status.
        """
        players_in_hand = self._hand_manager.get_players_in_hand()

        if 1 in players_in_hand and fold_results.get(1, False):
            logger.info("Hero FOLD detected via badge for seat 1")
            game_state.actions_since_last_frame.append(
                ActionRecord(
                    seat=1,
                    action="FOLD",
                    amount=0,
                    confidence="high",
                )
            )
            self._clear_hero_card_cache("hero fold badge detected")

        for seat in range(2, 7):
            if seat not in players_in_hand:
                continue
            if not fold_results.get(seat, False):
                continue

            logger.info("FOLD detected via badge for seat %d", seat)
            game_state.actions_since_last_frame.append(
                ActionRecord(
                    seat=seat,
                    action="FOLD",
                    amount=0,
                    confidence="high",
                )
            )

    def _apply_seat_card_visibility(
        self,
        game_state: GameState,
        seat_card_results: dict[int, bool],
    ) -> None:
        """Apply SeatCardDetector results to PlayerState.cards_visible."""
        for seat in range(2, 7):
            seat_key = str(seat)
            player = game_state.players.get(seat_key)
            if player is None:
                continue
            player.cards_visible = bool(
                player.is_seated and seat_card_results.get(seat, False)
            )

    def _is_seat_card_detection_allowed(self) -> bool:
        """Return whether opponent card detection may run for the current frame."""
        hand_start = getattr(self._hand_manager, "_hand_start_monotonic", None)
        if hand_start is None:
            return True
        elapsed = time.monotonic() - hand_start
        return elapsed >= self._hand_start_grace_sec

    def _process_seat_card_detection(
        self,
        game_state: GameState,
        seat_card_results: dict[int, bool],
    ) -> None:
        """Track consecutive no-card detection without generating FOLD actions."""
        players_in_hand = self._hand_manager.get_players_in_hand()

        for seat in range(2, 7):
            if seat not in players_in_hand:
                self._seat_no_card_streak[seat] = 0
                continue

            if seat in self._seat_card_fold_latched:
                continue

            has_card = seat_card_results.get(seat, True)
            if has_card:
                if self._seat_no_card_streak.get(seat, 0) > 0:
                    logger.debug(
                        "Seat %d card visible again, clearing no-card streak",
                        seat,
                    )
                self._seat_no_card_streak[seat] = 0
                continue

            streak = self._seat_no_card_streak.get(seat, 0) + 1
            self._seat_no_card_streak[seat] = streak
            logger.debug(
                "Seat %d no-card streak: %d/%d",
                seat,
                streak,
                self._seat_card_fold_confirm_frames,
            )
            if streak < self._seat_card_fold_confirm_frames:
                continue

            player = game_state.players[str(seat)]
            logger.debug(
                "Seat-card absence reached fold threshold for seat %d; "
                "FOLD generation is disabled "
                "(streak=%d, cards_visible=%s, is_seated=%s, stack=%s, bet=%d)",
                seat,
                streak,
                player.cards_visible,
                player.is_seated,
                player.stack,
                player.bet,
            )

    def _sync_game_state_with_hand_manager(self, game_state: GameState) -> None:
        """Copy HandManager phase, hand ID, and active count into GameState."""
        old_phase = game_state.phase
        old_hand_id = game_state.hand_id
        game_state.phase = self._hand_manager.phase
        game_state.hand_id = self._hand_manager.hand_id
        if old_phase != game_state.phase:
            logger.debug(
                "Sync phase: %s -> %s (id=%s -> %s)",
                old_phase,
                game_state.phase,
                old_hand_id,
                game_state.hand_id,
            )
        players_in_hand = self._hand_manager.get_players_in_hand()
        hero_in_hand = 1 in players_in_hand
        game_state.hero.in_current_hand = hero_in_hand
        game_state.hero.has_folded = bool(
            getattr(self._hand_manager, "hero_folded", False)
        )
        if not game_state.table_visible:
            if game_state.active_player_count != 0:
                logger.info(
                    "active_player_count synced: %d -> 0 (table not visible)",
                    game_state.active_player_count,
                )
            game_state.active_player_count = 0
            game_state.hero.in_current_hand = False
            return

        active_count = 1 if hero_in_hand else 0
        active_count += sum(1 for seat in range(2, 7) if seat in players_in_hand)
        if active_count != game_state.active_player_count:
            logger.info(
                "active_player_count synced: %d -> %d (players_in_hand=%s)",
                game_state.active_player_count,
                active_count,
                sorted(players_in_hand),
            )
            game_state.active_player_count = active_count

    def _update_hand_position_lock(self, game_state: GameState) -> None:
        """Lock positions once at hand start and reuse them during the hand."""
        phase = self._hand_manager.phase
        if not game_state.table_visible:
            if self._hand_positions is not None or self._hand_dealer_seat is not None:
                logger.debug("Clearing locked hand positions: table not visible")
            self._hand_positions = None
            self._hand_dealer_seat = None
            game_state.hero.position = None
            return

        if phase in {"waiting", "hand_end"}:
            if self._hand_positions is not None or self._hand_dealer_seat is not None:
                logger.debug("Clearing locked hand positions: phase=%s", phase)
            self._hand_positions = None
            self._hand_dealer_seat = None
            game_state.hero.position = None
            return

        if self._hand_manager.hand_just_started:
            active_seats = self._active_seats_for_position(game_state)
            self._hand_dealer_seat = game_state.dealer_seat
            self._hand_positions = calculate_positions(
                self._hand_dealer_seat,
                active_seats,
            )
            logger.info(
                "Positions locked for hand: dealer=%s, active_seats=%s, "
                "positions=%s",
                self._hand_dealer_seat,
                active_seats,
                self._hand_positions,
            )

        self._apply_locked_positions(game_state)

    @staticmethod
    def _active_seats_for_position(game_state: GameState) -> list[int]:
        """Return seat numbers eligible for position assignment."""
        active_seats = [1]
        for seat_key, player in game_state.players.items():
            if player.in_current_hand:
                active_seats.append(int(seat_key))

        if len(active_seats) <= 1:
            for seat_key, player in game_state.players.items():
                if player.is_seated:
                    active_seats.append(int(seat_key))

        return sorted(set(active_seats))

    def _apply_locked_positions(self, game_state: GameState) -> None:
        """Apply locked dealer and hero position to a GameState."""
        if self._hand_positions is None:
            return
        game_state.dealer_seat = self._hand_dealer_seat
        game_state.hero.position = self._hand_positions.get(1)

    def _build_game_state(self, frame: np.ndarray, timestamp: float) -> GameState:
        """Build a GameState by running recognition modules."""
        game_state = create_empty_game_state()
        game_state.timestamp = datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        ).isoformat()
        game_state.frame_number = self._frame_number
        game_state.hand_id = self._hand_manager.hand_id
        game_state.phase = self._hand_manager.phase
        self._update_card_clear_wait_state(game_state.phase)

        if game_state.phase in {"waiting", "hand_end"}:
            self._clear_hero_card_cache(f"phase={game_state.phase}")

        if (
            self._cached_hero_cards is not None
            and game_state.phase not in {"waiting", "hand_end"}
        ):
            game_state.hero.cards = list(self._cached_hero_cards)
        else:
            hero_cards = self._card_recognizer.recognize_hero_cards(
                frame,
                log_info=False,
            )
            game_state.hero.cards = self._format_hero_cards(hero_cards)
            game_state.hero.cards_visible = not self._hero_cards_missing(
                game_state.hero.cards
            )
            if game_state.phase == "waiting":
                if self._waiting_for_card_clear:
                    if self._hero_cards_missing(hero_cards):
                        logger.debug("Waiting for card clear: cards cleared")
                        self._waiting_for_card_clear = False
                        self._hero_cards_missing_since_hand_end = True
                    else:
                        self._log_stale_waiting_cards(hero_cards)
                    game_state.hero.cards = None
                    game_state.hero.cards_visible = False
                elif self._hero_cards_missing(hero_cards):
                    self._hero_cards_missing_since_hand_end = True
                    current_log_key = str(hero_cards)
                    if current_log_key != self._last_waiting_log:
                        logger.info(
                            "Waiting: hero card recognition failed - result=%s",
                            hero_cards,
                        )
                        self._last_waiting_log = current_log_key
                    else:
                        logger.debug(
                            "Waiting: repeated hero card recognition failure "
                            "suppressed - result=%s",
                            hero_cards,
                        )
                elif self._should_suppress_stale_waiting_cards(game_state.hero.cards):
                    self._log_stale_waiting_cards(game_state.hero.cards)
                    game_state.hero.cards = None
                    game_state.hero.cards_visible = False
                else:
                    logger.info(
                        "Waiting: hero cards recognized - %s, starting hand",
                        hero_cards,
                    )
                    self._last_waiting_log = None
        game_state.hero.cards_visible = not self._hero_cards_missing(
            game_state.hero.cards
        )

        game_state.board = self._format_board_cards(
            self._card_recognizer.recognize_board_cards(frame)
        )
        game_state.board_card_count = self._card_recognizer.count_board_cards(frame)

        number_results = self._number_recognizer.recognize_all(frame)
        game_state.pot = int(number_results.get("pot") or 0)
        game_state.hero.stack = number_results.get("hero_stack")
        game_state.hero.bet = int(number_results.get("hero_bet") or 0)

        game_state.hero.is_my_turn = self._button_recognizer.detect_my_turn(frame)
        if game_state.hero.is_my_turn:
            game_state.buttons = self._build_button_state(frame)

        dealer_seat = self._dealer_recognizer.detect_dealer_seat(frame)
        fresh_dealer_detected = dealer_seat is not None
        if dealer_seat is not None:
            self._cached_dealer_seat = dealer_seat
            game_state.dealer_seat = dealer_seat
            if dealer_seat != self._last_detected_dealer_seat:
                logger.info(
                    "Dealer seat changed: %s -> %d (fresh)",
                    self._last_detected_dealer_seat,
                    dealer_seat,
                )
                self._last_detected_dealer_seat = dealer_seat
            logger.debug("Dealer seat detected: %d (fresh)", dealer_seat)
        else:
            game_state.dealer_seat = self._cached_dealer_seat
            logger.debug(
                "Dealer seat not detected, using cached: %s",
                self._cached_dealer_seat,
            )
        current_hand_id = self._hand_manager.hand_id
        if (
            current_hand_id is not None
            and self._player_names_captured_for_hand == current_hand_id
        ):
            player_names = dict(self._cached_player_names)
        elif current_hand_id is not None:
            players_in_hand = self._hand_manager.get_players_in_hand()
            raw_names = self._name_recognizer.recognize_player_names(frame)
            player_names: dict[str, str | None] = {}
            for seat in range(2, 7):
                seat_key = str(seat)
                if seat in players_in_hand:
                    ocr_name = raw_names.get(seat_key)
                    if ocr_name is not None:
                        player_names[seat_key] = ocr_name
                    else:
                        player_names[seat_key] = self._cached_player_names.get(seat_key)
                else:
                    player_names[seat_key] = None
            self._cached_player_names = dict(player_names)
            self._player_names_captured_for_hand = current_hand_id
            logger.info(
                "Player names locked for hand %d: %s",
                current_hand_id,
                {key: value for key, value in player_names.items() if value is not None},
            )
        else:
            player_names = self._name_recognizer.recognize_player_names(frame)
            for seat_key, name in player_names.items():
                if name is not None:
                    self._cached_player_names[seat_key] = name
        self._populate_players(game_state, number_results, player_names)
        self._update_table_visibility(game_state, fresh_dealer_detected)
        if not game_state.table_visible:
            self._clear_players_for_inactive_table(game_state)
        self._populate_position(game_state)

        game_state.hero.seat = 1
        return game_state

    def _update_card_clear_wait_state(self, current_phase: str) -> None:
        if (
            current_phase == "waiting"
            and self._last_hand_manager_phase not in {None, "waiting"}
        ):
            self._last_ended_hero_cards = self._current_visible_hero_cards()
            self._hero_cards_missing_since_hand_end = False
            self._waiting_for_card_clear = True
            self._clear_hero_card_cache("phase transition to waiting")
        self._last_hand_manager_phase = current_phase

    @staticmethod
    def _hero_cards_missing(cards: Any) -> bool:
        return cards is None or len(cards) < 2 or any(card is None for card in cards)

    def _current_visible_hero_cards(self) -> list[str | None] | None:
        """Return the best known visible hero cards from the ending hand."""
        if self._cached_hero_cards is not None:
            return list(self._cached_hero_cards)
        if (
            self._prev_state is not None
            and not self._hero_cards_missing(self._prev_state.hero.cards)
        ):
            return list(self._prev_state.hero.cards or [])
        return None

    def _should_suppress_stale_waiting_cards(
        self,
        hero_cards: list[str | None] | None,
    ) -> bool:
        """Return whether waiting-state hero cards are stale from the last hand."""
        return (
            self._last_ended_hero_cards is not None
            and hero_cards == self._last_ended_hero_cards
            and not self._hero_cards_missing_since_hand_end
        )

    def _log_stale_waiting_cards(self, hero_cards: Any) -> None:
        """Log stale waiting cards that are suppressed as a false hand start."""
        logger.info(
            "Waiting: hero cards recognized but suppressed as stale cards - "
            "current=%s last=%s",
            hero_cards,
            self._last_ended_hero_cards,
        )

    def _build_button_state(self, frame: np.ndarray) -> ButtonState | None:
        button_result = self._button_recognizer.classify_buttons(frame)
        if not button_result:
            return None
        return ButtonState(
            fold=bool(button_result.get("fold", False)),
            call_or_check=button_result.get("call_or_check"),
            raise_or_bet=button_result.get("raise_or_bet"),
            bet_size=button_result.get("bet_size"),
        )

    def _populate_players(
        self,
        game_state: GameState,
        number_results: dict[str, Any],
        player_names: dict[str, str | None],
    ) -> None:
        player_stacks = number_results.get("player_stacks", {})
        player_bets = number_results.get("player_bets", {})
        players_in_hand = self._hand_manager.get_players_in_hand()

        for seat in range(2, 7):
            seat_key = str(seat)
            stack = player_stacks.get(seat_key)
            bet = player_bets.get(seat_key)
            is_seated = stack is not None
            existing_player = game_state.players.get(seat_key, PlayerState())
            game_state.players[seat_key] = PlayerState(
                name=player_names.get(seat_key),
                stack=stack,
                bet=int(bet or 0),
                is_seated=is_seated,
                cards_visible=existing_player.cards_visible,
                in_current_hand=seat in players_in_hand,
            )

        hero_in_hand = 1 in players_in_hand
        game_state.hero.in_current_hand = hero_in_hand
        game_state.hero.has_folded = bool(
            getattr(self._hand_manager, "hero_folded", False)
        )
        active_count = 1 if hero_in_hand else 0
        active_count += sum(1 for seat in range(2, 7) if seat in players_in_hand)
        game_state.active_player_count = active_count
        logger.debug(
            "Active player count: %d (players_in_hand=%s)",
            game_state.active_player_count,
            sorted(players_in_hand),
        )

    def _update_table_visibility(
        self,
        game_state: GameState,
        fresh_dealer_detected: bool,
    ) -> None:
        """Update table visibility state using current-frame recognition signals."""
        seated_count = sum(
            1 for player in game_state.players.values() if player.is_seated
        )
        strong_signal = (
            game_state.hero.cards_visible
            or game_state.board_card_count > 0
            or fresh_dealer_detected
            or game_state.hero.is_my_turn
        )
        weak_signal = game_state.pot > 0 and seated_count >= 2
        detected = strong_signal or weak_signal

        if detected:
            self._table_active_streak += 1
            self._table_inactive_streak = 0
        else:
            self._table_inactive_streak += 1
            self._table_active_streak = 0

        previous = self._table_visible
        if self._table_active_streak >= self._table_active_confirm_frames:
            self._table_visible = True
        elif self._table_inactive_streak >= self._table_inactive_confirm_frames:
            self._table_visible = False

        game_state.table_visible = self._table_visible
        if previous != self._table_visible:
            logger.info(
                "Table visibility changed: %s -> %s "
                "(strong=%s, weak=%s, seated=%d, board=%d, pot=%d, "
                "fresh_dealer=%s)",
                previous,
                self._table_visible,
                strong_signal,
                weak_signal,
                seated_count,
                game_state.board_card_count,
                game_state.pot,
                fresh_dealer_detected,
            )

    def _clear_players_for_inactive_table(self, game_state: GameState) -> None:
        """Clear stale player OCR values when the table is not visible."""
        for seat in range(2, 7):
            game_state.players[str(seat)] = PlayerState()

        game_state.active_player_count = 0
        game_state.dealer_seat = None
        game_state.hero.position = None
        game_state.hero.cards = None
        game_state.hero.cards_visible = False
        game_state.hero.is_my_turn = False
        game_state.hero.in_current_hand = False
        logger.debug("Inactive table: cleared stale player and hero visibility state")

    def _populate_position(self, game_state: GameState) -> None:
        if not game_state.table_visible:
            game_state.hero.position = None
            return

        if game_state.phase in {"waiting", "hand_end"}:
            game_state.hero.position = None
            return

        if self._hand_positions is not None:
            self._apply_locked_positions(game_state)
            return

        if game_state.dealer_seat is None:
            game_state.hero.position = None
            return

        active_seats = self._active_seats_for_position(game_state)
        positions = calculate_positions(game_state.dealer_seat, active_seats)
        game_state.hero.position = get_hero_position(positions, hero_seat=1)

    def _clear_hero_card_cache(self, reason: str) -> None:
        if self._cached_hero_cards is not None or self._partial_hero_cards is not None:
            logger.debug("Hero cards cache cleared: %s", reason)
        self._cached_hero_cards = None
        self._partial_hero_cards = None

    def _manage_hero_card_cache(self, game_state: GameState) -> None:
        current_hand_id = self._hand_manager.hand_id
        current_phase = self._hand_manager.phase
        if current_phase in {"waiting", "hand_end"}:
            self._clear_hero_card_cache(f"phase={current_phase}")
            self._cached_hand_id = current_hand_id
            return

        if current_hand_id != self._cached_hand_id:
            previous_hand_id = self._cached_hand_id
            self._clear_hero_card_cache(
                f"hand_id changed {previous_hand_id} -> {current_hand_id}",
            )
            logger.debug(
                "Hero cards cache cleared: hand_id changed %s -> %s",
                previous_hand_id,
                current_hand_id,
            )
            self._cached_hand_id = current_hand_id

        if (
            self._cached_hero_cards is None
            and game_state.hero.cards is not None
            and len(game_state.hero.cards) == 2
            and all(card is not None for card in game_state.hero.cards)
        ):
            self._cached_hero_cards = list(game_state.hero.cards)
            self._cached_hand_id = current_hand_id
            logger.debug("Hero cards cached: %s", self._cached_hero_cards)

        elif (
            self._cached_hero_cards is None
            and game_state.hero.cards is not None
            and len(game_state.hero.cards) == 2
        ):
            if self._partial_hero_cards is None:
                self._partial_hero_cards = [None, None]

            for index, card in enumerate(game_state.hero.cards):
                if card is not None and self._partial_hero_cards[index] is None:
                    self._partial_hero_cards[index] = card

            if all(card is not None for card in self._partial_hero_cards):
                self._cached_hero_cards = list(self._partial_hero_cards)
                self._partial_hero_cards = None
                game_state.hero.cards = list(self._cached_hero_cards)
                logger.debug("Hero cards completed from partial cache")

        if self._cached_hero_cards is not None:
            game_state.hero.cards = list(self._cached_hero_cards)

    def _format_hero_cards(self, cards_result: Any) -> list[str | None] | None:
        if cards_result is None:
            return None

        formatted: list[str | None] = []
        has_any_card = False
        for card in cards_result:
            if card is None:
                formatted.append(None)
                continue
            if isinstance(card, str):
                formatted.append(card)
                has_any_card = True
            elif isinstance(card, (tuple, list)) and len(card) == 2:
                formatted.append(f"{card[0]}{card[1]}")
                has_any_card = True
            else:
                formatted.append(None)
        return formatted if len(formatted) == 2 and has_any_card else None

    def _format_board_cards(self, cards_result: Any) -> list[str]:
        if cards_result is None:
            return []

        formatted: list[str] = []
        for card in cards_result:
            if isinstance(card, str):
                formatted.append(card)
            elif isinstance(card, (tuple, list)) and len(card) == 2:
                formatted.append(f"{card[0]}{card[1]}")
        return formatted

    def _dict_to_action_record(self, value: dict[str, Any]) -> ActionRecord:
        return ActionRecord(
            seat=int(value.get("seat", 0)),
            action=str(value.get("action", "UNKNOWN")),
            amount=int(value.get("amount", 0)),
            confidence=str(value.get("confidence", "low")),
        )
