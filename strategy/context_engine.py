"""PokerSkill-style Context Engine for multiway postflop decisions.

SPEC.md §9.4.7 - §9.4.8.5 準拠。
決定論的にboard texture、hand class、draw class、kicker、nutsを分類する。
LLM呼び出しは含まない。Budget計算・viable action logicはStep 2で追加。
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations
import json
import math
from typing import TypedDict

import eval7


FloatBudget = float

RANK_TO_VALUE: dict[str, int] = {
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "T": 10,
    "J": 11,
    "Q": 12,
    "K": 13,
    "A": 14,
}
VALUE_TO_RANK: dict[int, str] = {value: rank for rank, value in RANK_TO_VALUE.items()}
HAND_TYPE_MAP: dict[str, str] = {
    "straight flush": "straight_flush",
    "quads": "four_of_a_kind",
    "four of a kind": "four_of_a_kind",
    "full house": "full_house",
    "flush": "flush",
    "straight": "straight",
    "trips": "three_of_a_kind",
    "three of a kind": "three_of_a_kind",
    "two pair": "two_pair",
    "pair": "one_pair",
    "one pair": "one_pair",
    "high card": "high_card",
}
STRAIGHT_WINDOWS: tuple[tuple[int, ...], ...] = tuple(
    tuple(range(high, high - 5, -1)) for high in range(14, 5, -1)
) + ((5, 4, 3, 2, 1),)
PRESSURE_WEIGHT_TABLE: tuple[tuple[float, float], ...] = (
    (5.0, 0.04),
    (20.0, 0.30),
    (32.0, 0.50),
    (50.0, 0.70),
    (67.0, 0.85),
    (85.0, 1.00),
    (100.0, 1.10),
    (122.0, 1.25),
    (150.0, 1.40),
    (195.0, 1.60),
    (300.0, 2.00),
    (400.0, 2.30),
    (500.0, 2.50),
    (700.0, 2.90),
    (1000.0, 3.40),
    (1500.0, 4.00),
)
KICKER_BUDGET_MODIFIERS: dict[str, tuple[float, float]] = {
    "top_kicker": (0.0, 0.0),
    "second_kicker": (-0.3, -0.2),
    "third_kicker": (-0.5, -0.3),
    "weak_kicker": (-0.8, -0.5),
}
MADE_HAND_BASE_BUDGETS: dict[str, dict[str, tuple[FloatBudget, FloatBudget]]] = {
    "nuts": {
        "default": (float("inf"), float("inf")),
    },
    "flush": {
        "default": (4.0, 5.0),
        "SRP": (4.0, 5.0),
        "3BP": (3.5, 4.5),
        "4BP+": (3.0, 4.0),
    },
    "straight": {
        "default": (5.5, 6.5),
        "SRP": (5.5, 6.5),
        "3BP": (4.5, 5.5),
        "4BP+": (3.5, 4.5),
    },
    "set": {
        "default": (float("inf"), float("inf")),
    },
    "trips": {
        "default": (4.0, 5.5),
        "SRP": (4.0, 5.5),
        "3BP": (3.0, 4.5),
        "4BP+": (2.5, 4.0),
    },
    "two_pair": {
        "default": (5.0, 6.5),
        "SRP": (5.0, 6.5),
        "3BP": (4.7, 6.0),
        "4BP+": (4.5, 5.7),
    },
    "overpair": {
        "SRP": (3.5, 4.5),
        "3BP": (3.4, 4.5),
        "4BP+": (3.4, 4.5),
        "limp": (3.5, 4.5),
    },
    "top_pair": {
        "SRP": (3.0, 4.0),
        "3BP": (2.9, 3.9),
        "4BP+": (2.6, 3.6),
        "limp": (3.0, 4.0),
    },
    "second_pair": {
        "SRP": (1.8, 2.8),
        "3BP": (1.5, 2.3),
        "4BP+": (1.0, 1.8),
        "limp": (1.8, 2.8),
    },
    "third_pair": {
        "SRP": (1.2, 2.2),
        "3BP": (1.5, 1.5),
        "4BP+": (1.5, 1.2),
        "limp": (1.2, 2.2),
    },
    "fourth_fifth_pair": {
        "SRP": (0.8, 1.8),
        "3BP": (1.5, 1.0),
        "4BP+": (1.5, 0.7),
        "limp": (0.8, 1.8),
    },
    "nuts_high": {
        "limp": (0.0, 0.8),
        "SRP": (0.0, 0.6),
        "3BP": (0.0, 0.4),
        "4BP+": (0.0, 0.1),
    },
    "second_high": {
        "limp": (0.0, 0.4),
        "SRP": (0.0, 0.3),
        "3BP": (0.0, 0.1),
        "4BP+": (0.0, 0.0),
    },
    "weak_showdown": {
        "limp": (0.0, 0.8),
        "SRP": (0.0, 0.8),
        "3BP": (0.0, 0.4),
        "4BP+": (0.0, 0.2),
    },
    "trash": {
        "default": (0.0, 0.0),
    },
}
DRAW_BUDGETS: dict[str, dict[str, float]] = {
    "strong_draw": {
        "att": 4.0,
        "flop_ip": 500.0,
        "flop_oop": 400.0,
        "turn_ip": 190.0,
        "turn_oop": 150.0,
        "check_raise_flop_ip": 500.0,
        "check_raise_flop_oop": 400.0,
        "check_raise_turn_ip": 190.0,
        "check_raise_turn_oop": 150.0,
        "combo_bonus": 2.0,
    },
    "medium_strong_draw": {
        "att": 3.0,
        "flop_ip": 250.0,
        "flop_oop": 200.0,
        "turn_ip": 100.0,
        "turn_oop": 75.0,
        "check_raise_flop_ip": 150.0,
        "check_raise_flop_oop": 100.0,
        "check_raise_turn_ip": 60.0,
        "check_raise_turn_oop": 40.0,
        "combo_bonus": 1.2,
    },
    "medium_draw": {
        "att": 1.5,
        "flop_ip": 150.0,
        "flop_oop": 120.0,
        "turn_ip": 60.0,
        "turn_oop": 40.0,
        "check_raise_flop_ip": 100.0,
        "check_raise_flop_oop": 75.0,
        "check_raise_turn_ip": 40.0,
        "check_raise_turn_oop": 28.0,
        "combo_bonus": 0.8,
    },
    "medium_weak_draw": {
        "att": 1.0,
        "flop_ip": 94.0,
        "flop_oop": 78.0,
        "turn_ip": 40.0,
        "turn_oop": 26.0,
        "check_raise_flop_ip": 60.0,
        "check_raise_flop_oop": 42.0,
        "check_raise_turn_ip": 20.0,
        "check_raise_turn_oop": 14.0,
        "combo_bonus": 0.7,
    },
    "weak_draw": {
        "att": 0.5,
        "flop_ip": 68.0,
        "flop_oop": 56.0,
        "turn_ip": 24.0,
        "turn_oop": 16.0,
        "check_raise_flop_ip": 40.0,
        "check_raise_flop_oop": 28.0,
        "check_raise_turn_ip": 0.0,
        "check_raise_turn_oop": 0.0,
        "combo_bonus": 0.4,
    },
    "strong_overcard_draw": {
        "att": 1.0,
        "flop_ip": 80.0,
        "flop_oop": 65.0,
        "turn_ip": 35.0,
        "turn_oop": 25.0,
        "check_raise_flop_ip": 55.0,
        "check_raise_flop_oop": 35.0,
        "check_raise_turn_ip": 0.0,
        "check_raise_turn_oop": 0.0,
        "combo_bonus": 0.3,
    },
    "medium_overcard_draw": {
        "att": 0.5,
        "flop_ip": 58.0,
        "flop_oop": 45.0,
        "turn_ip": 23.0,
        "turn_oop": 15.0,
        "check_raise_flop_ip": 28.0,
        "check_raise_flop_oop": 18.0,
        "check_raise_turn_ip": 0.0,
        "check_raise_turn_oop": 0.0,
        "combo_bonus": 0.2,
    },
    "weak_overcard_draw": {
        "att": 0.5,
        "flop_ip": 35.0,
        "flop_oop": 25.0,
        "turn_ip": 15.0,
        "turn_oop": 9.0,
        "check_raise_flop_ip": 0.0,
        "check_raise_flop_oop": 0.0,
        "check_raise_turn_ip": 0.0,
        "check_raise_turn_oop": 0.0,
        "combo_bonus": 0.1,
    },
}
DRAW_DOWNGRADE_CHAIN: tuple[str, ...] = (
    "strong_draw",
    "medium_strong_draw",
    "medium_draw",
    "medium_weak_draw",
    "weak_draw",
    "weak_overcard_draw",
)
ONE_PAIR_CLASSES: set[str] = {
    "top_pair",
    "second_pair",
    "third_pair",
    "fourth_fifth_pair",
    "overpair",
}
HIGH_CARD_CLASSES: set[str] = {
    "nuts_high",
    "second_high",
    "weak_showdown",
    "trash",
}
VALUE_HAND_CLASSES: set[str] = {
    "nuts",
    "flush",
    "straight",
    "set",
    "trips",
    "two_pair",
}
MADE_HAND_STRENGTH: dict[str, int] = {
    "trash": 0,
    "weak_showdown": 1,
    "second_high": 2,
    "nuts_high": 3,
    "fourth_fifth_pair": 4,
    "third_pair": 5,
    "second_pair": 6,
    "top_pair": 7,
    "overpair": 8,
    "two_pair": 9,
    "trips": 10,
    "set": 11,
    "straight": 12,
    "flush": 13,
    "nuts": 14,
}
SKILL_GUIDANCE_TEMPLATES: dict[str, str] = {
    "nuts": (
        "You hold the nuts or near-nuts. Build the pot aggressively. "
        "Consider slow-play only at low SPR."
    ),
    "flush": (
        "You have a flush. On 3-flush boards, bet for value but watch for higher "
        "flushes. On paired boards, be cautious of full houses."
    ),
    "straight": (
        "You have a straight. Watch for flush draws on the board. Two-card "
        "straights are stronger than one-card straights."
    ),
    "set": (
        "You have a set. This is a strong hand but vulnerable to flush/straight "
        "draws. Bet to deny equity on wet boards."
    ),
    "overpair": (
        "You have an overpair. Good for value on safe boards. Reduce aggression "
        "on wet/paired boards."
    ),
    "top_pair": (
        "You have top pair. Kicker strength matters. Bet for thin value on safe "
        "boards; pot-control on wet boards."
    ),
    "second_pair": (
        "You have second pair. Mostly a defensive hand. Check-call on safe boards; "
        "fold to heavy pressure."
    ),
    "trash": (
        "No made hand or draw. Look for bluff opportunities on favorable boards; "
        "fold to any aggression."
    ),
    "strong_draw": "Strong combo draw. Semi-bluff aggressively, especially with fold equity.",
    "medium_draw": "Decent draw. Call if pot odds are right; semi-bluff only with good fold equity.",
    "weak_draw": "Weak draw. Only continue with direct pot odds; don't invest more than the threshold.",
}
BOARD_GUIDANCE_TEMPLATES: dict[str, str] = {
    "dry": "Dry board favors the preflop aggressor. C-bets are effective. Draws are unlikely.",
    "wet": "Wet board with multiple draws. Made hands should bet to protect. Draws have good equity.",
    "very_wet": (
        "Very wet board. Be cautious with marginal holdings. Strong draws can "
        "semi-bluff effectively."
    ),
    "paired": "Paired board. Thin value bets are risky. Trips/full house are possible.",
}
VIABLE_ACTION_PRIORITY: tuple[str, ...] = (
    "all_in",
    "raise",
    "bet",
    "call",
    "check",
    "fold",
)


class BoardTexture(TypedDict):
    """Board texture classification result."""

    suit_texture: str
    rank_texture: list[str]
    overall_texture: str
    special_board: str | None
    flush_possible: bool
    straight_possible: bool
    paired: bool


class HandClassification(TypedDict):
    """Hero hand classification result."""

    made_hand_class: str
    draw_class: str | None
    draw_outs: int
    is_nuts: bool
    nuts_distance: int
    kicker_class: str | None
    combo_applied: bool


class MadeHandBudget(TypedDict):
    """Made-hand ATT/DEF budget lookup result."""

    base_att: float
    base_def: float
    is_infinite: bool
    board_modifier_applied: list[str]


class DrawBudget(TypedDict):
    """Draw ATT, threshold, check-raise, and combo bonus lookup result."""

    att: float
    threshold_ip: float | None
    threshold_oop: float | None
    check_raise_ip: float | None
    check_raise_oop: float | None
    combo_bonus: float


class SpecialBoardOverride(TypedDict):
    """Special-board ATT/DEF override result."""

    override_att: float
    override_def: float
    override_class: str


class CumulativePressure(TypedDict):
    """Cumulative pressure spent by hero on the current action line."""

    att_spent: float
    def_spent: float
    raise_count_bonus: float


class MwModifierResult(TypedDict):
    """Multiway-adjusted budget result."""

    adjusted_att: float
    adjusted_def: float
    draw_threshold_multiplier: float
    modifiers_applied: list[str]


class FullBudget(TypedDict):
    """Full deterministic Context Engine budget pipeline result."""

    board_texture: BoardTexture
    hand_class: HandClassification
    base_att: float
    base_def: float
    draw_budget: DrawBudget
    special_override: SpecialBoardOverride | None
    adjusted_att: float
    adjusted_def: float
    att_spent: float
    def_spent: float
    remaining_att: float
    remaining_def: float
    draw_threshold: float | None
    viable_actions: list[str]
    budget_verdict: str


def classify_board_texture(board_cards: list[str]) -> BoardTexture:
    """Classify board texture from three to five board cards.

    Args:
        board_cards: Board cards such as ``["Ah", "Ks", "7d"]``.

    Returns:
        Board texture flags and labels defined by SPEC.md §9.4.7.

    Raises:
        ValueError: If board card count is outside the postflop range.
    """
    _validate_cards(board_cards, min_count=3, max_count=5)
    ranks = [_rank_value(card) for card in board_cards]
    suits = [_suit_value(card) for card in board_cards]
    suit_counts = Counter(suits)
    rank_counts = Counter(ranks)
    max_suit_count = max(suit_counts.values())

    suit_texture = _classify_suit_texture(len(board_cards), max_suit_count)
    rank_texture = _classify_rank_texture(ranks)
    paired = any(count >= 2 for count in rank_counts.values())
    board_straight = _is_complete_straight(set(ranks))
    board_flush = len(board_cards) == 5 and max_suit_count == 5
    board_full_house = sorted(rank_counts.values(), reverse=True)[:2] == [3, 2]
    special_board = _classify_special_board(
        rank_counts,
        board_flush,
        board_straight,
        board_full_house,
    )
    if board_straight and "board_straight" not in rank_texture:
        rank_texture.append("board_straight")

    straight_possible = _has_straight_draw_texture(set(ranks))
    flush_possible = max_suit_count >= 3
    connected_count = _count_straight_draw_windows(set(ranks))
    overall_texture = _classify_overall_texture(
        suit_texture,
        flush_possible,
        straight_possible,
        paired,
        connected_count,
    )

    return {
        "suit_texture": suit_texture,
        "rank_texture": rank_texture,
        "overall_texture": overall_texture,
        "special_board": special_board,
        "flush_possible": flush_possible,
        "straight_possible": straight_possible,
        "paired": paired,
    }


def classify_hand(hero_cards: list[str], board_cards: list[str]) -> HandClassification:
    """Classify hero hand, draw, kicker, combo state, and nuts distance.

    Args:
        hero_cards: Two hero cards such as ``["Ah", "Kh"]``.
        board_cards: Three to five board cards.

    Returns:
        Hand classification result defined by SPEC.md §9.4.8.
    """
    _validate_cards(hero_cards, min_count=2, max_count=2)
    _validate_cards(board_cards, min_count=3, max_count=5)
    made_hand_class = _classify_made_hand(hero_cards, board_cards)
    is_nuts, nuts_distance = _check_nuts(hero_cards, board_cards)
    kicker_class = _classify_kicker(hero_cards, board_cards, made_hand_class)
    draw_class, draw_outs = _classify_draw(hero_cards, board_cards)
    combo_applied = _apply_combo_rule(made_hand_class, draw_class)

    return {
        "made_hand_class": made_hand_class,
        "draw_class": draw_class,
        "draw_outs": draw_outs,
        "is_nuts": is_nuts,
        "nuts_distance": nuts_distance,
        "kicker_class": kicker_class,
        "combo_applied": combo_applied,
    }


def get_made_hand_budget(
    made_hand_class: str,
    kicker_class: str | None,
    board_texture: dict[str, object],
    pot_type: str,
    street: str,
) -> MadeHandBudget:
    """Look up made-hand ATT/DEF budget and apply board texture modifiers.

    Args:
        made_hand_class: Made-hand class from :func:`classify_hand`.
        kicker_class: Kicker class from :func:`classify_hand`, if applicable.
        board_texture: Board texture dictionary from :func:`classify_board_texture`.
        pot_type: One of ``"limp"``, ``"SRP"``, ``"3BP"``, or ``"4BP+"``.
        street: One of ``"flop"``, ``"turn"``, or ``"river"``.

    Returns:
        Made-hand budget with applied board modifier names.
    """
    _validate_pot_type(pot_type)
    _validate_street(street)

    hero_hand_info = {
        "made_hand_class": made_hand_class,
        "kicker_class": kicker_class,
    }
    override = get_special_board_override(
        _object_as_optional_str(board_texture.get("special_board")),
        hero_hand_info,
        board_texture,
    )
    if override is not None:
        return {
            "base_att": override["override_att"],
            "base_def": override["override_def"],
            "is_infinite": _budget_is_infinite(
                override["override_att"],
                override["override_def"],
            ),
            "board_modifier_applied": ["special_board_override"],
        }

    base_att, base_def = _lookup_made_hand_base_budget(made_hand_class, pot_type)
    if not _budget_is_infinite(base_att, base_def):
        base_att, base_def = _apply_kicker_budget_modifier(
            base_att,
            base_def,
            made_hand_class,
            kicker_class,
        )
    base_att, base_def, modifiers = _apply_board_budget_modifiers(
        base_att,
        base_def,
        made_hand_class,
        board_texture,
        street,
    )

    return {
        "base_att": base_att,
        "base_def": base_def,
        "is_infinite": _budget_is_infinite(base_att, base_def),
        "board_modifier_applied": modifiers,
    }


def get_draw_budget(
    draw_class: str | None,
    pot_type: str,
    street: str,
    position: str,
) -> DrawBudget:
    """Look up draw ATT, call thresholds, check-raise thresholds, and combo bonus.

    Args:
        draw_class: Draw class from :func:`classify_hand`, or ``None``.
        pot_type: One of ``"limp"``, ``"SRP"``, ``"3BP"``, or ``"4BP+"``.
        street: One of ``"flop"``, ``"turn"``, or ``"river"``.
        position: ``"IP"`` or ``"OOP"``.

    Returns:
        Draw budget values. River or ``None`` draw returns zero/``None`` values.
    """
    _validate_pot_type(pot_type)
    _validate_street(street)
    _validate_position(position)
    if draw_class is None or street == "river":
        return _empty_draw_budget()

    normalized_draw_class = _downgrade_draw_for_pot_type(draw_class, pot_type)
    budget = DRAW_BUDGETS.get(normalized_draw_class)
    if budget is None:
        return _empty_draw_budget()

    street_prefix = "flop" if street == "flop" else "turn"
    threshold_ip = budget.get(f"{street_prefix}_ip")
    threshold_oop = budget.get(f"{street_prefix}_oop")
    check_raise_ip = budget.get(f"check_raise_{street_prefix}_ip")
    check_raise_oop = budget.get(f"check_raise_{street_prefix}_oop")
    return {
        "att": budget["att"],
        "threshold_ip": _zero_as_none(threshold_ip),
        "threshold_oop": _zero_as_none(threshold_oop),
        "check_raise_ip": _zero_as_none(check_raise_ip),
        "check_raise_oop": _zero_as_none(check_raise_oop),
        "combo_bonus": budget["combo_bonus"],
    }


def get_special_board_override(
    special_board: str | None,
    hero_hand_info: dict[str, object],
    board_texture: dict[str, object],
) -> SpecialBoardOverride | None:
    """Return special-board ATT/DEF override values when applicable.

    Args:
        special_board: Special board label from board texture classification.
        hero_hand_info: Hero hand classification dictionary.
        board_texture: Board texture dictionary.

    Returns:
        Override values, or ``None`` when no special-board override applies.
    """
    del board_texture
    if special_board is None:
        return None

    made_hand_class = _object_as_optional_str(hero_hand_info.get("made_hand_class"))
    kicker_class = _object_as_optional_str(hero_hand_info.get("kicker_class"))
    if made_hand_class == "nuts":
        return {
            "override_att": float("inf"),
            "override_def": float("inf"),
            "override_class": "nuts",
        }
    if special_board == "trips_board":
        if made_hand_class in {"four_of_a_kind", "full_house"}:
            return {
                "override_att": float("inf"),
                "override_def": float("inf"),
                "override_class": "nuts",
            }
        if kicker_class == "top_kicker":
            return {
                "override_att": 0.5,
                "override_def": 1.5,
                "override_class": "trips_board_nut_kicker",
            }
        if kicker_class == "second_kicker":
            return {
                "override_att": 0.0,
                "override_def": 0.8,
                "override_class": "trips_board_second_kicker",
            }
        return {
            "override_att": 0.0,
            "override_def": 0.0,
            "override_class": "trips_board_low_kicker",
        }
    if special_board == "double_paired":
        if made_hand_class == "full_house":
            return {
                "override_att": 2.5,
                "override_def": 3.5,
                "override_class": "double_paired_full_house",
            }
        if made_hand_class in {"flush", "straight"}:
            return {
                "override_att": 2.0,
                "override_def": 3.0,
                "override_class": f"double_paired_{made_hand_class}",
            }
    if special_board == "quads_board":
        if made_hand_class == "nuts_high":
            return {
                "override_att": float("inf"),
                "override_def": float("inf"),
                "override_class": "quads_board_nut_high",
            }
        if made_hand_class == "second_high":
            return {
                "override_att": 1.5,
                "override_def": 2.5,
                "override_class": "quads_board_second_high",
            }
        return {
            "override_att": 0.5,
            "override_def": 1.5,
            "override_class": "quads_board_kicker",
        }
    if special_board == "board_flush":
        return {
            "override_att": 0.0,
            "override_def": 1.5,
            "override_class": "board_flush_kicker_only",
        }
    if special_board in {"board_straight", "board_full_house"}:
        return {
            "override_att": 0.0,
            "override_def": 1.0,
            "override_class": f"{special_board}_shared",
        }
    return None


def calculate_pressure_weight(bet_percent_pot: float) -> float:
    """Convert a bet size as percent of pot into pressure weight.

    Args:
        bet_percent_pot: Bet or raise-to-call divided by pot before action, in %.

    Returns:
        Piecewise-linear pressure weight from SPEC.md section 9.4.12.
    """
    if bet_percent_pot <= PRESSURE_WEIGHT_TABLE[0][0]:
        return PRESSURE_WEIGHT_TABLE[0][1]
    for index in range(1, len(PRESSURE_WEIGHT_TABLE)):
        upper_percent, upper_weight = PRESSURE_WEIGHT_TABLE[index]
        lower_percent, lower_weight = PRESSURE_WEIGHT_TABLE[index - 1]
        if bet_percent_pot <= upper_percent:
            ratio = (bet_percent_pot - lower_percent) / (upper_percent - lower_percent)
            return lower_weight + ratio * (upper_weight - lower_weight)
    return PRESSURE_WEIGHT_TABLE[-1][1]


def calculate_cumulative_pressure(
    action_history: list[dict[str, object]],
    hero_seat: int,
    pot_history: list[int],
) -> CumulativePressure:
    """Calculate hero ATT/DEF pressure already spent on the current line.

    Args:
        action_history: Actions containing ``seat``, ``action``, and ``amount``.
        hero_seat: Hero seat number.
        pot_history: Pot before each action in ``action_history``.

    Returns:
        ATT spent, DEF spent, and raise-count bonus for the street.
    """
    att_spent = 0.0
    def_spent = 0.0
    pending_faced_weight = 0.0
    raise_count = 0

    for index, event in enumerate(action_history):
        action = str(event.get("action", "")).upper()
        seat = event.get("seat")
        amount = float(event.get("amount", 0) or 0)
        pot_before_action = float(pot_history[index]) if index < len(pot_history) else 0.0
        weight = _pressure_weight_from_amount(amount, pot_before_action)

        if action in {"BET", "RAISE"}:
            if action == "RAISE":
                raise_count += 1
            if seat == hero_seat:
                att_spent += weight
            else:
                pending_faced_weight = weight
        elif seat == hero_seat and action in {"CALL", "RAISE"} and pending_faced_weight > 0:
            def_spent += pending_faced_weight
            pending_faced_weight = 0.0
            if action == "RAISE":
                att_spent += weight

    raise_count_bonus = min(0.75, 0.25 * max(0, raise_count - 1))
    return {
        "att_spent": att_spent,
        "def_spent": def_spent,
        "raise_count_bonus": raise_count_bonus,
    }


def apply_mw_modifiers(
    base_att: float,
    base_def: float,
    made_hand_class: str,
    draw_class: str | None,
    board_texture: dict[str, object],
    active_player_count: int,
    position: str,
    pot_type: str,
) -> MwModifierResult:
    """Apply multiway, position, pot-type, and wet-board budget modifiers.

    Args:
        base_att: Base ATT budget after made-hand board modifiers.
        base_def: Base DEF budget after made-hand board modifiers.
        made_hand_class: Made-hand class from :func:`classify_hand`.
        draw_class: Draw class from :func:`classify_hand`, if any.
        board_texture: Board texture dictionary.
        active_player_count: Active players including hero.
        position: ``"IP"``, ``"OOP"``, ``"sandwich"``, or ``"closing_action"``.
        pot_type: One of ``"limp"``, ``"SRP"``, ``"3BP"``, or ``"4BP+"``.

    Returns:
        Adjusted ATT/DEF, draw threshold multiplier, and modifier labels.
    """
    _validate_pot_type(pot_type)
    _validate_mw_position(position)
    if _budget_is_infinite(base_att, base_def):
        return {
            "adjusted_att": base_att,
            "adjusted_def": base_def,
            "draw_threshold_multiplier": 1.0,
            "modifiers_applied": [],
        }

    adjusted_att = base_att
    adjusted_def = base_def
    draw_threshold_multiplier = 1.0
    modifiers: list[str] = []
    opponents_remaining = max(0, active_player_count - 1)
    extra_opponents = max(0, opponents_remaining - 1)

    if extra_opponents > 0:
        if made_hand_class == "trash":
            modifiers.append("mw_trash")
        elif made_hand_class in HIGH_CARD_CLASSES:
            adjusted_def -= 0.35 * extra_opponents
            modifiers.append("mw_high_card")
        else:
            adjusted_att -= 0.35 * extra_opponents
            adjusted_def -= 0.25 * extra_opponents
            modifiers.append("mw_made_hand")
        if draw_class is not None:
            adjusted_att -= 0.25 * extra_opponents
            draw_threshold_multiplier *= max(0.0, 1.0 - 0.08 * extra_opponents)
            modifiers.append("mw_draw")

    if position in {"IP", "closing_action"}:
        adjusted_att += 0.15
        adjusted_def += 0.20
        modifiers.append("position_ip")
    elif position == "sandwich":
        adjusted_att -= 0.30
        adjusted_def -= 0.45
        modifiers.append("position_sandwich")
    elif position == "OOP":
        adjusted_att -= 0.20
        adjusted_def -= 0.30
        modifiers.append("position_oop")

    if pot_type == "limp":
        if _is_second_pair_or_weaker(made_hand_class):
            adjusted_def += 0.20
            modifiers.append("limp_def")
        if made_hand_class in VALUE_HAND_CLASSES:
            adjusted_att += 0.10
            modifiers.append("limp_value_att")
    elif pot_type == "3BP":
        if _is_top_pair_or_weaker(made_hand_class):
            adjusted_att -= 0.20
            adjusted_def -= 0.30
            modifiers.append("3bp_top_pair_or_weaker")
        elif _is_two_pair_or_better(made_hand_class):
            adjusted_att += 0.20
            modifiers.append("3bp_two_pair_plus")
    elif pot_type == "4BP+":
        if made_hand_class in {"top_pair", "second_pair", "third_pair", "fourth_fifth_pair"}:
            adjusted_def -= 0.40
            modifiers.append("4bp_marginal_def")
        if made_hand_class in {"overpair"} or _is_two_pair_or_better(made_hand_class):
            adjusted_att += 0.20
            modifiers.append("4bp_overpair_plus")

    if (
        board_texture.get("overall_texture") in {"wet", "very_wet"}
        and opponents_remaining >= 2
    ):
        if made_hand_class in ONE_PAIR_CLASSES:
            adjusted_att -= 0.40
            adjusted_def -= 0.40
            modifiers.append("wet_multiway_one_pair")
        if draw_class == "strong_draw":
            adjusted_att += 0.20
            modifiers.append("wet_multiway_strong_draw")
        if draw_class == "weak_draw":
            draw_threshold_multiplier *= 0.85
            modifiers.append("wet_multiway_weak_draw")

    return {
        "adjusted_att": _round_budget(max(0.0, adjusted_att)),
        "adjusted_def": _round_budget(max(0.0, adjusted_def)),
        "draw_threshold_multiplier": max(0.0, draw_threshold_multiplier),
        "modifiers_applied": modifiers,
    }


def compute_full_budget(
    hero_cards: list[str],
    board_cards: list[str],
    pot_type: str,
    position: str,
    active_player_count: int,
    street: str,
    action_history: list[dict[str, object]],
    hero_seat: int,
    pot_history: list[int],
) -> FullBudget:
    """Run the deterministic Context Engine budget pipeline.

    Args:
        hero_cards: Two hero cards.
        board_cards: Three to five board cards.
        pot_type: One of ``"limp"``, ``"SRP"``, ``"3BP"``, or ``"4BP+"``.
        position: ``"IP"``, ``"OOP"``, ``"sandwich"``, or ``"closing_action"``.
        active_player_count: Active players including hero.
        street: One of ``"flop"``, ``"turn"``, or ``"river"``.
        action_history: Street action history.
        hero_seat: Hero seat number.
        pot_history: Pot before each action.

    Returns:
        Full budget context including remaining budget, draw threshold, and actions.
    """
    board_texture = classify_board_texture(board_cards)
    hand_class = classify_hand(hero_cards, board_cards)
    made_hand_class = hand_class["made_hand_class"]
    draw_class = hand_class["draw_class"]
    made_budget = get_made_hand_budget(
        made_hand_class,
        hand_class["kicker_class"],
        board_texture,
        pot_type,
        street,
    )
    draw_position = "IP" if position in {"IP", "closing_action"} else "OOP"
    draw_budget = get_draw_budget(draw_class, pot_type, street, draw_position)
    special_override = get_special_board_override(
        board_texture["special_board"],
        hand_class,
        board_texture,
    )
    mw_budget = apply_mw_modifiers(
        made_budget["base_att"],
        made_budget["base_def"],
        made_hand_class,
        draw_class,
        board_texture,
        active_player_count,
        position,
        pot_type,
    )
    adjusted_att = mw_budget["adjusted_att"]
    adjusted_def = mw_budget["adjusted_def"]
    if hand_class["combo_applied"]:
        adjusted_def = _add_budget(adjusted_def, draw_budget["combo_bonus"])
    adjusted_att = _max_budget(adjusted_att, draw_budget["att"])

    pressure = calculate_cumulative_pressure(action_history, hero_seat, pot_history)
    remaining_att = _subtract_budget(adjusted_att, pressure["att_spent"])
    remaining_def = _subtract_budget(adjusted_def, pressure["def_spent"])
    draw_threshold = _select_draw_threshold(draw_budget, draw_position)
    if draw_threshold is not None:
        draw_threshold *= mw_budget["draw_threshold_multiplier"]

    viable_actions = determine_viable_actions(
        remaining_att=remaining_att,
        remaining_def=remaining_def,
        draw_threshold=draw_threshold,
        draw_class=draw_class,
        made_hand_class=made_hand_class,
        board_texture=board_texture,
        position=position,
        street=street,
        spr=5.0,
        legal_actions=["fold", "check", "call", "bet", "raise", "all_in"],
        is_nuts=hand_class["is_nuts"],
        active_player_count=active_player_count,
    )
    return {
        "board_texture": board_texture,
        "hand_class": hand_class,
        "base_att": made_budget["base_att"],
        "base_def": made_budget["base_def"],
        "draw_budget": draw_budget,
        "special_override": special_override,
        "adjusted_att": adjusted_att,
        "adjusted_def": adjusted_def,
        "att_spent": pressure["att_spent"],
        "def_spent": pressure["def_spent"],
        "remaining_att": remaining_att,
        "remaining_def": remaining_def,
        "draw_threshold": draw_threshold,
        "viable_actions": viable_actions,
        "budget_verdict": _budget_verdict(
            made_budget["is_infinite"],
            remaining_att,
            remaining_def,
            draw_threshold,
        ),
    }


def determine_viable_actions(
    remaining_att: float,
    remaining_def: float,
    draw_threshold: float | None,
    draw_class: str | None,
    made_hand_class: str,
    board_texture: dict[str, object],
    position: str,
    street: str,
    spr: float,
    legal_actions: list[str],
    is_nuts: bool,
    active_player_count: int,
) -> list[str]:
    """Determine viable actions from remaining budgets and context.

    Args:
        remaining_att: Remaining ATT budget.
        remaining_def: Remaining DEF budget.
        draw_threshold: Draw call threshold in percent pot, if active.
        draw_class: Draw class, if any.
        made_hand_class: Made-hand class.
        board_texture: Board texture dictionary.
        position: ``"IP"``, ``"OOP"``, ``"sandwich"``, or ``"closing_action"``.
        street: One of ``"flop"``, ``"turn"``, or ``"river"``.
        spr: Stack-to-pot ratio.
        legal_actions: Legal actions for the current decision.
        is_nuts: Whether hero currently has the nuts.
        active_player_count: Active players including hero.

    Returns:
        Legal viable actions.
    """
    legal_action_set = set(legal_actions)
    viable: set[str] = set()
    facing_bet = "call" in legal_action_set

    if "fold" in legal_action_set:
        viable.add("fold")
    if "check" in legal_action_set:
        viable.add("check")
    if facing_bet and (remaining_def > 0 or draw_threshold is not None):
        viable.add("call")

    can_attack = remaining_att > 0 or is_nuts
    safe_for_thin_value = not bool(board_texture.get("paired")) and board_texture.get(
        "special_board"
    ) is None
    can_value_bet = can_attack and _is_at_least(made_hand_class, "top_pair")
    can_thin_value = (
        0.4 <= remaining_att < 1.0
        and safe_for_thin_value
        and position in {"IP", "closing_action"}
    )
    can_semi_bluff = draw_class is not None and remaining_att > 0 and position != "sandwich"
    can_trash_bluff = (
        made_hand_class == "trash"
        and remaining_att >= 0
        and ("check" in legal_action_set or street == "river")
    )
    if "bet" in legal_action_set and (
        can_value_bet or can_thin_value or can_semi_bluff or can_trash_bluff
    ):
        viable.add("bet")

    special_board_danger = board_texture.get("special_board") is not None and not is_nuts
    value_heavy_multiway = active_player_count >= 3 and not _is_at_least(
        made_hand_class,
        "two_pair",
    )
    can_raise_value = (
        is_nuts
        or (
            remaining_att >= 2.0
            and not special_board_danger
            and not value_heavy_multiway
        )
    )
    can_raise_draw = (
        draw_class == "strong_draw"
        and street == "flop"
        and position != "sandwich"
        and spr > 1.0
    )
    if "raise" in legal_action_set and (can_raise_value or can_raise_draw):
        viable.add("raise")

    low_spr_commit = (
        is_nuts
        or _is_at_least(made_hand_class, "two_pair")
        or made_hand_class == "overpair"
        or draw_class == "strong_draw"
    )
    if "all_in" in legal_action_set and spr <= 1.5 and low_spr_commit:
        viable.add("all_in")
    if spr <= 0.7 and _is_at_least(made_hand_class, "top_pair"):
        viable.discard("fold")

    return [action for action in legal_actions if action in viable]


def build_context_prompt(
    budget_result: dict[str, object],
    game_context: dict[str, object],
) -> str:
    """Build the structured Context Engine prompt for the LLM policy proposer.

    Args:
        budget_result: Result dictionary from :func:`compute_full_budget`.
        game_context: GameState-derived context values used in SPEC.md section 9.4.16.

    Returns:
        Complete prompt string. No API call is performed.
    """
    board_texture = _dict_value(budget_result, "board_texture")
    hand_class = _dict_value(budget_result, "hand_class")
    legal_actions = _list_str_value(game_context.get("legal_actions"))
    viable_actions = _filter_legal_viable_actions(
        _list_str_value(budget_result.get("viable_actions")),
        legal_actions,
    )
    compressed_actions = _compress_viable_actions(viable_actions)
    board_flags = _board_flags(board_texture)
    made_hand_class = str(hand_class.get("made_hand_class"))
    draw_class = _object_as_optional_str(hand_class.get("draw_class"))
    hand_guidance = _hand_guidance(made_hand_class, draw_class)
    board_guidance = _board_guidance(board_texture)
    pot_bb = _float_context(game_context, "pot_bb")

    return "\n".join(
        [
            "SYSTEM:",
            "You are a poker decision assistant. Choose only from viable_actions.",
            'Return JSON only. Do not explain unless "reason" field is requested.',
            "",
            "SITUATION:",
            f"street: {game_context.get('street')}",
            f"pot_bb: {_format_prompt_number(pot_bb)}",
            f"effective_stack_bb: {_format_prompt_number(_float_context(game_context, 'effective_stack_bb'))}",
            f"spr: {_format_prompt_number(_float_context(game_context, 'spr'))}",
            f"hero_position: {game_context.get('hero_position')}",
            f"active_players: {game_context.get('active_players')}",
            f"hero_cards: {game_context.get('hero_cards')}",
            f"board_cards: {game_context.get('board_cards')}",
            f"legal_actions: {legal_actions}",
            f"action_history: {game_context.get('action_history')}",
            "",
            "COMPUTED_CONTEXT:",
            f"pot_type: {game_context.get('pot_type', 'unknown')}",
            f"initiative: {game_context.get('initiative')}",
            f"board_texture: {board_texture.get('overall_texture')}",
            f"board_flags: {board_flags}",
            f"special_board: {board_texture.get('special_board')}",
            f"made_hand_class: {made_hand_class}",
            f"draw_class: {draw_class}",
            f"kicker_class: {hand_class.get('kicker_class')}",
            (
                f"mw_context: {game_context.get('position', game_context.get('hero_position'))}, "
                f"players_behind={game_context.get('players_behind')}"
            ),
            "",
            "BUDGET:",
            f"base_att: {_format_budget_for_prompt(budget_result.get('base_att'))}",
            f"base_def: {_format_budget_for_prompt(budget_result.get('base_def'))}",
            f"adjusted_att: {_format_budget_for_prompt(budget_result.get('adjusted_att'))}",
            f"adjusted_def: {_format_budget_for_prompt(budget_result.get('adjusted_def'))}",
            f"pressure_att_spent: {_format_budget_for_prompt(budget_result.get('att_spent'))}",
            f"pressure_def_spent: {_format_budget_for_prompt(budget_result.get('def_spent'))}",
            f"remaining_att: {_format_budget_for_prompt(budget_result.get('remaining_att'))}",
            f"remaining_def: {_format_budget_for_prompt(budget_result.get('remaining_def'))}",
            f"draw_threshold: {_format_draw_threshold(budget_result.get('draw_threshold'))}",
            f"budget_verdict: {budget_result.get('budget_verdict')}",
            "",
            "SKILL_GUIDANCE:",
            hand_guidance,
            board_guidance,
            "",
            "VIABLE_ACTIONS:",
            *_format_viable_action_lines(compressed_actions, pot_bb),
            "",
            "OUTPUT_SCHEMA:",
            '{"action":"fold|check|call|bet|raise|all_in","amount_bb":number|null,"reason":"short"}',
        ]
    )


def validate_llm_output(
    raw_json_str: str,
    viable_actions: list[str],
    legal_actions: list[str],
    effective_stack_bb: float,
) -> dict[str, object]:
    """Validate and correct an LLM action JSON payload.

    Args:
        raw_json_str: Raw LLM response string.
        viable_actions: Actions allowed by Context Engine logic.
        legal_actions: Actions legal in the current GameState.
        effective_stack_bb: Effective stack used to clip bet sizes.

    Returns:
        Corrected action dictionary with validity and correction metadata.
    """
    safe_viable_actions = [action for action in viable_actions if action in legal_actions]
    if not safe_viable_actions:
        return _validator_result(None, None, "", False, "no_viable_actions")

    try:
        parsed = json.loads(raw_json_str)
    except json.JSONDecodeError:
        fallback = _fallback_action(safe_viable_actions)
        return _validator_result(
            fallback,
            None,
            "",
            False,
            "json_parse_failed",
        )
    if not isinstance(parsed, dict):
        fallback = _fallback_action(safe_viable_actions)
        return _validator_result(fallback, None, "", False, "json_not_object")

    action = _normalize_action(parsed.get("action"))
    amount_bb = parsed.get("amount_bb")
    reason = _truncate_reason(parsed.get("reason"))
    correction: str | None = None
    is_valid = True

    if action not in safe_viable_actions:
        corrected_action = _correct_invalid_action(action, safe_viable_actions)
        if corrected_action is None:
            corrected_action = _fallback_action(safe_viable_actions)
            correction = "fallback_action"
        else:
            correction = f"action_corrected_to_{corrected_action}"
        action = corrected_action
        is_valid = False

    normalized_amount, amount_correction = _normalize_llm_amount(
        action,
        amount_bb,
        effective_stack_bb,
    )
    if amount_correction is not None:
        correction = amount_correction if correction is None else f"{correction}; {amount_correction}"
        is_valid = False

    return _validator_result(action, normalized_amount, reason, is_valid, correction)


def _dict_value(container: dict[str, object], key: str) -> dict[str, object]:
    value = container.get(key)
    if isinstance(value, dict):
        return value
    return {}


def _list_str_value(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _float_context(container: dict[str, object], key: str) -> float:
    value = container.get(key, 0.0)
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _filter_legal_viable_actions(
    viable_actions: list[str],
    legal_actions: list[str],
) -> list[str]:
    legal_action_set = set(legal_actions)
    return [action for action in viable_actions if action in legal_action_set]


def _compress_viable_actions(viable_actions: list[str]) -> list[str]:
    viable_action_set = set(viable_actions)
    ordered = [action for action in VIABLE_ACTION_PRIORITY if action in viable_action_set]
    return ordered[:5]


def _board_flags(board_texture: dict[str, object]) -> list[str]:
    flags: list[str] = []
    for key in ("suit_texture", "overall_texture", "special_board"):
        value = board_texture.get(key)
        if value is not None:
            flags.append(str(value))
    for key in ("paired", "flush_possible", "straight_possible"):
        if bool(board_texture.get(key)):
            flags.append(key)
    rank_texture = board_texture.get("rank_texture")
    if isinstance(rank_texture, list):
        flags.extend(str(item) for item in rank_texture)
    return flags


def _hand_guidance(made_hand_class: str, draw_class: str | None) -> str:
    if made_hand_class in SKILL_GUIDANCE_TEMPLATES:
        return SKILL_GUIDANCE_TEMPLATES[made_hand_class]
    if draw_class in SKILL_GUIDANCE_TEMPLATES:
        return SKILL_GUIDANCE_TEMPLATES[draw_class]
    return "Use the computed budget and viable actions. Prefer lower variance with marginal hands."


def _board_guidance(board_texture: dict[str, object]) -> str:
    if bool(board_texture.get("paired")):
        return BOARD_GUIDANCE_TEMPLATES["paired"]
    texture = str(board_texture.get("overall_texture", "dry"))
    return BOARD_GUIDANCE_TEMPLATES.get(texture, BOARD_GUIDANCE_TEMPLATES["dry"])


def _format_prompt_number(value: float) -> str:
    return f"{value:.1f}"


def _format_budget_for_prompt(value: object) -> str:
    if isinstance(value, int | float):
        float_value = float(value)
        if math.isinf(float_value):
            return "∞"
        return f"{float_value:.1f}"
    return "None"


def _format_draw_threshold(value: object) -> str:
    if isinstance(value, int | float):
        return f"{round(float(value))}%pot"
    return "None"


def _format_viable_action_lines(actions: list[str], pot_bb: float) -> list[str]:
    if not actions:
        return ["1. no_action"]
    return [
        f"{index}. {_format_action_hint(action, pot_bb)}"
        for index, action in enumerate(actions, start=1)
    ]


def _format_action_hint(action: str, pot_bb: float) -> str:
    small_size = pot_bb * 0.30
    large_size = pot_bb * 0.50
    if action == "bet":
        return f"bet {small_size:.1f}-{large_size:.1f} BB (30-50% pot)"
    if action == "raise":
        return f"raise {large_size:.1f}-{pot_bb:.1f} BB (50-100% pot)"
    if action == "all_in":
        return "all_in effective stack"
    if action == "call":
        return "call current bet"
    return action


def _normalize_action(action: object) -> str | None:
    if action is None:
        return None
    normalized = str(action).strip().lower().replace("-", "_")
    if normalized == "allin":
        return "all_in"
    return normalized


def _correct_invalid_action(action: str | None, viable_actions: list[str]) -> str | None:
    if action in {"bet", "raise", "all_in"}:
        if "call" in viable_actions:
            return "call"
        if "check" in viable_actions:
            return "check"
    if action == "call" and "fold" in viable_actions:
        return "fold"
    return None


def _fallback_action(viable_actions: list[str]) -> str:
    for action in ("check", "fold", "call"):
        if action in viable_actions:
            return action
    return viable_actions[0]


def _truncate_reason(reason: object) -> str:
    if reason is None:
        return ""
    return str(reason)[:200]


def _normalize_llm_amount(
    action: str | None,
    amount_bb: object,
    effective_stack_bb: float,
) -> tuple[float | None, str | None]:
    if action in {None, "fold", "check", "call"}:
        return None, None if amount_bb is None else "amount_normalized_to_none"
    if action == "all_in":
        return max(0.0, effective_stack_bb), "amount_clipped_to_stack"

    parsed_amount = _parse_optional_amount(amount_bb)
    if parsed_amount is None:
        return None, "amount_missing"
    clipped_amount = min(max(0.0, parsed_amount), max(0.0, effective_stack_bb))
    if clipped_amount != parsed_amount:
        return clipped_amount, "amount_clipped_to_stack"
    return clipped_amount, None


def _parse_optional_amount(amount_bb: object) -> float | None:
    if isinstance(amount_bb, int | float):
        return float(amount_bb)
    if isinstance(amount_bb, str):
        try:
            return float(amount_bb)
        except ValueError:
            return None
    return None


def _validator_result(
    action: str | None,
    amount_bb: float | None,
    reason: str,
    is_valid: bool,
    correction_applied: str | None,
) -> dict[str, object]:
    return {
        "action": action,
        "amount_bb": amount_bb,
        "reason": reason,
        "is_valid": is_valid,
        "correction_applied": correction_applied,
    }


def _parse_card(card_str: str) -> eval7.Card:
    """Convert a card string to an eval7 card."""
    _rank_value(card_str)
    _suit_value(card_str)
    return eval7.Card(card_str)


def _parse_cards(card_strs: list[str]) -> list[eval7.Card]:
    """Convert card strings to eval7 cards."""
    return [_parse_card(card_str) for card_str in card_strs]


def _validate_pot_type(pot_type: str) -> None:
    if pot_type not in {"limp", "SRP", "3BP", "4BP+"}:
        raise ValueError(f"Invalid pot type: {pot_type}")


def _validate_street(street: str) -> None:
    if street not in {"flop", "turn", "river"}:
        raise ValueError(f"Invalid street: {street}")


def _validate_position(position: str) -> None:
    if position not in {"IP", "OOP"}:
        raise ValueError(f"Invalid position: {position}")


def _validate_mw_position(position: str) -> None:
    if position not in {"IP", "OOP", "sandwich", "closing_action"}:
        raise ValueError(f"Invalid MW position: {position}")


def _object_as_optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _budget_is_infinite(base_att: float, base_def: float) -> bool:
    return math.isinf(base_att) or math.isinf(base_def)


def _lookup_made_hand_base_budget(made_hand_class: str, pot_type: str) -> tuple[float, float]:
    budgets = MADE_HAND_BASE_BUDGETS.get(made_hand_class)
    if budgets is None:
        raise ValueError(f"Unknown made hand class: {made_hand_class}")
    return budgets.get(pot_type, budgets.get("default", (0.0, 0.0)))


def _apply_kicker_budget_modifier(
    base_att: float,
    base_def: float,
    made_hand_class: str,
    kicker_class: str | None,
) -> tuple[float, float]:
    if made_hand_class not in {"top_pair", "second_pair", "third_pair"}:
        return base_att, base_def
    if kicker_class is None:
        return base_att, base_def
    att_modifier, def_modifier = KICKER_BUDGET_MODIFIERS.get(kicker_class, (0.0, 0.0))
    return max(0.0, base_att + att_modifier), max(0.0, base_def + def_modifier)


def _apply_board_budget_modifiers(
    base_att: float,
    base_def: float,
    made_hand_class: str,
    board_texture: dict[str, object],
    street: str,
) -> tuple[float, float, list[str]]:
    if _budget_is_infinite(base_att, base_def):
        return base_att, base_def, []

    modifiers: list[str] = []
    if bool(board_texture.get("paired")) and made_hand_class in _one_pair_classes():
        penalty = _paired_board_penalty(made_hand_class)
        base_att -= penalty
        base_def -= penalty
        modifiers.append("paired")

    suit_texture = _object_as_optional_str(board_texture.get("suit_texture"))
    flush_possible = bool(board_texture.get("flush_possible"))
    if suit_texture in {"four_flush", "five_flush"}:
        base_att = 0.0
        base_def = min(base_def, 0.6)
        modifiers.append("one_card_flush")
    elif flush_possible:
        penalty = _flush_possible_penalty(street)
        base_att -= penalty
        base_def -= penalty
        modifiers.append("flush_possible")

    straight_penalty, straight_modifier = _straight_penalty(board_texture, street)
    if straight_modifier is not None:
        base_att -= straight_penalty
        base_def -= straight_penalty
        modifiers.append(straight_modifier)

    return max(0.0, base_att), max(0.0, base_def), modifiers


def _one_pair_classes() -> set[str]:
    return {"overpair", "top_pair", "second_pair", "third_pair", "fourth_fifth_pair"}


def _paired_board_penalty(made_hand_class: str) -> float:
    if made_hand_class in {"overpair", "top_pair"}:
        return 0.5
    if made_hand_class == "second_pair":
        return 0.4
    return 0.3


def _flush_possible_penalty(street: str) -> float:
    return {"flop": 1.1, "turn": 0.9, "river": 0.7}[street]


def _straight_penalty(
    board_texture: dict[str, object],
    street: str,
) -> tuple[float, str | None]:
    rank_texture = board_texture.get("rank_texture", [])
    rank_texture_values = rank_texture if isinstance(rank_texture, list) else []
    if "one_card_straight_oesd" in rank_texture_values:
        return 2.5, "one_card_straight_oesd"
    if "one_card_straight_gutshot" in rank_texture_values:
        return 1.5, "one_card_straight_gutshot"
    if not bool(board_texture.get("straight_possible")):
        return 0.0, None
    if "straight_possible_multi" in rank_texture_values:
        return {"flop": 0.6, "turn": 0.5, "river": 0.4}[street], "straight_possible_multi"
    return {"flop": 0.4, "turn": 0.3, "river": 0.2}[street], "straight_possible"


def _empty_draw_budget() -> DrawBudget:
    return {
        "att": 0.0,
        "threshold_ip": None,
        "threshold_oop": None,
        "check_raise_ip": None,
        "check_raise_oop": None,
        "combo_bonus": 0.0,
    }


def _zero_as_none(value: float | None) -> float | None:
    if value is None or value <= 0:
        return None
    return value


def _downgrade_draw_for_pot_type(draw_class: str, pot_type: str) -> str:
    if pot_type not in {"3BP", "4BP+"} or not draw_class.endswith("_overcard_draw"):
        return draw_class
    if draw_class == "weak_overcard_draw":
        return "weak_overcard_draw"
    downgrade_steps = 1 if pot_type == "3BP" else 2
    return _downgrade_draw_class(draw_class, downgrade_steps)


def _downgrade_draw_class(draw_class: str, steps: int) -> str:
    if draw_class not in DRAW_DOWNGRADE_CHAIN:
        return draw_class
    index = DRAW_DOWNGRADE_CHAIN.index(draw_class)
    downgraded_index = min(len(DRAW_DOWNGRADE_CHAIN) - 1, index + steps)
    return DRAW_DOWNGRADE_CHAIN[downgraded_index]


def _pressure_weight_from_amount(amount: float, pot_before_action: float) -> float:
    if amount <= 0 or pot_before_action <= 0:
        return 0.0
    return calculate_pressure_weight((amount / pot_before_action) * 100.0)


def _round_budget(value: float) -> float:
    if math.isinf(value):
        return value
    return round(value, 2)


def _add_budget(value: float, addition: float) -> float:
    if math.isinf(value):
        return value
    return _round_budget(value + addition)


def _max_budget(first: float, second: float) -> float:
    if math.isinf(first) or math.isinf(second):
        return float("inf")
    return _round_budget(max(first, second))


def _subtract_budget(value: float, spent: float) -> float:
    if math.isinf(value):
        return value
    return _round_budget(max(0.0, value - spent))


def _is_at_least(made_hand_class: str, minimum_class: str) -> bool:
    return MADE_HAND_STRENGTH.get(made_hand_class, 0) >= MADE_HAND_STRENGTH[minimum_class]


def _is_top_pair_or_weaker(made_hand_class: str) -> bool:
    return MADE_HAND_STRENGTH.get(made_hand_class, 0) <= MADE_HAND_STRENGTH["top_pair"]


def _is_second_pair_or_weaker(made_hand_class: str) -> bool:
    return MADE_HAND_STRENGTH.get(made_hand_class, 0) <= MADE_HAND_STRENGTH["second_pair"]


def _is_two_pair_or_better(made_hand_class: str) -> bool:
    return _is_at_least(made_hand_class, "two_pair")


def _select_draw_threshold(draw_budget: DrawBudget, position: str) -> float | None:
    if position == "IP":
        return draw_budget["threshold_ip"]
    return draw_budget["threshold_oop"]


def _budget_verdict(
    is_infinite: bool,
    remaining_att: float,
    remaining_def: float,
    draw_threshold: float | None,
) -> str:
    if is_infinite:
        return "nuts"
    if remaining_att >= 2.0:
        return "strong_value"
    if 0.4 <= remaining_att < 2.0:
        return "thin_value"
    if draw_threshold is not None:
        return "draw_continue"
    if remaining_def > 0:
        return "marginal_defense"
    return "fold_lean"


def _validate_cards(cards: list[str], min_count: int, max_count: int) -> None:
    if not min_count <= len(cards) <= max_count:
        raise ValueError(f"Expected {min_count}-{max_count} cards, got {len(cards)}")
    if len(set(cards)) != len(cards):
        raise ValueError("Duplicate cards are not allowed")
    for card in cards:
        _rank_value(card)
        _suit_value(card)


def _rank_value(card: str) -> int:
    rank = card[:-1].upper()
    if rank == "10":
        rank = "T"
    if rank not in RANK_TO_VALUE:
        raise ValueError(f"Invalid card rank: {card}")
    return RANK_TO_VALUE[rank]


def _suit_value(card: str) -> str:
    if len(card) < 2 or card[-1].lower() not in {"h", "d", "c", "s"}:
        raise ValueError(f"Invalid card suit: {card}")
    return card[-1].lower()


def _classify_suit_texture(board_count: int, max_suit_count: int) -> str:
    if max_suit_count == 5:
        return "five_flush"
    if max_suit_count == 4:
        return "four_flush"
    if max_suit_count == 3:
        return "monotone" if board_count == 3 else "three_flush"
    if max_suit_count == 2:
        return "two_tone"
    return "rainbow"


def _classify_rank_texture(ranks: list[int]) -> list[str]:
    rank_counts = Counter(ranks)
    textures: list[str] = []
    count_values = sorted(rank_counts.values(), reverse=True)
    if any(count == 2 for count in rank_counts.values()):
        textures.append("paired")
    if any(count == 3 for count in rank_counts.values()):
        textures.append("trips_board")
    if any(count == 4 for count in rank_counts.values()):
        textures.append("quads_board")
    if count_values.count(2) >= 2:
        textures.append("double_paired")
    if _has_straight_draw_texture(set(ranks)):
        textures.append("straight_possible")
        textures.append("one_card_straight")
    return textures


def _classify_special_board(
    rank_counts: Counter[int],
    board_flush: bool,
    board_straight: bool,
    board_full_house: bool,
) -> str | None:
    count_values = sorted(rank_counts.values(), reverse=True)
    if any(count == 4 for count in rank_counts.values()):
        return "quads_board"
    if board_full_house:
        return "board_full_house"
    if any(count == 3 for count in rank_counts.values()):
        return "trips_board"
    if count_values.count(2) >= 2:
        return "double_paired"
    if board_flush:
        return "board_flush"
    if board_straight:
        return "board_straight"
    return None


def _classify_overall_texture(
    suit_texture: str,
    flush_possible: bool,
    straight_possible: bool,
    paired: bool,
    connected_count: int,
) -> str:
    if flush_possible and straight_possible:
        return "very_wet"
    if suit_texture in {"monotone", "four_flush", "five_flush"}:
        return "very_wet"
    if flush_possible or straight_possible:
        return "wet"
    if suit_texture == "two_tone" or connected_count == 1 or paired:
        return "slightly_wet"
    return "dry"


def _is_complete_straight(ranks: set[int]) -> bool:
    normalized = _normalize_ace_low(ranks)
    return any(all(rank in normalized for rank in window) for window in STRAIGHT_WINDOWS)


def _has_straight_draw_texture(ranks: set[int]) -> bool:
    normalized = _normalize_ace_low(ranks)
    return any(sum(rank in normalized for rank in window) >= 3 for window in STRAIGHT_WINDOWS)


def _count_straight_draw_windows(ranks: set[int]) -> int:
    normalized = _normalize_ace_low(ranks)
    return sum(sum(rank in normalized for rank in window) >= 3 for window in STRAIGHT_WINDOWS)


def _normalize_ace_low(ranks: set[int]) -> set[int]:
    normalized = set(ranks)
    if 14 in normalized:
        normalized.add(1)
    return normalized


def _classify_made_hand(hero_cards: list[str], board_cards: list[str]) -> str:
    hand_type = _hand_type(hero_cards, board_cards)
    if hand_type in {"straight_flush", "four_of_a_kind"}:
        return "nuts"
    if hand_type == "full_house":
        return "nuts" if _check_nuts(hero_cards, board_cards)[0] else "set"
    if hand_type == "flush":
        return "flush"
    if hand_type == "straight":
        return "straight"
    if hand_type == "three_of_a_kind":
        return _classify_three_of_a_kind(hero_cards, board_cards)
    if hand_type == "two_pair":
        if _is_board_only_two_pair(hero_cards, board_cards):
            return _classify_high_card(hero_cards)
        return "two_pair"
    if hand_type == "one_pair":
        if _is_board_only_one_pair(hero_cards, board_cards):
            return _classify_high_card(hero_cards)
        return _classify_pair(hero_cards, board_cards)
    return _classify_high_card(hero_cards)


def _hand_type(hero_cards: list[str], board_cards: list[str]) -> str:
    score = eval7.evaluate(_parse_cards(hero_cards + board_cards))
    raw_hand_type = eval7.handtype(score).lower()
    return HAND_TYPE_MAP.get(raw_hand_type, raw_hand_type.replace(" ", "_"))


def _classify_three_of_a_kind(hero_cards: list[str], board_cards: list[str]) -> str:
    hero_ranks = [_rank_value(card) for card in hero_cards]
    board_ranks = [_rank_value(card) for card in board_cards]
    board_counts = Counter(board_ranks)
    if hero_ranks[0] == hero_ranks[1] and board_counts[hero_ranks[0]] == 1:
        return "set"
    if any(count >= 3 for count in board_counts.values()):
        return "trips"
    if any(board_counts[rank] == 2 for rank in hero_ranks):
        return "trips"
    return "trips"


def _is_board_only_one_pair(hero_cards: list[str], board_cards: list[str]) -> bool:
    hero_ranks = [_rank_value(card) for card in hero_cards]
    if hero_ranks[0] == hero_ranks[1]:
        return False

    board_counts = Counter(_rank_value(card) for card in board_cards)
    board_pair_ranks = {rank for rank, count in board_counts.items() if count == 2}
    if len(board_pair_ranks) != 1:
        return False
    return all(rank not in board_pair_ranks for rank in hero_ranks)


def _is_board_only_two_pair(hero_cards: list[str], board_cards: list[str]) -> bool:
    hero_ranks = {_rank_value(card) for card in hero_cards}
    board_counts = Counter(_rank_value(card) for card in board_cards)
    board_pair_ranks = {rank for rank, count in board_counts.items() if count == 2}
    if len(board_pair_ranks) < 2:
        return False
    return hero_ranks.isdisjoint(board_pair_ranks)


def _classify_pair(hero_cards: list[str], board_cards: list[str]) -> str:
    hero_ranks = [_rank_value(card) for card in hero_cards]
    board_ranks = [_rank_value(card) for card in board_cards]
    board_unique_desc = sorted(set(board_ranks), reverse=True)

    if hero_ranks[0] == hero_ranks[1]:
        pair_rank = hero_ranks[0]
        if pair_rank > max(board_unique_desc):
            return "overpair"
        if pair_rank == board_unique_desc[0]:
            return "top_pair"
        overcards_on_board = sum(rank > pair_rank for rank in board_unique_desc)
        if overcards_on_board == 1:
            return "second_pair"
        if overcards_on_board == 2:
            return "third_pair"
        return "fourth_fifth_pair"

    hero_pair_ranks = [rank for rank in hero_ranks if rank in board_unique_desc]
    if not hero_pair_ranks:
        return "weak_showdown"
    pair_rank = max(hero_pair_ranks)
    pair_index = board_unique_desc.index(pair_rank)
    if pair_index == 0:
        return "top_pair"
    if pair_index == 1:
        return "second_pair"
    if pair_index == 2:
        return "third_pair"
    return "fourth_fifth_pair"


def _classify_high_card(hero_cards: list[str]) -> str:
    hero_ranks = sorted((_rank_value(card) for card in hero_cards), reverse=True)
    hero_high = hero_ranks[0]
    hero_second = hero_ranks[1]
    if hero_high == 14:
        return "nuts_high"
    if hero_high == 13 and hero_second >= 10:
        return "nuts_high"
    if hero_high == 13 and hero_second < 10:
        return "second_high"
    if hero_high == 12:
        return "second_high"
    if hero_high >= 10:
        return "weak_showdown"
    return "trash"


def _classify_kicker(
    hero_cards: list[str],
    board_cards: list[str],
    made_hand_class: str,
) -> str | None:
    if made_hand_class not in {"top_pair", "overpair", "second_pair", "third_pair"}:
        return None

    hero_ranks = [_rank_value(card) for card in hero_cards]
    board_ranks = [_rank_value(card) for card in board_cards]
    board_unique = set(board_ranks)
    pair_rank = _find_pair_rank(hero_ranks, board_ranks, made_hand_class)
    if pair_rank is None:
        return None

    if hero_ranks[0] == hero_ranks[1]:
        non_pair_board = [rank for rank in board_unique if rank != pair_rank]
        if not non_pair_board:
            return None
        kicker_rank = max(non_pair_board)
    else:
        kickers = [rank for rank in hero_ranks if rank != pair_rank]
        if not kickers:
            return None
        kicker_rank = max(kickers)

    possible_kickers = [
        rank
        for rank in range(14, 1, -1)
        if rank not in board_unique and rank != pair_rank
    ]
    if kicker_rank not in possible_kickers:
        return "weak_kicker"
    kicker_position = possible_kickers.index(kicker_rank)
    if kicker_position == 0:
        return "top_kicker"
    if kicker_position == 1:
        return "second_kicker"
    if kicker_position == 2:
        return "third_kicker"
    return "weak_kicker"


def _find_pair_rank(
    hero_ranks: list[int],
    board_ranks: list[int],
    made_hand_class: str,
) -> int | None:
    if hero_ranks[0] == hero_ranks[1]:
        return hero_ranks[0]
    board_unique_desc = sorted(set(board_ranks), reverse=True)
    hero_pair_ranks = [rank for rank in hero_ranks if rank in board_unique_desc]
    if not hero_pair_ranks:
        return None
    if made_hand_class == "top_pair":
        return board_unique_desc[0]
    if made_hand_class == "second_pair" and len(board_unique_desc) > 1:
        return board_unique_desc[1]
    if made_hand_class == "third_pair" and len(board_unique_desc) > 2:
        return board_unique_desc[2]
    return max(hero_pair_ranks)


def _classify_draw(hero_cards: list[str], board_cards: list[str]) -> tuple[str | None, int]:
    if len(board_cards) == 5:
        return None, 0

    flush_draw, flush_outs, nut_flush_draw = _detect_flush_draw(hero_cards, board_cards)
    straight_draw, straight_outs = _detect_straight_draw(hero_cards, board_cards)
    backdoor_flush = _detect_backdoor_flush(hero_cards, board_cards)
    backdoor_straight = _detect_backdoor_straight(hero_cards, board_cards)
    paired_board = classify_board_texture(board_cards)["paired"]
    suit_texture = classify_board_texture(board_cards)["suit_texture"]

    if paired_board and flush_draw:
        flush_outs = max(0, flush_outs - 1)
    if suit_texture == "monotone" and straight_draw:
        straight_outs = max(0, straight_outs - 2)

    total_outs = flush_outs + straight_outs
    made_hand_class = _classify_made_hand(hero_cards, board_cards)
    has_pair = made_hand_class not in {
        "nuts_high",
        "second_high",
        "weak_showdown",
        "trash",
    }

    if flush_draw and straight_draw == "oesd":
        if nut_flush_draw:
            return "strong_draw", max(total_outs, 15)
        return "medium_strong_draw", max(total_outs, 12)
    if flush_draw and nut_flush_draw and has_pair:
        return "strong_draw", max(flush_outs + 3, 12)
    if flush_draw and nut_flush_draw:
        return "medium_strong_draw", flush_outs
    if flush_draw:
        if paired_board:
            return "medium_weak_draw", flush_outs
        return "medium_draw", flush_outs
    if straight_draw == "oesd":
        return "medium_draw", straight_outs
    if straight_draw == "gutshot":
        if backdoor_flush:
            return "medium_weak_draw", max(straight_outs + 2, 6)
        return "weak_draw", straight_outs

    overcard_class = _classify_overcard_draw(
        hero_cards,
        board_cards,
        backdoor_flush,
        backdoor_straight,
    )
    if overcard_class is not None:
        return overcard_class, 0
    return None, 0


def _detect_flush_draw(hero_cards: list[str], board_cards: list[str]) -> tuple[bool, int, bool]:
    all_cards = hero_cards + board_cards
    suit_counts = Counter(_suit_value(card) for card in all_cards)
    hero_suit_counts = Counter(_suit_value(card) for card in hero_cards)
    for suit, count in suit_counts.items():
        if count >= 5:
            return False, 0, False
        if count == 4 and hero_suit_counts[suit] >= 1:
            hero_flush_high = max(
                _rank_value(card) for card in hero_cards if _suit_value(card) == suit
            )
            return True, 9, hero_flush_high == 14
    return False, 0, False


def _detect_backdoor_flush(hero_cards: list[str], board_cards: list[str]) -> bool:
    if len(board_cards) != 3:
        return False
    all_cards = hero_cards + board_cards
    suit_counts = Counter(_suit_value(card) for card in all_cards)
    hero_suit_counts = Counter(_suit_value(card) for card in hero_cards)
    return any(count == 3 and hero_suit_counts[suit] >= 1 for suit, count in suit_counts.items())


def _detect_straight_draw(hero_cards: list[str], board_cards: list[str]) -> tuple[str | None, int]:
    hero_ranks = {_rank_value(card) for card in hero_cards}
    all_ranks = _normalize_ace_low(hero_ranks | {_rank_value(card) for card in board_cards})
    hero_ranks_normalized = _normalize_ace_low(hero_ranks)
    best_draw: tuple[str | None, int] = (None, 0)
    for window in STRAIGHT_WINDOWS:
        present = sum(rank in all_ranks for rank in window)
        hero_contribution = sum(rank in hero_ranks_normalized for rank in window)
        if hero_contribution < 1 or present == 5:
            continue
        if present == 4:
            missing = [rank for rank in window if rank not in all_ranks]
            if not missing:
                continue
            if missing[0] in {window[0], window[-1]}:
                candidate = ("oesd", 8)
            else:
                candidate = ("gutshot", 4)
            if candidate[1] > best_draw[1]:
                best_draw = candidate
    return best_draw


def _detect_backdoor_straight(hero_cards: list[str], board_cards: list[str]) -> bool:
    if len(board_cards) != 3:
        return False
    hero_ranks = {_rank_value(card) for card in hero_cards}
    all_ranks = _normalize_ace_low(hero_ranks | {_rank_value(card) for card in board_cards})
    hero_ranks_normalized = _normalize_ace_low(hero_ranks)
    return any(
        sum(rank in all_ranks for rank in window) == 3
        and sum(rank in hero_ranks_normalized for rank in window) >= 1
        for window in STRAIGHT_WINDOWS
    )


def _classify_overcard_draw(
    hero_cards: list[str],
    board_cards: list[str],
    backdoor_flush: bool,
    backdoor_straight: bool,
) -> str | None:
    hero_ranks = sorted((_rank_value(card) for card in hero_cards), reverse=True)
    board_max = max(_rank_value(card) for card in board_cards)
    overcards = [rank for rank in hero_ranks if rank > board_max]
    if len(overcards) >= 2:
        if set(hero_ranks) in ({14, 13}, {14, 12}, {13, 12}) and (
            backdoor_flush or backdoor_straight
        ):
            return "strong_overcard_draw"
        if sum(hero_ranks) > 19:
            return "medium_overcard_draw"
        return "weak_overcard_draw"
    if len(overcards) == 1:
        if overcards[0] == 14 and backdoor_flush and backdoor_straight:
            return "medium_overcard_draw"
        if overcards[0] in {14, 13}:
            return "weak_overcard_draw"
    return None


def _apply_combo_rule(made_hand_class: str, draw_class: str | None) -> bool:
    return made_hand_class is not None and draw_class is not None


def _check_nuts(hero_cards: list[str], board_cards: list[str]) -> tuple[bool, int]:
    hero_eval_cards = _parse_cards(hero_cards)
    board_eval_cards = _parse_cards(board_cards)
    known_cards = set(hero_eval_cards + board_eval_cards)
    hero_strength = eval7.evaluate(hero_eval_cards + board_eval_cards)
    deck = eval7.Deck()
    remaining_deck = [card for card in deck.cards if card not in known_cards]
    stronger_strengths: set[int] = set()
    for opp_cards in combinations(remaining_deck, 2):
        opponent_strength = eval7.evaluate(list(opp_cards) + board_eval_cards)
        if opponent_strength > hero_strength:
            stronger_strengths.add(opponent_strength)
    nuts_distance = len(stronger_strengths)
    return nuts_distance == 0, nuts_distance
