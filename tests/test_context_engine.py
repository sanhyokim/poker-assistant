"""Tests for the PokerSkill-style context engine."""

from __future__ import annotations

import pytest

from strategy.context_engine import (
    apply_mw_modifiers,
    calculate_cumulative_pressure,
    calculate_pressure_weight,
    classify_board_texture,
    classify_hand,
    compute_full_budget,
    determine_viable_actions,
    get_draw_budget,
    get_made_hand_budget,
    get_special_board_override,
)


dry_board = {
    "suit_texture": "rainbow",
    "rank_texture": [],
    "overall_texture": "dry",
    "special_board": None,
    "flush_possible": False,
    "straight_possible": False,
    "paired": False,
}
paired_board = {
    "suit_texture": "rainbow",
    "rank_texture": ["paired"],
    "overall_texture": "dry",
    "special_board": None,
    "flush_possible": False,
    "straight_possible": False,
    "paired": True,
}
flush_possible_board = {
    "suit_texture": "two_tone",
    "rank_texture": [],
    "overall_texture": "slightly_wet",
    "special_board": None,
    "flush_possible": True,
    "straight_possible": False,
    "paired": False,
}
trips_board_texture = {
    "suit_texture": "rainbow",
    "rank_texture": ["trips_board"],
    "overall_texture": "dry",
    "special_board": "trips_board",
    "flush_possible": False,
    "straight_possible": False,
    "paired": False,
}


def test_board_texture_rainbow_dry() -> None:
    """Rainbow disconnected unpaired board is dry."""
    texture = classify_board_texture(["Ah", "7d", "2c"])

    assert texture["suit_texture"] == "rainbow"
    assert texture["overall_texture"] == "dry"


def test_board_texture_two_tone() -> None:
    """Two suited cards on flop are two-tone."""
    texture = classify_board_texture(["Ah", "Kh", "7d"])

    assert texture["suit_texture"] == "two_tone"


def test_board_texture_monotone_flop() -> None:
    """Three suited flop cards are monotone and flush possible."""
    texture = classify_board_texture(["Ah", "Kh", "7h"])

    assert texture["suit_texture"] == "monotone"
    assert texture["flush_possible"] is True


def test_board_texture_paired() -> None:
    """Paired board is flagged."""
    texture = classify_board_texture(["Ah", "Ad", "7c"])

    assert texture["paired"] is True


def test_board_texture_trips_board() -> None:
    """Trips board is a special board."""
    texture = classify_board_texture(["Ah", "As", "Ad"])

    assert texture["special_board"] == "trips_board"


def test_board_texture_straight_possible() -> None:
    """Connected board is straight possible."""
    texture = classify_board_texture(["9h", "Td", "Jc"])

    assert texture["straight_possible"] is True


def test_board_texture_very_wet() -> None:
    """Monotone connected flop is very wet."""
    texture = classify_board_texture(["9h", "Th", "Jh"])

    assert texture["overall_texture"] == "very_wet"


def test_board_texture_board_flush_5cards() -> None:
    """Five suited board cards make a board flush special board."""
    texture = classify_board_texture(["2h", "5h", "8h", "Jh", "Ah"])

    assert texture["special_board"] == "board_flush"


def test_board_texture_board_straight_5cards() -> None:
    """Five connected board cards make a board straight special board."""
    texture = classify_board_texture(["5d", "6h", "7c", "8s", "9d"])

    assert texture["special_board"] == "board_straight"


def test_board_texture_double_paired() -> None:
    """Two board pairs are a double-paired special board."""
    texture = classify_board_texture(["Ah", "Ad", "7c", "7s"])

    assert texture["special_board"] == "double_paired"


def test_classify_nuts_royal_flush() -> None:
    """Royal flush is classified as nuts."""
    hand = classify_hand(["Ah", "Kh"], ["Qh", "Jh", "Th"])

    assert hand["made_hand_class"] == "nuts"


def test_classify_set() -> None:
    """Pocket pair hitting one board card is a set."""
    hand = classify_hand(["7h", "7d"], ["7c", "Ks", "2d"])

    assert hand["made_hand_class"] == "set"


def test_classify_trips() -> None:
    """One hole card matching a paired board is trips."""
    hand = classify_hand(["7h", "Ad"], ["7c", "7s", "2d"])

    assert hand["made_hand_class"] == "trips"


