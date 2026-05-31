"""Load a vLLM or llama-cpp model and run inference."""

from typing import Any


class InferenceEngine:
    """Load a resident LLM and extract hidden states.

    This is a skeleton only. Implementation is planned for Sprint 4.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Store the inference configuration.

        Args:
            config: PokerRL inference configuration.
        """
        self._config = config

    def load_model(self) -> None:
        """Load the model.

        Raises:
            NotImplementedError: Always, until Sprint 4.
        """
        raise NotImplementedError("Planned for Sprint 4")

    def encode(self, prompt: str) -> Any:
        """Encode a prompt and return its hidden state.

        Raises:
            NotImplementedError: Always, until Sprint 4.
        """
        raise NotImplementedError("Planned for Sprint 4")
