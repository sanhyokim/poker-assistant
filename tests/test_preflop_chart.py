"""Tests for preflop chart lookup and range parsing."""

from __future__ import annotations

from typing import Any

import pytest

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


def test_recommendation_btn_vs_raise_aks() -> None:
    """BTN AKs facing a raise recommends 3bet (not fallback)."""
    recommendation = PreflopChart().get_recommendation(
        "BTN",
        "AsKs",
        "vs_raise",
        current_max_bet=200,
    )

    assert recommendation["action"] == "3bet"
    assert recommendation["source"] == "preflop_chart"


def test_recommendation_btn_vs_raise_ato() -> None:
    """BTN ATo facing a raise recommends call."""
    recommendation = PreflopChart().get_recommendation(
        "BTN",
        "AhTc",
        "vs_raise",
        current_max_bet=200,
    )

    assert recommendation["action"] == "call"
    assert recommendation["source"] == "preflop_chart"


def test_recommendation_co_vs_raise_fold_weak() -> None:
    """CO 72o facing a raise recommends fold."""
    recommendation = PreflopChart().get_recommendation(
        "CO",
        "7h2c",
        "vs_raise",
        current_max_bet=200,
    )

    assert recommendation["action"] == "fold"


def test_recommendation_utg_vs_raise_aks() -> None:
    """UTG AKs facing a raise recommends 4bet."""
    recommendation = PreflopChart().get_recommendation(
        "UTG",
        "AsKs",
        "vs_raise",
        current_max_bet=300,
    )

    assert recommendation["action"] == "4bet"


def test_recommendation_mp_vs_raise_jj() -> None:
    """MP JJ facing a raise recommends call."""
    recommendation = PreflopChart().get_recommendation(
        "MP",
        "JsJc",
        "vs_raise",
        current_max_bet=200,
    )

    assert recommendation["action"] == "call"


def test_scenario_fallback_to_vs_raise() -> None:
    """Unknown position-specific raise scenario falls back to vs_raise."""
    recommendation = PreflopChart().get_recommendation("BTN", "AsKs", "vs_UTG_raise")

    assert recommendation["source"] == "preflop_chart"
    assert recommendation["action"] in {"3bet", "4bet", "call"}


def test_scenario_fallback_chain_to_vs_3bet() -> None:
    """Fallback chain can recover from an unsupported position-specific raise."""
    recommendation = PreflopChart().get_recommendation("UTG", "AsAc", "vs_MP_raise")

    assert recommendation["source"] == "preflop_chart"
    assert recommendation["action"] in {"4bet", "call"}


def test_load_chart_vs_raise_exists() -> None:
    """All non-blind positions have vs_raise in the chart."""
    chart = PreflopChart()
    data = chart.chart["6max"]

    for position in ["UTG", "MP", "CO", "BTN"]:
        assert "vs_raise" in data[position], f"{position} missing vs_raise"


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


class TestResolveAllInScenario:
    """Test _resolve_all_in_scenario with various stack depths."""

    def test_short_stack_at_boundary(self) -> None:
        """20BB exactly should resolve to vs_all_in_short."""
        result = PreflopChart._resolve_all_in_scenario(20.0)

        assert result == "vs_all_in_short"

    def test_short_stack_below(self) -> None:
        """10BB should resolve to vs_all_in_short."""
        result = PreflopChart._resolve_all_in_scenario(10.0)

        assert result == "vs_all_in_short"

    def test_medium_stack_at_lower_boundary(self) -> None:
        """21BB should resolve to vs_all_in_medium."""
        result = PreflopChart._resolve_all_in_scenario(21.0)

        assert result == "vs_all_in_medium"

    def test_medium_stack_at_upper_boundary(self) -> None:
        """50BB exactly should resolve to vs_all_in_medium."""
        result = PreflopChart._resolve_all_in_scenario(50.0)

        assert result == "vs_all_in_medium"

    def test_deep_stack(self) -> None:
        """51BB should resolve to vs_all_in_deep."""
        result = PreflopChart._resolve_all_in_scenario(51.0)

        assert result == "vs_all_in_deep"

    def test_very_deep_stack(self) -> None:
        """200BB should resolve to vs_all_in_deep."""
        result = PreflopChart._resolve_all_in_scenario(200.0)

        assert result == "vs_all_in_deep"

    def test_none_returns_vs_all_in(self) -> None:
        """None effective stack should return vs_all_in as the fallback."""
        result = PreflopChart._resolve_all_in_scenario(None)

        assert result == "vs_all_in"

    def test_custom_thresholds(self) -> None:
        """Custom thresholds should be respected."""
        result = PreflopChart._resolve_all_in_scenario(15.0, 10.0, 30.0)
        result2 = PreflopChart._resolve_all_in_scenario(5.0, 10.0, 30.0)

        assert result == "vs_all_in_medium"
        assert result2 == "vs_all_in_short"


