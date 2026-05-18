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
        self.deep_spr_threshold: float = float(
            solver_config.get("deep_spr_threshold", 10.0)
        )
        self.deep_spr_light_timeout_ms: int = int(
            solver_config.get("deep_spr_light_timeout_ms", 5000)
        )
        self.deep_spr_light_max_iterations: int = int(
            solver_config.get("deep_spr_light_max_iterations", 80)
        )
        self.deep_spr_light_target_exploitability_pct: float = float(
            solver_config.get("deep_spr_light_target_exploitability_pct", 1.5)
        )
        self.deep_spr_light_bet_sizes: str = str(
            solver_config.get("deep_spr_light_bet_sizes", "50%")
        )
        self.deep_spr_light_raise_sizes: str = str(
            solver_config.get("deep_spr_light_raise_sizes", "2.5x")
        )
        self.add_allin_threshold: float = float(solver_config["add_allin_threshold"])
        self.force_allin_threshold: float = float(
            solver_config["force_allin_threshold"]
        )
        self.merging_threshold: float = float(solver_config["merging_threshold"])
        self.rake_rate: float = float(solver_config["rake_rate"])
        self.rake_cap: float = float(solver_config["rake_cap"])

        self.blind_bb: int = int(game_config["blind_bb"])

    def is_deep_spr(
        self,
        phase: str,
        starting_pot: int,
        effective_stack: int,
    ) -> bool:
        """Return True for flop/turn contexts that qualify as deep SPR.

        Args:
            phase: Current street name.
            starting_pot: Pot used by the solver tree root.
            effective_stack: Effective stack used by the solver tree root.

        Returns:
            True when the context is flop/turn and SPR meets the configured
            deep-SPR threshold.
        """
        if starting_pot <= 0 or effective_stack <= 0:
            return False
        spr = effective_stack / starting_pot
        return phase in {"flop", "turn"} and spr >= self.deep_spr_threshold

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

    def diagnose_request_unavailable(
        self,
        game_state: GameState,
        street_start_pot: int | None,
        street_start_effective_stack: int | None,
        actions_played: list[str] | None,
        hero_is_ip: bool | None,
    ) -> dict[str, object]:
        """Return diagnostics explaining why a solver request may be unavailable.

        Args:
            game_state: Current recognized game state.
            street_start_pot: Pot at the start of the current street.
            street_start_effective_stack: Effective stack at street start.
            actions_played: Solver action path, if one was built.
            hero_is_ip: Whether hero is in position.

        Returns:
            Structured diagnostic fields and reason codes for logging.
        """
        reason_codes: list[str] = []
        board_count = len(game_state.board or [])
        expected_board_counts = {"flop": 3, "turn": 4, "river": 5}
        expected_board_count = expected_board_counts.get(game_state.phase)
        if game_state.phase not in {"flop", "turn", "river"}:
            reason_codes.append("invalid_phase")
        if expected_board_count is not None and board_count < expected_board_count:
            reason_codes.append("invalid_board_count")
        hero_stack = game_state.hero.stack
        if hero_stack is None or hero_stack <= 0:
            reason_codes.append("hero_stack_missing_or_zero")

        active_opponents_raw = [
            {
                "seat": int(seat_key),
                "stack": player.stack,
                "bet": player.bet,
                "in_current_hand": player.in_current_hand,
            }
            for seat_key, player in game_state.players.items()
            if seat_key != "1" and player.in_current_hand
        ]
        active_opponents = self._get_active_opponents(game_state)
        if len(active_opponents) != 1:
            reason_codes.append("active_opponent_count_not_one")
        if active_opponents_raw and not active_opponents:
            reason_codes.append("active_opponent_stack_missing_or_zero")

        effective_stack = self.compute_effective_stack(game_state)
        if effective_stack is None:
            reason_codes.append("effective_stack_missing")
        if street_start_pot is None or street_start_pot <= 0:
            reason_codes.append("street_start_pot_missing")

        actions_status = "empty"
        if actions_played is None:
            actions_status = "empty"
        elif not isinstance(actions_played, list):
            actions_status = "failed"
            reason_codes.append("actions_played_unavailable")
        elif actions_played:
            actions_status = "ok"

        if self._is_facing_all_in(game_state):
            reason_codes.append("facing_all_in")

        diagnostics = {
            "can_use_solver": self.can_use_solver(game_state),
            "phase": game_state.phase,
            "board_count": board_count,
            "hero_stack": hero_stack,
            "active_opponents": active_opponents_raw,
            "active_opponent_count": len(active_opponents_raw),
            "active_opponent_stacks": [
                opponent.get("stack") for opponent in active_opponents_raw
            ],
            "effective_stack": effective_stack,
            "street_start_effective_stack": street_start_effective_stack,
            "street_start_pot": street_start_pot,
            "actions_played": actions_played or [],
            "actions_played_status": actions_status,
            "hero_is_ip": bool(hero_is_ip) if hero_is_ip is not None else None,
            "current_street_actions": [
                {
                    "seat": action.seat,
                    "action": action.action,
                    "amount": action.amount,
                }
                for action in game_state.current_street_actions
            ],
            "reason_codes": reason_codes,
        }
        return diagnostics

    @staticmethod
    def _is_facing_all_in(game_state: GameState) -> bool:
        """Return True when hero appears to face an opponent all-in."""
        hero_bet = int(game_state.hero.bet or 0)
        max_opponent_bet = 0
        for action in game_state.current_street_actions:
            if action.seat == 1 or action.action.upper() != "ALL_IN":
                continue
            max_opponent_bet = max(max_opponent_bet, int(action.amount or 0))
        return max_opponent_bet > hero_bet

    def build_request(
        self,
        game_state: GameState,
        range_oop: str,
        range_ip: str,
        hero_is_ip: bool,
        street_start_pot: int | None = None,
        street_start_effective_stack: int | None = None,
        actions_played: list[str] | None = None,
        profile: str = "default",
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
            profile: Request profile. ``default`` preserves production
                settings; ``deep_spr_light_probe`` builds a comparison-only
                lightweight candidate for deep-SPR flop/turn spots.

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

        spr = request["effective_stack"] / max(request["starting_pot"], 1)
        is_deep_spr = self.is_deep_spr(
            game_state.phase,
            int(request["starting_pot"]),
            int(request["effective_stack"]),
        )

        if profile == "deep_spr_light_probe":
            if not is_deep_spr:
                return None
            request["timeout_ms"] = self.deep_spr_light_timeout_ms
            request["max_iterations"] = self.deep_spr_light_max_iterations
            request["target_exploitability_pct"] = (
                self.deep_spr_light_target_exploitability_pct
            )
            for street in ("flop", "turn"):
                request[f"{street}_bet_sizes_oop"] = self.deep_spr_light_bet_sizes
                request[f"{street}_bet_sizes_ip"] = self.deep_spr_light_bet_sizes
                request[f"{street}_raise_sizes_oop"] = (
                    self.deep_spr_light_raise_sizes
                )
                request[f"{street}_raise_sizes_ip"] = self.deep_spr_light_raise_sizes
            logger.info(
                "DEEP_SPR_LIGHT_REQUEST_BUILT: phase=%s SPR=%.1f "
                "timeout_ms=%d max_iterations=%d target_exploitability_pct=%s "
                "bet_sizes=%s raise_sizes=%s",
                game_state.phase,
                spr,
                request["timeout_ms"],
                request["max_iterations"],
                request["target_exploitability_pct"],
                self.deep_spr_light_bet_sizes,
                self.deep_spr_light_raise_sizes,
            )
            return request

        # Extend timeout for deep-SPR flop positions. This preserves the
        # current production behavior; turn deep-SPR remains default for now.
        if (
            game_state.phase == "flop"
            and is_deep_spr
        ):
            request["timeout_ms"] = 20000
            request["max_iterations"] = 300
            logger.info(
                "Solver timeout extended for deep-SPR flop: SPR=%.1f, "
                "timeout_ms=%d, max_iterations=%d",
                spr,
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
