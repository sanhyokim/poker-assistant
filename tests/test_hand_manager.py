"""Tests for HandManager lifecycle and action history management."""

import json
import logging
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import core.hand_manager as hand_manager_module
from core.game_state import ActionRecord, GameState, PlayerState, create_empty_game_state
from core.hand_manager import HandManager, StreetActions


@pytest.fixture
def tmp_path() -> Path:
    """Return a workspace-local temporary path for this module.

    The default pytest tmp_path can be unavailable in restricted Windows temp
    directories, so these tests use a local scratch directory.
    """
    path = Path(".test_tmp") / f"hand_manager_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def manager(tmp_path: Path) -> HandManager:
    """Return a HandManager with default test config."""
    return HandManager(
        {
            "capture": {"polling_interval_sec": 0.5},
            "game": {"blind_sb": 50, "blind_bb": 100},
            "db": {"path": ":memory:"},
            "replay": {"base_dir": str(tmp_path / "replays")},
        },
    )


def make_state(
    hero_cards: list[str | None] | None = None,
    hero_stack: int | None = 5000,
    hero_bet: int = 0,
    hero_is_my_turn: bool = False,
    board: list[str] | None = None,
    pot: int = 100,
    dealer_seat: int | None = None,
    game_event: str | None = None,
    actions: list[ActionRecord] | None = None,
    players: dict[str, tuple[int | None, int]] | None = None,
    player_cards_visible: set[str] | None = None,
) -> GameState:
    """Create a GameState for HandManager tests.

    Args:
        hero_cards: Hero cards, or None when not visible.
        hero_stack: Hero stack amount.
        hero_bet: Hero bet amount.
        hero_is_my_turn: Whether hero is currently to act.
        board: Board cards.
        pot: Pot value.
        dealer_seat: Dealer button seat.
        game_event: Game event.
        actions: Actions since last frame.
        players: Seat to (stack, bet) mapping.
        player_cards_visible: Seats whose opponent cards are visible.

    Returns:
        Configured GameState.
    """
    state = create_empty_game_state()
    state.hero.cards = hero_cards
    state.hero.stack = hero_stack
    state.hero.bet = hero_bet
    state.hero.is_my_turn = hero_is_my_turn
    state.board = list(board or [])
    state.board_card_count = len(state.board)
    state.pot = pot
    state.dealer_seat = dealer_seat
    state.game_event = game_event
    state.actions_since_last_frame = list(actions or [])

    values = players or {"2": (5000, 0), "3": (5000, 0)}
    visible_seats = player_cards_visible or set()
    for seat_key in ["2", "3", "4", "5", "6"]:
        stack, bet = values.get(seat_key, (None, 0))
        state.players[seat_key] = PlayerState(
            stack=stack,
            bet=bet,
            is_seated=stack is not None,
            cards_visible=seat_key in visible_seats,
            in_current_hand=stack is not None,
        )

    return state


def test_db_connection_allows_worker_thread_access(tmp_path: Path) -> None:
    """SQLite connection can be used from the polling worker thread."""
    manager = HandManager(
        {
            "capture": {"polling_interval_sec": 0.5},
            "game": {"blind_sb": 50, "blind_bb": 100},
            "db": {"path": ":memory:"},
            "replay": {"base_dir": str(tmp_path / "replays")},
        },
    )
    errors: list[BaseException] = []

    def query_from_worker() -> None:
        try:
            assert manager._db_conn is not None
            manager._db_conn.execute("SELECT 1").fetchone()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=query_from_worker)
    thread.start()
    thread.join(timeout=2.0)

    try:
        assert not thread.is_alive()
        assert errors == []
    finally:
        manager.close()


def make_manager(tmp_path: Path, db_path: str | None = ":memory:") -> HandManager:
    """Create a HandManager with isolated persistence paths.

    Args:
        tmp_path: pytest temporary directory.
        db_path: SQLite path override.

    Returns:
        Configured HandManager.
    """
    config = {
        "capture": {"polling_interval_sec": 0.5},
        "game": {"blind_sb": 50, "blind_bb": 100, "table_id": "test_table"},
        "db": {"path": db_path or ":memory:"},
        "replay": {"base_dir": str(tmp_path / "replays"), "retention_days": 30},
    }
    return HandManager(config, db_path=db_path)


def start_hand(manager: HandManager) -> None:
    """Move a manager from waiting to preflop."""
    manager.process_frame(make_state(hero_cards=["Ah", "Kd"]))


def finish_hand_by_pot_decrease(
    manager: HandManager,
    board: list[str] | None = None,
    players: dict[str, tuple[int | None, int]] | None = None,
) -> None:
    """Move an active hand to hand_end with a payout-like pot decrease."""
    manager.process_frame(
        make_state(
            hero_cards=["Ah", "Kd"],
            board=board,
            pot=1200,
            players=players,
        )
    )
    manager.process_frame(
        make_state(
            hero_cards=["Ah", "Kd"],
            board=board,
            pot=0,
            players=players,
        )
    )


def stat_action(seat: int, action: str, street: str, amount: int = 0) -> dict:
    """Return a street-tagged action dictionary for stat helper tests."""
    return {
        "seat": seat,
        "action": action,
        "amount": amount,
        "confidence": "high",
        "street": street,
    }


