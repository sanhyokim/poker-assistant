"""Tests for preflop chart lookup and range parsing."""

from __future__ import annotations

from typing import Any

from strategy.preflop_chart import PreflopChart


def test_load_chart() -> None:
    """JSON chart loads and contains all required scenarios."""
    chart = PreflopChart()
    data = chart.chart["6max"]

    for position in ["UTG", "MP", "CO", "BTN", "SB", "BB"]:
        assert position in data
    for position in ["UTG", "MP", "CO", "BTN", "SB"]:
        assert "RFI" in data[position]
        assert "vs_3bet" in data[position]
        assert "vs_limp" in data[position]
        assert "vs_all_in" in data[position]
    for scenario in [
        "vs_UTG_raise",
        "vs_MP_raise",
        "vs_CO_raise",
        "vs_BTN_raise",
    ]:
        assert scenario in data["SB"]
    for scenario in [
        "vs_UTG_raise",
        "vs_MP_raise",
        "vs_CO_raise",
        "vs_BTN_raise",
        "vs_SB_raise",
    ]:
        assert scenario in data["BB"]
    assert "vs_limp" in data["BB"]
    assert "vs_3bet" in data["BB"]
    assert "vs_all_in" in data["BB"]


def test_hand_to_generic_offsuit() -> None:
    """AhKs converts to AKo."""
    assert PreflopChart.hand_to_generic("Ah", "Ks") == "AKo"


def test_hand_to_generic_suited() -> None:
    """AhKh converts to AKs."""
    assert PreflopChart.hand_to_generic("Ah", "Kh") == "AKs"


def test_hand_to_generic_pair() -> None:
    """AsAc converts to AA."""
    assert PreflopChart.hand_to_generic("As", "Ac") == "AA"


def test_hand_to_generic_reverse_order() -> None:
    """Reverse concrete card order still converts by rank strength."""
    assert PreflopChart.hand_to_generic("Ks", "Ah") == "AKo"


def test_hand_in_range_exact() -> None:
    """Exact hand entries are matched."""
    assert PreflopChart.hand_in_range("AKo", "AKo,AQo")


def test_hand_in_range_plus() -> None:
    """Pair plus ranges include higher pairs."""
    assert PreflopChart.hand_in_range("TT", "77+")


def test_hand_in_range_plus_below() -> None:
    """Pair plus ranges exclude lower pairs."""
    assert not PreflopChart.hand_in_range("55", "77+")


def test_hand_in_range_dash() -> None:
    """Pair dash ranges include middle pairs."""
    assert PreflopChart.hand_in_range("99", "JJ-77")


def test_hand_in_range_suited_plus() -> None:
    """Suited plus ranges include higher kickers with fixed high card."""
    assert PreflopChart.hand_in_range("AJs", "ATs+")


def test_hand_in_range_suited_plus_below() -> None:
    """Suited plus ranges exclude lower kickers."""
    assert not PreflopChart.hand_in_range("A9s", "ATs+")


def test_hand_in_range_offsuit_dash() -> None:
    """Offsuit dash ranges include all kickers between endpoints."""
    assert PreflopChart.hand_in_range("ATo", "AJo-A9o")


def test_recommendation_utg_rfi_raise() -> None:
    """UTG AA RFI recommends raise."""
    recommendation = PreflopChart().get_recommendation("UTG", "AsAc", "RFI")

    assert recommendation["action"] == "raise"
    assert recommendation["amount"] == 300
    assert recommendation["confidence"] == "high"
    assert recommendation["source"] == "preflop_chart"


def test_recommendation_utg_rfi_fold() -> None:
    """UTG 72o RFI recommends fold."""
    recommendation = PreflopChart().get_recommendation("UTG", "7h2c", "RFI")

    assert recommendation["action"] == "fold"


def test_recommendation_bb_vs_btn() -> None:
    """BB JJ versus BTN raise recommends 3bet."""
    recommendation = PreflopChart().get_recommendation("BB", "JsJc", "vs_BTN_raise")

    assert recommendation["action"] == "3bet"
    assert recommendation["amount"] == 300


def test_recommendation_raise_amount_uses_current_max_bet() -> None:
    """Facing a raise, chart raise sizes use three times the current max bet."""
    recommendation = PreflopChart().get_recommendation(
        "BB",
        "JsJc",
        "vs_BTN_raise",
        current_max_bet=250,
        blind_bb=100,
    )

    assert recommendation["action"] == "3bet"
    assert recommendation["amount"] == 750


def test_recommendation_rfi_amount_uses_big_blind() -> None:
    """RFI raises use three big blinds."""
    recommendation = PreflopChart().get_recommendation(
        "UTG",
        "AsAc",
        "RFI",
        blind_bb=50,
    )

    assert recommendation["action"] == "raise"
    assert recommendation["amount"] == 150


def test_recommendation_bb_call() -> None:
    """BB 98s versus BTN raise recommends call."""
    recommendation = PreflopChart().get_recommendation("BB", "9h8h", "vs_BTN_raise")

    assert recommendation["action"] == "call"


def test_recommendation_bb_vs_limp_check() -> None:
    """BB 96s versus limpers checks when outside raise and call ranges."""
    recommendation = PreflopChart().get_recommendation("BB", "9h6h", "vs_limp")

    assert recommendation["action"] == "check"
    assert recommendation["amount"] == 0
    assert recommendation["source"] == "preflop_chart"


def test_recommendation_bb_vs_limp_raise() -> None:
    """BB AKo versus limpers raises."""
    recommendation = PreflopChart().get_recommendation("BB", "AhKd", "vs_limp")

    assert recommendation["action"] == "raise"


