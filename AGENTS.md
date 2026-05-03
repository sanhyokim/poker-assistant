# AGENTS.md — Builder向け開発ルール

**プロジェクト:** ポーカーAIアシスタントシステム
**最終更新:** 2026-04-27
**参照ドキュメント:** SPEC.md v1.4, IMPLEMENTATION_PLAN.md v1.2

---

## 1. プロジェクト概要

CoinPokerの6人テーブル（NLH）において、キャプチャカード経由で画面認識し、GTO最適アクションを算出してHUDオーバーレイで推奨手を表示するシステム。Python 3.11.9 + PyTorch 2.11.0+cu130（EasyOCR GPU）+ postflop-solver（Rust CLI）+ PyQt6。

---

## 2. コーディング規約

### 2.1 スタイル

- **PEP 8** に準拠する。1行の最大文字数は 99 文字
- インデントは半角スペース4つ
- import の順序: 標準ライブラリ → サードパーティ → プロジェクト内モジュール。各グループ間は空行1行
- 未使用の import は残さない

### 2.2 型ヒント

- 全ての関数・メソッドに **型ヒントを必須** とする（引数と戻り値の両方）
- `Optional[X]` は `X | None` と書く（Python 3.11対応）
- 複雑な型は `TypeAlias` または `TypedDict` で定義する
- 型チェックの基準: mypyで `--strict` は不要だが、明らかな型エラーがないこと

```python
# OK
def detect_suit(card_img: np.ndarray) -> str:
    ...

# OK
def extract_number(crop: np.ndarray, is_pot: bool = False) -> int | None:
    ...

# NG（型ヒントなし）
def detect_suit(card_img):
    ...
```

### 2.3 docstring

- **Google スタイル** を使用する
- 全ての public 関数・クラスに docstring を記述する
- private 関数（`_` プレフィックス）はロジックが複雑な場合のみ docstring を記述

```python
def detect_my_turn(img: np.ndarray, profile: dict) -> bool:
    """btn_fold領域のHSV色でヒーローのターンかどうかを判定する。

    Args:
        img: BGR形式の1920x1080フレーム画像。
        profile: 座標プロファイル辞書。"btn_fold" キーを含む。

    Returns:
        True: ヒーローのターン（フォールドボタンが赤色）。
        False: ヒーローのターンではない。
    """
```

### 2.4 命名規則

| 対象 | 規則 | 例 |
|------|------|-----|
| ファイル名 | snake_case | `card_recognizer.py` |
| クラス名 | PascalCase | `CardRecognizer` |
| 関数・メソッド | snake_case | `detect_suit()` |
| 定数 | UPPER_SNAKE_CASE | `HSV_HEART_H_MAX = 10` |
| プライベート | `_` プレフィックス | `_preprocess_image()` |
| テストファイル | `test_` プレフィックス | `test_card_recognizer.py` |
| テスト関数 | `test_` プレフィックス | `test_detect_suit_heart()` |

### 2.5 エラーハンドリング

- 認識系モジュール（recognition/）は例外を投げず、失敗時は `None` または `"unknown"` を返す
- 外部通信（ソルバーCLI、LLM API）は適切なtry-exceptで囲み、タイムアウトを設定する
- 全ての例外は `logging` モジュールでログに記録する。`print()` は使わない
- フォールバック動作はSPEC.md セクション10のエラーハンドリング表に従う

### 2.6 ログ

- `logging` モジュールを使用する。`print()` はデバッグ目的でも使わない
- ログレベルの使い分け:
  - `DEBUG`: OCR中間結果、HSV値、差分スコア等の詳細情報
  - `INFO`: フェーズ遷移、ハンド開始/終了、推奨アクション出力
  - `WARNING`: フォールバック発動、OCR信頼度低、ディーラーボタン未検出
  - `ERROR`: キャプチャ喪失、ソルバープロセス死亡、DB書き込み失敗
