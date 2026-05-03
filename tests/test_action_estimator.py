"""Tests for action estimation core logic."""

import json
import pathlib
from typing import Any

import pytest

from core.game_state import (
    ActionRecord,
    GameState,
    PlayerState,
    create_empty_game_state,
)
from recognition.action_estimator import ActionEstimator

ACTION_SEQUENCE_DIR = pathlib.Path(__file__).parent / "fixtures" / "action_sequences"


@pytest.fixture
def estimator() -> ActionEstimator:
    """Return an ActionEstimator with default project config values."""
    return ActionEstimator(
        {
            "action_estimation": {
                "new_hand_pot_ratio": 0.3,
                "new_hand_min_pot_bb": 2,
                "raise_threshold": 1.1,
            },
            "game": {"blind_sb": 50, "blind_bb": 100},
        }
    )


def make_state(
    pot: int = 0,
    board_count: int = 0,
    hero_stack: int | None = 1000,
    hero_bet: int = 0,
    player_values: dict[str, tuple[int | None, int]] | None = None,
) -> GameState:
    """Create a GameState for action-estimator tests.

    Args:
        pot: Pot amount.
        board_count: Board card count.
        hero_stack: Hero stack.
        hero_bet: Hero bet.
        player_values: Mapping of seat to (stack, bet).

    Returns:
        Configured GameState.
    """
    state = create_empty_game_state()
    state.pot = pot
    state.board_card_count = board_count
    state.hero.stack = hero_stack
    state.hero.bet = hero_bet
    for seat in ["2", "3", "4", "5", "6"]:
        stack, bet = (player_values or {}).get(seat, (1000, 0))
        state.players[seat] = PlayerState(stack=stack, bet=bet)
    return state


def action_tuples(result: dict[str, Any]) -> list[tuple[int, str, int, str]]:
    """Convert estimator actions to comparable tuples."""
    return [
        (action.seat, action.action, action.amount, action.confidence)
        for action in result["actions"]
    ]


