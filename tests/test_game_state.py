"""Tests for GameState structures and state-diff utilities."""

import json

from core.game_state import (
    ActionRecord,
    ButtonState,
    GameState,
    HeroState,
    PlayerState,
    compute_state_diff,
    create_empty_game_state,
)


class TestGameStateCreation:
    """Tests for GameState creation and defaults."""

    def test_create_empty(self) -> None:
        """create_empty_game_state creates the expected waiting state."""
        game_state = create_empty_game_state()

        assert game_state.phase == "waiting"
        assert game_state.hand_id is None
        assert game_state.table_visible is False
        assert game_state.hero.seat == 1
        assert game_state.hero.position is None
        assert game_state.hero.cards is None
        assert game_state.hero.cards_visible is False
        assert game_state.hero.is_my_turn is False
        assert game_state.hero.in_current_hand is False
        assert game_state.hero.has_folded is False
        assert game_state.board == []
        assert game_state.board_card_count == 0
        assert game_state.pot == 0
        assert game_state.dealer_seat is None
        assert game_state.active_player_count == 0
        assert game_state.buttons is None
        assert game_state.actions_since_last_frame == []
        assert game_state.hero_action is None
        assert game_state.game_event is None

    def test_default_players_seats_2_to_6(self) -> None:
        """Default players are created for seats 2 through 6."""
        game_state = create_empty_game_state()

        assert set(game_state.players.keys()) == {"2", "3", "4", "5", "6"}
        for seat_key in ["2", "3", "4", "5", "6"]:
            player = game_state.players[seat_key]
            assert player.name is None
            assert player.stack is None
            assert player.bet == 0
            assert player.is_seated is False
            assert player.cards_visible is False
            assert player.in_current_hand is False

    def test_hero_not_in_players(self) -> None:
        """Hero seat 1 is stored separately from players."""
        game_state = create_empty_game_state()

        assert "1" not in game_state.players


class TestGameStateToDict:
    """Tests for to_dict and JSON serialization."""

    def test_to_dict_basic(self) -> None:
        """Basic to_dict returns a dictionary."""
        game_state = create_empty_game_state()
        data = game_state.to_dict()

        assert isinstance(data, dict)
        assert data["phase"] == "waiting"
        assert data["hand_id"] is None
        assert data["table_visible"] is False
        assert data["pot"] == 0

    def test_to_dict_json_serializable(self) -> None:
        """to_dict output can be serialized to JSON."""
        game_state = create_empty_game_state()
        game_state.hero.cards = ["Td", "9c"]
        game_state.board = ["8c", "7d", "8d"]
        game_state.board_card_count = 3
        game_state.pot = 348
        game_state.phase = "flop"
        game_state.hand_id = 123

        json_text = json.dumps(game_state.to_dict())
        parsed = json.loads(json_text)

        assert parsed["phase"] == "flop"
        assert parsed["hero"]["cards"] == ["Td", "9c"]
        assert parsed["board"] == ["8c", "7d", "8d"]

    def test_to_dict_with_players(self) -> None:
        """Player data is included in to_dict output."""
        game_state = create_empty_game_state()
        game_state.players["2"].name = "mrkrebs"
        game_state.players["2"].stack = 14439
        game_state.players["2"].is_seated = True
        game_state.players["2"].cards_visible = True
        game_state.players["2"].in_current_hand = True

        player_2 = game_state.to_dict()["players"]["2"]

        assert player_2["name"] == "mrkrebs"
        assert player_2["stack"] == 14439
        assert player_2["is_seated"] is True
        assert player_2["cards_visible"] is True
        assert player_2["in_current_hand"] is True

    def test_to_dict_with_buttons(self) -> None:
        """Button state is included in to_dict output."""
        game_state = create_empty_game_state()
        game_state.buttons = ButtonState(
            fold=True,
            call_or_check="check",
            raise_or_bet="bet",
            bet_size=100,
        )
        data = game_state.to_dict()

        assert data["buttons"]["fold"] is True
        assert data["buttons"]["call_or_check"] == "check"

    def test_to_dict_with_actions(self) -> None:
        """Action history is included in to_dict output."""
        game_state = create_empty_game_state()
        game_state.actions_since_last_frame = [
            ActionRecord(seat=2, action="CHECK", amount=0),
            ActionRecord(seat=3, action="BET", amount=200, confidence="high"),
        ]
        actions = game_state.to_dict()["actions_since_last_frame"]

        assert len(actions) == 2
        assert actions[0]["seat"] == 2
        assert actions[0]["action"] == "CHECK"
        assert actions[1]["amount"] == 200

    def test_to_dict_buttons_none(self) -> None:
        """buttons=None remains None in to_dict output."""
        game_state = create_empty_game_state()
        game_state.buttons = None

        assert game_state.to_dict()["buttons"] is None


