"""Main application window with tabbed interface.

Provides Operation, Settings, and Statistics tabs for the poker assistant.
SPEC.md section 9.1.
"""

import csv
import json
import logging
import os
import sqlite3
from dataclasses import asdict
from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.game_state import GameState

logger = logging.getLogger(__name__)

_STATS_SORT_ROLE = Qt.ItemDataRole.UserRole
_STATS_OPPONENT_INDEX_ROLE = Qt.ItemDataRole.UserRole + 1
_STATS_COLUMNS = [
    "Player",
    "Hands",
    "VPIP",
    "PFR",
    "3Bet%",
    "CBet%",
    "F/3Bet",
    "WTSD",
    "Style",
    "Last Seen",
]
_STATS_EXPORT_FIELDS = [
    "player_name",
    "total_hands",
    "vpip",
    "pfr",
    "three_bet_pct",
    "cbet_flop_pct",
    "fold_to_three_bet",
    "went_to_showdown",
    "long_term_style",
    "last_seen",
    "freshness_note",
]
_OPERATION_PLAYER_COLUMNS = [
    "Seat",
    "Name",
    "Stack",
    "Bet",
    "Seated",
    "Cards",
    "In Hand",
    "Status",
]


class _SortableTableWidgetItem(QTableWidgetItem):
    """Table item that sorts by a hidden numeric/text value when present."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(_STATS_SORT_ROLE)
        right = other.data(_STATS_SORT_ROLE)
        if left is not None and right is not None:
            return left < right
        return super().__lt__(other)


class MainWindow(QMainWindow):
    """Main tabbed window for controlling and monitoring the assistant.

    Args:
        config: Optional parsed config dictionary.
    """

    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    reload_requested = pyqtSignal()
    rejoin_seat_requested = pyqtSignal(int)

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._config = config or {}
        self._is_running = False
        self._opponents_data: list[dict[str, Any]] = []
        self._last_displayed_hand_id: int | None = None
        self._stats_db_path = str(
            self._config.get("db", {}).get("path", "data/poker_assistant.db")
        )

        self.setWindowTitle("Poker Assistant")
        self.resize(1100, 760)

        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)
        self._tabs.addTab(self._create_operation_tab(), "Operation")
        self._tabs.addTab(self._create_settings_tab(), "Settings")
        self._tabs.addTab(self._create_statistics_tab(), "Statistics")

    def update_phase(self, phase: str) -> None:
        """Update the displayed game phase.

        Args:
            phase: Current phase string.
        """
        self._phase_label.setText(f"Phase: {phase}")
        color_map = {
            "waiting": "#888888",
            "PRE-HAND": "#ffdd66",
            "preflop": "#ffcc00",
            "flop": "#33cc33",
            "turn": "#3399ff",
            "river": "#ff6633",
            "hand_end": "#cc33cc",
        }
        color = color_map.get(phase, "#ffffff")
        self._phase_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def update_recommendation(self, recommendation: Any) -> None:
        """Update the recommendation display area.

        Args:
            recommendation: Recommendation object, or None to clear.
        """
        if recommendation is None:
            self._recommendation_display.clear()
            self._recommendation_display.setPlaceholderText(
                "推奨アクションがここに表示されます"
            )
            return

        try:
            action = getattr(recommendation, "action", "?")
            amount = getattr(recommendation, "amount", 0)
            source = getattr(recommendation, "strategy_source", "?")
            confidence = getattr(recommendation, "confidence", "?")
            reason = getattr(recommendation, "reason", "")

            lines = [
                f"Action: {action} {amount}" if amount > 0 else f"Action: {action}",
                f"Source: {source}",
                f"Confidence: {confidence}",
            ]
            if reason:
                lines.append("")
                lines.append(f"Reason: {reason}")

            self._recommendation_display.setPlainText("\n".join(lines))
        except Exception:
            logger.warning(
                "Failed to format recommendation for display",
                exc_info=True,
            )

    def update_game_state(self, game_state: GameState) -> None:
        """Update the GameState JSON display.

        Args:
            game_state: Current game state to display.
        """
        try:
            if game_state.hand_start_status == "PRE-HAND":
                self.update_phase("PRE-HAND")
            self._update_summary_panel(game_state)
            self._update_player_table(game_state)
            state_dict = asdict(game_state)
            json_text = json.dumps(state_dict, indent=2, ensure_ascii=False)
        except Exception:
            logger.exception("Failed to serialize GameState for display")
            json_text = str(game_state)
        self._state_display.setPlainText(json_text)

    def clear_live_state(self) -> None:
        """Clear live game state displays after polling stops."""
        self._last_displayed_hand_id = None
        for key in self._summary_labels:
            self._set_summary_value(key, "-")
        self.update_phase("waiting")

        for row, seat in enumerate(range(2, 7)):
            for column in range(self._player_table.columnCount()):
                self._player_table.removeCellWidget(row, column)
                value = str(seat) if column == 0 else "-"
                foreground = QColor("#888888")
                background = QColor("#1e1e1e")
                self._player_table.setItem(
                    row,
                    column,
                    self._make_operation_table_item(value, foreground, background),
                )

        for button in self._rejoin_buttons.values():
            button.setVisible(False)

        self._state_display.clear()
        logger.info("Live state cleared")

    def append_log(self, message: str, level: str = "INFO") -> None:
        """Append a log message to the operation log display.

        Args:
            message: Log message text.
            level: Log level string.
        """
        if not self._should_show_log(level):
            return
        color_map = {
            "DEBUG": "#888888",
            "INFO": "#d4d4d4",
            "WARNING": "#ffcc00",
            "ERROR": "#ff3333",
        }
        level_name = level.upper()
        color = color_map.get(level_name, "#d4d4d4")
        self._log_display.append(
            f'<span style="color:{color}">[{level_name}] {message}</span>'
        )

    def get_settings(self) -> dict[str, Any]:
        """Return the current values from the Settings tab widgets.

        Returns:
            Dictionary mirroring the editable config.yaml sections.
        """
        return {
            "capture": {
                "method": self._settings_capture_method.currentText(),
                "device_index": self._settings_device_index.value(),
                "polling_interval_sec": self._settings_polling_interval.value(),
            },
            "game": {
                "blind_sb": self._settings_blind_sb.value(),
                "blind_bb": self._settings_blind_bb.value(),
            },
            "solver": {
                "max_iterations": self._settings_solver_iterations.value(),
                "target_exploitability_pct": (
                    self._settings_solver_exploitability.value()
                ),
                "timeout_ms": self._settings_solver_timeout.value(),
                "default_bet_sizes": self._settings_solver_bet_sizes.text(),
            },
            "llm": {
                "timeout_sec": self._settings_llm_timeout.value(),
                "retry_count": self._settings_llm_retry.value(),
            },
            "hud": {
                "font_size": self._settings_hud_font_size.value(),
                "opacity": self._settings_hud_opacity.value(),
            },
            "ocr": {
                "confidence_threshold": self._settings_ocr_confidence.value(),
            },
        }

    def load_opponents(self, opponents: list[dict[str, Any]]) -> None:
        """Populate the statistics table with opponent data.

        Args:
            opponents: Opponent dictionaries from the DB opponents table.
        """
        self._opponents_data = opponents
        self._stats_table.setSortingEnabled(False)
        self._stats_table.setRowCount(0)

        for row, opponent in enumerate(opponents):
            self._stats_table.insertRow(row)
            player_item = self._create_table_item(
                str(opponent.get("player_name") or ""),
                str(opponent.get("player_name") or "").lower(),
            )
            player_item.setData(_STATS_OPPONENT_INDEX_ROLE, row)
            self._stats_table.setItem(row, 0, player_item)

            total_hands = self._int_value(opponent.get("total_hands"))
            self._stats_table.setItem(
                row,
                1,
                self._create_table_item(str(total_hands), total_hands),
            )

            percent_fields = [
                "vpip",
                "pfr",
                "three_bet_pct",
                "cbet_flop_pct",
                "fold_to_three_bet",
                "went_to_showdown",
            ]
            for offset, field_name in enumerate(percent_fields, start=2):
                percent = self._percent_value(opponent.get(field_name))
                self._stats_table.setItem(
                    row,
                    offset,
                    self._create_table_item(f"{percent:.1f}%", percent),
                )

            style = str(opponent.get("long_term_style") or "")
            self._stats_table.setItem(row, 8, self._create_table_item(style, style))

            last_seen = str(opponent.get("last_seen") or "")
            last_seen_item = self._create_table_item(last_seen, last_seen)
            if opponent.get("freshness_note"):
                last_seen_item.setBackground(QColor(80, 30, 30))
            self._stats_table.setItem(row, 9, last_seen_item)

        self._stats_table.setSortingEnabled(True)
        self._stats_count_label.setText(f"{len(opponents)} players")
        self._stats_detail.clear()

    def refresh_statistics(self, hand_id: int | None = None) -> None:
        """Reload opponent statistics from SQLite into the Statistics tab.

        Args:
            hand_id: Optional hand ID that triggered the refresh.
        """
        _ = hand_id
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(
                self._stats_db_path,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT player_name, total_hands, vpip, pfr, three_bet_pct, "
                "cbet_flop_pct, fold_to_three_bet, went_to_showdown, "
                "long_term_style, last_seen, freshness_note "
                "FROM opponents ORDER BY total_hands DESC"
            )
            opponents = [dict(row) for row in cursor.fetchall()]
            self.load_opponents(opponents)
            logger.info("Statistics refreshed: %d opponents", len(opponents))
        except sqlite3.Error as exc:
            logger.warning("Failed to refresh statistics: %s", exc)
        finally:
            if conn is not None:
                conn.close()

    def mark_stopped(self) -> None:
        """Force the UI into stopped state from an external trigger."""
        self._is_running = False
        self._start_stop_btn.setText("START")
        self._start_stop_btn.setStyleSheet("")
        self._status_label.setText("Stopped")
        self._status_label.setStyleSheet("color: #cc3333;")
        self.clear_live_state()

    def _create_operation_tab(self) -> QWidget:
        """Create the operation tab contents."""
        tab = QWidget()
        main_layout = QVBoxLayout()

        control_bar = QHBoxLayout()
        self._start_stop_btn = QPushButton("START")
        self._start_stop_btn.clicked.connect(self._on_start_stop_clicked)
        self._reload_btn = QPushButton("Reload Config")
        self._reload_btn.clicked.connect(self._on_reload_clicked)
        self._phase_label = QLabel("Phase: waiting")
        self.update_phase("waiting")
        control_bar.addWidget(self._start_stop_btn)
        control_bar.addWidget(self._reload_btn)
        control_bar.addStretch(1)
        control_bar.addWidget(self._phase_label)

        summary_group = QGroupBox("Current State")
        summary_group.setStyleSheet(
            "QGroupBox { color: #d4d4d4; font-weight: bold; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; }"
        )
        summary_layout = QGridLayout()
        self._summary_labels: dict[str, QLabel] = {}
        summary_keys = [
            "Table",
            "Phase",
            "Hand ID",
            "Frame",
            "Pot",
            "Board",
            "Active",
            "Hero",
            "Hero In Hand",
            "Hero Folded",
            "Turn",
        ]
        value_font = QFont("Consolas", 11)
        value_font.setBold(True)
        for index, key in enumerate(summary_keys):
            row = index // 5
            column = (index % 5) * 2
            key_label = QLabel(f"{key}:")
            key_label.setStyleSheet("color: #9cdcfe;")
            value_label = QLabel("-")
            value_label.setFont(value_font)
            value_label.setMinimumWidth(120)
            value_label.setStyleSheet(
                "background-color: #252526; color: #d4d4d4; "
                "padding: 4px 6px; border: 1px solid #3c3c3c;"
            )
            summary_layout.addWidget(key_label, row, column)
            summary_layout.addWidget(value_label, row, column + 1)
            self._summary_labels[key] = value_label
        summary_group.setLayout(summary_layout)

        self._player_table = QTableWidget()
        self._player_table.setColumnCount(len(_OPERATION_PLAYER_COLUMNS))
        self._player_table.setHorizontalHeaderLabels(_OPERATION_PLAYER_COLUMNS)
        self._player_table.setRowCount(5)
        self._player_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._player_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._player_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._player_table.verticalHeader().setVisible(False)
        self._player_table.setAlternatingRowColors(True)
        self._player_table.setMinimumHeight(155)
        self._player_table.setMaximumHeight(210)
        self._player_table.setStyleSheet(
            "QTableWidget { background-color: #1e1e1e; color: #d4d4d4; "
            "gridline-color: #3c3c3c; alternate-background-color: #252526; }"
            "QHeaderView::section { background-color: #2d2d30; color: #ffffff; "
            "padding: 4px; border: 1px solid #3c3c3c; }"
        )
        player_header = self._player_table.horizontalHeader()
        player_header.setStretchLastSection(False)
        player_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

        self._rejoin_button_layout = QHBoxLayout()
        self._rejoin_button_layout.setSpacing(6)
        self._rejoin_buttons: dict[int, QPushButton] = {}
        for seat in range(2, 7):
            button = QPushButton(f"Rejoin Seat {seat}")
            button.setFixedHeight(28)
            button.setMinimumWidth(100)
            button.setVisible(False)
            button.setStyleSheet(
                "QPushButton { background-color: #3c6e3c; color: white; "
                "border: 1px solid #5a5a5a; border-radius: 4px; "
                "font-size: 11px; padding: 2px 8px; }"
                "QPushButton:hover { background-color: #4a8a4a; }"
                "QPushButton:pressed { background-color: #2a5a2a; }"
            )
            button.clicked.connect(
                lambda _checked=False, selected_seat=seat: self._on_rejoin_clicked(
                    selected_seat
                )
            )
            self._rejoin_buttons[seat] = button
            self._rejoin_button_layout.addWidget(button)
        self._rejoin_button_layout.addStretch(1)

        self._state_display = QTextEdit()
        self._state_display.setReadOnly(True)
        self._state_display.setFont(QFont("Consolas", 11))
        self._state_display.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._state_display.setStyleSheet(
            "background-color: #1e1e1e; color: #d4d4d4;"
        )

        self._log_display = QTextEdit()
        self._log_display.setReadOnly(True)
        self._log_display.setFont(QFont("Consolas", 9))
        self._log_display.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._log_display.setStyleSheet(
            "background-color: #1e1e1e; color: #d4d4d4;"
        )
        self._log_display.document().setMaximumBlockCount(1000)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._state_display)
        splitter.addWidget(self._log_display)
        splitter.setSizes([260, 220])

        self._recommendation_display = QTextEdit()
        self._recommendation_display.setReadOnly(True)
        self._recommendation_display.setLineWrapMode(
            QTextEdit.LineWrapMode.WidgetWidth
        )
        self._recommendation_display.setMinimumHeight(90)
        self._recommendation_display.setMaximumHeight(140)
        self._recommendation_display.setFont(QFont("Meiryo", 10))
        self._recommendation_display.setPlaceholderText(
            "推奨アクションがここに表示されます"
        )
        self._recommendation_display.setStyleSheet(
            "QTextEdit {"
            "  background-color: #1a1a2e;"
            "  color: #d4d4d4;"
            "  border: 1px solid #4a6a8a;"
            "  border-radius: 4px;"
            "  padding: 6px;"
            "}"
        )

        status_bar = QHBoxLayout()
        self._status_label = QLabel("Stopped")
        self._status_label.setStyleSheet("color: #cc3333;")
        self._log_filter_combo = QComboBox()
        self._log_filter_combo.addItems(["ALL", "INFO", "WARNING", "ERROR"])
        status_bar.addWidget(self._status_label)
        status_bar.addStretch(1)
        status_bar.addWidget(QLabel("Log Filter:"))
        status_bar.addWidget(self._log_filter_combo)

        main_layout.addLayout(control_bar)
        main_layout.addWidget(summary_group)
        main_layout.addWidget(self._player_table)
        main_layout.addLayout(self._rejoin_button_layout)
        main_layout.addWidget(self._recommendation_display)
        main_layout.addWidget(splitter)
        main_layout.addLayout(status_bar)
        tab.setLayout(main_layout)
        return tab

    def _update_summary_panel(self, game_state: GameState) -> None:
        """Update the compact operation summary labels."""
        board_text = " ".join(game_state.board) if game_state.board else "-"
        hero_cards = game_state.hero.cards or []
        hero_cards_text = " ".join(str(card) for card in hero_cards) or "-"
        if hero_cards_text != "-":
            hero_text = (
                f"{hero_cards_text} / stack={self._display_value(game_state.hero.stack)} "
                f"/ bet={game_state.hero.bet}"
            )
        else:
            hero_text = "-"

        self._set_summary_value("Phase", game_state.phase, self._phase_color(game_state.phase))
        table_text = "VISIBLE" if game_state.table_visible else "CLOSED"
        table_color = "#33cc66" if game_state.table_visible else "#cc6666"
        self._set_summary_value("Table", table_text, table_color)
        display_hand_id = game_state.hand_id
        if display_hand_id is not None:
            self._last_displayed_hand_id = display_hand_id
        elif game_state.phase in {"hand_end", "waiting"}:
            display_hand_id = self._last_displayed_hand_id
        self._set_summary_value("Hand ID", self._display_value(display_hand_id))
        self._set_summary_value("Frame", str(game_state.frame_number))
        self._set_summary_value("Pot", str(game_state.pot))
        self._set_summary_value("Board", board_text)
        self._set_summary_value("Active", str(game_state.active_player_count))
        self._set_summary_value("Hero", hero_text)
        self._set_summary_value(
            "Hero In Hand",
            self._yes_no(game_state.hero.in_current_hand),
            "#33cc66" if game_state.hero.in_current_hand else "#888888",
        )
        self._set_summary_value(
            "Hero Folded",
            self._yes_no(game_state.hero.has_folded),
            "#ff6666" if game_state.hero.has_folded else "#33cc66",
        )
        self._set_summary_value(
            "Turn",
            self._yes_no(game_state.hero.is_my_turn),
            "#ffcc00" if game_state.hero.is_my_turn else "#888888",
        )

    def _update_player_table(self, game_state: GameState) -> None:
        """Update seat 2-6 player rows for quick recognition debugging."""
        self._player_table.setRowCount(5)
        for row, seat in enumerate(range(2, 7)):
            seat_key = str(seat)
            player = game_state.players.get(seat_key)
            if not game_state.table_visible:
                name = "-"
                stack = "-"
                bet = "0"
                is_seated = False
                cards_visible = False
                in_hand = False
                status = "TABLE CLOSED"
            else:
                name = player.name if player is not None and player.name else "-"
                stack = self._display_value(player.stack if player is not None else None)
                bet = str(player.bet if player is not None else 0)
                is_seated = bool(player is not None and player.is_seated)
                raw_cards_visible = bool(player is not None and player.cards_visible)
                in_hand = bool(player is not None and player.in_current_hand)
                cards_visible = bool(is_seated and in_hand and raw_cards_visible)
                status = self._player_status(
                    is_seated, cards_visible, in_hand, phase=game_state.phase,
                )
            status_foreground, status_background = self._status_colors(status)

            values = [
                str(seat),
                name,
                stack,
                bet,
                self._yes_no(is_seated),
                self._yes_no(cards_visible),
                self._yes_no(in_hand),
                status,
            ]
            for column, value in enumerate(values):
                foreground = QColor("#d4d4d4")
                background = QColor("#1e1e1e")
                if column == 4:
                    foreground = QColor("#33cc66" if is_seated else "#888888")
                elif column == 5:
                    foreground = QColor("#33cc66" if cards_visible else "#888888")
                elif column == 6:
                    foreground = QColor("#33cc66" if in_hand else "#888888")
                elif column == 7:
                    foreground = status_foreground
                    background = status_background
                self._player_table.setItem(
                    row,
                    column,
                    self._make_operation_table_item(value, foreground, background),
                )
        self._update_rejoin_buttons(game_state)

    def _update_rejoin_buttons(self, game_state: GameState) -> None:
        """Show or hide external rejoin buttons based on player state."""
        for seat in range(2, 7):
            button = self._rejoin_buttons.get(seat)
            if button is None:
                continue
            seat_key = str(seat)
            player = game_state.players.get(seat_key)
            is_seated = bool(player is not None and player.is_seated)
            in_hand = bool(player is not None and player.in_current_hand)
            can_rejoin = (
                is_seated
                and not in_hand
                and game_state.phase in {"preflop", "flop", "turn", "river"}
            )
            button.setVisible(can_rejoin)

    def _on_rejoin_clicked(self, seat: int) -> None:
        """Handle rejoin button click with visual feedback.

        Args:
            seat: Seat number from 2 to 6.
        """
        logger.info("Rejoin button clicked for seat %d", seat)
        button = self._rejoin_buttons.get(seat)
        if button is not None:
            original_text = button.text()
            original_style = button.styleSheet()
            button.setText("Requesting...")
            button.setStyleSheet(
                "QPushButton { background-color: #b59f3b; color: white; "
                "border: 1px solid #5a5a5a; border-radius: 4px; "
                "font-size: 11px; padding: 2px 8px; }"
            )
            button.setEnabled(False)
            QTimer.singleShot(
                1500,
                lambda: self._restore_rejoin_button(
                    button,
                    original_text,
                    original_style,
                ),
            )
        self.rejoin_seat_requested.emit(seat)

    def _restore_rejoin_button(
        self,
        button: QPushButton,
        text: str,
        style: str,
    ) -> None:
        """Restore a rejoin button after feedback timeout."""
        button.setText(text)
        button.setStyleSheet(style)
        button.setEnabled(True)

    def _set_summary_value(
        self,
        key: str,
        value: str,
        color: str | None = None,
    ) -> None:
        """Set one operation summary value label."""
        label = self._summary_labels.get(key)
        if label is None:
            return
        text_color = color or "#d4d4d4"
        label.setText(value)
        label.setStyleSheet(
            "background-color: #252526; "
            f"color: {text_color}; "
            "padding: 4px 6px; border: 1px solid #3c3c3c;"
        )

    def _make_operation_table_item(
        self,
        text: str,
        foreground: QColor,
        background: QColor,
    ) -> QTableWidgetItem:
        """Create a non-editable operation table item."""
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setForeground(foreground)
        item.setBackground(background)
        return item

    def _player_status(
        self,
        is_seated: bool,
        cards_visible: bool,
        in_current_hand: bool,
        phase: str = "",
    ) -> str:
        """Return compact player status text for the operation table."""
        if phase == "hand_end":
            return "END"
        if in_current_hand:
            return "ACTIVE"
        if not is_seated:
            return "EMPTY"
        if not cards_visible:
            return "WAITING"
        return "OUT"

    def _status_colors(self, status: str) -> tuple[QColor, QColor]:
        """Return foreground/background colors for a player status."""
        colors = {
            "ACTIVE": ("#ffffff", "#1f6f3a"),
            "WAITING": ("#1e1e1e", "#b59f3b"),
            "OUT": ("#d4d4d4", "#4d4d4d"),
            "END": ("#ffffff", "#5a3a6e"),
            "EMPTY": ("#888888", "#2d2d30"),
            "TABLE CLOSED": ("#ff9999", "#3a1f1f"),
        }
        foreground, background = colors.get(status, ("#d4d4d4", "#1e1e1e"))
        return QColor(foreground), QColor(background)

    def _phase_color(self, phase: str) -> str:
        """Return display color for a phase value."""
        return {
            "waiting": "#888888",
            "preflop": "#ffcc00",
            "flop": "#33cc33",
            "turn": "#3399ff",
            "river": "#ff6633",
            "hand_end": "#cc33cc",
        }.get(phase, "#d4d4d4")

    def _yes_no(self, value: bool) -> str:
        """Return YES/NO text for boolean display."""
        return "YES" if value else "NO"

    def _display_value(self, value: Any) -> str:
        """Return a compact display string for optional values."""
        return "-" if value is None else str(value)

    def _create_settings_tab(self) -> QWidget:
        """Create the scrollable Settings tab."""
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)

        form_container = QWidget()
        form_layout = QVBoxLayout()
        form_layout.addWidget(self._create_capture_settings_group())
        form_layout.addWidget(self._create_game_settings_group())
        form_layout.addWidget(self._create_solver_settings_group())
        form_layout.addWidget(self._create_llm_settings_group())
        form_layout.addWidget(self._create_hud_settings_group())
        form_layout.addWidget(self._create_ocr_settings_group())
        form_layout.addStretch(1)
        form_container.setLayout(form_layout)

        scroll_area.setWidget(form_container)
        return scroll_area

    def _create_statistics_tab(self) -> QWidget:
        """Create the opponent statistics tab."""
        tab = QWidget()
        main_layout = QVBoxLayout()

        toolbar = QHBoxLayout()
        self._stats_refresh_btn = QPushButton("Refresh")
        self._stats_export_csv_btn = QPushButton("Export CSV")
        self._stats_export_json_btn = QPushButton("Export JSON")
        self._stats_refresh_btn.clicked.connect(self.refresh_statistics)
        self._stats_export_csv_btn.clicked.connect(self._on_export_csv)
        self._stats_export_json_btn.clicked.connect(self._on_export_json)
        toolbar.addWidget(self._stats_refresh_btn)
        toolbar.addWidget(self._stats_export_csv_btn)
        toolbar.addWidget(self._stats_export_json_btn)
        toolbar.addStretch(1)

        self._stats_table = QTableWidget()
        self._stats_table.setColumnCount(len(_STATS_COLUMNS))
        self._stats_table.setHorizontalHeaderLabels(_STATS_COLUMNS)
        self._stats_table.setSortingEnabled(True)
        self._stats_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._stats_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._stats_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self._stats_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._stats_table.itemSelectionChanged.connect(self._on_player_selected)

        self._stats_detail = QTextEdit()
        self._stats_detail.setReadOnly(True)
        self._stats_detail.setFont(QFont("Consolas", 10))
        self._stats_detail.setStyleSheet(
            "background-color: #1e1e1e; color: #d4d4d4;"
        )

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._stats_table)
        splitter.addWidget(self._stats_detail)
        splitter.setSizes([560, 340])

        self._stats_count_label = QLabel("0 players")

        main_layout.addLayout(toolbar)
        main_layout.addWidget(splitter)
        main_layout.addWidget(self._stats_count_label)
        tab.setLayout(main_layout)
        return tab

    def _create_capture_settings_group(self) -> QGroupBox:
        """Create Capture settings controls."""
        capture_cfg = self._config.get("capture", {})
        group = QGroupBox("Capture")
        layout = QFormLayout()

        self._settings_capture_method = QComboBox()
        self._settings_capture_method.addItems(["capture_card", "mss", "file"])
        self._settings_capture_method.setCurrentText(
            str(capture_cfg.get("method", "capture_card"))
        )

        self._settings_device_index = QSpinBox()
        self._settings_device_index.setRange(0, 9)
        self._settings_device_index.setValue(int(capture_cfg.get("device_index", 0)))

        self._settings_polling_interval = QDoubleSpinBox()
        self._settings_polling_interval.setRange(0.1, 5.0)
        self._settings_polling_interval.setSingleStep(0.1)
        self._settings_polling_interval.setValue(
            float(capture_cfg.get("polling_interval_sec", 0.5))
        )

        layout.addRow("Method:", self._settings_capture_method)
        layout.addRow("Device Index:", self._settings_device_index)
        layout.addRow("Polling Interval (s):", self._settings_polling_interval)
        group.setLayout(layout)
        return group

    def _create_game_settings_group(self) -> QGroupBox:
        """Create Game settings controls."""
        game_cfg = self._config.get("game", {})
        group = QGroupBox("Game")
        layout = QFormLayout()

        self._settings_blind_sb = QSpinBox()
        self._settings_blind_sb.setRange(1, 10000)
        self._settings_blind_sb.setValue(int(game_cfg.get("blind_sb", 50)))

        self._settings_blind_bb = QSpinBox()
        self._settings_blind_bb.setRange(1, 10000)
        self._settings_blind_bb.setValue(int(game_cfg.get("blind_bb", 100)))

        layout.addRow("Small Blind:", self._settings_blind_sb)
        layout.addRow("Big Blind:", self._settings_blind_bb)
        group.setLayout(layout)
        return group

    def _create_solver_settings_group(self) -> QGroupBox:
        """Create Solver settings controls."""
        solver_cfg = self._config.get("solver", {})
        group = QGroupBox("Solver")
        layout = QFormLayout()

        self._settings_solver_cli_path = QLineEdit(
            str(solver_cfg.get("cli_path", "solver/bin/postflop_cli.exe"))
        )
        self._settings_solver_cli_path.setReadOnly(True)

        self._settings_solver_iterations = QSpinBox()
        self._settings_solver_iterations.setRange(10, 1000)
        self._settings_solver_iterations.setValue(
            int(solver_cfg.get("max_iterations", 200))
        )

        self._settings_solver_exploitability = QDoubleSpinBox()
        self._settings_solver_exploitability.setRange(0.01, 10.0)
        self._settings_solver_exploitability.setValue(
            float(solver_cfg.get("target_exploitability_pct", 0.5))
        )

        self._settings_solver_timeout = QSpinBox()
        self._settings_solver_timeout.setRange(1000, 30000)
        self._settings_solver_timeout.setValue(int(solver_cfg.get("timeout_ms", 7000)))

        self._settings_solver_bet_sizes = QLineEdit(
            str(solver_cfg.get("default_bet_sizes", "60%,a"))
        )

        layout.addRow("CLI Path:", self._settings_solver_cli_path)
        layout.addRow("Max Iterations:", self._settings_solver_iterations)
        layout.addRow(
            "Target Exploitability %:",
            self._settings_solver_exploitability,
        )
        layout.addRow("Timeout (ms):", self._settings_solver_timeout)
        layout.addRow("Default Bet Sizes:", self._settings_solver_bet_sizes)
        group.setLayout(layout)
        return group

    def _create_llm_settings_group(self) -> QGroupBox:
        """Create LLM settings controls."""
        llm_cfg = self._config.get("llm", {})
        group = QGroupBox("LLM")
        layout = QFormLayout()

        self._settings_llm_api_status = QLabel()
        if os.environ.get("OPENROUTER_API_KEY", ""):
            self._settings_llm_api_status.setText("Configured (masked)")
            self._settings_llm_api_status.setStyleSheet("color: #33cc33;")
        else:
            self._settings_llm_api_status.setText("Not Set")
            self._settings_llm_api_status.setStyleSheet("color: #cc3333;")

        self._settings_llm_timeout = QDoubleSpinBox()
        self._settings_llm_timeout.setRange(0.5, 10.0)
        self._settings_llm_timeout.setValue(float(llm_cfg.get("timeout_sec", 2)))

        self._settings_llm_retry = QSpinBox()
        self._settings_llm_retry.setRange(0, 5)
        self._settings_llm_retry.setValue(int(llm_cfg.get("retry_count", 1)))

        layout.addRow("API Key Status:", self._settings_llm_api_status)
        layout.addRow("Timeout (s):", self._settings_llm_timeout)
        layout.addRow("Retry Count:", self._settings_llm_retry)
        group.setLayout(layout)
        return group

    def _create_hud_settings_group(self) -> QGroupBox:
        """Create HUD settings controls."""
        hud_cfg = self._config.get("hud", {})
        group = QGroupBox("HUD")
        layout = QFormLayout()

        self._settings_hud_font_size = QSpinBox()
        self._settings_hud_font_size.setRange(8, 30)
        self._settings_hud_font_size.setValue(int(hud_cfg.get("font_size", 14)))

        self._settings_hud_opacity = QDoubleSpinBox()
        self._settings_hud_opacity.setRange(0.1, 1.0)
        self._settings_hud_opacity.setSingleStep(0.05)
        self._settings_hud_opacity.setValue(float(hud_cfg.get("opacity", 0.85)))

        layout.addRow("Font Size:", self._settings_hud_font_size)
        layout.addRow("Opacity:", self._settings_hud_opacity)
        group.setLayout(layout)
        return group

    def _create_ocr_settings_group(self) -> QGroupBox:
        """Create OCR settings controls."""
        ocr_cfg = self._config.get("ocr", {})
        group = QGroupBox("OCR")
        layout = QFormLayout()

        self._settings_ocr_confidence = QDoubleSpinBox()
        self._settings_ocr_confidence.setRange(0.1, 1.0)
        self._settings_ocr_confidence.setSingleStep(0.05)
        self._settings_ocr_confidence.setValue(
            float(ocr_cfg.get("confidence_threshold", 0.4))
        )

        layout.addRow("Confidence Threshold:", self._settings_ocr_confidence)
        group.setLayout(layout)
        return group

    def _create_placeholder_tab(self, text: str) -> QWidget:
        """Create a centered placeholder tab.

        Args:
            text: Placeholder message.
        """
        tab = QWidget()
        layout = QVBoxLayout()
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        tab.setLayout(layout)
        return tab

    def _on_player_selected(self) -> None:
        """Show detailed stats for the selected player."""
        selected_items = self._stats_table.selectedItems()
        if not selected_items:
            self._stats_detail.clear()
            return

        row = selected_items[0].row()
        player_item = self._stats_table.item(row, 0)
        if player_item is None:
            self._stats_detail.clear()
            return

        opponent_index = player_item.data(_STATS_OPPONENT_INDEX_ROLE)
        if not isinstance(opponent_index, int) or opponent_index >= len(
            self._opponents_data
        ):
            self._stats_detail.clear()
            return

        opponent = self._opponents_data[opponent_index]
        self._stats_detail.setPlainText(self._format_opponent_detail(opponent))

    def _on_export_csv(self) -> None:
        """Export opponent statistics to a CSV file."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export CSV",
            "",
            "CSV Files (*.csv)",
        )
        if not path:
            logger.warning("CSV export cancelled or failed")
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as file_obj:
                writer = csv.writer(file_obj)
                writer.writerow(_STATS_EXPORT_FIELDS)
                for opponent in self._opponents_data:
                    writer.writerow(
                        [
                            opponent.get(field_name, "")
                            for field_name in _STATS_EXPORT_FIELDS
                        ]
                    )
            logger.info("Exported %d opponents to %s", len(self._opponents_data), path)
        except OSError:
            logger.warning("CSV export cancelled or failed", exc_info=True)

    def _on_export_json(self) -> None:
        """Export opponent statistics to a JSON file."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export JSON",
            "",
            "JSON Files (*.json)",
        )
        if not path:
            logger.warning("JSON export cancelled or failed")
            return

        try:
            with open(path, "w", encoding="utf-8") as file_obj:
                json.dump(self._opponents_data, file_obj, indent=2, ensure_ascii=False)
            logger.info("Exported %d opponents to %s", len(self._opponents_data), path)
        except OSError:
            logger.warning("JSON export cancelled or failed", exc_info=True)

    def _format_opponent_detail(self, opponent: dict[str, Any]) -> str:
        """Return formatted detail text for one opponent."""
        freshness_note = opponent.get("freshness_note") or "(none)"
        return "\n".join(
            [
                f"Player: {opponent.get('player_name') or ''}",
                f"Total Hands: {self._int_value(opponent.get('total_hands'))}",
                f"Style: {opponent.get('long_term_style') or 'Unknown'}",
                "",
                "--- Statistics ---",
                f"VPIP: {self._percent_value(opponent.get('vpip')):.1f}%",
                f"PFR: {self._percent_value(opponent.get('pfr')):.1f}%",
                f"3-Bet: {self._percent_value(opponent.get('three_bet_pct')):.1f}%",
                f"C-Bet Flop: {self._percent_value(opponent.get('cbet_flop_pct')):.1f}%",
                "Fold to 3-Bet: "
                f"{self._percent_value(opponent.get('fold_to_three_bet')):.1f}%",
                "Went to SD: "
                f"{self._percent_value(opponent.get('went_to_showdown')):.1f}%",
                "",
                "--- Freshness ---",
                f"Last Seen: {opponent.get('last_seen') or ''}",
                f"Note: {freshness_note}",
            ]
        )

    def _on_start_stop_clicked(self) -> None:
        """Handle START/STOP button clicks."""
        if self._is_running:
            self._request_stop()
        else:
            self._request_start()

    def _request_start(self) -> None:
        """Switch UI to running state and emit start_requested."""
        self._is_running = True
        self._start_stop_btn.setText("STOP")
        self._start_stop_btn.setStyleSheet("background-color: #cc3333; color: white;")
        self._status_label.setText("Running")
        self._status_label.setStyleSheet("color: #33cc33;")
        self.start_requested.emit()
        logger.info("Start requested")

    def _request_stop(self) -> None:
        """Switch UI to stopped state and emit stop_requested."""
        self._is_running = False
        self._start_stop_btn.setText("START")
        self._start_stop_btn.setStyleSheet("")
        self._status_label.setText("Stopped")
        self._status_label.setStyleSheet("color: #cc3333;")
        self.clear_live_state()
        self.stop_requested.emit()
        logger.info("Stop requested")

    def _on_reload_clicked(self) -> None:
        """Emit reload_requested for config reload."""
        self.reload_requested.emit()
        logger.info("Reload requested")

    def _should_show_log(self, level: str) -> bool:
        """Return whether a log level passes the current filter."""
        current_filter = self._log_filter_combo.currentText()
        if current_filter == "ALL":
            return True
        level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
        return level_order.get(level.upper(), 0) >= level_order.get(
            current_filter,
            0,
        )

    def _create_table_item(self, text: str, sort_value: Any) -> QTableWidgetItem:
        """Create a non-editable table item with a hidden sort value."""
        item = _SortableTableWidgetItem(text)
        item.setData(_STATS_SORT_ROLE, sort_value)
        return item

    def _percent_value(self, value: Any) -> float:
        """Normalize stored ratio or percent values to percent units."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        if abs(number) <= 1.0:
            return number * 100.0
        return number

    def _int_value(self, value: Any) -> int:
        """Convert a DB value to int for display and sorting."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