- ロガー名はモジュール名を使用: `logger = logging.getLogger(__name__)`

---

## 3. テスト要件

### 3.1 テストフレームワーク

- **pytest** を使用する
- GUI テストには **pytest-qt** を使用する
- テストファイルは `tests/` ディレクトリに配置し、対応モジュール名に `test_` プレフィックスを付ける

### 3.2 テスト要件

- 各Phaseの受け入れ基準をテストコードで実装する
- 新規関数を追加した場合、最低1つのテストケースを書く
- 認識系モジュールのテストは `tests/fixtures/screenshots/coinpoker/` のテスト画像と `tests/fixtures/ground_truth/coinpoker.json` の正解データを使用する
- アクション推定のテストは `tests/fixtures/action_sequences/` のGameStateシーケンスを使用する
- 外部依存（LLM API、ソルバーCLI）のテストはモック/スタブを使用する

### 3.3 テストの実行

```bash
# 全テスト実行
pytest

# 特定モジュールのテスト
pytest tests/test_card_recognizer.py

# 詳細出力
pytest -v

# カバレッジ（参考）
pytest --cov=recognition --cov=core
```

### 3.4 テスト画像の扱い

- `tests/fixtures/screenshots/` 内の画像ファイルはテスト専用。変更・削除しない
- 新しいテスト画像を追加する場合は `tests/fixtures/screenshots/coinpoker/` に配置し、`tests/fixtures/ground_truth/coinpoker.json` に正解データを追記する

---

## 4. PR（プルリクエスト）の粒度

### 4.1 基本方針

- 1つのPhaseを1〜複数のPRに分割する
- 1つのPRは **200〜500行以内** を推奨（テストコード含む）
- 1つのPRは単一の責務（1つのモジュール、1つの機能）に限定する

### 4.2 PR分割の目安

| Phase規模 | PR分割 | 例 |
|----------|--------|-----|
| 推定タスク数 1〜2 | 1 PR | Phase 6（差分検知） |
| 推定タスク数 2〜3 | 1〜2 PR | Phase 3（カード認識: 本体 + テスト） |
| 推定タスク数 3〜4 | 2〜3 PR | Phase 9（ハンドマネージャー: 遷移ロジック + アクション蓄積 + DB/リプレイ保存） |
| 推定タスク数 4〜5 | 3〜4 PR | Phase 20（メインウィンドウ: タブごとに分割） |

### 4.3 PRのセルフチェック

PRを提出する前に以下を確認する:

1. `pytest` が全テストPASS
2. 型ヒントが全関数に付与されている
3. docstringが全public関数に記述されている
4. `print()` が使われていない（`logging` を使用）
5. 未使用の import がない
6. SPEC.md の該当セクションと整合している

---

## 5. コミットメッセージ規約

**Conventional Commits** 形式を使用する:

```
<type>(<scope>): <description>

[optional body]
```

### type の種類

| type | 用途 |
|------|------|
| feat | 新機能追加 |
| fix | バグ修正 |
| test | テスト追加・修正 |
| refactor | リファクタリング（機能変更なし） |
| docs | ドキュメント変更 |
| chore | ビルド・設定変更 |

### scope の例

`capture`, `recognition`, `core`, `strategy`, `solver`, `gui`, `config`, `tests`

### コミットメッセージの例

```
feat(recognition): カード認識モジュールを実装

- 4色HSVスート判定（SPEC.md 4.3.1準拠）
- EasyOCR GPUランク認識（SPEC.md 4.3.2準拠）
- ヒーローカードマージン3px（SPEC.md 4.3.3準拠）
- 可視性判定とキャッシュ（SPEC.md 4.3.4準拠）
```

```
test(recognition): カード認識のユニットテストを追加

- cp_01〜cp_06の通常画面で25/25カード正解を確認
- cp_07/cp_08の特殊画面で可視性スキップを確認
```

---

## 6. プロジェクト固有のルール

