"""Tests for chart-anchored preflop delta policy."""

from __future__ import annotations

import time

import pytest

from strategy.preflop_delta_policy import PreflopDeltaPolicy


@pytest.fixture
def default_config() -> dict:
    """Return a standard preflop delta configuration."""
    return {
        "preflop_delta": {
            "enabled": True,
            "sample_threshold_low": 50,
            "sample_threshold_high": 200,
            "shift_cap_low": 0.03,
            "shift_cap_high": 0.08,
            "timeout_ms": 1000,
        }
    }


@pytest.fixture
def policy(default_config: dict) -> PreflopDeltaPolicy:
    """Return a policy without an LLM pipeline."""
    return PreflopDeltaPolicy(llm_pipeline=None, config=default_config)


class MockDeltaPipeline:
    """Small LLM pipeline stub for delta policy tests."""

    @staticmethod
    def _anonymize_stats(stats: dict) -> dict:
        """Return stats without player-name keys."""
        return {
            key: value
            for key, value in stats.items()
            if key not in {"name", "player_name"}
        }

    def request_preflop_delta(self, request: dict) -> dict:
        """Return a valid delta response."""
        return {"delta_probs": {"raise": 0.05, "call": -0.02, "fold": -0.03}}


class SlowDeltaPipeline(MockDeltaPipeline):
    """LLM pipeline stub that exceeds the delta timeout."""

    def request_preflop_delta(self, request: dict) -> dict:
        """Sleep before returning a valid response."""
        time.sleep(0.02)
        return super().request_preflop_delta(request)


class TestShouldApply:
    """Tests for delta-policy gating."""

    def test_disabled(self, default_config: dict) -> None:
        """Disabled delta policy never applies."""
        default_config["preflop_delta"]["enabled"] = False
        disabled_policy = PreflopDeltaPolicy(llm_pipeline=MockDeltaPipeline(), config=default_config)

        assert disabled_policy.should_apply({"total_hands": 100}) is False

    def test_no_llm(self, policy: PreflopDeltaPolicy) -> None:
        """Missing LLM pipeline disables delta policy."""
        assert policy.should_apply({"total_hands": 100}) is False

    def test_no_stats(self, default_config: dict) -> None:
        """Missing villain stats disables delta policy."""
        enabled_policy = PreflopDeltaPolicy(llm_pipeline=MockDeltaPipeline(), config=default_config)

        assert enabled_policy.should_apply(None) is False

    def test_insufficient_hands(self, default_config: dict) -> None:
        """Samples below the low threshold do not apply delta policy."""
        enabled_policy = PreflopDeltaPolicy(llm_pipeline=MockDeltaPipeline(), config=default_config)

        assert enabled_policy.should_apply({"total_hands": 20}) is False

    def test_sufficient_hands(self, default_config: dict) -> None:
        """Samples at or above the low threshold apply delta policy."""
        enabled_policy = PreflopDeltaPolicy(llm_pipeline=MockDeltaPipeline(), config=default_config)

        assert enabled_policy.should_apply({"total_hands": 50}) is True


class TestShiftCap:
    """Tests for sample-size based shift caps."""

    def test_low_sample(self, policy: PreflopDeltaPolicy) -> None:
        """Low samples use the low cap."""
        assert policy.get_shift_cap({"total_hands": 50}) == 0.03

    def test_high_sample(self, policy: PreflopDeltaPolicy) -> None:
        """High samples use the high cap."""
        assert policy.get_shift_cap({"total_hands": 250}) == 0.08

    def test_boundary(self, policy: PreflopDeltaPolicy) -> None:
        """The high threshold boundary uses the high cap."""
        assert policy.get_shift_cap({"total_hands": 200}) == 0.08


