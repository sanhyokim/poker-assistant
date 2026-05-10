"""Tests for the main application window."""

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QGroupBox, QPushButton, QTableWidget

from core.game_state import create_empty_game_state
from gui import MainWindow
import gui.main_window as main_window


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Return a QApplication for widget tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_main_window_constructs_with_tabs(qapp: QApplication) -> None:
    """MainWindow creates Operation, Settings, and Statistics tabs."""
    _ = qapp
    window = MainWindow()

    assert window.windowTitle() == "Poker Assistant"
    assert window._tabs.count() == 3
    assert [window._tabs.tabText(index) for index in range(3)] == [
        "Operation",
        "Settings",
        "Statistics",
    ]
    assert hasattr(window, "_summary_labels")
    assert "Table" in window._summary_labels
    assert hasattr(window, "_player_table")
    assert window._player_table.rowCount() == 5


def test_start_stop_button_emits_signals(qapp: QApplication) -> None:
    """START/STOP button toggles state and emits matching signals."""
    _ = qapp
    window = MainWindow()
    start_callback = MagicMock()
    stop_callback = MagicMock()
    window.start_requested.connect(start_callback)
    window.stop_requested.connect(stop_callback)

    window._start_stop_btn.click()
    assert window._is_running is True
    assert window._start_stop_btn.text() == "STOP"
    assert window._status_label.text() == "Running"
    start_callback.assert_called_once()

    window._start_stop_btn.click()
    assert window._is_running is False
    assert window._start_stop_btn.text() == "START"
    assert window._status_label.text() == "Stopped"
    stop_callback.assert_called_once()


def test_reload_button_emits_signal(qapp: QApplication) -> None:
    """Reload button emits reload_requested."""
    _ = qapp
    window = MainWindow()
    reload_callback = MagicMock()
    window.reload_requested.connect(reload_callback)

    window._reload_btn.click()

    reload_callback.assert_called_once()


def test_update_phase_sets_text_and_color(qapp: QApplication) -> None:
    """update_phase changes phase label text and style."""
    _ = qapp
    window = MainWindow()

    window.update_phase("turn")

    assert window._phase_label.text() == "Phase: turn"
    assert "#3399ff" in window._phase_label.styleSheet()


def test_update_game_state_displays_json(qapp: QApplication) -> None:
    """update_game_state serializes GameState as JSON."""
    _ = qapp
    window = MainWindow()
    game_state = create_empty_game_state()
    game_state.phase = "flop"
    game_state.pot = 300
    game_state.hero.cards = ["Ah", "Kd"]

    window.update_game_state(game_state)

    text = window._state_display.toPlainText()
    assert '"phase": "flop"' in text
    assert '"pot": 300' in text
    assert '"cards": [' in text


def test_update_game_state_updates_summary_panel(qapp: QApplication) -> None:
    """update_game_state updates the operation summary labels."""
    _ = qapp
    window = MainWindow()
    game_state = create_empty_game_state()
    game_state.phase = "flop"
    game_state.table_visible = True
    game_state.hand_id = 12
    game_state.frame_number = 99
    game_state.pot = 750
    game_state.board = ["Ah", "Kd", "2c"]
    game_state.active_player_count = 3
    game_state.hero.cards = ["Qs", "Qh"]
    game_state.hero.stack = 4200
    game_state.hero.bet = 100
    game_state.hero.in_current_hand = True
    game_state.hero.has_folded = False
    game_state.hero.is_my_turn = True

    window.update_game_state(game_state)

    assert window._summary_labels["Table"].text() == "VISIBLE"
    assert window._summary_labels["Phase"].text() == "flop"
    assert window._summary_labels["Hand ID"].text() == "12"
    assert window._summary_labels["Frame"].text() == "99"
    assert window._summary_labels["Pot"].text() == "750"
    assert window._summary_labels["Board"].text() == "Ah Kd 2c"
    assert window._summary_labels["Active"].text() == "3"
    assert window._summary_labels["Hero"].text() == "Qs Qh / stack=4200 / bet=100"
    assert window._summary_labels["Hero In Hand"].text() == "YES"
    assert window._summary_labels["Hero Folded"].text() == "NO"
    assert window._summary_labels["Turn"].text() == "YES"


