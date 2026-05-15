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

        assert result == {
            "game_event": None,
            "actions": [],
            "filtered_pot": None,
            "pot_spike_hold": False,
        }

    def test_new_hand_detected(self, estimator: ActionEstimator) -> None:
        """Two large pot decreases after a sufficiently large pot are NEW_HAND."""
        previous = make_state(pot=1000)
        current = make_state(pot=100)

        result = estimator.estimate(previous, current)
        if result["filtered_pot"] is not None:
            current.pot = result["filtered_pot"]
        confirmed = estimator.estimate(current, make_state(pot=100))

        assert result["game_event"] is None
        assert result["filtered_pot"] == 1000
        assert confirmed["game_event"] == "NEW_HAND"
        assert confirmed["actions"] == []

    def test_new_hand_requires_dynamic_threshold(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Small previous pots do not trigger NEW_HAND."""
        previous = make_state(pot=150)
        current = make_state(pot=10)

        result = estimator.estimate(previous, current)

        assert result["game_event"] is None


class TestNewHandConfirmation:
    """Tests for consecutive NEW_HAND confirmation."""

    @pytest.fixture
    def estimator(self) -> ActionEstimator:
        """Return an estimator with NEW_HAND confirmation enabled."""
        return ActionEstimator(
            {
                "action_estimation": {
                    "new_hand_pot_ratio": 0.3,
                    "new_hand_min_pot_bb": 2,
                },
                "game": {"blind_bb": 100},
                "recognition": {"new_hand_confirm_frames": 2},
            }
        )

    def test_new_hand_requires_two_consecutive_frames(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """One candidate frame is held; the second confirms NEW_HAND."""
        previous = make_state(pot=1000)
        candidate = make_state(pot=100)

        first = estimator.estimate(previous, candidate)
        if first["filtered_pot"] is not None:
            candidate.pot = first["filtered_pot"]
        second = estimator.estimate(candidate, make_state(pot=100))

        assert first["game_event"] is None
        assert first["filtered_pot"] == 1000
        assert second["game_event"] == "NEW_HAND"
        assert second["filtered_pot"] is None

    def test_new_hand_single_frame_no_trigger(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """A single pot drop followed by recovery does not trigger NEW_HAND."""
        previous = make_state(pot=1000)
        candidate = make_state(pot=100)

        first = estimator.estimate(previous, candidate)
        if first["filtered_pot"] is not None:
            candidate.pot = first["filtered_pot"]
        recovered = estimator.estimate(candidate, make_state(pot=900))

        assert first["game_event"] is None
        assert first["filtered_pot"] == 1000
        assert recovered["game_event"] is None
        assert recovered["filtered_pot"] is None

    def test_new_hand_streak_reset_on_normal_pot(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """A normal pot frame resets the candidate streak."""
        previous = make_state(pot=1000)
        candidate = make_state(pot=100)

        first = estimator.estimate(previous, candidate)
        if first["filtered_pot"] is not None:
            candidate.pot = first["filtered_pot"]
        recovered = estimator.estimate(candidate, make_state(pot=900))
        second_candidate = make_state(pot=100)
        second_first = estimator.estimate(make_state(pot=1000), second_candidate)

        assert recovered["game_event"] is None
        assert estimator._new_hand_streak == 1
        assert second_first["game_event"] is None
        assert second_first["filtered_pot"] == 1000

    def test_new_hand_candidate_holds_pot_value(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """NEW_HAND candidates return filtered_pot to protect prev_state."""
        previous = make_state(pot=1200)
        candidate = make_state(pot=0)

        result = estimator.estimate(previous, candidate)

        assert result["game_event"] is None
        assert result["actions"] == []
        assert result["filtered_pot"] == 1200

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
                "recognition": {
                    "fold_confirm_frames": 1,
                    "new_hand_confirm_frames": 1,
                },
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
                "recognition": {
                    "fold_confirm_frames": 1,
                    "new_hand_confirm_frames": 1,
                },
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
                "recognition": {"new_hand_confirm_frames": 1},
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
                    "new_hand_confirm_frames": 1,
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
        """Three consecutive None frames produce low-confidence FOLD."""
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
        assert fold_actions[0].confidence == "low"

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
        assert result["filtered_pot"] == 400
        assert result["pot_spike_hold"] is True

    def test_pot_spike_held_returns_filtered_pot(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Held pot spikes return the pre-spike pot as filtered_pot."""
        previous = create_empty_game_state()
        previous.pot = 400

        current = create_empty_game_state()
        current.pot = 2000

        result = estimator.estimate(previous, current)

        assert result["game_event"] is None
        assert result["actions"] == []
        assert result["filtered_pot"] == 400
        assert result["pot_spike_hold"] is True

    def test_pot_spike_no_false_new_hand(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """A held spike followed by a normal pot does not trigger NEW_HAND."""
        previous = create_empty_game_state()
        previous.pot = 400

        spike_frame = create_empty_game_state()
        spike_frame.pot = 2000
        spike_result = estimator.estimate(previous, spike_frame)
        if spike_result["filtered_pot"] is not None:
            spike_frame.pot = spike_result["filtered_pot"]

        normal_frame = create_empty_game_state()
        normal_frame.pot = 500
        normal_result = estimator.estimate(spike_frame, normal_frame)

        assert spike_result["filtered_pot"] == 400
        assert normal_result["game_event"] != "NEW_HAND"
        assert normal_result["filtered_pot"] is None

    def test_pot_spike_confirmed_returns_none_filtered_pot(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Confirmed repeated pot spikes do not request pot replacement."""
        previous = create_empty_game_state()
        previous.pot = 400

        first_spike = create_empty_game_state()
        first_spike.pot = 2000
        first_result = estimator.estimate(previous, first_spike)

        second_spike = create_empty_game_state()
        second_spike.pot = 2000
        if first_result["filtered_pot"] is not None:
            first_spike.pot = first_result["filtered_pot"]
        second_result = estimator.estimate(first_spike, second_spike)

        assert first_result["filtered_pot"] == 400
        assert first_result["pot_spike_hold"] is True
        assert second_result["filtered_pot"] is None
        assert second_result["pot_spike_hold"] is False

    def test_suspicious_ten_x_pot_spike_is_not_confirmed(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Clear ten-x OCR pot spikes are held even after repeated frames."""
        previous = create_empty_game_state()
        previous.pot = 7740

        first_spike = create_empty_game_state()
        first_spike.pot = 103320
        first_result = estimator.estimate(previous, first_spike)

        second_spike = create_empty_game_state()
        second_spike.pot = 103320
        if first_result["filtered_pot"] is not None:
            first_spike.pot = first_result["filtered_pot"]
        second_result = estimator.estimate(first_spike, second_spike)

        assert first_result["game_event"] is None
        assert first_result["filtered_pot"] == 7740
        assert first_result["pot_spike_hold"] is False
        assert second_result["game_event"] is None
        assert second_result["filtered_pot"] == 7740
        assert second_result["pot_spike_hold"] is False

    def test_natural_pot_increase_is_not_suspicious(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Natural pot growth is not held by suspicious-spike filtering."""
        previous = create_empty_game_state()
        previous.pot = 7740

        current = create_empty_game_state()
        current.pot = 10320

        result = estimator.estimate(previous, current)

        assert result["filtered_pot"] is None

    def test_non_suspicious_pot_spike_keeps_confirm_behavior(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Large non-ten-x spikes still use the existing confirm behavior."""
        previous = create_empty_game_state()
        previous.pot = 1000

        first_spike = create_empty_game_state()
        first_spike.pot = 5000
        first_result = estimator.estimate(previous, first_spike)

        second_spike = create_empty_game_state()
        second_spike.pot = 5000
        if first_result["filtered_pot"] is not None:
            first_spike.pot = first_result["filtered_pot"]
        second_result = estimator.estimate(first_spike, second_spike)

        assert first_result["filtered_pot"] == 1000
        assert second_result["filtered_pot"] is None

    def test_no_spike_returns_none_filtered_pot(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Regular pot changes do not request pot replacement."""
        previous = create_empty_game_state()
        previous.pot = 400

        current = create_empty_game_state()
        current.pot = 500

        result = estimator.estimate(previous, current)

        assert result["filtered_pot"] is None


class TestAllInReclassification:
    """Tests for reclassifying large raises/bets as ALL_IN."""

    def test_raise_reclassified_as_all_in(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """BET/RAISE of 90%+ of previous stack is reclassified as ALL_IN."""
        previous = make_state(
            player_values={"2": (10000, 0), "3": (10000, 0)}
        )
        current = make_state(
            player_values={"2": (10000, 0), "3": (500, 9500)}
        )

        result = estimator.estimate(previous, current)

        assert action_tuples(result) == [(3, "ALL_IN", 9500, "high")]

    def test_raise_not_reclassified_when_small(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Normal RAISE below 90% threshold keeps original action type."""
        previous = make_state(
            player_values={"2": (8000, 2000), "3": (10000, 0)}
        )
        current = make_state(
            player_values={"2": (8000, 2000), "3": (7000, 3000)}
        )

        result = estimator.estimate(previous, current)

        assert action_tuples(result) == [(3, "RAISE", 3000, "high")]

    def test_hero_raise_reclassified_as_all_in(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Hero BET/RAISE of 90%+ of previous stack is reclassified as ALL_IN."""
        previous = make_state(hero_stack=10000, hero_bet=0)
        current = make_state(hero_stack=500, hero_bet=9500)

        result = estimator.estimate(previous, current)

        assert action_tuples(result) == [(1, "ALL_IN", 9500, "high")]


# ---------------------------------------------------------------------------
# Phase 30-Fix34: BET amount normalization and suspicious detection tests
# ---------------------------------------------------------------------------


class TestBetAmountNormalization:
    """Tests for _normalize_bet_amount_text and _is_suspicious_bet_amount."""

    def test_decimal_truncated_to_integer(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Decimal amounts drop fractional part: '1980.4' -> 1980, '595.2' -> 595."""
        amount, suspicious, reason = estimator._normalize_bet_amount_text("1980.4")
        assert amount == 1980
        assert suspicious is False
        assert reason == "decimal_truncated"

        amount, suspicious, reason = estimator._normalize_bet_amount_text("595.2")
        assert amount == 595
        assert suspicious is False
        assert reason == "decimal_truncated"

    def test_comma_removed_as_thousands_separator(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Commas are stripped as digit-group separators: '1,980' -> 1980."""
        amount, suspicious, _reason = estimator._normalize_bet_amount_text("1,980")
        assert amount == 1980
        assert suspicious is False

    def test_suspicious_when_scaled10_is_natural(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Large integer whose /10 value fits the pot is flagged suspicious."""
        # 19804 -> 1980 fits pot=1000, flagged suspicious
        assert estimator._is_suspicious_bet_amount(19804, 5000, 4950, 1000) is True

        # 5952 -> 595 fits pot=500, flagged suspicious
        assert estimator._is_suspicious_bet_amount(5952, 6000, 5400, 500) is True


class TestSuspiciousAllInReclassification:
    """Tests that suspicious bets skip ALL_IN reclassification."""

    def test_suspicious_bet_skips_all_in_reclassify(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Suspicious bet does NOT get reclassified to ALL_IN, gets confidence=low."""
        previous = make_state(
            pot=500,
            player_values={"2": (6000, 0)},
        )
        current = make_state(
            pot=500,
            player_values={"2": (5400, 5952)},
        )

        result = estimator.estimate(previous, current)

        actions = action_tuples(result)
        # Seat 2 should be BET (not ALL_IN) with low confidence
        assert len(actions) == 1
        assert actions[0] == (2, "BET", 5952, "low")

    def test_normal_all_in_still_works(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Normal all-in (stack=0, bet large) is preserved as ALL_IN with high conf."""
        previous = make_state(
            pot=1000,
            player_values={"2": (10000, 0)},
        )
        current = make_state(
            pot=1000,
            player_values={"2": (0, 9500)},
        )

        result = estimator.estimate(previous, current)

        actions = action_tuples(result)
        assert len(actions) == 1
        assert actions[0] == (2, "ALL_IN", 9500, "high")


# ---------------------------------------------------------------------------
# Phase 30-Fix35: Refined suspicious detection tests
# ---------------------------------------------------------------------------


class TestRefinedSuspiciousDetection:
    """Verify that normal bet amounts are no longer flagged suspicious."""

    def test_small_bets_are_not_suspicious(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Bets ≤ 5 BB are never suspicious."""
        for amount in (50, 100, 200, 448, 500):
            assert estimator._is_suspicious_bet_amount(
                amount, 5000, 5000 - amount, 1000,
            ) is False, f"bet_curr={amount} should not be suspicious"

    def test_stack_drop_matching_bet_is_not_suspicious(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """When bet matches the actual stack decrease, it is NOT suspicious."""
        # bet_curr=1100, stack dropped by 1100 — consistent
        assert estimator._is_suspicious_bet_amount(
            1100, 5000, 3900, 800,
        ) is False

        # bet_curr=1600, stack dropped by 1600 — consistent
        assert estimator._is_suspicious_bet_amount(
            1600, 5000, 3400, 1200,
        ) is False

    def test_5952_still_suspicious_with_small_stack_drop(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """5952 with small stack drop (e.g. 600) is still suspicious."""
        # stack_drop=600, bet_curr=5952 — huge mismatch
        assert estimator._is_suspicious_bet_amount(
            5952, 6000, 5400, 500,
        ) is True

    def test_19804_still_suspicious_with_small_stack_drop(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """19804 with small stack drop (e.g. 50) is still suspicious."""
        # stack_drop=50, bet_curr=19804 — huge mismatch
        assert estimator._is_suspicious_bet_amount(
            19804, 5000, 4950, 1000,
        ) is True

    def test_suspicious_still_skips_all_in_reclassify(
        self,
        estimator: ActionEstimator,
    ) -> None:
        """Suspicious bet → ALL_IN reclassification skipped, confidence=low."""
        previous = make_state(
            pot=500,
            player_values={"2": (6000, 0)},
        )
        current = make_state(
            pot=500,
            player_values={"2": (5400, 5952)},
        )

        result = estimator.estimate(previous, current)

        actions = action_tuples(result)
        assert len(actions) == 1
        assert actions[0] == (2, "BET", 5952, "low")
