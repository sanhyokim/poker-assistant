"""Tests for the PyQt HUD overlay."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from gui import HudOverlay
from strategy.recommendation_engine import Recommendation


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Return a QApplication for widget tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_hud_overlay_updates_recommendation(qapp: QApplication) -> None:
    """update_recommendation displays action, confidence, source, and reason."""
    _ = qapp
    overlay = HudOverlay(config={"font_size": 12, "opacity": 0.75})
    recommendation = Recommendation(
        action="RAISE",
        amount=300,
        reason="Strong value spot",
        confidence="high",
        strategy_source="solver",
        action_probabilities={"CHECK": 0.25, "RAISE 300": 0.75},
        pot_percentage=50.0,
        amount_bb=3.0,
        preset_hint="50%",
        raise_multiplier=3.0,
        raise_multiplier_label="3.0X",
    )

    overlay.update_recommendation(recommendation)
    QApplication.processEvents()

    assert overlay._action_label.text() == "RAISE 300 (3.0BB) [3.0X]"
    assert overlay._confidence_label.text() == "HIGH"
    assert overlay._source_label.text() == "Source: Solver"
    assert "Solver Mix:" in overlay._probabilities_label.text()
    assert "RAISE 300 75%" in overlay._probabilities_label.text()
    assert overlay._reason_label.text() == "Strong value spot"
    assert "#ffa500" in overlay._action_label.styleSheet()


def test_hud_overlay_formats_amount_without_preset(qapp: QApplication) -> None:
    """CALL displays chip and BB amounts without pot-size metadata."""
    _ = qapp
    recommendation = Recommendation(
        action="CALL",
        amount=200,
        confidence="medium",
        strategy_source="solver",
        pot_percentage=17.0,
        amount_bb=2.0,
    )

    assert HudOverlay._format_action(recommendation) == "CALL 200 (2.0BB)"


def test_hud_overlay_formats_bet_with_pot_preset(qapp: QApplication) -> None:
    """BET displays the pot-size preset."""
    _ = qapp
    recommendation = Recommendation(
        action="BET",
        amount=825,
        confidence="medium",
        strategy_source="solver",
        pot_percentage=33.0,
        amount_bb=8.2,
        preset_hint="33%",
    )

    assert HudOverlay._format_action(recommendation) == "BET 825 (8.2BB) [33%pot]"


def test_hud_overlay_formats_all_in_without_ratio_hint(qapp: QApplication) -> None:
    """ALL_IN displays amount and BB only."""
    _ = qapp
    recommendation = Recommendation(
        action="ALL_IN",
        amount=9500,
        confidence="medium",
        strategy_source="solver",
        pot_percentage=100.0,
        amount_bb=95.0,
        preset_hint="100%",
        raise_multiplier=4.8,
        raise_multiplier_label="4.8X",
    )

    assert HudOverlay._format_action(recommendation) == "ALL_IN 9500 (95.0BB)"


def test_hud_overlay_formats_check_without_size_metadata() -> None:
    """CHECK and FOLD display without size metadata."""
    recommendation = Recommendation(
        action="CHECK",
        amount=0,
        pot_percentage=50.0,
        amount_bb=3.0,
        preset_hint="50%",
    )

    assert HudOverlay._format_action(recommendation) == "CHECK"


def test_hud_source_label_chart() -> None:
    """Chart source maps to Source: Chart."""
    recommendation = Recommendation(action="FOLD", strategy_source="preflop_chart")

    assert HudOverlay._source_display_label(recommendation.strategy_source) == "Chart"


def test_hud_source_label_solver() -> None:
    """Solver source maps to Source: Solver."""
    recommendation = Recommendation(action="CHECK", strategy_source="solver")

    assert HudOverlay._source_display_label(recommendation.strategy_source) == "Solver"


def test_hud_action_name_remains_english() -> None:
    """アクション名は英語のまま表示される。"""
    recommendation = Recommendation(
        action="RAISE",
        amount=300,
        strategy_source="preflop_chart",
        amount_bb=3.0,
        raise_multiplier_label="3.0X",
    )

    assert HudOverlay._format_action(recommendation) == "RAISE 300 (3.0BB) [3.0X]"


def test_hud_overlay_waiting_and_computing_states(qapp: QApplication) -> None:
    """Waiting and computing states hide recommendation labels."""
    _ = qapp
    overlay = HudOverlay()

    overlay.show_computing()
    assert overlay._status_label.text() == "Computing..."
    assert overlay._action_label.isHidden() is True

    overlay.update_recommendation(None)
    QApplication.processEvents()
    assert overlay._status_label.text() == "Waiting for hand..."
    assert overlay._probabilities_label.isHidden() is True


def test_hud_overlay_solver_probabilities_are_sorted_and_limited(
    qapp: QApplication,
) -> None:
    """Solver probabilities are sorted, capped at three, and label ALL_IN clearly."""
    _ = qapp
    overlay = HudOverlay()
    recommendation = Recommendation(
        action="FOLD",
        strategy_source="solver",
        action_probabilities={
            "CALL": 0.31,
            "FOLD": 0.52,
            "ALL_IN 2934": 0.17,
            "RAISE 700": 0.01,
        },
        reason="solver result",
    )

    overlay.update_recommendation(recommendation)
    QApplication.processEvents()

    lines = overlay._probabilities_label.text().splitlines()
    assert lines == [
        "Solver Mix:",
        "FOLD 52%",
        "CALL 31%",
        "ALL-IN 2934 17%",
    ]
    probability_index = overlay.layout().indexOf(overlay._probabilities_label)
    reason_index = overlay.layout().indexOf(overlay._reason_label)
    assert probability_index < reason_index


@pytest.mark.parametrize(
    "strategy_source",
    ["preflop_chart", "llm_multiway", "solver_timeout"],
)
def test_hud_overlay_hides_solver_mix_for_non_solver_sources(
    qapp: QApplication,
    strategy_source: str,
) -> None:
    """Solver Mix is only shown for solver-sourced recommendations."""
    _ = qapp
    overlay = HudOverlay()
    recommendation = Recommendation(
        action="FOLD",
        strategy_source=strategy_source,
        action_probabilities={"FOLD": 0.7, "CALL": 0.3},
        reason="not solver",
    )

    overlay.update_recommendation(recommendation)
    QApplication.processEvents()

    assert overlay._probabilities_label.isHidden() is True
    assert "Solver Mix" not in overlay._probabilities_label.text()


def test_hud_overlay_solver_timeout_message(qapp: QApplication) -> None:
    """Solver timeout recommendations render as an explicit non-strategy result."""
    _ = qapp
    overlay = HudOverlay()
    recommendation = Recommendation(
        action="SOLVER_TIMEOUT",
        reason="Solver timeout: no reliable solver result",
        confidence="low",
        strategy_source="solver_timeout",
    )

    overlay.update_recommendation(recommendation)
    QApplication.processEvents()

    assert overlay._action_label.text() == "SOLVER TIMEOUT"
    assert overlay._source_label.text() == "Source: Solver Timeout"
    assert overlay._reason_label.text() == "Solver timeout: no reliable solver result"


@pytest.mark.parametrize(
    "message",
    ["CHART CHECKING...", "LLM ANALYZING...", "SOLVER THINKING..."],
)
def test_hud_overlay_computing_uses_message(
    qapp: QApplication,
    message: str,
) -> None:
    """show_computing accepts and displays caller-provided messages."""
    _ = qapp
    overlay = HudOverlay()

    overlay.show_computing(message)

    assert overlay._status_label.text() == message
    assert overlay._status_label.isHidden() is False
    assert overlay._action_label.isHidden() is True


def test_hud_overlay_color_helpers() -> None:
    """Action and confidence color helpers map required values."""
    assert HudOverlay._action_color("FOLD").name() == QColor(255, 80, 80).name()
    assert HudOverlay._action_color("CHECK").name() == QColor(80, 200, 80).name()
    assert HudOverlay._action_color("CALL").name() == QColor(80, 150, 255).name()
    assert HudOverlay._action_color("BET").name() == QColor(255, 165, 0).name()
    assert HudOverlay._action_color("RAISE").name() == QColor(255, 165, 0).name()
    assert HudOverlay._action_color("ALL_IN").name() == QColor(255, 0, 255).name()

    high_text, high_color = HudOverlay._confidence_display("high")
    medium_text, medium_color = HudOverlay._confidence_display("medium")
    low_text, low_color = HudOverlay._confidence_display("low")

    assert high_text == "HIGH"
    assert high_color.name() == QColor(80, 200, 80).name()
    assert medium_text == "MEDIUM"
    assert medium_color.name() == QColor(255, 200, 50).name()
    assert low_text == "LOW"
    assert low_color.name() == QColor(255, 80, 80).name()


def test_hud_overlay_drag_state(qapp: QApplication) -> None:
    """Dragging state can be cleared on mouse release."""
    _ = qapp
    overlay = HudOverlay()
    overlay._drag_position = QPoint(10, 10)

    class FakeMouseEvent:
        """Minimal mouse event for release handling."""

        def __init__(self) -> None:
            self.accepted = False

        def accept(self) -> None:
            """Record event acceptance."""
            self.accepted = True

    event = FakeMouseEvent()
    overlay.mouseReleaseEvent(event)  # type: ignore[arg-type]

    assert overlay._drag_position is None
    assert event.accepted is True


# ---------------------------------------------------------------------------
# Phase 30-Fix37: HUD closing guard test
# ---------------------------------------------------------------------------


def test_closing_hud_ignores_updates(qapp: QApplication) -> None:
    """HUD that is closing ignores incoming recommendation updates."""
    _ = qapp
    overlay = HudOverlay()

    # Simulate closing
    overlay.mark_closing()
    assert overlay._closing is True

    # Sending an update while closing should not crash
    rec = Recommendation(action="CALL", amount=100, reason="test")
    overlay._on_update(rec)

    # After marking open, updates should work again
    overlay.mark_open()
    assert overlay._closing is False
    overlay._on_update(rec)

    overlay.close()