def test_update_game_state_updates_player_table(qapp: QApplication) -> None:
    """update_game_state updates seat rows in the operation player table."""
    _ = qapp
    window = MainWindow()
    game_state = create_empty_game_state()
    game_state.table_visible = True
    game_state.players["2"].name = "Alice"
    game_state.players["2"].stack = 5000
    game_state.players["2"].bet = 100
    game_state.players["2"].is_seated = True
    game_state.players["2"].cards_visible = True
    game_state.players["2"].in_current_hand = True
    game_state.players["3"].name = "Bob"
    game_state.players["3"].stack = 4800
    game_state.players["3"].is_seated = True
    game_state.players["3"].cards_visible = False
    game_state.players["3"].in_current_hand = False

    window.update_game_state(game_state)

    assert window._player_table.item(0, 0).text() == "2"
    assert window._player_table.item(0, 1).text() == "Alice"
    assert window._player_table.item(0, 2).text() == "5000"
    assert window._player_table.item(0, 3).text() == "100"
    assert window._player_table.item(0, 4).text() == "YES"
    assert window._player_table.item(0, 5).text() == "YES"
    assert window._player_table.item(0, 6).text() == "YES"
    assert window._player_table.item(0, 7).text() == "ACTIVE"
    assert window._player_table.item(1, 1).text() == "Bob"
    assert window._player_table.item(1, 7).text() == "WAITING"


def test_update_game_state_table_closed_summary_and_players(
    qapp: QApplication,
) -> None:
    """Closed table state is visible in summary and clears player rows."""
    _ = qapp
    window = MainWindow()
    game_state = create_empty_game_state()
    game_state.table_visible = False
    game_state.players["2"].name = "Alice"
    game_state.players["2"].stack = 5000
    game_state.players["2"].is_seated = True
    game_state.players["2"].cards_visible = True
    game_state.players["2"].in_current_hand = True

    window.update_game_state(game_state)

    assert window._summary_labels["Table"].text() == "CLOSED"
    assert window._player_table.item(0, 1).text() == "-"
    assert window._player_table.item(0, 2).text() == "-"
    assert window._player_table.item(0, 4).text() == "NO"
    assert window._player_table.item(0, 5).text() == "NO"
    assert window._player_table.item(0, 6).text() == "NO"
    assert window._player_table.item(0, 7).text() == "TABLE CLOSED"


def test_clear_live_state_resets_summary(qapp: QApplication) -> None:
    """clear_live_state resets summary labels and phase display."""
    _ = qapp
    window = MainWindow()
    game_state = create_empty_game_state()
    game_state.phase = "river"
    game_state.table_visible = True
    game_state.hand_id = 9
    game_state.pot = 2400

    window.update_game_state(game_state)
    window.clear_live_state()

    assert all(label.text() == "-" for label in window._summary_labels.values())
    assert window._phase_label.text() == "Phase: waiting"


def test_clear_live_state_resets_player_table(qapp: QApplication) -> None:
    """clear_live_state clears player rows and hides Rejoin buttons."""
    _ = qapp
    window = MainWindow()
    game_state = create_empty_game_state()
    game_state.phase = "flop"
    game_state.table_visible = True
    game_state.players["3"].name = "Bob"
    game_state.players["3"].stack = 4800
    game_state.players["3"].is_seated = True
    game_state.players["3"].in_current_hand = False
    window.update_game_state(game_state)

    assert window._rejoin_buttons[3].isHidden() is False

    window.clear_live_state()

    for row, seat in enumerate(range(2, 7)):
        assert window._player_table.item(row, 0).text() == str(seat)
        assert window._player_table.item(row, 1).text() == "-"
    for button in window._rejoin_buttons.values():
        assert button.isHidden() is True


def test_request_stop_calls_clear_live_state(qapp: QApplication) -> None:
    """_request_stop clears live displays before emitting stop."""
    _ = qapp
    window = MainWindow()
    game_state = create_empty_game_state()
    game_state.phase = "turn"
    game_state.table_visible = True
    game_state.pot = 1200
    window.update_game_state(game_state)

    window._request_stop()

    assert all(label.text() == "-" for label in window._summary_labels.values())
    assert window._phase_label.text() == "Phase: waiting"
    assert window._state_display.toPlainText() == ""


def test_rejoin_buttons_exist_outside_table(qapp: QApplication) -> None:
    """Rejoin buttons exist as separate widgets, not table cells."""
    _ = qapp
    window = MainWindow()

    assert hasattr(window, "_rejoin_buttons")
    assert len(window._rejoin_buttons) == 5
    assert window._player_table.columnCount() == 8
    for seat in range(2, 7):
        assert window._rejoin_buttons[seat] is not None
        assert window._rejoin_buttons[seat].isHidden() is True


