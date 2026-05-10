"""GameState からソルバーリクエスト JSON を構築する。

SPEC.md セクション5.8, 5.12 準拠。
"""

from __future__ import annotations

import logging
from typing import Any

from core.game_state import GameState


logger = logging.getLogger(__name__)


SolverRequest = dict[str, Any]
ActiveOpponent = dict[str, int]


class SolverRequestBuilder:
    """GameState + レンジ情報からソルバーリクエストdictを構築する。"""

    def __init__(self, config: dict[str, Any]) -> None:
        """config.yaml の solver セクションと game セクションを受け取る。

        Args:
            config: Repository config containing solver and game sections.
        """
        solver_config = config["solver"]
        game_config = config["game"]

        self.max_iterations: int = int(solver_config["max_iterations"])
        self.target_exploitability_pct: float = float(
            solver_config["target_exploitability_pct"]
        )
        self.timeout_ms: int = int(solver_config["timeout_ms"])
        self.default_bet_sizes: str = str(solver_config["default_bet_sizes"])
        self.default_raise_sizes: str = str(solver_config["default_raise_sizes"])
        self.add_allin_threshold: float = float(solver_config["add_allin_threshold"])
        self.force_allin_threshold: float = float(
            solver_config["force_allin_threshold"]
        )
        self.merging_threshold: float = float(solver_config["merging_threshold"])
        self.rake_rate: float = float(solver_config["rake_rate"])
        self.rake_cap: float = float(solver_config["rake_cap"])

        self.blind_bb: int = int(game_config["blind_bb"])

    def can_use_solver(self, game_state: GameState) -> bool:
        """Return True only for heads-up postflop states.

        Args:
            game_state: Current recognized game state.

        Returns:
            True if the solver can be used, otherwise False.
        """
        if game_state.phase not in {"flop", "turn", "river"}:
            return False

        hero_active = game_state.hero.stack is not None and game_state.hero.stack > 0
        active_opponents = self._get_active_opponents(game_state)
        return hero_active and len(active_opponents) == 1

    def compute_effective_stack(self, game_state: GameState) -> int | None:
        """Compute the smaller current stack between hero and one active opponent.

        Args:
            game_state: Current recognized game state.

        Returns:
            Effective stack, or None when the state is not heads-up.
        """
        if game_state.hero.stack is None or game_state.hero.stack <= 0:
            return None

        active_opponents = self._get_active_opponents(game_state)
        if len(active_opponents) != 1:
            return None

        return min(game_state.hero.stack, active_opponents[0]["stack"])

    def build_request(
        self,
        game_state: GameState,
        range_oop: str,
        range_ip: str,
        hero_is_ip: bool,
        street_start_pot: int | None = None,
        street_start_effective_stack: int | None = None,
        actions_played: list[str] | None = None,
    ) -> SolverRequest | None:
        """Build a postflop-solver JSON request from a GameState.

        Args:
            game_state: Current recognized game state.
            range_oop: OOP range in PioSOLVER-compatible notation.
            range_ip: IP range in PioSOLVER-compatible notation.
            hero_is_ip: Whether hero is in position. The request schema keeps
                ranges explicit, so this flag is reserved for caller validation.
            street_start_pot: Pot at the start of the current street.
            street_start_effective_stack: Effective stack at the start of street.
            actions_played: Solver tree navigation actions already played.

        Returns:
            Solver request dictionary, or None when solver use is invalid.
        """
        _ = hero_is_ip
        if not self.can_use_solver(game_state):
            return None

        effective_stack = self.compute_effective_stack(game_state)
        if effective_stack is None:
            return None

        board = game_state.board
        flop_str = self._board_to_flop_str(board)
        turn_str = board[3] if len(board) >= 4 else None
        river_str = board[4] if len(board) >= 5 else None
        actual_starting_pot = (
            street_start_pot if street_start_pot is not None else game_state.pot
        )
        actual_effective_stack = (
            street_start_effective_stack
            if street_start_effective_stack is not None
            else effective_stack
        )

        request: SolverRequest = {
            "board": flop_str,
            "turn": turn_str,
            "river": river_str,
            "range_oop": range_oop,
            "range_ip": range_ip,
            "starting_pot": actual_starting_pot,
            "effective_stack": actual_effective_stack,
            "flop_bet_sizes_oop": self.default_bet_sizes,
            "flop_bet_sizes_ip": self.default_bet_sizes,
            "flop_raise_sizes_oop": self.default_raise_sizes,
            "flop_raise_sizes_ip": self.default_raise_sizes,
            "turn_bet_sizes_oop": self.default_bet_sizes,
            "turn_bet_sizes_ip": self.default_bet_sizes,
            "turn_raise_sizes_oop": self.default_raise_sizes,
            "turn_raise_sizes_ip": self.default_raise_sizes,
            "river_bet_sizes_oop": self.default_bet_sizes,
            "river_bet_sizes_ip": self.default_bet_sizes,
            "river_raise_sizes_oop": self.default_raise_sizes,
            "river_raise_sizes_ip": self.default_raise_sizes,
            "rake_rate": self.rake_rate,
            "rake_cap": self.rake_cap,
            "add_allin_threshold": self.add_allin_threshold,
            "force_allin_threshold": self.force_allin_threshold,
            "merging_threshold": self.merging_threshold,
            "max_iterations": self.max_iterations,
            "target_exploitability_pct": self.target_exploitability_pct,
            "timeout_ms": self.timeout_ms,
            "bunching": None,
            "actions_played": actions_played,
        }

        # Extend timeout for deep-SPR flop positions
        if (
            game_state.phase == "flop"
            and request["effective_stack"] > 0
            and request["starting_pot"] > 0
            and request["effective_stack"] / request["starting_pot"] > 10
        ):
            request["timeout_ms"] = 20000
            request["max_iterations"] = 300
            logger.info(
                "Solver timeout extended for deep-SPR flop: SPR=%.1f, "
                "timeout_ms=%d, max_iterations=%d",
                request["effective_stack"] / request["starting_pot"],
                request["timeout_ms"],
                request["max_iterations"],
            )

        return request

    @staticmethod
    def _board_to_flop_str(board: list[str]) -> str:
        """Convert the first three board cards into a flop string.

        Args:
            board: Board cards such as ["8c", "7d", "8d"].

        Returns:
            Concatenated flop string such as "8c7d8d".
        """
        return "".join(board[:3])

    @staticmethod
    def _get_active_opponents(game_state: GameState) -> list[ActiveOpponent]:
        """Return active non-hero players with usable remaining stacks.

        Args:
            game_state: Current recognized game state.

        Returns:
            List of dictionaries with seat and stack keys.
        """
        opponents: list[ActiveOpponent] = []
        for seat_key, player in game_state.players.items():
            if not player.in_current_hand:
                continue
            if player.stack is None or player.stack <= 0:
                continue
            opponents.append({"seat": int(seat_key), "stack": player.stack})

        return opponents
