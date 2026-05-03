"""Phase 10b: ライブ映像テスト用スクリプト。

CoinPokerのPractice Gamesを30分間キャプチャし、
GameStateをリアルタイムで表示する。

使い方:
  1. CoinPokerでPractice Gamesテーブルに座る
  2. python scripts/live_test.py を実行
  3. 30分間プレイしながらコンソール出力を観察
  4. Ctrl+C で終了
"""

import json
import sys
import time
import logging
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from capture.card_capture import CardCapture
from core.game_loop import GameLoop
from core.hand_manager import HandManager

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    with open("config.yaml", "r", encoding="utf-8") as f:
        import yaml
        return yaml.safe_load(f)


def load_profile() -> dict:
    with open("profiles/coinpoker_6max.json", "r", encoding="utf-8") as f:
        return json.load(f)


def on_game_state(state) -> None:
    """GameState更新時のコールバック。コンソールに要約を表示する。"""
    cards = state.hero.cards or "---"
    board = state.board or []
    board_str = " ".join(board) if board else "---"
    pot = state.pot or 0
    stack = state.hero.stack or 0
    turn = ">>> MY TURN <<<" if state.hero.is_my_turn else ""
    pos = state.hero.position or "?"
    dealer = state.dealer_seat or "?"
    phase = state.phase

    actions_str = ""
    if state.actions_since_last_frame:
        actions_str = " | actions: " + ", ".join(
            f"s{a.seat}:{a.action}({a.amount})"
            for a in state.actions_since_last_frame
        )

    event_str = ""
    if state.game_event:
        event_str = f" | EVENT: {state.game_event}"

    print(
        f"F{state.frame_number:04d} "
        f"[{phase:10s}] "
        f"cards={cards} | "
        f"board={board_str} | "
        f"pot={pot:>6} | "
        f"stack={stack:>6} | "
        f"pos={pos:>3} | "
        f"D={dealer} "
        f"{turn}"
        f"{event_str}"
        f"{actions_str}"
    )


def main() -> None:
    config = load_config()
    profile = load_profile()

    # キャプチャカード初期化
    device_index = config.get("capture", {}).get("device_index", 0)
    logger.info("Opening capture card (device %d)...", device_index)

    capture = CardCapture(
        device_index=device_index,
        width=1920,
        height=1080,
        fps=60,
    )

    if not capture.is_open():
        logger.error("Failed to open capture card!")
        logger.info("Try changing device_index in config.yaml (0, 1, 2...)")
        return

    logger.info("Capture card opened successfully")

    hand_manager = HandManager(config)

    game_loop = GameLoop(
        capture=capture,
        config=config,
        profile=profile,
        hand_manager=hand_manager,
        on_game_state=on_game_state,
    )

    logger.info("=" * 60)
    logger.info("LIVE TEST STARTED")
    logger.info("Play poker on CoinPoker Practice Games table")
    logger.info("Press Ctrl+C to stop")
    logger.info("=" * 60)

    start_time = time.time()
    try:
        game_loop.start()
    except KeyboardInterrupt:
        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info("LIVE TEST STOPPED (%.1f minutes)", elapsed / 60)
        logger.info("Final phase: %s", hand_manager.phase)
        logger.info("Hands played: %s", hand_manager.hand_id)
        logger.info("=" * 60)
    finally:
        game_loop.stop()
        capture.release()
        hand_manager.close()


if __name__ == "__main__":
    main()
