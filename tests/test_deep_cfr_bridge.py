"""Tests for strategy.deep_cfr_bridge."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.game_state import GameState, HeroState, PlayerState
from strategy.recommendation_engine import Recommendation

_DEFAULT = object()


def _make_game_state(
    phase: str = "flop",
    hero_cards: object = _DEFAULT,
    board: list[str] | None = None,
    pot: int = 500,
    hero_stack: int = 5000,
    hero_bet: int = 0,
    active_count: int = 2,
    dealer_seat: int = 2,
    players: dict[str, PlayerState] | None = None,
    current_street_actions: object = _DEFAULT,
) -> GameState:
    """Create a minimal GameState for testing."""
    if hero_cards is _DEFAULT:
        hero_cards = ["Ah", "Kd"]
    if board is None:
        board = ["Tc", "7h", "2s"]
    if players is None:
        players = {}
        for seat in range(2, 7):
            in_hand = (seat == 2) if active_count == 2 else (seat <= active_count)
            players[str(seat)] = PlayerState(
                stack=5000 if in_hand else None,
                bet=0,
                is_seated=True,
                in_current_hand=in_hand,
            )
    if current_street_actions is _DEFAULT:
        current_street_actions = []
    return GameState(
        phase=phase,
        hand_id=1,
        hero=HeroState(
            seat=1,
            cards=hero_cards,
            cards_visible=True,
            stack=hero_stack,
            bet=hero_bet,
            is_my_turn=True,
            in_current_hand=True,
        ),
        board=board,
        board_card_count=len(board),
        pot=pot,
        players=players,
        dealer_seat=dealer_seat,
        active_player_count=active_count,
        current_street_actions=current_street_actions,
    )


def _make_unloaded_bridge() -> object:
    """Create a bridge without triggering model loading."""
    from strategy.deep_cfr_bridge import DeepCFRBridge

    with patch.object(DeepCFRBridge, "_try_load_model"):
        bridge = DeepCFRBridge(config={"deep_cfr": {"device": "cpu"}})
        bridge._numpy = __import__("numpy")
    return bridge


class TestCardToIndex:
    """Test card encoding."""

    def test_ace_of_hearts(self) -> None:
        """Ah should map to the heart ace slot."""
        from strategy.deep_cfr_bridge import card_to_index

        assert card_to_index("Ah") == 25

    def test_two_of_spades(self) -> None:
        """2s should map to the first slot."""
        from strategy.deep_cfr_bridge import card_to_index

        assert card_to_index("2s") == 0

    def test_king_of_diamonds(self) -> None:
        """Kd should map to the diamond king slot."""
        from strategy.deep_cfr_bridge import card_to_index

        assert card_to_index("Kd") == 37

    def test_ten_of_clubs(self) -> None:
        """Tc should map to the club ten slot."""
        from strategy.deep_cfr_bridge import card_to_index

        assert card_to_index("Tc") == 47

    def test_invalid_card(self) -> None:
        """Invalid card strings should return None."""
        from strategy.deep_cfr_bridge import card_to_index

        assert card_to_index("Xx") is None
        assert card_to_index("") is None
        assert card_to_index("A") is None


class TestEncodeGameState:
    """Test state encoding without a real model."""

    def test_encode_produces_correct_size(self) -> None:
        """Encoding should produce the expected input size."""
        bridge = _make_unloaded_bridge()
        gs = _make_game_state()
        encoded = bridge.encode_game_state(gs)
        assert encoded.shape == (bridge._compute_input_size(),)

    def test_encode_hero_cards_set(self) -> None:
        """Hero card indices should be set in the encoding."""
        from strategy.deep_cfr_bridge import card_to_index

        bridge = _make_unloaded_bridge()
        gs = _make_game_state(hero_cards=["Ah", "Kd"])
        encoded = bridge.encode_game_state(gs)
        assert encoded[card_to_index("Ah")] == 1.0
        assert encoded[card_to_index("Kd")] == 1.0

    def test_encode_board_cards_set(self) -> None:
        """Board card indices should be set in offset 52-103."""
        from strategy.deep_cfr_bridge import card_to_index

        bridge = _make_unloaded_bridge()
        gs = _make_game_state(board=["Tc", "7h", "2s"])
        encoded = bridge.encode_game_state(gs)
        for card in ["Tc", "7h", "2s"]:
            assert encoded[52 + card_to_index(card)] == 1.0

    def test_encode_missing_hero_cards_raises(self) -> None:
        """Encoding should raise ValueError when hero cards are missing."""
        bridge = _make_unloaded_bridge()
        gs = _make_game_state(hero_cards=None)
        with pytest.raises(ValueError, match="Hero cards missing"):
            bridge.encode_game_state(gs)

    def test_encode_phase_flop(self) -> None:
        """Stage encoding should set index 1 for flop."""
        bridge = _make_unloaded_bridge()
        gs = _make_game_state(phase="flop")
        encoded = bridge.encode_game_state(gs)
        assert encoded[104 + 1] == 1.0


class TestGenerateRecommendation:
    """Test recommendation generation with a mock model."""

    def _make_bridge_with_mock_model(
        self, logits_values: list[list[float]] | None = None, sizing_value: float = 1.5
    ) -> object:
        """Create a bridge with a mock strategy network."""
        import numpy as np
        from strategy.deep_cfr_bridge import DeepCFRBridge

        try:
            import torch
            import torch.nn.functional as F
        except ImportError:
            pytest.skip("torch not installed")

        with patch.object(DeepCFRBridge, "_try_load_model"):
            bridge = DeepCFRBridge(config={"deep_cfr": {"device": "cpu"}})

        bridge._numpy = np
        bridge._torch = torch
        bridge._torch_nn_functional = F

        mock_net = MagicMock()
        logits = torch.tensor(logits_values or [[0.1, 0.2, 0.7]])
        sizing = torch.tensor([[sizing_value]])
        mock_net.return_value = (logits, sizing)
        mock_net.eval = MagicMock()

        bridge._strategy_net = mock_net
        bridge._model_loaded = True
        bridge._device = torch.device("cpu")
        return bridge

    def test_generate_returns_recommendation(self) -> None:
        """Successful mock inference should return a Recommendation."""
        bridge = self._make_bridge_with_mock_model()
        gs = _make_game_state()
        rec = bridge.generate_recommendation(gs, blind_bb=100)
        assert rec is not None
        assert isinstance(rec, Recommendation)
        assert rec.strategy_source == "deep_cfr"

    def test_generate_raise_action(self) -> None:
        """Raise should convert to BET when no bet is faced."""
        bridge = self._make_bridge_with_mock_model()
        gs = _make_game_state(pot=500, hero_bet=0)
        rec = bridge.generate_recommendation(gs, blind_bb=100)
        assert rec is not None
        assert rec.action in ("RAISE", "BET")
        assert rec.amount > 0

    def test_generate_check_when_no_facing_bet(self) -> None:
        """When call amount is zero and model says CALL, output CHECK."""
        bridge = self._make_bridge_with_mock_model(logits_values=[[0.05, 0.8, 0.15]])
        gs = _make_game_state(hero_bet=0)
        rec = bridge.generate_recommendation(gs, blind_bb=100)
        assert rec is not None
        assert rec.action == "CHECK"
        assert rec.amount == 0

    def test_generate_fold_action(self) -> None:
        """Fold should keep amount at zero."""
        bridge = self._make_bridge_with_mock_model(logits_values=[[0.9, 0.05, 0.05]])
        gs = _make_game_state()
        rec = bridge.generate_recommendation(gs, blind_bb=100)
        assert rec is not None
        assert rec.action == "FOLD"
        assert rec.amount == 0

    def test_confidence_high(self) -> None:
        """Confidence should always be one of the supported labels."""
        bridge = self._make_bridge_with_mock_model()
        gs = _make_game_state()
        rec = bridge.generate_recommendation(gs, blind_bb=100)
        assert rec is not None
        assert rec.confidence in ("high", "medium", "low")

    def test_model_not_loaded_returns_none(self) -> None:
        """Unavailable bridge should safely return None."""
        from strategy.deep_cfr_bridge import DeepCFRBridge

        with patch.object(DeepCFRBridge, "_try_load_model"):
            bridge = DeepCFRBridge(config={"deep_cfr": {"device": "cpu"}})
        gs = _make_game_state()
        rec = bridge.generate_recommendation(gs, blind_bb=100)
        assert rec is None

    def test_available_property(self) -> None:
        """available should mirror model-loaded state."""
        from strategy.deep_cfr_bridge import DeepCFRBridge

        with patch.object(DeepCFRBridge, "_try_load_model"):
            bridge = DeepCFRBridge(config={"deep_cfr": {"device": "cpu"}})
        assert bridge.available is False
        bridge._model_loaded = True
        assert bridge.available is True
