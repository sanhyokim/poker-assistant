"""プリフロップチャート参照ロジック。

SPEC.md セクション7 準拠。
事前解析済みGTOチャートをJSONから読み込み、
ポジション×アクション履歴から推奨アクションとレンジを返す。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


RANK_ORDER = "23456789TJQKA"
ACTION_PRIORITY = ("4bet", "3bet", "raise", "call", "check", "fold")
POSITION_ORDER = ("UTG", "MP", "CO", "BTN", "SB", "BB")

Recommendation = dict[str, Any]


class PreflopChart:
    """プリフロップGTOチャートの読み込みと参照。"""

    def __init__(self, chart_path: str = "preflop_charts/6max_gto.json") -> None:
        """JSONファイルからチャートデータを読み込む。

        Args:
            chart_path: チャートJSONファイルのパス。

        Raises:
            FileNotFoundError: ファイルが存在しない場合。
            json.JSONDecodeError: JSONパースに失敗した場合。
        """
        with Path(chart_path).open("r", encoding="utf-8") as file:
            self.chart: dict[str, Any] = json.load(file)

    def get_recommendation(
        self,
        hero_position: str,
        hand: str,
        scenario: str,
        current_max_bet: int = 0,
        blind_bb: int = 100,
    ) -> Recommendation:
        """Return a chart recommendation for position, hand, and scenario.

        Args:
            hero_position: Hero position such as UTG, MP, CO, BTN, SB, or BB.
            hand: Concrete cards such as AhKs, or generic hand such as AKo.
            scenario: Chart scenario such as RFI or vs_BTN_raise.
            current_max_bet: Current maximum visible preflop bet.
            blind_bb: Big blind amount.

        Returns:
            Recommendation dictionary with action, confidence, source, and range.
        """
        position_chart = self.chart.get("6max", {}).get(hero_position)
        if not isinstance(position_chart, dict):
            return self._fallback()

        scenario_chart = position_chart.get(scenario)
        if not isinstance(scenario_chart, dict):
            return self._fallback()

        generic_hand = self._normalize_hand(hand)
        for action in ACTION_PRIORITY:
            range_str = scenario_chart.get(action)
            if not isinstance(range_str, str) or range_str == "remainder":
                continue
            if self.hand_in_range(generic_hand, range_str):
                return {
                    "action": action,
                    "amount": self._action_amount(action, current_max_bet, blind_bb),
                    "confidence": "high",
                    "source": "preflop_chart",
                    "range": range_str,
                    "reason": self._reason_for_action(action, hero_position, scenario),
                }

        for action in ACTION_PRIORITY:
            if action != "check":
                continue
            if scenario_chart.get(action) == "remainder":
                return {
                    "action": action,
                    "amount": self._action_amount(action, current_max_bet, blind_bb),
                    "confidence": "high",
                    "source": "preflop_chart",
                    "range": "remainder",
                    "reason": self._reason_for_action(action, hero_position, scenario),
                }

        return {
            "action": "fold",
            "amount": 0,
            "confidence": "high",
            "source": "preflop_chart",
            "range": scenario_chart.get("fold"),
            "reason": "レンジ外のためフォールド",
        }

    @staticmethod
    def hand_to_generic(card1: str, card2: str) -> str:
        """Convert two concrete cards to generic preflop notation.

        Args:
            card1: First concrete card, such as Ah.
            card2: Second concrete card, such as Ks.

        Returns:
            Generic hand notation such as AKo, AKs, or AA.
        """
        rank1, suit1 = card1[0].upper(), card1[1].lower()
        rank2, suit2 = card2[0].upper(), card2[1].lower()

        if rank1 == rank2:
            return f"{rank1}{rank2}"

        if RANK_ORDER.index(rank1) < RANK_ORDER.index(rank2):
            rank1, rank2 = rank2, rank1
            suit1, suit2 = suit2, suit1

        suffix = "s" if suit1 == suit2 else "o"
        return f"{rank1}{rank2}{suffix}"

    @staticmethod
    def hand_in_range(hand: str, range_str: str) -> bool:
        """Return whether a generic hand is contained in a range string.

        Args:
            hand: Generic hand notation such as AKo, AKs, or AA.
            range_str: Comma-separated PioSOLVER-style range string.

        Returns:
            True if the hand is included in the range, otherwise False.
        """
        normalized_hand = PreflopChart._normalize_hand(hand)
        for token in range_str.split(","):
            token = token.strip()
            if not token or token == "remainder":
                continue
            if normalized_hand in PreflopChart._expand_range_token(token):
                return True
        return False

    @staticmethod
    def get_scenario(hero_position: str, action_history: list[dict[str, Any]]) -> str:
        """Derive a preflop chart scenario from action history.

        Args:
            hero_position: Hero position.
            action_history: Preflop action history excluding blinds.

        Returns:
            Scenario name, or unknown for unsupported complex spots.
        """
        raises = [
            action
            for action in action_history
            if PreflopChart._is_raise_action(str(action.get("action", "")))
        ]
        limps = [
            action
            for action in action_history
            if str(action.get("action", "")).upper() in {"CALL", "LIMP"}
        ]
        if not raises and not limps:
            return "RFI"
        if not raises and limps:
            return "vs_limp"

        opponent_all_in = any(
            str(action.get("action", "")).upper() == "ALL_IN"
            and not PreflopChart._is_hero_action(action, hero_position)
            for action in action_history
        )
        if opponent_all_in:
            return "vs_all_in"

        hero_raises = [
            action
            for action in raises
            if PreflopChart._is_hero_action(action, hero_position)
        ]
        opponent_raises = [action for action in raises if action not in hero_raises]

        if hero_raises and opponent_raises and raises[-1] in opponent_raises:
            return "vs_3bet"

        if not hero_raises and opponent_raises:
            first_raise_position = str(opponent_raises[0].get("position", ""))
            if hero_position == "BB" and first_raise_position in POSITION_ORDER:
                return f"vs_{first_raise_position}_raise"
            if hero_position == "SB" and first_raise_position in POSITION_ORDER:
                return f"vs_{first_raise_position}_raise"
            return "vs_raise"

        if hero_raises and not opponent_raises:
            return "RFI"

        return "unknown"

    @staticmethod
    def _is_hero_action(action: dict[str, Any], hero_position: str) -> bool:
        """Return whether a history action belongs to the hero."""
        seat = action.get("seat")
        if seat is not None:
            try:
                if int(seat) == 1:
                    return True
            except (TypeError, ValueError):
                pass
        return str(action.get("position", "")) == hero_position

    @staticmethod
    def _normalize_hand(hand: str) -> str:
        """Normalize concrete or generic hand text into generic notation."""
        stripped = hand.strip()
        if len(stripped) == 4:
            return PreflopChart.hand_to_generic(stripped[:2], stripped[2:])
        if len(stripped) == 3:
            return f"{stripped[0].upper()}{stripped[1].upper()}{stripped[2].lower()}"
        if len(stripped) == 2:
            return stripped.upper()
        return stripped

    @staticmethod
    def _expand_range_token(token: str) -> set[str]:
        """Expand one range token into generic hands."""
        if "-" in token:
            start, end = token.split("-", 1)
            return PreflopChart._expand_dash_range(start, end)
        if token.endswith("+"):
            return PreflopChart._expand_plus_range(token[:-1])
        return {PreflopChart._normalize_hand(token)}

    @staticmethod
    def _expand_plus_range(base: str) -> set[str]:
        """Expand a plus range token such as 77+ or ATs+."""
        base = PreflopChart._normalize_hand(base)
        if len(base) == 2 and base[0] == base[1]:
            start_index = RANK_ORDER.index(base[0])
            return {f"{rank}{rank}" for rank in RANK_ORDER[start_index:]}

        high, kicker, suffix = base[0], base[1], base[2]
        high_index = RANK_ORDER.index(high)
        kicker_index = RANK_ORDER.index(kicker)
        return {
            f"{high}{rank}{suffix}"
            for rank in RANK_ORDER[kicker_index:high_index]
        }

    @staticmethod
    def _expand_dash_range(start: str, end: str) -> set[str]:
        """Expand a dash range token such as JJ-77 or A9s-A2s."""
        start = PreflopChart._normalize_hand(start)
        end = PreflopChart._normalize_hand(end)
        if len(start) == 2 and len(end) == 2:
            start_index = RANK_ORDER.index(start[0])
            end_index = RANK_ORDER.index(end[0])
            low, high = sorted((start_index, end_index))
            return {f"{rank}{rank}" for rank in RANK_ORDER[low : high + 1]}

        if len(start) != 3 or len(end) != 3:
            return {start, end}
        if start[0] != end[0] or start[2] != end[2]:
            return {start, end}

        high_rank = start[0]
        suffix = start[2]
        start_index = RANK_ORDER.index(start[1])
        end_index = RANK_ORDER.index(end[1])
        low, high = sorted((start_index, end_index))
        return {f"{high_rank}{rank}{suffix}" for rank in RANK_ORDER[low : high + 1]}

    @staticmethod
    def _is_raise_action(action: str) -> bool:
        """Return whether action text represents a preflop raise action."""
        normalized = action.strip().lower().replace("-", "_")
        return normalized in {
            "raise",
            "bet",
            "3bet",
            "4bet",
            "all_in",
            "allin",
        } or "raise" in normalized

    @staticmethod
    def _action_amount(action: str, current_max_bet: int, blind_bb: int) -> int:
        """Return the default chip amount for a chart action."""
        if action == "call":
            return current_max_bet
        if action in {"check", "fold"}:
            return 0
        if current_max_bet > 0:
            min_raise = current_max_bet * 2
            recommended = current_max_bet * 3
            return max(recommended, min_raise)
        return blind_bb * 3

    @staticmethod
    def _fallback() -> Recommendation:
        """Return a low-confidence fold fallback recommendation."""
        return {
            "action": "fold",
            "amount": 0,
            "confidence": "low",
            "source": "preflop_chart_fallback",
            "range": None,
            "reason": "該当シナリオなし",
        }

    @staticmethod
    def _reason_for_action(action: str, position: str, scenario: str) -> str:
        """Return a concise Japanese chart reason for the selected action."""
        if scenario == "RFI" and action == "raise":
            return f"{position}からオープンレイズ"
        if action in {"3bet", "4bet"}:
            return "3betレンジ内" if action == "3bet" else "4betレンジ内"
        if action == "call":
            return "コールレンジ内"
        if action == "check":
            return "チェックレンジ内（リンプポット）"
        if action == "raise":
            return "レイズレンジ内"
        return "チャートレンジ内"
