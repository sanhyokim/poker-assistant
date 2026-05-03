"""Tests for automatic poker position calculation."""

import logging

import pytest

import core.position_calculator as position_calculator
from core.position_calculator import calculate_positions, get_hero_position


class TestSixPlayers:
    """Tests for six-player position assignment."""

    def test_dealer_seat_1(self) -> None:
        """BTN is seat 1, action order descends."""
        positions = calculate_positions(
            dealer_seat=1,
            active_seats=[1, 2, 3, 4, 5, 6],
        )
        assert positions == {
            1: "BTN",
            6: "SB",
            5: "BB",
            4: "UTG",
            3: "MP",
            2: "CO",
        }

    def test_dealer_seat_2(self) -> None:
        """BTN is seat 2, action order is 2,1,6,5,4,3."""
        positions = calculate_positions(
            dealer_seat=2,
            active_seats=[1, 2, 3, 4, 5, 6],
        )
        assert positions == {
            2: "BTN",
            1: "SB",
            6: "BB",
            5: "UTG",
            4: "MP",
            3: "CO",
        }

    def test_dealer_seat_3(self) -> None:
        """BTN is seat 3."""
        positions = calculate_positions(
            dealer_seat=3,
            active_seats=[1, 2, 3, 4, 5, 6],
        )
        assert positions == {
            3: "BTN",
            2: "SB",
            1: "BB",
            6: "UTG",
            5: "MP",
            4: "CO",
        }

    def test_dealer_seat_6(self) -> None:
        """BTN is seat 6 with wraparound."""
        positions = calculate_positions(
            dealer_seat=6,
            active_seats=[1, 2, 3, 4, 5, 6],
        )
        assert positions == {
            6: "BTN",
            5: "SB",
            4: "BB",
            3: "UTG",
            2: "MP",
            1: "CO",
        }


class TestFivePlayers:
    """Tests for five-player position assignment."""

    def test_five_players_seat4_missing(self) -> None:
        """Seat 4 is missing and BTN is seat 1."""
        positions = calculate_positions(
            dealer_seat=1,
            active_seats=[1, 2, 3, 5, 6],
        )
        assert positions == {
            1: "BTN",
            6: "SB",
            5: "BB",
            3: "UTG",
            2: "CO",
        }

    def test_five_players_seat1_missing(self) -> None:
        """Hero seat 1 is missing and BTN is seat 2."""
        positions = calculate_positions(
            dealer_seat=2,
            active_seats=[2, 3, 4, 5, 6],
        )
        assert positions == {
            2: "BTN",
            6: "SB",
            5: "BB",
            4: "UTG",
            3: "CO",
        }

    def test_five_players_dealer_seat_5(self) -> None:
        """BTN is seat 5 and seat 3 is missing."""
        positions = calculate_positions(
            dealer_seat=5,
            active_seats=[1, 2, 4, 5, 6],
        )
        assert positions == {
            5: "BTN",
            4: "SB",
            2: "BB",
            1: "UTG",
            6: "CO",
        }


class TestFourPlayers:
    """Tests for four-player position assignment."""

    def test_four_players(self) -> None:
        """Four players with BTN on seat 1."""
        positions = calculate_positions(
            dealer_seat=1,
            active_seats=[1, 3, 4, 6],
        )
        assert positions == {
            1: "BTN",
            6: "SB",
            4: "BB",
            3: "CO",
        }

    def test_four_players_dealer_seat_4(self) -> None:
        """Four players with BTN on seat 4."""
        positions = calculate_positions(
            dealer_seat=4,
            active_seats=[1, 2, 4, 6],
        )
        assert positions == {
            4: "BTN",
            2: "SB",
            1: "BB",
            6: "CO",
        }

    def test_four_players_dealer4_live_order(self) -> None:
        """Four players: dealer=4, active order is 4,3,1,6."""
        positions = calculate_positions(
            dealer_seat=4,
            active_seats=[1, 3, 4, 6],
        )
        assert positions == {
            4: "BTN",
            3: "SB",
            1: "BB",
            6: "CO",
        }


