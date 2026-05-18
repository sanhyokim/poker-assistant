"""Smoke tests for GUI startup widgets and main.py helpers."""

import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import yaml
import pytest
from PyQt6.QtWidgets import QApplication

import main
from core.game_state import create_empty_game_state


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Return a QApplication for smoke tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_main_window_smoke(qapp: QApplication) -> None:
    """MainWindow creates with 3 tabs and can be shown/hidden."""
    _ = qapp
    from gui.main_window import MainWindow

    window = MainWindow()
    window.show()

    assert window.isVisible()
    assert window._tabs.count() == 3

    window.close()


def test_hud_overlay_smoke(qapp: QApplication) -> None:
    """HudOverlay creates and can show waiting state."""
    _ = qapp
    from gui.hud_overlay import HudOverlay

    hud = HudOverlay()
    hud.show()
    hud.show_waiting()

    assert hud.isVisible()

    hud.close()


def test_hud_overlay_show_pre_hand(qapp: QApplication) -> None:
    """HudOverlay shows PRE-HAND as a simple stable-wait status."""
    _ = qapp
    from gui.hud_overlay import HudOverlay

    hud = HudOverlay()
    hud.show_pre_hand()

    assert hud._status_label.text() == "安定待ち..."

    hud.close()


def test_main_window_update_game_state_shows_pre_hand(qapp: QApplication) -> None:
    """MainWindow displays PRE-HAND supplemental status."""
    _ = qapp
    from gui.main_window import MainWindow

    window = MainWindow()
    state = create_empty_game_state()
    state.hand_start_status = "PRE-HAND"

    window.update_game_state(state)

    assert window._phase_label.text() == "Phase: PRE-HAND"

    window.close()


def test_load_config_reads_yaml(project_root: Path) -> None:
    """load_config returns parsed YAML contents."""
    with tempfile.TemporaryDirectory(dir=project_root / "tests") as temp_dir:
        config_path = Path(temp_dir) / "config.yaml"
        config_path.write_text(
            yaml.safe_dump({"game": {"blind_bb": 100}}),
            encoding="utf-8",
        )

        config = main.load_config(str(config_path))

        assert config == {"game": {"blind_bb": 100}}


def test_setup_logging_creates_log_file(project_root: Path) -> None:
    """setup_logging configures file and console handlers."""
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(dir=project_root / "tests") as temp_dir:
        work_dir = Path(temp_dir)
        os.chdir(work_dir)

        try:
            main.setup_logging({"logging": {"level": "INFO", "max_bytes": 1024}})
            logging.getLogger(__name__).info("smoke")

            assert (work_dir / "logs" / "poker_assistant.log").exists()
        finally:
            os.chdir(original_cwd)
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                handler.close()
                root_logger.removeHandler(handler)


def test_load_stats_populates_main_window(qapp: QApplication) -> None:
    """_load_stats reads opponents rows and populates the Statistics tab."""
    _ = qapp
    from gui.main_window import MainWindow

    db_conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        (
            "mrkrebs",
            156,
            0.24,
            0.18,
            0.08,
            0.65,
            0.55,
            0.28,
            "TAG",
            "2026-04-28",
            "",
        )
    ]
    db_conn.execute.return_value = cursor
    hand_manager = MagicMock()
    hand_manager._db_conn = db_conn
    window = MainWindow()

    main._load_stats(hand_manager, window)

    assert window._stats_table.rowCount() == 1
    assert window._stats_table.item(0, 0).text() == "mrkrebs"
    assert window._stats_count_label.text() == "1 players"
