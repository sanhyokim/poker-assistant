"""Poker Assistant application entry point.

Run with: python main.py

SPEC.md section 23 startup/shutdown sequence.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication


logger = logging.getLogger(__name__)


def load_config(path: str = "config.yaml") -> dict[str, Any]:
    """Load and return the YAML configuration file.

    Args:
        path: Path to config.yaml.

    Returns:
        Parsed configuration dictionary.
    """
    config_path = Path(path)
    if not config_path.exists():
        print(f"WARNING: {path} not found, using defaults")
        return {}
    with open(config_path, "r", encoding="utf-8") as file_obj:
        return yaml.safe_load(file_obj) or {}


def setup_logging(config: dict[str, Any]) -> None:
    """Initialize rotating file handler and console logging.

    Args:
        config: Parsed configuration dictionary.
    """
    log_cfg = config.get("logging", {})
    log_level = str(log_cfg.get("level", "INFO"))
    max_bytes = int(log_cfg.get("max_bytes", 50 * 1024 * 1024))
    backup_count = int(log_cfg.get("backup_count", 5))

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "poker_assistant.log"

    formatter = logging.Formatter(
        "[%(asctime)s.%(msecs)03d] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    for handler in root_logger.handlers[:]:
        handler.close()
        root_logger.removeHandler(handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def load_profile(config: dict[str, Any]) -> dict[str, Any]:
    """Load the coordinate profile JSON.

    Args:
        config: Parsed configuration dictionary.

    Returns:
        Coordinate profile dictionary.
    """
    profile_path = config.get("profile", {}).get("path", "profiles/coinpoker_6max.json")
    path = Path(profile_path)
    if not path.exists():
        logger.warning("Profile %s not found, using empty", path)
        return {}
    with open(path, "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _load_stats(hand_manager: Any, main_window: Any) -> None:
    """Load opponent statistics from DB into the statistics tab.

    Args:
        hand_manager: HandManager instance with DB access.
        main_window: MainWindow instance.
    """
    try:
        db_conn = getattr(hand_manager, "_db_conn", None)
        if db_conn is None:
            return
        cursor = db_conn.execute(
            "SELECT player_name, total_hands, vpip, pfr, three_bet_pct, "
            "cbet_flop_pct, fold_to_three_bet, went_to_showdown, "
            "long_term_style, last_seen, freshness_note "
            "FROM opponents ORDER BY total_hands DESC"
        )
        columns = [
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
        opponents = [dict(zip(columns, row)) for row in cursor.fetchall()]
        main_window.load_opponents(opponents)
    except Exception:
        logger.warning("Failed to load opponent stats", exc_info=True)


class GameLoopWorker(QObject):
    """Worker that runs the game loop in a background thread."""

    game_state_ready = pyqtSignal(object)
    phase_changed = pyqtSignal(str)
    hand_saved = pyqtSignal(int)
    log_message = pyqtSignal(str, str)
    stopped = pyqtSignal()

    def __init__(self, game_loop: Any) -> None:
        super().__init__()
        self._game_loop = game_loop
        self._running = False
        self._last_emitted_saved_hand_id: int | None = None

    def run(self) -> None:
        """Execute the polling loop."""
        import time

        self._running = True
        logger.info("Game loop thread started")
        polling_interval = float(getattr(self._game_loop, "_polling_interval", 0.5))

        while self._running:
            loop_start = time.perf_counter()
            try:
                game_state = self._game_loop.process_one_frame()
                if game_state is not None:
                    self._game_loop._hand_manager.process_frame(game_state)
                    saved_hand_id = self._game_loop._hand_manager.last_saved_hand_id
                    if (
                        saved_hand_id is not None
                        and saved_hand_id != self._last_emitted_saved_hand_id
                    ):
                        self.hand_saved.emit(saved_hand_id)
                        self._last_emitted_saved_hand_id = saved_hand_id
                    self._game_loop._handle_strategy(game_state)
                    self.game_state_ready.emit(game_state)
                    if game_state.phase:
                        self.phase_changed.emit(game_state.phase)
            except Exception:
                logger.exception("Error in game loop thread")
                self.log_message.emit("Game loop error (see log)", "ERROR")

            if self._game_loop.capture_failed:
                self.log_message.emit("Capture lost, stopping", "ERROR")
                break

            elapsed = time.perf_counter() - loop_start
            sleep_time = max(0.0, polling_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        self.stopped.emit()
        logger.info("Game loop thread stopped")

    def request_stop(self) -> None:
        """Request the polling loop to stop."""
        self._running = False


def main() -> None:
    """Run the Poker Assistant application."""
    config = load_config()
    load_dotenv(override=True)
    setup_logging(config)
    logger.info("Poker Assistant starting")

    profile = load_profile(config)

    app = QApplication(sys.argv)

    from capture import create_capture
    from core.game_loop import GameLoop
    from core.hand_manager import HandManager
    from gui.hud_overlay import HudOverlay
    from gui.main_window import MainWindow

    db_path = str(config.get("db", {}).get("path", "data/poker_assistant.db"))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    hand_manager = HandManager(config, db_path=db_path)

    main_window = MainWindow(config=config)
    hud = HudOverlay(config=config.get("hud", {}))

    capture = create_capture(config)
    game_loop = GameLoop(
        capture=capture,
        config=config,
        profile=profile,
        hand_manager=hand_manager,
        hud_callback=hud.update_recommendation,
        hud_computing_callback=hud.show_computing,
        enable_strategy=True,
    )

    worker: GameLoopWorker | None = None
    thread: QThread | None = None

    def on_start() -> None:
        """Start the background game loop worker."""
        nonlocal worker, thread
        if thread is not None and thread.isRunning():
            return

        worker = GameLoopWorker(game_loop)
        thread = QThread()
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.game_state_ready.connect(main_window.update_game_state)
        worker.phase_changed.connect(main_window.update_phase)
        worker.hand_saved.connect(main_window.refresh_statistics)
        worker.log_message.connect(main_window.append_log)
        worker.stopped.connect(thread.quit)
        worker.stopped.connect(main_window.mark_stopped)
        thread.finished.connect(worker.deleteLater)

        hud.show()
        hud.show_waiting()
        thread.start()
        logger.info("Game loop thread launched")

    def on_stop() -> None:
        """Stop the background game loop worker and release resources."""
        nonlocal worker, thread
        if worker is not None:
            worker.request_stop()
        if thread is not None:
            thread.quit()
            thread.wait(5000)
            thread = None
            worker = None
        game_loop.stop()
        main_window.mark_stopped()
        hud.hide()
        logger.info("Game loop stopped by user")

    def on_reload() -> None:
        """Reload config/profile files for future use."""
        nonlocal config, profile
        config = load_config()
        profile = load_profile(config)
        logger.info("Config and profile reloaded")
        main_window.append_log("Config reloaded", "INFO")
        _load_stats(hand_manager, main_window)

    main_window.start_requested.connect(on_start)
    main_window.stop_requested.connect(on_stop)
    main_window.reload_requested.connect(on_reload)
    main_window.rejoin_seat_requested.connect(
        lambda seat: game_loop.request_rejoin_seat(seat)
    )

    _load_stats(hand_manager, main_window)

    main_window.show()
    logger.info("Poker Assistant ready")

    exit_code = app.exec()

    logger.info("Shutting down")
    on_stop()
    hand_manager.close()
    logger.info("Poker Assistant shutdown complete")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