class TestGameStateFieldValues:
    """Tests for supported field values."""

    def test_phase_values(self) -> None:
        """All expected phase strings can be assigned."""
        game_state = create_empty_game_state()
        for phase in ["waiting", "preflop", "flop", "turn", "river", "hand_end"]:
            game_state.phase = phase
            assert game_state.phase == phase

    def test_hero_position_values(self) -> None:
        """All expected position strings can be assigned."""
        game_state = create_empty_game_state()
        for position in ["BTN", "SB", "BB", "UTG", "MP", "CO"]:
            game_state.hero.position = position
            assert game_state.hero.position == position

    def test_game_event_values(self) -> None:
        """All expected game event values can be assigned."""
        game_state = create_empty_game_state()
        for event in ["NEW_HAND", "NEW_STREET", "BETS_COLLECTED", None]:
            game_state.game_event = event
            assert game_state.game_event == event

    def test_action_types(self) -> None:
        """All expected action types can be assigned."""
        action_types = [
            "FOLD",
            "CHECK",
            "CALL",
            "BET",
            "RAISE",
            "ALL_IN",
            "BLIND_SB",
            "BLIND_BB",
        ]
        for action_type in action_types:
            action = ActionRecord(seat=2, action=action_type, amount=100)
            assert action.action == action_type


class TestPlayerState:
    """Tests for PlayerState."""

    def test_is_seated_vs_in_current_hand(self) -> None:
        """is_seated and in_current_hand are independently assignable."""
        player = PlayerState(
            name="test",
            stack=1000,
            is_seated=True,
            cards_visible=True,
            in_current_hand=True,
        )

        assert player.is_seated is True
        assert player.cards_visible is True
        assert player.in_current_hand is True

    def test_empty_seat(self) -> None:
        """Default PlayerState represents an empty seat."""
        player = PlayerState()

        assert player.stack is None
        assert player.is_seated is False
        assert player.cards_visible is False
        assert player.in_current_hand is False


class TestHeroState:
    """Tests for HeroState."""

    def test_default_hand_state_flags(self) -> None:
        """Hero hand-state flags default to false."""
        hero = HeroState()

        assert hero.cards_visible is False
        assert hero.in_current_hand is False
        assert hero.has_folded is False


