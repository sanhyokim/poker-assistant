"""PokerRL+GRPO inference bridge."""

from typing import Any


class PokerRLBridge:
    """New inference engine enabled incrementally during Stages A-D.

    This is a skeleton only. Implementation is planned for Sprint 4.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize the inactive bridge.

        Args:
            config: PokerRL inference configuration.
        """
        self._config = config
        self._ready = False

    def infer(
        self,
        game_state: Any,
        request_id: str,
        context_snapshot: Any,
    ) -> None:
        """Return no result until the inference bridge is implemented."""
        return None

    def reset(self) -> None:
        """Reset the inference process."""

    def is_ready(self) -> bool:
        """Return whether the inference engine is ready."""
        return self._ready