class TestPhaseTransitions:
    """Tests for lifecycle phase transitions."""

    def test_empty_hero_cards_do_not_start_hand(
        self,
        manager: HandManager,
    ) -> None:
        """An empty hero-card list does not start a hand."""
        manager.process_frame(make_state(hero_cards=[]))
        assert manager.phase == "waiting"
        assert manager.hand_id is None

    def test_partial_hero_cards_do_not_start_hand(
        self,
        manager: HandManager,
    ) -> None:
        """A partial hero-card recognition does not start a hand."""
        manager.process_frame(make_state(hero_cards=["Ac", None]))
        assert manager.phase == "waiting"
        assert manager.hand_id is None

    def test_new_hand_without_hero_cards_does_not_start_hand(
        self,
        manager: HandManager,
    ) -> None:
        """A NEW_HAND event alone does not start a hand without two cards."""
        manager.process_frame(make_state(hero_cards=None, game_event="NEW_HAND"))
        assert manager.phase == "waiting"
        assert manager.hand_id is None

    def test_full_lifecycle_transition(self, manager: HandManager) -> None:
        """waiting -> preflop -> flop -> turn -> river -> hand_end -> waiting."""
        start_hand(manager)
        assert manager.phase == "preflop"
        assert manager.hand_id == 1

        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                board=["2c", "7d", "Ts"],
                game_event="NEW_STREET",
            )
        )
        assert manager.phase == "flop"

        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                board=["2c", "7d", "Ts", "Jc"],
                game_event="NEW_STREET",
            )
        )
        assert manager.phase == "turn"

        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                board=["2c", "7d", "Ts", "Jc", "4h"],
                game_event="NEW_STREET",
            )
        )
        assert manager.phase == "river"

        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                board=["2c", "7d", "Ts", "Jc", "4h"],
                pot=0,
            )
        )
        assert manager.phase == "hand_end"

        manager.process_frame(make_state(game_event="NEW_HAND"))
        assert manager.phase == "waiting"
        assert manager.hand_id is None

    def test_invalid_waiting_to_flop_is_ignored(
        self,
        manager: HandManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A NEW_STREET frame while waiting is ignored with a warning."""
        with caplog.at_level(logging.WARNING):
            manager.process_frame(
                make_state(board=["2c", "7d", "Ts"], game_event="NEW_STREET")
            )

        assert manager.phase == "waiting"
        assert "Invalid transition ignored" in caplog.text

    def test_invalid_new_street_is_ignored(
        self,
        manager: HandManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A preflop NEW_STREET with river board count is ignored."""
        start_hand(manager)

        with caplog.at_level(logging.WARNING):
            manager.process_frame(
                make_state(
                    hero_cards=["Ah", "Kd"],
                    board=["2c", "7d", "Ts", "Jc", "4h"],
                    game_event="NEW_STREET",
                )
            )

        assert manager.phase == "preflop"
        assert "Invalid NEW_STREET ignored" in caplog.text

    @pytest.mark.parametrize(
        "phase,board",
        [
            ("preflop", []),
            ("flop", ["2c", "7d", "Ts"]),
            ("turn", ["2c", "7d", "Ts", "Jc"]),
            ("river", ["2c", "7d", "Ts", "Jc", "4h"]),
        ],
    )
    def test_new_hand_during_active_hand_forces_waiting(
        self,
        manager: HandManager,
        phase: str,
        board: list[str],
    ) -> None:
        """A NEW_HAND event during an active phase ends the current hand."""
        start_hand(manager)
        for street_board in [
            ["2c", "7d", "Ts"],
            ["2c", "7d", "Ts", "Jc"],
            ["2c", "7d", "Ts", "Jc", "4h"],
        ]:
            if manager.phase == phase:
                break
            manager.process_frame(
                make_state(
                    hero_cards=["Ah", "Kd"],
                    board=street_board,
                    game_event="NEW_STREET",
                )
            )

        assert manager.phase == phase

        manager._hand_start_monotonic = 0.0
        manager.process_frame(
            make_state(
                hero_cards=None,
                board=board,
                pot=0,
                game_event="NEW_HAND",
            )
        )

        assert manager.phase == "waiting"
        assert manager.hand_id is None

    def test_new_hand_suppressed_during_cooldown(
        self,
        manager: HandManager,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A NEW_HAND event inside cooldown is ignored and normal processing continues."""
        now = 100.0
        monkeypatch.setattr(hand_manager_module.time, "monotonic", lambda: now)
        start_hand(manager)

        now = 102.0
        state = make_state(hero_cards=["Ah", "Kd"], game_event="NEW_HAND")
        with caplog.at_level(logging.WARNING):
            manager.process_frame(state)

        assert manager.phase == "preflop"
        assert state.game_event is None
        assert "NEW_HAND suppressed" in caplog.text

    def test_new_hand_allowed_after_cooldown(
        self,
        manager: HandManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A NEW_HAND event after cooldown ends the active hand."""
        now = 200.0
        monkeypatch.setattr(hand_manager_module.time, "monotonic", lambda: now)
        start_hand(manager)

        now = 206.0
        manager.process_frame(make_state(hero_cards=None, pot=0, game_event="NEW_HAND"))

        assert manager.phase == "waiting"
        assert manager.hand_id is None

    def test_new_hand_cooldown_cleared_on_reset(self, manager: HandManager) -> None:
        """Reset clears the NEW_HAND cooldown timer."""
        start_hand(manager)
        assert manager._hand_start_monotonic is not None

        manager.reset()

        assert manager._hand_start_monotonic is None


class TestHandEndConditions:
    """Tests for active hand to hand_end transition conditions."""

    def test_hero_cards_missing_five_frames(self, manager: HandManager) -> None:
        """Five consecutive missing hero-card frames end the hand."""
        start_hand(manager)

        for _ in range(5):
            manager.process_frame(make_state(hero_cards=None))

        assert manager.phase == "hand_end"

    def test_hero_fold_keeps_table_hand_active(self, manager: HandManager) -> None:
        """A seat 1 FOLD action marks hero folded without ending the table hand."""
        start_hand(manager)
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                actions=[ActionRecord(seat=1, action="FOLD", amount=0)],
            )
        )

        assert manager.phase == "preflop"
        assert manager.hero_folded is True
        assert 1 not in manager.get_players_in_hand()

    def test_hero_folded_missing_cards_do_not_end_hand(self, manager: HandManager) -> None:
        """Hero card disappearance after hero fold is not a table hand-end signal."""
        start_hand(manager)
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                actions=[ActionRecord(seat=1, action="FOLD", amount=0)],
            )
        )

        for _ in range(5):
            manager.process_frame(make_state(hero_cards=None))

        assert manager.phase == "preflop"
        assert manager._hero_card_missing_count == 0

    def test_hero_folded_pot_decrease_ends_hand(self, manager: HandManager) -> None:
        """A pot decrease still ends the table hand after hero folded."""
        start_hand(manager)
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                pot=1200,
                actions=[ActionRecord(seat=1, action="FOLD", amount=0)],
            )
        )

        manager.process_frame(make_state(hero_cards=None, pot=0))

        assert manager.phase == "hand_end"

    def test_showdown_stable_pot_ends_hand(self, manager: HandManager) -> None:
        """A five-card board with stable pot for 10 frames ends the hand."""
        start_hand(manager)
        for board in [
            ["2c", "7d", "Ts"],
            ["2c", "7d", "Ts", "Jc"],
            ["2c", "7d", "Ts", "Jc", "4h"],
        ]:
            manager.process_frame(
                make_state(hero_cards=["Ah", "Kd"], board=board, game_event="NEW_STREET")
            )

        for _ in range(11):
            manager.process_frame(
                make_state(
                    hero_cards=["Ah", "Kd"],
                    board=["2c", "7d", "Ts", "Jc", "4h"],
                    pot=1200,
                )
            )

        assert manager.phase == "hand_end"

    def test_pot_decrease_ends_hand(self, manager: HandManager) -> None:
        """A pot decrease during an active hand ends the hand."""
        start_hand(manager)
        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], pot=1200))

        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], pot=0))

        assert manager.phase == "hand_end"

    def test_pot_same_zero_does_not_end_hand(self, manager: HandManager) -> None:
        """Repeated zero pot values do not trigger pot-decrease hand end."""
        start_hand(manager)
        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], pot=0))

        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], pot=0))

        assert manager.phase == "preflop"

    def test_pot_increase_does_not_end_hand(self, manager: HandManager) -> None:
        """Pot increases during an active hand do not end the hand."""
        start_hand(manager)
        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], pot=100))

        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], pot=300))

        assert manager.phase == "preflop"

    def test_first_tracked_pot_does_not_end_hand(self, manager: HandManager) -> None:
        """The first active frame with no previous pot does not end the hand."""
        start_hand(manager)
        assert manager._prev_frame_pot is None

        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], pot=0))

        assert manager.phase == "preflop"
        assert manager._prev_frame_pot == 0


class TestPlayersInHandStartModel:
    """Tests for participants captured when a new hand starts."""

    def test_stack_only_player_is_not_in_hand(self, manager: HandManager) -> None:
        """A visible stack alone does not make a seat a hand participant."""
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                players={"2": (5000, 0)},
            )
        )

        assert manager.get_players_in_hand() == {1}

    def test_cards_visible_player_is_in_hand(self, manager: HandManager) -> None:
        """A visible seat-card region makes a seat a hand participant."""
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                players={"2": (5000, 0)},
                player_cards_visible={"2"},
            )
        )

        assert manager.get_players_in_hand() == {1, 2}

    def test_player_with_bet_is_in_hand(self, manager: HandManager) -> None:
        """A seat with a live bet is a hand participant."""
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                players={"2": (4900, 100)},
            )
        )

        assert manager.get_players_in_hand() == {1, 2}

    @pytest.mark.parametrize("action", ["BET", "CALL", "RAISE", "ALL_IN"])
    def test_action_participant_is_in_hand(
        self,
        manager: HandManager,
        action: str,
    ) -> None:
        """A non-passive action on the hand-start frame confirms participation."""
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                actions=[ActionRecord(seat=4, action=action, amount=100)],
                players={"4": (5000, 0)},
            )
        )

        assert 4 in manager.get_players_in_hand()


