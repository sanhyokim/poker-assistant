"""Action estimation from consecutive GameState frames."""

import logging
from typing import Any, TypedDict

from core.game_state import ActionRecord, GameState, StateDiff, compute_state_diff

logger = logging.getLogger(__name__)


class EstimateResult(TypedDict):
    """Action estimator result."""

    game_event: str | None
    actions: list[ActionRecord]
    filtered_pot: int | None
    pot_spike_hold: bool
    suspicious_pot_spike: bool


class ActionEstimator:
    """Estimate game events and player actions from GameState differences.

    Args:
        config: Full config dictionary.

    Attributes:
        _none_streak: Seat string to consecutive None-frame count placeholder.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        ae_config = config.get("action_estimation", {})
        game_config = config.get("game", {})
        recognition_config = config.get("recognition", {})

        self.new_hand_pot_ratio = float(ae_config.get("new_hand_pot_ratio", 0.3))
        self.new_hand_min_pot_bb = int(ae_config.get("new_hand_min_pot_bb", 2))
        self.raise_threshold = float(ae_config.get("raise_threshold", 1.1))
        self.blind_bb = int(game_config.get("blind_bb", 100))
        self.blind_sb = int(game_config.get("blind_sb", 50))
        self._fold_confirm_frames = int(recognition_config.get("fold_confirm_frames", 3))
        self._pot_spike_ratio = float(recognition_config.get("pot_spike_ratio", 2.0))
        self._pot_spike_confirm_frames = int(
            recognition_config.get("pot_spike_confirm_frames", 2)
        )
        self._new_hand_confirm_frames = int(
            recognition_config.get("new_hand_confirm_frames", 2)
        )

        self._none_streak: dict[str, int] = {}
        self._pot_spike_streak = 0
        self._pot_spike_value = 0
        self._new_hand_streak = 0

        logger.info(
            "ActionEstimator initialized: new_hand_pot_ratio=%.2f, "
            "raise_threshold=%.2f, blind_bb=%d",
            self.new_hand_pot_ratio,
            self.raise_threshold,
            self.blind_bb,
        )

    def estimate(self, prev_state: GameState, curr_state: GameState) -> EstimateResult:
        """Estimate a game event and actions from consecutive states.

        Args:
            prev_state: Previous frame GameState.
            curr_state: Current frame GameState.

        Returns:
            Dictionary with game_event and actions.
        """
        diff = compute_state_diff(prev_state, curr_state)

        if not diff.any_change:
            actions = self._confirm_static_none_streaks(curr_state)
            return {
                "game_event": None,
                "actions": actions,
                "filtered_pot": None,
                "pot_spike_hold": False,
                "suspicious_pot_spike": False,
            }

        original_pot_curr = diff.pot_curr
        diff, pot_spike_hold, suspicious_pot_spike = self._filter_pot_spike(diff)
        filtered_pot: int | None = None
        if diff.pot_curr != original_pot_curr:
            filtered_pot = diff.pot_curr

        if not diff.any_change:
            return {
                "game_event": None,
                "actions": [],
                "filtered_pot": filtered_pot,
                "pot_spike_hold": pot_spike_hold,
                "suspicious_pot_spike": suspicious_pot_spike,
            }

        if self._check_new_hand(diff):
            return {
                "game_event": "NEW_HAND",
                "actions": [],
                "filtered_pot": None,
                "pot_spike_hold": pot_spike_hold,
                "suspicious_pot_spike": suspicious_pot_spike,
            }
        if diff.pot_curr != original_pot_curr:
            filtered_pot = diff.pot_curr

        if not diff.any_change:
            return {
                "game_event": None,
                "actions": [],
                "filtered_pot": filtered_pot,
                "pot_spike_hold": pot_spike_hold,
                "suspicious_pot_spike": suspicious_pot_spike,
            }

        if self._check_new_street(diff):
            return {
                "game_event": "NEW_STREET",
                "actions": [],
                "filtered_pot": filtered_pot,
                "pot_spike_hold": pot_spike_hold,
                "suspicious_pot_spike": suspicious_pot_spike,
            }

        if self._check_bets_collected(diff):
            return {
                "game_event": "BETS_COLLECTED",
                "actions": [],
                "filtered_pot": filtered_pot,
                "pot_spike_hold": pot_spike_hold,
                "suspicious_pot_spike": suspicious_pot_spike,
            }

        actions = self._analyze_seat_actions(prev_state, curr_state, diff)
        return {
            "game_event": None,
            "actions": actions,
            "filtered_pot": filtered_pot,
            "pot_spike_hold": pot_spike_hold,
            "suspicious_pot_spike": suspicious_pot_spike,
        }

    def _filter_pot_spike(self, diff: StateDiff) -> tuple[StateDiff, bool, bool]:
        """Filter one-frame pot spikes before event/action analysis.

        Args:
            diff: Computed StateDiff. The object is updated in place.

        Returns:
            The same StateDiff after pot spike filtering, whether the pot is
            temporarily held, and whether a suspicious OCR-like spike was ignored.
        """
        if not diff.pot_changed:
            self._pot_spike_streak = 0
            self._pot_spike_value = 0
            return diff, False, False

        if (
            diff.pot_prev > 0
            and diff.pot_curr > diff.pot_prev * self._pot_spike_ratio
        ):
            if self._is_suspicious_pot_spike(diff.pot_prev, diff.pot_curr, diff):
                logger.warning(
                    "Suspicious pot spike ignored: %d -> %d scaled10=%d, "
                    "holding previous value",
                    diff.pot_prev,
                    diff.pot_curr,
                    diff.pot_curr // 10,
                )
                self._pot_spike_streak = 0
                self._pot_spike_value = 0
                diff.pot_curr = diff.pot_prev
                diff.pot_changed = False
                diff.any_change = self._has_non_pot_change(diff)
                return diff, False, True

            self._pot_spike_streak += 1
            self._pot_spike_value = diff.pot_curr

            if self._pot_spike_streak < self._pot_spike_confirm_frames:
                logger.warning(
                    "Pot spike detected (streak=%d): %d -> %d, holding previous value",
                    self._pot_spike_streak,
                    diff.pot_prev,
                    diff.pot_curr,
                )
                diff.pot_curr = diff.pot_prev
                diff.pot_changed = False
                diff.any_change = self._has_non_pot_change(diff)
                return diff, True, False

            logger.info(
                "Pot spike confirmed (streak=%d): %d -> %d",
                self._pot_spike_streak,
                diff.pot_prev,
                diff.pot_curr,
            )
            self._pot_spike_streak = 0
            self._pot_spike_value = 0
            return diff, False, False

        self._pot_spike_streak = 0
        self._pot_spike_value = 0
        return diff, False, False

    def _is_suspicious_pot_spike(
        self,
        pot_prev: int,
        pot_curr: int,
        diff: StateDiff,
    ) -> bool:
        """Return whether a pot spike looks like an OCR digit-scale error."""
        if pot_prev <= 0 or pot_curr <= 0:
            return False

        if pot_curr <= pot_prev * self._pot_spike_ratio:
            return False

        scaled10 = pot_curr // 10
        if scaled10 > pot_prev and scaled10 <= pot_prev * 2:
            return True

        return False

    def _has_non_pot_change(self, diff: StateDiff) -> bool:
        """Return whether the diff still has changes after pot filtering."""
        return (
            diff.board_count_changed
            or diff.hero_stack_changed
            or diff.hero_bet_changed
            or diff.is_my_turn_changed
            or any(
                player_change["stack_changed"] or player_change["bet_changed"]
                for player_change in diff.player_changes.values()
            )
        )

    def estimate_blinds(
        self,
        curr_state: GameState,
        sb_seat: int,
        bb_seat: int,
    ) -> list[ActionRecord]:
        """Estimate blind posts from a NEW_HAND frame.

        Args:
            curr_state: Current NEW_HAND GameState.
            sb_seat: Small blind seat.
            bb_seat: Big blind seat.

        Returns:
            Blind action records for seats with positive bet values.
        """
        actions: list[ActionRecord] = []
        sb_bet = self._get_seat_bet(curr_state, sb_seat)
        bb_bet = self._get_seat_bet(curr_state, bb_seat)

        if sb_bet > 0:
            actions.append(
                ActionRecord(
                    seat=sb_seat,
                    action="BLIND_SB",
                    amount=sb_bet,
                    confidence="high",
                )
            )

        if bb_bet > 0:
            actions.append(
                ActionRecord(
                    seat=bb_seat,
                    action="BLIND_BB",
                    amount=bb_bet,
                    confidence="high",
                )
            )

        if actions:
            logger.info(
                "Blinds detected: %s",
                [(action.seat, action.action, action.amount) for action in actions],
            )

        return actions

    def _check_new_hand(self, diff: StateDiff) -> bool:
        """Return whether the diff indicates a new hand.

        Args:
            diff: Computed StateDiff.

        Returns:
            True when pot drops below the configured new-hand ratio.
        """
        if not diff.pot_changed:
            self._new_hand_streak = 0
            return False

        new_hand_threshold = max(self.blind_bb * self.new_hand_min_pot_bb, 20)
        is_new_hand_candidate = (
            diff.pot_curr < diff.pot_prev * self.new_hand_pot_ratio
            and diff.pot_prev > new_hand_threshold
        )

        if is_new_hand_candidate:
            self._new_hand_streak += 1
            if self._new_hand_streak < self._new_hand_confirm_frames:
                logger.info(
                    "NEW_HAND candidate (streak=%d/%d): pot %d -> %d "
                    "(threshold=%d), waiting for confirmation",
                    self._new_hand_streak,
                    self._new_hand_confirm_frames,
                    diff.pot_prev,
                    diff.pot_curr,
                    new_hand_threshold,
                )
                diff.pot_curr = diff.pot_prev
                diff.pot_changed = False
                diff.any_change = self._has_non_pot_change(diff)
                return False

            logger.info(
                "NEW_HAND confirmed (streak=%d): pot %d -> %d (threshold=%d)",
                self._new_hand_streak,
                diff.pot_prev,
                diff.pot_curr,
                new_hand_threshold,
            )
            self._new_hand_streak = 0
            return True

        self._new_hand_streak = 0
        return False

    def _check_new_street(self, diff: StateDiff) -> bool:
        """Return whether board card count increased.

        Args:
            diff: Computed StateDiff.

        Returns:
            True when a new street is detected.
        """
        if diff.board_count_changed and diff.board_count_curr > diff.board_count_prev:
            logger.info(
                "NEW_STREET detected: board cards %d -> %d",
                diff.board_count_prev,
                diff.board_count_curr,
            )
            return True
        return False

    def _check_bets_collected(self, diff: StateDiff) -> bool:
        """Return whether bets were collected into the pot.

        Args:
            diff: Computed StateDiff.

        Returns:
            True when pot increased and all current bets are zero.
        """
        if diff.board_count_changed:
            return False
        if not diff.pot_changed or diff.pot_curr <= diff.pot_prev:
            return False
        if diff.max_bet_curr == 0:
            logger.info(
                "BETS_COLLECTED detected: pot %d -> %d, all bets cleared",
                diff.pot_prev,
                diff.pot_curr,
            )
            return True
        return False

    def _analyze_seat_actions(
        self,
        prev_state: GameState,
        curr_state: GameState,
        diff: StateDiff,
    ) -> list[ActionRecord]:
        """Analyze hero and player actions from stack/bet changes.

        Args:
            prev_state: Previous GameState.
            curr_state: Current GameState.
            diff: Computed StateDiff.

        Returns:
            Detected action records sorted by seat.
        """
        _ = prev_state
        _ = curr_state
        actions: list[ActionRecord] = []

        hero_action = self._analyze_hero_action(diff)
        if hero_action is not None:
            actions.append(hero_action)

        for seat_key in ["2", "3", "4", "5", "6"]:
            action = self._analyze_player_action(
                int(seat_key),
                diff.player_changes.get(seat_key, {}),
                diff,
            )
            if action is not None:
                actions.append(action)

        actions = self._check_for_checks(prev_state, curr_state, diff, actions)

        actions.sort(key=lambda action: action.seat)

        if len(actions) >= 3:
            logger.warning(
                "3+ actions in single frame (%d), setting confidence=low",
                len(actions),
            )
            for action in actions:
                action.confidence = "low"

        if actions:
            logger.info(
                "Actions detected: %s",
                [
                    (action.seat, action.action, action.amount, action.confidence)
                    for action in actions
                ],
            )

        return actions

    def _check_for_checks(
        self,
        prev_state: GameState,
        curr_state: GameState,
        diff: StateDiff,
        detected_actions: list[ActionRecord],
    ) -> list[ActionRecord]:
        """Detect CHECK actions that do not change stack or bet values.

        Args:
            prev_state: Previous GameState.
            curr_state: Current GameState.
            diff: Computed StateDiff.
            detected_actions: Actions already detected from value changes.

        Returns:
            Action list with CHECK appended when the pattern is detected.
        """
        _ = prev_state
        _ = curr_state

        if (
            diff.is_my_turn_changed
            and diff.is_my_turn_prev is True
            and diff.is_my_turn_curr is False
            and not diff.hero_stack_changed
            and not diff.hero_bet_changed
        ):
            detected_actions.append(
                ActionRecord(
                    seat=1,
                    action="CHECK",
                    amount=0,
                    confidence="high",
                )
            )
            logger.info("Hero CHECK detected (is_my_turn True->False)")
            return detected_actions

        if (
            not diff.pot_changed
            and not diff.board_count_changed
            and not diff.hero_stack_changed
            and not diff.hero_bet_changed
            and diff.max_bet_prev == diff.max_bet_curr
            and not any(
                player_change["stack_changed"] or player_change["bet_changed"]
                for player_change in diff.player_changes.values()
            )
            and len(detected_actions) == 0
            and diff.any_change
        ):
            detected_actions.append(
                ActionRecord(
                    seat=0,
                    action="CHECK",
                    amount=0,
                    confidence="low",
                )
            )
            logger.info("Opponent CHECK estimated from non-value state change")

        return detected_actions

    def _analyze_hero_action(self, diff: StateDiff) -> ActionRecord | None:
        """Analyze hero stack/bet changes.

        Args:
            diff: Computed StateDiff.

        Returns:
            Detected hero action, or None.
        """
        if not diff.hero_stack_changed and not diff.hero_bet_changed:
            return None

        hero_stack_prev = diff.hero_stack_prev or 0
        hero_stack_curr = diff.hero_stack_curr or 0

        if hero_stack_prev > 0 and hero_stack_curr == 0 and diff.hero_bet_changed:
            return ActionRecord(
                seat=1,
                action="ALL_IN",
                amount=diff.hero_bet_curr,
                confidence="high",
            )

        if hero_stack_curr < hero_stack_prev and diff.hero_bet_changed:
            # Reclassify as ALL_IN if hero committed ≥90% of previous stack
            if hero_stack_prev > 0 and diff.hero_bet_curr >= hero_stack_prev * 0.9:
                logger.info(
                    "Hero action reclassified as ALL_IN: amount=%d, "
                    "previous_stack=%d",
                    diff.hero_bet_curr,
                    hero_stack_prev,
                )
                return ActionRecord(
                    seat=1,
                    action="ALL_IN",
                    amount=diff.hero_bet_curr,
                    confidence="high",
                )

            if diff.max_bet_prev == 0:
                return ActionRecord(
                    seat=1,
                    action="BET",
                    amount=diff.hero_bet_curr,
                    confidence="high",
                )
            if diff.hero_bet_curr > diff.max_bet_prev * self.raise_threshold:
                return ActionRecord(
                    seat=1,
                    action="RAISE",
                    amount=diff.hero_bet_curr,
                    confidence="high",
                )
            return ActionRecord(
                seat=1,
                action="CALL",
                amount=diff.hero_bet_curr,
                confidence="high",
            )

        return None

    def _analyze_player_action(
        self,
        seat_num: int,
        p_change: dict[str, Any],
        diff: StateDiff,
    ) -> ActionRecord | None:
        """Analyze one non-hero player's stack/bet changes.

        Args:
            seat_num: Seat number from 2 to 6.
            p_change: Player change dictionary from StateDiff.
            diff: Computed StateDiff.

        Returns:
            Detected action, or None.
        """
        stack_changed = bool(p_change.get("stack_changed", False))
        bet_changed = bool(p_change.get("bet_changed", False))

        if not stack_changed and not bet_changed:
            return None

        stack_prev = p_change.get("stack_prev")
        stack_curr = p_change.get("stack_curr")
        bet_prev = int(p_change.get("bet_prev", 0))
        bet_curr = int(p_change.get("bet_curr", 0))
        seat_key = str(seat_num)

        if stack_prev is not None and stack_curr is None:
            return self._update_none_streak(seat_num)

        if stack_curr is not None and seat_key in self._none_streak:
            logger.debug(
                "Seat %d stack recovered from None, resetting streak",
                seat_num,
            )
            del self._none_streak[seat_key]

        if stack_prev is None or stack_curr is None:
            return None

        stack_prev_int = int(stack_prev)
        stack_curr_int = int(stack_curr)

        suspicious = self._is_suspicious_bet_amount(
            bet_curr, stack_prev_int, stack_curr_int, diff.pot_prev,
        )
        if suspicious:
            logger.warning(
                "Suspicious bet amount detected: seat=%d bet_curr=%d "
                "scaled10=%d stack_prev=%d stack_curr=%d stack_drop=%d "
                "pot_prev=%d pot_curr=%d",
                seat_num,
                bet_curr,
                bet_curr // 10,
                stack_prev_int,
                stack_curr_int,
                stack_prev_int - stack_curr_int,
                diff.pot_prev,
                diff.pot_curr,
            )

        if stack_prev_int > 0 and stack_curr_int == 0 and bet_changed:
            confidence = "low" if suspicious else "high"
            action_type = "ALL_IN"
            if suspicious:
                action_type = "BET" if diff.max_bet_prev == 0 else (
                    "RAISE" if bet_curr > diff.max_bet_prev * self.raise_threshold
                    else "CALL"
                )
                logger.info(
                    "ALL_IN reclassification skipped for seat %d: "
                    "suspicious bet amount (bet_curr=%d)",
                    seat_num,
                    bet_curr,
                )
            return ActionRecord(
                seat=seat_num,
                action=action_type,
                amount=bet_curr,
                confidence=confidence,
            )

        if stack_curr_int < stack_prev_int and bet_changed and bet_curr > bet_prev:
            # Reclassify as ALL_IN if the player committed ≥90% of their previous stack
            if (
                stack_prev_int > 0
                and bet_curr >= stack_prev_int * 0.9
                and not suspicious
            ):
                logger.info(
                    "Action reclassified as ALL_IN: seat=%d, amount=%d, "
                    "previous_stack=%d",
                    seat_num,
                    bet_curr,
                    stack_prev_int,
                )
                return ActionRecord(
                    seat=seat_num,
                    action="ALL_IN",
                    amount=bet_curr,
                    confidence="high",
                )

            confidence = "low" if suspicious else "high"

            if diff.max_bet_prev == 0:
                return ActionRecord(
                    seat=seat_num,
                    action="BET",
                    amount=bet_curr,
                    confidence=confidence,
                )
            if bet_curr > diff.max_bet_prev * self.raise_threshold:
                return ActionRecord(
                    seat=seat_num,
                    action="RAISE",
                    amount=bet_curr,
                    confidence=confidence,
                )
            return ActionRecord(
                seat=seat_num,
                action="CALL",
                amount=bet_curr,
                confidence=confidence,
            )

        return None

    def _confirm_static_none_streaks(self, state: GameState) -> list[ActionRecord]:
        """Advance existing None streaks during otherwise unchanged frames.

        Args:
            state: Current GameState.

        Returns:
            Confirmed FOLD actions.
        """
        actions: list[ActionRecord] = []
        for seat_key in list(self._none_streak.keys()):
            player = state.players.get(seat_key)
            if player is not None and player.stack is None:
                action = self._update_none_streak(int(seat_key))
                if action is not None:
                    actions.append(action)
            else:
                logger.debug(
                    "Seat %s stack recovered from None, resetting streak",
                    seat_key,
                )
                del self._none_streak[seat_key]
        return actions

    def _update_none_streak(self, seat_num: int) -> ActionRecord | None:
        """Update a seat's consecutive None streak and confirm FOLD if needed.

        Args:
            seat_num: Seat number from 2 to 6.

        Returns:
            Low-confidence FOLD after the configured confirmation count,
            otherwise None.
        """
        seat_key = str(seat_num)
        self._none_streak[seat_key] = self._none_streak.get(seat_key, 0) + 1
        streak = self._none_streak[seat_key]

        if streak >= self._fold_confirm_frames:
            logger.info(
                "Possible FOLD from stack=None for seat %d "
                "(%d consecutive None frames)",
                seat_num,
                streak,
            )
            del self._none_streak[seat_key]
            return ActionRecord(
                seat=seat_num,
                action="FOLD",
                amount=0,
                confidence="low",
            )

        logger.debug(
            "Seat %d stack=None streak=%d, waiting for confirmation",
            seat_num,
            streak,
        )
        return None

    def _get_seat_bet(self, state: GameState, seat: int) -> int:
        if seat == 1:
            return state.hero.bet
        player = state.players.get(str(seat))
        if player is None:
            return 0
        return player.bet

    def _normalize_bet_amount_text(
        self,
        raw_text: str,
    ) -> tuple[int | None, bool, str]:
        """Normalize a BET amount OCR string into a safe integer.

        Returns:
            amount: Normalized amount, or None on failure.
            suspicious: True when the digit count suggests a decimal was dropped.
            reason: Short description of the normalization applied.
        """
        text = str(raw_text).strip()
        text = text.replace(",", "")

        if not text:
            return None, True, "empty"

        if "." in text:
            integer_part = text.split(".", 1)[0]
            digits = "".join(ch for ch in integer_part if ch.isdigit())
            if not digits:
                return None, True, "decimal_without_integer"
            return int(digits), False, "decimal_truncated"

        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            return None, True, "no_digits"

        return int(digits), False, "integer"

    def _is_suspicious_bet_amount(
        self,
        bet_curr: int,
        stack_prev: int,
        stack_curr: int,
        pot_prev: int,
    ) -> bool:
        """Return whether a bet amount looks suspiciously large.

        Only flags amounts where a missing-decimal OCR misread is the most
        plausible explanation. Small bets, bets that match the stack decrease,
        and genuine all-ins are excluded.
        """
        if bet_curr <= 0:
            return False

        # Small and normal-sized bets are never suspicious
        if bet_curr <= self.blind_bb * 5:
            return False

        stack_drop = stack_prev - stack_curr
        scaled10 = bet_curr // 10

        # If the bet amount closely matches the actual stack decrease,
        # the OCR reading is consistent and not suspicious.
        if stack_drop > 0 and abs(bet_curr - stack_drop) <= max(2, int(self.blind_bb * 0.1)):
            return False

        # 1. Bet is extremely large vs pot AND scaled10 is a natural pot-sized bet.
        if (
            stack_curr > 0
            and pot_prev > 0
            and bet_curr > pot_prev * 5
            and scaled10 > 0
            and scaled10 <= pot_prev * 2
        ):
            return True

        # 2. Bet is much larger than the stack decrease AND scaled10
        #    falls close to the actual stack drop — suggesting a missing
        #    decimal point.
        if (
            stack_drop > 0
            and bet_curr > stack_drop * 3
            and abs(scaled10 - stack_drop) <= max(5, int(stack_drop * 0.2))
        ):
            return True

        return False

    def reset(self) -> None:
        """Reset placeholder internal state."""
        self._none_streak.clear()
        self._pot_spike_streak = 0
        self._pot_spike_value = 0
        self._new_hand_streak = 0
        logger.info("ActionEstimator: internal state reset")