### 6.1 座標プロファイル

- 座標プロファイルのキー形式は `{"x": int, "y": int, "w": int, "h": int}`
- `"width"` / `"height"` ではなく `"w"` / `"h"` を使用する
- crop_region関数では必ず `r["w"]`、`r["h"]` を参照する

### 6.2 EasyOCR Reader

- EasyOCR Readerは `recognition/__init__.py` でシングルトンとして管理する
- card_recognizer、number_recognizer、name_recognizer が同一インスタンスを共有する
- 複数インスタンスを作成しない（GPU VRAM節約）

### 6.3 config.yaml の参照

- 設定値は `config.yaml` から読み込む。ハードコーディングは HSV閾値等の固定パラメータのみ
- config.yaml で管理する値と固定値の区分は SPEC.md セクション21の一覧表に従う

### 6.4 GameState

- GameState の型定義は `core/game_state.py` に集約する
- 他モジュールは GameState を import して使用する
- GameState のフィールド変更は Phase 7 の範囲内でのみ行い、下流Phaseには波及させない
- `is_seated`（着席中）と `in_current_hand`（ハンド参加中）は別概念。SPEC.md セクション19参照

### 6.5 ファイル/ディレクトリ操作

- `data/`、`hand_replays/`、`logs/` ディレクトリはプログラムが自動作成する（存在しない場合）
- `profiles/` のJSONファイルは読み取り専用として扱う（プログラムから上書きしない）
- テストは `tests/fixtures/` 内のファイルを変更しない

---

## 7. 禁止事項

- `tests/fixtures/` 内のテスト画像・正解データの無断変更
- SPEC.md / IMPLEMENTATION_PLAN.md の無断変更（変更が必要な場合はCommanderに報告）
- `solver/bin/postflop_cli.exe` の変更（ローカルビルドのみ）
- `print()` によるデバッグ出力の残存（`logging` を使用）
- グローバル変数の使用（モジュールレベルの定数は許可）
- `import *` の使用
- テストなしの機能追加

---

## 8. 質問・エスカレーション

以下の場合はCommander（司令塔AI）に報告する:

- SPEC.md の記述が曖昧で実装判断できない場合
- 受け入れ基準を満たせない技術的問題が発生した場合
- 既存モジュールのインターフェース変更が必要な場合
- テスト画像の追加や正解データの修正が必要な場合
- config.yaml のパラメータ追加が必要な場合
- Phase間の依存関係で想定外の問題が発生した場合

報告形式:
```
⚠️ エスカレーション
- Phase: X
- Task: Y
- 問題: （具体的な問題の説明）
- 影響範囲: （他のPhase/Taskへの影響）
- 提案: （可能であれば解決案）
```

---

## 9. 環境情報

| 項目 | 値 |
|------|-----|
| Python | 3.11.9 |
| OS | Windows 10/11 |
| GPU | NVIDIA RTX 3080 (VRAM 10GB) |
| PyTorch | 2.11.0+cu130 |
| CUDA | 13.0 |
| EasyOCR | GPU モード |
| postflop-solver CLI | solver/bin/postflop_cli.exe |
| DB | SQLite3（標準ライブラリ） |

---

## 10. 参照ドキュメント

| ドキュメント | 用途 |
|------------|------|
| SPEC.md | 実装仕様の全詳細。パラメータ、閾値、アルゴリズム、データ構造 |
| IMPLEMENTATION_PLAN.md | Phase分割、依存関係、受け入れ基準、推定タスク数 |
| config.yaml | 全設定項目のデフォルト値 |
| profiles/coinpoker_6max.json | 座標プロファイル（PoC検証済み） |
| tests/fixtures/ground_truth/coinpoker.json | テスト正解データ |

実装中に判断に迷った場合は、まず SPEC.md の該当セクションを参照する。SPEC.md に記載がない場合はCommanderにエスカレーションする。
```

---