class TestComputeStateDiff:
    """Tests for compute_state_diff."""

    def test_identical_states_no_change(self) -> None:
        """Identical GameStates produce no changes."""
        previous = create_empty_game_state()
        previous.pot = 200
        previous.hero.stack = 1000
        current = create_empty_game_state()
        current.pot = 200
        current.hero.stack = 1000

        diff = compute_state_diff(previous, current)

        assert diff.any_change is False
        assert diff.pot_changed is False
        assert diff.hero_stack_changed is False

    def test_pot_change(self) -> None:
        """Pot changes are detected."""
        previous = create_empty_game_state()
        previous.pot = 200
        current = create_empty_game_state()
        current.pot = 400

        diff = compute_state_diff(previous, current)

        assert diff.pot_changed is True
        assert diff.pot_prev == 200
        assert diff.pot_curr == 400
        assert diff.any_change is True

    def test_board_count_change(self) -> None:
        """Board card count changes are detected."""
        previous = create_empty_game_state()
        current = create_empty_game_state()
        current.board_card_count = 3

        diff = compute_state_diff(previous, current)

        assert diff.board_count_changed is True
        assert diff.board_count_prev == 0
        assert diff.board_count_curr == 3

    def test_hero_stack_change(self) -> None:
        """Hero stack changes are detected."""
        previous = create_empty_game_state()
        previous.hero.stack = 1000
        current = create_empty_game_state()
        current.hero.stack = 800

        diff = compute_state_diff(previous, current)

        assert diff.hero_stack_changed is True
        assert diff.hero_stack_prev == 1000
        assert diff.hero_stack_curr == 800

    def test_hero_bet_change(self) -> None:
        """Hero bet changes are detected."""
        previous = create_empty_game_state()
        current = create_empty_game_state()
        current.hero.bet = 200

        diff = compute_state_diff(previous, current)

        assert diff.hero_bet_changed is True
        assert diff.hero_bet_prev == 0
        assert diff.hero_bet_curr == 200

    def test_is_my_turn_change(self) -> None:
        """Hero turn changes are detected."""
        previous = create_empty_game_state()
        current = create_empty_game_state()
        current.hero.is_my_turn = True

        diff = compute_state_diff(previous, current)

        assert diff.is_my_turn_changed is True
        assert diff.is_my_turn_prev is False
        assert diff.is_my_turn_curr is True

    def test_player_stack_change(self) -> None:
        """Player stack changes are detected."""
        previous = create_empty_game_state()
        previous.players["2"].stack = 5000
        current = create_empty_game_state()
        current.players["2"].stack = 4800

        diff = compute_state_diff(previous, current)

        assert diff.player_changes["2"]["stack_changed"] is True
        assert diff.player_changes["2"]["stack_prev"] == 5000
        assert diff.player_changes["2"]["stack_curr"] == 4800
        assert diff.any_change is True

    def test_player_bet_change(self) -> None:
        """Player bet changes are detected."""
        previous = create_empty_game_state()
        current = create_empty_game_state()
        current.players["3"].bet = 200

        diff = compute_state_diff(previous, current)

        assert diff.player_changes["3"]["bet_changed"] is True
        assert diff.player_changes["3"]["bet_prev"] == 0
        assert diff.player_changes["3"]["bet_curr"] == 200

    def test_player_stack_to_none_fold(self) -> None:
        """Player stack changing to None is detected."""
        previous = create_empty_game_state()
        previous.players["4"].stack = 3000
        current = create_empty_game_state()
        current.players["4"].stack = None

        diff = compute_state_diff(previous, current)

        assert diff.player_changes["4"]["stack_changed"] is True
        assert diff.player_changes["4"]["stack_prev"] == 3000
        assert diff.player_changes["4"]["stack_curr"] is None

    def test_max_bet_calculation(self) -> None:
        """Maximum bet values are calculated across hero and players."""
        previous = create_empty_game_state()
        previous.hero.bet = 100
        previous.players["2"].bet = 200
        current = create_empty_game_state()
        current.hero.bet = 100
        current.players["2"].bet = 200
        current.players["3"].bet = 500

        diff = compute_state_diff(previous, current)

        assert diff.max_bet_prev == 200
        assert diff.max_bet_curr == 500

    def test_multiple_changes(self) -> None:
        """Multiple simultaneous changes are all detected."""
        previous = create_empty_game_state()
        previous.pot = 200
        previous.hero.stack = 1000
        previous.players["2"].stack = 5000
        current = create_empty_game_state()
        current.pot = 400
        current.hero.stack = 800
        current.players["2"].stack = 4800

        diff = compute_state_diff(previous, current)

        assert diff.pot_changed is True
        assert diff.hero_stack_changed is True
        assert diff.player_changes["2"]["stack_changed"] is True
        assert diff.any_change is True

    def test_unchanged_players_no_false_positive(self) -> None:
        """Unchanged player seats do not produce false positives."""
        previous = create_empty_game_state()
        previous.players["2"].stack = 5000
        previous.players["3"].stack = 3000
        current = create_empty_game_state()
        current.players["2"].stack = 4000
        current.players["3"].stack = 3000

        diff = compute_state_diff(previous, current)

        assert diff.player_changes["2"]["stack_changed"] is True
        assert diff.player_changes["3"]["stack_changed"] is False

    def test_all_seats_in_player_changes(self) -> None:
        """player_changes includes all seats 2 through 6."""
        diff = compute_state_diff(
            create_empty_game_state(),
            create_empty_game_state(),
        )

        assert set(diff.player_changes.keys()) == {"2", "3", "4", "5", "6"}


class TestStateDiffEdgeCases:
    """StateDiff edge case tests."""

    def test_both_none_stacks_no_change(self) -> None:
        """Stacks that are both None are unchanged."""
        diff = compute_state_diff(
            create_empty_game_state(),
            create_empty_game_state(),
        )

        for seat_key in ["2", "3", "4", "5", "6"]:
            assert diff.player_changes[seat_key]["stack_changed"] is False

    def test_hero_stack_none_to_value(self) -> None:
        """Hero stack changing from None to a value is detected."""
        previous = create_empty_game_state()
        previous.hero.stack = None
        current = create_empty_game_state()
        current.hero.stack = 5000

        diff = compute_state_diff(previous, current)

        assert diff.hero_stack_changed is True

    def test_max_bet_all_zero(self) -> None:
        """max_bet is zero when all bets are zero."""
        diff = compute_state_diff(
            create_empty_game_state(),
            create_empty_game_state(),
        )

        assert diff.max_bet_prev == 0
        assert diff.max_bet_curr == 0


def test_direct_game_state_can_use_default_players() -> None:
    """GameState.create_default_players initializes independent player states."""
    players = GameState.create_default_players()
    players["2"].stack = 100

    assert players["3"].stack is None