def _load_sequence(filename: str) -> dict[str, Any]:
    """Load an action sequence fixture.

    Args:
        filename: Fixture filename.

    Returns:
        Parsed sequence dictionary.
    """
    with open(ACTION_SEQUENCE_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_game_state(frame_state: dict[str, Any]) -> GameState:
    """Build a GameState from a simplified sequence frame.

    Args:
        frame_state: Simplified frame state from JSON.

    Returns:
        GameState with fields needed by ActionEstimator populated.
    """
    game_state = create_empty_game_state()
    game_state.pot = frame_state["pot"]
    game_state.board_card_count = frame_state["board_card_count"]
    game_state.hero.stack = frame_state["hero_stack"]
    game_state.hero.bet = frame_state["hero_bet"]
    game_state.hero.is_my_turn = frame_state["hero_is_my_turn"]

    for seat_key, player_data in frame_state["players"].items():
        if seat_key not in game_state.players:
            continue
        game_state.players[seat_key].stack = player_data["stack"]
        game_state.players[seat_key].bet = player_data["bet"]
        game_state.players[seat_key].is_seated = player_data["stack"] is not None

    return game_state


def _assert_actions_match(
    actual_actions: list[ActionRecord],
    expected_actions: list[dict[str, Any]],
) -> None:
    """Assert actual ActionRecord list matches expected action dictionaries.

    Args:
        actual_actions: Actions returned by ActionEstimator.
        expected_actions: Expected action dictionaries from fixture.
    """
    assert len(actual_actions) == len(expected_actions), (
        f"Action count mismatch: got {len(actual_actions)}, "
        f"expected {len(expected_actions)}"
    )
    for actual, expected in zip(actual_actions, expected_actions):
        assert actual.seat == expected["seat"], (
            f"Seat mismatch: got {actual.seat}, expected {expected['seat']}"
        )
        assert actual.action == expected["action"], (
            f"Action mismatch for seat {actual.seat}: "
            f"got {actual.action}, expected {expected['action']}"
        )
        if expected.get("amount", 0) > 0:
            assert actual.amount == expected["amount"], (
                f"Amount mismatch for seat {actual.seat}: "
                f"got {actual.amount}, expected {expected['amount']}"
            )


def _assert_sequence_matches(
    sequence: dict[str, Any],
    estimator: ActionEstimator,
) -> None:
    """Run a full sequence and assert expected transition results.

    Args:
        sequence: Parsed sequence fixture.
        estimator: ActionEstimator instance.
    """
    frames = sequence["frames"]
    for index in range(1, len(frames)):
        prev_state = _build_game_state(frames[index - 1]["state"])
        curr_state = _build_game_state(frames[index]["state"])
        expected = frames[index]["expected"]

        result = estimator.estimate(prev_state, curr_state)

        assert result["game_event"] == expected["game_event"], (
            f"Frame {frames[index]['frame_id']}: event mismatch: "
            f"got {result['game_event']}, expected {expected['game_event']}"
        )
        _assert_actions_match(result["actions"], expected.get("actions", []))


class TestGameEvents:
    """Tests for event priority checks."""

    def test_no_change_returns_empty(self, estimator: ActionEstimator) -> None:
        """No state change returns no event and no actions."""
        previous = make_state(pot=100)
        current = make_state(pot=100)

        result = estimator.estimate(previous, current)

        assert result == {"game_event": None, "actions": []}

    def test_new_hand_detected(self, estimator: ActionEstimator) -> None:
        """A large pot decrease after a sufficiently large pot is NEW_HAND."""
        previous = make_state(pot=1000)
        current = make_state(pot=100)

        result = estimator.estimate(previous, current)

        assert result["game_event"] == "NEW_HAND"
        assert result["actions"] == []

    def test_new_hand_requires_dynamic_threshold(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Small previous pots do not trigger NEW_HAND."""
        previous = make_state(pot=150)
        current = make_state(pot=10)

        result = estimator.estimate(previous, current)

        assert result["game_event"] is None

    def test_new_street_detected(self, estimator: ActionEstimator) -> None:
        """Board card count increase is NEW_STREET."""
        previous = make_state(pot=300, board_count=0)
        current = make_state(pot=300, board_count=3)

        result = estimator.estimate(previous, current)

        assert result["game_event"] == "NEW_STREET"
        assert result["actions"] == []

    def test_bets_collected_detected(self, estimator: ActionEstimator) -> None:
        """Pot increase with all current bets cleared is BETS_COLLECTED."""
        previous = make_state(
            pot=300,
            player_values={"2": (900, 100), "3": (900, 100)},
        )
        current = make_state(pot=500)

        result = estimator.estimate(previous, current)

        assert result["game_event"] == "BETS_COLLECTED"
        assert result["actions"] == []

    def test_event_priority_new_street_before_bets_collected(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Board increase takes priority over bets-collected pattern."""
        previous = make_state(pot=300, board_count=0, player_values={"2": (900, 100)})
        current = make_state(pot=500, board_count=3)

        result = estimator.estimate(previous, current)

        assert result["game_event"] == "NEW_STREET"


class TestSeatActions:
    """Tests for seat action analysis."""

    def test_bet_detected(self, estimator: ActionEstimator) -> None:
        """A first wager when max_bet_prev is zero is BET."""
        previous = make_state(player_values={"2": (1000, 0)})
        current = make_state(player_values={"2": (800, 200)})

        result = estimator.estimate(previous, current)

        assert action_tuples(result) == [(2, "BET", 200, "high")]

    def test_call_detected(self, estimator: ActionEstimator) -> None:
        """A wager up to previous max bet is CALL."""
        previous = make_state(
            player_values={"2": (800, 200), "3": (1000, 0)}
        )
        current = make_state(
            player_values={"2": (800, 200), "3": (800, 200)}
        )

        result = estimator.estimate(previous, current)

        assert action_tuples(result) == [(3, "CALL", 200, "high")]

    def test_raise_detected(self, estimator: ActionEstimator) -> None:
        """A wager above raise threshold is RAISE."""
        previous = make_state(
            player_values={"2": (800, 200), "3": (1000, 0)}
        )
        current = make_state(
            player_values={"2": (800, 200), "3": (700, 300)}
        )

        result = estimator.estimate(previous, current)

        assert action_tuples(result) == [(3, "RAISE", 300, "high")]

    def test_all_in_detected(self, estimator: ActionEstimator) -> None:
        """Stack dropping to zero with bet change is ALL_IN."""
        previous = make_state(player_values={"2": (300, 0)})
        current = make_state(player_values={"2": (0, 300)})

        result = estimator.estimate(previous, current)

        assert action_tuples(result) == [(2, "ALL_IN", 300, "high")]

    def test_one_frame_none_is_not_fold(self, estimator: ActionEstimator) -> None:
        """A single None frame is treated as possible OCR failure."""
        previous = make_state(player_values={"2": (1000, 0)})
        current = make_state(player_values={"2": (None, 0)})

        result = estimator.estimate(previous, current)

        assert action_tuples(result) == []

    def test_hero_bet_detected(self, estimator: ActionEstimator) -> None:
        """Hero stack and bet changes are analyzed as seat 1."""
        previous = make_state(hero_stack=1000, hero_bet=0)
        current = make_state(hero_stack=800, hero_bet=200)

        result = estimator.estimate(previous, current)

        assert action_tuples(result) == [(1, "BET", 200, "high")]

    def test_actions_sorted_by_seat(self, estimator: ActionEstimator) -> None:
        """Detected actions are sorted by seat number."""
        previous = make_state(
            hero_stack=1000,
            hero_bet=0,
            player_values={"3": (1000, 0), "2": (1000, 0)},
        )
        current = make_state(
            hero_stack=900,
            hero_bet=100,
            player_values={"3": (900, 100), "2": (900, 100)},
        )

        result = estimator.estimate(previous, current)

        assert [action.seat for action in result["actions"]] == [1, 2, 3]
        assert all(action.confidence == "low" for action in result["actions"])


class TestBlinds:
    """Tests for blind estimation."""

    def test_estimate_blinds_non_hero(self, estimator: ActionEstimator) -> None:
        """SB and BB posts are detected for non-hero seats."""
        state = make_state(player_values={"2": (950, 50), "3": (900, 100)})

        actions = estimator.estimate_blinds(state, sb_seat=2, bb_seat=3)

        assert [(a.seat, a.action, a.amount) for a in actions] == [
            (2, "BLIND_SB", 50),
            (3, "BLIND_BB", 100),
        ]

    def test_estimate_blinds_hero_sb(self, estimator: ActionEstimator) -> None:
        """Hero blind post is detected when hero is SB."""
        state = make_state(hero_bet=50, player_values={"2": (900, 100)})

        actions = estimator.estimate_blinds(state, sb_seat=1, bb_seat=2)

        assert [(a.seat, a.action, a.amount) for a in actions] == [
            (1, "BLIND_SB", 50),
            (2, "BLIND_BB", 100),
        ]

    def test_reset_clears_internal_state(self, estimator: ActionEstimator) -> None:
        """reset() clears placeholder internal state."""
        estimator._none_streak["2"] = 2
        estimator._pot_spike_streak = 1
        estimator._pot_spike_value = 1000

        estimator.reset()

        assert estimator._none_streak == {}
        assert estimator._pot_spike_streak == 0
        assert estimator._pot_spike_value == 0


class TestSequenceHand001:
    """Sequence tests for hand_001 preflop actions."""

    @pytest.fixture
    def sequence(self) -> dict[str, Any]:
        """Load hand_001 sequence fixture."""
        return _load_sequence("hand_001_preflop_actions.json")

    @pytest.fixture
    def estimator(self) -> ActionEstimator:
        """Return an ActionEstimator for sequence tests."""
        return ActionEstimator(
            {
                "action_estimation": {
                    "new_hand_pot_ratio": 0.3,
                    "new_hand_min_pot_bb": 2,
                    "raise_threshold": 1.1,
                },
                "game": {"blind_bb": 100, "blind_sb": 50},
                "recognition": {"fold_confirm_frames": 1},
            }
        )

    def test_full_sequence(
        self,
        sequence: dict[str, Any],
        estimator: ActionEstimator,
    ) -> None:
        """All hand_001 transitions match expected results."""
        _assert_sequence_matches(sequence, estimator)


class TestSequenceHand002:
    """Sequence tests for hand_002 postflop bet and fold."""

    @pytest.fixture
    def sequence(self) -> dict[str, Any]:
        """Load hand_002 sequence fixture."""
        return _load_sequence("hand_002_postflop_fold.json")

    @pytest.fixture
    def estimator(self) -> ActionEstimator:
        """Return an ActionEstimator for sequence tests."""
        return ActionEstimator(
            {
                "action_estimation": {
                    "new_hand_pot_ratio": 0.3,
                    "new_hand_min_pot_bb": 2,
                    "raise_threshold": 1.1,
                },
                "game": {"blind_bb": 100, "blind_sb": 50},
                "recognition": {"fold_confirm_frames": 1},
            }
        )

    def test_full_sequence(
        self,
        sequence: dict[str, Any],
        estimator: ActionEstimator,
    ) -> None:
        """All hand_002 transitions match expected results."""
        _assert_sequence_matches(sequence, estimator)


class TestSequenceHand003:
    """Sequence tests for hand_003 all-in and street transition."""

    @pytest.fixture
    def sequence(self) -> dict[str, Any]:
        """Load hand_003 sequence fixture."""
        return _load_sequence("hand_003_allin_showdown.json")

    @pytest.fixture
    def estimator(self) -> ActionEstimator:
        """Return an ActionEstimator for sequence tests."""
        return ActionEstimator(
            {
                "action_estimation": {
                    "new_hand_pot_ratio": 0.3,
                    "new_hand_min_pot_bb": 2,
                    "raise_threshold": 1.1,
                },
                "game": {"blind_bb": 100, "blind_sb": 50},
            }
        )

    def test_full_sequence(
        self,
        sequence: dict[str, Any],
        estimator: ActionEstimator,
    ) -> None:
        """All hand_003 transitions match expected results."""
        _assert_sequence_matches(sequence, estimator)


class TestSequenceHand004:
    """Sequence tests for CHECK, OCR-skip, and pot-spike filtering."""

    @pytest.fixture
    def sequence(self) -> dict[str, Any]:
        """Load hand_004 sequence fixture."""
        return _load_sequence("hand_004_check_and_ocr_skip.json")

    @pytest.fixture
    def estimator(self) -> ActionEstimator:
        """Return an ActionEstimator for OCR-skip sequence tests."""
        return ActionEstimator(
            {
                "action_estimation": {
                    "new_hand_pot_ratio": 0.3,
                    "new_hand_min_pot_bb": 2,
                    "raise_threshold": 1.1,
                },
                "game": {"blind_bb": 100, "blind_sb": 50},
                "recognition": {
                    "fold_confirm_frames": 3,
                    "pot_spike_ratio": 2.0,
                    "pot_spike_confirm_frames": 2,
                },
            }
        )

    def test_full_sequence(
        self,
        sequence: dict[str, Any],
        estimator: ActionEstimator,
    ) -> None:
        """All hand_004 transitions match expected results."""
        _assert_sequence_matches(sequence, estimator)


class TestEstimateBlindsFromSequence:
    """Tests for estimate_blinds using sequence fixtures."""

    def test_blinds_from_hand_001(self) -> None:
        """Blinds are detected from hand_001 frame 1."""
        sequence = _load_sequence("hand_001_preflop_actions.json")
        frame_1 = sequence["frames"][1]
        game_state = _build_game_state(frame_1["state"])
        estimator = ActionEstimator(
            {
                "action_estimation": {},
                "game": {"blind_bb": 100, "blind_sb": 50},
            }
        )

        blinds = estimator.estimate_blinds(game_state, sb_seat=2, bb_seat=3)

        assert len(blinds) == 2
        assert blinds[0].action == "BLIND_SB"
        assert blinds[0].seat == 2
        assert blinds[0].amount == 50
        assert blinds[1].action == "BLIND_BB"
        assert blinds[1].seat == 3
        assert blinds[1].amount == 100


class TestOcrFailureSkip:
    """Tests for OCR failure skip and FOLD confirmation."""

    @pytest.fixture
    def estimator(self) -> ActionEstimator:
        """Return an estimator using three-frame FOLD confirmation."""
        return ActionEstimator(
            {
                "action_estimation": {"raise_threshold": 1.1},
                "game": {"blind_bb": 100},
                "recognition": {"fold_confirm_frames": 3},
            }
        )

    def test_one_frame_none_no_fold(self, estimator: ActionEstimator) -> None:
        """One None frame does not produce FOLD."""
        previous = create_empty_game_state()
        previous.players["2"].stack = 5000

        current = create_empty_game_state()
        current.players["2"].stack = None

        result = estimator.estimate(previous, current)

        assert [action for action in result["actions"] if action.action == "FOLD"] == []

    def test_two_frames_none_no_fold(self, estimator: ActionEstimator) -> None:
        """Two consecutive None frames do not produce FOLD."""
        previous = create_empty_game_state()
        previous.players["2"].stack = 5000

        current = create_empty_game_state()
        current.players["2"].stack = None

        estimator.estimate(previous, current)

        none_previous = create_empty_game_state()
        none_previous.players["2"].stack = None
        none_current = create_empty_game_state()
        none_current.players["2"].stack = None

        result = estimator.estimate(none_previous, none_current)

        assert [action for action in result["actions"] if action.action == "FOLD"] == []

    def test_three_frames_none_fold_confirmed(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Three consecutive None frames produce high-confidence FOLD."""
        previous = create_empty_game_state()
        previous.players["2"].stack = 5000

        current = create_empty_game_state()
        current.players["2"].stack = None

        estimator.estimate(previous, current)

        none_previous = create_empty_game_state()
        none_previous.players["2"].stack = None
        none_current = create_empty_game_state()
        none_current.players["2"].stack = None

        estimator.estimate(none_previous, none_current)
        result = estimator.estimate(none_previous, none_current)
        fold_actions = [
            action for action in result["actions"] if action.action == "FOLD"
        ]

        assert len(fold_actions) == 1
        assert fold_actions[0].seat == 2
        assert fold_actions[0].confidence == "high"

    def test_none_recovery_resets_streak(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Recovered stack resets the consecutive None streak."""
        previous = create_empty_game_state()
        previous.players["2"].stack = 5000

        current = create_empty_game_state()
        current.players["2"].stack = None

        estimator.estimate(previous, current)

        recovered_previous = create_empty_game_state()
        recovered_previous.players["2"].stack = None
        recovered_current = create_empty_game_state()
        recovered_current.players["2"].stack = 5000

        estimator.estimate(recovered_previous, recovered_current)

        retry_previous = create_empty_game_state()
        retry_previous.players["2"].stack = 5000
        retry_current = create_empty_game_state()
        retry_current.players["2"].stack = None

        result = estimator.estimate(retry_previous, retry_current)

        assert [action for action in result["actions"] if action.action == "FOLD"] == []


class TestHeroCheck:
    """Tests for hero CHECK detection."""

    @pytest.fixture
    def estimator(self) -> ActionEstimator:
        """Return a default estimator."""
        return ActionEstimator(
            {
                "action_estimation": {},
                "game": {"blind_bb": 100},
                "recognition": {},
            }
        )

    def test_hero_check_detected(self, estimator: ActionEstimator) -> None:
        """is_my_turn True to False with no value changes is CHECK."""
        previous = create_empty_game_state()
        previous.hero.is_my_turn = True
        previous.hero.stack = 4700
        previous.hero.bet = 0

        current = create_empty_game_state()
        current.hero.is_my_turn = False
        current.hero.stack = 4700
        current.hero.bet = 0

        result = estimator.estimate(previous, current)
        checks = [action for action in result["actions"] if action.action == "CHECK"]

        assert len(checks) == 1
        assert checks[0].seat == 1

    def test_hero_turn_off_with_bet_is_not_check(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """is_my_turn True to False with a wager is not CHECK."""
        previous = create_empty_game_state()
        previous.hero.is_my_turn = True
        previous.hero.stack = 4700
        previous.hero.bet = 0

        current = create_empty_game_state()
        current.hero.is_my_turn = False
        current.hero.stack = 4460
        current.hero.bet = 240

        result = estimator.estimate(previous, current)
        checks = [action for action in result["actions"] if action.action == "CHECK"]

        assert checks == []


class TestOpponentCheck:
    """Tests for low-confidence opponent CHECK estimation."""

    def test_opponent_check_with_non_value_state_change(self) -> None:
        """A non-value state change with no actions is an unknown-seat CHECK."""
        estimator = ActionEstimator(
            {
                "action_estimation": {},
                "game": {"blind_bb": 100},
                "recognition": {},
            }
        )
        previous = create_empty_game_state()
        previous.hero.is_my_turn = False

        current = create_empty_game_state()
        current.hero.is_my_turn = True

        result = estimator.estimate(previous, current)

        assert action_tuples(result) == [(0, "CHECK", 0, "low")]


class TestPotSpikeFilter:
    """Tests for one-frame pot spike filtering."""

    @pytest.fixture
    def estimator(self) -> ActionEstimator:
        """Return an estimator with default pot-spike thresholds."""
        return ActionEstimator(
            {
                "action_estimation": {
                    "new_hand_pot_ratio": 0.3,
                    "new_hand_min_pot_bb": 2,
                },
                "game": {"blind_bb": 100},
                "recognition": {
                    "pot_spike_ratio": 2.0,
                    "pot_spike_confirm_frames": 2,
                },
            }
        )

    def test_single_spike_filtered(self, estimator: ActionEstimator) -> None:
        """One-frame pot spike is filtered and produces no event/action."""
        previous = create_empty_game_state()
        previous.pot = 400

        current = create_empty_game_state()
        current.pot = 1000

        result = estimator.estimate(previous, current)

        assert result["game_event"] is None
        assert result["actions"] == []
