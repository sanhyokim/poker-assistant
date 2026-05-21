"""Deep CFR PokerNetwork definition.

This must match the architecture in the training repository
(dberweger2017/deepcfr-texas-no-limit-holdem-6-players src/core/model.py)
so that checkpoint weights can be loaded correctly.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class PokerNetwork(nn.Module):
    """Poker network with continuous bet sizing capabilities."""

    def __init__(
        self, input_size: int = 500, hidden_size: int = 256, num_actions: int = 3
    ) -> None:
        """Initialize the action and sizing network heads.

        Args:
            input_size: Encoded state vector size.
            hidden_size: Shared hidden layer width.
            num_actions: Number of action logits to emit.
        """
        super().__init__()
        self.base = nn.Sequential(
            nn.Linear(input_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
        )
        self.action_head = nn.Linear(hidden_size, num_actions)
        self.sizing_head = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.Tanh(),
            nn.Linear(hidden_size // 2, 1),
            nn.Sigmoid(),
        )

    def forward(
        self, x: torch.Tensor, opponent_features: torch.Tensor | None = None
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Run a forward pass.

        Args:
            x: Encoded game state tensor.
            opponent_features: Reserved for opponent modeling variants.

        Returns:
            Tuple of action logits and bet-size prediction.
        """
        del opponent_features
        features = self.base(x)
        action_logits = self.action_head(features)
        bet_size = 0.1 + 2.9 * self.sizing_head(features)
        return action_logits, bet_size