class TestPlayersInHandRecovery:
    """Tests for delayed participant recovery from card or bet visibility."""

    def test_late_stack_only_does_not_recover_player(
        self,
        manager: HandManager,
    ) -> None:
        """A stack becoming visible alone does not recover a hand participant."""
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                players={"2": (5000, 0), "4": (None, 0)},
                player_cards_visible={"2"},
            )
        )
        assert 4 not in manager.get_players_in_hand()

        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                players={"2": (5000, 0), "4": (5000, 0)},
                player_cards_visible={"2"},
            )
        )

        assert 4 not in manager.get_players_in_hand()

    def test_late_cards_visible_recovery_marks_player_in_hand(
        self,
        manager: HandManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A missed seat recovers when cards become visible later."""
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                players={"2": (5000, 0), "3": (5000, 0), "4": (None, 0)},
                player_cards_visible={"2", "3"},
            )
        )
        assert 4 not in manager.get_players_in_hand()

        with caplog.at_level(logging.INFO):
            manager.process_frame(
                make_state(
                    hero_cards=["Ah", "Kd"],
                    players={"2": (5000, 0), "3": (5000, 0), "4": (5000, 0)},
                    player_cards_visible={"2", "3", "4"},
                )
            )

        assert 4 in manager.get_players_in_hand()
        assert "Player late-recovered: seat=4 cards_visible=True bet=0" in caplog.text

    def test_late_bet_recovery_marks_player_in_hand(
        self,
        manager: HandManager,
    ) -> None:
        """A missed seat recovers when a live bet appears later."""
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                players={"2": (5000, 0), "4": (None, 0)},
                player_cards_visible={"2"},
            )
        )

        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                players={"2": (5000, 0), "4": (4900, 100)},
                player_cards_visible={"2"},
            )
        )

        assert 4 in manager.get_players_in_hand()

    def test_folded_player_is_not_late_recovered(self, manager: HandManager) -> None:
        """A seat explicitly folded does not recover just because stack is visible."""
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                players={"2": (5000, 0), "3": (5000, 0), "4": (5000, 0)},
                player_cards_visible={"2", "3", "4"},
            )
        )
        assert 4 in manager.get_players_in_hand()

        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                actions=[ActionRecord(seat=4, action="FOLD", amount=0)],
                players={"2": (5000, 0), "3": (5000, 0), "4": (5000, 0)},
                player_cards_visible={"2", "3", "4"},
            )
        )
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                players={"2": (5000, 0), "3": (5000, 0), "4": (4900, 100)},
                player_cards_visible={"2", "3", "4"},
            )
        )

        assert 4 not in manager.get_players_in_hand()
        assert "4" in manager._folded_seats

    def test_folded_seats_cleared_on_reset(self, manager: HandManager) -> None:
        """Hand reset clears folded-seat recovery guards."""
        manager.process_frame(make_state(hero_cards=["Ah", "Kd"]))
        manager._players_in_hand["4"] = True
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                actions=[ActionRecord(seat=4, action="FOLD", amount=0)],
                players={"2": (5000, 0), "3": (5000, 0), "4": (5000, 0)},
            )
        )
        assert manager._folded_seats == {"4"}

        manager.reset()

        assert manager._folded_seats == set()


class TestWaitingTransition:
    """Tests for hand_end to waiting transitions."""

    def test_new_hand_event_moves_hand_end_to_waiting(
        self,
        manager: HandManager,
    ) -> None:
        """NEW_HAND event moves hand_end to waiting."""
        start_hand(manager)
        finish_hand_by_pot_decrease(manager)

        manager.process_frame(make_state(game_event="NEW_HAND"))

        assert manager.phase == "waiting"
        assert manager.hand_id is None

    def test_timeout_moves_hand_end_to_waiting(
        self,
        manager: HandManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A 10-second timeout moves hand_end to waiting."""
        current_time = 100.0
        monkeypatch.setattr(hand_manager_module.time, "monotonic", lambda: current_time)

        start_hand(manager)
        finish_hand_by_pot_decrease(manager)
        assert manager.phase == "hand_end"

        current_time = 111.0
        manager.process_frame(make_state())

        assert manager.phase == "waiting"


class TestActionAccumulation:
    """Tests for action accumulation and street buckets."""

    def test_get_players_in_hand_excludes_folded_seat(
        self,
        manager: HandManager,
    ) -> None:
        """Folded seats are removed from the active hand participant set."""
        manager._phase = "preflop"
        manager._players_in_hand = {"1": True, "2": True, "3": True}

        manager._add_actions([ActionRecord(seat=3, action="FOLD", amount=0)])

        assert manager.get_players_in_hand() == {1, 2}

    def test_fold_updates_players_in_hand(self, manager: HandManager) -> None:
        """FOLD actions remove each folded seat from _players_in_hand."""
        manager._phase = "flop"
        manager._players_in_hand = {
            "1": True,
            "2": True,
            "3": True,
            "4": True,
            "5": True,
        }

        manager._add_actions(
            [
                ActionRecord(seat=2, action="FOLD", amount=0),
                ActionRecord(seat=3, action="FOLD", amount=0),
                ActionRecord(seat=4, action="FOLD", amount=0),
            ]
        )

        assert manager.get_players_in_hand() == {1, 5}

    def test_fold_reduces_active_count_via_get_players_in_hand(
        self,
        manager: HandManager,
    ) -> None:
        """get_players_in_hand returns the reduced participant set after folds."""
        manager._phase = "turn"
        manager._players_in_hand = {
            "1": True,
            "2": True,
            "3": True,
            "4": True,
            "5": True,
        }

        manager._add_actions(
            [
                ActionRecord(seat=2, action="FOLD", amount=0),
                ActionRecord(seat=3, action="FOLD", amount=0),
                ActionRecord(seat=4, action="FOLD", amount=0),
            ]
        )

        assert len(manager.get_players_in_hand()) == 2

    def test_get_players_in_hand_keeps_all_in_seat(
        self,
        manager: HandManager,
    ) -> None:
        """All-in seats remain active because they still contest the pot."""
        manager._phase = "preflop"
        manager._players_in_hand = {"1": True, "2": True}

        manager._add_actions([ActionRecord(seat=2, action="ALL_IN", amount=5000)])

        assert manager.get_players_in_hand() == {1, 2}

    def test_actions_are_available_after_process_frame(
        self,
        manager: HandManager,
    ) -> None:
        """get_all_actions and current street actions expose accumulated data."""
        start_hand(manager)
        action = ActionRecord(seat=2, action="CALL", amount=100)

        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], actions=[action]))

        assert manager.get_all_actions() == [action]
        current_street = manager.get_current_street_actions()
        assert current_street is not None
        assert current_street.actions == [action]

    def test_duplicate_same_amount_is_ignored(self, manager: HandManager) -> None:
        """Same seat/action/amount in consecutive frames is deduplicated."""
        start_hand(manager)
        action = ActionRecord(seat=2, action="CALL", amount=100)

        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], actions=[action]))
        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], actions=[action]))

        assert manager.get_all_actions() == [action]

    def test_duplicate_within_five_percent_is_ignored(
        self,
        manager: HandManager,
    ) -> None:
        """Same action with amount within 5 percent is deduplicated."""
        start_hand(manager)
        action = ActionRecord(seat=2, action="BET", amount=100)
        near_duplicate = ActionRecord(seat=2, action="BET", amount=104)

        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], actions=[action]))
        manager.process_frame(
            make_state(hero_cards=["Ah", "Kd"], actions=[near_duplicate])
        )

        assert manager.get_all_actions() == [action]

    def test_different_action_same_seat_is_kept(self, manager: HandManager) -> None:
        """Different action types from the same seat are separate actions."""
        start_hand(manager)
        bet = ActionRecord(seat=2, action="BET", amount=100)
        raise_action = ActionRecord(seat=2, action="RAISE", amount=300)

        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], actions=[bet]))
        manager.process_frame(
            make_state(hero_cards=["Ah", "Kd"], actions=[raise_action])
        )

        assert manager.get_all_actions() == [bet, raise_action]

    def test_zero_amount_duplicate_is_ignored(self, manager: HandManager) -> None:
        """Repeated zero-amount actions are deduplicated."""
        start_hand(manager)
        check = ActionRecord(seat=2, action="CHECK", amount=0)

        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], actions=[check]))
        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], actions=[check]))

        assert manager.get_all_actions() == [check]

    def test_street_actions_are_split_by_phase(self, manager: HandManager) -> None:
        """Preflop and flop actions are stored in separate street buckets."""
        start_hand(manager)
        preflop_action = ActionRecord(seat=2, action="CALL", amount=100)
        flop_action = ActionRecord(seat=1, action="BET", amount=200)

        manager.process_frame(
            make_state(hero_cards=["Ah", "Kd"], actions=[preflop_action])
        )
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                board=["2c", "7d", "Ts"],
                game_event="NEW_STREET",
            )
        )
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                board=["2c", "7d", "Ts"],
                actions=[flop_action],
            )
        )

        summary = manager.get_hand_summary()
        assert summary is not None
        assert summary["streets"]["preflop"]["actions"][0]["action"] == "CALL"
        assert summary["streets"]["flop"]["actions"][0]["action"] == "BET"


