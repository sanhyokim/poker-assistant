"""Validation tests for LLM response schemas."""

import pytest
from pydantic import ValidationError

from strategy.llm_schemas import (
    ExploitAdjustmentResponse,
    MultiwayDecisionResponse,
    PreflopDeltaResponse,
    RangeEstimationResponse,
    ReasonGenerationResponse,
)


class TestRangeEstimationResponse:
    """Range-estimation schema tests."""

    def test_valid_response(self) -> None:
        """Valid range response is accepted."""
        response = RangeEstimationResponse(
            range_oop="QQ-88,AJs+,KQs",
            range_ip="22+,A2s+,K9s+",
            confidence=0.8,
            reasoning="Standard ranges",
        )

        assert response.range_oop == "QQ-88,AJs+,KQs"
        assert response.confidence == 0.8

    def test_empty_range_allowed(self) -> None:
        """Empty range strings are accepted by the schema layer."""
        response = RangeEstimationResponse(range_oop="", range_ip="AA")

        assert response.range_oop == ""

    def test_invalid_range_characters_rejected(self) -> None:
        """Invalid range characters raise ValidationError."""
        with pytest.raises(ValidationError):
            RangeEstimationResponse(range_oop="XX,YY", range_ip="AA")

    def test_confidence_out_of_range_rejected(self) -> None:
        """Numeric confidence must be between 0 and 1."""
        with pytest.raises(ValidationError):
            RangeEstimationResponse(range_oop="AA", range_ip="KK", confidence=1.5)

    def test_weighted_range_accepted(self) -> None:
        """Weighted ranges are accepted."""
        response = RangeEstimationResponse(range_oop="AA:0.5,AKs:1.0", range_ip="KK")

        assert "AA:0.5" in response.range_oop

    def test_missing_required_field_rejected(self) -> None:
        """Both OOP and IP ranges are required."""
        with pytest.raises(ValidationError):
            RangeEstimationResponse(range_oop="AA")

    def test_model_validate_from_dict(self) -> None:
        """model_validate accepts a valid dictionary."""
        raw = {
            "range_oop": "AA,KK",
            "range_ip": "QQ",
            "confidence": 0.7,
            "reasoning": "test",
        }

        result = RangeEstimationResponse.model_validate(raw)

        assert result.range_ip == "QQ"

    def test_model_validate_invalid_dict(self) -> None:
        """model_validate rejects missing required keys."""
        with pytest.raises(ValidationError):
            RangeEstimationResponse.model_validate({"wrong_key": "value"})


class TestExploitAdjustmentResponse:
    """Exploit-adjustment schema tests."""

    def test_valid_response(self) -> None:
        """Valid exploit adjustment response is accepted."""
        response = ExploitAdjustmentResponse(
            adjusted_action="RAISE",
            adjusted_amount=300,
            adjustment_reason="Opponent folds too often",
            confidence=0.6,
        )

        assert response.adjusted_action == "RAISE"
        assert response.adjusted_size == 300

    def test_existing_pipeline_keys_accepted(self) -> None:
        """Existing adjusted_size/reasoning keys are accepted."""
        response = ExploitAdjustmentResponse.model_validate(
            {
                "adjusted_action": "BET",
                "adjusted_size": "450",
                "confidence": "medium",
                "reasoning": "Value target",
            }
        )

        assert response.adjusted_size == "450"
        assert response.confidence == "medium"

    def test_missing_required_field_rejected(self) -> None:
        """adjusted_action is required."""
        with pytest.raises(ValidationError):
            ExploitAdjustmentResponse(adjusted_amount=300)

    def test_negative_amount_rejected(self) -> None:
        """Negative adjusted amount is rejected."""
        with pytest.raises(ValidationError):
            ExploitAdjustmentResponse(adjusted_action="BET", adjusted_amount=-100)


class TestMultiwayDecisionResponse:
    """Multiway-decision schema tests."""

    def test_valid_action(self) -> None:
        """Valid action response is accepted."""
        response = MultiwayDecisionResponse(
            action="FOLD",
            amount=0,
            reason="Low equity",
            confidence=0.7,
        )

        assert response.action == "FOLD"

    def test_action_case_normalized(self) -> None:
        """Action text is normalized to uppercase."""
        response = MultiwayDecisionResponse(action="fold", amount=0)

        assert response.action == "FOLD"

    def test_existing_pipeline_keys_accepted(self) -> None:
        """Existing size/reasoning keys are accepted."""
        response = MultiwayDecisionResponse.model_validate(
            {
                "action": "bet",
                "size": "300",
                "confidence": "medium",
                "reasoning": "Value bet",
            }
        )

        assert response.action == "BET"
        assert response.amount == "300"
        assert response.reason == "Value bet"

    def test_invalid_action_rejected(self) -> None:
        """Unknown action is rejected."""
        with pytest.raises(ValidationError):
            MultiwayDecisionResponse(action="BLUFF", amount=0)

    def test_negative_amount_rejected(self) -> None:
        """Negative amount is rejected."""
        with pytest.raises(ValidationError):
            MultiwayDecisionResponse(action="BET", amount=-100)

    def test_all_valid_actions(self) -> None:
        """All recommendation action constants are valid."""
        for action in ["FOLD", "CHECK", "CALL", "BET", "RAISE", "ALL_IN"]:
            response = MultiwayDecisionResponse(action=action, amount=0)
            assert response.action == action


class TestReasonGenerationResponse:
    """Reason-generation schema tests."""

    def test_valid_reason(self) -> None:
        """Non-empty reason is accepted."""
        response = ReasonGenerationResponse(reason="トップペアでバリューベット")

        assert "バリューベット" in response.reason

    def test_empty_reason_rejected(self) -> None:
        """Empty reason is rejected."""
        with pytest.raises(ValidationError):
            ReasonGenerationResponse(reason="")


class TestPreflopDeltaResponse:
    """Preflop-delta schema tests."""

    def test_valid_delta_response(self) -> None:
        """Valid delta response is accepted."""
        response = PreflopDeltaResponse(
            delta_probs={"raise": 0.05, "call": -0.02, "fold": -0.03},
            confidence=0.8,
            reason="Opponent overfolds to raises",
        )

        assert response.delta_probs["raise"] == 0.05
        assert response.confidence == 0.8

    def test_missing_delta_probs_rejected(self) -> None:
        """delta_probs is required."""
        with pytest.raises(ValidationError):
            PreflopDeltaResponse(confidence=0.5)

    def test_confidence_out_of_range_rejected(self) -> None:
        """Confidence must be in the 0..1 range."""
        with pytest.raises(ValidationError):
            PreflopDeltaResponse(delta_probs={"raise": 0.0}, confidence=1.5)
