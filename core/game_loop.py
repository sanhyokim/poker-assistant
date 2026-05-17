"""Main polling loop that builds GameState objects from captured frames."""

import copy
import inspect
import json
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
    snapshot: dict[str, object] | None = None
    exact_key: str | None = None
    coarse_key: str | None = None


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
        self._hand_manager.set_hand_end_guard(
            self._should_suppress_pot_decrease_hand_end
        )
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
        self._hand_position_hand_id: int | None = None
        self._last_position_lock_log_key: tuple[
            int | None,
            str,
            str | None,
            int | None,
            tuple[int, ...],
            str,
        ] | None = None
        self._last_position_apply_log_key: tuple[
            int | None,
            str | None,
            int | None,
            str | None,
            tuple[tuple[int, str], ...],
        ] | None = None
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
        self._suspicious_amount_guard_until: float = 0.0
        self._new_hand_suppressed_hand_end_guard_sec: float = float(
            recognition_config.get("new_hand_suppressed_hand_end_guard_sec", 2.5)
        )
        self._new_hand_suppressed_at_monotonic: float | None = None
        self._new_hand_suppressed_reason: str | None = None
        self._new_hand_suppressed_hand_id: int | None = None
        self._new_hand_suppressed_pot_from: int | None = None
        self._new_hand_suppressed_pot_to: int | None = None
        self._hand_end_suppressed_this_frame: bool = False
        self._pending_amount_rechecks: dict[int, dict[str, object]] = {}
        self._amount_recheck_max_frames: int = int(
            recognition_config.get("amount_recheck_max_frames", 2)
        )
        self._amount_recheck_failed_seats_this_frame: set[int] = set()
        self._amount_recheck_accepted_seats_this_frame: set[int] = set()
        self._pre_hand_enabled: bool = bool(
            recognition_config.get("pre_hand_enabled", True)
        )
        self._pre_hand_min_carded_seats: int = int(
            recognition_config.get("pre_hand_min_carded_seats", 2)
        )
        self._pre_hand_timeout_sec: float = float(
            recognition_config.get("pre_hand_timeout_sec", 5.0)
        )
        self._pre_hand_hard_timeout_sec: float = float(
            recognition_config.get("pre_hand_hard_timeout_sec", 9.0)
        )
        self._pre_hand_candidate_timeout_sec: float = float(
            recognition_config.get("pre_hand_candidate_timeout_sec", 5.0)
        )
        self._pre_hand_candidate_hard_timeout_sec: float = float(
            recognition_config.get("pre_hand_candidate_hard_timeout_sec", 9.0)
        )
        self._pre_hand_active: bool = False
        self._pre_hand_started_at: float | None = None
        self._pre_hand_started_frame: int | None = None
        self._pre_hand_dealer_seat: int | None = None
        self._pre_hand_cards_visible_seats: set[int] = set()
        self._waiting_preflop_action_buffer: list[ActionRecord] = []
        self._waiting_preflop_action_keys: set[tuple[int, str, int]] = set()
        self._pre_hand_candidate_active: bool = False
        self._pre_hand_candidate_started_at: float | None = None
        self._pre_hand_candidate_started_frame: int | None = None
        self._pre_hand_candidate_dealer_seat: int | None = None
        self._pre_hand_candidate_cards_visible_seats: set[int] = set()
        self._pre_hand_candidate_action_buffer: list[ActionRecord] = []
        self._pre_hand_candidate_action_keys: set[tuple[int, str, int]] = set()
        self._pre_hand_candidate_no_card_frames: int = 0
        self._pre_hand_timeout_held_logged: bool = False
        self._pre_hand_candidate_timeout_held_logged: bool = False
        self._last_hero_non_fold_action_time: float | None = None
        self._last_hero_non_fold_action_name: str | None = None
        self._hero_fold_badge_ignored_for_hand: bool = False
        self._hero_fold_badge_ignored_reason: str | None = None
        self._pending_hero_fold_badge_recovery: bool = False
        self._pending_hero_fold_badge_recovery_since: float | None = None
        self._hero_card_candidate: list[str] | None = None
        self._hero_card_candidate_streak: int = 0
        self._hero_card_confirm_frames: int = int(
            recognition_config.get("hero_card_confirm_frames", 2)
        )
        self._hero_card_active_mismatch_streak: int = 0
        self._hero_card_mismatch_confirm_frames: int = int(
            recognition_config.get("hero_card_mismatch_confirm_frames", 2)
        )
        self._hero_cards_invalid_for_hand: bool = False
        self._hero_cards_invalid_reason: str | None = None
        self._hero_cards_recommendation_started_for_hand: bool = False

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
        self._pending_recommendation_exact_key: str | None = None
        self._pending_recommendation_coarse_key: str | None = None
        self._solver_timeout_contexts: dict[str, float] = {}
        self._solver_suppressed_contexts: dict[str, dict[str, object]] = {}
        self._solver_context_suppression_ttl_sec: float = float(
            recognition_config.get("solver_context_suppression_ttl_sec", 12.0)
        )

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
                    self.process_game_state_after_frame(game_state)
                    if self._on_game_state is not None:
                        self._on_game_state(game_state)
            except Exception:
                logger.exception("Error in game loop")

            elapsed = time.perf_counter() - loop_start
            sleep_time = max(0.0, self._polling_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def process_game_state_after_frame(self, game_state: GameState) -> None:
        """Run canonical post-frame processing for CLI and GUI loops.

        Args:
            game_state: Frame recognition result returned by process_one_frame().
        """
        self._hand_end_suppressed_this_frame = False
        if game_state.game_event == "NEW_HAND":
            self._pending_amount_rechecks.clear()
            self._clear_solver_timeout_contexts("new_hand")
            self._clear_new_hand_suppressed_guard()
        if game_state.game_event == "NEW_STREET":
            self._clear_solver_timeout_contexts("new_street")
        game_state.actions_since_last_frame = self._filter_invalid_actions(
            game_state.actions_since_last_frame,
        )
        self._amount_recheck_failed_seats_this_frame = set()
        self._amount_recheck_accepted_seats_this_frame = set()
        accepted_recheck_actions = self._evaluate_pending_amount_rechecks(game_state)
        if accepted_recheck_actions:
            game_state.actions_since_last_frame = (
                accepted_recheck_actions + game_state.actions_since_last_frame
            )
        game_state.actions_since_last_frame = (
            self._filter_suspicious_amount_actions(
                game_state,
                game_state.actions_since_last_frame,
            )
        )
        self._hand_manager.process_frame(game_state)
        if self._hand_manager.phase in {"waiting", "hand_end"}:
            self._clear_new_hand_suppressed_guard()
        self._commit_pre_hand_buffer_if_started(game_state)
        self._recover_pending_hero_fold_badge(game_state)
        self._sync_game_state_with_hand_manager(game_state)
        self._update_hand_position_lock(game_state)
        self._handle_strategy(game_state)
        self._notify_hud_after_hand_end_suppression(game_state)

    @staticmethod
    def _filter_invalid_actions(actions: list[ActionRecord]) -> list[ActionRecord]:
        """Drop actions for non-table seats before hand processing."""
        filtered: list[ActionRecord] = []
        for action in actions:
            if action.seat < 1 or action.seat > 6:
                logger.info(
                    "Ignored invalid action before hand manager: seat=%s "
                    "action=%s amount=%s confidence=%s",
                    action.seat,
                    action.action,
                    action.amount,
                    action.confidence,
                )
                continue
            filtered.append(action)
        return filtered

    def _update_pre_hand_state(self, game_state: GameState) -> None:
        """Start, maintain, or discard PRE-HAND buffering state."""
        if not self._pre_hand_enabled:
            return

        if self._hand_manager.phase != "waiting":
            if self._pre_hand_active:
                self._discard_pre_hand("phase_changed", game_state)
            return

        discard_reason = self._pre_hand_discard_reason(game_state)
        if discard_reason is not None:
            self._discard_pre_hand(discard_reason, game_state)
            return

        if not self._pre_hand_active and self._can_start_pre_hand(game_state):
            carded_seats = self._pre_hand_carded_seats(game_state)
            self._pre_hand_active = True
            self._pre_hand_started_at = time.monotonic()
            self._pre_hand_started_frame = game_state.frame_number
            self._pre_hand_dealer_seat = game_state.dealer_seat
            self._pre_hand_cards_visible_seats = set(carded_seats)
            self._pre_hand_timeout_held_logged = False
            logger.info(
                "PRE-HAND started: frame=%s dealer=%s cards_visible_seats=%s "
                "pot=%s board_count=%s",
                game_state.frame_number,
                game_state.dealer_seat,
                carded_seats,
                game_state.pot,
                game_state.board_card_count,
            )
            self._notify_hud_computing("PRE-HAND\nBuffering preflop actions...")

        if self._pre_hand_active:
            game_state.hand_start_status = "PRE-HAND"

    def _update_pre_hand_candidate_state(self, game_state: GameState) -> None:
        """Start, maintain, promote, or discard PRE-HAND candidate buffering."""
        if not self._pre_hand_enabled:
            return

        if self._hand_manager.phase != "waiting":
            if (
                self._pre_hand_candidate_active
                and not getattr(self._hand_manager, "hand_just_started", False)
            ):
                self._discard_pre_hand_candidate("phase_changed", game_state)
            return

        discard_reason = self._pre_hand_candidate_discard_reason(game_state)
        if discard_reason is not None:
            self._discard_pre_hand_candidate(discard_reason, game_state)
            return

        if self._pre_hand_candidate_active and self._can_start_pre_hand(game_state):
            self._promote_pre_hand_candidate(game_state)
            return

        if (
            not self._pre_hand_candidate_active
            and not self._pre_hand_active
            and not self._can_start_pre_hand(game_state)
            and self._can_start_pre_hand_candidate(game_state)
        ):
            carded_seats = self._pre_hand_carded_seats(game_state)
            dealer_seat = self._candidate_dealer_seat(game_state)
            self._pre_hand_candidate_active = True
            self._pre_hand_candidate_started_at = time.monotonic()
            self._pre_hand_candidate_started_frame = game_state.frame_number
            self._pre_hand_candidate_dealer_seat = dealer_seat
            self._pre_hand_candidate_cards_visible_seats = set(carded_seats)
            self._pre_hand_candidate_no_card_frames = 0
            self._pre_hand_candidate_timeout_held_logged = False
            game_state.hand_start_status = "PRE-HAND-CANDIDATE"
            logger.info(
                "PRE_HAND_CANDIDATE_STARTED: frame=%s dealer=%s "
                "cards_visible_seats=%s pot=%s board_count=%s "
                "obstruction_protected=%s",
                game_state.frame_number,
                dealer_seat,
                carded_seats,
                game_state.pot,
                game_state.board_card_count,
                self._is_visual_obstruction_protected(),
            )

        if self._pre_hand_candidate_active:
            game_state.hand_start_status = "PRE-HAND-CANDIDATE"

    def _can_start_pre_hand_candidate(self, game_state: GameState) -> bool:
        """Return whether early PRE-HAND candidate buffering should start."""
        if game_state.board_card_count != 0:
            return False
        if not game_state.table_visible:
            return False
        if self._candidate_dealer_seat(game_state) is None:
            return False
        if game_state.pot > self._pre_hand_max_start_pot():
            return False
        return len(self._pre_hand_carded_seats(game_state)) >= 1

    def _pre_hand_candidate_discard_reason(
        self,
        game_state: GameState,
    ) -> str | None:
        """Return the reason active PRE-HAND candidate state should be discarded."""
        if not self._pre_hand_candidate_active:
            return None
        if game_state.board_card_count > 0:
            return "board_visible"
        if not game_state.table_visible:
            return "table_not_visible"
        if game_state.pot > self._pre_hand_max_start_pot():
            return "pot_too_large"
        if (
            self._pre_hand_candidate_started_at is not None
            and time.monotonic() - self._pre_hand_candidate_started_at
            > self._pre_hand_candidate_hard_timeout_sec
        ):
            return "hard_timeout"
        if (
            self._pre_hand_candidate_started_at is not None
            and time.monotonic() - self._pre_hand_candidate_started_at
            > self._pre_hand_candidate_timeout_sec
        ):
            if self._should_hold_pre_hand_timeout(
                game_state,
                self._pre_hand_candidate_action_buffer,
            ):
                if not self._pre_hand_candidate_timeout_held_logged:
                    logger.info(
                        "PRE_HAND_CANDIDATE_TIMEOUT_HELD: frame=%s "
                        "buffered_actions=%s "
                        "reason=hero_or_cards_still_candidate",
                        game_state.frame_number,
                        self._action_records_for_log(
                            self._pre_hand_candidate_action_buffer
                        ),
                    )
                    self._pre_hand_candidate_timeout_held_logged = True
                return None
            return "timeout"
        if len(self._pre_hand_carded_seats(game_state)) == 0:
            self._pre_hand_candidate_no_card_frames += 1
        else:
            self._pre_hand_candidate_no_card_frames = 0
        if self._pre_hand_candidate_no_card_frames >= 2:
            return "cards_disappeared"
        return None

    def _buffer_pre_hand_candidate_actions(self, game_state: GameState) -> None:
        """Buffer valid early preflop actions before formal PRE-HAND starts."""
        if not self._pre_hand_candidate_active or self._pre_hand_active:
            return
        if self._hand_manager.phase != "waiting":
            return

        game_state.hand_start_status = "PRE-HAND-CANDIDATE"
        for action in game_state.actions_since_last_frame:
            drop_reason = self._pre_hand_buffer_action_drop_reason(action)
            if drop_reason is not None:
                self._log_pre_hand_action_dropped(drop_reason, action)
                continue
            action_name = action.action.upper()
            key = (action.seat, action_name, action.amount)
            if key in self._pre_hand_candidate_action_keys:
                continue
            buffered_action = ActionRecord(
                seat=action.seat,
                action=action_name,
                amount=action.amount,
                confidence=action.confidence,
            )
            self._pre_hand_candidate_action_keys.add(key)
            self._pre_hand_candidate_action_buffer.append(buffered_action)
            logger.info(
                "PRE_HAND_CANDIDATE_ACTION_BUFFERED: frame=%s seat=%s "
                "action=%s amount=%s candidate_buffer_count=%s",
                game_state.frame_number,
                buffered_action.seat,
                buffered_action.action,
                buffered_action.amount,
                len(self._pre_hand_candidate_action_buffer),
            )

    def _promote_pre_hand_candidate(self, game_state: GameState) -> None:
        """Promote candidate actions into the formal PRE-HAND buffer."""
        moved_actions = list(self._pre_hand_candidate_action_buffer)
        for action in moved_actions:
            key = (action.seat, action.action.upper(), action.amount)
            if key in self._waiting_preflop_action_keys:
                continue
            self._waiting_preflop_action_keys.add(key)
            self._waiting_preflop_action_buffer.append(action)

        carded_seats = self._pre_hand_carded_seats(game_state)
        self._pre_hand_active = True
        self._pre_hand_started_at = time.monotonic()
        self._pre_hand_started_frame = game_state.frame_number
        self._pre_hand_dealer_seat = game_state.dealer_seat
        self._pre_hand_cards_visible_seats = set(carded_seats)
        self._pre_hand_timeout_held_logged = False
        game_state.hand_start_status = "PRE-HAND"
        logger.info(
            "PRE_HAND_CANDIDATE_PROMOTED: frame=%s moved_actions=%s "
            "dealer=%s pot=%s cards_visible_seats=%s",
            game_state.frame_number,
            self._action_records_for_log(moved_actions),
            game_state.dealer_seat,
            game_state.pot,
            carded_seats,
        )
        self._clear_pre_hand_candidate_state()
        self._notify_hud_computing("PRE-HAND\nBuffering preflop actions...")

    def _can_start_pre_hand(self, game_state: GameState) -> bool:
        """Return whether waiting-state PRE-HAND buffering should start."""
        if game_state.board_card_count != 0:
            return False
        if game_state.dealer_seat is None:
            return False
        if not game_state.table_visible:
            return False
        if self._is_visual_obstruction_protected():
            return False
        if game_state.pot > self._pre_hand_max_start_pot():
            return False
        return (
            len(self._pre_hand_carded_seats(game_state))
            >= self._pre_hand_min_carded_seats
        )

    def _pre_hand_discard_reason(self, game_state: GameState) -> str | None:
        """Return the reason active PRE-HAND state should be discarded."""
        if not self._pre_hand_active:
            return None
        if game_state.board_card_count > 0:
            return "board_visible"
        if not game_state.table_visible:
            return "table_not_visible"
        if self._is_visual_obstruction_protected():
            return "visual_obstruction"
        if (
            self._pre_hand_dealer_seat is not None
            and game_state.dealer_seat is not None
            and game_state.dealer_seat != self._pre_hand_dealer_seat
        ):
            return "dealer_changed"
        if game_state.pot > self._pre_hand_max_start_pot():
            return "pot_too_large"
        if (
            self._pre_hand_started_at is not None
            and time.monotonic() - self._pre_hand_started_at
            > self._pre_hand_hard_timeout_sec
        ):
            return "hard_timeout"
        if (
            self._pre_hand_started_at is not None
            and time.monotonic() - self._pre_hand_started_at
            > self._pre_hand_timeout_sec
        ):
            if self._should_hold_pre_hand_timeout(
                game_state,
                self._waiting_preflop_action_buffer,
            ):
                if not self._pre_hand_timeout_held_logged:
                    logger.info(
                        "PRE_HAND_TIMEOUT_HELD: frame=%s buffered_actions=%s "
                        "reason=hero_or_cards_still_candidate",
                        game_state.frame_number,
                        self._action_records_for_log(
                            self._waiting_preflop_action_buffer
                        ),
                    )
                    self._pre_hand_timeout_held_logged = True
                return None
            return "timeout"
        if len(self._pre_hand_carded_seats(game_state)) == 0:
            return "cards_disappeared"
        return None

    def _buffer_pre_hand_actions(self, game_state: GameState) -> None:
        """Buffer valid preflop actions detected before formal hand start."""
        if not self._pre_hand_active:
            return
        if self._hand_manager.phase != "waiting":
            return

        game_state.hand_start_status = "PRE-HAND"
        for action in game_state.actions_since_last_frame:
            drop_reason = self._pre_hand_buffer_action_drop_reason(action)
            if drop_reason is not None:
                self._log_pre_hand_action_dropped(drop_reason, action)
                continue
            action_name = action.action.upper()
            key = (action.seat, action_name, action.amount)
            if key in self._waiting_preflop_action_keys:
                continue
            buffered_action = ActionRecord(
                seat=action.seat,
                action=action_name,
                amount=action.amount,
                confidence=action.confidence,
            )
            self._waiting_preflop_action_keys.add(key)
            self._waiting_preflop_action_buffer.append(buffered_action)
            logger.info(
                "PRE-HAND action buffered: frame=%s seat=%s action=%s "
                "amount=%s buffer_count=%s",
                game_state.frame_number,
                buffered_action.seat,
                buffered_action.action,
                buffered_action.amount,
                len(self._waiting_preflop_action_buffer),
            )

    def _commit_pre_hand_buffer_if_started(self, game_state: GameState) -> None:
        """Commit buffered PRE-HAND actions after HandManager starts a hand."""
        if not getattr(self._hand_manager, "hand_just_started", False):
            return
        buffered_actions = list(self._waiting_preflop_action_buffer)
        direct_candidate_actions = list(self._pre_hand_candidate_action_buffer)
        existing_keys = {
            (action.seat, action.action.upper(), action.amount)
            for action in buffered_actions
        }
        for action in direct_candidate_actions:
            key = (action.seat, action.action.upper(), action.amount)
            if key not in existing_keys:
                existing_keys.add(key)
                buffered_actions.append(action)

        if buffered_actions:
            self._hand_manager.add_preflop_buffered_actions(buffered_actions)
            logger.info(
                "PRE-HAND committed: hand_id=%s buffered_actions=%s",
                self._hand_manager.hand_id,
                self._action_records_for_log(buffered_actions),
            )
        if direct_candidate_actions:
            logger.info(
                "PRE_HAND_CANDIDATE_COMMITTED_DIRECTLY: hand_id=%s "
                "buffered_actions=%s",
                self._hand_manager.hand_id,
                self._action_records_for_log(direct_candidate_actions),
            )
        game_state.hand_start_status = None
        self._clear_pre_hand_state()
        self._clear_pre_hand_candidate_state()

    def _discard_pre_hand(self, reason: str, game_state: GameState) -> None:
        """Discard PRE-HAND state and buffered actions."""
        logger.info(
            "PRE-HAND discarded: reason=%s frame=%s buffered_actions=%s",
            reason,
            game_state.frame_number,
            self._action_records_for_log(self._waiting_preflop_action_buffer),
        )
        self._clear_pre_hand_state()

    def _discard_pre_hand_candidate(self, reason: str, game_state: GameState) -> None:
        """Discard PRE-HAND candidate state and buffered actions."""
        logger.info(
            "PRE_HAND_CANDIDATE_DISCARDED: reason=%s frame=%s "
            "buffered_actions=%s",
            reason,
            game_state.frame_number,
            self._action_records_for_log(self._pre_hand_candidate_action_buffer),
        )
        self._clear_pre_hand_candidate_state()

    def _clear_pre_hand_state(self) -> None:
        """Clear PRE-HAND state and action buffers."""
        self._pre_hand_active = False
        self._pre_hand_started_at = None
        self._pre_hand_started_frame = None
        self._pre_hand_dealer_seat = None
        self._pre_hand_cards_visible_seats.clear()
        self._waiting_preflop_action_buffer.clear()
        self._waiting_preflop_action_keys.clear()
        self._pre_hand_timeout_held_logged = False

    def _clear_pre_hand_candidate_state(self) -> None:
        """Clear PRE-HAND candidate state and action buffers."""
        self._pre_hand_candidate_active = False
        self._pre_hand_candidate_started_at = None
        self._pre_hand_candidate_started_frame = None
        self._pre_hand_candidate_dealer_seat = None
        self._pre_hand_candidate_cards_visible_seats.clear()
        self._pre_hand_candidate_action_buffer.clear()
        self._pre_hand_candidate_action_keys.clear()
        self._pre_hand_candidate_no_card_frames = 0
        self._pre_hand_candidate_timeout_held_logged = False

    def _candidate_dealer_seat(self, game_state: GameState) -> int | None:
        """Return dealer seat usable for early PRE-HAND candidate detection."""
        if game_state.dealer_seat is not None:
            return game_state.dealer_seat
        return self._cached_dealer_seat

    def _pre_hand_carded_seats(self, game_state: GameState) -> list[int]:
        """Return sorted opponent seats with visible cards during waiting."""
        return sorted(
            seat
            for seat in range(2, 7)
            if (
                (player := game_state.players.get(str(seat))) is not None
                and player.cards_visible
            )
        )

    def _pre_hand_max_start_pot(self) -> int:
        """Return the maximum pot that can start PRE-HAND buffering."""
        return self._blind_bb() * 10

    def _should_hold_pre_hand_timeout(
        self,
        game_state: GameState,
        actions: list[ActionRecord],
    ) -> bool:
        """Return whether a soft timeout should preserve buffered actions."""
        if not actions or not game_state.table_visible or game_state.board_card_count != 0:
            return False
        hero_cards_visible = not self._hero_cards_missing(game_state.hero.cards)
        if hero_cards_visible or self._hero_card_candidate is not None:
            return True
        return len(self._pre_hand_carded_seats(game_state)) >= self._pre_hand_min_carded_seats

    @staticmethod
    def _pre_hand_buffer_action_drop_reason(action: ActionRecord) -> str | None:
        """Return why an action cannot be buffered before a formal hand."""
        action_name = action.action.upper()
        if action.seat < 1 or action.seat > 6:
            return "invalid_seat"
        if action.seat == 1:
            return "hero_seat_waiting"
        if action.confidence != "high":
            return "low_confidence"
        if action_name in {"CHECK", "FOLD"}:
            return "unsupported_action"
        if action.amount < 0:
            return "negative_amount"
        if action_name in {"BET", "RAISE", "ALL_IN", "CALL"} and action.amount <= 0:
            return "non_positive_amount"
        if action_name not in {
            "CALL",
            "BET",
            "RAISE",
            "ALL_IN",
            "BLIND_SB",
            "BLIND_BB",
        }:
            return "unsupported_action"
        return None

    @staticmethod
    def _log_pre_hand_action_dropped(reason: str, action: ActionRecord) -> None:
        """Log dropped PRE-HAND buffer actions."""
        if reason not in {"hero_seat_waiting", "low_confidence"}:
            return
        logger.info(
            "PRE_HAND_ACTION_DROPPED: reason=%s seat=%s action=%s amount=%s",
            reason,
            action.seat,
            action.action,
            action.amount,
        )

    @staticmethod
    def _action_records_for_log(actions: list[ActionRecord]) -> list[dict[str, object]]:
        """Return compact action dictionaries for structured logs."""
        return [
            {
                "seat": action.seat,
                "action": action.action,
                "amount": action.amount,
                "confidence": action.confidence,
            }
            for action in actions
        ]

    def _filter_suspicious_amount_actions(
        self,
        game_state: GameState,
        actions: list[ActionRecord],
    ) -> list[ActionRecord]:
        """Drop implausible amount actions before hand processing."""
        phase = game_state.phase
        active_phases = {"preflop", "flop", "turn", "river"}
        if phase not in active_phases and self._hand_manager is not None:
            phase = self._hand_manager.phase
        if phase not in active_phases:
            return actions

        blind_bb = self._blind_bb()
        preflop_spike_threshold = self._preflop_spike_action_threshold(blind_bb)
        postflop_spike_threshold = self._postflop_spike_action_threshold(game_state.pot)
        absolute_threshold = self._max_reasonable_preflop_action_amount(blind_bb)
        known_max_stack = self._known_max_stack(game_state)
        spike_context = game_state.strategy_defer_reason in {
            "pot_spike_hold",
            "suspicious_pot_spike",
        }
        recovery_context = self._is_suspicious_amount_guard_active()
        filtered: list[ActionRecord] = []

        for action in actions:
            if action.seat in self._amount_recheck_failed_seats_this_frame:
                continue
            action_name = action.action.upper()
            if action.seat in self._amount_recheck_accepted_seats_this_frame:
                filtered.append(action)
                continue
            if action_name not in {"BET", "RAISE", "ALL_IN", "CALL"}:
                filtered.append(action)
                continue

            if (
                phase == "preflop"
                and action_name in {"BET", "RAISE", "ALL_IN"}
                and action.amount <= 0
            ):
                logger.warning(
                    "Ignored invalid preflop action amount: hand_id=%s phase=%s "
                    "seat=%s action=%s amount=%s pot=%s blind_bb=%s reason=%s",
                    game_state.hand_id,
                    phase,
                    action.seat,
                    action.action,
                    action.amount,
                    game_state.pot,
                    blind_bb,
                    "non_positive_amount",
                )
                continue

            if (
                phase == "preflop"
                and spike_context
                and action.amount >= preflop_spike_threshold
            ):
                if self._request_amount_recheck(
                    game_state,
                    action,
                    phase,
                    "preflop_pot_spike_hold",
                ):
                    continue
                filtered.append(action)
                continue

            if phase in {"flop", "turn", "river"}:
                if spike_context and action.amount >= postflop_spike_threshold:
                    if self._request_amount_recheck(
                        game_state,
                        action,
                        phase,
                        "postflop_pot_spike_hold",
                    ):
                        continue
                    filtered.append(action)
                    continue
                if recovery_context and action.amount >= blind_bb * 50:
                    if self._request_amount_recheck(
                        game_state,
                        action,
                        phase,
                        "postflop_pot_spike_recovery",
                    ):
                        continue
                    filtered.append(action)
                    continue

            if phase == "preflop" and action.amount >= absolute_threshold:
                if self._request_amount_recheck(
                    game_state,
                    action,
                    phase,
                    "preflop_absolute_threshold",
                ):
                    continue
                filtered.append(action)
                continue

            if (
                phase == "preflop"
                and known_max_stack is not None
                and action.amount > known_max_stack * 1.2
            ):
                if self._request_amount_recheck(
                    game_state,
                    action,
                    phase,
                    "preflop_stack_threshold",
                ):
                    continue
                filtered.append(action)
                continue

            filtered.append(action)

        return filtered

    def _request_amount_recheck(
        self,
        game_state: GameState,
        action: ActionRecord,
        phase: str,
        reason: str,
    ) -> bool:
        """Hold a suspicious amount action for a seat-specific reread."""
        if not self._amount_recheck_enabled():
            return False

        if action.seat in self._pending_amount_rechecks:
            self._mark_amount_recheck_deferred(
                game_state,
                "existing_pending_amount_recheck",
                pending=True,
            )
            return True

        previous_state = self._prev_state
        previous_stack = self._seat_stack(previous_state, action.seat)
        candidate_stack = self._seat_stack(game_state, action.seat)
        candidate_bet = self._seat_bet(game_state, action.seat)
        confirmed_pot = previous_state.pot if previous_state is not None else game_state.pot
        pending = {
            "seat": action.seat,
            "action": action.action,
            "amount": action.amount,
            "confidence": action.confidence,
            "first_seen_frame": game_state.frame_number,
            "phase": phase,
            "board_count": game_state.board_card_count,
            "candidate_pot": game_state.pot,
            "confirmed_pot": confirmed_pot,
            "candidate_bet": candidate_bet,
            "candidate_stack": candidate_stack,
            "previous_stack": previous_stack,
        }
        self._pending_amount_rechecks[action.seat] = pending
        self._mark_amount_recheck_deferred(game_state, reason, pending=True)
        logger.warning(
            "Amount recheck requested: hand_id=%s phase=%s seat=%s action=%s "
            "amount=%s old_pot=%s new_pot=%s board_count=%s reason=%s",
            game_state.hand_id,
            phase,
            action.seat,
            action.action,
            action.amount,
            confirmed_pot,
            game_state.pot,
            game_state.board_card_count,
            reason,
        )
        return True

    def _evaluate_pending_amount_rechecks(
        self,
        game_state: GameState,
    ) -> list[ActionRecord]:
        """Evaluate pending amount rereads and return accepted actions."""
        accepted_actions: list[ActionRecord] = []
        if not self._pending_amount_rechecks:
            return accepted_actions

        for seat, pending in list(self._pending_amount_rechecks.items()):
            phase = str(pending["phase"])
            amount = int(pending["amount"])
            action_name = str(pending["action"])
            first_seen_frame = int(pending["first_seen_frame"])
            board_count = int(pending["board_count"])
            current_phase = self._active_phase(game_state)
            current_bet = self._seat_bet(game_state, seat)
            current_stack = self._seat_stack(game_state, seat)
            previous_stack = self._optional_int(pending.get("previous_stack"))
            candidate_pot = int(pending["candidate_pot"])
            confirmed_pot = int(pending["confirmed_pot"])
            frames_waited = max(0, game_state.frame_number - first_seen_frame)

            if current_phase != phase or game_state.board_card_count != board_count:
                self._fail_amount_recheck(
                    game_state,
                    pending,
                    "phase_or_board_changed",
                    current_bet,
                    current_stack,
                )
                continue

            matched_by = self._amount_recheck_match_reason(
                game_state,
                pending,
                current_bet,
                current_stack,
                previous_stack,
            )
            if matched_by is not None:
                accepted_action = ActionRecord(
                    seat=seat,
                    action=action_name,
                    amount=amount,
                    confidence=str(pending.get("confidence", "high")),
                )
                accepted_actions.append(accepted_action)
                self._amount_recheck_accepted_seats_this_frame.add(seat)
                del self._pending_amount_rechecks[seat]
                logger.warning(
                    "Amount recheck accepted: hand_id=%s phase=%s seat=%s "
                    "action=%s amount=%s matched_by=%s pot=%s player_bet=%s "
                    "stack_prev=%s stack_curr=%s",
                    game_state.hand_id,
                    phase,
                    seat,
                    action_name,
                    amount,
                    matched_by,
                    game_state.pot,
                    current_bet,
                    previous_stack,
                    current_stack,
                )
                if game_state.pot <= confirmed_pot:
                    self._mark_amount_recheck_deferred(
                        game_state,
                        "accepted_waiting_for_pot",
                        pending=True,
                    )
                continue

            stack_drop = self._stack_drop(previous_stack, current_stack)
            if current_bet <= self._amount_tolerance() and (stack_drop is None or stack_drop <= 0):
                self._fail_amount_recheck(
                    game_state,
                    pending,
                    "bet_disappeared",
                    current_bet,
                    current_stack,
                )
                continue

            if frames_waited >= self._amount_recheck_max_frames:
                self._fail_amount_recheck(
                    game_state,
                    pending,
                    "max_frames_exceeded",
                    current_bet,
                    current_stack,
                )
                continue

            self._mark_amount_recheck_deferred(
                game_state,
                "waiting_for_reread",
                pending=True,
            )
            logger.info(
                "Amount recheck pending: hand_id=%s phase=%s seat=%s action=%s "
                "amount=%s frames_waited=%s pot=%s player_bet=%s stack_prev=%s "
                "stack_curr=%s first_pot=%s",
                game_state.hand_id,
                phase,
                seat,
                action_name,
                amount,
                frames_waited,
                game_state.pot,
                current_bet,
                previous_stack,
                current_stack,
                candidate_pot,
            )

        return accepted_actions

    def _amount_recheck_match_reason(
        self,
        game_state: GameState,
        pending: dict[str, object],
        current_bet: int,
        current_stack: int | None,
        previous_stack: int | None,
    ) -> str | None:
        """Return the amount recheck match reason, or None if not matched."""
        amount = int(pending["amount"])
        action_name = str(pending["action"]).upper()
        confirmed_pot = int(pending["confirmed_pot"])
        bet_matches = self._amount_close(current_bet, amount)
        stack_drop = self._stack_drop(previous_stack, current_stack)
        stack_matches = stack_drop is not None and self._amount_close(stack_drop, amount)
        pot_increased = game_state.pot >= confirmed_pot

        if bet_matches and stack_matches:
            return "bet_stack"
        if action_name == "ALL_IN" and current_stack == 0 and bet_matches:
            return "all_in_stack_zero"
        if bet_matches and pot_increased:
            return "bet_pot"
        return None

    def _fail_amount_recheck(
        self,
        game_state: GameState,
        pending: dict[str, object],
        reason: str,
        current_bet: int,
        current_stack: int | None,
    ) -> None:
        """Fail and clear one pending amount recheck."""
        seat = int(pending["seat"])
        if seat in self._pending_amount_rechecks:
            del self._pending_amount_rechecks[seat]
        self._amount_recheck_failed_seats_this_frame.add(seat)
        self._mark_amount_recheck_deferred(game_state, reason, pending=False)
        logger.warning(
            "Amount recheck failed: hand_id=%s phase=%s seat=%s action=%s "
            "amount=%s first_pot=%s reread_pot=%s first_bet=%s reread_bet=%s "
            "first_stack=%s reread_stack=%s reason=%s",
            game_state.hand_id,
            pending["phase"],
            seat,
            pending["action"],
            pending["amount"],
            pending["candidate_pot"],
            game_state.pot,
            pending["candidate_bet"],
            current_bet,
            pending["candidate_stack"],
            current_stack,
            reason,
        )

    @staticmethod
    def _mark_amount_recheck_deferred(
        game_state: GameState,
        reason: str,
        *,
        pending: bool,
    ) -> None:
        """Mark GameState as deferred due to amount recheck."""
        game_state.strategy_defer_reason = (
            "amount_recheck_pending" if pending else "amount_recheck_failed"
        )
        game_state.amount_recheck_pending = pending
        game_state.amount_recheck_reason = reason

    def _active_phase(self, game_state: GameState) -> str:
        """Return GameState phase, falling back to HandManager active phase."""
        active_phases = {"preflop", "flop", "turn", "river"}
        if game_state.phase in active_phases:
            return game_state.phase
        if self._hand_manager is not None and self._hand_manager.phase in active_phases:
            return self._hand_manager.phase
        return game_state.phase

    @staticmethod
    def _seat_bet(game_state: GameState, seat: int) -> int:
        """Return the current bet for a seat in the frame."""
        if seat == 1:
            return game_state.hero.bet
        player = game_state.players.get(str(seat))
        if player is None:
            return 0
        return player.bet

    @staticmethod
    def _seat_stack(game_state: GameState | None, seat: int) -> int | None:
        """Return the visible stack for a seat in the frame."""
        if game_state is None:
            return None
        if seat == 1:
            return game_state.hero.stack
        player = game_state.players.get(str(seat))
        if player is None:
            return None
        return player.stack

    @staticmethod
    def _stack_drop(
        previous_stack: int | None,
        current_stack: int | None,
    ) -> int | None:
        """Return stack decrease, or None when unavailable."""
        if previous_stack is None or current_stack is None:
            return None
        return previous_stack - current_stack

    @staticmethod
    def _optional_int(value: object) -> int | None:
        """Convert an optional object value to int."""
        if value is None:
            return None
        return int(value)

    def _amount_tolerance(self) -> int:
        """Return amount reread tolerance."""
        return max(2, int(self._blind_bb() * 0.1))

    def _amount_close(self, lhs: int, rhs: int) -> bool:
        """Return whether two chip amounts are close enough for reread."""
        return abs(lhs - rhs) <= self._amount_tolerance()

    def _amount_recheck_enabled(self) -> bool:
        """Return whether amount reread protection is enabled."""
        recognition_config = self._config.get("recognition", {})
        return bool(recognition_config.get("amount_recheck_enabled", True))

    def _blind_bb(self) -> int:
        """Return the configured big blind for amount sanity checks."""
        return int(self._config.get("game", {}).get("blind_bb", 100))

    def _preflop_spike_action_threshold(self, blind_bb: int) -> int:
        """Return the pot-spike-context preflop amount threshold."""
        recognition_config = self._config.get("recognition", {})
        return int(
            recognition_config.get(
                "preflop_pot_spike_action_amount",
                blind_bb * int(recognition_config.get("amount_recheck_min_bb", 50)),
            )
        )

    def _postflop_spike_action_threshold(self, pot: int) -> int:
        """Return the postflop pot-spike-context amount threshold."""
        recognition_config = self._config.get("recognition", {})
        min_bb = int(recognition_config.get("amount_recheck_min_bb", 50))
        pot_ratio = float(recognition_config.get("amount_recheck_pot_ratio", 5.0))
        return max(self._blind_bb() * min_bb, int(pot * pot_ratio))

    def _max_reasonable_preflop_action_amount(self, blind_bb: int) -> int:
        """Return the absolute preflop amount guardrail."""
        recognition_config = self._config.get("recognition", {})
        return int(
            recognition_config.get(
                "max_reasonable_preflop_action_amount",
                blind_bb * 200,
            )
        )

    @staticmethod
    def _known_max_stack(game_state: GameState) -> int | None:
        """Return the largest visible stack for amount sanity checks."""
        stack_values: list[int] = []
        if game_state.hero.stack is not None:
            stack_values.append(game_state.hero.stack)
        for player in game_state.players.values():
            if player.stack is not None:
                stack_values.append(player.stack)
        if not stack_values:
            return None
        return max(stack_values)

    def _is_suspicious_amount_guard_active(self) -> bool:
        """Return whether recent pot-spike frames should still guard actions."""
        return time.monotonic() < self._suspicious_amount_guard_until

    def _extend_suspicious_amount_guard(self) -> None:
        """Keep amount filtering active briefly after a pot-spike hold frame."""
        hold_sec = float(
            self._config.get("recognition", {}).get(
                "suspicious_amount_recovery_sec",
                1.0,
            )
        )
        self._suspicious_amount_guard_until = max(
            self._suspicious_amount_guard_until,
            time.monotonic() + hold_sec,
        )

    def _start_new_hand_suppressed_guard(
        self,
        game_state: GameState,
        reason: str,
        pot_from: int,
        pot_to: int,
    ) -> None:
        """Protect the active hand after a false NEW_HAND candidate."""
        self._new_hand_suppressed_at_monotonic = time.monotonic()
        self._new_hand_suppressed_reason = reason
        self._new_hand_suppressed_hand_id = self._hand_manager.hand_id
        self._new_hand_suppressed_pot_from = pot_from
        self._new_hand_suppressed_pot_to = pot_to
        logger.info(
            "NEW_HAND_SUPPRESSED_GUARD_STARTED: hand_id=%s phase=%s "
            "reason=%s pot_from=%s pot_to=%s",
            self._hand_manager.hand_id,
            self._hand_manager.phase,
            reason,
            pot_from,
            pot_to,
        )

    def _clear_new_hand_suppressed_guard(self) -> None:
        """Clear NEW_HAND suppression guard state."""
        self._new_hand_suppressed_at_monotonic = None
        self._new_hand_suppressed_reason = None
        self._new_hand_suppressed_hand_id = None
        self._new_hand_suppressed_pot_from = None
        self._new_hand_suppressed_pot_to = None

    def _should_suppress_pot_decrease_hand_end(
        self,
        game_state: GameState,
        pot_prev: int,
        pot_curr: int,
    ) -> bool:
        """Return whether a pot decrease should not end the active hand yet."""
        if self._hand_manager.phase not in {"preflop", "flop", "turn", "river"}:
            return False
        if not self._hero_cards_still_visible_or_cached(game_state):
            return False

        now = time.monotonic()
        if (
            self._new_hand_suppressed_at_monotonic is not None
            and self._new_hand_suppressed_hand_id == self._hand_manager.hand_id
            and now - self._new_hand_suppressed_at_monotonic
            <= self._new_hand_suppressed_hand_end_guard_sec
        ):
            self._hand_end_suppressed_this_frame = True
            game_state.strategy_defer_reason = "hand_end_guard"
            logger.info(
                "HAND_END_SUPPRESSED_AFTER_NEW_HAND_SUPPRESS: hand_id=%s "
                "phase=%s pot_prev=%s pot_curr=%s reason=%s",
                self._hand_manager.hand_id,
                self._hand_manager.phase,
                pot_prev,
                pot_curr,
                self._new_hand_suppressed_reason or "hero_cards_still_visible",
            )
            return True

        if self._is_suspicious_amount_guard_active():
            self._hand_end_suppressed_this_frame = True
            game_state.strategy_defer_reason = "hand_end_guard"
            logger.info(
                "HAND_END_SUPPRESSED_AFTER_SUSPICIOUS_POT: hand_id=%s "
                "phase=%s pot_prev=%s pot_curr=%s recent_spike=%s",
                self._hand_manager.hand_id,
                self._hand_manager.phase,
                pot_prev,
                pot_curr,
                True,
            )
            return True

        return False

    def _hero_cards_still_visible_or_cached(self, game_state: GameState) -> bool:
        """Return whether Hero cards still indicate the active hand is visible."""
        if game_state.hero.cards_visible:
            return True
        if len(game_state.hero.cards or []) == 2:
            return True
        return self._cached_hero_cards is not None

    def _notify_hud_after_hand_end_suppression(self, game_state: GameState) -> None:
        """Keep HUD out of waiting state when hand_end was suppressed."""
        if not self._hand_end_suppressed_this_frame:
            return
        if self._hand_manager.phase not in {"preflop", "flop", "turn", "river"}:
            return
        if self._previous_recommendation is not None:
            self._notify_hud(self._previous_recommendation)
            return
        if not game_state.hero.is_my_turn:
            self._notify_hud_computing(
                "HAND STILL ACTIVE\nWaiting for stable state..."
            )

    def stop(self, reason: str = "user_stop") -> None:
        """Request polling loop stop."""
        self._running = False
        self._cached_player_names = {}
        self._cached_hero_cards = None
        self._cached_hand_id = None
        self._previous_recommendation = None
        self._previous_recommendation_context = None
        self._clear_pending_state("user_stop")
        self._pending_amount_rechecks.clear()
        self._clear_pre_hand_state()
        self._clear_pre_hand_candidate_state()
        self._last_recommendation_log = None
        self._last_strategy_is_my_turn = False
        self._hero_fold_badge_ignored_for_hand = False
        self._hero_fold_badge_ignored_reason = None
        self._clear_pending_hero_fold_badge_recovery()
        self._reset_waiting_hero_card_candidate()
        self._reset_active_hero_card_validation()
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
                        self._start_new_hand_suppressed_guard(
                            game_state,
                            "hero_cards_still_visible",
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
            game_state.actions_since_last_frame = (
                self._filter_low_confidence_opponent_folds(
                    game_state,
                    game_state.actions_since_last_frame,
                )
            )
            filtered_pot = estimation.get("filtered_pot")
            if filtered_pot is not None:
                logger.debug(
                    "Applying filtered pot: %d -> %d (spike held)",
                    game_state.pot,
                    filtered_pot,
                )
                game_state.pot = filtered_pot
            if estimation.get("pot_spike_hold"):
                game_state.strategy_defer_reason = "pot_spike_hold"
                self._extend_suspicious_amount_guard()
            if estimation.get("suspicious_pot_spike"):
                game_state.strategy_defer_reason = "suspicious_pot_spike"
                self._extend_suspicious_amount_guard()
        else:
            game_state.game_event = None
            game_state.actions_since_last_frame = []

        self._update_pre_hand_candidate_state(game_state)
        self._buffer_pre_hand_candidate_actions(game_state)
        self._update_pre_hand_state(game_state)
        self._buffer_pre_hand_actions(game_state)

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
            self._clear_pending_hero_fold_badge_recovery()
            self._reset_waiting_hero_card_candidate()
            self._reset_active_hero_card_validation()
            self._waiting_for_card_clear = False
            self._hero_cards_missing_since_hand_end = False
            self._last_ended_hero_cards = None
            self._stale_suppression_start_time = None
            self._stale_suppression_bypassed = False
            self._clear_hand_position_lock("hand start")
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
        self._validate_active_hero_cards(frame, game_state)

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
        self._hand_position_hand_id = None
        self._last_position_lock_log_key = None
        self._last_position_apply_log_key = None
        self._last_waiting_log = None
        self._waiting_for_card_clear = False
        self._hero_cards_missing_since_hand_end = False
        self._last_ended_hero_cards = None
        self._stale_suppression_start_time = None
        self._stale_suppression_bypassed = False
        self._last_hand_manager_phase = None
        self._previous_recommendation = None
        self._previous_recommendation_context = None
        self._clear_pending_state("reset")
        self._last_strategy_phase = None
        self._clear_pre_hand_state()
        self._clear_pre_hand_candidate_state()
        self._last_hero_non_fold_action_time = None
        self._last_hero_non_fold_action_name = None
        self._hero_fold_badge_ignored_for_hand = False
        self._hero_fold_badge_ignored_reason = None
        self._clear_pending_hero_fold_badge_recovery()
        self._reset_waiting_hero_card_candidate()
        self._reset_active_hero_card_validation()
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
        self._clear_pending_state("abandon_active_hand")
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

        hero_cards_invalid = bool(
            getattr(self, "_hero_cards_invalid_for_hand", False)
        )
        if hero_cards_invalid or game_state.hero_cards_unstable_reason:
            reason = (
                getattr(self, "_hero_cards_invalid_reason", None)
                or game_state.hero_cards_unstable_reason
            )
            logger.warning(
                "Strategy skipped: hero cards unstable reason=%s cached=%s "
                "current=%s phase=%s hand_id=%s",
                reason,
                self._cached_hero_cards,
                game_state.hero.cards,
                game_state.phase,
                game_state.hand_id,
            )
            self._last_recommendation_log = None
            self._previous_recommendation = None
            self._previous_recommendation_context = None
            self._clear_pending_state("hero_cards_unstable")
            self._notify_hud_computing("HERO CARDS UNSTABLE")
            self._last_strategy_phase = phase
            self._last_strategy_is_my_turn = game_state.hero.is_my_turn
            return

        # Phase が非アクティブなら何もしない
        if phase in (None, "waiting", "hand_end"):
            self._last_recommendation_log = None
            self._previous_recommendation = None
            self._previous_recommendation_context = None
            self._clear_pending_state(str(phase or "inactive_phase"))
            self._clear_solver_timeout_contexts(str(phase or "inactive_phase"))
            self._last_strategy_phase = phase
            self._last_strategy_is_my_turn = False
            if game_state.hand_start_status == "PRE-HAND":
                self._notify_hud_computing("PRE-HAND\nBuffering preflop actions...")
            else:
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
            self._clear_pending_state("hero_not_in_hand")
            self._last_strategy_is_my_turn = False
            self._notify_hud(None)
            self._save_human_action_to_hand_manager(game_state)
            return

        # NEW_HAND / ストリート変化時にリセット
        if game_state.game_event == "NEW_HAND":
            self._last_recommendation_log = None
            self._previous_recommendation = None
            self._previous_recommendation_context = None
            self._clear_pending_state("new_hand")
            self._clear_solver_timeout_contexts("new_hand")

        if phase == "preflop" and self._last_strategy_phase != "preflop":
            self._previous_recommendation = None
            self._previous_recommendation_context = None
            self._clear_pending_state("preflop_entered")

        if game_state.game_event == "NEW_STREET":
            self._last_recommendation_log = None
            self._clear_pending_state("new_street")
            self._clear_solver_timeout_contexts("new_street")
            if self._hand_manager is not None:
                game_state.phase = self._hand_manager.phase

        # ヒーローのターンでなければ推奨をクリアして終了
        if not game_state.hero.is_my_turn:
            # ターン終了時（True→False）にクリア
            if self._last_strategy_is_my_turn:
                self._last_recommendation_log = None
                self._previous_recommendation = None
                self._previous_recommendation_context = None
                self._clear_pending_state("hero_turn_ended")
                self._notify_hud(None)
            self._save_human_action_to_hand_manager(game_state)
            self._last_strategy_phase = phase
            self._last_strategy_is_my_turn = False
            return

        # === ここから is_my_turn=True のみ ===

        if game_state.strategy_defer_reason:
            logger.info(
                "Strategy deferred: reason=%s phase=%s pot=%d actions=%s",
                game_state.strategy_defer_reason,
                phase,
                game_state.pot,
                [
                    (action.seat, action.action, action.amount)
                    for action in game_state.actions_since_last_frame
                ],
            )
            self._last_recommendation_log = None
            self._previous_recommendation = None
            self._previous_recommendation_context = None
            self._clear_pending_state(game_state.strategy_defer_reason)
            if game_state.strategy_defer_reason == "hand_end_guard":
                self._notify_hud_computing(
                    "HAND STILL ACTIVE\nWaiting for stable state..."
                )
            else:
                self._notify_hud_computing("WAITING FOR STABLE POT...")
            self._save_human_action_to_hand_manager(game_state)
            self._last_strategy_phase = phase
            self._last_strategy_is_my_turn = game_state.hero.is_my_turn
            return

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
                    self._log_preflop_recommendation_context(game_state)
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
                self._log_preflop_recommendation_context(game_state)
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
                            if self._is_solver_retry_suppressed(game_state):
                                recommendation = self._solver_timeout_recommendation()
                                recommendation.reason = (
                                    "Solver skipped: previous solver request became "
                                    "stale/timeout in this context"
                                )
                                self._previous_recommendation = recommendation
                                self._previous_recommendation_context = snapshot
                                self._notify_hud(recommendation)
                            else:
                                self._start_async_postflop_recommendation(
                                    game_state, snapshot,
                                )
                        else:
                            with self._pending_recommendation_lock:
                                request_id = self._pending_recommendation_active_id
                            logger.info(
                                "SOLVER_START_SUPPRESSED: "
                                "reason=worker_already_alive "
                                "active_request_id=%s hand_id=%s phase=%s",
                                request_id,
                                game_state.hand_id,
                                game_state.phase,
                            )
                            self._notify_hud_computing(
                                "SOLVER STILL RUNNING\nWaiting for current solver..."
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
        turn_started_at = (
            self._hand_manager.hero_turn_started_monotonic
            if self._hand_manager is not None
            else None
        )
        if (
            prefix == "Preflop recommendation"
            and isinstance(turn_started_at, int | float)
        ):
            elapsed_ms = (time.monotonic() - turn_started_at) * 1000.0
            recommendation_text = (
                f"{recommendation_text} "
                f"turn_to_recommendation_ms={elapsed_ms:.0f}"
            )
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
    def _log_preflop_recommendation_context(game_state: GameState) -> None:
        """Log the preflop state used by the recommendation engine."""
        GameLoop._log_preflop_action_integrity(game_state)
        max_bet = max(
            [game_state.hero.bet]
            + [player.bet for player in game_state.players.values()]
        )
        logger.info(
            "Preflop recommendation context: hand_id=%s hero_position=%s "
            "hero_cards=%s pot=%s hero_bet=%s max_bet=%s preflop_actions=%s "
            "strategy_defer_reason=%s",
            game_state.hand_id,
            game_state.hero.position,
            game_state.hero.cards,
            game_state.pot,
            game_state.hero.bet,
            max_bet,
            [
                {
                    "seat": action.seat,
                    "action": action.action,
                    "amount": action.amount,
                    "confidence": action.confidence,
                }
                for action in game_state.preflop_actions
            ],
            game_state.strategy_defer_reason,
        )

    @staticmethod
    def _log_preflop_action_integrity(game_state: GameState) -> None:
        """Log preflop blind/action consistency diagnostics."""
        positions: dict[int, str | None] = {1: game_state.hero.position}
        for seat in range(2, 7):
            player = game_state.players.get(str(seat))
            positions[seat] = (
                getattr(player, "position", None)
                if player is not None
                else None
            )

        street_actions = list(game_state.current_street_actions)
        blinds = [
            action
            for action in street_actions
            if action.action.upper() in {"BLIND_SB", "BLIND_BB"}
        ]
        warnings: list[str] = []
        blind_by_seat = {action.seat: action for action in blinds}

        if not any(action.action.upper() == "BLIND_SB" for action in blinds):
            warnings.append("sb_missing")
        if not any(action.action.upper() == "BLIND_BB" for action in blinds):
            warnings.append("bb_missing")
        for action in street_actions:
            action_name = action.action.upper()
            blind = blind_by_seat.get(action.seat)
            if blind is None or action_name not in {"CALL", "BET", "RAISE"}:
                continue
            if action.amount == blind.amount:
                warnings.append("same_seat_blind_and_call_same_amount")
        for blind in blinds:
            blind_name = blind.action.upper()
            active_position_count = sum(
                1 for position in positions.values() if position is not None
            )
            if active_position_count == 2 and blind_name == "BLIND_SB":
                expected_position = "BTN"
            else:
                expected_position = "SB" if blind_name == "BLIND_SB" else "BB"
            if positions.get(blind.seat) not in {None, expected_position}:
                warnings.append("blind_seat_mismatch_position")
        if game_state.hero.position == "BB" and 1 not in blind_by_seat:
            warnings.append("hero_bb_but_no_blind")

        logger.info(
            "PREFLOP_ACTION_INTEGRITY: hand_id=%s positions=%s blinds=%s "
            "preflop_actions=%s warnings=%s",
            game_state.hand_id,
            positions,
            GameLoop._action_records_for_log(blinds),
            GameLoop._action_records_for_log(game_state.preflop_actions),
            sorted(set(warnings)),
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
        self._hero_cards_recommendation_started_for_hand = True

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
        promoted = self._hand_manager.rejoin_seat(
            seat,
            allow_folded_rejoin=True,
        )
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
        self._record_solver_suppressed_context_from_snapshot(
            snapshot=self._pending_recommendation_context,
            reason=reason,
            request_id=active_id,
            exact_key=self._pending_recommendation_exact_key,
            coarse_key=self._pending_recommendation_coarse_key,
        )
        self._pending_recommendation_cancelled_ids.add(active_id)
        logger.info(
            "Async recommendation cancelled: request_id=%d reason=%s",
            active_id,
            reason,
        )

    def _clear_pending_state(self, reason: str = "pending_cleared") -> None:
        """Clear active pending metadata without stopping the worker thread."""
        with self._pending_recommendation_lock:
            active_id = self._pending_recommendation_active_id
            if active_id is not None:
                self._cancel_pending_recommendation_locked(reason)
            thread = self._pending_recommendation_thread
            if thread is not None and not thread.is_alive():
                self._pending_recommendation_thread = None
            self._pending_recommendation_context = None
            self._pending_recommendation_active_id = None
            self._pending_recommendation_exact_key = None
            self._pending_recommendation_coarse_key = None
            self._cleanup_async_recommendation_state_locked()

    def _is_pending_recommendation_alive(self) -> bool:
        """Return True when a background solver thread is still running."""
        with self._pending_recommendation_lock:
            thread = self._pending_recommendation_thread
            return thread is not None and thread.is_alive()

    def _solver_exact_context_key(self, game_state: GameState) -> str:
        """Return a detailed key for exact HU solver context matching."""
        max_opponent_bet = max(
            [
                int(player.bet or 0)
                for seat, player in game_state.players.items()
                if seat != "1" and player is not None
            ],
            default=0,
        )
        payload = {
            "hand_id": game_state.hand_id,
            "phase": game_state.phase,
            "board": list(game_state.board or []),
            "pot": int(game_state.pot or 0),
            "hero_bet": int(game_state.hero.bet or 0),
            "max_opponent_bet": max_opponent_bet,
            "current_street_actions": [
                {
                    "seat": action.seat,
                    "action": action.action,
                    "amount": action.amount,
                }
                for action in game_state.current_street_actions
            ],
            "active_player_count": game_state.active_player_count,
            "hero_cards": list(game_state.hero.cards or []),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _solver_context_key(self, game_state: GameState) -> str:
        """Return the exact HU solver context key for compatibility."""
        return self._solver_exact_context_key(game_state)

    def _solver_bet_bucket(self, amount: int, pot: int) -> str:
        """Bucket an amount for coarse Solver context matching."""
        config = getattr(self, "_config", {}) or {}
        blind_bb = max(1, int(config.get("game", {}).get("blind_bb", 100)))
        amount = int(amount or 0)
        pot = int(pot or 0)
        if amount <= 0:
            return "NONE"
        if amount >= blind_bb * 50:
            return "ALL_IN"
        if amount <= blind_bb * 2:
            return "BET_SMALL"
        if pot > 0 and amount <= int(pot * 0.75):
            return "BET_HALF"
        if pot > 0 and amount <= int(pot * 1.5):
            return "BET_POT"
        return "BET_LARGE"

    def _solver_action_signature(self, game_state: GameState) -> list[tuple[int, str]]:
        """Return a coarse street-action signature for retry suppression."""
        signature: list[tuple[int, str]] = []
        pot = int(game_state.pot or 0)
        for action in game_state.current_street_actions or []:
            action_name = (action.action or "").upper()
            if action_name in {"CHECK", "CALL", "FOLD"}:
                bucket = action_name
            elif action_name == "ALL_IN":
                bucket = "ALL_IN"
            elif action_name in {"BET", "RAISE"}:
                bucket = self._solver_bet_bucket(int(action.amount or 0), pot)
            else:
                bucket = action_name
            signature.append((action.seat, bucket))
        return signature

    def _solver_coarse_context_key(self, game_state: GameState) -> str:
        """Return a coarse key to suppress repeated stale/timeout Solver starts."""
        max_opponent_bet = max(
            [
                int(player.bet or 0)
                for seat, player in game_state.players.items()
                if seat != "1" and player is not None
            ],
            default=0,
        )
        facing_bet = max(0, max_opponent_bet - int(game_state.hero.bet or 0))
        payload = {
            "hand_id": game_state.hand_id,
            "phase": game_state.phase,
            "board": list(game_state.board or []),
            "hero_cards": list(game_state.hero.cards or []),
            "active_player_count": game_state.active_player_count,
            "facing_bet_bucket": self._solver_bet_bucket(
                facing_bet,
                int(game_state.pot or 0),
            ),
            "street_action_signature": self._solver_action_signature(game_state),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _solver_context_keys(self, game_state: GameState) -> tuple[str, str]:
        """Return exact and coarse HU solver context keys."""
        exact_key = self._solver_exact_context_key(game_state)
        coarse_key = self._solver_coarse_context_key(game_state)
        logger.debug(
            "SOLVER_CONTEXT_KEY_BUILT: exact=%s coarse=%s",
            exact_key,
            coarse_key,
        )
        return exact_key, coarse_key

    def _record_solver_suppressed_context_from_snapshot(
        self,
        snapshot: dict[str, object] | None,
        reason: str,
        request_id: int | None,
        exact_key: str | None,
        coarse_key: str | None,
    ) -> None:
        """Record a discarded async Solver context by its saved keys."""
        hand_id = snapshot.get("hand_id") if snapshot is not None else None
        phase = snapshot.get("phase") if snapshot is not None else None
        self._record_solver_suppressed_context(
            reason=reason,
            request_id=request_id,
            hand_id=hand_id,
            phase=phase,
            exact_key=exact_key,
            coarse_key=coarse_key,
        )

    def _record_solver_suppressed_context(
        self,
        reason: str,
        request_id: int | None,
        hand_id: object,
        phase: object,
        exact_key: str | None,
        coarse_key: str | None,
    ) -> None:
        """Record a Solver context that should not be retried immediately."""
        if not coarse_key:
            return
        if not hasattr(self, "_solver_suppressed_contexts"):
            self._solver_suppressed_contexts = {}
        self._solver_suppressed_contexts[coarse_key] = {
            "reason": reason,
            "request_id": request_id,
            "hand_id": hand_id,
            "phase": phase,
            "exact_key": exact_key,
            "created_at": time.monotonic(),
        }
        logger.info(
            "SOLVER_CONTEXT_SUPPRESSED: reason=%s request_id=%s hand_id=%s "
            "phase=%s key=%s",
            reason,
            request_id,
            hand_id,
            phase,
            coarse_key,
        )

    def _record_solver_timeout_context(self, game_state: GameState) -> None:
        """Remember a solver timeout context to suppress immediate retries."""
        if not hasattr(self, "_solver_timeout_contexts"):
            self._solver_timeout_contexts = {}
        key, coarse_key = self._solver_context_keys(game_state)
        self._solver_timeout_contexts[key] = time.monotonic()
        self._record_solver_suppressed_context(
            reason="timeout",
            request_id=self._pending_recommendation_active_id,
            hand_id=game_state.hand_id,
            phase=game_state.phase,
            exact_key=key,
            coarse_key=coarse_key,
        )
        logger.info(
            "SOLVER_TIMEOUT_CONTEXT_RECORDED: key=%s hand_id=%s phase=%s "
            "board=%s pot=%s actions=%s",
            key,
            game_state.hand_id,
            game_state.phase,
            game_state.board,
            game_state.pot,
            [
                (action.seat, action.action, action.amount)
                for action in game_state.current_street_actions
            ],
        )

    def _is_solver_retry_suppressed(self, game_state: GameState) -> bool:
        """Return True when a similar Solver context should not be retried."""
        if not hasattr(self, "_solver_timeout_contexts"):
            self._solver_timeout_contexts = {}
        if not hasattr(self, "_solver_suppressed_contexts"):
            self._solver_suppressed_contexts = {}
        exact_key, coarse_key = self._solver_context_keys(game_state)
        now = time.monotonic()
        ttl = max(
            0.1,
            float(getattr(self, "_solver_context_suppression_ttl_sec", 12.0)),
        )
        for key, entry in list(self._solver_suppressed_contexts.items()):
            created_at = float(entry.get("created_at", 0.0) or 0.0)
            if now - created_at > ttl:
                self._solver_suppressed_contexts.pop(key, None)
        suppressed = self._solver_suppressed_contexts.get(coarse_key)
        if suppressed is not None:
            logger.info(
                "SOLVER_RETRY_SUPPRESSED: "
                "reason=previous_stale_or_timeout_similar_context "
                "hand_id=%s phase=%s coarse_key=%s previous_reason=%s",
                game_state.hand_id,
                game_state.phase,
                coarse_key,
                suppressed.get("reason"),
            )
            return True
        if exact_key not in self._solver_timeout_contexts:
            return False
        logger.info(
            "SOLVER_RETRY_SUPPRESSED: reason=previous_timeout_same_context "
            "hand_id=%s phase=%s key=%s",
            game_state.hand_id,
            game_state.phase,
            exact_key,
        )
        return True

    def _clear_solver_timeout_contexts(self, reason: str) -> None:
        """Clear timeout retry suppression when the poker context advances."""
        if not hasattr(self, "_solver_timeout_contexts"):
            self._solver_timeout_contexts = {}
        if not hasattr(self, "_solver_suppressed_contexts"):
            self._solver_suppressed_contexts = {}
        timeout_count = len(self._solver_timeout_contexts)
        suppressed_count = len(self._solver_suppressed_contexts)
        if timeout_count == 0 and suppressed_count == 0:
            return
        logger.info(
            "SOLVER_TIMEOUT_CONTEXTS_CLEARED: reason=%s count=%d "
            "suppressed_count=%d",
            reason,
            timeout_count,
            suppressed_count,
        )
        self._solver_timeout_contexts.clear()
        self._solver_suppressed_contexts.clear()

    @staticmethod
    def _solver_timeout_recommendation() -> Recommendation:
        """Return a non-strategic HUD recommendation for solver timeout."""
        return Recommendation(
            action="SOLVER_TIMEOUT",
            amount=0,
            reason="Solver timeout: no reliable solver result",
            confidence="low",
            strategy_source="solver_timeout",
        )

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
    ) -> bool:
        """Start a daemon worker thread for HU postflop solver computation.

        Args:
            game_state: Current GameState to deep-copy for the worker.
            snapshot: Decision-point snapshot for later freshness checks.

        Returns:
            True when a new worker was started; False when an in-flight worker
            already owns the Solver.
        """
        game_state_copy = copy.deepcopy(game_state)
        exact_key, coarse_key = self._solver_context_keys(game_state)

        with self._pending_recommendation_lock:
            existing_thread = self._pending_recommendation_thread
            if existing_thread is not None and existing_thread.is_alive():
                logger.info(
                    "SOLVER_START_SUPPRESSED: reason=worker_already_alive "
                    "active_request_id=%s hand_id=%s phase=%s",
                    self._pending_recommendation_active_id,
                    game_state.hand_id,
                    game_state.phase,
                )
                self._notify_hud_computing(
                    "SOLVER STILL RUNNING\nWaiting for current solver..."
                )
                return False
            self._pending_recommendation_id += 1
            request_id = self._pending_recommendation_id
            self._pending_recommendation_context = snapshot
            self._pending_recommendation_active_id = request_id
            self._pending_recommendation_exact_key = exact_key
            self._pending_recommendation_coarse_key = coarse_key
            self._pending_recommendation_cancelled_ids.discard(request_id)
            self._pending_recommendation_completed.pop(request_id, None)

            thread = threading.Thread(
                target=self._run_recommendation_worker,
                args=(request_id, game_state_copy, snapshot, exact_key, coarse_key),
                daemon=True,
            )
            self._pending_recommendation_thread = thread
        thread.start()

        logger.info(
            "Async recommendation started: request_id=%d phase=%s",
            request_id,
            snapshot.get("phase", "unknown"),
        )
        return True

    def _run_recommendation_worker(
        self,
        request_id: int,
        game_state_copy: GameState,
        snapshot: dict[str, object] | None = None,
        exact_key: str | None = None,
        coarse_key: str | None = None,
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
        if snapshot is None:
            snapshot = self._build_recommendation_context_snapshot(game_state_copy)
        if exact_key is None or coarse_key is None:
            exact_key, coarse_key = self._solver_context_keys(game_state_copy)
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
                    snapshot=snapshot,
                    exact_key=exact_key,
                    coarse_key=coarse_key,
                )
            )
        logger.info("Async recommendation completed: request_id=%d", request_id)

    def _log_async_stale_detail(
        self,
        request_id: int,
        snapshot: dict[str, object] | None,
        current_state: GameState,
    ) -> None:
        """Log the exact freshness differences for a stale async result."""
        snapshot = snapshot or {}
        logger.info(
            "ASYNC_RECOMMENDATION_STALE_DETAIL: request_id=%s "
            "snapshot_phase=%s current_phase=%s snapshot_board=%s "
            "current_board=%s snapshot_action_count=%s current_action_count=%s "
            "snapshot_hero_turn=%s current_hero_turn=%s",
            request_id,
            snapshot.get("phase"),
            current_state.phase,
            snapshot.get("board"),
            tuple(current_state.board or []),
            snapshot.get("current_street_actions_count"),
            len(current_state.current_street_actions or []),
            snapshot.get("hero_is_my_turn"),
            current_state.hero.is_my_turn,
        )

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
                    completed = self._pending_recommendation_completed.get(
                        completed_id
                    )
                    if completed is not None:
                        self._record_solver_suppressed_context_from_snapshot(
                            snapshot=completed.snapshot,
                            reason="inactive_request",
                            request_id=completed_id,
                            exact_key=completed.exact_key,
                            coarse_key=completed.coarse_key,
                        )
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
                    completed = self._pending_recommendation_completed.get(
                        completed_id
                    )
                    if completed is not None:
                        self._record_solver_suppressed_context_from_snapshot(
                            snapshot=completed.snapshot,
                            reason="inactive_request",
                            request_id=completed_id,
                            exact_key=completed.exact_key,
                            coarse_key=completed.coarse_key,
                        )
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
            pending_exact_key = getattr(self, "_pending_recommendation_exact_key", None)
            pending_coarse_key = getattr(
                self,
                "_pending_recommendation_coarse_key",
                None,
            )

        if cancelled:
            self._record_solver_suppressed_context_from_snapshot(
                snapshot=completed.snapshot or pending_ctx,
                reason="cancelled",
                request_id=active_id,
                exact_key=completed.exact_key or pending_exact_key,
                coarse_key=completed.coarse_key or pending_coarse_key,
            )
            logger.info(
                "Async recommendation discarded: request_id=%d reason=cancelled",
                active_id,
            )
            with self._pending_recommendation_lock:
                self._finish_async_request_locked(active_id)
            return None

        if pending_ctx is None:
            self._record_solver_suppressed_context_from_snapshot(
                snapshot=completed.snapshot,
                reason="inactive_request",
                request_id=active_id,
                exact_key=completed.exact_key or pending_exact_key,
                coarse_key=completed.coarse_key or pending_coarse_key,
            )
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
            self._log_async_stale_detail(active_id, pending_ctx, current_state)
            self._record_solver_suppressed_context_from_snapshot(
                snapshot=completed.snapshot or pending_ctx,
                reason="stale",
                request_id=active_id,
                exact_key=completed.exact_key or pending_exact_key,
                coarse_key=completed.coarse_key or pending_coarse_key,
            )
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
            "Async recommendation accepted: request_id=%s hand_id=%s phase=%s "
            "source=%s action=%s amount=%s confidence=%s reason=%s latency=%s",
            active_id,
            current_state.hand_id,
            current_state.phase,
            result.strategy_source,
            result.action,
            result.amount,
            result.confidence,
            result.reason[:160],
            result.latency_breakdown,
        )
        if result.strategy_source == "fallback":
            logger.warning(
                "Async fallback recommendation accepted: request_id=%s hand_id=%s "
                "phase=%s reason=%s latency=%s",
                active_id,
                current_state.hand_id,
                current_state.phase,
                result.reason,
                result.latency_breakdown,
            )
        if result.strategy_source == "solver_timeout":
            self._record_solver_timeout_context(current_state)
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
            self._pending_recommendation_exact_key = None
            self._pending_recommendation_coarse_key = None
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
                action_name = hero_non_fold_action.action.upper()
                if action_name == "CHECK" and self._is_current_recommendation_fold():
                    prioritized = self._prioritize_recommended_fold_badge(
                        game_state,
                        action_name,
                        0.0,
                    )
                    if prioritized:
                        self._drop_same_frame_hero_check(game_state)
                    else:
                        self._mark_pending_hero_fold_badge_recovery()
                elif (
                    action_name == "CHECK"
                    and self._hand_manager.replace_recent_hero_check_with_fold(
                        max_age_sec=1.5
                    )
                ):
                    logger.info("Hero FOLD recovered from CHECK via fold badge")
                    self._clear_hero_card_cache(
                        "hero fold badge recovered from check"
                    )
                elif action_name == "CHECK":
                    self._mark_pending_hero_fold_badge_recovery()
                else:
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
                if (
                    action_name.upper() == "CHECK"
                    and self._is_current_recommendation_fold()
                    and self._prioritize_recommended_fold_badge(
                        game_state,
                        action_name,
                        age,
                    )
                ):
                    pass
                elif (
                    action_name.upper() == "CHECK"
                    and self._hand_manager.replace_recent_hero_check_with_fold(
                        max_age_sec=1.5
                    )
                ):
                    logger.info(
                        "Hero FOLD recovered from recent CHECK via fold badge"
                    )
                    self._clear_hero_card_cache(
                        "hero fold badge recovered from recent check"
                    )
                else:
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

    def _filter_low_confidence_opponent_folds(
        self,
        game_state: GameState,
        actions: list[ActionRecord],
    ) -> list[ActionRecord]:
        """Drop weak opponent FOLDs that conflict with card or hand evidence."""
        filtered: list[ActionRecord] = []
        players_in_hand = self._hand_manager.get_players_in_hand()
        for action in actions:
            if (
                action.seat == 1
                or action.action.upper() != "FOLD"
                or action.confidence != "low"
            ):
                filtered.append(action)
                continue

            reason = self._low_confidence_fold_ignore_reason(
                game_state,
                action.seat,
                players_in_hand,
            )
            if reason is None:
                filtered.append(action)
                continue

            logger.info(
                "Opponent low-confidence FOLD ignored: seat=%d reason=%s",
                action.seat,
                reason,
            )
        return filtered

    def _low_confidence_fold_ignore_reason(
        self,
        game_state: GameState,
        seat: int,
        players_in_hand: set[int],
    ) -> str | None:
        """Return why a weak opponent FOLD should be ignored, if any."""
        if self._is_visual_obstruction_protected():
            return "visual_obstruction"
        if self._last_seat_card_states.get(seat, False):
            return "recent_card_detected"
        if seat in self._seat_card_confirmed:
            return "seat_card_confirmed"
        player = game_state.players.get(str(seat))
        if player is not None and player.cards_visible:
            return "cards_visible"
        if seat in players_in_hand:
            return "in_current_hand"
        return None

    def _latch_hero_fold_badge_ignore(self, reason: str, action: str) -> None:
        """Ignore subsequent hero fold-badge latch results for this hand."""
        self._hero_fold_badge_ignored_for_hand = True
        self._hero_fold_badge_ignored_reason = reason
        logger.info(
            "Hero fold badge ignore latched for hand: reason=%s action=%s",
            reason,
            action,
        )

    def _mark_pending_hero_fold_badge_recovery(self) -> None:
        """Defer same-frame Hero CHECK plus fold-badge recovery until after poll."""
        self._pending_hero_fold_badge_recovery = True
        self._pending_hero_fold_badge_recovery_since = time.monotonic()
        logger.info("Hero fold badge recovery pending: same-frame CHECK detected")

    def _clear_pending_hero_fold_badge_recovery(self) -> None:
        """Clear deferred Hero fold-badge recovery state."""
        self._pending_hero_fold_badge_recovery = False
        self._pending_hero_fold_badge_recovery_since = None

    def _recover_pending_hero_fold_badge(self, game_state: GameState) -> None:
        """Recover a same-frame Hero CHECK to FOLD after HandManager records it."""
        if not self._pending_hero_fold_badge_recovery:
            return

        if game_state.phase in {"hand_end", "waiting"}:
            self._clear_pending_hero_fold_badge_recovery()
            return

        age = 0.0
        if self._pending_hero_fold_badge_recovery_since is not None:
            age = time.monotonic() - self._pending_hero_fold_badge_recovery_since

        if age > 1.5:
            logger.info(
                "Hero fold badge pending recovery expired: age=%.2fs",
                age,
            )
            self._clear_pending_hero_fold_badge_recovery()
            return

        replaced = self._hand_manager.replace_recent_hero_check_with_fold(
            max_age_sec=1.5,
        )
        if replaced:
            logger.info(
                "Hero FOLD recovered from pending same-frame CHECK via fold badge"
            )
            self._clear_hero_card_cache(
                "hero fold badge recovered from pending same-frame check"
            )
            self._clear_pending_hero_fold_badge_recovery()
            return

        logger.debug(
            "Hero fold badge pending recovery not ready yet: age=%.2fs",
            age,
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

    def _is_current_recommendation_fold(self) -> bool:
        """Return whether the current visible recommendation is FOLD."""
        recommendation = self.current_recommendation or self._previous_recommendation
        if recommendation is None:
            return False
        return recommendation.action.upper() == "FOLD"

    def _prioritize_recommended_fold_badge(
        self,
        game_state: GameState,
        action_name: str,
        age: float,
    ) -> bool:
        """Recover or record Hero FOLD when a FOLD recommendation is active."""
        if action_name.upper() != "CHECK":
            return False
        if age > 1.5:
            return False

        replaced = self._hand_manager.replace_recent_hero_check_with_fold(
            max_age_sec=1.5,
        )
        recorded = False
        if not replaced:
            recorded = self._hand_manager.record_hero_fold_from_badge(
                reason="recommended_fold_no_recent_check",
            )
        if not replaced and not recorded:
            return False

        logger.info(
            "Hero FOLD badge prioritized over recent CHECK because "
            "recommendation was FOLD: age=%.2fs hand_id=%s phase=%s",
            age,
            self._hand_manager.hand_id,
            self._hand_manager.phase,
        )
        if replaced:
            logger.info("Hero FOLD recovered from CHECK via fold badge")
        self._clear_hero_card_cache("hero fold badge prioritized over check")
        self._clear_pending_hero_fold_badge_recovery()
        return True

    @staticmethod
    def _drop_same_frame_hero_check(game_state: GameState) -> None:
        """Remove same-frame Hero CHECK after fold badge has confirmed FOLD."""
        game_state.actions_since_last_frame = [
            action
            for action in game_state.actions_since_last_frame
            if not (action.seat == 1 and action.action.upper() == "CHECK")
        ]

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

    def _clear_hand_position_lock(self, reason: str) -> None:
        if self._hand_positions is not None or self._hand_dealer_seat is not None:
            logger.info(
                "POSITION_LOCK_CLEARED: Position lock cleared: reason=%s "
                "previous_hand_id=%s "
                "previous_dealer=%s previous_positions=%s",
                reason,
                self._hand_position_hand_id,
                self._hand_dealer_seat,
                self._hand_positions,
            )
        else:
            logger.debug("Position lock clear skipped: reason=%s no existing lock", reason)
        self._hand_positions = None
        self._hand_dealer_seat = None
        self._hand_position_hand_id = None
        self._last_position_apply_log_key = None

    def _update_hand_position_lock(self, game_state: GameState) -> None:
        """Recalculate and apply locked positions from the latest hand state."""
        hand_id = self._hand_manager.hand_id if self._hand_manager else None
        phase = self._hand_manager.phase if self._hand_manager else game_state.phase
        logger.debug(
            "Position lock check: hm_hand_id=%s game_hand_id=%s hm_phase=%s "
            "game_phase=%s game_dealer=%s cached_dealer=%s hand_dealer=%s "
            "current_lock_hand_id=%s",
            hand_id,
            game_state.hand_id,
            self._hand_manager.phase if self._hand_manager else None,
            game_state.phase,
            game_state.dealer_seat,
            self._cached_dealer_seat,
            self._hand_dealer_seat,
            self._hand_position_hand_id,
        )
        if self._hand_manager is None:
            self._log_position_lock_skip(
                "no_hand_manager",
                hand_id,
                phase,
                None,
                "none",
                [],
            )
            game_state.hero.position = None
            return

        if not game_state.table_visible:
            self._clear_hand_position_lock("table not visible")
            self._log_position_lock_skip(
                "inactive_phase",
                hand_id,
                phase,
                game_state.dealer_seat,
                "game_state" if game_state.dealer_seat is not None else "none",
                [],
            )
            game_state.hero.position = None
            return

        if phase not in {"preflop", "flop", "turn", "river"}:
            self._clear_hand_position_lock(f"phase={phase}")
            self._log_position_lock_skip(
                "inactive_phase",
                hand_id,
                phase,
                game_state.dealer_seat,
                "game_state" if game_state.dealer_seat is not None else "none",
                [],
            )
            game_state.hero.position = None
            return

        if hand_id is None:
            self._log_position_lock_skip(
                "no_hand_id",
                hand_id,
                phase,
                game_state.dealer_seat,
                "game_state" if game_state.dealer_seat is not None else "none",
                [],
            )
            game_state.hero.position = None
            return

        if self._hand_manager.hand_just_started:
            self._clear_hand_position_lock("hand start")

        dealer_seat, dealer_source = self._select_dealer_for_position(game_state)
        if dealer_seat is None:
            self._log_position_lock_skip(
                "no_dealer",
                hand_id,
                phase,
                dealer_seat,
                dealer_source,
                [],
            )
            game_state.hero.position = None
            return

        active_seats = self._active_seats_for_position(
            game_state,
            allow_seated_fallback=False,
        )
        if not active_seats:
            self._log_position_lock_skip(
                "no_active_seats",
                hand_id,
                phase,
                dealer_seat,
                dealer_source,
                active_seats,
            )
            game_state.hero.position = None
            return

        if (
            self._hand_positions is not None
            and self._hand_dealer_seat == dealer_seat
            and self._hand_position_hand_id == hand_id
        ):
            self._apply_locked_positions(game_state)
            self._log_position_lock_skip(
                "existing_lock_current",
                hand_id,
                phase,
                dealer_seat,
                dealer_source,
                active_seats,
            )
            return
        if (
            self._hand_positions is not None
            and self._hand_position_hand_id == hand_id
            and self._hand_dealer_seat is not None
            and self._hand_dealer_seat != dealer_seat
        ):
            self._apply_locked_positions(game_state)
            return

        positions = calculate_positions(dealer_seat, active_seats)
        if not positions:
            self._log_position_lock_skip(
                "calculate_positions_empty",
                hand_id,
                phase,
                dealer_seat,
                dealer_source,
                active_seats,
            )
            game_state.hero.position = None
            return

        self._hand_dealer_seat = dealer_seat
        self._hand_positions = positions
        self._hand_position_hand_id = hand_id
        self._apply_locked_positions(game_state)
        logger.info(
            "Position lock dealer selected: hand_id=%s phase=%s dealer=%s "
            "dealer_source=%s",
            hand_id,
            phase,
            dealer_seat,
            dealer_source,
        )
        logger.info(
            "POSITION_LOCK_APPLIED: Position lock updated: hand_id=%s phase=%s "
            "dealer=%s active_seats=%s "
            "positions=%s hero_position=%s",
            hand_id,
            phase,
            dealer_seat,
            active_seats,
            positions,
            game_state.hero.position,
        )

    def _log_position_lock_skip(
        self,
        reason: str,
        hand_id: int | None,
        phase: str | None,
        dealer_seat: int | None,
        dealer_source: str,
        active_seats: list[int],
    ) -> None:
        """Log a position-lock skip reason with duplicate INFO suppression."""
        key = (
            hand_id,
            reason,
            phase,
            dealer_seat,
            tuple(active_seats),
            dealer_source,
        )
        log_method = logger.debug if key == self._last_position_lock_log_key else logger.info
        log_method(
            "POSITION_LOCK_SKIPPED: Position lock skipped: reason=%s hand_id=%s "
            "phase=%s dealer=%s "
            "active_seats=%s dealer_source=%s",
            reason,
            hand_id,
            phase,
            dealer_seat,
            active_seats,
            dealer_source,
        )
        self._last_position_lock_log_key = key

    @staticmethod
    def _log_position_lock_ignored(
        reason: str,
        hand_id: int | None,
        phase: str | None,
        locked_dealer: int | None,
        observed_dealer: int | None,
        hero_position: str | None,
        active_seats: list[int],
    ) -> None:
        """Log ignored dealer OCR changes while preserving an active lock."""
        logger.info(
            "POSITION_LOCK_IGNORED: reason=%s hand_id=%s phase=%s "
            "locked_dealer=%s observed_dealer=%s hero_position=%s "
            "active_seats=%s",
            reason,
            hand_id,
            phase,
            locked_dealer,
            observed_dealer,
            hero_position,
            active_seats,
        )

    def _select_dealer_for_position(
        self,
        game_state: GameState,
    ) -> tuple[int | None, str]:
        """Return the dealer seat and source used for position locking."""
        if game_state.dealer_seat is not None:
            return game_state.dealer_seat, "game_state"
        if self._cached_dealer_seat is not None:
            return self._cached_dealer_seat, "cached"
        if self._hand_dealer_seat is not None:
            return self._hand_dealer_seat, "locked"
        return None, "none"

    def _active_seats_for_position(
        self,
        game_state: GameState,
        *,
        allow_seated_fallback: bool = True,
    ) -> list[int]:
        """Return seat numbers eligible for position assignment."""
        players_in_hand = self._hand_manager.get_players_in_hand()
        active_seats = [seat for seat in players_in_hand if 1 <= seat <= 6]

        if allow_seated_fallback and len(active_seats) <= 1:
            active_seats = [1]
            for seat_key, player in game_state.players.items():
                if player.is_seated:
                    active_seats.append(int(seat_key))

        return sorted(set(active_seats))

    def _apply_locked_positions(self, game_state: GameState) -> None:
        """Apply locked dealer and hero position to a GameState."""
        if self._hand_positions is None:
            return
        current_hand_id = self._hand_manager.hand_id if self._hand_manager else None
        phase = self._hand_manager.phase if self._hand_manager else game_state.phase
        if self._hand_position_hand_id != current_hand_id:
            self._clear_hand_position_lock("hand id mismatch")
            return
        if (
            game_state.dealer_seat is not None
            and self._hand_dealer_seat is not None
            and game_state.dealer_seat != self._hand_dealer_seat
        ):
            if phase in {"preflop", "flop", "turn", "river"}:
                self._log_position_lock_ignored(
                    "active_hand_dealer_changed",
                    current_hand_id,
                    phase,
                    self._hand_dealer_seat,
                    game_state.dealer_seat,
                    get_hero_position(self._hand_positions, hero_seat=1),
                    (
                        self._active_seats_for_position(
                            game_state,
                            allow_seated_fallback=False,
                        )
                        if self._hand_manager is not None
                        else []
                    ),
                )
            else:
                self._clear_hand_position_lock("dealer mismatch")
                return
        game_state.dealer_seat = self._hand_dealer_seat
        game_state.hero.position = get_hero_position(self._hand_positions, hero_seat=1)
        for seat in range(2, 7):
            player = game_state.players.get(str(seat))
            if player is not None:
                setattr(player, "position", self._hand_positions.get(seat))
        apply_key = (
            self._hand_position_hand_id,
            phase,
            self._hand_dealer_seat,
            game_state.hero.position,
            tuple(sorted(self._hand_positions.items())),
        )
        log_method = (
            logger.debug
            if apply_key == self._last_position_apply_log_key
            else logger.info
        )
        log_method(
            "POSITION_LOCK_APPLIED: Position lock applied: hand_id=%s phase=%s "
            "dealer=%s "
            "hero_position=%s positions=%s",
            self._hand_position_hand_id,
            phase,
            self._hand_dealer_seat,
            game_state.hero.position,
            self._hand_positions,
        )
        self._last_position_apply_log_key = apply_key

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
                            "Waiting: hero cards recognized candidate - %s",
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
                        "Waiting: hero cards recognized candidate - %s",
                        hero_cards,
                    )
                    self._last_waiting_log = None
        if game_state.phase == "waiting":
            if self._hero_cards_missing(game_state.hero.cards):
                self._update_waiting_hero_card_candidate(None)
            elif not self._update_waiting_hero_card_candidate(game_state.hero.cards):
                game_state.hero_cards_unstable_reason = "hero_cards_waiting_unstable"
                game_state.hero.cards = None
                game_state.hero.cards_visible = False

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

    def _reset_waiting_hero_card_candidate(self) -> None:
        """Clear waiting-state hero card stability candidate."""
        self._hero_card_candidate = None
        self._hero_card_candidate_streak = 0

    def _reset_active_hero_card_validation(self) -> None:
        """Clear active-hand hero card invalidation state."""
        self._hero_card_active_mismatch_streak = 0
        self._hero_cards_invalid_for_hand = False
        self._hero_cards_invalid_reason = None
        self._hero_cards_recommendation_started_for_hand = False

    def _update_waiting_hero_card_candidate(
        self,
        cards: list[str | None] | None,
    ) -> bool:
        """Return True when waiting hero cards are stable enough to use."""
        if self._hero_cards_missing(cards) or self._is_visual_obstruction_protected():
            self._reset_waiting_hero_card_candidate()
            return False

        candidate = [str(card) for card in cards or []]
        if self._hero_card_candidate == candidate:
            self._hero_card_candidate_streak += 1
        else:
            self._hero_card_candidate = candidate
            self._hero_card_candidate_streak = 1

        if self._hero_card_candidate_streak >= self._hero_card_confirm_frames:
            logger.info(
                "Waiting hero cards stable: %s streak=%d/%d",
                candidate,
                self._hero_card_candidate_streak,
                self._hero_card_confirm_frames,
            )
            return True

        logger.info(
            "Waiting hero cards candidate: %s streak=%d/%d",
            candidate,
            self._hero_card_candidate_streak,
            self._hero_card_confirm_frames,
        )
        return False

    def _validate_active_hero_cards(
        self,
        frame: np.ndarray,
        game_state: GameState,
    ) -> None:
        """Invalidate active hands when fresh hero OCR contradicts cached cards."""
        if self._hand_manager is None:
            return
        phase = self._hand_manager.phase
        if phase not in {"preflop", "flop", "turn", "river"}:
            return
        if self._cached_hero_cards is None:
            return
        if self._is_visual_obstruction_protected():
            return

        fresh_cards = self._format_hero_cards(
            self._card_recognizer.recognize_hero_cards(frame, log_info=False)
        )
        if self._hero_cards_missing(fresh_cards):
            return

        fresh_list = [str(card) for card in fresh_cards or []]
        cached_list = list(self._cached_hero_cards)
        if fresh_list == cached_list:
            self._hero_card_active_mismatch_streak = 0
            return

        self._hero_card_active_mismatch_streak += 1
        if self._hero_card_active_mismatch_streak < self._hero_card_mismatch_confirm_frames:
            logger.warning(
                "Hero cards mismatch candidate: cached=%s fresh=%s "
                "streak=%d/%d phase=%s",
                cached_list,
                fresh_list,
                self._hero_card_active_mismatch_streak,
                self._hero_card_mismatch_confirm_frames,
                phase,
            )
            return

        reason = "hero_cards_changed_during_active_hand"
        if self._hero_cards_recommendation_started_for_hand:
            reason = "hero_cards_changed_after_recommendation"
        self._hero_cards_invalid_for_hand = True
        self._hero_cards_invalid_reason = reason
        game_state.hero_cards_unstable_reason = reason
        logger.warning(
            "Hero cards invalidated for hand: cached=%s fresh=%s reason=%s",
            cached_list,
            fresh_list,
            reason,
        )
        logger.warning(
            "Active hand abandoned because hero cards became unstable: "
            "hand_id=%s phase=%s cached=%s fresh=%s",
            self._hand_manager.hand_id,
            phase,
            cached_list,
            fresh_list,
        )
        if self._abandon_active_hand("hero_cards_unstable"):
            game_state.phase = self._hand_manager.phase
            game_state.hand_id = self._hand_manager.hand_id
            game_state.hero.cards = None
            game_state.hero.cards_visible = False
            game_state.hero.in_current_hand = False

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
            if self._hand_positions is not None:
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
            self._reset_waiting_hero_card_candidate()
            self._reset_active_hero_card_validation()
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
