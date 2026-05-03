"""マルチウェイ判断エンジン: eval7 MCエクイティ + LLMパイプライン。

SPEC.md セクション6.2, 6.3 準拠。
3人以上のポストフロップ局面でソルバーの代わりに使用する。
"""

from __future__ import annotations

import logging
import random
from typing import Any

from core.game_state import GameState
from strategy.llm_pipeline import LLMPipeline


logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


class MultiwayEngine:
    """マルチウェイポットの判断エンジン。"""

    def __init__(self, llm_pipeline: LLMPipeline, config: JsonDict) -> None:
        """Initialize the multiway decision engine.

        Args:
            llm_pipeline: LLMPipeline instance.
            config: Full config.yaml dictionary.
        """
        self.llm = llm_pipeline
        self.blind_bb: int = int(config.get("game", {}).get("blind_bb", 100))
        self.mc_samples: int = 10000
        self.logger = logger

    def evaluate(
        self,
        game_state: GameState,
        opponent_stats_list: list[JsonDict | None],
    ) -> JsonDict:
        """Evaluate a multiway spot and return the recommended action.

        Args:
            game_state: Current recognized game state.
            opponent_stats_list: Statistics for active opponents.

        Returns:
            Decision dictionary including action, size, confidence, and equity.
        """
        hero_cards = game_state.hero.cards or []
        board = game_state.board
        num_opponents = self._num_opponents(game_state, opponent_stats_list)
        equity = self.calculate_equity(hero_cards, board, num_opponents)
        opponent_profiles = self._format_opponent_profiles(opponent_stats_list)

        try:
            llm_result = self.llm.decide_multiway(
                game_state=game_state,
                hero_equity=equity,
                opponent_profiles=opponent_profiles,
            )
        except Exception as error:
            self.logger.warning("Multiway LLM decision failed: %s", error)
            llm_result = {}

        if isinstance(llm_result, dict) and llm_result.get("action"):
            return {
                "action": llm_result["action"],
                "size": llm_result.get("size"),
                "confidence": "medium",
                "reasoning": llm_result.get("reasoning", ""),
                "equity": equity,
                "source": "multiway_engine",
            }

        return self._heuristic_fallback(equity)

    def calculate_equity(
        self,
        hero_cards: list[str],
        board: list[str],
        num_opponents: int,
    ) -> float:
        """Calculate Monte-Carlo equity with eval7.

        Args:
            hero_cards: Two hero cards, such as ["Td", "9c"].
            board: Three to five board cards, such as ["8c", "7d", "8d"].
            num_opponents: Number of opponents from 1 to 5.

        Returns:
            Equity from 0.0 to 1.0. Returns 0.5 on calculation failure.
        """
        try:
            import eval7
        except ImportError:
            self.logger.warning("eval7 is unavailable; returning neutral equity")
            return 0.5

        try:
            if len(hero_cards) != 2 or not 0 <= len(board) <= 5:
                return 0.5
            if num_opponents < 1:
                return 0.5

            hero = [eval7.Card(card) for card in hero_cards]
            board_cards = [eval7.Card(card) for card in board]
            known_cards = hero + board_cards
            deck = eval7.Deck()
            remaining = [card for card in deck.cards if card not in known_cards]

            cards_needed = num_opponents * 2 + (5 - len(board_cards))
            if cards_needed > len(remaining):
                return 0.5

            wins = 0
            ties = 0
            total = 0
            for _ in range(self.mc_samples):
                sampled = random.sample(remaining, cards_needed)
                index = 0
                opponent_hands = []
                for _ in range(num_opponents):
                    opponent_hands.append(sampled[index : index + 2])
                    index += 2

                sim_board = board_cards + sampled[index:]
                hero_score = eval7.evaluate(hero + sim_board)
                best_opponent = max(
                    eval7.evaluate(opponent + sim_board)
                    for opponent in opponent_hands
                )

                if hero_score > best_opponent:
                    wins += 1
                elif hero_score == best_opponent:
                    ties += 1
                total += 1

            return (wins + ties * 0.5) / total if total > 0 else 0.5
        except Exception as error:
            self.logger.warning("Equity calculation failed: %s", error)
            return 0.5

    def _format_opponent_profiles(
        self,
        stats_list: list[JsonDict | None],
    ) -> list[JsonDict]:
        """Format opponent statistics for LLM prompts.

        Args:
            stats_list: Opponent stats dictionaries or None values.

        Returns:
            Prompt-safe list of opponent profile dictionaries.
        """
        profiles: list[JsonDict] = []
        for index, stats in enumerate(stats_list):
            if stats is None:
                seat_id = f"seat_{index + 2}"
                profiles.append(
                    {
                        "identifier": seat_id,
                        "player": seat_id,
                        "style": "Unknown",
                        "vpip": "N/A",
                        "pfr": "N/A",
                        "notes": "No data available",
                    }
                )
                continue

            seat_id = f"seat_{index + 2}"
            safe_stats = LLMPipeline._anonymize_stats(stats, seat=seat_id)
            profiles.append(
                {
                    "identifier": safe_stats.get("identifier", seat_id),
                    "player": safe_stats.get("identifier", seat_id),
                    "style": safe_stats.get("long_term_style", "Unknown"),
                    "vpip": safe_stats.get("vpip", "N/A"),
                    "pfr": safe_stats.get("pfr", "N/A"),
                    "notes": safe_stats.get("freshness_note", ""),
                }
            )

        return profiles

    @staticmethod
    def _num_opponents(
        game_state: GameState,
        opponent_stats_list: list[JsonDict | None],
    ) -> int:
        """Infer opponent count from stats list or active player count."""
        if opponent_stats_list:
            return len(opponent_stats_list)
        return max(1, game_state.active_player_count - 1)

    @staticmethod
    def _heuristic_fallback(equity: float) -> JsonDict:
        """Return an equity-threshold heuristic decision."""
        if equity > 0.6:
            action, size = "bet", "60%"
        elif equity > 0.4:
            action, size = "check", None
        else:
            action, size = "fold", None

        return {
            "action": action,
            "size": size,
            "confidence": "medium",
            "reasoning": MultiwayEngine._japanese_reason(action, equity),
            "equity": equity,
            "source": "multiway_heuristic_fallback",
        }

    @staticmethod
    def _japanese_reason(action: str, equity: float) -> str:
        """Return a concise Japanese reason for heuristic fallback actions."""
        normalized = action.upper()
        if normalized == "FOLD":
            return f"エクイティ{equity:.0%} - 不利"
        if normalized == "CHECK":
            return f"エクイティ{equity:.0%} - チェック推奨"
        if normalized == "CALL":
            return f"エクイティ{equity:.0%} - コール可能"
        if normalized == "BET":
            return f"エクイティ{equity:.0%} - ベット推奨"
        if normalized == "RAISE":
            return f"エクイティ{equity:.0%} - レイズ推奨"
        return f"エクイティ{equity:.0%} - 安全策"
