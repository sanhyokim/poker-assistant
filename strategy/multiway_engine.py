"""マルチウェイ判断エンジン: eval7 MCエクイティ + LLMパイプライン。

SPEC.md セクション6.2, 6.3 準拠。
3人以上のポストフロップ局面でソルバーの代わりに使用する。
"""

from __future__ import annotations

import logging
import random
from itertools import combinations
from typing import Any

from core.game_state import ActionRecord, GameState
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
        self.sample_threshold_low: int = int(
            config.get("preflop_delta", {}).get("sample_threshold_low", 50)
        )
        self.mc_samples: int = 10000
        self.logger = logger
        self._baseline_ranges: JsonDict = self._load_baseline_ranges()

    @staticmethod
    def _load_baseline_ranges() -> JsonDict:
        """Load baseline_ranges.json for range-based equity sampling."""
        try:
            import json
            from pathlib import Path

            path = Path(__file__).with_name("baseline_ranges.json")
            with path.open("r", encoding="utf-8") as json_file:
                return json.load(json_file)
        except Exception:
            return {}

    # 定数: FOLDガードのmargin値
    FOLD_GUARD_EQUITY_MARGIN = 0.10
    FOLD_GUARD_MIN_EQUITY = 0.35

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
        opponent_range = self._get_opponent_range(game_state)
        equity = self.calculate_equity(
            hero_cards,
            board,
            num_opponents,
            opponent_range,
        )
        opponent_profiles = self._format_opponent_profiles(opponent_stats_list)

        # 数理メトリクス計算
        metrics = self._compute_metrics(game_state)
        hero_ip_or_oop = self._hero_ip_or_oop(game_state)
        full_street_actions = (
            list(game_state.current_street_actions)
            if hasattr(game_state, "current_street_actions")
            and game_state.current_street_actions
            else None
        )
        preflop_actions = list(game_state.preflop_actions or [])

        self.logger.info(
            "Multiway metrics: hero_cards=%s, board=%s, phase=%s, "
            "pot=%d, hero_current_bet=%d, "
            "facing_bet=%d, raw_call_amount=%d, "
            "effective_call_amount=%d, hero_stack=%d, pot_after_call=%d, "
            "required_equity=%.4f, hero_call_is_all_in=%s, spr=%.1f, "
            "hero_ip_or_oop=%s, hero_equity=%.4f, "
            "num_opponents=%d, active_player_count=%d, "
            "opponent_range=%s, preflop_action_history=%s, "
            "full_street_action_history=%s",
            hero_cards,
            board,
            game_state.phase,
            game_state.pot,
            metrics["hero_current_bet"],
            metrics["facing_bet"],
            metrics["raw_call_amount"],
            metrics["effective_call_amount"],
            metrics["hero_stack"],
            metrics["pot_after_call"],
            metrics["required_equity"],
            metrics["hero_call_is_all_in"],
            metrics["spr"],
            hero_ip_or_oop,
            equity,
            num_opponents,
            game_state.active_player_count,
            opponent_range,
            self._format_actions_summary(preflop_actions),
            self._format_actions_summary(full_street_actions),
        )

        llm_result: JsonDict = {}
        llm_error: str | None = None
        try:
            llm_result = self.llm.decide_multiway(
                game_state=game_state,
                hero_equity=equity,
                opponent_profiles=opponent_profiles,
                call_amount=metrics["call_amount"],
                facing_bet=metrics["facing_bet"],
                pot_after_call=metrics["pot_after_call"],
                required_equity=metrics["required_equity"],
                raw_call_amount=metrics["raw_call_amount"],
                effective_call_amount=metrics["effective_call_amount"],
                hero_call_is_all_in=metrics["hero_call_is_all_in"],
                spr=metrics["spr"],
                hero_ip_or_oop=hero_ip_or_oop,
                preflop_actions=preflop_actions,
                current_street_actions=full_street_actions,
            )
        except Exception as error:
            self.logger.warning("Multiway LLM decision failed: %s", error)
            llm_error = str(error)

        guard_applied = False
        original_action: str | None = None

        if isinstance(llm_result, dict) and llm_result.get("action"):
            original_action = str(llm_result["action"]).lower()
            parsed_action = original_action
            parsed_size = llm_result.get("size")
            parsed_reasoning = str(llm_result.get("reasoning", ""))
        else:
            original_action = None
            parsed_action = ""
            parsed_size = None
            parsed_reasoning = ""

        # FOLDガード: equityが十分ある場合はLLMのFOLDをCALLへ補正
        if (
            parsed_action == "fold"
            and metrics["call_amount"] > 0
            and equity >= max(
                metrics["required_equity"] + self.FOLD_GUARD_EQUITY_MARGIN,
                self.FOLD_GUARD_MIN_EQUITY,
            )
        ):
            self.logger.info(
                "Multiway FOLD overridden by pot-odds guard: "
                "equity=%.3f required=%.3f call_amount=%d "
                "original_action=fold final_action=call",
                equity,
                metrics["required_equity"],
                metrics["call_amount"],
            )
            parsed_action = "call"
            parsed_size = metrics["call_amount"]
            guard_applied = True
            parsed_reasoning = (
                f"{parsed_reasoning} "
                "[LLM FOLD overridden by pot-odds guard]"
            ).strip()

        if parsed_action:
            final_action = parsed_action
            final_amount = parsed_size
            result_source = "multiway_engine"
        else:
            fallback = self._heuristic_fallback(equity)
            final_action = fallback["action"]
            final_amount = fallback["size"]
            parsed_reasoning = fallback.get("reasoning", "")
            result_source = fallback.get("source", "multiway_heuristic_fallback")

        self.logger.info(
            "Multiway LLM result: parsed_action=%s, parsed_size=%s, "
            "parsed_reasoning=%s, final_action=%s, final_amount=%s, "
            "guard_applied=%s, llm_error=%s, raw_response_head=%s",
            original_action,
            parsed_size,
            parsed_reasoning[:200],
            final_action,
            final_amount,
            guard_applied,
            llm_error is not None,
            (self._safe_head(llm_result.get("raw_response", ""), 1000)
             if isinstance(llm_result, dict) else ""),
        )

        result: JsonDict = {
            "action": final_action,
            "size": final_amount,
            "confidence": "medium",
            "reasoning": parsed_reasoning,
            "equity": equity,
            "source": result_source,
        }
        if guard_applied:
            result["guard_applied"] = True
        return result

    @staticmethod
    def _compute_metrics(game_state: GameState) -> JsonDict:
        """Compute mathematical metrics for multiway decision-making.

        Returns dictionary with: hero_current_bet, facing_bet, call_amount,
        raw_call_amount, effective_call_amount, hero_stack, pot_after_call,
        required_equity, pot_odds, spr, hero_call_is_all_in.
        """
        pot = int(game_state.pot or 0)
        hero_current_bet = int(game_state.hero.bet or 0)
        hero_stack = int(game_state.hero.stack or 0)
        facing_bet = max(
            (int(player.bet or 0) for player in game_state.players.values()),
            default=0,
        )
        raw_call_amount = max(0, facing_bet - hero_current_bet)
        if hero_stack > 0:
            call_amount = min(raw_call_amount, hero_stack)
        else:
            call_amount = raw_call_amount

        if call_amount > 0:
            pot_after_call = pot + call_amount
            required_equity = call_amount / pot_after_call
        else:
            pot_after_call = pot
            required_equity = 0.0
        spr = hero_stack / pot if pot > 0 else 0.0
        hero_call_is_all_in = bool(
            call_amount > 0 and hero_stack > 0 and call_amount >= hero_stack
        )

        return {
            "hero_current_bet": hero_current_bet,
            "facing_bet": facing_bet,
            "call_amount": call_amount,
            "raw_call_amount": raw_call_amount,
            "effective_call_amount": call_amount,
            "hero_stack": hero_stack,
            "pot_after_call": pot_after_call,
            "required_equity": required_equity,
            "pot_odds": required_equity,
            "spr": spr,
            "hero_call_is_all_in": hero_call_is_all_in,
        }

    @staticmethod
    def _format_actions_summary(
        actions: list[ActionRecord] | None,
    ) -> str:
        """Format street actions as a compact summary for logging."""
        if not actions:
            return "[]"
        parts = []
        for action in actions:
            seat = getattr(action, "seat", "?")
            action_name = getattr(action, "action", "?")
            amount = getattr(action, "amount", 0)
            parts.append(f"S{seat} {action_name} {amount}")
        return "[" + ", ".join(parts) + "]"

    @staticmethod
    def _safe_head(text: str, max_chars: int) -> str:
        """Return the first max_chars of text, truncating safely."""
        if not text:
            return ""
        return text[:max_chars]

    def calculate_equity(
        self,
        hero_cards: list[str],
        board: list[str],
        num_opponents: int,
        opponent_range_str: str | None = None,
    ) -> float:
        """Calculate Monte-Carlo equity with eval7.

        Args:
            hero_cards: Two hero cards, such as ["Td", "9c"].
            board: Three to five board cards, such as ["8c", "7d", "8d"].
            num_opponents: Number of opponents from 1 to 5.
            opponent_range_str: Optional PioSOLVER-format opponent range.

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
            known_cards = set(hero + board_cards)
            deck = eval7.Deck()
            remaining = [card for card in deck.cards if card not in known_cards]
            opponent_hands_pool = self._build_opponent_hands_pool(
                opponent_range_str,
                remaining,
                known_cards,
            )
            cards_needed_board = 5 - len(board_cards)

            wins = 0
            ties = 0
            total = 0
            for _ in range(self.mc_samples):
                opponent_hands = []
                used_cards = set()
                valid = True
                for _ in range(num_opponents):
                    hand = self._sample_opponent_hand(
                        opponent_hands_pool,
                        remaining,
                        used_cards,
                        known_cards,
                    )
                    if hand is None:
                        valid = False
                        break
                    opponent_hands.append(hand)
                    used_cards.update(hand)
                if not valid:
                    continue

                board_remaining = [card for card in remaining if card not in used_cards]
                if len(board_remaining) < cards_needed_board:
                    continue
                sim_board = board_cards + random.sample(
                    board_remaining,
                    cards_needed_board,
                )
                hero_score = eval7.evaluate(hero + sim_board)
                best_opponent = max(
                    eval7.evaluate(list(opponent) + sim_board)
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

    def _build_opponent_hands_pool(
        self,
        range_str: str | None,
        remaining: list[Any],
        known_cards: set[Any],
    ) -> list[tuple[Any, Any]] | None:
        """Build a pool of valid opponent hands from a range string.

        Args:
            range_str: PioSOLVER-style range string.
            remaining: Cards not already known.
            known_cards: Hero and board cards.

        Returns:
            Valid hand tuples, or None to fall back to random sampling.
        """
        _ = known_cards
        if not range_str:
            return None

        try:
            pool = []
            for card_one, card_two in combinations(remaining, 2):
                hand_key = self._cards_to_range_key(card_one, card_two)
                if self._hand_matches_range(hand_key, range_str):
                    pool.append((card_one, card_two))
            if len(pool) < 10:
                return None
            return pool
        except Exception:
            return None

    def _sample_opponent_hand(
        self,
        pool: list[tuple[Any, Any]] | None,
        remaining: list[Any],
        used_cards: set[Any],
        known_cards: set[Any],
    ) -> tuple[Any, Any] | None:
        """Sample one opponent hand, avoiding already-used cards."""
        _ = known_cards
        if pool is not None:
            valid = [
                hand
                for hand in pool
                if hand[0] not in used_cards and hand[1] not in used_cards
            ]
            if valid:
                return random.choice(valid)

        available = [card for card in remaining if card not in used_cards]
        if len(available) < 2:
            return None
        sampled = random.sample(available, 2)
        return (sampled[0], sampled[1])

    @staticmethod
    def _cards_to_range_key(card_one: Any, card_two: Any) -> str:
        """Convert two cards to generic notation like AKs, AKo, or TT."""
        rank_order = "23456789TJQKA"
        rank_one = str(card_one)[0]
        rank_two = str(card_two)[0]
        suit_one = str(card_one)[1]
        suit_two = str(card_two)[1]
        if rank_order.index(rank_one) < rank_order.index(rank_two):
            rank_one, rank_two = rank_two, rank_one
            suit_one, suit_two = suit_two, suit_one
        if rank_one == rank_two:
            return f"{rank_one}{rank_two}"
        if suit_one == suit_two:
            return f"{rank_one}{rank_two}s"
        return f"{rank_one}{rank_two}o"

    @staticmethod
    def _hand_matches_range(hand_key: str, range_str: str) -> bool:
        """Return whether a hand key matches a simplified PioSOLVER range."""
        hands_in_range = set()
        rank_order = "23456789TJQKA"

        for part in range_str.split(","):
            part = part.strip()
            if not part:
                continue

            if "+" in part:
                base = part.replace("+", "")
                if len(base) == 2 and base[0] == base[1]:
                    start_index = rank_order.index(base[0])
                    for index in range(start_index, len(rank_order)):
                        hands_in_range.add(f"{rank_order[index]}{rank_order[index]}")
                elif len(base) == 3:
                    rank_high = base[0]
                    rank_low = base[1]
                    suitedness = base[2]
                    start_index = rank_order.index(rank_low)
                    high_index = rank_order.index(rank_high)
                    for index in range(start_index, high_index):
                        hands_in_range.add(
                            f"{rank_high}{rank_order[index]}{suitedness}"
                        )
            elif "-" in part:
                left, right = part.split("-", 1)
                left = left.strip()
                right = right.strip()
                if (
                    len(left) == 2
                    and len(right) == 2
                    and left[0] == left[1]
                    and right[0] == right[1]
                ):
                    low_index = rank_order.index(right[0])
                    high_index = rank_order.index(left[0])
                    for index in range(low_index, high_index + 1):
                        hands_in_range.add(f"{rank_order[index]}{rank_order[index]}")
                else:
                    hands_in_range.add(left)
                    hands_in_range.add(right)
            else:
                hands_in_range.add(part)

        return hand_key in hands_in_range

    def _get_opponent_range(self, game_state: GameState) -> str | None:
        """Get a baseline opponent range string based on pot-size heuristic."""
        pot = int(game_state.pot or 0)
        if self.blind_bb <= 0:
            ranges = self._baseline_ranges.get("single_raised_pot", {})
            return ranges.get("IP") if isinstance(ranges, dict) else None

        pot_bb = pot / self.blind_bb
        if pot_bb >= 40:
            scenario = "4bet_pot"
        elif pot_bb >= 15:
            scenario = "3bet_pot"
        elif pot_bb <= 4:
            scenario = "limp_pot"
        else:
            scenario = "single_raised_pot"

        ranges = self._baseline_ranges.get(scenario, {})
        return ranges.get("IP") if isinstance(ranges, dict) else None

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
                continue

            total_hands = stats.get("total_hands", 0)
            if not isinstance(total_hands, (int, float)):
                continue
            if total_hands < self.sample_threshold_low:
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
        """Infer opponent count from currently active players."""
        return max(1, int(game_state.active_player_count or 0) - 1)

    @staticmethod
    def _hero_ip_or_oop(game_state: GameState) -> str:
        """Return a simple IP/OOP hint for multiway prompt."""
        hero_position = game_state.hero.position or "Unknown"
        if hero_position in {"BTN", "CO"}:
            return "likely IP"
        if hero_position in {"SB", "BB"}:
            return "likely OOP"
        if hero_position == "Unknown":
            return "Unknown"
        return "mixed/early"

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
