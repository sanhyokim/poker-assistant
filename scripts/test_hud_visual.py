"""HUD overlay manual visual test.

Run with: python scripts/test_hud_visual.py

Displays the HUD overlay and cycles through states:
  0s  - Waiting state
  3s  - Computing state
  6s  - RAISE recommendation (high confidence, solver, with probabilities)
  10s - FOLD recommendation (low confidence, llm, no probabilities)
  14s - CHECK recommendation (medium confidence, preflop_chart)
  18s - ALL_IN recommendation (high confidence, multiway)
  22s - CALL recommendation (medium confidence, solver)
  26s - Back to Waiting (None)

Press F9 at any time to toggle HUD visibility.
Drag the window with left mouse button to reposition.
Close the terminal (Ctrl+C) or wait 30s to exit.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from gui.hud_overlay import HudOverlay
from strategy.recommendation_engine import Recommendation


def main() -> None:
    """Run the HUD visual test."""
    app = QApplication(sys.argv)

    hud = HudOverlay(config={"font_size": 14, "opacity": 0.85})
    hud.show()

    control = QWidget()
    control.setWindowTitle("HUD Test Control")
    control.resize(280, 80)
    control.move(100, 100)
    ctrl_layout = QVBoxLayout()
    ctrl_label = QLabel(
        "F9: toggle HUD visibility\n"
        "Drag HUD to reposition\n"
        "Auto-exit in 30s"
    )
    ctrl_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    ctrl_layout.addWidget(ctrl_label)
    control.setLayout(ctrl_layout)
    control.show()

    shortcut = QShortcut(QKeySequence("F9"), control)
    shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
    shortcut.activated.connect(hud.toggle_visibility)

    print("HUD launched. Cycling through states...")
    print("Press F9 to toggle visibility. Drag to move. Ctrl+C or wait 30s to exit.")

    QTimer.singleShot(3000, hud.show_computing)

    def show_raise() -> None:
        """Show a high-confidence raise recommendation."""
        rec = Recommendation(
            action="RAISE",
            amount=300,
            reason=(
                "Strong value hand on wet board. Opponent likely has marginal "
                "holdings."
            ),
            confidence="high",
            strategy_source="solver",
            action_probabilities={"Check": 0.15, "Bet 60%": 0.55, "All-in": 0.30},
        )
        hud.update_recommendation(rec)
        print("  -> RAISE 300 (high, solver)")

    QTimer.singleShot(6000, show_raise)

    def show_fold() -> None:
        """Show a low-confidence fold recommendation."""
        rec = Recommendation(
            action="FOLD",
            amount=0,
            reason="No equity against this range.",
            confidence="low",
            strategy_source="llm",
        )
        hud.update_recommendation(rec)
        print("  -> FOLD (low, llm)")

    QTimer.singleShot(10000, show_fold)

    def show_check() -> None:
        """Show a medium-confidence check recommendation."""
        rec = Recommendation(
            action="CHECK",
            amount=0,
            reason="Board favors opponent range. Check to control pot size.",
            confidence="medium",
            strategy_source="preflop_chart",
        )
        hud.update_recommendation(rec)
        print("  -> CHECK (medium, preflop_chart)")

    QTimer.singleShot(14000, show_check)

    def show_allin() -> None:
        """Show a high-confidence all-in recommendation."""
        rec = Recommendation(
            action="ALL_IN",
            amount=5000,
            reason="Nut flush draw with pair. Maximum equity realization.",
            confidence="high",
            strategy_source="multiway",
            action_probabilities={"Fold": 0.0, "Call": 0.05, "All-in": 0.95},
        )
        hud.update_recommendation(rec)
        print("  -> ALL_IN 5000 (high, multiway)")

    QTimer.singleShot(18000, show_allin)

    def show_call() -> None:
        """Show a medium-confidence call recommendation."""
        rec = Recommendation(
            action="CALL",
            amount=150,
            reason="Getting correct pot odds with decent equity.",
            confidence="medium",
            strategy_source="solver",
            action_probabilities={"Fold": 0.30, "Call": 0.60, "Raise": 0.10},
        )
        hud.update_recommendation(rec)
        print("  -> CALL 150 (medium, solver)")

    QTimer.singleShot(22000, show_call)

    def show_waiting() -> None:
        """Return the HUD to waiting state."""
        hud.update_recommendation(None)
        print("  -> Waiting (None)")

    QTimer.singleShot(26000, show_waiting)
    QTimer.singleShot(30000, app.quit)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