class TestHeroTurnActions:
    """Tests for hero turn boundary detection and action recording."""

    def test_turn_boundary_states_are_saved(self, manager: HandManager) -> None:
        """False -> True -> False stores turn start and end snapshots."""
        start_hand(manager)

        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                hero_stack=4700,
                hero_bet=0,
                hero_is_my_turn=True,
            )
        )
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                hero_stack=4700,
                hero_bet=0,
                hero_is_my_turn=False,
            )
        )

        assert manager._turn_start_state is not None
        assert manager._turn_end_state is not None
        assert manager._turn_start_state.hero.is_my_turn is True
        assert manager._turn_end_state.hero.is_my_turn is False

    def test_hero_check_is_recorded(self, manager: HandManager) -> None:
        """No stack/bet change and visible cards records CHECK."""
        start_hand(manager)
        self._run_hero_turn(
            manager,
            start_stack=4700,
            end_stack=4700,
            start_bet=0,
            end_bet=0,
        )

        current_street = manager.get_current_street_actions()
        assert current_street is not None
        assert current_street.human_action == "CHECK"
        assert manager.get_all_actions()[-1].action == "CHECK"

    def test_hero_fold_is_recorded(self, manager: HandManager) -> None:
        """No stack/bet change and missing cards records FOLD."""
        start_hand(manager)
        self._run_hero_turn(
            manager,
            start_stack=4700,
            end_stack=4700,
            start_bet=0,
            end_bet=0,
            end_cards_visible=False,
        )

        assert manager._hero_folded is True
        assert manager.get_all_actions()[-1].action == "FOLD"

    def test_hero_call_is_recorded(self, manager: HandManager) -> None:
        """Stack decrease and bet up to max bet records CALL."""
        start_hand(manager)
        self._run_hero_turn(
            manager,
            start_stack=4700,
            end_stack=4600,
            start_bet=0,
            end_bet=100,
            start_players={"2": (4900, 100), "3": (5000, 0)},
            end_players={"2": (4900, 100), "3": (5000, 0)},
        )

        assert manager.get_all_actions()[-1].action == "CALL"
        assert manager.get_all_actions()[-1].amount == 100

    def test_hero_bet_is_recorded(self, manager: HandManager) -> None:
        """Stack decrease and no previous max bet records BET."""
        start_hand(manager)
        self._run_hero_turn(
            manager,
            start_stack=4700,
            end_stack=4500,
            start_bet=0,
            end_bet=200,
        )

        assert manager.get_all_actions()[-1].action == "BET"
        assert manager.get_all_actions()[-1].amount == 200

    def test_hero_raise_is_recorded(self, manager: HandManager) -> None:
        """Bet above max bet times 1.1 records RAISE."""
        start_hand(manager)
        self._run_hero_turn(
            manager,
            start_stack=4700,
            end_stack=4400,
            start_bet=0,
            end_bet=300,
            start_players={"2": (4900, 100), "3": (5000, 0)},
            end_players={"2": (4900, 100), "3": (5000, 0)},
        )

        assert manager.get_all_actions()[-1].action == "RAISE"
        assert manager.get_all_actions()[-1].amount == 300

    def test_hero_all_in_is_recorded(self, manager: HandManager) -> None:
        """Hero stack reaching zero records ALL_IN."""
        start_hand(manager)
        self._run_hero_turn(
            manager,
            start_stack=500,
            end_stack=0,
            start_bet=0,
            end_bet=500,
        )

        assert manager.get_all_actions()[-1].action == "ALL_IN"
        assert manager.get_all_actions()[-1].amount == 500

    def test_recommendation_followed_is_recorded(
        self,
        manager: HandManager,
    ) -> None:
        """set_recommendation records whether hero followed the action type."""
        start_hand(manager)
        manager.set_recommendation("BET 200")

        self._run_hero_turn(
            manager,
            start_stack=4700,
            end_stack=4500,
            start_bet=0,
            end_bet=200,
        )

        current_street = manager.get_current_street_actions()
        assert current_street is not None
        assert current_street.followed_recommendation is True

    def _run_hero_turn(
        self,
        manager: HandManager,
        start_stack: int,
        end_stack: int,
        start_bet: int,
        end_bet: int,
        start_players: dict[str, tuple[int | None, int]] | None = None,
        end_players: dict[str, tuple[int | None, int]] | None = None,
        end_cards_visible: bool = True,
    ) -> None:
        """Run one hero turn from start to end frame."""
        end_cards = ["Ah", "Kd"] if end_cards_visible else None
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                hero_stack=start_stack,
                hero_bet=start_bet,
                hero_is_my_turn=True,
                players=start_players,
            )
        )
        manager.process_frame(
            make_state(
                hero_cards=end_cards,
                hero_stack=end_stack,
                hero_bet=end_bet,
                hero_is_my_turn=False,
                players=end_players,
            )
        )