def test_classify_overpair() -> None:
    """Pocket pair above all board cards is overpair."""
    hand = classify_hand(["Ah", "As"], ["Kd", "7c", "2s"])

    assert hand["made_hand_class"] == "overpair"


def test_classify_top_pair() -> None:
    """Hero A hitting board A is top pair."""
    hand = classify_hand(["Ah", "9d"], ["Ad", "Kc", "7s"])

    assert hand["made_hand_class"] == "top_pair"


def test_classify_second_pair() -> None:
    """Hero K on A-K-7 is second pair."""
    hand = classify_hand(["Kh", "9d"], ["Ad", "Kc", "7s"])

    assert hand["made_hand_class"] == "second_pair"


def test_classify_third_pair() -> None:
    """Hero 7 on A-K-7 is third pair."""
    hand = classify_hand(["7h", "9d"], ["Ad", "Kc", "7s"])

    assert hand["made_hand_class"] == "third_pair"


def test_classify_two_pair() -> None:
    """Hero hitting two board ranks is two pair."""
    hand = classify_hand(["Ah", "Kd"], ["Ad", "Kc", "7s"])

    assert hand["made_hand_class"] == "two_pair"


def test_classify_flush() -> None:
    """Hero with five-card heart flush is flush or better."""
    hand = classify_hand(["Ah", "5h"], ["Kh", "9h", "2h"])

    assert hand["made_hand_class"] in {"flush", "nuts"}


def test_classify_straight() -> None:
    """Hero with 6-high straight is straight."""
    hand = classify_hand(["6h", "5d"], ["4c", "3s", "2d"])

    assert hand["made_hand_class"] == "straight"


def test_classify_trash() -> None:
    """Low cards with no pair or draw are trash."""
    hand = classify_hand(["3h", "2d"], ["Ac", "Ks", "9d"])

    assert hand["made_hand_class"] == "trash"


def test_classify_nuts_high() -> None:
    """Ace-high unpaired hand is nuts_high."""
    hand = classify_hand(["Ah", "Qd"], ["Kc", "9s", "7d"])

    assert hand["made_hand_class"] == "nuts_high"


def test_classify_weak_showdown() -> None:
    """Ten-high classifies as weak showdown on high-card board."""
    hand = classify_hand(["Th", "9d"], ["Ac", "Ks", "7d"])

    assert hand["made_hand_class"] == "weak_showdown"


def test_classify_board_only_pair() -> None:
    """Board-only pair falls through to high-card classification."""
    hand = classify_hand(["Th", "9d"], ["7c", "7s", "2d"])

    assert hand["made_hand_class"] == "weak_showdown"


def test_classify_board_only_two_pair() -> None:
    """Board-only two pair falls through to high-card classification."""
    hand = classify_hand(["Th", "9d"], ["Ah", "Ad", "7c", "7s"])

    assert hand["made_hand_class"] == "weak_showdown"


def test_kicker_top_pair_top_kicker() -> None:
    """Top pair with best available kicker is top kicker."""
    hand = classify_hand(["Ah", "Kd"], ["Ad", "9c", "7s"])

    assert hand["made_hand_class"] == "top_pair"
    assert hand["kicker_class"] == "top_kicker"


def test_kicker_top_pair_weak_kicker() -> None:
    """Top pair with deuce kicker is weak kicker."""
    hand = classify_hand(["Ah", "2d"], ["Ad", "Kc", "9s"])

    assert hand["made_hand_class"] == "top_pair"
    assert hand["kicker_class"] == "weak_kicker"


def test_draw_flush_draw() -> None:
    """Nut flush draw is medium-strong draw or better."""
    hand = classify_hand(["Ah", "5h"], ["Kh", "9h", "2d"])

    assert hand["draw_class"] in {"medium_strong_draw", "strong_draw"}
    assert hand["draw_outs"] >= 9


def test_draw_oesd() -> None:
    """Open-ended straight draw reports eight outs."""
    hand = classify_hand(["6h", "5d"], ["4c", "3s", "Ad"])

    assert hand["draw_outs"] == 8


def test_draw_gutshot() -> None:
    """Gutshot straight draw reports four outs."""
    hand = classify_hand(["6h", "5d"], ["4c", "2s", "Ad"])

    assert hand["draw_outs"] == 4


def test_draw_none_on_river() -> None:
    """River does not report unfinished draws."""
    hand = classify_hand(["Ah", "5h"], ["Kh", "9h", "2d", "3c", "Jc"])

    assert hand["draw_class"] is None


