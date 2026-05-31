"""Convert model output to a Recommendation."""

from typing import Any


class OutputParser:
    """Convert auxiliary-head output to the existing Recommendation format.

    This is a skeleton only. Implementation is planned for Sprint 4.
    """

    def parse(
        self,
        action_logits: Any,
        sizing_logit: Any,
        legal_actions: Any,
    ) -> None:
        """Parse model output.

        Raises:
            NotImplementedError: Always, until Sprint 4.
        """
        raise NotImplementedError("Planned for Sprint 4")