class TestBlindRecording:
    """Tests for blind recording during NEW_HAND initialization."""

    def test_blinds_recorded_for_three_players(self, tmp_path: Path) -> None:
        """Dealer seat 1 records seats 2 and 3 as SB and BB."""
        manager = make_manager(tmp_path)
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                dealer_seat=1,
                players={"2": (4950, 50), "3": (4900, 100)},
            )
        )

        assert [(a.seat, a.action, a.amount) for a in manager.get_all_actions()] == [
            (2, "BLIND_SB", 50),
            (3, "BLIND_BB", 100),
        ]

    def test_blinds_recorded_for_heads_up(self, tmp_path: Path) -> None:
        """Heads-up dealer is SB and the other active seat is BB."""
        manager = make_manager(tmp_path)
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                hero_bet=50,
                dealer_seat=1,
                players={"2": (4900, 100)},
            )
        )

        assert [(a.seat, a.action, a.amount) for a in manager.get_all_actions()] == [
            (1, "BLIND_SB", 50),
            (2, "BLIND_BB", 100),
        ]

    def test_dealer_missing_does_not_record_blinds(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Missing dealer seat leaves blinds unrecorded and logs a warning."""
        manager = make_manager(tmp_path)

        with caplog.at_level(logging.WARNING):
            manager.process_frame(
                make_state(
                    hero_cards=["Ah", "Kd"],
                    players={"2": (4950, 50), "3": (4900, 100)},
                )
            )

        assert manager.get_all_actions() == []
        assert "Cannot record blinds" in caplog.text


class TestNewHandInitialization:
    """Tests for NEW_HAND initialization details."""

    def test_new_hand_initializes_internal_state(self, tmp_path: Path) -> None:
        """New hand clears turn snapshots, counters, and fold flags."""
        manager = make_manager(tmp_path)
        manager._turn_start_state = make_state(hero_cards=["Qs", "Qd"])
        manager._turn_end_state = make_state(hero_cards=["Qs", "Qd"])
        manager._hero_card_missing_count = 4
        manager._showdown_stable_count = 9
        manager._last_pot_at_showdown = 1000
        manager._hero_folded = True
        manager._last_hero_action = ActionRecord(seat=1, action="FOLD")

        manager.process_frame(make_state(hero_cards=["Ah", "Kd"]))

        assert manager.phase == "preflop"
        assert manager._turn_start_state is None
        assert manager._turn_end_state is None
        assert manager._prev_is_my_turn is False
        assert manager._last_hero_action is None
        assert manager._hero_card_missing_count == 0
        assert manager._showdown_stable_count == 0
        assert manager._last_pot_at_showdown is None
        assert manager._hero_folded is False


class TestPersistence:
    """Tests for DB and replay persistence at hand end."""

    def test_db_initialization_creates_tables(self, tmp_path: Path) -> None:
        """HandManager creates opponents and hand_history tables."""
        db_path = tmp_path / "history.db"
        manager = make_manager(tmp_path, db_path=str(db_path))

        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()

        manager.close()
        assert {"opponents", "hand_history"}.issubset({row[0] for row in rows})

    def test_hand_end_saves_to_db(self, tmp_path: Path) -> None:
        """hand_end inserts hand history with JSON fields."""
        db_path = tmp_path / "history.db"
        manager = make_manager(tmp_path, db_path=str(db_path))
        start_hand(manager)
        action = ActionRecord(seat=2, action="CALL", amount=100)
        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], actions=[action]))
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                board=["2c", "7d", "Ts"],
                game_event="NEW_STREET",
            )
        )
        finish_hand_by_pot_decrease(manager, board=["2c", "7d", "Ts"])
        manager.close()

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT hole_cards, actions, board FROM hand_history"
            ).fetchone()

        assert json.loads(row[0]) == ["Ah", "Kd"]
        assert json.loads(row[1])[0]["action"] == "CALL"
        assert json.loads(row[2]) == ["2c", "7d", "Ts"]

    def test_hand_end_inserts_opponent_stats(self, tmp_path: Path) -> None:
        """hand_end inserts a new opponent row with VPIP/PFR statistics."""
        db_path = tmp_path / "history.db"
        manager = make_manager(tmp_path, db_path=str(db_path))
        start_state = make_state(hero_cards=["Ah", "Kd"])
        start_state.players["2"].name = "Alice"
        manager.process_frame(start_state)
        action = ActionRecord(seat=2, action="CALL", amount=100)
        action_state = make_state(hero_cards=["Ah", "Kd"], actions=[action])
        action_state.players["2"].name = "Alice"
        manager.process_frame(action_state)
        finish_hand_by_pot_decrease(manager)

        stats = manager.get_opponent_stats("Alice")
        manager.close()

        assert stats is not None
        assert stats["player_name"] == "Alice"
        assert stats["total_hands"] == 1
        assert stats["vpip"] == 100.0
        assert stats["pfr"] == 0.0
        assert "sample_size_note" in stats

    def test_hand_end_updates_existing_opponent_by_prefix(self, tmp_path: Path) -> None:
        """Existing opponents are matched by prefix and updated incrementally."""
        db_path = tmp_path / "history.db"
        manager = make_manager(tmp_path, db_path=str(db_path))
        assert manager._db_conn is not None
        manager._db_conn.execute(
            """
            INSERT INTO opponents
                (player_name, total_hands, first_seen, last_seen, vpip, pfr)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "AliceLong",
                1,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
                0.0,
                0.0,
            ),
        )
        manager._db_conn.commit()

        start_state = make_state(hero_cards=["Ah", "Kd"])
        start_state.players["2"].name = "Alice"
        manager.process_frame(start_state)
        raise_state = make_state(
            hero_cards=["Ah", "Kd"],
            actions=[ActionRecord(seat=2, action="RAISE", amount=300)],
        )
        raise_state.players["2"].name = "Alice"
        manager.process_frame(raise_state)
        finish_hand_by_pot_decrease(manager)

        stats = manager.get_opponent_stats("Alice")
        manager.close()

        assert stats is not None
        assert stats["player_name"] == "AliceLong"
        assert stats["total_hands"] == 2
        assert stats["vpip"] == 50.0
        assert stats["pfr"] == 50.0

    def test_get_opponent_stats_adds_freshness_note(self, tmp_path: Path) -> None:
        """Stats older than 90 days include a freshness note."""
        manager = make_manager(tmp_path)
        assert manager._db_conn is not None
        old_seen = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
        manager._db_conn.execute(
            """
            INSERT INTO opponents
                (player_name, total_hands, first_seen, last_seen)
            VALUES (?, ?, ?, ?)
            """,
            ("Bob", 20, old_seen, old_seen),
        )
        manager._db_conn.commit()

        stats = manager.get_opponent_stats("Bo")
        manager.close()

        assert stats is not None
        assert "freshness_note" in stats
        assert "データ古い" in stats["freshness_note"]

    def test_get_opponent_stats_not_found(self, tmp_path: Path) -> None:
        """Unknown players return None."""
        manager = make_manager(tmp_path)

        stats = manager.get_opponent_stats("Unknown")
        manager.close()

        assert stats is None

    def test_active_new_hand_forced_end_persists(self, tmp_path: Path) -> None:
        """A NEW_HAND boundary during an active hand persists the old hand."""
        db_path = tmp_path / "history.db"
        manager = make_manager(tmp_path, db_path=str(db_path))
        start_hand(manager)
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                actions=[ActionRecord(seat=2, action="CALL", amount=100)],
            )
        )

        manager._hand_start_monotonic = 0.0
        manager.process_frame(make_state(hero_cards=None, pot=0, game_event="NEW_HAND"))
        manager.close()

        with sqlite3.connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM hand_history").fetchone()[0]
        replay_files = list((tmp_path / "replays").glob("*/hand_000001.json"))

        assert count == 1
        assert len(replay_files) == 1
        assert manager.phase == "waiting"

    def test_db_connection_none_reconnects_and_saves(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Missing DB connection is re-established before saving."""
        db_path = tmp_path / "hands.db"
        manager = make_manager(tmp_path, db_path=str(db_path))
        start_hand(manager)
        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], pot=1200))
        manager.close()
        manager._db_conn = None

        with caplog.at_level(logging.INFO):
            manager.process_frame(make_state(hero_cards=["Ah", "Kd"], pot=0))

        assert "Database reconnected" in caplog.text
        assert "DB connection not available" not in caplog.text
        manager.close()
        with sqlite3.connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM hand_history").fetchone()[0]
        assert count == 1

    def test_replay_json_saved_on_hand_end(self, tmp_path: Path) -> None:
        """hand_end writes hand_NNNNNN.json under dated replay directory."""
        manager = make_manager(tmp_path)
        start_hand(manager)
        finish_hand_by_pot_decrease(manager)

        replay_files = list((tmp_path / "replays").glob("* /hand_000001.json"))
        if not replay_files:
            replay_files = list((tmp_path / "replays").glob("*/hand_000001.json"))

        assert len(replay_files) == 1

    def test_replay_json_schema(self, tmp_path: Path) -> None:
        """Replay JSON contains meta, streets, and result schema keys."""
        manager = make_manager(tmp_path)
        start_hand(manager)
        finish_hand_by_pot_decrease(manager)
        replay_path = next((tmp_path / "replays").glob("*/hand_000001.json"))
        replay = json.loads(replay_path.read_text(encoding="utf-8"))

        assert set(replay.keys()) == {"meta", "streets", "result"}
        assert {"hand_id", "timestamp", "table", "seat", "blinds", "site"}.issubset(
            replay["meta"].keys()
        )

    def test_replay_streets_preflop_and_flop(self, tmp_path: Path) -> None:
        """Preflop and flop actions are saved while turn/river stay null."""
        manager = make_manager(tmp_path)
        start_hand(manager)
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                actions=[ActionRecord(seat=2, action="CALL", amount=100)],
            )
        )
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                board=["2c", "7d", "Ts"],
                game_event="NEW_STREET",
            )
        )
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                board=["2c", "7d", "Ts"],
                actions=[ActionRecord(seat=1, action="BET", amount=200)],
            )
        )
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                board=["2c", "7d", "Ts"],
                actions=[ActionRecord(seat=1, action="FOLD", amount=0)],
            )
        )
        finish_hand_by_pot_decrease(manager, board=["2c", "7d", "Ts"])
        replay_path = next((tmp_path / "replays").glob("*/hand_000001.json"))
        replay = json.loads(replay_path.read_text(encoding="utf-8"))

        assert replay["streets"]["preflop"]["actions_observed"][0]["action"] == "CALL"
        assert replay["streets"]["flop"]["actions_observed"][0]["action"] == "BET"
        assert replay["streets"]["turn"] is None
        assert replay["streets"]["river"] is None

    def test_replay_spectate_only_street(self, tmp_path: Path) -> None:
        """A board-only street after hero fold is written as spectate_only."""
        manager = make_manager(tmp_path)
        start_hand(manager)
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                board=["2c", "7d", "Ts"],
                game_event="NEW_STREET",
            )
        )
        manager.get_current_street_actions().spectate_only = True  # type: ignore[union-attr]
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                actions=[ActionRecord(seat=1, action="FOLD", amount=0)],
            )
        )
        manager._street_actions["turn"].board = ["2c", "7d", "Ts", "Jc"]
        manager._save_replay_json()

        replay_path = next((tmp_path / "replays").glob("*/hand_000001.json"))
        replay = json.loads(replay_path.read_text(encoding="utf-8"))

        assert replay["streets"]["turn"]["spectate_only"] is True
        assert replay["streets"]["turn"]["board"] == ["2c", "7d", "Ts", "Jc"]

    def test_replay_save_error_is_logged(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Replay save errors are logged and do not raise."""
        blocked_path = tmp_path / "blocked"
        blocked_path.write_text("not a directory", encoding="utf-8")
        manager = make_manager(tmp_path)
        manager._replay_dir = str(blocked_path)
        start_hand(manager)
        manager.process_frame(make_state(hero_cards=["Ah", "Kd"], pot=1200))

        with caplog.at_level(logging.ERROR):
            manager.process_frame(make_state(hero_cards=["Ah", "Kd"], pot=0))

        assert "Failed to save replay" in caplog.text

    def test_close_sets_db_connection_none(self, tmp_path: Path) -> None:
        """close() closes and clears the DB connection."""
        manager = make_manager(tmp_path)

        manager.close()

        assert manager._db_conn is None

    def test_cleanup_old_replays_removes_old_directories(
        self,
        tmp_path: Path,
    ) -> None:
        """Replay directories older than retention_days are removed."""
        replay_dir = tmp_path / "replays"
        old_dir = replay_dir / (datetime.now() - timedelta(days=31)).strftime(
            "%Y-%m-%d"
        )
        recent_dir = replay_dir / (datetime.now() - timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        old_dir.mkdir(parents=True)
        recent_dir.mkdir(parents=True)
        (old_dir / "hand_000001.json").write_text("{}", encoding="utf-8")
        (recent_dir / "hand_000002.json").write_text("{}", encoding="utf-8")
        manager = make_manager(tmp_path)

        manager.close()

        assert old_dir.exists() is False
        assert recent_dir.exists() is True

    def test_cleanup_old_replays_keeps_recent_directories(
        self,
        tmp_path: Path,
    ) -> None:
        """Replay directories within retention_days are kept."""
        replay_dir = tmp_path / "replays"
        recent_dir = replay_dir / (datetime.now() - timedelta(days=5)).strftime(
            "%Y-%m-%d"
        )
        recent_dir.mkdir(parents=True)
        (recent_dir / "hand_000001.json").write_text("{}", encoding="utf-8")
        manager = make_manager(tmp_path)

        manager.close()

        assert recent_dir.exists() is True

    def test_cleanup_old_replays_handles_missing_base_dir(
        self,
        tmp_path: Path,
    ) -> None:
        """Missing replay base_dir does not raise during close."""
        manager = make_manager(tmp_path)
        manager._replay_dir = str(tmp_path / "missing_replays")

        manager.close()

        assert manager._db_conn is None

    def test_cleanup_old_replays_ignores_non_date_directories(
        self,
        tmp_path: Path,
    ) -> None:
        """Replay cleanup skips directories that are not YYYY-MM-DD."""
        replay_dir = tmp_path / "replays"
        non_date_dir = replay_dir / "manual_exports"
        non_date_dir.mkdir(parents=True)
        (non_date_dir / "keep.json").write_text("{}", encoding="utf-8")
        manager = make_manager(tmp_path)

        manager.close()

        assert non_date_dir.exists() is True

    def test_street_transition_records_board(self, tmp_path: Path) -> None:
        """StreetActions.board is updated when transitioning to flop."""
        manager = make_manager(tmp_path)
        start_hand(manager)
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                board=["2c", "7d", "Ts"],
                game_event="NEW_STREET",
            )
        )

        assert manager._street_actions["flop"].board == ["2c", "7d", "Ts"]

    def test_full_hand_persists_db_and_replay(self, tmp_path: Path) -> None:
        """Complete hand lifecycle persists to DB and replay JSON."""
        db_path = tmp_path / "history.db"
        manager = make_manager(tmp_path, db_path=str(db_path))
        start_hand(manager)
        for board in [
            ["2c", "7d", "Ts"],
            ["2c", "7d", "Ts", "Jc"],
            ["2c", "7d", "Ts", "Jc", "4h"],
        ]:
            manager.process_frame(
                make_state(hero_cards=["Ah", "Kd"], board=board, game_event="NEW_STREET")
            )
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                board=["2c", "7d", "Ts", "Jc", "4h"],
                actions=[ActionRecord(seat=1, action="BET", amount=200)],
            )
        )
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                board=["2c", "7d", "Ts", "Jc", "4h"],
                actions=[ActionRecord(seat=1, action="FOLD", amount=0)],
            )
        )
        finish_hand_by_pot_decrease(manager, board=["2c", "7d", "Ts", "Jc", "4h"])
        manager.process_frame(make_state(game_event="NEW_HAND"))
        manager.close()

        with sqlite3.connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM hand_history").fetchone()[0]
        replay_files = list((tmp_path / "replays").glob("*/hand_000001.json"))

        assert count == 1
        assert len(replay_files) == 1
        assert manager.phase == "waiting"


