"""画像上のクリック位置の座標を表示するツール。

使い方:
  python scripts/measure_coordinates.py <画像パス>

操作:
  左クリック: クリック位置の(x, y)をコンソールに表示
  ドラッグ: 矩形選択し、離した時に(x, y, w, h)を表示
  r: 矩形描画をリセット
  q / ESC: 終了

例:
  python scripts/measure_coordinates.py scripts/dealer_btn_captures/dealer_btn_001.png
"""

from __future__ import annotations

import os
import sys

import cv2


class CoordinateMeasurer:
    """OpenCVウィンドウ上でクリック座標と矩形座標を測定する。"""

    def __init__(self, image_path: str) -> None:
        """画像を読み込み、測定状態を初期化する。"""
        self.image = cv2.imread(image_path)
        if self.image is None:
            print(f"ERROR: 画像を読み込めません: {image_path}")
            sys.exit(1)
        self.original = self.image.copy()
        self.drawing = False
        self.start_x = 0
        self.start_y = 0
        self.rectangles: list[tuple[int, int, int, int]] = []
        print(f"画像サイズ: {self.image.shape[1]}x{self.image.shape[0]}")
        print()
        print("=== 操作方法 ===")
        print("  左クリック: 座標(x, y)を表示")
        print("  ドラッグ:   矩形選択 → 離すと(x, y, w, h)を表示")
        print("  r:          矩形描画リセット")
        print("  q / ESC:    終了")
        print()

    def mouse_callback(
        self,
        event: int,
        x: int,
        y: int,
        flags: int,
        param: object,
    ) -> None:
        """マウス操作に応じてクリック座標または矩形座標を表示する。"""
        _ = flags, param
        if event == cv2.EVENT_LBUTTONDOWN:
            self.drawing = True
            self.start_x = x
            self.start_y = y
            print(f"  クリック: ({x}, {y})")

        elif event == cv2.EVENT_MOUSEMOVE:
            if self.drawing:
                temp = self.image.copy()
                cv2.rectangle(
                    temp,
                    (self.start_x, self.start_y),
                    (x, y),
                    (0, 255, 0),
                    1,
                )
                cv2.imshow("Measure", temp)

        elif event == cv2.EVENT_LBUTTONUP:
            self.drawing = False
            end_x, end_y = x, y
            rect_x = min(self.start_x, end_x)
            rect_y = min(self.start_y, end_y)
            rect_w = abs(end_x - self.start_x)
            rect_h = abs(end_y - self.start_y)
            if rect_w > 3 and rect_h > 3:
                print(f"  矩形: x={rect_x}, y={rect_y}, w={rect_w}, h={rect_h}")
                print(
                    f'  JSON:  {{"x": {rect_x}, "y": {rect_y}, '
                    f'"w": {rect_w}, "h": {rect_h}}}',
                )
                cv2.rectangle(
                    self.image,
                    (rect_x, rect_y),
                    (rect_x + rect_w, rect_y + rect_h),
                    (0, 255, 0),
                    2,
                )
                label = f"({rect_x},{rect_y}) {rect_w}x{rect_h}"
                cv2.putText(
                    self.image,
                    label,
                    (rect_x, rect_y - 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    1,
                )
                cv2.imshow("Measure", self.image)
                self.rectangles.append((rect_x, rect_y, rect_w, rect_h))

    def run(self) -> None:
        """測定ウィンドウを開き、キー入力があるまで待機する。"""
        cv2.namedWindow("Measure", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Measure", 1280, 720)
        cv2.setMouseCallback("Measure", self.mouse_callback)
        cv2.imshow("Measure", self.image)

        while True:
            key = cv2.waitKey(0) & 0xFF
            if key == ord("q") or key == 27:
                break
            if key == ord("r"):
                self.image = self.original.copy()
                self.rectangles.clear()
                cv2.imshow("Measure", self.image)
                print("  リセット")

        if self.rectangles:
            print()
            print("=== 測定結果まとめ ===")
            for index, (rect_x, rect_y, rect_w, rect_h) in enumerate(
                self.rectangles,
                1,
            ):
                print(
                    f'  矩形{index}: {{"x": {rect_x}, "y": {rect_y}, '
                    f'"w": {rect_w}, "h": {rect_h}}}',
                )

        cv2.destroyAllWindows()


def _latest_capture_path() -> str | None:
    """dealer_btn_captures内の最新PNGパスを返す。"""
    capture_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "dealer_btn_captures",
    )
    if not os.path.isdir(capture_dir):
        return None

    files = sorted(
        [filename for filename in os.listdir(capture_dir) if filename.endswith(".png")],
        reverse=True,
    )
    if not files:
        return None

    print(f"最新画像を使用: {files[0]}")
    return os.path.join(capture_dir, files[0])


def main() -> None:
    """画像パスを解決し、座標測定ツールを起動する。"""
    if len(sys.argv) < 2:
        image_path = _latest_capture_path()
        if image_path is None:
            print("ERROR: dealer_btn_captures/ に画像がありません")
            print("先に capture_dealer_btn.py を実行してください")
            print("使い方: python scripts/measure_coordinates.py <画像パス>")
            sys.exit(1)
    else:
        image_path = sys.argv[1]

    measurer = CoordinateMeasurer(image_path)
    measurer.run()


if __name__ == "__main__":
    main()