class TestThreePlayers:
    """Tests for three-player position assignment."""

    def test_three_players(self) -> None:
        """Three players with BTN on seat 1."""
        positions = calculate_positions(
            dealer_seat=1,
            active_seats=[1, 3, 5],
        )
        assert positions == {1: "BTN", 5: "SB", 3: "BB"}

    def test_three_players_dealer_seat_5(self) -> None:
        """Three players with BTN on seat 5."""
        positions = calculate_positions(
            dealer_seat=5,
            active_seats=[1, 2, 5],
        )
        assert positions == {5: "BTN", 2: "SB", 1: "BB"}

    def test_three_players_dealer2(self) -> None:
        """Three players: dealer=2, active order is 2,1,5."""
        positions = calculate_positions(
            dealer_seat=2,
            active_seats=[1, 2, 5],
        )
        assert positions == {2: "BTN", 1: "SB", 5: "BB"}


class TestHeadsUp:
    """Tests for heads-up position assignment."""

    def test_heads_up_hero_btn(self) -> None:
        """Hero is BTN."""
        positions = calculate_positions(
            dealer_seat=1,
            active_seats=[1, 4],
        )
        assert positions == {1: "BTN", 4: "BB"}

    def test_heads_up_hero_bb(self) -> None:
        """Hero is BB."""
        positions = calculate_positions(
            dealer_seat=4,
            active_seats=[1, 4],
        )
        assert positions == {4: "BTN", 1: "BB"}

    def test_heads_up_adjacent_seats(self) -> None:
        """Heads-up with adjacent seats."""
        positions = calculate_positions(
            dealer_seat=2,
            active_seats=[1, 2],
        )
        assert positions == {2: "BTN", 1: "BB"}


class TestGetHeroPosition:
    """Tests for get_hero_position."""

    def test_hero_has_position(self) -> None:
        """Hero has a position when participating in the hand."""
        positions = calculate_positions(
            dealer_seat=3,
            active_seats=[1, 2, 3, 4, 5, 6],
        )
        assert get_hero_position(positions) == "BB"

    def test_hero_btn(self) -> None:
        """Hero position is BTN."""
        positions = calculate_positions(
            dealer_seat=1,
            active_seats=[1, 2, 3, 4, 5, 6],
        )
        assert get_hero_position(positions) == "BTN"

    def test_hero_not_in_hand(self) -> None:
        """Hero position is None when hero is not active."""
        positions = calculate_positions(
            dealer_seat=2,
            active_seats=[2, 3, 4, 5, 6],
        )
        assert get_hero_position(positions) is None

    def test_empty_positions(self) -> None:
        """Empty positions return None for hero."""
        assert get_hero_position({}) is None