class TestHandIdAndReset:
    """Tests for hand ID and reset behavior."""

    def test_hand_id_increments_across_hands(self, manager: HandManager) -> None:
        """Two consecutive hands receive incrementing hand IDs."""
        start_hand(manager)
        assert manager.hand_id == 1

        finish_hand_by_pot_decrease(manager)
        manager.process_frame(make_state(game_event="NEW_HAND"))
        start_hand(manager)

        assert manager.hand_id == 2

    def test_reset_clears_state(self, manager: HandManager) -> None:
        """reset returns to waiting and clears hand/action state."""
        start_hand(manager)
        manager.process_frame(
            make_state(
                hero_cards=["Ah", "Kd"],
                actions=[ActionRecord(seat=2, action="CALL", amount=100)],
            )
        )

        manager.reset()

        assert manager.phase == "waiting"
        assert manager.hand_id is None
        assert manager.get_all_actions() == []
        assert manager.get_current_street_actions() is None


class TestOpponentStatsCalculation:
    """Tests for opponent stat calculation helpers."""

    def test_three_bet_pct_opportunity_and_did(self, manager: HandManager) -> None:
        """Other player opens and target reraises."""
        actions = [
            stat_action(3, "RAISE", "preflop", 300),
            stat_action(2, "RAISE", "preflop", 900),
        ]
        assert manager._calc_three_bet_pct(2, actions) == 1.0

    def test_three_bet_pct_opportunity_but_called(self, manager: HandManager) -> None:
        """Other player opens and target calls."""
        actions = [
            stat_action(3, "RAISE", "preflop", 300),
            stat_action(2, "CALL", "preflop", 300),
        ]
        assert manager._calc_three_bet_pct(2, actions) == 0.0

    def test_three_bet_pct_no_opportunity_no_raise(self, manager: HandManager) -> None:
        """No open raise means no 3bet opportunity."""
        actions = [
            stat_action(3, "CALL", "preflop", 100),
            stat_action(2, "CHECK", "preflop", 0),
        ]
        assert manager._calc_three_bet_pct(2, actions) is None

    def test_three_bet_pct_no_opportunity_self_opened(self, manager: HandManager) -> None:
        """Self open raise is not a 3bet opportunity."""
        actions = [
            stat_action(2, "RAISE", "preflop", 300),
            stat_action(3, "FOLD", "preflop", 0),
        ]
        assert manager._calc_three_bet_pct(2, actions) is None

    def test_cbet_flop_did_bet(self, manager: HandManager) -> None:
        """Last preflop aggressor bets flop."""
        actions = [
            stat_action(2, "RAISE", "preflop", 300),
            stat_action(3, "CALL", "preflop", 300),
            stat_action(2, "BET", "flop", 200),
        ]
        assert manager._calc_cbet_flop_pct(2, actions) == 1.0

    def test_cbet_flop_checked(self, manager: HandManager) -> None:
        """Last preflop aggressor checks flop."""
        actions = [
            stat_action(2, "RAISE", "preflop", 300),
            stat_action(3, "CALL", "preflop", 300),
            stat_action(2, "CHECK", "flop", 0),
        ]
        assert manager._calc_cbet_flop_pct(2, actions) == 0.0

    def test_cbet_flop_not_aggressor(self, manager: HandManager) -> None:
        """Non-aggressor has no c-bet opportunity."""
        actions = [
            stat_action(3, "RAISE", "preflop", 300),
            stat_action(2, "CALL", "preflop", 300),
            stat_action(2, "CHECK", "flop", 0),
        ]
        assert manager._calc_cbet_flop_pct(2, actions) is None

    def test_cbet_flop_no_flop(self, manager: HandManager) -> None:
        """No flop action means no c-bet opportunity."""
        actions = [
            stat_action(2, "RAISE", "preflop", 300),
            stat_action(3, "FOLD", "preflop", 0),
        ]
        assert manager._calc_cbet_flop_pct(2, actions) is None

    def test_fold_to_three_bet_did_fold(self, manager: HandManager) -> None:
        """Open raiser folds after facing a 3bet."""
        actions = [
            stat_action(2, "RAISE", "preflop", 300),
            stat_action(3, "RAISE", "preflop", 900),
            stat_action(2, "FOLD", "preflop", 0),
        ]
        assert manager._calc_fold_to_three_bet(2, actions) == 1.0

    def test_fold_to_three_bet_did_call(self, manager: HandManager) -> None:
        """Open raiser continues after facing a 3bet."""
        actions = [
            stat_action(2, "RAISE", "preflop", 300),
            stat_action(3, "RAISE", "preflop", 900),
            stat_action(2, "CALL", "preflop", 900),
        ]
        assert manager._calc_fold_to_three_bet(2, actions) == 0.0

    def test_fold_to_three_bet_no_three_bet(self, manager: HandManager) -> None:
        """No 3bet after open means no fold-to-3bet opportunity."""
        actions = [
            stat_action(2, "RAISE", "preflop", 300),
            stat_action(3, "CALL", "preflop", 300),
        ]
        assert manager._calc_fold_to_three_bet(2, actions) is None

    def test_fold_to_three_bet_not_opener(self, manager: HandManager) -> None:
        """Non-opener has no fold-to-3bet opportunity."""
        actions = [
            stat_action(3, "RAISE", "preflop", 300),
            stat_action(2, "CALL", "preflop", 300),
        ]
        assert manager._calc_fold_to_three_bet(2, actions) is None

    def test_went_to_showdown_reached_river(self, manager: HandManager) -> None:
        """VPIP player reaches river without folding."""
        actions = [
            stat_action(2, "CALL", "preflop", 100),
            stat_action(2, "CHECK", "river", 0),
        ]
        assert manager._calc_went_to_showdown(2, actions) == 1.0

    def test_went_to_showdown_folded_on_turn(self, manager: HandManager) -> None:
        """VPIP player who folded before river did not reach showdown."""
        actions = [
            stat_action(2, "CALL", "preflop", 100),
            stat_action(2, "FOLD", "turn", 0),
            stat_action(3, "CHECK", "river", 0),
        ]
        assert manager._calc_went_to_showdown(2, actions) == 0.0

    def test_went_to_showdown_no_vpip(self, manager: HandManager) -> None:
        """Player without VPIP has no WTSD opportunity."""
        actions = [
            stat_action(2, "CHECK", "preflop", 0),
            stat_action(2, "CHECK", "river", 0),
        ]
        assert manager._calc_went_to_showdown(2, actions) is None

    def test_went_to_showdown_no_river(self, manager: HandManager) -> None:
        """VPIP player not reaching river did not reach showdown."""
        actions = [
            stat_action(2, "CALL", "preflop", 100),
            stat_action(2, "CHECK", "flop", 0),
        ]
        assert manager._calc_went_to_showdown(2, actions) == 0.0