def test_recommendation_bb_vs_3bet_66_folds() -> None:
    """BB 66 versus a 3bet folds."""
    recommendation = PreflopChart().get_recommendation("BB", "6h6s", "vs_3bet")

    assert recommendation["action"] == "fold"


def test_recommendation_bb_vs_3bet_qq_4bets() -> None:
    """BB QQ versus a 3bet recommends 4bet."""
    recommendation = PreflopChart().get_recommendation("BB", "QhQs", "vs_3bet")

    assert recommendation["action"] == "4bet"


def test_recommendation_bb_vs_3bet_99_calls() -> None:
    """BB 99 versus a 3bet calls."""
    recommendation = PreflopChart().get_recommendation("BB", "9h9s", "vs_3bet")

    assert recommendation["action"] == "call"


def test_recommendation_vs_all_in_folds_weak_hand() -> None:
    """Weak hands fold against an opponent all-in."""
    recommendation = PreflopChart().get_recommendation(
        "BB",
        "8c6c",
        "vs_all_in",
        current_max_bet=9984,
    )

    assert recommendation["action"] == "fold"
    assert recommendation["amount"] == 0


def test_recommendation_vs_all_in_calls_premium_hand() -> None:
    """Premium hands call against an opponent all-in."""
    recommendation = PreflopChart().get_recommendation(
        "BB",
        "AsAc",
        "vs_all_in",
        current_max_bet=9984,
    )

    assert recommendation["action"] == "call"
    assert recommendation["amount"] == 9984


def test_recommendation_sb_vs_btn_raise_3bet() -> None:
    """SB AA versus BTN raise recommends 3bet."""
    recommendation = PreflopChart().get_recommendation("SB", "AsAc", "vs_BTN_raise")

    assert recommendation["action"] == "3bet"


def test_recommendation_unknown_scenario() -> None:
    """Unknown scenarios fall back to low-confidence fold."""
    recommendation = PreflopChart().get_recommendation("UTG", "AsAc", "unknown")

    assert recommendation == {
        "action": "fold",
        "amount": 0,
        "confidence": "low",
        "source": "preflop_chart_fallback",
        "range": None,
        "reason": "該当シナリオなし",
    }


def test_get_scenario_rfi() -> None:
    """Empty action history means RFI."""
    assert PreflopChart.get_scenario("CO", []) == "RFI"


def test_get_scenario_vs_limp() -> None:
    """Limp-only history maps to vs_limp."""
    history: list[dict[str, Any]] = [{"position": "CO", "action": "CALL"}]

    assert PreflopChart.get_scenario("BB", history) == "vs_limp"


def test_get_scenario_hero_raise_then_opponent_reraise() -> None:
    """Opponent reraise after a hero raise maps to vs_3bet."""
    history: list[dict[str, Any]] = [
        {"seat": 1, "position": "BB", "action": "RAISE", "amount": 300},
        {"seat": 4, "position": "BTN", "action": "RAISE", "amount": 900},
    ]

    assert PreflopChart.get_scenario("BB", history) == "vs_3bet"


def test_get_scenario_limp_hero_raise_then_opponent_reraise() -> None:
    """Limp, hero isolation raise, and reraise maps to vs_3bet."""
    history: list[dict[str, Any]] = [
        {"seat": 3, "position": "CO", "action": "CALL", "amount": 100},
        {"seat": 1, "position": "BB", "action": "RAISE", "amount": 300},
        {"seat": 3, "position": "CO", "action": "RAISE", "amount": 900},
    ]

    scenario = PreflopChart.get_scenario("BB", history)
    recommendation = PreflopChart().get_recommendation("BB", "6h6s", scenario)

    assert scenario == "vs_3bet"
    assert recommendation["action"] == "fold"


def test_get_scenario_limp_then_opponent_raise_before_hero() -> None:
    """Limp followed by an opponent raise before hero maps to a raise scenario."""
    history: list[dict[str, Any]] = [
        {"seat": 3, "position": "CO", "action": "CALL", "amount": 100},
        {"seat": 4, "position": "BTN", "action": "RAISE", "amount": 400},
    ]

    scenario = PreflopChart.get_scenario("BB", history)

    assert scenario.startswith("vs_")
    assert "raise" in scenario


def test_get_scenario_opponent_all_in_returns_vs_all_in() -> None:
    """Opponent all-in takes priority over regular raise scenarios."""
    history: list[dict[str, Any]] = [
        {"seat": 5, "position": "MP", "action": "ALL_IN", "amount": 9868},
        {"seat": 2, "position": "SB", "action": "ALL_IN", "amount": 9984},
    ]

    assert PreflopChart.get_scenario("BB", history) == "vs_all_in"


def test_is_raise_action_includes_all_in() -> None:
    """ALL_IN is treated as a raise-like preflop action."""
    assert PreflopChart._is_raise_action("ALL_IN")
    assert PreflopChart._is_raise_action("all-in")


def test_get_scenario_bb_vs_btn() -> None:
    """BB facing a BTN raise maps to vs_BTN_raise."""
    history: list[dict[str, Any]] = [{"position": "BTN", "action": "raise"}]

    assert PreflopChart.get_scenario("BB", history) == "vs_BTN_raise"


def test_get_scenario_sb_vs_btn() -> None:
    """SB facing a BTN raise maps to vs_BTN_raise."""
    history: list[dict[str, Any]] = [{"position": "BTN", "action": "raise"}]

    assert PreflopChart.get_scenario("SB", history) == "vs_BTN_raise"


def test_get_scenario_vs_3bet() -> None:
    """Hero RFI followed by another raise maps to vs_3bet."""
    history: list[dict[str, Any]] = [
        {"position": "CO", "action": "raise"},
        {"position": "BTN", "action": "3bet"},
    ]

    assert PreflopChart.get_scenario("CO", history) == "vs_3bet"
