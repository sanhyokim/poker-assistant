"""HUD overlay window for displaying strategy recommendations.

The overlay is a frameless, always-on-top, translucent PyQt6 window that shows
only confirmed user-facing recommendations and simple processing states.
"""

import logging
from typing import Any

from PyQt6.QtCore import QPoint, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPainterPath,
)
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from strategy.recommendation_engine import Recommendation

logger = logging.getLogger(__name__)

DISPLAYABLE_ACTIONS = {"CHECK", "CALL", "BET", "RAISE", "FOLD", "ALL_IN"}
STABLE_WAITING_MESSAGE = "安定待ち..."


class HudOverlay(QWidget):
    """Transparent HUD window for poker recommendations.

    Args:
        parent: Optional parent widget.
        config: HUD config dictionary. Uses font_size=14 and opacity=0.85 when
            omitted.
    """

    _update_signal = pyqtSignal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(parent)
        hud_config = config or {}
        self._font_size = int(hud_config.get("font_size", 14))
        self._opacity = float(hud_config.get("opacity", 0.85))
        self._drag_position: QPoint | None = None
        self._closing: bool = False
        self._last_computing_message: str | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(300)
        self.setMinimumHeight(200)
        self.move(1600, 200)

        self._setup_layout()
        self._update_signal.connect(self._on_update)
        self.show_waiting()

    def update_recommendation(self, recommendation: Recommendation | None) -> None:
        """Update the HUD with a recommendation in a thread-safe way.

        Args:
            recommendation: Recommendation to display, or None to show waiting.
        """
        self._update_signal.emit(recommendation)

    def show_computing(self, message: str = "Computing...") -> None:
        """Show the computing status message.

        Args:
            message: Status text to display while recommendation work is running.
        """
        if (
            self._last_computing_message == message
            and self._status_label.isVisible()
            and self._action_label.isHidden()
        ):
            return
        self._last_computing_message = message
        self._hide_recommendation_labels()
        self._status_label.setText(message)
        self._set_label_color(self._status_label, QColor(255, 200, 50))
        self._status_label.show()
        self.show()

    def show_waiting_for_stable_hand(self) -> None:
        """Show a non-recommendation waiting state while hand inputs stabilize."""
        self.show_computing(STABLE_WAITING_MESSAGE)

    def show_pre_hand(self) -> None:
        """Show PRE-HAND buffering status without a recommendation."""
        self._last_computing_message = STABLE_WAITING_MESSAGE
        self._hide_recommendation_labels()
        self._status_label.setText(STABLE_WAITING_MESSAGE)
        self._set_label_color(self._status_label, QColor(255, 220, 90))
        self._status_label.show()
        self.show()

    def mark_closing(self) -> None:
        """Flag the HUD as closing so pending updates are ignored."""
        self._closing = True

    def mark_open(self) -> None:
        """Clear the closing flag when starting a new session."""
        self._closing = False

    def show_waiting(self) -> None:
        """Show the waiting-for-hand status message."""
        self._last_computing_message = "Waiting for hand..."
        self._hide_recommendation_labels()
        self._status_label.setText(STABLE_WAITING_MESSAGE)
        self._set_label_color(self._status_label, QColor(180, 180, 180))
        self._status_label.show()

    @pyqtSlot(object)
    def _on_update(self, recommendation: Recommendation | None) -> None:
        """Apply a recommendation update on the UI thread."""
        if self._closing:
            return
        if recommendation is None:
            self.show_waiting()
            return
        if not self._is_displayable_recommendation(recommendation):
            logger.debug(
                "HUD internal recommendation hidden: action=%s source=%s",
                recommendation.action,
                recommendation.strategy_source,
            )
            self.show_computing(STABLE_WAITING_MESSAGE)
            return

        self._last_computing_message = None
        action_text = self._format_action(recommendation)
        action_color = self._action_color(recommendation.action)

        self._status_label.hide()
        self._action_label.setText(action_text)
        self._set_label_color(self._action_label, action_color)
        self._confidence_label.hide()
        self._source_label.hide()
        self._probabilities_label.hide()
        self._separator_1.hide()

        if recommendation.reason:
            self._reason_label.setText(recommendation.reason)
            self._reason_label.show()
        else:
            self._reason_label.hide()

        for label in (
            self._action_label,
            self._separator_2,
        ):
            label.show()

        logger.debug("HUD updated: %s", recommendation.action)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Start dragging when the left mouse button is pressed."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_position = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Move the overlay while dragging with the left mouse button."""
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and self._drag_position is not None
        ):
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Stop dragging when the mouse button is released."""
        self._drag_position = None
        event.accept()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the translucent rounded background."""
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(
            0.0,
            0.0,
            float(self.width()),
            float(self.height()),
            10.0,
            10.0,
        )
        bg_color = QColor(20, 20, 20, int(255 * self._opacity))
        painter.fillPath(path, QBrush(bg_color))
        painter.end()

    def _setup_layout(self) -> None:
        common_font = QFont("Consolas", self._font_size)
        source_font = QFont("Consolas", max(self._font_size - 3, 9))
        action_font = QFont("Consolas", self._font_size + 6)
        action_font.setBold(True)

        self._action_label = QLabel()
        self._action_label.setFont(action_font)
        self._confidence_label = QLabel()
        self._source_label = QLabel()
        self._source_label.setFont(source_font)
        self._separator_1 = QLabel("-" * 30)
        self._probabilities_label = QLabel()
        self._separator_2 = QLabel("-" * 30)
        self._reason_label = QLabel()
        self._status_label = QLabel()

        for label in (
            self._confidence_label,
            self._separator_1,
            self._probabilities_label,
            self._separator_2,
            self._reason_label,
            self._status_label,
        ):
            label.setFont(common_font)
            self._set_label_color(label, QColor(255, 255, 255))

        self._reason_label.setWordWrap(True)
        self._set_label_color(self._separator_1, QColor(90, 90, 90))
        self._set_label_color(self._separator_2, QColor(90, 90, 90))

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self._action_label)
        layout.addWidget(self._confidence_label)
        layout.addWidget(self._source_label)
        layout.addWidget(self._separator_1)
        layout.addWidget(self._probabilities_label)
        layout.addWidget(self._separator_2)
        layout.addWidget(self._reason_label)
        layout.addWidget(self._status_label)
        self.setLayout(layout)

    def _hide_recommendation_labels(self) -> None:
        for label in (
            self._action_label,
            self._confidence_label,
            self._source_label,
            self._separator_1,
            self._probabilities_label,
            self._separator_2,
            self._reason_label,
        ):
            label.hide()

    @staticmethod
    def _set_label_color(label: QLabel, color: QColor) -> None:
        label.setStyleSheet(f"color: {color.name()};")

    @staticmethod
    def _format_action(recommendation: Recommendation) -> str:
        action = recommendation.action.upper()
        if action in {"FOLD", "CHECK"} or recommendation.amount <= 0:
            return action

        text = f"{action} {int(recommendation.amount)}"
        if recommendation.amount_bb is not None:
            text += f" ({recommendation.amount_bb}BB)"
        if action == "RAISE" and recommendation.raise_multiplier_label is not None:
            text += f" [{recommendation.raise_multiplier_label}]"
        elif action == "BET" and recommendation.preset_hint is not None:
            text += f" [{recommendation.preset_hint}pot]"
        elif action == "BET" and recommendation.pot_percentage is not None:
            text += f" [{int(recommendation.pot_percentage)}%pot]"
        return text

    @staticmethod
    def _format_probabilities(probabilities: dict[str, float]) -> str:
        """Return top-three solver probabilities for prominent HUD display."""
        ordered = sorted(
            probabilities.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:3]
        lines = ["Solver Mix:"]
        lines.extend(
            f"{HudOverlay._format_probability_action(action)} {probability:.0%}"
            for action, probability in ordered
        )
        return "\n".join(lines)

    @staticmethod
    def _format_probability_action(action: str) -> str:
        """Return a display-friendly action label for solver mix rows."""
        return action.replace("ALL_IN", "ALL-IN").replace("_", "-")

    @staticmethod
    def _is_displayable_recommendation(recommendation: Recommendation) -> bool:
        """Return whether the recommendation can be shown as a user action."""
        return recommendation.action.upper() in DISPLAYABLE_ACTIONS

    @staticmethod
    def _action_color(action: str) -> QColor:
        action_key = action.upper()
        if action_key == "FOLD":
            return QColor(255, 80, 80)
        if action_key == "CHECK":
            return QColor(80, 200, 80)
        if action_key == "CALL":
            return QColor(80, 150, 255)
        if action_key in {"BET", "RAISE"}:
            return QColor(255, 165, 0)
        if action_key == "ALL_IN":
            return QColor(255, 0, 255)
        return QColor(255, 255, 255)