class TestOpponentStatsDBUpdate:
    """DB persistence tests for expanded opponent statistics."""

    def test_new_player_stats_inserted(self, tmp_path: Path) -> None:
        """New opponent rows include all expanded stat fields."""
        db_path = tmp_path / "stats.db"
        manager = make_manager(tmp_path, db_path=str(db_path))
        manager._hand_id = 1
        manager._hero_cards = ["Ah", "Kd"]
        manager._current_players = {"2": {"name": "Alice", "in_current_hand": True}}
        manager._street_actions = {
            "preflop": StreetActions(
                street="preflop",
                actions=[
                    ActionRecord(seat=3, action="RAISE", amount=300),
                    ActionRecord(seat=2, action="RAISE", amount=900),
                ],
            ),
            "flop": StreetActions(
                street="flop",
                actions=[ActionRecord(seat=2, action="BET", amount=200)],
            ),
            "turn": StreetActions(street="turn", actions=[]),
            "river": StreetActions(
                street="river",
                actions=[ActionRecord(seat=2, action="CHECK", amount=0)],
            ),
        }

        manager._save_to_db()
        stats = manager.get_opponent_stats("Alice")
        manager.close()

        assert stats is not None
        assert stats["vpip"] == 100.0
        assert stats["pfr"] == 100.0
        assert stats["three_bet_pct"] == 100.0
        assert stats["cbet_flop_pct"] == 100.0
        assert stats["went_to_showdown"] == 100.0
        assert stats["three_bet_opportunities"] == 1
        assert stats["three_bet_count"] == 1
        assert stats["cbet_flop_opportunities"] == 1
        assert stats["cbet_flop_count"] == 1
        assert stats["wtsd_opportunities"] == 1
        assert stats["wtsd_count"] == 1

    def test_existing_player_stats_updated(self, tmp_path: Path) -> None:
        """Existing opponent counter fields are accumulated."""
        db_path = tmp_path / "stats.db"
        manager = make_manager(tmp_path, db_path=str(db_path))
        assert manager._db_conn is not None
        timestamp = datetime.now(timezone.utc).isoformat()
        manager._db_conn.execute(
            """
            INSERT INTO opponents
                (player_name, total_hands, first_seen, last_seen, vpip, pfr,
                 three_bet_opportunities, three_bet_count, three_bet_pct)
            VALUES (?, 1, ?, ?, 100.0, 100.0, 1, 1, 100.0)
            """,
            ("Alice", timestamp, timestamp),
        )
        actions = [
            stat_action(3, "RAISE", "preflop", 300),
            stat_action(2, "CALL", "preflop", 300),
        ]

        manager._update_opponent_stats(
            manager._db_conn.cursor(),
            "Alice",
            2,
            timestamp,
            actions,
        )
        manager._db_conn.commit()
        stats = manager.get_opponent_stats("Alice")
        manager.close()

        assert stats is not None
        assert stats["total_hands"] == 2
        assert stats["three_bet_opportunities"] == 2
        assert stats["three_bet_count"] == 1
        assert stats["three_bet_pct"] == 50.0

    def test_opportunity_counters_accumulate(self, tmp_path: Path) -> None:
        """Multiple hands produce accurate opportunity-based percentages."""
        db_path = tmp_path / "stats.db"
        manager = make_manager(tmp_path, db_path=str(db_path))
        assert manager._db_conn is not None
        timestamp = datetime.now(timezone.utc).isoformat()
        cursor = manager._db_conn.cursor()
        hand_one = [
            stat_action(2, "RAISE", "preflop", 300),
            stat_action(3, "RAISE", "preflop", 900),
            stat_action(2, "FOLD", "preflop", 0),
        ]
        hand_two = [
            stat_action(2, "RAISE", "preflop", 300),
            stat_action(3, "RAISE", "preflop", 900),
            stat_action(2, "CALL", "preflop", 900),
        ]

        manager._update_opponent_stats(cursor, "Alice", 2, timestamp, hand_one)
        manager._update_opponent_stats(cursor, "Alice", 2, timestamp, hand_two)
        manager._db_conn.commit()
        stats = manager.get_opponent_stats("Alice")
        manager.close()

        assert stats is not None
        assert stats["fold_to_three_bet_opportunities"] == 2
        assert stats["fold_to_three_bet_count"] == 1
        assert stats["fold_to_three_bet"] == 50.0

    def test_no_opportunity_does_not_dilute(self, tmp_path: Path) -> None:
        """Hands without opportunities leave opportunity stats unchanged."""
        db_path = tmp_path / "stats.db"
        manager = make_manager(tmp_path, db_path=str(db_path))
        assert manager._db_conn is not None
        timestamp = datetime.now(timezone.utc).isoformat()
        manager._db_conn.execute(
            """
            INSERT INTO opponents
                (player_name, total_hands, first_seen, last_seen,
                 three_bet_opportunities, three_bet_count, three_bet_pct)
            VALUES (?, 1, ?, ?, 1, 1, 100.0)
            """,
            ("Alice", timestamp, timestamp),
        )
        actions = [
            stat_action(3, "CALL", "preflop", 100),
            stat_action(2, "CHECK", "preflop", 0),
        ]

        manager._update_opponent_stats(
            manager._db_conn.cursor(),
            "Alice",
            2,
            timestamp,
            actions,
        )
        manager._db_conn.commit()
        stats = manager.get_opponent_stats("Alice")
        manager.close()

        assert stats is not None
        assert stats["three_bet_opportunities"] == 1
        assert stats["three_bet_count"] == 1
        assert stats["three_bet_pct"] == 100.0

    def test_actions_json_includes_street(self, tmp_path: Path) -> None:
        """hand_history.actions stores street names."""
        db_path = tmp_path / "stats.db"
        manager = make_manager(tmp_path, db_path=str(db_path))
        manager._hand_id = 1
        manager._hero_cards = ["Ah", "Kd"]
        manager._current_players = {}
        manager._street_actions = {
            "preflop": StreetActions(
                street="preflop",
                actions=[ActionRecord(seat=2, action="CALL", amount=100)],
            ),
            "flop": StreetActions(
                street="flop",
                actions=[ActionRecord(seat=2, action="BET", amount=200)],
            ),
            "turn": StreetActions(street="turn", actions=[]),
            "river": StreetActions(street="river", actions=[]),
        }

        manager._save_to_db()
        manager.close()

        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT actions FROM hand_history").fetchone()
        actions = json.loads(row[0])

        assert actions[0]["street"] == "preflop"
        assert actions[1]["street"] == "flop"


