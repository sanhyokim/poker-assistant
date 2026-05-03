"""ディーラーボタン座標測定用キャプチャスクリプト。

使い方:
  python scripts/capture_dealer_btn.py

操作:
  Enter  = 現在のフレームを1枚保存
  a      = 自動連続キャプチャ（0.5秒間隔×20枚）
  q      = 終了

保存先: scripts/dealer_btn_captures/

撮影後:
  1. 画像をペイント等で開く
  2. ディーラーボタン（赤い四角に白D）の左上角座標(x,y)とサイズ(w,h)を読み取る
  3. profiles/coinpoker_6max.json の dealer_btn_5 / dealer_btn_6 を更新
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from typing import Any

import cv2
import numpy as np


SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dealer_btn_captures")
PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def load_profile() -> dict[str, Any]:
    """座標プロファイルを読み込み、既存のdealer_btn座標を表示する。"""
    profile_path = os.path.join(PROJECT_ROOT, "profiles", "coinpoker_6max.json")
    try:
        with open(profile_path, "r", encoding="utf-8") as file:
            profile = json.load(file)
        print("=== 既存ディーラーボタン座標 ===")
        for key in sorted(profile.keys()):
            if key.startswith("dealer_btn"):
                value = profile[key]
                if isinstance(value, dict) and "x" in value:
                    print(
                        f"  {key}: x={value['x']}, y={value['y']}, "
                        f"w={value['w']}, h={value['h']}",
                    )
                else:
                    print(f"  {key}: {value} (未測定)")
        print()
        return profile
    except Exception as error:
        print(f"プロファイル読み込みスキップ: {error}")
        return {}


def try_detect_dealer(frame: np.ndarray, profile: dict[str, Any]) -> None:
    """既存座標で全座席のディーラーボタンスコアを表示する。"""
    try:
        sys.path.insert(0, PROJECT_ROOT)

        import yaml

        from recognition.dealer_recognizer import DealerRecognizer

        config_path = os.path.join(PROJECT_ROOT, "config.yaml")
        with open(config_path, "r", encoding="utf-8") as file:
            config = yaml.safe_load(file)

        recognizer = DealerRecognizer(profile, config)
        seat = recognizer.detect_dealer_seat(frame)
        print(f"  検出結果: ディーラー座席 = {seat}")

        for seat_num in range(1, 7):
            key = f"dealer_btn_{seat_num}"
            region = profile.get(key)
            if not isinstance(region, dict) or "x" not in region:
                print(f"    座席{seat_num}: 座標未定義")
                continue

            x, y, width, height = (
                region["x"],
                region["y"],
                region["w"],
                region["h"],
            )
            crop = frame[y : y + height, x : x + width]
            if crop.size == 0:
                print(f"    座席{seat_num}: クロップ空")
                continue

            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            h_ch, s_ch, v_ch = cv2.split(hsv)
            red_mask = ((h_ch < 15) | (h_ch > 160)) & (s_ch > 80) & (v_ch > 80)
            white_mask = (s_ch < 40) & (v_ch > 200)
            total = crop.shape[0] * crop.shape[1]
            red_ratio = float(np.sum(red_mask)) / total
            white_ratio = float(np.sum(white_mask)) / total
            score = red_ratio * 0.7 + white_ratio * 0.3
            marker = " <<< DETECTED" if score > 0.05 else ""
            print(
                f"    座席{seat_num}: score={score:.4f} "
                f"(red={red_ratio:.3f}, white={white_ratio:.3f}){marker}",
            )
    except Exception as error:
        print(f"  検出テストスキップ: {error}")


def save_frame(frame: np.ndarray, count: int) -> str:
    """フレームを保存してファイル名を返す。"""
    timestamp = datetime.now().strftime("%H%M%S_%f")[:-3]
    filename = f"dealer_btn_{count:03d}_{timestamp}.png"
    filepath = os.path.join(SAVE_DIR, filename)
    cv2.imwrite(filepath, frame)
    return filename


def main() -> None:
    """キャプチャデバイスからフレームを保存する対話ループを実行する。"""
    os.makedirs(SAVE_DIR, exist_ok=True)

    print("キャプチャデバイスを開いています...")
    cap = cv2.VideoCapture(0, cv2.CAP_MSMF)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    cap.set(cv2.CAP_PROP_FPS, 60)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

    if not cap.isOpened():
        print("ERROR: キャプチャデバイスを開けません")
        sys.exit(1)

    for _ in range(10):
        cap.read()

    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"キャプチャ解像度: {actual_width}x{actual_height}")
    print(f"保存先: {SAVE_DIR}")
    print()

    profile = load_profile()

    print("=== 操作方法 ===")
    print("  Enter = 1枚撮影＋ディーラー検出テスト")
    print("  a     = 自動20枚撮影（0.5秒間隔）")
    print("  q     = 終了")
    print()
    print("座席5(左上)または座席6(左下)にディーラーボタンがある")
    print("タイミングでEnterまたはaを押してください。")
    print()

    count = 0
    while True:
        try:
            user_input = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if user_input == "q":
            break
        if user_input == "a":
            print("自動キャプチャ開始（0.5秒×20枚）...")
            for _ in range(20):
                ret, frame = cap.read()
                if ret:
                    count += 1
                    filename = save_frame(frame, count)
                    print(f"  [{count}] {filename}")
                time.sleep(0.5)
            print("自動キャプチャ完了。")
            continue

        ret, frame = cap.read()
        if not ret:
            print("ERROR: フレーム取得失敗")
            continue
        count += 1
        filename = save_frame(frame, count)
        print(f"  保存: {filename} ({frame.shape[1]}x{frame.shape[0]})")
        if profile:
            try_detect_dealer(frame, profile)

    cap.release()
    print(f"\n合計 {count} 枚を {SAVE_DIR} に保存しました。")
    print()
    print("=== 次の手順 ===")
    print("1. 保存した画像をペイントで開く")
    print("2. ディーラーボタン(赤い四角に白D)の左上角(x,y)とサイズ(w,h)を読み取る")
    print("3. profiles/coinpoker_6max.json を更新:")
    print('   "dealer_btn_5": {"x": ???, "y": ???, "w": 30, "h": 30}')
    print('   "dealer_btn_6": {"x": ???, "y": ???, "w": 30, "h": 30}')


if __name__ == "__main__":
    main()