class TestAllInStackRouting:
    """Test get_recommendation routes to correct stack-depth scenario."""

    @pytest.fixture
    def chart(self) -> PreflopChart:
        """Return a preflop chart fixture."""
        return PreflopChart("preflop_charts/6max_gto.json")

    def test_short_stack_wider_call_range(self, chart: PreflopChart) -> None:
        """Short stack 15BB: 55 should call from the short all-in range."""
        result = chart.get_recommendation(
            "UTG",
            "5h5d",
            "vs_all_in",
            current_max_bet=1500,
            blind_bb=100,
            effective_stack_bb=15.0,
        )

        assert result["action"] == "call"

    def test_deep_stack_narrow_call_range(self, chart: PreflopChart) -> None:
        """Deep stack 80BB: 55 should fold outside the deep all-in range."""
        result = chart.get_recommendation(
            "UTG",
            "5h5d",
            "vs_all_in",
            current_max_bet=8000,
            blind_bb=100,
            effective_stack_bb=80.0,
        )

        assert result["action"] == "fold"

    def test_medium_stack_boundary(self, chart: PreflopChart) -> None:
        """Medium stack 35BB: ATs should call from the medium all-in range."""
        result = chart.get_recommendation(
            "UTG",
            "AsTs",
            "vs_all_in",
            current_max_bet=3500,
            blind_bb=100,
            effective_stack_bb=35.0,
        )

        assert result["action"] == "call"

    def test_none_stack_uses_legacy_range(self, chart: PreflopChart) -> None:
        """None stack: 55 should fold outside the legacy all-in range."""
        result = chart.get_recommendation(
            "UTG",
            "5h5d",
            "vs_all_in",
            current_max_bet=5000,
            blind_bb=100,
            effective_stack_bb=None,
        )

        assert result["action"] == "fold"

    def test_premium_hand_calls_at_all_depths(self, chart: PreflopChart) -> None:
        """AA should call at any stack depth."""
        for stack_bb in [10.0, 35.0, 80.0, None]:
            result = chart.get_recommendation(
                "BTN",
                "AhAd",
                "vs_all_in",
                current_max_bet=5000,
                blind_bb=100,
                effective_stack_bb=stack_bb,
            )

            assert result["action"] == "call", f"AA should call at {stack_bb}BB"


class TestAllInFallbackScenarios:
    """Test fallback chain for stack-specific all-in scenarios."""

    def test_short_falls_back_to_vs_all_in(self) -> None:
        """vs_all_in_short falls back to legacy vs_all_in."""
        assert PreflopChart._get_fallback_scenarios("vs_all_in_short") == [
            "vs_all_in",
        ]

    def test_medium_falls_back_to_vs_all_in(self) -> None:
        """vs_all_in_medium falls back to legacy vs_all_in."""
        assert PreflopChart._get_fallback_scenarios("vs_all_in_medium") == [
            "vs_all_in",
        ]

    def test_deep_falls_back_to_vs_all_in(self) -> None:
        """vs_all_in_deep falls back to legacy vs_all_in."""
        assert PreflopChart._get_fallback_scenarios("vs_all_in_deep") == [
            "vs_all_in",
        ]


class TestAllInConfigThresholds:
    """Test PreflopChart reads config thresholds."""

    def test_default_thresholds_without_config(self) -> None:
        """Without config, default thresholds 20/50 are used."""
        chart = PreflopChart("preflop_charts/6max_gto.json")

        assert chart._all_in_threshold_short == 20.0
        assert chart._all_in_threshold_medium == 50.0

    def test_custom_thresholds_from_config(self) -> None:
        """Config thresholds should override defaults."""
        config = {
            "preflop_chart": {
                "path": "preflop_charts/6max_gto.json",
                "all_in_stack_threshold_short": 15,
                "all_in_stack_threshold_medium": 40,
            },
        }
        chart = PreflopChart("preflop_charts/6max_gto.json", config=config)

        assert chart._all_in_threshold_short == 15.0
        assert chart._all_in_threshold_medium == 40.0
