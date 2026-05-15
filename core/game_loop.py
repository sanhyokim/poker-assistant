"""Main polling loop that builds GameState objects from captured frames."""

import copy
import inspect
import logging
import threading
import time
from dataclasses import dataclass
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

HERO_NON_FOLD_ACTIONS = {"CHECK", "CALL", "BET", "RAISE", "ALL_IN"}
HERO_FOLD_BADGE_RECENT_ACTION_GUARD_SEC = 1.0


@dataclass
class _AsyncRecommendationResult:
    """Completed async solver result keyed by request id."""

    request_id: int
    recommendation: Recommendation | None = None
    error: Exception | None = None


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
        hud_computing_callback: Callable[[str], None] | None = None,
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
        self._visual_obstruction_active = False
        self._visual_obstruction_until = 0.0
        self._visual_obstruction_hold_sec = 1.0
        self._visual_obstruction_recovery_until = 0.0
        self._visual_obstruction_recovery_sec = 1.5
        self._last_seat_card_states: dict[int, bool] = {}
        self._seat_card_confirmed: set[int] = set()

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
        self._stale_suppression_start_time: float | None = None
        self._stale_suppression_bypassed: bool = False
        self._last_hand_manager_phase: str | None = None
        self._previous_recommendation: Recommendation | None = None
        self._previous_recommendation_context: dict[str, object] | None = None
        self._last_strategy_phase: str | None = None
        self._last_hero_non_fold_action_time: float | None = None
        self._last_hero_non_fold_action_name: str | None = None
        self._hero_fold_badge_ignored_for_hand: bool = False
        self._hero_fold_badge_ignored_reason: str | None = None

        # Async HU postflop solver worker state
        self._pending_recommendation_lock = threading.Lock()
        self._pending_recommendation_thread: threading.Thread | None = None
        self._pending_recommendation_context: dict[str, object] | None = None
        self._pending_recommendation_id: int = 0
        self._pending_recommendation_active_id: int | None = None
        self._pending_recommendation_completed: dict[
            int, _AsyncRecommendationResult
        ] = {}
        self._pending_recommendation_cancelled_ids: set[int] = set()

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

    def stop(self, reason: str = "user_stop") -> None:
        """Request polling loop stop."""
        self._running = False
        self._cached_player_names = {}
        self._cached_hero_cards = None
        self._cached_hand_id = None
        self._previous_recommendation = None
        self._previous_recommendation_context = None
        self._clear_pending_state()
        self._last_recommendation_log = None
        self._last_strategy_is_my_turn = False
        self._hero_fold_badge_ignored_for_hand = False
        self._hero_fold_badge_ignored_reason = None
        self._notify_hud(None)
        self._abandon_active_hand(reason)

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
                if self._is_visual_obstruction_active():
                    logger.info(
                        "NEW_HAND suppressed: visual obstruction active "
                        "(until %.1fs from now)",
                        self._visual_obstruction_until - time.monotonic(),
                    )
                    game_state.game_event = None
                elif self._hand_manager.phase not in {"preflop", "flop", "turn", "river"}:
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
            self._visual_obstruction_active = False
            self._visual_obstruction_until = 0.0
            self._visual_obstruction_recovery_until = 0.0
            self._last_seat_card_states.clear()
            self._seat_card_confirmed.clear()
            self._last_hero_non_fold_action_time = None
            self._last_hero_non_fold_action_name = None
            self._hero_fold_badge_ignored_for_hand = False
            self._hero_fold_badge_ignored_reason = None
            self._waiting_for_card_clear = False
            self._hero_cards_missing_since_hand_end = False
            self._last_ended_hero_cards = None
            self._stale_suppression_start_time = None
            self._stale_suppression_bypassed = False
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

        if (
            self._is_visual_obstruction_protected()
            and self._prev_state is not None
            and game_state.pot < self._prev_state.pot
            and self._prev_state.pot > 0
        ):
            logger.info(
                "Pot decrease ignored during visual obstruction/recovery: "
                "prev_pot=%d curr_pot=%d phase=%s",
                self._prev_state.pot,
                game_state.pot,
                self._hand_manager.phase,
            )
            game_state.pot = self._prev_state.pot

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
        self._stale_suppression_start_time = None
        self._stale_suppression_bypassed = False
        self._last_hand_manager_phase = None
        self._previous_recommendation = None
        self._previous_recommendation_context = None
        self._clear_pending_state()
        self._last_strategy_phase = None
        self._last_hero_non_fold_action_time = None
        self._last_hero_non_fold_action_name = None
        self._hero_fold_badge_ignored_for_hand = False
        self._hero_fold_badge_ignored_reason = None
        self._diff_detector.reset()
        self._action_estimator.reset()
        self._fold_badge_detector.reset()
        self._seat_card_detector.reset()
        self._seat_no_card_streak.clear()
        self._seat_card_fold_latched.clear()
        self._table_visible = False
        self._table_inactive_streak = 0
        self._table_active_streak = 0
        self._visual_obstruction_active = False
        self._visual_obstruction_until = 0.0
        self._visual_obstruction_recovery_until = 0.0
        self._last_seat_card_states.clear()
        self._seat_card_confirmed.clear()
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
            # Quick connectivity check
            self._check_llm_connectivity(llm_pipeline)
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

    def _check_llm_connectivity(self, llm_pipeline: Any) -> None:
        """Run a quick LLM API connectivity check at startup."""
        import requests

        api_key = getattr(llm_pipeline, "api_key", None)
        model = getattr(llm_pipeline, "model_default", None)
        if not api_key:
            logger.warning(
                "LLM startup check: OPENROUTER_API_KEY is not set. "
                "LLM features will use fallback."
            )
            return

        try:
            provider_config = None
            get_provider_config = getattr(
                llm_pipeline,
                "openrouter_provider_config",
                None,
            )
            if callable(get_provider_config):
                provider_config = get_provider_config()
            payload: dict[str, Any] = {
                "model": model or "deepseek/deepseek-chat",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 16,
            }
            if provider_config is not None:
                payload["provider"] = provider_config
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=5,
            )
            if response.status_code == 200:
                logger.info(
                    "LLM startup check: OK (model=%s, status=200)",
                    model,
                )
            else:
                logger.warning(
                    "LLM startup check: FAILED (model=%s, status=%d, body=%s). "
                    "LLM features may use fallback. "
                    "Check .env OPENROUTER_API_KEY and model name.",
                    model,
                    response.status_code,
                    response.text[:500],
                )
        except Exception as exc:
            logger.warning(
                "LLM startup check: connection error (%s). "
                "LLM features may use fallback.",
                exc,
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
        self.stop(reason="capture_lost")

    def _abandon_active_hand(self, reason: str) -> bool:
        """Abandon an active hand and clear strategy/HUD state."""
        if self._hand_manager is None:
            return False
        try:
            abandoned = self._hand_manager.abandon_current_hand(reason)
        except Exception:
            logger.warning("HandManager abandon failed: reason=%s", reason, exc_info=True)
            return False

        if not abandoned:
            return False

        self._previous_recommendation = None
        self._previous_recommendation_context = None
        self._clear_pending_state()
        self._last_recommendation_log = None
        self._last_strategy_is_my_turn = False
        self._notify_hud(None)
        return True

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
            self._previous_recommendation_context = None
            self._clear_pending_state()
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
            self._previous_recommendation_context = None
            self._clear_pending_state()
            self._last_strategy_is_my_turn = False
            self._notify_hud(None)
            self._save_human_action_to_hand_manager(game_state)
            return

        # NEW_HAND / ストリート変化時にリセット
        if game_state.game_event == "NEW_HAND":
            self._last_recommendation_log = None
            self._previous_recommendation = None
            self._previous_recommendation_context = None
            self._clear_pending_state()

        if phase == "preflop" and self._last_strategy_phase != "preflop":
            self._previous_recommendation = None
            self._previous_recommendation_context = None
            self._clear_pending_state()

        if game_state.game_event == "NEW_STREET":
            self._last_recommendation_log = None
            self._clear_pending_state()
            if self._hand_manager is not None:
                game_state.phase = self._hand_manager.phase

        # ヒーローのターンでなければ推奨をクリアして終了
        if not game_state.hero.is_my_turn:
            # ターン終了時（True→False）にクリア
            if self._last_strategy_is_my_turn:
                self._last_recommendation_log = None
                self._previous_recommendation = None
                self._previous_recommendation_context = None
                self._clear_pending_state()
                self._notify_hud(None)
            self._save_human_action_to_hand_manager(game_state)
            self._last_strategy_phase = phase
            self._last_strategy_is_my_turn = False
            return

        # === ここから is_my_turn=True のみ ===

        # Cached recommendation freshness guard
        if (
            self._last_strategy_is_my_turn
            and self._previous_recommendation is not None
            and self._previous_recommendation_context is not None
            and not self._is_recommendation_context_still_valid(
                self._previous_recommendation_context, game_state
            )
        ):
            logger.info(
                "Cached recommendation discarded: context no longer valid "
                "(phase=%s board_count=%d hand_id=%s)",
                phase,
                len(game_state.board or []),
                game_state.hand_id,
            )
            self._previous_recommendation = None
            self._previous_recommendation_context = None
            self._notify_hud(None)

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
                self._notify_hud_computing("CHART CHECKING...")
                self._revalidate_seat_cards_before_strategy(game_state)
                snapshot = self._build_recommendation_context_snapshot(game_state)
                recommendation = self._generate_recommendation(
                    game_state,
                    preflop_actions=self._get_preflop_actions_for_strategy(),
                )
                if not self._is_recommendation_context_still_valid(
                    snapshot, game_state
                ):
                    logger.info(
                        "Stale recommendation discarded: "
                        "phase=%s board_count=%d actions=%d "
                        "hero_is_my_turn=%s hero_in_hand=%s",
                        phase,
                        len(game_state.board or []),
                        len(game_state.current_street_actions or []),
                        game_state.hero.is_my_turn,
                        game_state.hero.in_current_hand,
                    )
                    self._save_human_action_to_hand_manager(game_state)
                    self._last_strategy_phase = phase
                    self._last_strategy_is_my_turn = True
                    return
                self._log_recommendation("Preflop recommendation", recommendation)
                self._log_recommendation_change(recommendation)
                self._save_recommendation_to_hand_manager(
                    recommendation, strategy_started_at
                )
                self._previous_recommendation = recommendation
                self._previous_recommendation_context = snapshot
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

                # Guard: skip strategy when board count does not match the phase
                expected_board_counts: dict[str, int] = {
                    "flop": 3,
                    "turn": 4,
                    "river": 5,
                }
                expected_bc = expected_board_counts.get(phase)
                if expected_bc is not None and len(game_state.board or []) != expected_bc:
                    logger.warning(
                        "Strategy skipped: phase/board_count mismatch "
                        "phase=%s board_count=%s expected=%s",
                        phase,
                        len(game_state.board or []),
                        expected_bc,
                    )
                    self._save_human_action_to_hand_manager(game_state)
                    self._last_strategy_phase = phase
                    self._last_strategy_is_my_turn = True
                    return

                self._revalidate_seat_cards_before_strategy(game_state)

                if game_state.active_player_count == 2:
                    # --- Heads-up: async solver via worker thread ---
                    self._notify_hud_computing("SOLVER THINKING...")

                    # Poll for a completed async result
                    recommendation = self._poll_async_recommendation_result(
                        game_state,
                    )
                    if recommendation is not None:
                        guarded = self._guard_postflop_recommendation_source(
                            recommendation, game_state, phase, "Async",
                        )
                        if guarded is None:
                            self._save_human_action_to_hand_manager(
                                game_state,
                            )
                            self._last_strategy_phase = phase
                            self._last_strategy_is_my_turn = True
                            return
                        recommendation = guarded
                        self._log_recommendation(
                            "Postflop recommendation", recommendation,
                        )
                        self._log_recommendation_change(recommendation)
                        self._save_recommendation_to_hand_manager(
                            recommendation, strategy_started_at,
                        )
                        self._previous_recommendation = recommendation
                        self._previous_recommendation_context = (
                            self._build_recommendation_context_snapshot(
                                game_state,
                            )
                        )
                        self._notify_hud(recommendation)
                    else:
                        # No result yet: start solver if not already running
                        if not self._is_pending_recommendation_alive():
                            snapshot = (
                                self._build_recommendation_context_snapshot(
                                    game_state,
                                )
                            )
                            self._start_async_postflop_recommendation(
                                game_state, snapshot,
                            )
                        else:
                            with self._pending_recommendation_lock:
                                request_id = self._pending_recommendation_active_id
                            logger.debug(
                                "Async recommendation already pending/alive: "
                                "request_id=%s",
                                request_id,
                            )
                        self._save_human_action_to_hand_manager(game_state)
                        self._last_strategy_phase = phase
                        self._last_strategy_is_my_turn = True
                        return

                elif game_state.active_player_count >= 3:
                    # --- Multiway: synchronous LLM (unchanged) ---
                    self._notify_hud_computing("LLM ANALYZING...")
                    snapshot = self._build_recommendation_context_snapshot(
                        game_state,
                    )
                    recommendation = self._generate_recommendation(game_state)
                    if not self._is_recommendation_context_still_valid(
                        snapshot, game_state
                    ):
                        logger.info(
                            "Stale recommendation discarded: "
                            "phase=%s board_count=%d actions=%d "
                            "hero_is_my_turn=%s hero_in_hand=%s",
                            phase,
                            len(game_state.board or []),
                            len(game_state.current_street_actions or []),
                            game_state.hero.is_my_turn,
                            game_state.hero.in_current_hand,
                        )
                        self._save_human_action_to_hand_manager(game_state)
                        self._last_strategy_phase = phase
                        self._last_strategy_is_my_turn = True
                        return
                    guarded = self._guard_postflop_recommendation_source(
                        recommendation, game_state, phase, "Synchronous",
                    )
                    if guarded is None:
                        self._save_human_action_to_hand_manager(game_state)
                        self._last_strategy_phase = phase
                        self._last_strategy_is_my_turn = True
                        return
                    recommendation = guarded
                    self._log_recommendation(
                        "Postflop recommendation", recommendation,
                    )
                    self._log_recommendation_change(recommendation)
                    self._save_recommendation_to_hand_manager(
                        recommendation, strategy_started_at,
                    )
                    self._previous_recommendation = recommendation
                    self._previous_recommendation_context = snapshot
                    self._notify_hud(recommendation)

                else:
                    # --- Fallback (active < 2): synchronous (unchanged) ---
                    self._notify_hud_computing("Computing...")
                    snapshot = self._build_recommendation_context_snapshot(
                        game_state,
                    )
                    recommendation = self._generate_recommendation(game_state)
                    if not self._is_recommendation_context_still_valid(
                        snapshot, game_state
                    ):
                        logger.info(
                            "Stale recommendation discarded: "
                            "phase=%s board_count=%d actions=%d "
                            "hero_is_my_turn=%s hero_in_hand=%s",
                            phase,
                            len(game_state.board or []),
                            len(game_state.current_street_actions or []),
                            game_state.hero.is_my_turn,
                            game_state.hero.in_current_hand,
                        )
                        self._save_human_action_to_hand_manager(game_state)
                        self._last_strategy_phase = phase
                        self._last_strategy_is_my_turn = True
                        return
                    guarded = self._guard_postflop_recommendation_source(
                        recommendation, game_state, phase, "Synchronous",
                    )
                    if guarded is None:
                        self._save_human_action_to_hand_manager(game_state)
                        self._last_strategy_phase = phase
                        self._last_strategy_is_my_turn = True
                        return
                    recommendation = guarded
                    self._log_recommendation(
                        "Postflop recommendation", recommendation,
                    )
                    self._log_recommendation_change(recommendation)
                    self._save_recommendation_to_hand_manager(
                        recommendation, strategy_started_at,
                    )
                    self._previous_recommendation = recommendation
                    self._previous_recommendation_context = snapshot
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

    def _notify_hud_computing(self, message: str = "Computing...") -> None:
        """Notify the HUD callback that computation is in progress.

        Args:
            message: Processing status text to display.
        """
        if self._hud_computing_callback is None:
            return
        try:
            self._hud_computing_callback(message)
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

    def _revalidate_seat_cards_before_strategy(self, game_state: GameState) -> None:
        """Re-scan seat cards and promote incorrectly excluded seats.

        Args:
            game_state: Current game state, mutated when seats are promoted.
        """
        capture = getattr(self, "_capture", None)
        seat_card_detector = getattr(self, "_seat_card_detector", None)
        if capture is None or seat_card_detector is None:
            return

        frame = capture.get_frame()
        if frame is None:
            return

        seat_card_results = seat_card_detector.detect_all(frame)
        players_in_hand = self._hand_manager.get_players_in_hand()
        changed = False

        for seat in range(2, 7):
            if seat in players_in_hand:
                continue
            if not seat_card_results.get(seat, False):
                continue

            seat_key = str(seat)
            player = game_state.players.get(seat_key)
            if player is None or not player.is_seated:
                continue

            promoted = self._hand_manager.rejoin_seat(seat)
            if not promoted:
                continue

            player.in_current_hand = True
            player.cards_visible = True
            changed = True
            logger.info(
                "Auto-revalidation: seat %d promoted to in_current_hand "
                "(cards detected)",
                seat,
            )

        if changed:
            new_active = len(self._hand_manager.get_players_in_hand())
            if new_active != game_state.active_player_count:
                logger.info(
                    "Auto-revalidation updated active_player_count: %d -> %d",
                    game_state.active_player_count,
                    new_active,
                )
                game_state.active_player_count = new_active

    def request_rejoin_seat(self, seat: int) -> bool:
        """Attempt to rejoin a seat via manual UI request.

        Uses multiple signals before giving up: recent card detection state,
        confirmed-seat cache, and up to 3 re-scans.

        Args:
            seat: Seat number from 2 to 6.

        Returns:
            True if the seat was successfully promoted.
        """
        if seat < 2 or seat > 6:
            logger.warning("Rejoin request for invalid seat %d", seat)
            return False

        # 1. Recently-sighted state still shows cards → allow
        if self._last_seat_card_states.get(seat, False):
            logger.info(
                "Rejoin allowed for seat %d: card recently sighted",
                seat,
            )
            return self._promote_rejoin(seat)

        # 2. Seat was previously confirmed with cards → allow
        if seat in self._seat_card_confirmed:
            logger.info(
                "Rejoin allowed for seat %d: seat was previously confirmed",
                seat,
            )
            return self._promote_rejoin(seat)

        # 3. Re-scan up to 3 times; succeed on the first positive detection
        for attempt in range(3):
            frame = self._capture.get_frame()
            if frame is None:
                continue
            results = self._seat_card_detector.detect_all(frame)
            if results.get(seat, False):
                logger.info(
                    "Rejoin allowed for seat %d: card detected on rescan "
                    "(attempt %d)",
                    seat,
                    attempt + 1,
                )
                return self._promote_rejoin(seat)

        logger.info(
            "Rejoin rejected for seat %d: no card detected after retries",
            seat,
        )
        return False

    def _promote_rejoin(self, seat: int) -> bool:
        """Promote a seat back into the current hand via HandManager.

        Returns:
            True if the seat was promoted, False if already in hand or folded.
        """
        promoted = self._hand_manager.rejoin_seat(seat)
        if promoted:
            logger.info(
                "Manual rejoin: seat %d promoted to in_current_hand", seat,
            )
        else:
            logger.info(
                "Manual rejoin: seat %d was not promoted "
                "(already in hand or folded)",
                seat,
            )
        return promoted

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

    @staticmethod
    def _build_recommendation_context_snapshot(game_state: GameState) -> dict[str, object]:
        """Build a lightweight snapshot of the decision point for freshness checks.

        Captured before calling the recommendation engine so the caller can
        detect whether the game state changed while the recommendation was
        being computed (e.g. Solver timeout).

        Returns:
            Snapshot dictionary with enough fields to detect a stale result.
        """
        board = tuple(game_state.board or [])
        return {
            "hand_id": game_state.hand_id,
            "phase": game_state.phase,
            "board": board,
            "board_count": len(board),
            "pot": game_state.pot,
            "active_player_count": game_state.active_player_count,
            "current_street_actions_count": len(
                game_state.current_street_actions or []
            ),
            "hero_is_my_turn": bool(game_state.hero.is_my_turn),
            "hero_in_current_hand": bool(game_state.hero.in_current_hand),
        }

    @staticmethod
    def _is_recommendation_context_still_valid(
        snapshot: dict[str, object],
        current_state: GameState,
    ) -> bool:
        """Return whether the decision-point snapshot is still valid.

        A stale recommendation is one where the hand, street, board, or hero
        situation has changed since the solver/fallback request was made.
        Pot is intentionally not checked because OCR noise and pot-spike
        hold logic can cause false mismatches.
        """
        current_board = tuple(current_state.board or [])
        if snapshot.get("hand_id") != current_state.hand_id:
            return False
        if snapshot.get("phase") != current_state.phase:
            return False
        if snapshot.get("board") != current_board:
            return False
        if snapshot.get("board_count") != len(current_board):
            return False
        if snapshot.get("active_player_count") != current_state.active_player_count:
            return False
        if snapshot.get("current_street_actions_count") != len(
            current_state.current_street_actions or []
        ):
            return False
        if not bool(current_state.hero.is_my_turn):
            return False
        if not bool(current_state.hero.in_current_hand):
            return False
        if current_state.phase in {"waiting", "hand_end"}:
            return False
        return True

    # ------------------------------------------------------------------
    # Async HU postflop solver worker
    # ------------------------------------------------------------------

    def _cancel_pending_recommendation(self, reason: str) -> None:
        """Mark the active async recommendation request as cancelled.

        Args:
            reason: Human-readable cancellation reason for diagnostics.
        """
        with self._pending_recommendation_lock:
            self._cancel_pending_recommendation_locked(reason)

    def _cancel_pending_recommendation_locked(self, reason: str) -> None:
        """Mark the active request cancelled while holding the pending lock."""
        active_id = self._pending_recommendation_active_id
        if active_id is None:
            return
        self._pending_recommendation_cancelled_ids.add(active_id)
        logger.info(
            "Async recommendation cancelled: request_id=%d reason=%s",
            active_id,
            reason,
        )

    def _clear_pending_state(self) -> None:
        """Clear active pending metadata without stopping the worker thread."""
        with self._pending_recommendation_lock:
            active_id = self._pending_recommendation_active_id
            if active_id is not None:
                self._cancel_pending_recommendation_locked("pending_cleared")
            thread = self._pending_recommendation_thread
            if thread is not None and not thread.is_alive():
                self._pending_recommendation_thread = None
            self._pending_recommendation_context = None
            self._pending_recommendation_active_id = None
            self._cleanup_async_recommendation_state_locked()

    def _is_pending_recommendation_alive(self) -> bool:
        """Return True when a background solver thread is still running."""
        with self._pending_recommendation_lock:
            thread = self._pending_recommendation_thread
            return thread is not None and thread.is_alive()

    def _cleanup_async_recommendation_state_locked(
        self,
        request_id: int | None = None,
    ) -> None:
        """Bound completed and cancelled async request bookkeeping.

        The caller must hold _pending_recommendation_lock.
        """
        newest_id = self._pending_recommendation_id
        min_keep_id = max(0, newest_id - 16)
        for completed_id in list(self._pending_recommendation_completed):
            if completed_id < min_keep_id or completed_id == request_id:
                self._pending_recommendation_completed.pop(completed_id, None)
        for cancelled_id in list(self._pending_recommendation_cancelled_ids):
            if cancelled_id < min_keep_id or cancelled_id == request_id:
                self._pending_recommendation_cancelled_ids.discard(cancelled_id)

    def _start_async_postflop_recommendation(
        self,
        game_state: GameState,
        snapshot: dict[str, object],
    ) -> None:
        """Start a daemon worker thread for HU postflop solver computation.

        Args:
            game_state: Current GameState to deep-copy for the worker.
            snapshot: Decision-point snapshot for later freshness checks.
        """
        game_state_copy = copy.deepcopy(game_state)

        with self._pending_recommendation_lock:
            existing_thread = self._pending_recommendation_thread
            if existing_thread is not None and existing_thread.is_alive():
                logger.info(
                    "Async recommendation already pending/alive: request_id=%s",
                    self._pending_recommendation_active_id,
                )
                return
            self._pending_recommendation_id += 1
            request_id = self._pending_recommendation_id
            self._pending_recommendation_context = snapshot
            self._pending_recommendation_active_id = request_id
            self._pending_recommendation_cancelled_ids.discard(request_id)
            self._pending_recommendation_completed.pop(request_id, None)

            thread = threading.Thread(
                target=self._run_recommendation_worker,
                args=(request_id, game_state_copy),
                daemon=True,
            )
            self._pending_recommendation_thread = thread
        thread.start()

        logger.info(
            "Async recommendation started: request_id=%d phase=%s",
            request_id,
            snapshot.get("phase", "unknown"),
        )

    def _run_recommendation_worker(
        self,
        request_id: int,
        game_state_copy: GameState,
    ) -> None:
        """Target for the daemon thread: run solver and store result.

        The worker only produces a Recommendation.  It does NOT update
        HUD, hand_manager, or _previous_recommendation -- those decisions
        belong to the main polling thread after freshness validation.
        """
        logger.debug(
            "Solver worker starting: request_id=%d phase=%s",
            request_id,
            game_state_copy.phase,
        )
        recommendation: Recommendation | None = None
        error: Exception | None = None
        try:
            recommendation = self._generate_recommendation(game_state_copy)
            logger.debug(
                "Solver worker completed: request_id=%d action=%s amount=%d",
                request_id,
                recommendation.action,
                recommendation.amount,
            )
        except Exception as exc:
            error = exc
        with self._pending_recommendation_lock:
            self._pending_recommendation_completed[request_id] = (
                _AsyncRecommendationResult(
                    request_id=request_id,
                    recommendation=recommendation,
                    error=error,
                )
            )
        logger.info("Async recommendation completed: request_id=%d", request_id)

    def _poll_async_recommendation_result(
        self,
        current_state: GameState,
    ) -> Recommendation | None:
        """Check whether the worker thread finished with a valid result.

        Returns:
            The Recommendation if the worker completed and the context
            is still valid; None when no result is available yet, the
            context is stale, or an error occurred.
        """
        with self._pending_recommendation_lock:
            active_id = self._pending_recommendation_active_id
            if active_id is None:
                for completed_id in list(self._pending_recommendation_completed):
                    logger.info(
                        "Async recommendation discarded: request_id=%d "
                        "reason=inactive_request",
                        completed_id,
                    )
                    self._pending_recommendation_completed.pop(completed_id, None)
                    self._pending_recommendation_cancelled_ids.discard(completed_id)
                return None
            for completed_id in list(self._pending_recommendation_completed):
                if completed_id != active_id:
                    logger.info(
                        "Async recommendation discarded: request_id=%d "
                        "reason=inactive_request",
                        completed_id,
                    )
                    self._pending_recommendation_completed.pop(completed_id, None)
                    self._pending_recommendation_cancelled_ids.discard(completed_id)
            completed = self._pending_recommendation_completed.get(active_id)
            if completed is None:
                return None
            cancelled = active_id in self._pending_recommendation_cancelled_ids
            pending_ctx = self._pending_recommendation_context

        if cancelled:
            logger.info(
                "Async recommendation discarded: request_id=%d reason=cancelled",
                active_id,
            )
            with self._pending_recommendation_lock:
                self._finish_async_request_locked(active_id)
            return None

        if pending_ctx is None:
            logger.info(
                "Async recommendation discarded: request_id=%d reason=inactive_request",
                active_id,
            )
            with self._pending_recommendation_lock:
                self._finish_async_request_locked(active_id)
            return None

        if not self._is_recommendation_context_still_valid(
            pending_ctx, current_state
        ):
            logger.info(
                "Async recommendation discarded: request_id=%d reason=stale",
                active_id,
            )
            with self._pending_recommendation_lock:
                self._finish_async_request_locked(active_id)
            return None

        if completed.error is not None:
            logger.error(
                "Async recommendation failed: request_id=%d error=%s",
                active_id,
                completed.error,
            )
            with self._pending_recommendation_lock:
                self._finish_async_request_locked(active_id)
            return None

        result = completed.recommendation
        if result is None:
            logger.info(
                "Async recommendation discarded: request_id=%d reason=empty_result",
                active_id,
            )
            with self._pending_recommendation_lock:
                self._finish_async_request_locked(active_id)
            return None

        logger.info(
            "Async recommendation accepted: request_id=%d action=%s source=%s",
            active_id,
            result.action,
            result.strategy_source,
        )
        with self._pending_recommendation_lock:
            self._finish_async_request_locked(active_id)
        return result

    def _finish_async_request(self, request_id: int) -> None:
        """Clear a completed async request after poll has handled it."""
        with self._pending_recommendation_lock:
            self._finish_async_request_locked(request_id)

    def _finish_async_request_locked(self, request_id: int) -> None:
        """Clear a completed async request while holding the pending lock."""
        if self._pending_recommendation_active_id == request_id:
            self._pending_recommendation_active_id = None
            self._pending_recommendation_context = None
        thread = self._pending_recommendation_thread
        if thread is not None and not thread.is_alive():
            self._pending_recommendation_thread = None
        self._cleanup_async_recommendation_state_locked(request_id)

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
        hero_non_fold_action = self._get_hero_non_fold_action(
            game_state.actions_since_last_frame
        )
        if hero_non_fold_action is not None:
            self._last_hero_non_fold_action_time = time.monotonic()
            self._last_hero_non_fold_action_name = hero_non_fold_action.action

        if self._is_visual_obstruction_protected():
            for seat, detected in fold_results.items():
                if detected:
                    logger.info(
                        "Fold badge ignored during visual obstruction "
                        "or recovery: seat=%s",
                        seat,
                    )
            return

        if 1 in players_in_hand and fold_results.get(1, False):
            if self._hero_fold_badge_ignored_for_hand:
                logger.debug(
                    "Hero fold badge ignored due to prior non-fold action in "
                    "this hand: reason=%s",
                    self._hero_fold_badge_ignored_reason,
                )
            elif hero_non_fold_action is not None:
                logger.info(
                    "Hero fold badge ignored because non-fold hero action was "
                    "detected: action=%s",
                    hero_non_fold_action.action,
                )
                self._latch_hero_fold_badge_ignore(
                    "non_fold_action",
                    hero_non_fold_action.action,
                )
            elif self._has_recent_hero_non_fold_action():
                action_name = self._last_hero_non_fold_action_name or "unknown"
                action_time = self._last_hero_non_fold_action_time
                age = (
                    time.monotonic() - action_time
                    if action_time is not None
                    else 0.0
                )
                logger.info(
                    "Hero fold badge ignored because recent non-fold hero "
                    "action was detected: action=%s age=%.2fs",
                    action_name,
                    age,
                )
                self._latch_hero_fold_badge_ignore(
                    "recent_non_fold_action",
                    action_name,
                )
            else:
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

            if self._is_showdown_or_payout_guard_active(game_state):
                logger.info(
                    "Fold badge ignored during showdown guard: seat=%d "
                    "phase=%s board_count=%d",
                    seat,
                    self._hand_manager.phase,
                    len(game_state.board or []),
                )
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

    def _latch_hero_fold_badge_ignore(self, reason: str, action: str) -> None:
        """Ignore subsequent hero fold-badge latch results for this hand."""
        self._hero_fold_badge_ignored_for_hand = True
        self._hero_fold_badge_ignored_reason = reason
        logger.info(
            "Hero fold badge ignore latched for hand: reason=%s action=%s",
            reason,
            action,
        )

    @staticmethod
    def _get_hero_non_fold_action(
        actions: list[ActionRecord],
    ) -> ActionRecord | None:
        """Return the hero's non-fold action in the current frame, if any."""
        for action in actions:
            if action.seat == 1 and action.action in HERO_NON_FOLD_ACTIONS:
                return action
        return None

    def _has_recent_hero_non_fold_action(self) -> bool:
        """Return whether a recent hero non-fold action should suppress badge fold."""
        action_time = self._last_hero_non_fold_action_time
        if action_time is None:
            return False
        return (
            time.monotonic() - action_time
            <= HERO_FOLD_BADGE_RECENT_ACTION_GUARD_SEC
        )

    def _apply_seat_card_visibility(
        self,
        game_state: GameState,
        seat_card_results: dict[int, bool],
    ) -> None:
        """Apply SeatCardDetector results to PlayerState.cards_visible."""
        self._update_visual_obstruction(seat_card_results)
        obstruction_active = self._is_visual_obstruction_active()
        folded_seats = set(getattr(self._hand_manager, "_folded_seats", set()))
        players_in_hand_seats = self._hand_manager.get_players_in_hand()

        for seat in range(2, 7):
            seat_key = str(seat)
            player = game_state.players.get(seat_key)
            if player is None:
                continue
            previous_visible = self._previous_player_cards_visible(game_state, seat_key)
            detected_visible = bool(
                player.is_seated and seat_card_results.get(seat, False)
            )
            if obstruction_active and previous_visible and not detected_visible:
                player.cards_visible = True
                logger.debug(
                    "Cards visibility freeze during visual obstruction: seat=%s "
                    "keep=True",
                    seat,
                )
                continue
            if seat_key in folded_seats:
                player.cards_visible = True
                continue

            # Track seats with confirmed card detection (stable, non-transient)
            if detected_visible and seat in players_in_hand_seats:
                self._seat_card_confirmed.add(seat)

            # Guard 2: Active in-hand seat with temporary detection failure
            # Only protect seats that have been confirmed with cards at least once
            if (
                seat in players_in_hand_seats
                and not detected_visible
                and seat in self._seat_card_confirmed
            ):
                player.cards_visible = True
                logger.debug(
                    "Cards visibility preserved for confirmed active seat %d "
                    "(in_current_hand=True, detected=False, confirmed=True)",
                    seat,
                )
                continue
            player.cards_visible = bool(detected_visible)

        # Force in_current_hand=False for seats with no visible cards
        # that are not folded and not confirmed.
        #
        # cards_visible is an observation; in_current_hand is participation
        # state. A transient NO_CARD must not drop participation. Only force
        # in_current_hand=False when there is strong evidence the player is
        # truly gone.
        if self._hand_manager.phase in {"preflop", "flop", "turn", "river"}:
            showdown_guard = self._is_showdown_or_payout_guard_active(game_state)
            obstruction_protected = self._is_visual_obstruction_protected()
            hm_participant_observed = getattr(
                self._hand_manager, "_participant_observed_seats", set()
            )
            hm_participated = getattr(
                self._hand_manager, "_participated_seats", set()
            )
            for seat in range(2, 7):
                seat_key = str(seat)
                player = game_state.players.get(seat_key)
                if player is None:
                    continue
                if not player.cards_visible and player.in_current_hand:
                    if (
                        seat in folded_seats
                        or int(seat) in folded_seats
                        or str(seat) in folded_seats
                    ):
                        continue
                    if seat in self._seat_card_confirmed:
                        continue
                    if obstruction_protected:
                        logger.debug(
                            "in_current_hand=False suppressed for seat %d: "
                            "visual obstruction protected",
                            seat,
                        )
                        continue
                    if seat in players_in_hand_seats:
                        logger.debug(
                            "in_current_hand=False suppressed for seat %d: "
                            "players_in_hand=True",
                            seat,
                        )
                        continue
                    if (
                        seat_key in hm_participant_observed
                        or seat_key in hm_participated
                    ):
                        logger.debug(
                            "in_current_hand=False suppressed for seat %d: "
                            "participant observed",
                            seat,
                        )
                        continue
                    if showdown_guard:
                        logger.info(
                            "Seat card NO_CARD ignored during showdown guard: "
                            "seat=%d phase=%s board_count=%d",
                            seat,
                            self._hand_manager.phase,
                            len(game_state.board or []),
                        )
                        continue
                    player.in_current_hand = False
                    logger.info(
                        "Forced in_current_hand=False for seat %d: "
                        "cards_visible=False, not folded, not confirmed",
                        seat,
                    )

        self._apply_name_obstruction_guard(game_state)

    def _update_visual_obstruction(self, seat_card_results: dict[int, bool]) -> None:
        """Detect simultaneous seat-card changes that indicate visual obstruction."""
        changed_seats = []
        for seat, has_card in seat_card_results.items():
            prev = self._last_seat_card_states.get(seat)
            if prev is not None and prev != has_card:
                changed_seats.append(seat)

        self._last_seat_card_states = dict(seat_card_results)
        if len(changed_seats) >= 3:
            self._visual_obstruction_active = True
            self._visual_obstruction_until = (
                time.monotonic() + self._visual_obstruction_hold_sec
            )
            self._visual_obstruction_recovery_until = (
                time.monotonic()
                + self._visual_obstruction_hold_sec
                + self._visual_obstruction_recovery_sec
            )
            logger.info(
                "Visual obstruction detected: simultaneous seat card changes=%s "
                "hold=%.1fs recovery=%.1fs",
                changed_seats,
                self._visual_obstruction_hold_sec,
                self._visual_obstruction_recovery_sec,
            )

    def _is_visual_obstruction_active(self) -> bool:
        """Return whether temporary visual obstruction protection is active."""
        if not self._visual_obstruction_active:
            return False
        if time.monotonic() <= self._visual_obstruction_until:
            return True
        self._visual_obstruction_active = False
        return False

    def _is_visual_obstruction_protected(self) -> bool:
        """Return whether visual obstruction protection or recovery window is active.

        During the recovery window after obstruction ends, NO_CARD readings
        are still likely as the display stabilizes.
        """
        if self._is_visual_obstruction_active():
            return True
        return time.monotonic() < self._visual_obstruction_recovery_until

    def _is_showdown_or_payout_guard_active(self, game_state: GameState) -> bool:
        """Return whether showdown/payout guard should protect active players.

        During river with 5 board cards and multiple active players, visual
        noise from showdown animations can cause false fold-badge detection
        and transient seat-card failures. This guard prevents premature
        removal of players who still have a claim to the pot.
        """
        phase = self._hand_manager.phase
        if phase != "river":
            return False
        if len(game_state.board or []) < 5:
            return False
        players_in_hand = self._hand_manager.get_players_in_hand()
        return len(players_in_hand) >= 2

    def _can_start_new_hand_from_waiting(
        self,
        game_state: GameState,
        hero_cards: list[str],
    ) -> bool:
        """Return whether a new hand can start from the waiting phase.

        Prevents false hand starts caused by stale hero cards, residual board
        cards, or inflated pot displays lingering from the previous hand's
        showdown/payout animations.
        """
        if (
            game_state.board_card_count >= 5
            and not game_state.suppress_phase_fast_forward
        ):
            logger.info(
                "New hand start suppressed: board still visible in waiting "
                "(board_count=%d, hero_cards=%s, pot=%d)",
                game_state.board_card_count,
                hero_cards,
                game_state.pot,
            )
            return False

        if (
            self._last_ended_hero_cards
            and hero_cards == self._last_ended_hero_cards
            and not self._hero_cards_missing_since_hand_end
            and not self._stale_suppression_bypassed
        ):
            logger.info(
                "New hand start suppressed: same as last ended hero cards "
                "(hero_cards=%s)",
                hero_cards,
            )
            return False

        bb = int(self._config.get("game", {}).get("blind_bb", 100))
        max_start_pot = bb * 10
        if game_state.pot > max_start_pot:
            logger.info(
                "New hand start suppressed: pot too large for waiting start "
                "(pot=%d, max_start_pot=%d, hero_cards=%s)",
                game_state.pot,
                max_start_pot,
                hero_cards,
            )
            return False

        return True

    def _previous_player_cards_visible(
        self,
        game_state: GameState,
        seat_key: str,
    ) -> bool:
        """Return the last known cards_visible value for a seat."""
        if self._prev_state is not None:
            previous_player = self._prev_state.players.get(seat_key)
            if previous_player is not None:
                return previous_player.cards_visible
        current_player = game_state.players.get(seat_key)
        return bool(current_player and current_player.cards_visible)

    def _apply_name_obstruction_guard(self, game_state: GameState) -> None:
        """Keep existing names when visual obstruction causes blank OCR."""
        if not self._is_visual_obstruction_active():
            return

        for seat_key, player in game_state.players.items():
            if player.name not in {None, "", "-"}:
                continue
            previous_name = self._previous_player_name(seat_key)
            if previous_name in {None, "", "-"}:
                continue
            player.name = previous_name
            self._cached_player_names[seat_key] = previous_name

    def _previous_player_name(self, seat_key: str) -> str | None:
        """Return cached or previous-frame name for a seat."""
        cached_name = self._cached_player_names.get(seat_key)
        if cached_name not in {None, "", "-"}:
            return cached_name
        if self._prev_state is None:
            return None
        previous_player = self._prev_state.players.get(seat_key)
        if previous_player is None:
            return None
        return previous_player.name

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
            game_state.current_street_actions = []
            game_state.preflop_actions = []
            return

        current_street = self._hand_manager.get_current_street_actions()
        if current_street is not None:
            game_state.current_street_actions = list(current_street.actions)
        else:
            game_state.current_street_actions = []
        if game_state.phase in {"waiting", "hand_end"}:
            game_state.preflop_actions = []
        else:
            game_state.preflop_actions = list(self._hand_manager.get_preflop_actions())
        logger.debug(
            "Synced street action histories: phase=%s current_count=%d "
            "preflop_count=%d actions=%s",
            game_state.phase,
            len(game_state.current_street_actions),
            len(game_state.preflop_actions),
            [
                {
                    "seat": action.seat,
                    "action": action.action,
                    "amount": action.amount,
                    "confidence": action.confidence,
                }
                for action in game_state.current_street_actions
            ],
        )

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
                self._mark_phase_fast_forward_suppression_if_recent_hand_end(
                    game_state
                )
                if self._waiting_for_card_clear:
                    if self._hero_cards_missing(hero_cards):
                        logger.debug("Waiting for card clear: cards cleared")
                        self._waiting_for_card_clear = False
                        self._hero_cards_missing_since_hand_end = True
                    elif self._hero_cards_missing(self._last_ended_hero_cards):
                        self._log_stale_waiting_cards(hero_cards)
                        game_state.hero.cards = None
                        game_state.hero.cards_visible = False
                    elif self._hero_cards_match_last_ended(game_state.hero.cards):
                        self._log_stale_waiting_cards(hero_cards)
                        game_state.hero.cards = None
                        game_state.hero.cards_visible = False
                    else:
                        logger.info(
                            "Stale hero card suppression cleared: new hero "
                            "cards differ from last ended hand current=%s "
                            "last=%s",
                            game_state.hero.cards,
                            self._last_ended_hero_cards,
                        )
                        self._waiting_for_card_clear = False
                        self._hero_cards_missing_since_hand_end = True
                        self._stale_suppression_start_time = None
                        self._stale_suppression_bypassed = False
                        logger.info(
                            "Waiting: hero cards recognized - %s, starting hand",
                            hero_cards,
                        )
                        self._last_waiting_log = None
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

        if (
            game_state.phase == "waiting"
            and not self._hero_cards_missing(game_state.hero.cards)
            and not self._can_start_new_hand_from_waiting(
                game_state,
                game_state.hero.cards or [],
            )
        ):
            logger.info(
                "Waiting hero cards suppressed by new-hand guard: "
                "hero_cards=%s, board_count=%d, pot=%d",
                game_state.hero.cards,
                game_state.board_card_count,
                game_state.pot,
            )
            game_state.hero.cards = None
            game_state.hero.cards_visible = False

        game_state.hero.seat = 1
        return game_state

    def _mark_phase_fast_forward_suppression_if_recent_hand_end(
        self,
        game_state: GameState,
    ) -> None:
        """Mark hand-start phase fast-forward unsafe after a recent hand end."""
        if self._last_ended_hero_cards is None:
            return
        if self._hero_cards_missing(game_state.hero.cards):
            return
        game_state.suppress_phase_fast_forward = True

    def _update_card_clear_wait_state(self, current_phase: str) -> None:
        if (
            current_phase == "waiting"
            and self._last_hand_manager_phase not in {None, "waiting"}
        ):
            self._last_ended_hero_cards = self._current_visible_hero_cards()
            self._hero_cards_missing_since_hand_end = False
            self._waiting_for_card_clear = True
            self._stale_suppression_start_time = time.monotonic()
            self._stale_suppression_bypassed = False
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
        if not self._hero_cards_match_last_ended(hero_cards):
            return False
        if self._hero_cards_missing_since_hand_end:
            return False
        if (
            self._stale_suppression_start_time is not None
            and time.monotonic() - self._stale_suppression_start_time > 10.0
        ):
            logger.info(
                "Stale card suppression timed out (>10s), "
                "accepting cards as new hand: %s",
                hero_cards,
            )
            self._stale_suppression_start_time = None
            self._stale_suppression_bypassed = True
            return False
        return True

    def _hero_cards_match_last_ended(
        self,
        current_cards: list[str | None] | None,
    ) -> bool:
        """Return True when current hero cards match the last ended hand cards."""
        if self._hero_cards_missing(current_cards):
            return False
        if self._hero_cards_missing(self._last_ended_hero_cards):
            return False
        return list(current_cards or []) == list(self._last_ended_hero_cards or [])

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
            name = player_names.get(seat_key) if is_seated else None
            if not is_seated and self._cached_player_names.get(seat_key) is not None:
                self._cached_player_names[seat_key] = None
            game_state.players[seat_key] = PlayerState(
                name=name,
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
            if not self._table_visible:
                self._abandon_active_hand_for_invisible_table()

    def _abandon_active_hand_for_invisible_table(self) -> None:
        """Abandon active hand after table invisibility has been confirmed."""
        if self._hand_manager is None:
            return
        phase = self._hand_manager.phase
        if phase not in {"preflop", "flop", "turn", "river"}:
            return
        if self._is_visual_obstruction_protected():
            return

        hand_id = self._hand_manager.hand_id
        logger.info(
            "Active hand abandoned because table became invisible: hand_id=%s "
            "phase=%s inactive_streak=%d",
            hand_id,
            phase,
            self._table_inactive_streak,
        )
        self._abandon_active_hand("table_invisible")

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
