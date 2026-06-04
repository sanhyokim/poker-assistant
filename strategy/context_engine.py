"""PokerSkill-style Context Engine for multiway postflop decisions.

SPEC.md §9.4.7 - §9.4.8.5 準拠。
決定論的にboard texture、hand class、draw class、kicker、nutsを分類する。
LLM呼び出しは含まない。Budget計算・viable action logicはStep 2で追加。
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations
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