def test_draw_combo_rule() -> None:
    """Made hand plus draw applies combo rule flag."""
    hand = classify_hand(["Ah", "5h"], ["Kh", "9h", "Ad"])

    assert hand["made_hand_class"] == "top_pair"
    assert hand["draw_class"] is not None
    assert hand["combo_applied"] is True


def test_nuts_true() -> None:
    """Royal flush is the nuts."""
    hand = classify_hand(["Ah", "Kh"], ["Qh", "Jh", "Th"])

    assert hand["is_nuts"] is True
    assert hand["nuts_distance"] == 0


def test_nuts_false() -> None:
    """Weak high-card hand is not the nuts."""
    hand = classify_hand(["2h", "3d"], ["Ac", "Ks", "9d"])

    assert hand["is_nuts"] is False
    assert hand["nuts_distance"] > 0


def test_budget_nuts() -> None:
    """Nuts class has infinite ATT/DEF."""
    result = get_made_hand_budget("nuts", None, dry_board, "SRP", "flop")

    assert result["is_infinite"] is True
    assert result["base_att"] == float("inf")


def test_budget_top_pair_tptk_srp() -> None:
    """SRP TPTK maps to ATT 3.0 and DEF 4.0."""
    result = get_made_hand_budget("top_pair", "top_kicker", dry_board, "SRP", "flop")

    assert result["base_att"] == pytest.approx(3.0, abs=0.01)
    assert result["base_def"] == pytest.approx(4.0, abs=0.01)


def test_budget_top_pair_3bp() -> None:
    """3BP TPTK maps to ATT 2.9 and DEF 3.9."""
    result = get_made_hand_budget("top_pair", "top_kicker", dry_board, "3BP", "flop")

    assert result["base_att"] == pytest.approx(2.9, abs=0.01)


def test_budget_top_pair_paired_board() -> None:
    """Top pair on paired board receives a base minus 0.5 penalty."""
    result = get_made_hand_budget(
        "top_pair",
        "top_kicker",
        paired_board,
        "SRP",
        "flop",
    )

    assert result["base_att"] == pytest.approx(2.5, abs=0.01)
    assert "paired" in result["board_modifier_applied"]


def test_budget_overpair_flush_possible() -> None:
    """Overpair on flush-possible board receives the flop flush penalty."""
    result = get_made_hand_budget(
        "overpair",
        "top_kicker",
        flush_possible_board,
        "SRP",
        "flop",
    )

    assert result["base_att"] == pytest.approx(2.4, abs=0.01)


def test_budget_trash() -> None:
    """Trash has no DEF budget."""
    result = get_made_hand_budget("trash", None, dry_board, "SRP", "flop")

    assert result["base_def"] == 0.0


def test_draw_budget_strong_draw() -> None:
    """Strong draw has ATT 4+ and combo bonus 2.0."""
    result = get_draw_budget("strong_draw", "SRP", "flop", "IP")

    assert result["att"] >= 4.0
    assert result["combo_bonus"] == pytest.approx(2.0, abs=0.01)
    assert result["threshold_ip"] == pytest.approx(500, abs=1)


def test_draw_budget_weak_draw() -> None:
    """Weak draw has combo bonus 0.4."""
    result = get_draw_budget("weak_draw", "SRP", "flop", "IP")

    assert result["combo_bonus"] == pytest.approx(0.4, abs=0.01)


def test_draw_budget_none() -> None:
    """None draw class returns zero and None values."""
    result = get_draw_budget(None, "SRP", "flop", "IP")

    assert result["att"] == 0.0
    assert result["combo_bonus"] == 0.0


def test_pressure_weight_50pct() -> None:
    """50 percent pot maps to pressure weight 0.70."""
    assert calculate_pressure_weight(50.0) == pytest.approx(0.70, abs=0.01)


def test_pressure_weight_100pct() -> None:
    """100 percent pot maps to pressure weight 1.10."""
    assert calculate_pressure_weight(100.0) == pytest.approx(1.10, abs=0.01)


def test_pressure_weight_300pct() -> None:
    """300 percent pot maps to pressure weight 2.00."""
    assert calculate_pressure_weight(300.0) == pytest.approx(2.00, abs=0.01)


def test_pressure_weight_over_1500() -> None:
    """>=1500 percent pot is capped at pressure weight 4.00."""
    assert calculate_pressure_weight(2000.0) == pytest.approx(4.00, abs=0.01)