class TestGetOpponentStats:
    """Tests for seat-keyed opponent stats lookup."""

    def test_returns_stats_for_seated_opponents(self, tmp_path: Path) -> None:
        """Stats are returned for named seated opponents."""
        manager = make_manager(tmp_path)
        assert manager._db_conn is not None
        now = datetime.now(timezone.utc).isoformat()
        manager._db_conn.execute(
            """
            INSERT INTO opponents
                (player_name, total_hands, first_seen, last_seen, vpip, pfr,
                 three_bet_pct, cbet_flop_pct, fold_to_three_bet,
                 went_to_showdown)
            VALUES (?, 20, ?, ?, 28.5, 18.0, 7.5, 55.0, 40.0, 35.0)
            """,
            ("Alice", now, now),
        )
        manager._db_conn.commit()
        state = make_state()
        state.players["2"].name = "Alice"

        stats = manager.get_opponent_stats(state)
        manager.close()

        assert isinstance(stats, dict)
        assert stats["2"]["player_name"] == "Alice"
        assert stats["2"]["three_bet_pct"] == 7.5

    def test_excludes_hero_seat(self, manager: HandManager) -> None:
        """Hero seat is not included in seat-keyed stats maps."""
        state = make_state()
        state.players["2"].name = None

        stats = manager.get_opponent_stats(state)

        assert stats == {}

    def test_returns_empty_for_unknown_players(self, manager: HandManager) -> None:
        """Unknown player names are omitted."""
        state = make_state()
        state.players["2"].name = "Unknown"

        assert manager.get_opponent_stats(state) == {}

    def test_prefix_match_for_truncated_names(self, tmp_path: Path) -> None:
        """Prefix matching works for truncated visible names."""
        manager = make_manager(tmp_path)
        assert manager._db_conn is not None
        now = datetime.now(timezone.utc).isoformat()
        manager._db_conn.execute(
            """
            INSERT INTO opponents
                (player_name, total_hands, first_seen, last_seen, vpip, pfr)
            VALUES (?, 20, ?, ?, 30.0, 20.0)
            """,
            ("AliceLong", now, now),
        )
        manager._db_conn.commit()
        state = make_state()
        state.players["2"].name = "Alice"

        stats = manager.get_opponent_stats(state)
        manager.close()

        assert isinstance(stats, dict)
        assert stats["2"]["player_name"] == "AliceLong"

    def test_returns_empty_on_db_error(self, manager: HandManager) -> None:
        """A closed DB connection yields an empty stats map."""
        manager.close()
        state = make_state()
        state.players["2"].name = "Alice"

        assert manager.get_opponent_stats(state) == {}

    def test_includes_all_stat_fields(self, tmp_path: Path) -> None:
        """Seat-keyed stats rows include all persisted stat fields."""
        manager = make_manager(tmp_path)
        assert manager._db_conn is not None
        now = datetime.now(timezone.utc).isoformat()
        manager._db_conn.execute(
            """
            INSERT INTO opponents
                (player_name, total_hands, first_seen, last_seen, vpip, pfr,
                 three_bet_pct, cbet_flop_pct, fold_to_three_bet,
                 went_to_showdown, long_term_style, freshness_note)
            VALUES (?, 20, ?, ?, 28.5, 18.0, 7.5, 55.0, 40.0, 35.0,
                    'TAG', 'fresh')
            """,
            ("Alice", now, now),
        )
        manager._db_conn.commit()
        state = make_state()
        state.players["2"].name = "Alice"

        stats = manager.get_opponent_stats(state)
        manager.close()

        assert isinstance(stats, dict)
        row = stats["2"]
        for field in [
            "player_name",
            "total_hands",
            "vpip",
            "pfr",
            "three_bet_pct",
            "cbet_flop_pct",
            "fold_to_three_bet",
            "went_to_showdown",
            "long_term_style",
            "freshness_note",
        ]:
            assert field in row