class TestEdgeCases:
    """Tests for edge cases."""

    def test_dealer_seat_none(self) -> None:
        """dealer_seat=None returns an empty mapping."""
        positions = calculate_positions(
            dealer_seat=None,
            active_seats=[1, 2, 3],
        )
        assert positions == {}

    def test_empty_active_seats(self) -> None:
        """No active seats returns an empty mapping."""
        positions = calculate_positions(
            dealer_seat=1,
            active_seats=[],
        )
        assert positions == {}

    def test_single_player(self) -> None:
        """A single active player returns an empty mapping."""
        positions = calculate_positions(
            dealer_seat=1,
            active_seats=[1],
        )
        assert positions == {}

    def test_dealer_not_in_active_seats(self) -> None:
        """Physical dealer position is used when dealer is inactive."""
        positions = calculate_positions(
            dealer_seat=3,
            active_seats=[1, 2, 4, 5, 6],
        )
        assert positions[2] == "BTN"
        assert len(positions) == 5

    def test_dealer_seat_5_empty_active_1_2_3_4(self) -> None:
        """Dealer on empty seat 5 makes seat 4 the next active BTN."""
        positions = calculate_positions(
            dealer_seat=5,
            active_seats=[1, 2, 3, 4],
        )
        assert positions == {
            4: "BTN",
            3: "SB",
            2: "BB",
            1: "CO",
        }

    def test_dealer_seat_6_active_1_3_4(self) -> None:
        """Dealer on empty seat 6 makes seat 4 BTN in a three-player hand."""
        positions = calculate_positions(
            dealer_seat=6,
            active_seats=[1, 3, 4],
        )
        assert positions == {4: "BTN", 3: "SB", 1: "BB"}

    def test_dealer_seat_3_active_1_5_heads_up(self) -> None:
        """Dealer on empty seat 3 makes seat 1 BTN in heads-up."""
        positions = calculate_positions(
            dealer_seat=3,
            active_seats=[1, 5],
        )
        assert positions == {1: "BTN", 5: "BB"}

    def test_live_screenshot_dealer3_seat2_empty(self) -> None:
        """Live case: dealer=3, seat 2 empty, action order is 3,1,6,5,4."""
        positions = calculate_positions(
            dealer_seat=3,
            active_seats=[1, 3, 4, 5, 6],
        )
        assert positions == {
            3: "BTN",
            1: "SB",
            6: "BB",
            5: "UTG",
            4: "CO",
        }

    def test_live_screenshot_dealer6_all_active(self) -> None:
        """Live case: dealer=6, seat 5 is SB and seat 4 is BB."""
        positions = calculate_positions(
            dealer_seat=6,
            active_seats=[1, 2, 3, 4, 5, 6],
        )
        assert positions[6] == "BTN"
        assert positions[5] == "SB"
        assert positions[4] == "BB"

    def test_dealer_empty_seat_5_active_1_2_3_4_6(self) -> None:
        """Dealer on empty seat 5 makes seat 4 BTN in a five-player hand."""
        positions = calculate_positions(
            dealer_seat=5,
            active_seats=[1, 2, 3, 4, 6],
        )
        assert positions == {
            4: "BTN",
            3: "SB",
            2: "BB",
            1: "UTG",
            6: "CO",
        }

    def test_active_seats_unsorted(self) -> None:
        """active_seats order does not affect results."""
        positions = calculate_positions(
            dealer_seat=1,
            active_seats=[5, 1, 3, 6, 2, 4],
        )
        assert positions == {
            1: "BTN",
            6: "SB",
            5: "BB",
            4: "UTG",
            3: "MP",
            2: "CO",
        }

    def test_positions_consistent_across_calls(self) -> None:
        """Same input returns the same positions across calls."""
        kwargs = {
            "dealer_seat": 3,
            "active_seats": [1, 2, 3, 5, 6],
        }
        result_1 = calculate_positions(**kwargs)
        result_2 = calculate_positions(**kwargs)
        assert result_1 == result_2

    def test_dealer_not_in_active_seats_debug_suppressed(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Repeated inactive-dealer debug messages are logged once per pattern."""
        position_calculator._last_dealer_warning = None

        with caplog.at_level(logging.DEBUG, logger="core.position_calculator"):
            calculate_positions(2, [1, 3, 4, 5])
            calculate_positions(2, [1, 3, 4, 5])
            calculate_positions(2, [1, 3, 4, 5])
            calculate_positions(3, [1, 4, 5])

        messages = [
            record.getMessage()
            for record in caplog.records
            if "not in active seats" in record.getMessage()
        ]
        assert len(messages) == 2
        assert "Dealer seat 2 not in active seats [1, 3, 4, 5]" in messages[0]
        assert "Dealer seat 3 not in active seats [1, 4, 5]" in messages[1]
        assert all(record.levelno == logging.DEBUG for record in caplog.records)