def test_pressure_weight_interpolation() -> None:
    """Pressure weight is linearly interpolated inside table intervals."""
    assert calculate_pressure_weight(26.0) == pytest.approx(0.40, abs=0.01)


def test_special_board_trips_board_nut_kicker() -> None:
    """Trips board with nut kicker overrides to ATT 0.5 and DEF 1.5."""
    result = get_special_board_override(
        "trips_board",
        {"kicker_class": "top_kicker", "made_hand_class": "trips"},
        trips_board_texture,
    )

    assert result is not None
    assert result["override_att"] == pytest.approx(0.5, abs=0.01)
    assert result["override_def"] == pytest.approx(1.5, abs=0.01)


def test_cumulative_pressure_hero_bet_and_called_raise() -> None:
    """Hero proactive action and continued facing pressure are accumulated."""
    result = calculate_cumulative_pressure(
        [
            {"seat": 1, "action": "BET", "amount": 50},
            {"seat": 2, "action": "RAISE", "amount": 150},
            {"seat": 1, "action": "CALL", "amount": 100},
        ],
        hero_seat=1,
        pot_history=[100, 150, 300],
    )

    assert result["att_spent"] == pytest.approx(0.70, abs=0.01)
    assert result["def_spent"] > 0.0


def test_mw_modifier_3way_made_hand() -> None:
    """3way made hand gets MW and IP modifiers."""
    result = apply_mw_modifiers(3.0, 4.0, "top_pair", None, dry_board, 3, "IP", "SRP")

    assert result["adjusted_att"] == pytest.approx(3.0 - 0.35 + 0.15, abs=0.01)
    assert result["adjusted_def"] == pytest.approx(4.0 - 0.25 + 0.20, abs=0.01)


def test_mw_modifier_4way_made_hand() -> None:
    """4way made hand uses two extra opponents."""
    result = apply_mw_modifiers(3.0, 4.0, "top_pair", None, dry_board, 4, "IP", "SRP")

    assert result["adjusted_att"] == pytest.approx(3.0 - 0.70 + 0.15, abs=0.01)


def test_mw_modifier_sandwich() -> None:
    """Sandwich position applies ATT and DEF penalties."""
    result = apply_mw_modifiers(
        3.0,
        4.0,
        "top_pair",
        None,
        dry_board,
        3,
        "sandwich",
        "SRP",
    )

    assert result["adjusted_att"] == pytest.approx(3.0 - 0.35 - 0.30, abs=0.01)
    assert result["adjusted_def"] == pytest.approx(4.0 - 0.25 - 0.45, abs=0.01)


def test_mw_modifier_3bp_top_pair() -> None:
    """3BP top pair and weaker hands receive pot-type penalties."""
    result = apply_mw_modifiers(3.0, 4.0, "top_pair", None, dry_board, 3, "IP", "3BP")

    assert result["adjusted_att"] == pytest.approx(
        3.0 - 0.35 + 0.15 - 0.20,
        abs=0.01,
    )


def test_mw_modifier_infinite_untouched() -> None:
    """Infinite budget is not modified by MW adjustments."""
    result = apply_mw_modifiers(
        float("inf"),
        float("inf"),
        "nuts",
        None,
        dry_board,
        4,
        "IP",
        "SRP",
    )

    assert result["adjusted_att"] == float("inf")
    assert result["adjusted_def"] == float("inf")


def test_mw_modifier_clamp_zero() -> None:
    """ATT/DEF never go below zero."""
    result = apply_mw_modifiers(
        0.5,
        0.5,
        "fourth_fifth_pair",
        None,
        dry_board,
        5,
        "sandwich",
        "4BP+",
    )

    assert result["adjusted_att"] >= 0.0
    assert result["adjusted_def"] >= 0.0


def test_mw_modifier_wet_multiway_one_pair() -> None:
    """Wet 3way one-pair hand receives the wet multiway penalty."""
    wet_board = {
        "suit_texture": "two_tone",
        "rank_texture": ["straight_possible"],
        "overall_texture": "wet",
        "special_board": None,
        "flush_possible": True,
        "straight_possible": True,
        "paired": False,
    }

    result = apply_mw_modifiers(3.0, 4.0, "top_pair", None, wet_board, 3, "IP", "SRP")

    assert result["adjusted_att"] == pytest.approx(
        3.0 - 0.35 + 0.15 - 0.40,
        abs=0.01,
    )