class TestValidateDelta:
    """Tests for delta validation and clamping."""

    def test_valid_delta(self, policy: PreflopDeltaPolicy) -> None:
        """A balanced, in-range delta is accepted."""
        chart = {"raise": 0.3, "call": 0.5, "fold": 0.2}
        delta = {"raise": 0.05, "call": -0.02, "fold": -0.03}

        result = policy._validate_delta(delta, chart, 0.10)

        assert result is not None
        assert abs(sum(result.values())) < 0.01

    def test_pure_node_positive_delta_rejected(self, policy: PreflopDeltaPolicy) -> None:
        """Positive delta for a zero-probability chart action is rejected."""
        chart = {"raise": 0.5, "call": 0.5, "fold": 0.0}
        delta = {"raise": -0.05, "call": -0.05, "fold": 0.10}

        result = policy._validate_delta(delta, chart, 0.10)

        assert result is None

    def test_delta_clamped_to_shift_cap(self, policy: PreflopDeltaPolicy) -> None:
        """Oversized deltas are clamped and rebalanced within the cap."""
        chart = {"raise": 0.5, "call": 0.3, "fold": 0.2}
        delta = {"raise": 0.20, "call": -0.10, "fold": -0.10}

        result = policy._validate_delta(delta, chart, 0.05)

        assert result is not None
        assert abs(sum(result.values())) < 0.01
        for value in result.values():
            assert abs(value) <= 0.05 + 0.001

    def test_sum_not_zero_is_rebalanced(self, policy: PreflopDeltaPolicy) -> None:
        """Unbalanced deltas are rebalanced when possible."""
        chart = {"raise": 0.5, "call": 0.5, "fold": 0.1}
        delta = {"raise": 0.05, "call": 0.05, "fold": 0.0}

        result = policy._validate_delta(delta, chart, 0.10)

        assert result is not None
        assert abs(sum(result.values())) < 0.01

    def test_result_out_of_range_rejected(self, policy: PreflopDeltaPolicy) -> None:
        """Adjusted probabilities outside [0, 1] are rejected."""
        chart = {"raise": 0.95, "call": 0.05, "fold": 0.0}
        delta = {"raise": 0.10, "call": -0.10, "fold": 0.0}

        result = policy._validate_delta(delta, chart, 0.10)

        assert result is None

    def test_none_delta_returns_none(self, policy: PreflopDeltaPolicy) -> None:
        """None delta is rejected."""
        chart = {"raise": 0.5, "call": 0.5}

        assert policy._validate_delta(None, chart, 0.10) is None


class TestApplyDelta:
    """Tests for applying validated deltas."""

    def test_apply_valid_delta(self, policy: PreflopDeltaPolicy) -> None:
        """A valid delta updates probabilities."""
        chart = {"raise": 0.3, "call": 0.5, "fold": 0.2}
        delta = {"raise": 0.05, "call": -0.02, "fold": -0.03}

        result = policy._apply_delta(chart, delta)

        assert abs(result["raise"] - 0.35) < 0.001
        assert abs(result["call"] - 0.48) < 0.001
        assert abs(result["fold"] - 0.17) < 0.001

    def test_apply_clamps_to_zero_one(self, policy: PreflopDeltaPolicy) -> None:
        """Final application clamps probabilities to [0, 1]."""
        chart = {"raise": 0.02, "call": 0.98}
        delta = {"raise": -0.05, "call": 0.05}

        result = policy._apply_delta(chart, delta)

        assert result["raise"] == 0.0
        assert result["call"] == 1.0


class TestApplyIntegration:
    """Integration-level tests for policy application."""

    def test_no_llm_returns_chart(self, policy: PreflopDeltaPolicy) -> None:
        """No LLM pipeline returns chart probabilities unchanged."""
        chart = {"raise": 0.5, "call": 0.3, "fold": 0.2}

        result = policy.apply("BTN", "AKo", "RFI", chart, {"total_hands": 50})

        assert result == chart

    def test_insufficient_hands_returns_chart(self, default_config: dict) -> None:
        """Insufficient sample returns chart probabilities unchanged."""
        enabled_policy = PreflopDeltaPolicy(
            llm_pipeline=MockDeltaPipeline(),
            config=default_config,
        )
        chart = {"raise": 0.5, "call": 0.3, "fold": 0.2}

        result = enabled_policy.apply(
            "BTN",
            "AKo",
            "RFI",
            chart,
            {"total_hands": 10},
        )

        assert result == chart

    def test_valid_llm_delta_adjusts_chart(self, default_config: dict) -> None:
        """A valid LLM delta adjusts chart probabilities."""
        enabled_policy = PreflopDeltaPolicy(
            llm_pipeline=MockDeltaPipeline(),
            config=default_config,
        )
        chart = {"raise": 0.5, "call": 0.3, "fold": 0.2}

        result = enabled_policy.apply(
            "BTN",
            "AKo",
            "RFI",
            chart,
            {"total_hands": 50, "player_name": "PrivateName"},
        )

        assert result["raise"] == pytest.approx(0.53)
        assert result["call"] == pytest.approx(0.29)
        assert result["fold"] == pytest.approx(0.18)

    def test_timeout_returns_chart(self, default_config: dict) -> None:
        """A slow delta response is discarded."""
        default_config["preflop_delta"]["timeout_ms"] = 1
        enabled_policy = PreflopDeltaPolicy(
            llm_pipeline=SlowDeltaPipeline(),
            config=default_config,
        )
        chart = {"raise": 0.5, "call": 0.3, "fold": 0.2}

        result = enabled_policy.apply(
            "BTN",
            "AKo",
            "RFI",
            chart,
            {"total_hands": 50},
        )

        assert result == chart
