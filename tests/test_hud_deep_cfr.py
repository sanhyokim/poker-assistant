"""Tests for Deep CFR HUD rendering."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from gui.hud_overlay import DEEP_CFR_THINKING_MESSAGE, HudOverlay
from strategy.recommendation_engine import Recommendation


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Return a QApplication for HUD tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _make_deep_cfr_recommendation(
    strategy_source: str = "deep_cfr",
    action_probabilities: dict[str, float] | None = None,
) -> Recommendation:
    """Create a displayable Deep CFR recommendation."""
    return Recommendation(
        action="BET",
        amount=250,
        reason="Deep CFR baseline",
        confidence="high",
        strategy_source=strategy_source,
        action_probabilities=action_probabilities
        or {"fold": 0.1, "call": 0.2, "raise": 0.7},
        amount_bb=2.5,
    )


def test_deep_cfr_source_label(qapp: QApplication) -> None:
    """Deep CFR recommendations show a Deep CFR source label."""
    _ = qapp
    overlay = HudOverlay()

    overlay.update_recommendation(_make_deep_cfr_recommendation())
    QApplication.processEvents()

    assert overlay._source_label.text() == "Source: Deep CFR"
    overlay.close()


def test_deep_cfr_exploit_source_label(qapp: QApplication) -> None:
    """Deep CFR exploit recommendations show a Deep CFR+ source label."""
    _ = qapp
    overlay = HudOverlay()

    overlay.update_recommendation(
        _make_deep_cfr_recommendation(strategy_source="deep_cfr_exploit")
    )
    QApplication.processEvents()

    assert overlay._source_label.text() == "Source: Deep CFR+"
    overlay.close()


def test_deep_cfr_probabilities_displayed(qapp: QApplication) -> None:
    """Deep CFR recommendations display action probabilities."""
    _ = qapp
    overlay = HudOverlay()

    overlay.update_recommendation(_make_deep_cfr_recommendation())
    QApplication.processEvents()

    assert overlay._probabilities_label.isHidden() is False
    assert "Deep CFR:" in overlay._probabilities_label.text()
    overlay.close()


def test_deep_cfr_probabilities_sorted() -> None:
    """Deep CFR probability rows are sorted by probability descending."""
    text = HudOverlay._format_deep_cfr_probabilities(
        {"call": 0.25, "raise": 0.6, "fold": 0.15}
    )

    assert text.splitlines() == [
        "Deep CFR:",
        "  raise 60%",
        "  call 25%",
        "  fold 15%",
    ]


def test_deep_cfr_confidence_displayed(qapp: QApplication) -> None:
    """Deep CFR confidence is visible when present."""
    _ = qapp
    overlay = HudOverlay()

    overlay.update_recommendation(_make_deep_cfr_recommendation())
    QApplication.processEvents()

    assert overlay._confidence_label.isHidden() is False
    assert overlay._confidence_label.text() == "Confidence: high"
    overlay.close()


def test_non_deep_cfr_hides_probabilities(qapp: QApplication) -> None:
    """Non-Deep CFR sources keep probability details hidden."""
    _ = qapp
    overlay = HudOverlay()
    recommendation = Recommendation(
        action="BET",
        amount=250,
        strategy_source="solver",
        action_probabilities={"BET": 0.8, "CHECK": 0.2},
    )

    overlay.update_recommendation(recommendation)
    QApplication.processEvents()

    assert overlay._probabilities_label.isHidden() is True
    overlay.close()


def test_deep_cfr_thinking_message(qapp: QApplication) -> None:
    """Deep CFR computing status uses the dedicated message."""
    _ = qapp
    overlay = HudOverlay()

    overlay.show_computing(DEEP_CFR_THINKING_MESSAGE)

    assert overlay._status_label.text() == DEEP_CFR_THINKING_MESSAGE
    assert overlay._status_label.isHidden() is False
    overlay.close()


def test_deep_cfr_action_format(qapp: QApplication) -> None:
    """Deep CFR action and amount use the standard HUD format."""
    _ = qapp
    overlay = HudOverlay()

    overlay.update_recommendation(_make_deep_cfr_recommendation())
    QApplication.processEvents()

    assert overlay._action_label.text() == "BET 250 (2.5BB)"
    overlay.close()