def test_mw_modifier_draw_threshold() -> None:
    """Draw class receives a multiway threshold multiplier."""
    result = apply_mw_modifiers(
        2.0,
        3.0,
        "top_pair",
        "medium_draw",
        dry_board,
        4,
        "IP",
        "SRP",
    )

    assert result["draw_threshold_multiplier"] == pytest.approx(0.84, abs=0.01)


def test_compute_full_budget_basic() -> None:
    """Basic dry-board SRP IP 3way pipeline returns budget context."""
    result = compute_full_budget(
        hero_cards=["Ah", "Kd"],
        board_cards=["As", "7c", "2d"],
        pot_type="SRP",
        position="IP",
        active_player_count=3,
        street="flop",
        action_history=[],
        hero_seat=0,
        pot_history=[],
    )

    assert "remaining_att" in result
    assert "remaining_def" in result
    assert "viable_actions" in result
    assert result["hand_class"]["made_hand_class"] == "top_pair"
    assert result["remaining_att"] > 0


def test_compute_full_budget_nuts() -> None:
    """Nuts hand yields a nuts budget verdict."""
    result = compute_full_budget(
        hero_cards=["Ah", "Kh"],
        board_cards=["Qh", "Jh", "Th"],
        pot_type="SRP",
        position="IP",
        active_player_count=3,
        street="flop",
        action_history=[],
        hero_seat=0,
        pot_history=[],
    )

    assert result["budget_verdict"] == "nuts"


def test_compute_full_budget_trash() -> None:
    """Trash hand yields a fold-lean budget verdict."""
    result = compute_full_budget(
        hero_cards=["2h", "3d"],
        board_cards=["Ks", "Qc", "Jd"],
        pot_type="SRP",
        position="OOP",
        active_player_count=4,
        street="flop",
        action_history=[],
        hero_seat=0,
        pot_history=[],
    )

    assert result["budget_verdict"] == "fold_lean"


def test_viable_actions_nuts_always_raise() -> None:
    """Nuts can always raise when raise is legal."""
    actions = determine_viable_actions(
        remaining_att=float("inf"),
        remaining_def=float("inf"),
        draw_threshold=None,
        draw_class=None,
        made_hand_class="nuts",
        board_texture=dry_board,
        position="IP",
        street="river",
        spr=5.0,
        legal_actions=["fold", "call", "raise"],
        is_nuts=True,
        active_player_count=3,
    )

    assert "raise" in actions


def test_viable_actions_no_att_no_bet() -> None:
    """When remaining ATT is zero, bet is not viable."""
    actions = determine_viable_actions(
        remaining_att=0.0,
        remaining_def=2.0,
        draw_threshold=None,
        draw_class=None,
        made_hand_class="second_pair",
        board_texture=dry_board,
        position="IP",
        street="flop",
        spr=5.0,
        legal_actions=["fold", "check", "bet"],
        is_nuts=False,
        active_player_count=3,
    )

    assert "bet" not in actions
    assert "check" in actions


def test_viable_actions_no_def_no_call() -> None:
    """When DEF is zero and no draw threshold exists, call is not viable."""
    actions = determine_viable_actions(
        remaining_att=0.0,
        remaining_def=0.0,
        draw_threshold=None,
        draw_class=None,
        made_hand_class="trash",
        board_texture=dry_board,
        position="OOP",
        street="turn",
        spr=5.0,
        legal_actions=["fold", "call"],
        is_nuts=False,
        active_player_count=3,
    )

    assert "call" not in actions
    assert "fold" in actions


def test_viable_actions_low_spr_commit() -> None:
    """Low SPR with top pair or better removes fold."""
    actions = determine_viable_actions(
        remaining_att=1.0,
        remaining_def=1.0,
        draw_threshold=None,
        draw_class=None,
        made_hand_class="top_pair",
        board_texture=dry_board,
        position="IP",
        street="flop",
        spr=0.5,
        legal_actions=["fold", "call", "raise"],
        is_nuts=False,
        active_player_count=3,
    )

    assert "fold" not in actions


def test_viable_actions_check_always_viable() -> None:
    """Check remains viable when legal."""
    actions = determine_viable_actions(
        remaining_att=0.0,
        remaining_def=0.0,
        draw_threshold=None,
        draw_class=None,
        made_hand_class="trash",
        board_texture=dry_board,
        position="OOP",
        street="flop",
        spr=5.0,
        legal_actions=["check", "bet"],
        is_nuts=False,
        active_player_count=3,
    )

    assert "check" in actions
