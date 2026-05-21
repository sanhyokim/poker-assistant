"""Deep CFR inference bridge for postflop recommendations.

Loads a trained Deep CFR 6-player NLHE model and converts
GameState into recommendations. See SPEC.md Section 10A.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.game_state import GameState, PlayerState
from strategy.recommendation_engine import Recommendation

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]

_RANK_MAP: dict[str, int] = {
    "2": 0,
    "3": 1,
    "4": 2,
    "5": 3,
    "6": 4,
    "7": 5,
    "8": 6,
    "9": 7,
    "T": 8,
    "J": 9,
    "Q": 10,
    "K": 11,
    "A": 12,
}
_SUIT_MAP: dict[str, int] = {"s": 0, "h": 1, "d": 2, "c": 3}
_ACTION_NAMES = ["FOLD", "CALL", "RAISE"]

_PHASE_TO_STAGE: dict[str, int] = {
    "preflop": 0,
    "flop": 1,
    "turn": 2,
    "river": 3,
}

_ACTION_TO_POKERS_ENUM: dict[str, int] = {
    "FOLD": 0,
    "CHECK": 1,
    "CALL": 2,
    "BET": 3,
    "RAISE": 3,
    "ALL_IN": 3,
    "BLIND_SB": 2,
    "BLIND_BB": 2,
}


def card_to_index(card: str) -> int | None:
    """Convert a card string like Ah to a 0-51 index.

    Args:
        card: Two-character card string such as "Ah" or "Tc".

    Returns:
        Card index using suit * 13 + rank, or None for invalid input.
    """
    if len(card) != 2:
        return None
    rank_char = card[0].upper()
    suit_char = card[1].lower()
    rank = _RANK_MAP.get(rank_char)
    suit = _SUIT_MAP.get(suit_char)
    if rank is None or suit is None:
        return None
    return suit * 13 + rank


@dataclass
class DeepCFRResult:
    """Raw inference result before Recommendation conversion."""

    fold_prob: float
    call_prob: float
    raise_prob: float
    raise_size_ratio: float
    top_action: str
    top_prob: float


class DeepCFRBridge:
    """Bridge between GameState and a trained Deep CFR model."""

    NUM_PLAYERS = 6

    def __init__(self, config: JsonDict) -> None:
        """Initialize the bridge and try to load the model.

        Args:
            config: Parsed config.yaml dictionary.
        """
        self._config = config
        deep_cfr_cfg = config.get("deep_cfr", {})
        self._model_path: str = deep_cfr_cfg.get(
            "model_path", "models/deep_cfr/best_checkpoint.pt"
        )
        self._device_name: str = deep_cfr_cfg.get("device", "cuda")
        self._model_loaded = False
        self._load_error: str | None = None

        self._torch: Any = None
        self._torch_nn_functional: Any = None
        self._numpy: Any = None
        self._strategy_net: Any = None
        self._device: Any = None

        self._try_load_model()

    def _try_load_model(self) -> None:
        """Attempt to load the Deep CFR model, logging warnings on failure."""
        try:
            import numpy as np
            import torch
            import torch.nn.functional as F
        except ImportError as exc:
            self._load_error = f"torch/numpy not installed: {exc}"
            logger.warning("Deep CFR model load skipped: %s", self._load_error)
            return

        self._torch = torch
        self._torch_nn_functional = F
        self._numpy = np

        device_str = self._device_name
        if device_str == "cuda" and not torch.cuda.is_available():
            device_str = "cpu"
            logger.warning("CUDA not available, falling back to CPU for Deep CFR")
        self._device = torch.device(device_str)

        try:
            checkpoint = torch.load(
                self._model_path,
                map_location=self._device,
                weights_only=False,
            )
            input_size = self._compute_input_size()
            from strategy._deep_cfr_network import PokerNetwork

            self._strategy_net = PokerNetwork(
                input_size=input_size, hidden_size=256, num_actions=3
            )
            self._strategy_net.load_state_dict(checkpoint["strategy_net"])
            self._strategy_net.to(self._device)
            self._strategy_net.eval()
            self._model_loaded = True
            logger.info(
                "Deep CFR model loaded: path=%s device=%s input_size=%d",
                self._model_path,
                self._device,
                input_size,
            )
        except FileNotFoundError:
            self._load_error = f"Model file not found: {self._model_path}"
            logger.warning("Deep CFR model load failed: %s", self._load_error)
        except Exception as exc:
            self._load_error = f"Model load error: {exc}"
            logger.warning("Deep CFR model load failed: %s", self._load_error)

    def _compute_input_size(self) -> int:
        """Compute the expected input tensor size for six players.

        Returns:
            Size of the encoded state vector.
        """
        n = self.NUM_PLAYERS
        return 52 + 52 + 5 + 1 + n + n + n * 4 + 1 + 4 + 5

    @property
    def available(self) -> bool:
        """Return True when the model is loaded and ready for inference."""
        return self._model_loaded

    @property
    def load_error(self) -> str | None:
        """Return the model load error message, or None on success."""
        return self._load_error

    def encode_game_state(self, game_state: GameState) -> Any:
        """Convert a GameState to the Deep CFR input tensor.

        Args:
            game_state: Current game state from the recognition pipeline.

        Returns:
            Numpy array with shape matching _compute_input_size().

        Raises:
            ValueError: If hero cards are missing or invalid.
        """
        np = self._numpy
        if np is None:
            raise ValueError("numpy is not available")
        encoded: list[Any] = []

        hero_cards = game_state.hero.cards
        if not hero_cards or len(hero_cards) != 2:
            raise ValueError(f"Hero cards missing or invalid: {hero_cards}")
        hand_enc = np.zeros(52)
        for card in hero_cards:
            idx = card_to_index(card)
            if idx is None:
                raise ValueError(f"Invalid hero card: {card}")
            hand_enc[idx] = 1
        encoded.append(hand_enc)

        board_enc = np.zeros(52)
        for card in game_state.board or []:
            idx = card_to_index(card)
            if idx is not None:
                board_enc[idx] = 1
        encoded.append(board_enc)

        stage_enc = np.zeros(5)
        stage_idx = _PHASE_TO_STAGE.get(game_state.phase, 0)
        stage_enc[stage_idx] = 1
        encoded.append(stage_enc)

        hero_stack = game_state.hero.stack or 0
        hero_bet = game_state.hero.bet or 0
        initial_stake = hero_stack + hero_bet
        if initial_stake <= 0:
            initial_stake = 1.0
        encoded.append([game_state.pot / initial_stake])

        button_enc = np.zeros(self.NUM_PLAYERS)
        if game_state.dealer_seat is not None:
            button_idx = (game_state.dealer_seat - 1) % self.NUM_PLAYERS
            button_enc[button_idx] = 1
        encoded.append(button_enc)

        current_player_enc = np.zeros(self.NUM_PLAYERS)
        current_player_enc[0] = 1
        encoded.append(current_player_enc)

        for p_idx in range(self.NUM_PLAYERS):
            player = game_state.hero if p_idx == 0 else game_state.players.get(str(p_idx + 1))
            encoded.append(self._encode_player_state(player, initial_stake))

        max_bet = max(
            [hero_bet]
            + [self._player_bet(game_state.players.get(str(s))) for s in range(2, 7)]
        )
        encoded.append([max_bet / initial_stake])

        legal_enc = np.zeros(4)
        legal_enc[0] = 1
        if max_bet <= hero_bet:
            legal_enc[1] = 1
        else:
            legal_enc[2] = 1
        legal_enc[3] = 1
        encoded.append(legal_enc)

        prev_action_enc = np.zeros(5)
        street_actions = game_state.current_street_actions or []
        if street_actions:
            last_action = street_actions[-1]
            action_key = (last_action.action or "").upper()
            pokers_idx = _ACTION_TO_POKERS_ENUM.get(action_key)
            if pokers_idx is not None:
                prev_action_enc[pokers_idx] = 1
                prev_action_enc[4] = last_action.amount / initial_stake
        encoded.append(prev_action_enc)

        return np.concatenate(encoded)

    def _encode_player_state(self, player: Any, initial_stake: float) -> Any:
        """Encode one player state into active, bet, pot chips, and stack values.

        Args:
            player: HeroState, PlayerState, or None.
            initial_stake: Normalization denominator.

        Returns:
            Numpy array with four normalized values.
        """
        np = self._numpy
        if player is None or not player.in_current_hand:
            return np.array([0.0, 0.0, 0.0, 0.0])
        stack = player.stack or 0
        bet = player.bet or 0
        return np.array([1.0, bet / initial_stake, 0.0, stack / initial_stake])

    @staticmethod
    def _player_bet(player: PlayerState | None) -> int:
        """Return a player's current bet, or zero when no state exists."""
        if player is None:
            return 0
        return player.bet or 0

    def infer(self, game_state: GameState) -> DeepCFRResult | None:
        """Run inference on a GameState and return raw probabilities.

        Args:
            game_state: Current game state.

        Returns:
            DeepCFRResult with action probabilities and sizing, or None on failure.
        """
        if not self._model_loaded:
            logger.warning("Deep CFR inference skipped: model not loaded")
            return None

        torch = self._torch
        F = self._torch_nn_functional
        np = self._numpy

        try:
            encoded = self.encode_game_state(game_state)
        except Exception as exc:
            logger.warning("Deep CFR encode failed: %s", exc)
            return None

        state_tensor = torch.FloatTensor(encoded).unsqueeze(0).to(self._device)
        with torch.no_grad():
            logits, bet_size_pred = self._strategy_net(state_tensor)
            probs = F.softmax(logits, dim=1)[0].cpu().numpy()
            raw_sizing = bet_size_pred[0][0].item()

        raise_size_ratio = float(np.clip(raw_sizing, 0.1, 3.0))
        fold_prob = float(probs[0])
        call_prob = float(probs[1])
        raise_prob = float(probs[2])
        action_idx = int(np.argmax(probs))

        return DeepCFRResult(
            fold_prob=fold_prob,
            call_prob=call_prob,
            raise_prob=raise_prob,
            raise_size_ratio=raise_size_ratio,
            top_action=_ACTION_NAMES[action_idx],
            top_prob=float(probs[action_idx]),
        )

    def generate_recommendation(
        self,
        game_state: GameState,
        blind_bb: int = 100,
    ) -> Recommendation | None:
        """Run inference and convert the result to a Recommendation.

        Args:
            game_state: Current game state.
            blind_bb: Big blind size in chips.

        Returns:
            Recommendation, or None when inference is unavailable.
        """
        del blind_bb
        result = self.infer(game_state)
        if result is None:
            return None

        hero_bet = game_state.hero.bet or 0
        max_bet = max(
            [hero_bet]
            + [self._player_bet(game_state.players.get(str(s))) for s in range(2, 7)]
        )
        call_amount = max(0, max_bet - hero_bet)
        pot = game_state.pot or 0

        pot_after_call = pot + call_amount
        raise_additional = int(pot_after_call * result.raise_size_ratio)
        raise_amount = max_bet + raise_additional

        action = result.top_action
        if action == "FOLD":
            amount = 0
        elif action == "CALL":
            if call_amount == 0:
                action = "CHECK"
            amount = call_amount
        elif action == "RAISE":
            if max_bet == 0 or max_bet <= hero_bet:
                action = "BET"
            amount = raise_amount
        else:
            amount = 0

        if result.top_prob >= 0.70:
            confidence = "high"
        elif result.top_prob >= 0.45:
            confidence = "medium"
        else:
            confidence = "low"

        reason = (
            f"Deep CFR: F={result.fold_prob:.0%} "
            f"C={result.call_prob:.0%} "
            f"R={result.raise_prob:.0%}"
        )
        if action in ("RAISE", "BET"):
            reason += f" size={result.raise_size_ratio:.1f}x pot"

        return Recommendation(
            action=action,
            amount=amount,
            reason=reason,
            confidence=confidence,
            strategy_source="deep_cfr",
            action_probabilities={
                "fold": result.fold_prob,
                "call": result.call_prob,
                "raise": result.raise_prob,
            },
            latency_breakdown={},
        )