def test_rejoin_button_visible_for_out_seat(qapp: QApplication) -> None:
    """OUT seated players in active phases show an external Rejoin button."""
    _ = qapp
    window = MainWindow()
    game_state = create_empty_game_state()
    game_state.phase = "flop"
    game_state.table_visible = True
    game_state.players["3"].name = "Bob"
    game_state.players["3"].stack = 4800
    game_state.players["3"].is_seated = True
    game_state.players["3"].cards_visible = True
    game_state.players["3"].in_current_hand = False

    window.update_game_state(game_state)

    button = window._rejoin_buttons[3]
    assert isinstance(button, QPushButton)
    assert button.isHidden() is False
    assert button.text() == "Rejoin Seat 3"


def test_rejoin_button_hidden_for_active_seat(qapp: QApplication) -> None:
    """ACTIVE seats do not show a Rejoin button."""
    _ = qapp
    window = MainWindow()
    game_state = create_empty_game_state()
    game_state.phase = "flop"
    game_state.table_visible = True
    game_state.players["3"].stack = 4800
    game_state.players["3"].is_seated = True
    game_state.players["3"].cards_visible = True
    game_state.players["3"].in_current_hand = True

    window.update_game_state(game_state)

    assert window._rejoin_buttons[3].isHidden() is True


def test_rejoin_button_hidden_during_waiting(qapp: QApplication) -> None:
    """Waiting phase does not show Rejoin buttons."""
    _ = qapp
    window = MainWindow()
    game_state = create_empty_game_state()
    game_state.phase = "waiting"
    game_state.table_visible = True
    game_state.players["3"].stack = 4800
    game_state.players["3"].is_seated = True
    game_state.players["3"].cards_visible = True
    game_state.players["3"].in_current_hand = False

    window.update_game_state(game_state)

    for button in window._rejoin_buttons.values():
        assert button.isHidden() is True


def test_rejoin_signal_emitted_on_click(qapp: QApplication) -> None:
    """Rejoin button click emits rejoin_seat_requested with the seat number."""
    _ = qapp
    window = MainWindow()
    callback = MagicMock()
    window.rejoin_seat_requested.connect(callback)
    game_state = create_empty_game_state()
    game_state.phase = "flop"
    game_state.table_visible = True
    game_state.players["3"].stack = 4800
    game_state.players["3"].is_seated = True
    game_state.players["3"].cards_visible = True
    game_state.players["3"].in_current_hand = False
    window.update_game_state(game_state)

    button = window._rejoin_buttons[3]
    assert isinstance(button, QPushButton)
    button.click()

    callback.assert_called_once_with(3)
    assert button.text() == "Requesting..."
    assert button.isEnabled() is False


def test_append_log_and_filter(qapp: QApplication) -> None:
    """append_log adds messages that pass the current filter."""
    _ = qapp
    window = MainWindow()

    window.append_log("info message", "INFO")
    assert "[INFO] info message" in window._log_display.toPlainText()

    window._log_filter_combo.setCurrentText("WARNING")
    window.append_log("hidden info", "INFO")
    window.append_log("visible warning", "WARNING")
    log_text = window._log_display.toPlainText()

    assert "hidden info" not in log_text
    assert "[WARNING] visible warning" in log_text


def test_mark_stopped_forces_stopped_state(qapp: QApplication) -> None:
    """mark_stopped resets the operation controls to stopped."""
    _ = qapp
    window = MainWindow()
    window._request_start()

    window.mark_stopped()

    assert window._is_running is False
    assert window._start_stop_btn.text() == "START"
    assert window._status_label.text() == "Stopped"


def test_settings_tab_has_groups(qapp: QApplication) -> None:
    """Settings tab contains the required configuration groups."""
    _ = qapp
    window = MainWindow()
    settings_groups = window._tabs.widget(1).findChildren(QGroupBox)

    assert [group.title() for group in settings_groups] == [
        "Capture",
        "Game",
        "Solver",
        "LLM",
        "HUD",
        "OCR",
    ]


