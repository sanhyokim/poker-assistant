"""Hand lifecycle state machine and action history management."""

import copy
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.game_state import ActionRecord, GameState

logger = logging.getLogger(__name__)


@dataclass
class StreetActions:
    """Action history and metadata for one street."""

    street: str
    board: list[str] = field(default_factory=list)
    actions: list[ActionRecord] = field(default_factory=list)
    recommendation: str | None = None
    human_action: str | None = None
    followed_recommendation: bool | None = None
    time_to_recommend_ms: float | None = None
    latency_breakdown: dict[str, float] = field(default_factory=dict)
    spectate_only: bool = False


class HandManager:
    """Manage one poker hand's lifecycle and accumulated actions.

    Args:
        config: Parsed config.yaml dictionary.
    """

    NEW_HAND_COOLDOWN_SEC = 5.0
    _PARTICIPANT_ACTIONS = {
        "BET",
        "CALL",
        "RAISE",
        "ALL_IN",
        "BLIND_SB",
        "BLIND_BB",
    }
    _HERO_BOUNDARY_ACTIONS = {"CHECK", "CALL", "BET", "RAISE", "ALL_IN"}
    _ACTIVE_PHASES = {"preflop", "flop", "turn", "river"}
    _VALID_TRANSITIONS: dict[str, set[str]] = {
        "waiting": {"preflop"},
        "preflop": {"flop", "hand_end"},
        "flop": {"turn", "hand_end"},
        "turn": {"river", "hand_end"},
        "river": {"hand_end"},
        "hand_end": {"waiting"},
    }

    def __init__(self, config: dict[str, Any], db_path: str | None = None) -> None:
        self._config = config
        capture_config = config.get("capture", {})
        replay_config = config.get("replay", {})
        game_config = config.get("game", {})
        self._polling_interval_sec = float(
            capture_config.get("polling_interval_sec", 0.5)
        )
        self._waiting_timeout_sec = 10.0
        self._db_path = db_path or config.get("db", {}).get(
            "path",
            "data/poker_assistant.db",
        )
        self._db_conn: sqlite3.Connection | None = None
        self._replay_dir = str(replay_config.get("base_dir", "hand_replays"))
        self._replay_retention_days = int(replay_config.get("retention_days", 30))
        self._table_id = str(game_config.get("table_id", "unknown"))

        self._phase = "waiting"
        self._hand_id: int | None = None
        self._next_hand_id = 1

        self._hero_cards: list[str] | None = None
        self._players_in_hand: dict[str, bool] = {}
        self._participated_seats: set[str] = set()
        self._folded_seats: set[str] = set()
        self._current_players: dict[str, dict[str, Any]] = {}

        self._street_actions: dict[str, StreetActions] = {}
        self._all_actions: list[ActionRecord] = []
        self._last_frame_actions: list[ActionRecord] = []

        self._hero_card_missing_count = 0
        self._showdown_stable_count = 0
        self._last_pot_at_showdown: int | None = None
        self._hand_end_timestamp: float | None = None
        self._hero_folded = False
        self._seen_hero_cards_this_hand = False
        self._turn_start_state: GameState | None = None
        self._turn_end_state: GameState | None = None
        self._prev_is_my_turn = False
        self._last_hero_action: ActionRecord | None = None
        self._last_saved_hand_id: int | None = None
        self._hand_just_started = False
        self._hand_start_monotonic: float | None = None
        self._prev_frame_pot: int | None = None
        self._last_hand_end_reason: str | None = None
        self._participant_observation_active = False
        self._participant_observation_started_at: float | None = None
        self._participant_observation_duration_sec = 1.5
        self._participant_observed_seats: set[str] = set()

        self._init_db()

    @property
    def phase(self) -> str:
        """Return the current lifecycle phase."""
        return self._phase

    @property
    def hand_id(self) -> int | None:
        """Return the current hand ID, or None while waiting."""
        return self._hand_id

    @property
    def last_saved_hand_id(self) -> int | None:
        """Return the latest hand ID successfully saved to the database."""
        return self._last_saved_hand_id

    @property
    def hand_just_started(self) -> bool:
        """Return whether a new hand started during the current frame."""
        return self._hand_just_started

    @property
    def hero_folded(self) -> bool:
        """Return whether hero has folded in the current hand."""
        return self._hero_folded

    def get_players_in_hand(self) -> set[int]:
        """Return seat numbers still participating in the current hand.

        Folded seats are excluded. All-in seats remain included because they
        still have a claim to the pot. Waiting and hand_end states return an
        empty set.
        """
        if self._phase in {"waiting", "hand_end"}:
            return set()
        return {
            int(seat)
            for seat, in_hand in self._players_in_hand.items()
            if in_hand
        }

    def rejoin_seat(self, seat: int) -> bool:
        """Promote a seated player back into the current hand.

        Args:
            seat: Seat number from 2 to 6 to rejoin.

        Returns:
            True if the seat was promoted, False otherwise.
        """
        if self._phase not in self._ACTIVE_PHASES:
            return False

        seat_key = str(seat)
        if self._players_in_hand.get(seat_key, False):
            return False

        if seat_key in self._folded_seats:
            logger.info("Rejoin rejected for seat %d: already in folded_seats", seat)
            return False

        self._players_in_hand[seat_key] = True
        self._participated_seats.add(seat_key)
        logger.info(
            "Seat %d rejoined hand %s via revalidation. players_in_hand: %s",
            seat,
            self._hand_id,
            {
                seat_id: in_hand
                for seat_id, in_hand in self._players_in_hand.items()
                if in_hand
            },
        )
        return True

    def close(self) -> None:
        """Close the SQLite connection."""
        try:
            self._cleanup_old_replays()
        except Exception as exc:
            logger.warning("Replay cleanup failed: %s", exc)

        if self._db_conn is None:
            return
        try:
            self._db_conn.close()
            logger.info("Database connection closed")
        except sqlite3.Error as exc:
            logger.error("Error closing database: %s", exc)
        finally:
            self._db_conn = None

    def _cleanup_old_replays(self) -> None:
        """Remove dated replay directories older than retention_days."""
        import shutil
        from datetime import timedelta

        base_dir = Path(self._replay_dir)
        if not base_dir.is_dir():
            return

        cutoff_date = datetime.now() - timedelta(days=self._replay_retention_days)
        for entry in base_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                dir_date = datetime.strptime(entry.name, "%Y-%m-%d")
            except ValueError:
                continue

            if dir_date < cutoff_date:
                shutil.rmtree(entry)
                logger.info("Cleaned up old replay directory: %s", entry)

    def process_frame(self, game_state: GameState) -> None:
        """Process one frame's GameState.

        Args:
            game_state: Current frame GameState.
        """
        self._hand_just_started = False

        if self._phase == "waiting":
            if self._has_visible_hero_cards(game_state):
                self._start_new_hand(game_state)
            elif game_state.game_event == "NEW_STREET":
                logger.debug("Invalid transition ignored: waiting -> street")
            return

        if self._phase == "hand_end":
            if self._check_waiting_transition(game_state):
                self._transition_phase("waiting", game_state)
            return

        if game_state.game_event == "NEW_HAND":
            if self._hand_start_monotonic is not None:
                elapsed = time.monotonic() - self._hand_start_monotonic
                if elapsed < self.NEW_HAND_COOLDOWN_SEC:
                    logger.warning(
                        "NEW_HAND suppressed: only %.1fs since hand start "
                        "(cooldown: %.1fs), phase=%s",
                        elapsed,
                        self.NEW_HAND_COOLDOWN_SEC,
                        self._phase,
                    )
                    game_state.game_event = None
                else:
                    logger.info(
                        "NEW_HAND during active hand (phase=%s), forcing hand_end",
                        self._phase,
                    )
                    self._transition_phase("hand_end", game_state)
                    self._transition_phase("waiting", game_state)
                    return
            else:
                logger.info(
                    "NEW_HAND during active hand (phase=%s), forcing hand_end",
                    self._phase,
                )
                self._transition_phase("hand_end", game_state)
                self._transition_phase("waiting", game_state)
                return

        self._update_current_players(game_state)

        if game_state.game_event == "NEW_STREET":
            self._handle_new_street_event(game_state)

        if game_state.actions_since_last_frame:
            self._add_actions(game_state.actions_since_last_frame)
        else:
            self._last_frame_actions = []

        self._update_hero_turn_boundary(game_state)

        if self._check_hand_end_conditions(game_state):
            hand_end_reason = self._last_hand_end_reason
            self._transition_phase("hand_end", game_state)
            if hand_end_reason == "pot_decreased":
                self._transition_phase("waiting", game_state)
                self._prev_frame_pot = game_state.pot
                return

        self._prev_frame_pot = game_state.pot

    def get_current_street_actions(self) -> StreetActions | None:
        """Return the current street action history."""
        street = self._get_current_street_name()
        return self._street_actions.get(street)

    def get_all_actions(self) -> list[ActionRecord]:
        """Return all accumulated actions for the current hand."""
        return list(self._all_actions)

    def get_preflop_actions(self) -> list[ActionRecord]:
        """Return accumulated preflop actions excluding blind posts."""
        street_actions = self._street_actions.get("preflop")
        if street_actions is None:
            return []
        return [
            action
            for action in street_actions.actions
            if action.action.upper() not in {"BLIND_SB", "BLIND_BB"}
        ]

    def get_hand_summary(self) -> dict[str, Any] | None:
        """Return a JSON-ready summary for replay generation."""
        if self._hand_id is None:
            return None

        return {
            "hand_id": self._hand_id,
            "phase": self._phase,
            "hero_cards": self._hero_cards,
            "players_in_hand": dict(self._players_in_hand),
            "participated_seats": sorted(self._participated_seats),
            "actions": [
                {
                    "seat": action.seat,
                    "action": action.action,
                    "amount": action.amount,
                    "confidence": action.confidence,
                }
                for action in self._all_actions
            ],
            "streets": {
                street: {
                    "board": street_actions.board,
                    "actions": [
                        {
                            "seat": action.seat,
                            "action": action.action,
                            "amount": action.amount,
                            "confidence": action.confidence,
                        }
                        for action in street_actions.actions
                    ],
                }
                for street, street_actions in self._street_actions.items()
            },
        }

    def reset(self) -> None:
        """Clear all internal state and return to waiting."""
        self._phase = "waiting"
        self._hand_id = None
        self._next_hand_id = 1
        self._last_saved_hand_id = None
        self._hand_just_started = False
        self._clear_current_hand_state()
        logger.info("HandManager reset")

    def set_recommendation(
        self,
        recommendation: str,
        time_to_recommend_ms: float = 0.0,
        latency_breakdown: dict[str, float] | None = None,
    ) -> None:
        """Set the current street's strategy recommendation.

        Args:
            recommendation: Recommendation text such as "RAISE 300" or "FOLD".
            time_to_recommend_ms: Time spent preparing the recommendation.
            latency_breakdown: Optional latency details by subsystem.
        """
        street_actions = self.get_current_street_actions()
        if street_actions is None:
            return
        street_actions.recommendation = recommendation
        street_actions.time_to_recommend_ms = time_to_recommend_ms
        street_actions.latency_breakdown = latency_breakdown or {}
        logger.debug(
            "Recommendation set for %s: %s (%.1fms)",
            street_actions.street,
            recommendation,
            time_to_recommend_ms,
        )

    def set_human_action(self, human_action: str) -> None:
        """Set the current street's detected hero action.

        Args:
            human_action: Hero action text such as "CALL 200" or "FOLD".
        """
        street_actions = self.get_current_street_actions()
        if street_actions is None:
            return
        street_actions.human_action = human_action
        if street_actions.recommendation is None:
            street_actions.followed_recommendation = None
        else:
            recommended_action = street_actions.recommendation.strip().split()[0].upper()
            human_action_name = human_action.strip().split()[0].upper()
            street_actions.followed_recommendation = (
                recommended_action == human_action_name
            )
        logger.debug(
            "Human action set for %s: %s",
            street_actions.street,
            human_action,
        )

    def _init_db(self) -> None:
        """Initialize SQLite tables used by hand history persistence."""
        try:
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

            self._db_conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
            )
            self._db_conn.row_factory = sqlite3.Row
            self._db_conn.execute("PRAGMA journal_mode=WAL")
            self._db_conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS opponents (
                    player_name TEXT PRIMARY KEY,
                    long_term_style TEXT,
                    total_hands INTEGER DEFAULT 0,
                    first_seen TEXT,
                    last_seen TEXT,
                    vpip REAL DEFAULT 0.0,
                    pfr REAL DEFAULT 0.0,
                    three_bet_pct REAL DEFAULT 0.0,
                    cbet_flop_pct REAL DEFAULT 0.0,
                    fold_to_three_bet REAL DEFAULT 0.0,
                    went_to_showdown REAL DEFAULT 0.0,
                    freshness_note TEXT,
                    three_bet_opportunities INTEGER DEFAULT 0,
                    three_bet_count INTEGER DEFAULT 0,
                    cbet_flop_opportunities INTEGER DEFAULT 0,
                    cbet_flop_count INTEGER DEFAULT 0,
                    fold_to_three_bet_opportunities INTEGER DEFAULT 0,
                    fold_to_three_bet_count INTEGER DEFAULT 0,
                    wtsd_opportunities INTEGER DEFAULT 0,
                    wtsd_count INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS hand_history (
                    hand_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_name TEXT,
                    timestamp TEXT,
                    hole_cards TEXT,
                    actions TEXT,
                    result REAL,
                    board TEXT
                );
                """
            )
            self._db_conn.commit()
            logger.info("Database initialized: %s", self._db_path)
        except sqlite3.Error as exc:
            logger.error("Database initialization failed: %s", exc)
            self._db_conn = None

    def _ensure_db_connection(self) -> bool:
        """Return whether the SQLite connection is available, reconnecting if needed."""
        if self._db_conn is not None:
            try:
                self._db_conn.execute("SELECT 1").fetchone()
                return True
            except sqlite3.Error as exc:
                logger.warning("Database connection invalid, reconnecting: %s", exc)
                try:
                    self._db_conn.close()
                except sqlite3.Error:
                    pass
                self._db_conn = None

        self._init_db()
        if self._db_conn is None:
            logger.warning("DB connection not available, skipping save")
            return False
        logger.info("Database reconnected: %s", self._db_path)
        return True

    def _start_new_hand(self, game_state: GameState) -> None:
        """Start a new hand from a frame with visible hero cards."""
        if self._phase != "waiting":
            logger.warning("Starting new hand from phase=%s", self._phase)
            self._on_hand_end(game_state)

        self._clear_current_hand_state()
        self._hand_id = self._next_hand_id
        self._next_hand_id += 1
        self._hand_just_started = True
        self._hand_start_monotonic = time.monotonic()
        if self._has_visible_hero_cards(game_state):
            self._hero_cards = list(game_state.hero.cards or [])
        else:
            self._hero_cards = None
            logger.warning(
                "New hand started without complete hero cards: %s",
                game_state.hero.cards,
            )
        self._seen_hero_cards_this_hand = self._has_any_hero_card(game_state)
        action_participant_seats = self._participant_action_seats(
            game_state.actions_since_last_frame
        )
        self._players_in_hand = {
            seat_key: bool(
                player.cards_visible
                or player.bet > 0
                or seat_key in action_participant_seats
            )
            for seat_key, player in game_state.players.items()
        }
        self._players_in_hand["1"] = True
        self._participated_seats = {
            seat_key
            for seat_key, in_hand in self._players_in_hand.items()
            if in_hand
        }
        self._participant_observation_active = True
        self._participant_observation_started_at = time.monotonic()
        self._participant_observed_seats = {
            seat_key
            for seat_key, in_hand in self._players_in_hand.items()
            if seat_key != "1" and in_hand
        }
        logger.info(
            "Players in hand at start: %s",
            dict(self._players_in_hand),
        )
        logger.info(
            "Participant observation started: duration=%.1fs initial=%s",
            self._participant_observation_duration_sec,
            sorted(self._participant_observed_seats),
        )
        self._update_current_players(game_state)
        self._street_actions = {
            "preflop": StreetActions(street="preflop", board=[]),
            "flop": StreetActions(street="flop"),
            "turn": StreetActions(street="turn"),
            "river": StreetActions(street="river"),
        }
        self._prev_is_my_turn = game_state.hero.is_my_turn
        self._transition_phase("preflop", game_state)
        self._record_blinds(game_state)

        # If board cards are already visible at hand start, advance phase
        board_count = game_state.board_card_count
        suppress_fast_forward = bool(
            getattr(game_state, "suppress_phase_fast_forward", False)
        )
        if suppress_fast_forward and board_count >= 3:
            logger.info(
                "Phase fast-forward suppressed at hand start: "
                "board_count=%d reason=recent_hand_end_or_stale_clear",
                board_count,
            )
        elif board_count >= 5:
            self._transition_phase("flop", game_state)
            self._transition_phase("turn", game_state)
            self._transition_phase("river", game_state)
            logger.info(
                "Phase fast-forwarded to river: board_count=%d at hand start",
                board_count,
            )
        elif board_count >= 4:
            self._transition_phase("flop", game_state)
            self._transition_phase("turn", game_state)
            logger.info(
                "Phase fast-forwarded to turn: board_count=%d at hand start",
                board_count,
            )
        elif board_count >= 3:
            self._transition_phase("flop", game_state)
            logger.info(
                "Phase fast-forwarded to flop: board_count=%d at hand start",
                board_count,
            )

        logger.info(
            "New hand started: hand_id=%s, hero_cards=%s",
            self._hand_id,
            self._hero_cards,
        )

    def _transition_phase(self, new_phase: str, game_state: GameState) -> None:
        """Transition to a new phase if the transition is valid.

        Args:
            new_phase: Target phase.
            game_state: Current GameState.
        """
        if new_phase == self._phase:
            return

        valid_targets = self._VALID_TRANSITIONS.get(self._phase, set())
        if new_phase not in valid_targets:
            logger.debug("Invalid transition ignored: %s -> %s", self._phase, new_phase)
            return

        old_phase = self._phase
        self._phase = new_phase

        if new_phase in self._ACTIVE_PHASES and new_phase not in self._street_actions:
            self._street_actions[new_phase] = StreetActions(
                street=new_phase,
                board=list(game_state.board),
            )
        elif new_phase in {"flop", "turn", "river"}:
            street_actions = self._street_actions.get(new_phase)
            if street_actions is not None and game_state.board:
                street_actions.board = list(game_state.board)

        if new_phase == "hand_end":
            self._hand_end_timestamp = time.monotonic()
            self._on_hand_end(game_state)
        elif new_phase == "waiting":
            self._hand_id = None
            self._clear_current_hand_state()

        logger.info("Phase transition: %s -> %s", old_phase, new_phase)

    def _check_hand_end_conditions(self, game_state: GameState) -> bool:
        """Return whether the active hand should move to hand_end."""
        self._last_hand_end_reason = None
        if self._hero_folded:
            self._hero_card_missing_count = 0
        else:
            if self._has_any_hero_card(game_state):
                self._seen_hero_cards_this_hand = True
                self._hero_card_missing_count = 0
            elif self._seen_hero_cards_this_hand:
                if self._hero_state_unchanged(game_state):
                    self._hero_card_missing_count += 1
                else:
                    logger.debug(
                        "Hero card missing but state changed, not counting toward hand end"
                    )
                    self._hero_card_missing_count = 0
            else:
                self._hero_card_missing_count = 0

        if self._hero_card_missing_count >= 5:
            self._last_hand_end_reason = "hero_cards_missing"
            logger.info("Hand end: hero cards missing for 5 consecutive frames")
            return True

        for action in game_state.actions_since_last_frame:
            if action.seat == 1 and action.action.upper() == "FOLD":
                if not self._hero_folded:
                    self._hero_folded = True
                    self._players_in_hand["1"] = False
                    self._folded_seats.add("1")
                    logger.info("Hero folded; continuing table-hand observation")

        if (
            self._prev_frame_pot is not None
            and game_state.pot < self._prev_frame_pot
            and self._prev_frame_pot > 0
        ):
            self._last_hand_end_reason = "pot_decreased"
            logger.info(
                "Hand end: pot decreased (%d -> %d), payout detected",
                self._prev_frame_pot,
                game_state.pot,
            )
            return True

        self._showdown_stable_count = 0
        self._last_pot_at_showdown = None

        return False

    def _hero_state_unchanged(self, game_state: GameState) -> bool:
        """Return whether hero stack and bet match the turn-start snapshot.

        Args:
            game_state: Current GameState.

        Returns:
            True if no reference state exists, or if hero stack and bet are
            unchanged from the current turn start.
        """
        if self._turn_start_state is None:
            return True
        start_stack = self._turn_start_state.hero.stack or 0
        start_bet = self._turn_start_state.hero.bet or 0
        curr_stack = game_state.hero.stack or 0
        curr_bet = game_state.hero.bet or 0
        return curr_stack == start_stack and curr_bet == start_bet

    def _check_waiting_transition(self, game_state: GameState) -> bool:
        """Return whether hand_end should move back to waiting."""
        if game_state.game_event == "NEW_HAND":
            return True
        if self._hand_end_timestamp is None:
            return False
        return time.monotonic() - self._hand_end_timestamp >= self._waiting_timeout_sec

    def _add_actions(
        self,
        actions: list[ActionRecord],
        *,
        allow_hero_boundary_actions: bool = False,
    ) -> None:
        """Add non-duplicate actions to hand and street histories."""
        accepted_actions: list[ActionRecord] = []
        street_actions = self.get_current_street_actions()
        street = self._get_current_street_name() if street_actions is not None else None

        for action in actions:
            action_name = action.action.upper()
            if (
                not allow_hero_boundary_actions
                and action.seat == 1
                and action_name in self._HERO_BOUNDARY_ACTIONS
            ):
                logger.debug(
                    "Hero action from frame actions ignored; turn boundary "
                    "will record it: action=%s amount=%s",
                    action.action,
                    action.amount,
                )
                continue
            if self._is_duplicate_action(action, self._last_frame_actions):
                logger.debug("Duplicate action ignored: %s", action)
                continue
            self._all_actions.append(action)
            accepted_actions.append(action)
            if street_actions is not None:
                street_actions.actions.append(action)
            self._update_players_in_hand_from_action(action)
            logger.info(
                "Street action recorded: street=%s seat=%s action=%s "
                "amount=%s confidence=%s count=%d",
                street,
                action.seat,
                action.action,
                action.amount,
                action.confidence,
                len(street_actions.actions) if street_actions is not None else 0,
            )

        self._last_frame_actions = accepted_actions

    def _update_players_in_hand_from_action(self, action: ActionRecord) -> None:
        """Remove folded seats from the current hand participant set."""
        action_name = action.action.upper()
        if action.seat not in (None, 0) and action_name in self._PARTICIPANT_ACTIONS:
            self._participated_seats.add(str(action.seat))

        if action_name == "FOLD":
            seat_key = str(action.seat)
            if seat_key in self._folded_seats:
                logger.debug("Duplicate fold ignored: seat=%s", seat_key)
                return
            self._players_in_hand[seat_key] = False
            self._folded_seats.add(seat_key)
            logger.info(
                "Player folded: seat=%d, remaining players_in_hand: %s",
                action.seat,
                {
                    seat: in_hand
                    for seat, in_hand in self._players_in_hand.items()
                    if in_hand
                },
            )

    def _update_hero_turn_boundary(self, game_state: GameState) -> None:
        """Track hero turn start/end frames and record the resulting action."""
        current_is_my_turn = game_state.hero.is_my_turn

        if current_is_my_turn and not self._prev_is_my_turn:
            self._turn_start_state = copy.deepcopy(game_state)
            self._turn_end_state = None
            logger.info("Hero turn started")

        if not current_is_my_turn and self._prev_is_my_turn:
            self._turn_end_state = copy.deepcopy(game_state)
            logger.info("Hero turn ended")
            hero_action = self._detect_hero_action()
            if hero_action is not None:
                self._record_hero_action(hero_action)

        self._prev_is_my_turn = current_is_my_turn

    def _detect_hero_action(self) -> ActionRecord | None:
        """Detect hero action from saved turn boundary states.

        Returns:
            Detected hero action, or None if it cannot be determined.
        """
        if self._turn_start_state is None or self._turn_end_state is None:
            return None

        start = self._turn_start_state
        end = self._turn_end_state

        start_stack = start.hero.stack or 0
        end_stack = end.hero.stack or 0
        start_bet = start.hero.bet or 0
        end_bet = end.hero.bet or 0

        stack_change = start_stack - end_stack
        bet_change = end_bet - start_bet
        max_bet_at_start = self._get_max_bet(start)

        if end_stack == 0 and start_stack > 0:
            return ActionRecord(
                seat=1,
                action="ALL_IN",
                amount=stack_change,
                confidence="high",
            )

        if stack_change == 0 and bet_change == 0:
            if self._has_any_hero_card(end):
                return ActionRecord(
                    seat=1,
                    action="CHECK",
                    amount=0,
                    confidence="high",
                )
            return ActionRecord(
                seat=1,
                action="FOLD",
                amount=0,
                confidence="high",
            )

        if stack_change > 0 and bet_change > 0:
            if max_bet_at_start == 0:
                return ActionRecord(
                    seat=1,
                    action="BET",
                    amount=end_bet,
                    confidence="high",
                )
            if end_bet <= max_bet_at_start:
                return ActionRecord(
                    seat=1,
                    action="CALL",
                    amount=end_bet,
                    confidence="high",
                )
            if end_bet > max_bet_at_start * 1.1:
                return ActionRecord(
                    seat=1,
                    action="RAISE",
                    amount=end_bet,
                    confidence="high",
                )
            return ActionRecord(
                seat=1,
                action="CALL",
                amount=end_bet,
                confidence="low",
            )

        logger.warning(
            "Could not determine hero action: stack_change=%d, bet_change=%d",
            stack_change,
            bet_change,
        )
        return None

    def _record_hero_action(self, action: ActionRecord) -> None:
        """Record the detected hero action on the current street."""
        street_actions = self.get_current_street_actions()
        if street_actions is not None:
            street_actions.human_action = action.action
            if action.amount > 0:
                street_actions.human_action = f"{action.action} {action.amount}"
            if street_actions.recommendation is not None:
                street_actions.followed_recommendation = (
                    self._check_recommendation_followed(
                        street_actions.recommendation,
                        action,
                    )
                )

        self._last_hero_action = action
        if action.action == "FOLD":
            self._hero_folded = True

        self._add_actions([action], allow_hero_boundary_actions=True)
        logger.info("Hero action recorded: %s %d", action.action, action.amount)

    def _check_recommendation_followed(
        self,
        recommendation: str,
        action: ActionRecord,
    ) -> bool:
        """Return whether the hero action followed a recommendation."""
        recommendation_parts = recommendation.strip().split()
        recommended_action = recommendation_parts[0].upper() if recommendation_parts else ""
        return recommended_action == action.action

    def _record_blinds(self, game_state: GameState) -> None:
        """Record small blind and big blind actions after a new hand starts."""
        dealer_seat = game_state.dealer_seat
        if dealer_seat is None:
            logger.warning("Cannot record blinds: dealer_seat is None")
            return

        active_seats = [1]
        for seat_key, in_hand in self._players_in_hand.items():
            if in_hand:
                active_seats.append(int(seat_key))
        active_seats = sorted(set(active_seats))

        sb_seat, bb_seat = self._find_blind_seats(dealer_seat, active_seats)
        blind_actions: list[ActionRecord] = []

        if sb_seat is not None:
            sb_bet = self._get_seat_bet(game_state, sb_seat)
            if sb_bet is not None and sb_bet > 0:
                blind_actions.append(
                    ActionRecord(
                        seat=sb_seat,
                        action="BLIND_SB",
                        amount=sb_bet,
                        confidence="high",
                    )
                )

        if bb_seat is not None:
            bb_bet = self._get_seat_bet(game_state, bb_seat)
            if bb_bet is not None and bb_bet > 0:
                blind_actions.append(
                    ActionRecord(
                        seat=bb_seat,
                        action="BLIND_BB",
                        amount=bb_bet,
                        confidence="high",
                    )
                )

        if blind_actions:
            self._add_actions(blind_actions)

    def _find_blind_seats(
        self,
        dealer_seat: int,
        active_seats: list[int],
    ) -> tuple[int | None, int | None]:
        """Find small blind and big blind seats from dealer and active seats."""
        if len(active_seats) < 2:
            return None, None

        if len(active_seats) == 2:
            if dealer_seat not in active_seats:
                return None, None
            bb_candidates = [seat for seat in active_seats if seat != dealer_seat]
            return dealer_seat, bb_candidates[0]

        sb_seat = self._next_active_seat(dealer_seat, active_seats)
        if sb_seat is None:
            return None, None
        bb_seat = self._next_active_seat(sb_seat, active_seats)
        return sb_seat, bb_seat

    def _next_active_seat(
        self,
        from_seat: int,
        active_seats: list[int],
    ) -> int | None:
        """Return the next active seat clockwise from a seat."""
        for offset in range(1, 7):
            candidate = (from_seat - 1 + offset) % 6 + 1
            if candidate in active_seats:
                return candidate
        return None

    def _is_duplicate_action(
        self,
        action: ActionRecord,
        recent_actions: list[ActionRecord],
    ) -> bool:
        """Return whether an action duplicates the previous frame."""
        for recent in recent_actions:
            if recent.seat != action.seat or recent.action != action.action:
                continue
            if action.amount == 0 and recent.amount == 0:
                return True
            if recent.amount > 0:
                ratio = abs(action.amount - recent.amount) / recent.amount
                if ratio <= 0.05:
                    return True
        return False

    def _get_current_street_name(self) -> str:
        """Return the street name corresponding to the current phase."""
        if self._phase in self._ACTIVE_PHASES:
            return self._phase
        return "preflop"

    def _get_max_bet(self, state: GameState) -> int:
        """Return the maximum bet value across all seats."""
        bets = [state.hero.bet]
        for player in state.players.values():
            bets.append(player.bet)
        return max(bets) if bets else 0

    def _get_seat_bet(self, game_state: GameState, seat: int) -> int | None:
        """Return the bet amount for a seat."""
        if seat == 1:
            return game_state.hero.bet
        player = game_state.players.get(str(seat))
        if player is None:
            return None
        return player.bet

    def _handle_new_street_event(self, game_state: GameState) -> None:
        """Apply a NEW_STREET event to the phase machine."""
        board_count = game_state.board_card_count
        if board_count == 3 and self._phase == "preflop":
            self._transition_phase("flop", game_state)
        elif board_count == 4 and self._phase == "flop":
            self._transition_phase("turn", game_state)
        elif board_count == 5 and self._phase == "turn":
            self._transition_phase("river", game_state)
        else:
            logger.warning(
                "Invalid NEW_STREET ignored: phase=%s board_count=%d",
                self._phase,
                board_count,
            )

    def _has_visible_hero_cards(self, game_state: GameState) -> bool:
        """Return whether hero has two visible, recognized cards."""
        return (
            game_state.hero.cards is not None
            and len(game_state.hero.cards) == 2
            and all(card is not None for card in game_state.hero.cards)
        )

    def _has_any_hero_card(self, game_state: GameState) -> bool:
        """Return whether at least one hero card is visible/recognized."""
        return (
            game_state.hero.cards is not None
            and len(game_state.hero.cards) == 2
            and any(card is not None for card in game_state.hero.cards)
        )

    def _clear_current_hand_state(self) -> None:
        """Clear state scoped to the current hand."""
        self._hero_cards = None
        self._players_in_hand = {}
        self._participated_seats.clear()
        self._folded_seats = set()
        self._current_players = {}
        self._street_actions = {}
        self._all_actions = []
        self._last_frame_actions = []
        self._hand_start_monotonic = None
        self._hero_card_missing_count = 0
        self._showdown_stable_count = 0
        self._last_pot_at_showdown = None
        self._hand_end_timestamp = None
        self._hero_folded = False
        self._seen_hero_cards_this_hand = False
        self._turn_start_state = None
        self._turn_end_state = None
        self._prev_is_my_turn = False
        self._last_hero_action = None
        self._prev_frame_pot = None
        self._last_hand_end_reason = None
        self._participant_observation_active = False
        self._participant_observation_started_at = None
        self._participant_observed_seats.clear()

    def _on_hand_end(self, game_state: GameState) -> None:
        """Persist hand data when the hand reaches hand_end."""
        _ = game_state
        if self._hand_id is None:
            return

        logger.info("Hand %d ended, saving data", self._hand_id)
        self._save_to_db()
        self._save_replay_json()

    def _save_to_db(self) -> None:
        """Save hand history and update opponent statistics."""
        if not self._ensure_db_connection():
            return

        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            hole_cards = json.dumps(self._hero_cards) if self._hero_cards else None
            actions_with_streets = self._actions_with_streets()
            actions_json = json.dumps(actions_with_streets)
            board = self._collect_board()
            board_json = json.dumps(board) if board else None

            cursor = self._db_conn.cursor()
            cursor.execute(
                """
                INSERT INTO hand_history
                    (player_name, timestamp, hole_cards, actions, result, board)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("hero", timestamp, hole_cards, actions_json, None, board_json),
            )

            db_participants = sorted(
                seat_key
                for seat_key in self._participated_seats
                if seat_key != "1"
                and self._current_players.get(seat_key, {}).get("name")
            )
            logger.info("DB participants for hand %s: %s", self._hand_id, db_participants)
            if not db_participants:
                logger.warning(
                    "Hand %s has no DB participants; possible false hand start",
                    self._hand_id,
                )

            for seat_key, player in self._current_players.items():
                player_name = player.get("name")
                if not player_name or seat_key not in self._participated_seats:
                    continue
                player_seat = self._parse_seat(seat_key)
                if player_seat is None or player_seat == 1:
                    continue
                self._update_opponent_stats(
                    cursor,
                    str(player_name),
                    player_seat,
                    timestamp,
                    actions_with_streets,
                )

            self._db_conn.commit()
            self._last_saved_hand_id = self._hand_id
            logger.info("Hand %d saved to DB", self._hand_id)
        except sqlite3.Error as exc:
            logger.error("Failed to save hand %s to DB: %s", self._hand_id, exc)

    def _update_opponent_stats(
        self,
        cursor: sqlite3.Cursor,
        player_name: str,
        player_seat: int,
        timestamp: str,
        actions: list[dict[str, Any]],
    ) -> None:
        """Update one opponent's cumulative statistics for the current hand."""
        row = cursor.execute(
            "SELECT * FROM opponents WHERE player_name LIKE ? || '%' LIMIT 1",
            (player_name,),
        ).fetchone()
        player_actions = [
            action
            for action in actions
            if self._parse_seat(action.get("seat")) == player_seat
        ]

        vpip_this_hand = 1.0 if any(
            action["action"] in {"CALL", "BET", "RAISE", "ALL_IN"}
            and action.get("street", "preflop") == "preflop"
            for action in player_actions
        ) else 0.0
        pfr_this_hand = 1.0 if any(
            action["action"] in {"RAISE", "ALL_IN"}
            and action.get("street", "preflop") == "preflop"
            for action in player_actions
        ) else 0.0
        three_bet_value = self._calc_three_bet_pct(player_seat, actions)
        cbet_value = self._calc_cbet_flop_pct(player_seat, actions)
        fold_to_three_bet_value = self._calc_fold_to_three_bet(
            player_seat,
            actions,
        )
        wtsd_value = self._calc_went_to_showdown(player_seat, actions)

        three_bet_opp, three_bet_count = self._stat_counter(three_bet_value)
        cbet_opp, cbet_count = self._stat_counter(cbet_value)
        fold_to_three_bet_opp, fold_to_three_bet_count = self._stat_counter(
            fold_to_three_bet_value
        )
        wtsd_opp, wtsd_count = self._stat_counter(wtsd_value)

        if row is None:
            cursor.execute(
                """
                INSERT INTO opponents
                    (player_name, total_hands, first_seen, last_seen,
                     vpip, pfr, went_to_showdown,
                     three_bet_pct, cbet_flop_pct, fold_to_three_bet,
                     three_bet_opportunities, three_bet_count,
                     cbet_flop_opportunities, cbet_flop_count,
                     fold_to_three_bet_opportunities, fold_to_three_bet_count,
                     wtsd_opportunities, wtsd_count)
                VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    player_name,
                    timestamp,
                    timestamp,
                    vpip_this_hand * 100.0,
                    pfr_this_hand * 100.0,
                    self._percentage(wtsd_count, wtsd_opp),
                    self._percentage(three_bet_count, three_bet_opp),
                    self._percentage(cbet_count, cbet_opp),
                    self._percentage(
                        fold_to_three_bet_count,
                        fold_to_three_bet_opp,
                    ),
                    three_bet_opp,
                    three_bet_count,
                    cbet_opp,
                    cbet_count,
                    fold_to_three_bet_opp,
                    fold_to_three_bet_count,
                    wtsd_opp,
                    wtsd_count,
                ),
            )
            return

        total_hands = int(row["total_hands"]) + 1
        old_total = int(row["total_hands"])
        new_vpip = (
            float(row["vpip"]) * old_total + vpip_this_hand * 100.0
        ) / total_hands
        new_pfr = (
            float(row["pfr"]) * old_total + pfr_this_hand * 100.0
        ) / total_hands

        new_three_bet_opp = int(row["three_bet_opportunities"]) + three_bet_opp
        new_three_bet_count = int(row["three_bet_count"]) + three_bet_count
        new_cbet_opp = int(row["cbet_flop_opportunities"]) + cbet_opp
        new_cbet_count = int(row["cbet_flop_count"]) + cbet_count
        new_fold_to_three_bet_opp = (
            int(row["fold_to_three_bet_opportunities"]) + fold_to_three_bet_opp
        )
        new_fold_to_three_bet_count = (
            int(row["fold_to_three_bet_count"]) + fold_to_three_bet_count
        )
        new_wtsd_opp = int(row["wtsd_opportunities"]) + wtsd_opp
        new_wtsd_count = int(row["wtsd_count"]) + wtsd_count

        cursor.execute(
            """
            UPDATE opponents SET
                total_hands = ?,
                last_seen = ?,
                vpip = ?,
                pfr = ?,
                went_to_showdown = ?,
                three_bet_pct = ?,
                cbet_flop_pct = ?,
                fold_to_three_bet = ?,
                three_bet_opportunities = ?,
                three_bet_count = ?,
                cbet_flop_opportunities = ?,
                cbet_flop_count = ?,
                fold_to_three_bet_opportunities = ?,
                fold_to_three_bet_count = ?,
                wtsd_opportunities = ?,
                wtsd_count = ?
            WHERE player_name = ?
            """,
            (
                total_hands,
                timestamp,
                new_vpip,
                new_pfr,
                self._percentage(new_wtsd_count, new_wtsd_opp),
                self._percentage(new_three_bet_count, new_three_bet_opp),
                self._percentage(new_cbet_count, new_cbet_opp),
                self._percentage(
                    new_fold_to_three_bet_count,
                    new_fold_to_three_bet_opp,
                ),
                new_three_bet_opp,
                new_three_bet_count,
                new_cbet_opp,
                new_cbet_count,
                new_fold_to_three_bet_opp,
                new_fold_to_three_bet_count,
                new_wtsd_opp,
                new_wtsd_count,
                row["player_name"],
            ),
        )

    def _calc_three_bet_pct(
        self,
        player_seat: int,
        actions: list[dict[str, Any]],
    ) -> float | None:
        """Return this hand's 3bet result for a player, or None without opportunity."""
        preflop_actions = self._street_action_dicts(actions, "preflop")
        non_blind = [
            action
            for action in preflop_actions
            if self._action_name(action) not in {"BLIND_SB", "BLIND_BB"}
        ]

        first_raise_idx = self._first_aggressive_index(non_blind)
        if first_raise_idx is None:
            return None
        if self._action_seat(non_blind[first_raise_idx]) == player_seat:
            return None

        for action in non_blind[first_raise_idx + 1:]:
            if self._action_seat(action) != player_seat:
                continue
            return 1.0 if self._action_name(action) in {"RAISE", "ALL_IN"} else 0.0
        return None

    def _calc_cbet_flop_pct(
        self,
        player_seat: int,
        actions: list[dict[str, Any]],
    ) -> float | None:
        """Return this hand's flop c-bet result, or None without opportunity."""
        preflop_actions = self._street_action_dicts(actions, "preflop")
        flop_actions = self._street_action_dicts(actions, "flop")
        if not flop_actions:
            return None

        last_aggressor_seat: int | None = None
        for action in preflop_actions:
            if self._action_name(action) in {"RAISE", "ALL_IN"}:
                last_aggressor_seat = self._action_seat(action)

        if last_aggressor_seat is None or last_aggressor_seat != player_seat:
            return None

        for action in flop_actions:
            if self._action_seat(action) != player_seat:
                continue
            return 1.0 if self._action_name(action) in {"BET", "RAISE", "ALL_IN"} else 0.0
        return None

    def _calc_fold_to_three_bet(
        self,
        player_seat: int,
        actions: list[dict[str, Any]],
    ) -> float | None:
        """Return this hand's fold-to-3bet result, or None without opportunity."""
        preflop_actions = self._street_action_dicts(actions, "preflop")
        non_blind = [
            action
            for action in preflop_actions
            if self._action_name(action) not in {"BLIND_SB", "BLIND_BB"}
        ]

        first_raise_idx = self._first_aggressive_index(non_blind)
        if first_raise_idx is None:
            return None
        if self._action_seat(non_blind[first_raise_idx]) != player_seat:
            return None

        after_three_bet = False
        for action in non_blind[first_raise_idx + 1:]:
            action_seat = self._action_seat(action)
            action_name = self._action_name(action)
            if not after_three_bet:
                if action_seat != player_seat and action_name in {"RAISE", "ALL_IN"}:
                    after_three_bet = True
                continue
            if action_seat != player_seat:
                continue
            return 1.0 if action_name == "FOLD" else 0.0
        return None

    def _calc_went_to_showdown(
        self,
        player_seat: int,
        actions: list[dict[str, Any]],
    ) -> float | None:
        """Return this hand's player-specific WTSD result, or None without VPIP."""
        preflop_actions = self._street_action_dicts(actions, "preflop")
        vpip = any(
            self._action_seat(action) == player_seat
            and self._action_name(action) in {"CALL", "BET", "RAISE", "ALL_IN"}
            for action in preflop_actions
        )
        if not vpip:
            return None

        river_actions = self._street_action_dicts(actions, "river")
        if not river_actions:
            return 0.0

        player_folded = any(
            self._action_seat(action) == player_seat
            and self._action_name(action) == "FOLD"
            for action in actions
        )
        return 0.0 if player_folded else 1.0

    @staticmethod
    def _stat_counter(value: float | None) -> tuple[int, int]:
        """Return opportunity/count increments for a hand-level stat value."""
        if value is None:
            return 0, 0
        return 1, 1 if value == 1.0 else 0

    @staticmethod
    def _percentage(count: int, opportunities: int) -> float:
        """Return a 0..100 percentage for count/opportunity counters."""
        if opportunities <= 0:
            return 0.0
        return count / opportunities * 100.0

    @staticmethod
    def _street_action_dicts(
        actions: list[dict[str, Any]],
        street: str,
    ) -> list[dict[str, Any]]:
        """Return action dictionaries for one street."""
        return [action for action in actions if action.get("street") == street]

    @staticmethod
    def _first_aggressive_index(actions: list[dict[str, Any]]) -> int | None:
        """Return index of the first preflop aggressive action."""
        for index, action in enumerate(actions):
            if HandManager._action_name(action) in {"RAISE", "ALL_IN"}:
                return index
        return None

    @staticmethod
    def _action_name(action: dict[str, Any]) -> str:
        """Return an uppercase action name from an action dictionary."""
        return str(action.get("action", "")).upper()

    @staticmethod
    def _action_seat(action: dict[str, Any]) -> int | None:
        """Return an integer seat from an action dictionary."""
        return HandManager._parse_seat(action.get("seat"))

    @staticmethod
    def _parse_seat(value: Any) -> int | None:
        """Parse a seat value into an integer seat number."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def get_opponent_stats(
        self,
        player_or_state: str | GameState | dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return opponent statistics by player name or active GameState seats.

        Args:
            player_or_state: Player-name prefix for a single lookup, or a
                GameState/dict containing players for a seat-keyed stats map.

        Returns:
            Single stats dictionary for a name lookup, a seat-keyed stats map
            for GameState input, or None when a single player is not found.
        """
        if isinstance(player_or_state, str):
            return self._get_opponent_stats_by_name(player_or_state)
        return self._get_opponent_stats_for_game_state(player_or_state)

    def _get_opponent_stats_by_name(self, player_name: str) -> dict[str, Any] | None:
        """Return one opponent statistics row using prefix player-name matching."""
        if self._db_conn is None:
            return None

        try:
            row = self._db_conn.execute(
                "SELECT * FROM opponents WHERE player_name LIKE ? || '%' LIMIT 1",
                (player_name,),
            ).fetchone()
        except sqlite3.Error as exc:
            logger.warning("Failed to fetch opponent stats for %s: %s", player_name, exc)
            return None
        if row is None:
            return None

        stats = dict(row)
        last_seen = stats.get("last_seen")
        if last_seen:
            try:
                last_seen_dt = datetime.fromisoformat(str(last_seen))
                if last_seen_dt.tzinfo is None:
                    last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
                days_since = (datetime.now(timezone.utc) - last_seen_dt).days
                if days_since > 90:
                    stats["freshness_note"] = (
                        f"データ古い（{days_since}日前）、傾向参考程度"
                    )
            except ValueError:
                logger.warning("Invalid last_seen timestamp for %s", player_name)

        total_hands = int(stats.get("total_hands", 0) or 0)
        if total_hands < 10:
            stats["sample_size_note"] = f"サンプル数不足（{total_hands}ハンド）"

        return stats

    def _get_opponent_stats_for_game_state(
        self,
        game_state: GameState | dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Return seat-keyed stats for seated non-hero players in a GameState."""
        if self._db_conn is None:
            return {}

        players = (
            game_state.get("players", {})
            if isinstance(game_state, dict)
            else getattr(game_state, "players", {})
        )
        if not isinstance(players, dict):
            return {}

        stats_by_seat: dict[str, dict[str, Any]] = {}
        for seat_key, player in players.items():
            seat = self._parse_seat(seat_key)
            if seat is None or seat == 1:
                continue
            name = (
                player.get("name")
                if isinstance(player, dict)
                else getattr(player, "name", None)
            )
            if not name:
                continue
            stats = self._get_opponent_stats_by_name(str(name))
            if stats is not None:
                stats_by_seat[str(seat)] = stats
        return stats_by_seat

    def _get_player_actions_in_hand(self, player_name: str) -> list[dict[str, Any]]:
        """Return current-hand actions for a player name."""
        seats = {
            int(seat_key)
            for seat_key, player in self._current_players.items()
            if player.get("name") == player_name
        }
        return [
            action
            for action in self._actions_with_streets()
            if int(action.get("seat", 0)) in seats
        ]

    def _actions_with_streets(self) -> list[dict[str, Any]]:
        """Return all current hand actions with street labels attached."""
        actions: list[dict[str, Any]] = []
        for street_name in ["preflop", "flop", "turn", "river"]:
            street_actions = self._street_actions.get(street_name)
            if street_actions is None:
                continue
            for action in street_actions.actions:
                action_dict = self._action_to_dict(action)
                action_dict["street"] = street_name
                actions.append(action_dict)
        return actions

    def _update_current_players(self, game_state: GameState) -> None:
        """Store latest player metadata and recover delayed participant OCR."""
        for seat_key, player in game_state.players.items():
            existing = self._current_players.get(seat_key, {})
            name = player.name if player.name is not None else existing.get("name")
            self._current_players[seat_key] = {
                "name": name,
                "stack": player.stack,
                "bet": player.bet,
                "is_seated": player.is_seated,
                "cards_visible": player.cards_visible,
                "in_current_hand": player.in_current_hand,
            }
            if (
                seat_key in self._players_in_hand
                and not self._players_in_hand[seat_key]
                and (player.cards_visible or player.bet > 0)
            ):
                logger.debug(
                    "Player recovery skipped: seat=%s cards_visible=%s bet=%d "
                    "folded=%s",
                    seat_key,
                    player.cards_visible,
                    player.bet,
                    seat_key in self._folded_seats,
                )
        self._update_participant_observation(game_state)

    def _participant_action_seats(self, actions: list[ActionRecord]) -> set[str]:
        """Return opponent seats with actions that prove hand participation."""
        return {
            str(action.seat)
            for action in actions
            if action.seat != 1 and action.action.upper() in self._PARTICIPANT_ACTIONS
        }

    def _update_participant_observation(self, game_state: GameState) -> None:
        """Promote observed participants during the hand-start observation window."""
        if not self._participant_observation_active:
            return

        started_at = self._participant_observation_started_at
        now = time.monotonic()
        elapsed = 0.0 if started_at is None else now - started_at
        if elapsed >= self._participant_observation_duration_sec:
            self._participant_observation_active = False
            logger.info(
                "Participant observation ended: players_in_hand=%s",
                dict(self._players_in_hand),
            )
            return

        action_participant_seats = self._participant_action_seats(
            game_state.actions_since_last_frame
        )

        for seat_key, player in game_state.players.items():
            if seat_key in self._folded_seats:
                continue

            reason: str | None = None
            if player.cards_visible:
                reason = "cards_visible"
            elif player.bet > 0:
                reason = "bet"
            elif seat_key in action_participant_seats:
                reason = "action"

            if reason is None:
                continue

            was_in_hand = self._players_in_hand.get(seat_key, False)
            self._participant_observed_seats.add(seat_key)
            self._players_in_hand[seat_key] = True
            self._participated_seats.add(seat_key)
            if not was_in_hand:
                logger.info(
                    "Participant observed during start window: seat=%s reason=%s",
                    seat_key,
                    reason,
                )

    def _save_replay_json(self) -> None:
        """Save the current hand replay JSON."""
        if self._hand_id is None:
            return

        try:
            now = datetime.now(timezone.utc)
            replay_dir = Path(self._replay_dir) / now.strftime("%Y-%m-%d")
            replay_dir.mkdir(parents=True, exist_ok=True)
            replay_path = replay_dir / f"hand_{self._hand_id:06d}.json"

            with open(replay_path, "w", encoding="utf-8") as replay_file:
                json.dump(
                    self._build_replay_json(now),
                    replay_file,
                    indent=2,
                    ensure_ascii=False,
                )

            logger.info("Replay saved: %s", replay_path)
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Failed to save replay for hand %s: %s", self._hand_id, exc)

    def _build_replay_json(self, timestamp: datetime) -> dict[str, Any]:
        """Build a replay JSON dictionary."""
        blind_bb = self._config.get("game", {}).get("blind_bb", 100)
        blind_sb = self._config.get("game", {}).get("blind_sb", 50)

        streets: dict[str, Any] = {}
        for street_name in ["preflop", "flop", "turn", "river"]:
            street_actions = self._street_actions.get(street_name)
            streets[street_name] = self._build_street_replay(
                street_name,
                street_actions,
            )

        seat_to_name = self._db_participant_seat_to_name()
        return {
            "meta": {
                "hand_id": self._hand_id,
                "timestamp": timestamp.isoformat(),
                "table": self._table_id,
                "seat": 1,
                "blinds": [blind_sb, blind_bb],
                "site": "coinpoker",
            },
            "participated_seats": sorted(self._participated_seats),
            "seat_to_name": seat_to_name,
            "db_participant_names": sorted(seat_to_name.values()),
            "streets": streets,
            "result": {
                "outcome": "unknown",
                "profit": None,
                "showdown": self._is_showdown(),
                "opponent_cards": None,
            },
        }

    def _db_participant_seat_to_name(self) -> dict[str, str]:
        """Return named non-hero participants for replay and DB audit output."""
        seat_to_name: dict[str, str] = {}
        for seat_key, player in self._current_players.items():
            if seat_key == "1" or seat_key not in self._participated_seats:
                continue
            name = player.get("name")
            if not name or name == "-":
                continue
            seat_to_name[seat_key] = str(name)
        return seat_to_name

    def _build_street_replay(
        self,
        street_name: str,
        street_actions: StreetActions | None,
    ) -> dict[str, Any] | None:
        """Build replay data for a single street."""
        if street_actions is None:
            return None

        if not street_actions.actions and street_name != "preflop":
            if street_actions.board:
                return {
                    "board": street_actions.board,
                    "spectate_only": True,
                }
            return None

        street_data: dict[str, Any] = {
            "actions_observed": [
                self._action_to_dict(action) for action in street_actions.actions
            ],
        }

        if street_name == "preflop":
            street_data["hole_cards"] = self._hero_cards
        elif street_actions.board:
            street_data["board"] = street_actions.board

        if street_actions.recommendation is not None:
            street_data["recommendation"] = street_actions.recommendation
        if street_actions.human_action is not None:
            street_data["human_action"] = street_actions.human_action
        if street_actions.followed_recommendation is not None:
            street_data["followed_recommendation"] = (
                street_actions.followed_recommendation
            )
        if street_actions.time_to_recommend_ms is not None:
            street_data["time_to_recommend_ms"] = street_actions.time_to_recommend_ms
        if street_actions.latency_breakdown:
            street_data["latency_breakdown"] = street_actions.latency_breakdown
        if street_actions.spectate_only:
            street_data["spectate_only"] = True

        return street_data

    def _action_to_dict(self, action: ActionRecord) -> dict[str, Any]:
        """Convert an ActionRecord to a JSON-ready dictionary."""
        return {
            "seat": action.seat,
            "action": action.action,
            "amount": action.amount,
            "confidence": action.confidence,
        }

    def _collect_board(self) -> list[str]:
        """Collect board cards across postflop streets."""
        board: list[str] = []
        for street_name in ["flop", "turn", "river"]:
            street_actions = self._street_actions.get(street_name)
            if street_actions is None:
                continue
            for card in street_actions.board:
                if card not in board:
                    board.append(card)
        return board

    def _is_showdown(self) -> bool:
        """Return whether this hand appears to have reached showdown."""
        river_actions = self._street_actions.get("river")
        return bool(river_actions and river_actions.actions and not self._hero_folded)