def test_settings_initial_values_from_config(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings widgets are initialized from config and environment."""
    _ = qapp
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    config = {
        "capture": {
            "method": "mss",
            "device_index": 2,
            "polling_interval_sec": 0.7,
        },
        "game": {"blind_sb": 25, "blind_bb": 50},
        "solver": {
            "cli_path": "solver/bin/custom.exe",
            "max_iterations": 300,
            "target_exploitability_pct": 0.75,
            "timeout_ms": 9000,
            "default_bet_sizes": "50%,a",
        },
        "llm": {"timeout_sec": 3.5, "retry_count": 2},
        "hud": {"font_size": 16, "opacity": 0.65},
        "ocr": {"confidence_threshold": 0.55},
    }

    window = MainWindow(config)

    assert window._settings_capture_method.currentText() == "mss"
    assert window._settings_device_index.value() == 2
    assert window._settings_polling_interval.value() == pytest.approx(0.7)
    assert window._settings_blind_sb.value() == 25
    assert window._settings_blind_bb.value() == 50
    assert window._settings_solver_cli_path.text() == "solver/bin/custom.exe"
    assert window._settings_solver_cli_path.isReadOnly() is True
    assert window._settings_solver_iterations.value() == 300
    assert window._settings_solver_exploitability.value() == pytest.approx(0.75)
    assert window._settings_solver_timeout.value() == 9000
    assert window._settings_solver_bet_sizes.text() == "50%,a"
    assert window._settings_llm_api_status.text() == "Configured (masked)"
    assert window._settings_llm_timeout.value() == pytest.approx(3.5)
    assert window._settings_llm_retry.value() == 2
    assert window._settings_hud_font_size.value() == 16
    assert window._settings_hud_opacity.value() == pytest.approx(0.65)
    assert window._settings_ocr_confidence.value() == pytest.approx(0.55)


def test_settings_api_key_not_set(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM API status shows Not Set when environment key is absent."""
    _ = qapp
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    window = MainWindow()

    assert window._settings_llm_api_status.text() == "Not Set"


def test_get_settings_returns_current_values(qapp: QApplication) -> None:
    """get_settings returns current widget values in config-like structure."""
    _ = qapp
    window = MainWindow()
    window._settings_capture_method.setCurrentText("file")
    window._settings_device_index.setValue(3)
    window._settings_polling_interval.setValue(1.2)
    window._settings_blind_sb.setValue(75)
    window._settings_blind_bb.setValue(150)
    window._settings_solver_iterations.setValue(400)
    window._settings_solver_exploitability.setValue(0.25)
    window._settings_solver_timeout.setValue(12000)
    window._settings_solver_bet_sizes.setText("33%,75%,a")
    window._settings_llm_timeout.setValue(4.0)
    window._settings_llm_retry.setValue(3)
    window._settings_hud_font_size.setValue(18)
    window._settings_hud_opacity.setValue(0.9)
    window._settings_ocr_confidence.setValue(0.6)

    settings = window.get_settings()

    assert settings == {
        "capture": {
            "method": "file",
            "device_index": 3,
            "polling_interval_sec": 1.2,
        },
        "game": {"blind_sb": 75, "blind_bb": 150},
        "solver": {
            "max_iterations": 400,
            "target_exploitability_pct": 0.25,
            "timeout_ms": 12000,
            "default_bet_sizes": "33%,75%,a",
        },
        "llm": {"timeout_sec": 4.0, "retry_count": 3},
        "hud": {"font_size": 18, "opacity": 0.9},
        "ocr": {"confidence_threshold": 0.6},
    }


def test_statistics_tab_has_table_and_toolbar(qapp: QApplication) -> None:
    """Statistics tab contains table, toolbar buttons, and count label."""
    _ = qapp
    window = MainWindow()
    statistics_tab = window._tabs.widget(2)
    buttons = [button.text() for button in statistics_tab.findChildren(QPushButton)]
    tables = statistics_tab.findChildren(QTableWidget)

    assert buttons == ["Refresh", "Export CSV", "Export JSON"]
    assert len(tables) == 1
    assert window._stats_table.columnCount() == 10
    assert [
        window._stats_table.horizontalHeaderItem(index).text()
        for index in range(window._stats_table.columnCount())
    ] == [
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
    assert window._stats_count_label.text() == "0 players"


def test_load_opponents_populates_statistics_table(qapp: QApplication) -> None:
    """load_opponents displays DB rows and updates the count label."""
    _ = qapp
    window = MainWindow()
    opponents = [
        {
            "player_name": "mrkrebs",
            "total_hands": 156,
            "vpip": 0.243,
            "pfr": 0.187,
            "three_bet_pct": 0.082,
            "cbet_flop_pct": 0.65,
            "fold_to_three_bet": 0.55,
            "went_to_showdown": 0.28,
            "long_term_style": "TAG",
            "last_seen": "2026-04-28",
            "freshness_note": "",
        },
        {
            "player_name": "old_player",
            "total_hands": 5,
            "vpip": 30.0,
            "pfr": 12.0,
            "three_bet_pct": 4.0,
            "cbet_flop_pct": 40.0,
            "fold_to_three_bet": 70.0,
            "went_to_showdown": 18.0,
            "long_term_style": "Loose",
            "last_seen": "2025-01-01",
            "freshness_note": "old data",
        },
    ]

    window.load_opponents(opponents)

    assert window._stats_table.rowCount() == 2
    assert window._stats_count_label.text() == "2 players"
    assert window._stats_table.item(0, 0).text() == "mrkrebs"
    assert window._stats_table.item(0, 2).text() == "24.3%"
    assert window._stats_table.item(1, 9).background().color().getRgb()[:3] == (
        80,
        30,
        30,
    )


def test_statistics_selection_updates_detail(qapp: QApplication) -> None:
    """Selecting a player row updates the detail view."""
    _ = qapp
    window = MainWindow()
    window.load_opponents(
        [
            {
                "player_name": "mrkrebs",
                "total_hands": 156,
                "vpip": 0.243,
                "pfr": 0.187,
                "three_bet_pct": 0.082,
                "cbet_flop_pct": 0.65,
                "fold_to_three_bet": 0.55,
                "went_to_showdown": 0.28,
                "long_term_style": "TAG",
                "last_seen": "2026-04-28",
                "freshness_note": "",
            }
        ]
    )

    window._stats_table.selectRow(0)

    detail_text = window._stats_detail.toPlainText()
    assert "Player: mrkrebs" in detail_text
    assert "Total Hands: 156" in detail_text
    assert "VPIP: 24.3%" in detail_text
    assert "Note: (none)" in detail_text


def test_statistics_numeric_sort(qapp: QApplication) -> None:
    """Statistics table sorts numeric columns by value, not text."""
    _ = qapp
    window = MainWindow()
    window.load_opponents(
        [
            {"player_name": "low", "total_hands": 5, "vpip": 0.1},
            {"player_name": "high", "total_hands": 100, "vpip": 0.4},
        ]
    )

    window._stats_table.sortItems(1, Qt.SortOrder.DescendingOrder)

    assert window._stats_table.item(0, 0).text() == "high"


def test_statistics_export_csv_and_json(
    qapp: QApplication,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Statistics export handlers write CSV and JSON files."""
    _ = qapp
    window = MainWindow()
    opponents = [
        {
            "player_name": "mrkrebs",
            "total_hands": 156,
            "vpip": 0.243,
            "pfr": 0.187,
        }
    ]
    window.load_opponents(opponents)

    with tempfile.TemporaryDirectory(dir=project_root / "tests") as temp_dir:
        export_dir = Path(temp_dir)
        csv_path = export_dir / "opponents.csv"
        json_path = export_dir / "opponents.json"

        monkeypatch.setattr(
            main_window.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(csv_path), "CSV Files (*.csv)"),
        )
        window._on_export_csv()

        monkeypatch.setattr(
            main_window.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(json_path), "JSON Files (*.json)"),
        )
        window._on_export_json()

        csv_text = csv_path.read_text(encoding="utf-8")
        assert "player_name,total_hands" in csv_text
        assert "mrkrebs" in csv_text
        assert json.loads(json_path.read_text(encoding="utf-8")) == opponents


def test_refresh_statistics_loads_opponents_from_db(
    qapp: QApplication,
    project_root: Path,
) -> None:
    """refresh_statistics reads opponents from SQLite and updates the table."""
    _ = qapp
    with tempfile.TemporaryDirectory(dir=project_root / "tests") as temp_dir:
        db_path = Path(temp_dir) / "stats.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE opponents (
                    player_name TEXT PRIMARY KEY,
                    total_hands INTEGER,
                    vpip REAL,
                    pfr REAL,
                    three_bet_pct REAL,
                    cbet_flop_pct REAL,
                    fold_to_three_bet REAL,
                    went_to_showdown REAL,
                    long_term_style TEXT,
                    last_seen TEXT,
                    freshness_note TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO opponents (
                    player_name, total_hands, vpip, pfr, three_bet_pct,
                    cbet_flop_pct, fold_to_three_bet, went_to_showdown,
                    long_term_style, last_seen, freshness_note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "villain",
                    4,
                    0.25,
                    0.10,
                    0.0,
                    0.0,
                    0.0,
                    0.50,
                    "Unknown",
                    "2026-04-30",
                    "",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        window = MainWindow({"db": {"path": str(db_path)}})

        window.refresh_statistics(hand_id=1)

        assert window._stats_table.rowCount() == 1
        assert window._stats_table.item(0, 0).text() == "villain"
        assert window._stats_count_label.text() == "1 players"
        window.close()
