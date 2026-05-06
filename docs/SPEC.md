# ポーカーAIアシスタントシステム — 実装仕様書（SPEC.md）

**バージョン:** 1.4
**作成日:** 2026-05-02

## 1. システム概要

### 1.1 目的

CoinPokerの6人テーブル（ノーリミットテキサスホールデム）において、キャプチャカード経由でリアルタイムに画面を認識し、GTO（ゲーム理論最適）に基づく最適アクションを算出し、HUDオーバーレイで推奨手を表示するシステム。最終操作は人間が行う（自動操作ではない）。

### 1.2 利用条件

本システムは検証・学習用途として開発する。

### 1.3 対象プラットフォーム

CoinPokerデスクトップクライアント。2026年3月2日リニューアル版（PokerBaaziベース）。UI言語は日本語。4色デッキ設定を使用（♠黒/♥赤/♦青/♣緑）。NLHキャッシュは6maxのみ提供（9maxは存在しない）。

### 1.4 用語集

| 用語 | 定義 |
|------|------|
| NLH | ノーリミットテキサスホールデム |
| GTO | Game Theory Optimal（ゲーム理論最適戦略） |
| CFR | Counterfactual Regret Minimization（反実仮想リグレット最小化） |
| BTN | ボタン（ディーラーポジション）。最後にアクションするため最も有利 |
| SB | スモールブラインド。BTNの左隣。強制ベット（BBの半額） |
| BB | ビッグブラインド。SBの左隣。強制ベット（テーブル最低額） |
| UTG | Under The Gun。BBの左隣。プリフロップで最初にアクション |
| MP | ミドルポジション。UTGの左隣 |
| CO | カットオフ。BTNの右隣 |
| IP | In Position。相手より後にアクションする有利なポジション |
| OOP | Out Of Position。相手より先にアクションする不利なポジション |
| SPR | Stack-to-Pot Ratio（スタック/ポット比率） |
| RFI | Raise First In（最初にレイズしてポットに参加） |
| 3bet | 最初のレイズに対するリレイズ |
| 4bet | 3betに対するリレイズ |
| cbet | Continuation Bet（プリフロップのレイザーがフロップで続けてベット） |
| VPIP | Voluntarily Put Money In Pot（自発的にポットに参加した割合） |
| PFR | Pre-Flop Raise（プリフロップでレイズした割合） |
| RTA | Real-Time Assistance（リアルタイムアシスタンス） |
| HUD | Heads-Up Display（ヘッドアップディスプレイ） |
| MC | Monte-Carlo（モンテカルロシミュレーション） |
| effective_stack | 対戦する2人のうち少ない方のスタック。ソルバーの入力パラメータ |
| バンチング | フォールドしたプレイヤーのレンジがデッキに与える影響を考慮する手法 |
| ベースラインレンジ | ポジション×アクション別のデフォルトハンドレンジ。LLM失敗時のフォールバック |

---

## 2. 技術スタック

| 項目 | 選定内容 |
|------|----------|
| OS | Windows 10/11 |
| Python | 3.11.9 |
| PyTorch | 2.11.0+cu130（CUDA 13.0） |
| GPU | NVIDIA RTX 3080（VRAM 10GB） |
| GPUドライバー | 591.86（CUDA 13.1対応） |
| OCR | EasyOCR GPU モード |
| 画面キャプチャ（本番） | HDMIキャプチャカード（UGREEN 4K、USB3.0、1080p@60Hz） |
| 画面キャプチャ（開発） | Python mss / ファイル読み込み |
| ソルバー | postflop-solver（Rust、Discounted CFR、AGPL-v3） |
| エクイティ計算 | eval7 0.1.10（Monte-Carlo 10k回、5-20ms） |
| LLM | OpenRouter経由（モデルは.envで切替可能） |
| GUI | PyQt6（メインウィンドウ + HUDオーバーレイ） |
| DB | SQLite |
| デッキ表示 | 4色デッキ（♠黒/♥赤/♦青/♣緑） |
| 設定管理 | config.yaml + .env（APIキー等の秘匿情報） |
| 開発体制 | 司令塔AI（Claude Opus 4.6）+ 実装役AI（Codex） |
| 戦略判断 | recommendation_engine.py（プリフロップ/HU/マルチウェイの統合ルーター） |


---

## 3. アーキテクチャ

### 3.1 全体フロー

```
キャプチャカード入力（OpenCV, 1080p@60fps）
         ↓
差分検知（前フレームと比較、変化なし→スキップ）
         ↓
座標プロファイルに基づき各領域クロップ
         ↓
┌─────────────┬─────────────┬──────────────┐
│カード認識     │数値認識       │UI認識          │
│4色HSV+EasyOCR│EasyOCR(GPU)  │HSV色検出       │
└──────┬──────┴──────┬──────┴───────┬──────┘
       └─────────────┴─────────────┘
                     ↓
       構造化データ（GameState JSON）
                     ↓
              局面判定分岐
    ┌─────────┼──────────────┐
    ↓         ↓              ↓
プリフロップ  ポストフロップ    ポストフロップ
GTOチャート   ヘッズアップ     マルチウェイ
＋LLM微調整  solver＋LLM     LLM＋eval7
             信頼度:高        信頼度:中
    └─────────┴──────────────┘
                     ↓
     HUDオーバーレイ表示（ウィンドウ外側）
                     ↓
           人間が判断して操作
                     ↓
     ハンド終了検知 → DB保存 + リプレイJSON保存
```

### 3.2 キャプチャ構成

```
PC (CoinPokerを実行)
  │ HDMI出力
  ↓
[UGREEN キャプチャーボード]
  │            │
  │ HDMI OUT   │ USB-C/A
  │(パススルー) │(キャプチャ映像 1080p@60Hz)
  ↓            ↓
モニター      同じPC（USB3.0ポート）
(プレイ用)    → Python(OpenCV)で読み取り
```

キャプチャ初期化コード:
```python
cap = cv2.VideoCapture(0, cv2.CAP_MSMF)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
cap.set(cv2.CAP_PROP_FPS, 60)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
```

実測性能: 平均16.7ms/フレーム、P95=18.6ms、P99=19.3ms、フレームドロップ0/100。

config.yamlでキャプチャ方式を切替可能にする（capture_card / mss / file）。OCR・ソルバー・LLM・HUDのコードはキャプチャ方式に依存しない設計とする。

### 3.3 ポーリングループ

0.5〜1秒間隔でキャプチャし、前回状態と比較して変化を検知する。

```
[待機] ホールカード出現を監視
  ↓ ホールカード検知
[プリフロップ] ボード0枚 → チャート＋LLM
  ↓ ボード3枚出現
[フロップ] → 残り人数で分岐（ソルバー or LLM）
  ↓ ボード4枚目
[ターン] → ソルバー再計算 or LLM
  ↓ ボード5枚目
[リバー] → ソルバー再計算 or LLM
  ↓ ショーダウン or フォールド
[ハンド終了] → 結果DB保存 + リプレイJSON → [待機]に戻る
```

### 3.4 時間制約

CoinPokerのタイムバンクは15秒（第1層アクションタイマー）。第2層タイムバンクは追加秒数の貯金（使用すると減少、徐々に回復）。推奨出力目標は7〜8秒以内。

パイプラインの時間内訳（2種ベットサイズ標準設定）:

| 処理 | フロップ | ターン |
|------|---------|--------|
| キャプチャ入力 | 0.017秒 | 0.017秒 |
| 差分検知 | 0.002秒 | 0.002秒 |
| 画面認識（OCR） | 0.086秒 | 0.086秒 |
| LLMレンジ推定 | 0.50〜1.50秒 | 0.50〜1.50秒 |
| ソルバー（CPU） | 3.72秒 | 0.03秒 |
| HUD表示 | 0.05秒 | 0.05秒 |
| **合計** | **4.37〜5.37秒** | **0.68〜1.68秒** |

プリフロップはソルバー不要（チャート参照のみ）で1〜2秒で完了。

---

## 4. 画面認識システム

### 4.1 座標プロファイル

全領域の矩形座標（x, y, w, h）を `profiles/coinpoker_6max.json` に格納する。CoinPokerウィンドウは最大化で固定サイズ（確認済み）のため、一度作成すれば再マッピング不要。再マッピングが必要になるのは、CoinPoker UIの変更またはディスプレイ解像度の変更が発生した場合のみ。

キー形式:
```json
{
  "hero_card_1": {"x": 859, "y": 755, "w": 41, "h": 81},
  "board_card_1": {"x": 365, "y": 225, "w": 55, "h": 70}
}
```

**重要:** キーは `"w"` / `"h"` を使用する（`"width"` / `"height"` ではない）。crop_region関数内では必ず `r["w"]`、`r["h"]` を参照すること。

登録対象領域: ヒーローカード2枚、ボードカード5スロット、ポットサイズ、ヒーロースタック、各プレイヤースタック（seat2〜6）、ヒーローベット、各プレイヤーベット（seat2〜6）、ディーラーボタン位置（全6座席分）、フォールド/コール・チェック/ライズ・ベットボタン領域、各プレイヤー名表示領域。

座標プロファイルの草案（`coinpoker_6max_draft.json`）は全PoCで検証済み。本実装では `profiles/coinpoker_6max.json` として配置する。

- PoC検証済みの座標プロファイルは `coinpoker_6max_draft.json` として引き継ぎ書セクション25で管理されており、これを `profiles/coinpoker_6max.json` として配置する
- `dealer_btn_5`, `dealer_btn_6` は未測定（`_skipped`）。座席5,6にディーラーボタンがある場合はフォールバック（前ハンドの座席を使用）で対応
- 引き継ぎ書にある追加領域キー（`action_badge_1〜6`, `seat_status_2〜3`, `timebank_icon`, `preaction_area`, `presets_bar`）はSPEC.mdの登録対象領域リストに含まれていない

**推奨対応：** SPEC.md セクション4.1の登録対象領域リストは現状のまま（Phase 3-10aで使う領域のみ）。追加領域は将来Phase用として引き継ぎ書を参照する旨の注記を追加。

### 4.2 認識モジュール一覧

| モジュール | 対象 | 方式 | PoC精度 |
|-----------|------|------|---------|
| card_recognizer | カードのスート＋ランク | 4色HSV判定＋EasyOCR GPU | 通常画面100% |
| number_recognizer | ポット、スタック、ベット額 | HSV色フィルタ＋EasyOCR GPU | 104/104=100% |
| button_recognizer | 自分ターン判定＋ボタン種別 | HSV色検出 | ターン検出8/8=100%、種別9/9=100% |
| dealer_recognizer | ディーラーボタン座席 | 赤＋白ピクセルスコアリング | 通常画面6/6=100% |
| name_recognizer | プレイヤー名 | EasyOCR GPU（多言語） | 未PoC（実装時テスト） |
| diff_detector | フレーム間差分 | ピクセル差分合計 | — |

### 4.3 カード認識

#### 4.3.1 スート判定（4色HSV）

CoinPokerの4色デッキ（♠黒/♥赤/♦青/♣緑）をHSV色空間で判定する。

確定閾値:

| スート | 色 | HSV条件 |
|--------|-----|---------|
| ♥ ハート | 赤 | (H < 10 or H > 170) かつ S > 80 |
| ♦ ダイヤ | 青 | H = 95〜140 かつ S > 70 |
| ♣ クラブ | 緑 | H = 35〜85 かつ S > 50 |
| ♠ スペード | 黒 | S < 50 かつ V < 150（＋暗ピクセルカウント補強） |

前処理: 白背景を除外（S > 30 または V < 200 のピクセルのみ使用）。座席背景の赤紫色（H=145-180, V<110）をフィルタリング。

#### 4.3.2 ランクOCR

```python
# ランク領域切り出し
rank_region = card_img[0:ch//2, 0:int(cw*2/3)]  # 上1/2 × 左2/3

# 前処理
gray = cv2.cvtColor(rank_region, cv2.COLOR_BGR2GRAY)
_, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

# 動的拡大率
scale = 5 if card_width < 50 else 3  # ヒーロー(~40px)→5倍、ボード(~93px)→3倍
enlarged = cv2.resize(binary, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

# OCR実行
results = reader.readtext(enlarged, allowlist='0123456789AJQKT',
                          text_threshold=0.3, low_text=0.2, min_size=5)
```

ランク正規化: "10"/"1O"/"IO"/"I0" → "T"、"0"→"T"、"O"→"Q"、"I"→"J" 等。

#### 4.3.3 ヒーローカードマージン

| カード | サイズ (w × h) | マージン |
|--------|---------------|---------|
| hero_card_1 | ~41 × 81 | 全辺3px |
| hero_card_2 | ~39 × 89 | 全辺3px |

margin ≥ 5px でランク文字（特にT）が「1」に劣化する。ボードカードにはマージン不要。


### 4.3.4 カード可視性判定とキャッシュ

**ヒーローカードの取得タイミング:** ヒーローカードはハンド開始時（ホールカード出現を検出した最初のフレーム）に認識し、ハンド終了までキャッシュする。CoinPokerではハンド開始時点でヒーローカードが即座に表示されるため、自分のターン到来を待つ必要はない。

**OCRスキップ条件:** 以下の場合はOCRを実行せず、キャッシュ値を保持する:
- 非アクティブ時の暗転表示（プリアクション表示中等）
- ショーダウン/オールイン画面（画面構成が根本的に異なる）
- カード可視性判定で dark / grayed / blank と分類された場合

**キャッシュの破棄:** hand_end遷移時にキャッシュをクリアし、次ハンドのホールカード認識に備える。


### 4.4 数値認識

#### 4.4.1 ポット表示（pot_display）前処理

```python
def preprocess_pot_color(crop):
    """HSV色フィルタで「ポット」ラベル（青緑）を除去し、数字（黄/白）のみ残す"""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    yellow_mask = (h >= 15) & (h <= 40) & (s > 60) & (v > 120)
    white_mask = (s < 80) & (v > 180)
    keep_mask = yellow_mask | white_mask
    result = crop.copy()
    result[~keep_mask] = 0
    return result
```

#### 4.4.2 共通OCRパラメータ

| パラメータ | 値 |
|-----------|-----|
| allowlist | `0123456789.,$ USDTCHP` |
| 二値化 | OTSU自動閾値 |
| 拡大率 | 2倍 |
| 空領域判定 | グレースケールのstd < 8 |

スタック/ベット値のクリーニング: OCR信頼度 ≥ 0.4 のトークンのみ使用。カンマ/スペース除去後、3桁区切りで左→右に結合。1-2桁の孤立トークンはノイズとしてスキップ。

### 4.5 ボタン検出

#### 4.5.1 自分ターン判定

btn_fold領域のHSV平均値で判定する:

```python
is_my_turn = (mean_h > 155 or mean_h < 10) and mean_s > 150 and mean_v > 140
```

赤色（フォールドボタン表示）= 自分のターン。暗緑（プリアクション）or 黒（特殊画面）= 自分のターンではない。

#### 4.5.2 ボタン種別分類（自分ターン時のみ）

| ボタン | 色 | HSV条件 | 文脈判定 |
|--------|-----|---------|---------|
| fold | 赤 | H>155 or H<10, S>150, V>140 | 常にfold |
| call/check | 緑 | 35≤H≤90, S>150, V>100 | アクティブベットあり→call、なし→check |
| raise/bet | オレンジ | 10≤H≤35, S>150, V>150 | アクティブベットあり→raise、なし→bet |

アクティブベット判定: hero_bet、player_bet_2〜6の領域をチェックし、グレースケールのstd > 25 かつ mean > 40 ならベットあり。

### 4.6 ディーラーボタン検出

各座席のdealer_btn領域で赤＋白ピクセルを検出する:

```python
red_mask = ((h < 15) | (h > 160)) & (s > 80) & (v > 80)
white_mask = (s < 40) & (v > 200)
score = red_ratio * 0.7 + white_ratio * 0.3
# score > 0.05 の最高スコア座席を選択
```

### 4.7 アクション推定

前フレームと現フレームのGameState差分から、各プレイヤーのアクションおよびゲーム状態遷移を推定する。

#### 4.7.1 入力と出力

**入力:**
- `prev_state`: 前フレームのGameState全体（セクション19のJSON構造）。システム起動直後やNEW_HAND検出直後はNone
- `curr_state`: 現フレームのGameState全体

**出力:**
```json
{
  "game_event": "NEW_STREET",
  "actions": [
    {"seat": 2, "action": "CALL", "amount": 200, "confidence": "high"},
    {"seat": 3, "action": "FOLD", "amount": 0, "confidence": "high"}
  ]
}
```

アクション型の定義:

| action | 意味 | amount | amount単位 |
|--------|------|--------|-----------|
| FOLD | フォールド | 0 | — |
| CHECK | チェック | 0 | — |
| CALL | コール | コール額 | チップ額 |
| BET | ベット | ベット額 | チップ額 |
| RAISE | レイズ | レイズ後のそのストリート内の合計ベット額（to-bet） | チップ額 |
| ALL_IN | オールイン | 投入額 | チップ額 |
| BLIND_SB | SBブラインド投入 | SB額 | チップ額 |
| BLIND_BB | BBブラインド投入 | BB額 | チップ額 |

**単位:** 全ての amount はチップ額で記録する（BB単位ではない）。ソルバー入力（セクション5.8）およびGameState内のpot/stack/bet値と単位を統一する。

**RAISEのamountセマンティクス:** to-bet方式（そのストリート内での合計ベット額）。例: 相手が100ベット、ヒーローがRAISE TO 300 の場合、amount=300。理由: CoinPokerのボタン表示（「ライズ 300」）およびソルバーのベットサイズ指定と一致するため。


confidence の定義:
- `"high"`: 数値変化が明確、OCR信頼度が十分
- `"low"`: OCR部分失敗あり、推定に不確実性あり

#### 4.7.2 判定の優先順序

1. **NEW_HAND** — ポットが前フレームの30%未満に減少 かつ prev_pot > 動的閾値（2 × BB）
2. **NEW_STREET** — ボードカード枚数が増加
3. **BETS_COLLECTED** — 全ベットが0に変化＋ポット増加（ボード枚数変化なし）
4. **座席別分析:**
   - FOLD — スタックが存在→Noneに変化が**3フレーム連続**で確認された場合。1〜2フレームのみNoneはOCR失敗として前回値を保持
   - ALL_IN — スタックが正値→0に変化
   - BET — スタック減少＋ベット出現（前フレームの最大ベット=0）
   - CALL — スタック減少＋ベットが既存最大ベット以内
   - RAISE — スタック減少＋ベットが既存最大ベットの1.1倍超
5. **CHECK（ヒーロー）** — is_my_turn が True→False に変化＋hero_stack/hero_bet 変化なし＋ヒーローカード継続可視
6. **CHECK（相手）** — 以下の条件を満たすフレーム遷移:
   - 全座席のbet変化なし（新たなベットが出現していない）
   - board_card_count変化なし（NEW_STREETではない）
   - pot変化なし
   - 前フレームから何らかの状態変化あり（完全なNO_CHANGEではない。例: is_my_turnの変化、微小なUI変化等）
   - **PoC v2での実績:** 167フレーム中CHECK 10件を正常検出。「数値変化なし＋状態変化あり」の単純なロジックで機能した
   - **制限事項:** ヒーローがフォールド済みの場合、相手間のCHECKの個数は正確にカウントできない（is_my_turnが観測不可のため）。この場合はNEW_STREET検出時に、前ストリートの未検出CHECKを「残りのアクティブプレイヤー全員がCHECK」として補完する
7. **BLIND判定** — NEW_HAND直後のフレームで、SB/BB座席にベットが出現した場合はBLIND_SB/BLIND_BBとして分類


#### 4.7.3 複数アクションの同一フレーム検出

ポーリング間隔（0.5〜1秒）の間に2人以上がアクションした場合、1フレームに複数の変化が現れる。処理ルール:

- 各座席を独立に分析し、変化があった全座席のアクションを `actions` 配列に格納する
- 座席番号の昇順でソート（アクション順序の正確な特定は不可能だが、ログ上の一貫性を確保）
- 同一フレームで3人以上の変化がある場合はconfidence="low"を付与（OCRノイズの可能性）

#### 4.7.4 OCR失敗時のスキップロジック

| 状態 | 判定 | 処理 |
|------|------|------|
| stack=None が1フレームのみ | OCR失敗 | 前回値を保持、アクション不検出 |
| stack=None が2フレーム連続 | OCR失敗の可能性高 | 前回値を保持、WARNING ログ |
| stack=None が3フレーム連続 | FOLD確定 | FOLDアクションを出力 |
| pot値が前回の200%超に急増（1フレーム） | OCR誤読の可能性 | 前回値を保持、次フレームで再確認 |
| pot値が前回の200%超に増加（2フレーム連続） | 実際の変化（Splash Pot等） | 値を採用 |

#### 4.7.5 ボードカード枚数カウント

```python
def count_board_cards(img):
    count = 0
    for i in range(1, 6):
        crop = crop_region(img, f"board_card_{i}")
        if crop is None:
            continue
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        _, s, v = cv2.split(hsv)
        mean_v = float(np.mean(v))
        std_v = float(np.std(v))
        if mean_v > 80 and std_v > 20:
            count += 1
    return count
```

#### 4.7.6 NEW_HAND閾値の動的化

Practice Games（5/10ブラインド）でも誤判定しないよう、固定値200ではなく動的閾値を使用する:

```python
new_hand_threshold = max(config.blind_bb * 2, 20)
is_new_hand = curr_pot < prev_pot * 0.3 and prev_pot > new_hand_threshold
```

config.yamlの `game.blind_bb` から算出。SplashPots（最大1000BB）で急増した直後のハンドでも、ポットが通常レベル（数BB）に戻るため、30%閾値で正しくNEW_HANDを検出できる。

### 4.8 ポジション自動判定

#### 4.8.1 基本ロジック

1. ディーラーボタン「D」の位置を全6座席分の領域で検出
2. ヒーロー座席は画面中央下部に固定（座席番号1、手動設定不要）
3. ディーラーボタンの座席から**座席番号降順（CoinPoker画面上の時計回り）**にポジションを割り当て
   - 巡回順: 例えばdealer_seat=4の場合、4→3→2→1→6→5の順
   - この順序はCoinPokerの6人テーブルの画面配置（セクション16.7）での時計回りに対応する
4. **in_current_hand=false の座席はスキップ**してポジションを詰める（is_seatedではなくin_current_handを使用）
5. ポジション割り当てはハンド開始時に実行し、**ハンド中は変更しない**（FOLD後もポジションは保持）
6. ディーラーボタンの座席がアクティブ座席に含まれない場合（空席にボタンがある場合）、ボタン位置から時計回りに最初のアクティブ座席をBTNとして割り当てる


#### 4.8.2 プレイヤー人数別のポジション割り当て

| 人数 | ポジション割り当て（BTN座席から時計回り） |
|------|----------------------------------------|
| 6人 | BTN → SB → BB → UTG → MP → CO |
| 5人 | BTN → SB → BB → UTG → CO |
| 4人 | BTN → SB → BB → CO |
| 3人 | BTN → SB → BB |
| 2人（ヘッズアップ） | BTN/SB → BB（ポーカー慣習: BTN=SB、BTNが先にアクション） |

2人の場合、BTN座席のプレイヤーはSBも兼ねる（ポーカーのヘッズアップルール）。プリフロップではBTN/SBが先にアクションし、ポストフロップではBBが先にアクションする。

#### 4.8.3 ハンド中の離席対応

ハンド進行中にプレイヤーが離席（stack→None）した場合、**当該ハンドのポジション割り当ては固定**する。次のNEW_HANDで再計算する。

#### 4.8.4 ディーラーボタン検出失敗時のフォールバック

1. 検出スコアが閾値（0.05）未満の場合、前ハンドのディーラー座席を使用
2. 前ハンドの記録もない場合（システム起動直後）、ディーラー座席=Noneとしポジション割り当てを保留。ポジション不明でもアクション推定とカード認識は動作する。ソルバーへのリクエストはポジション確定後に実行
3. WARNING ログを出力: `"Dealer button detection failed, using fallback"`

### 4.9 シットアウト・空席の扱い

専用検出ロジックは不要。自分のシットアウト＝ヒーローカード不在で自動的に待機状態。相手のシットアウト＝カードが配られずアクション不要、数値変化なしとして自然に無視。空席＝stack=Noneとして数値認識で自動検出済み。アクティブプレイヤー数はstack値が存在する座席のカウントで取得。

### 4.10 差分検知によるOCR最適化

1. 毎フレーム全領域をクロップ
2. 前フレームとのピクセル差分合計を計算
3. 差が閾値以下 → 前回OCR値を再利用（OCRスキップ）
4. 差が閾値以上 → EasyOCR実行
5. OCR結果の信頼度チェック（config `ocr.confidence_threshold` 以下なら不合格）
6. 信頼度低 or 異常値 → 前回値を保持し、次フレームで再認識
**領域別の差分閾値（config.yaml `recognition` セクションで管理）:**

| 領域種別 | config キー | デフォルト値 | 理由 |
|---------|------------|-------------|------|
| カード領域 | diff_threshold_card | 500 | カード出現/消失は大きな変化 |
| 数値領域（ポット/スタック/ベット） | diff_threshold_number | 300 | 数字の変化は中程度の変化 |
| ボタン領域 | diff_threshold_button | 200 | 色変化は比較的小さい |

差分の計算方法:
```python
diff = np.sum(np.abs(curr_crop.astype(int) - prev_crop.astype(int)))
if diff < threshold:
    return prev_value  # スキップ
```

**演出ノイズ対策:**
チップアニメーション中は差分が発生するが内容は変化しない。差分が閾値を超えてOCRを実行しても信頼度が低い場合は前回値を保持する。Throwable Objects等の演出は差分大＋信頼度低の同時発生パターンで自動フィルタされる。

処理速度: 通常30-86ms（差分検知＋変化領域のみOCR）。全カード認識時は12.2ms×7枚≈86ms。差分検知による典型的なスキップ率は約69%（PoC v2の167フレームで115/166がNO_CHANGE）。
```


### 4.11 ヒーローのアクション記録

ヒーロー自身が取ったアクション（human_action）を検出し記録する。

#### 4.11.1 ターン境界の保存

ヒーローアクションの検出精度を上げるため、**is_my_turnの境界フレーム**を特別に保存する:

- `turn_start_state`: is_my_turn が False→True に変化した最初のフレームのGameState
- `turn_end_state`: is_my_turn が True→False に変化した最初のフレームのGameState

ヒーローアクションの判定は、通常のフレーム間比較（prev_state vs curr_state）ではなく、**turn_start_state vs turn_end_state の比較**で行う。これにより、ポーリング間隔中に次プレイヤーのアクションが進行していても、ヒーローの変化のみを正確に抽出できる。

#### 4.11.2 検出ロジック

turn_start_state と turn_end_state の比較で判定:

| 条件 | 推定アクション |
|------|--------------|
| hero_stack変化なし、hero_bet変化なし、かつ次フレーム以降も phase が継続（フォールドしていない） | CHECK |
| hero_stack変化なし、hero_bet変化なし、かつ3フレーム以内にヒーローのカード領域が消失 | FOLD |
| hero_stack減少、hero_bet増加、hero_betが turn_start_state時点の最大ベット以内 | CALL |
| hero_stack減少、hero_bet増加、hero_betが turn_start_state時点の最大ベットの1.1倍超 | RAISE |
| hero_stack減少、hero_bet増加、turn_start_state時点の最大ベット=0 | BET |
| hero_stack→0 | ALL_IN |

**CHECK vs FOLD の区別:**
- CHECK: ヒーローアクション後もヒーローカードが可視のまま。phaseがhand_endに遷移しない
- FOLD: ヒーローアクション後にヒーローカード領域が消失（暗転/ブランク化）。3フレーム以内の消失をFOLDと確定する

#### 4.11.3 推奨アクションとの差分記録

ハンドリプレイJSONの各ストリートに以下を記録:

```json
{
  "recommendation": "RAISE 300",
  "human_action": "CALL 200",
  "followed_recommendation": false,
  "deviation_reason": null
}
```

`followed_recommendation` はアクション種別が一致すればtrue（金額の多少の差異は許容）。この差分データは将来の推奨精度分析に使用可能。


### 4.12 ハンド境界とライフサイクル

#### 4.12.1 ハンド開始条件

**ホールカード出現**をハンド開始とする。hero_card_1 と hero_card_2 が両方とも認識成功（rank + suit が確定）した最初のフレーム。

#### 4.12.2 ハンド終了条件 — 2段階遷移

ハンド終了は2段階で処理する:

**段階1: hand_end 遷移**
以下のいずれかで phase を hand_end に遷移:
- ヒーローのホールカードが消失し、5フレーム連続で認識不可（フォールド、またはハンド終了演出）
- ヒーローのFOLDアクションを検出（セクション4.11.2）
- **ショーダウン完了:** board_card_count=5 の状態が10フレーム以上継続し、かつポット値が変化しなくなった場合（配当完了の推定）。ヒーローがオールインしてショーダウンに進んだ場合、カードは消えずに可視のまま最後まで表示されるため、この条件で検出する

**段階2: waiting 遷移**
hand_end 状態で以下のいずれかを検出したら waiting に遷移:
- NEW_HANDイベント（ポット急減）→ 次ハンドの準備完了
- 10秒間 phase=hand_end が継続 → タイムアウトで強制 waiting 遷移

hand_end 遷移時に DB保存 + リプレイJSON保存を実行する。waiting遷移後に次のホールカード出現を待つ。


#### 4.12.3 フォールド後の挙動

ヒーローがフォールドした後、そのハンドの残りのストリートは「観戦状態」となる:
- phase は hand_end に遷移済み
- ポット/ボード/相手スタックの変化は監視するが、アクション推定やソルバー呼び出しは行わない
- ショーダウンで相手カードが見えた場合は認識を試み、リプレイJSONの result.opponent_cards に記録

#### 4.12.4 ハンドID

グローバル連番（hand_history テーブルの AUTOINCREMENT）。テーブル別ではなく全テーブル通しの連番。テーブルIDは meta.table に記録。

#### 4.12.5 ショーダウンとフォールド勝ちの区別

| 条件 | result.showdown |
|------|----------------|
| board_card_count=5 かつ 相手カードが認識可能 | true |
| board_card_count < 5、または相手カード不可視 | false |

#### 4.12.6 ハンドのフェーズ遷移図
```
[waiting]
  │ hero_card_1 + hero_card_2 認識成功
  ↓
[preflop] (board_card_count=0)
  │ board_card_count → 3
  ↓
[flop] (board_card_count=3)
  │ board_card_count → 4
  ↓
[turn] (board_card_count=4)
  │ board_card_count → 5
  ↓
[river] (board_card_count=5)
  │
  ├─ ヒーローカード消失5フレーム or FOLD検出 ─────→ [hand_end]
  │                                                      │
  ├─ ショーダウン完了（board=5, pot安定10フレーム）──→ [hand_end]
  │                                                      │
  │                                                ├─ NEW_HAND検出 → [waiting]
  │                                                └─ 10秒タイムアウト → [waiting]
  │
  └─ 全ストリート完了、ポット急減 ──────────────→ [hand_end] → [waiting]

※ preflop〜river の任意フェーズから hand_end に遷移可能
   （フォールド、オールインショーダウン等）
※ hand_end 遷移時に DB保存 + リプレイJSON保存を実行
※ ヒーローカードのキャッシュは hand_end 遷移時にクリア
```

### 4.13 プレイヤー識別と統計蓄積

#### 4.13.1 プレイヤー名の取得と照合

CoinPokerでは長い名前が末尾「..」で省略表示される。OCRで取得した文字列（省略記号含む）をそのまま使用する。フルネームの別途取得は行わない。

照合ルール:
- DB問い合わせ時は**前方一致**（LIKE 'xxx%'）で検索
- 新規プレイヤーはOCR取得文字列をそのまま player_name として登録
- 同一テーブル・同一座席で名前が異なるプレイヤーが現れた場合は新規プレイヤーとして扱う

#### 4.13.2 セッション内の一意特定

テーブルID（ウィンドウタイトルから取得）＋座席番号で、1セッション内のプレイヤーを一意に特定する。プレイヤー名のOCR失敗時も、座席番号で追跡を継続する。

#### 4.13.3 省略名の衝突リスク

CoinPokerのプレイヤー名はユニークだが、省略後は衝突しうる（例: "LongPlayerName1.." と "LongPlayerName2.." が同一の "LongPlaye.." に省略される可能性）。この衝突は統計の精度を下げるが、発生頻度は極めて低い。対策として:

- 統計は total_hands が10以上の場合のみ信頼度「高」とする
- total_hands < 10 の場合、LLMへの統計提供時に「サンプル数不足」の注釈を付与

名前変更されたプレイヤーは新規プレイヤーとして扱う（旧名との紐付けは行わない）。

### 4.14 アクション履歴の蓄積と管理

#### 4.14.1 責務の所在

`core/hand_manager.py` がハンド単位のアクション履歴蓄積を担当する。hand_manager は以下を管理:

- 現在のハンドのフェーズ（waiting / preflop / flop / turn / river / hand_end）
- ハンド開始からの全アクション履歴リスト
- 各ストリートのアクション履歴（ストリート別に分割）
- ヒーローのターン境界state（turn_start_state / turn_end_state）
- ハンド開始時のプレイヤー参加状態（in_current_hand）

#### 4.14.2 アクション蓄積フロー

```
フレームごと:
  1. action_estimator が actions_since_last_frame を出力
  2. hand_manager が受け取り、重複排除を実行
  3. 重複排除済みアクションを hand_actions リストに追加
  4. ストリート別リストにも振り分け

ハンド終了時:
  5. hand_actions を hand_history DB に保存
  6. ストリート別データをリプレイJSON に保存
  7. hand_actions をクリア、次ハンドに備える
```

#### 4.14.3 重複排除ルール

ポーリング間隔0.5秒では、同じアクションが複数フレームにまたがって検出される可能性がある。排除条件:

- 同一seat、同一action、同一amount（±5%以内）のアクションが直前フレームで既に検出済みの場合は破棄
- ただし同一seatの異なるアクション（例: BET → RAISE）は別アクションとして蓄積

#### 4.14.4 アクション順序の取り扱い

1フレーム内で複数座席のアクションが検出された場合、正確な順序の特定は不可能。以下のルールで近似する:

- ディーラーボタン位置から時計回りのアクション順（ポーカーのルール上の順序）で並べる
- ただしこの順序は推定であり、confidence="low" を付与する
- リプレイJSON/DB保存時に `order_estimated: true` フラグを付与

### 4.15 NEW_HAND直後の初期化

#### 4.15.1 prev_stateの取り扱い

NEW_HANDイベント検出時の処理:

1. **現フレームを「ハンド初期状態」として保存** — このフレームのpot/bet/stack値を新ハンドの基準値とする
2. prev_state をこのフレームで上書きする（Noneにはしない）
3. 次フレーム以降の差分計算はこの基準値との比較で行う

#### 4.15.2 ブラインドの記録

NEW_HANDフレーム時点で既にSB/BBのベットが画面に表示されている場合:
- SB座席のベット額を BLIND_SB アクションとして記録
- BB座席のベット額を BLIND_BB アクションとして記録
- これらは差分検出ではなく、**初期状態の静的解析**として記録する

SB/BB座席の特定: ディーラーボタン座席から時計回り1つ目=SB、2つ目=BB（セクション4.8.2に基づく）。

---

## 5. ソルバー統合

### 5.1 選定ソルバー

postflop-solver（Rust、Discounted CFR γ=3.0、AGPL-v3）。ビルド・ベンチマーク完了済み。

ビルド時に適用した修正2点:
1. Cargo.toml: `default = ["bincode", "rayon"]` → `default = ["rayon"]`（bincode API不整合回避）
2. src/lib.rs 先頭に `#![allow(dangerous_implicit_autorefs, mismatched_lifetime_syntaxes)]` 追加

### 5.2 実測性能

| ストリート | ベットサイズ | 実測時間 | メモリ |
|-----------|------------|---------|--------|
| フロップ | 3種（60%, geometric, all-in） | 7.09秒 | 0.73GB |
| フロップ | 2種（60%, all-in）← **標準設定** | 3.72秒 | 0.48GB |
| ターン | 3種 | 0.03秒 | — |
| リバー | （推定） | < 0.03秒 | — |

### 5.3 ベットサイズ動的切替

タイムバンク残秒数に応じて切替:
- 残り10秒以上 → 3種ベットサイズ（60%, geometric, all-in）で精度優先
- 残り10秒未満 → 2種ベットサイズ（60%, all-in）で速度優先
- 残り5秒未満 → ソルバーをスキップしLLM単独判断

### 5.4 統合方式: Rust CLI常駐ラッパー + Python subprocess

PyO3（Rust→Python直接バインディング）はGILとrayonの競合で性能劣化するため不採用。Rustで常駐CLIラッパーを作成し、Pythonからsubprocess.Popenで永続プロセスとして管理、stdin/stdoutでJSON行通信する。

```
solver/
  ├─ postflop_cli/           # Rust CLIソースコード（AGPL-v3）
  │   ├─ Cargo.toml
  │   └─ src/main.rs         # stdin JSON行 → solve → stdout JSON行（常駐ループ）
  ├─ bin/
  │   └─ postflop_cli.exe    # ビルド済みバイナリ
  ├─ solver_bridge.py        # Python側ブリッジ
  └─ README.md
```

Codex（実装役）はPythonブリッジ（solver_bridge.py）のみを実装・修正する。Rust CLIラッパーの実装・ビルドはローカル環境で行い、ビルド済みバイナリをリポジトリにコミットする。

### 5.5 カードエンコーディング

u8型 0-51: `card = rank * 4 + suit`
- rank: 2=0, 3=1, ..., A=12
- suit: Club=0, Diamond=1, Heart=2, Spade=3
- ヘルパー: `card_from_str("Ah")` → 50
- 未配布: `NOT_DEALT = 255`

### 5.6 レンジ文字列形式（PioSOLVER互換）

カンマ区切り: `"AA,AKs,AKo,QQ-88,A2s+,K9o+"` 。重み付き: `"AA:0.5,AKs:1.0"` 。プラス表記、コネクタープラス、ダッシュ範囲、明示スートに対応。空文字列許可。

### 5.7 ベットサイズ構文

- `%` = ポットに対する割合（例: `"60%"`）
- `x` = 前回ベットの倍数（レイズのみ、例: `"2.5x"`）
- `c` = 固定チップ額（例: `"100c"`）
- `e` = ジオメトリック（例: `"2e"`）
- `a` = オールイン

### 5.8 JSON入出力スキーマ

**リクエスト:**
```json
{
  "board": "QsJh2h",
  "turn": null,
  "river": null,
  "range_oop": "QQ-88,AJs+,KQs,AJo+,KQo",
  "range_ip": "22+,A2s+,K9s+,Q9s+,J9s+,T8s+,97s+,87s,76s,65s,A8o+,K9o+,QTo+,JTo",
  "starting_pot": 200,
  "effective_stack": 900,
  "flop_bet_sizes_oop": "60%,a",
  "flop_bet_sizes_ip": "60%,a",
  "flop_raise_sizes_oop": "2.5x",
  "flop_raise_sizes_ip": "2.5x",
  "turn_bet_sizes_oop": "60%,a",
  "turn_bet_sizes_ip": "60%,a",
  "turn_raise_sizes_oop": "2.5x",
  "turn_raise_sizes_ip": "2.5x",
  "river_bet_sizes_oop": "60%,a",
  "river_bet_sizes_ip": "60%,a",
  "river_raise_sizes_oop": "2.5x",
  "river_raise_sizes_ip": "2.5x",
  "rake_rate": 0.0,
  "rake_cap": 0.0,
  "add_allin_threshold": 1.5,
  "force_allin_threshold": 0.15,
  "merging_threshold": 0.1,
  "max_iterations": 200,
  "target_exploitability_pct": 0.5,
  "timeout_ms": 7000,
  "bunching": null
}
```

**レスポンス:**
```json
{
  "success": true,
  "exploitability": 0.299,
  "exploitability_pct": 0.598,
  "solve_time_ms": 3720,
  "memory_usage_bytes": 503316480,
  "iterations_run": 60,
  "root_strategy": {
    "actions": ["Check", "Bet 120", "AllIn 900"],
    "hands": ["AhAs", "AhKh", "..."],
    "strategy_matrix": [[0.0, 0.65, 0.35], "..."],
    "equity": [0.72, 0.68, "..."],
    "ev": [145.2, 132.1, "..."],
    "average_strategy": {"Check": 0.32, "Bet 120": 0.45, "AllIn 900": 0.23}
  },
  "queried_nodes": []
}
```
**単位の明記:**
- `starting_pot`、`effective_stack`、全ベットサイズの `c` 指定: **チップ額**（BB単位ではない）
- ソルバーは内部的にチップ額で計算する。BB単位への変換はHUD表示側で行う

**レーキのデフォルト値:**
- Practice Games（開発・テスト用）: `rake_rate: 0.0`、`rake_cap: 0.0`
- リアルマネー（将来用）: `rake_rate: 0.05`、`rake_cap: 暫定3BB`（CoinPokerの正確な値が判明次第更新）
- config.yamlで設定可能にする

### 5.9 タイムアウト時の部分結果出力

Rust側で `solve_step()` を使用し、時間制限内で可能な限りイテレーションを実行する。制限時間到達時は現時点のexploitabilityと戦略を部分結果として出力する。

### 5.10 Pythonブリッジ

```python
class PostflopSolverBridge:
    def __init__(self, cli_path="./solver/bin/postflop_cli.exe"):
        self.cli_path = cli_path
        self.process = None
        self.disabled = False  # 3回連続再起動失敗で True

    def start(self):
        self.process = subprocess.Popen(
            [self.cli_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True
        )

    def solve(self, request: dict, timeout: float = 7.0) -> dict:
        """JSON行をstdinに送信し、stdoutからJSON行を受信する。
        threading + queue.Queue でtimeout付きstdout読み取りを実装。"""
        self.process.stdin.write(json.dumps(request) + '\n')
        self.process.stdin.flush()
        result_line = self._readline_with_timeout(timeout)
        return json.loads(result_line)

    def stop(self):
        if self.process:
            self.process.terminate()

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

```

### 5.11 バンチングデータ（将来対応）

最大4人のフォールドプレイヤーのレンジを考慮可能（postflop-solverの固有機能）。ただし約3.42GBのRAMを使用するため、初期実装では無効化する。


### 5.12 effective_stackの計算ロジック

ソルバーはヘッズアップ専用のため、effective_stackは2人のプレイヤー間で計算する。

#### 5.12.1 ヘッズアップ（2人）

```python
effective_stack = min(hero_stack, opponent_stack)
```

#### 5.12.2 マルチウェイからヘッズアップへの簡略化

3人以上が残っている場合、ソルバーは使用できずLLM主導判断（セクション6.3）に切り替える。ただし以下の条件でヘッズアップに簡略化し、ソルバーを使用する場合がある:

**簡略化条件:**
- 残りプレイヤーが3人で、うち1人のスタックが極小（< 5BB）の場合、主要2人でのヘッズアップとして計算
- フロップ以降でアクティブベッターが2人のみ（他は全員チェック済みまたはフォールド済み）の場合

**effective_stackの決定:**
```python
# ヒーロー vs アクティブ相手の中で最も近いスタック
opponent_stacks = [s for s in active_opponent_stacks if s is not None and s > 0]
if len(opponent_stacks) == 1:
    effective_stack = min(hero_stack, opponent_stacks[0])
elif len(opponent_stacks) > 1:
    # マルチウェイ → LLM判断に切り替え
    use_solver = False
```

---

## 6. LLMパイプライン

### 6.1 4タスク

**タスク1 — レンジ推定（最重要）:** 相手のポジション、アクション履歴、DB統計から、ソルバーに渡す range_oop / range_ip の文字列を生成する。ベースラインJSONテーブル（位置・アクション別デフォルトレンジ）を用意し、LLMは相手統計に基づく微調整のみ行う。出力が空や不自然な場合はベースラインへフォールバック。Rust側で `parse::<Range>()` によるバリデーションを実施する。

**タスク2 — 搾取調整:** ソルバーのGTO出力を、相手の弱点に基づき微調整する。ソルバー出力との極端な矛盾は禁止（ソルバー優先）。

**タスク3 — マルチウェイ判断:** 3人以上残っている局面（ソルバー不可）で、eval7 MCエクイティとDB統計を基に主導判断する。保守的バイアス付き。

**タスク4 — 判断理由の生成:** HUDに表示する短文の判断根拠を生成する。

### 6.2 LLMの役割変化

| 状況 | ソルバー | LLMの役割 |
|------|---------|-----------|
| ヘッズアップ（2人） | postflop-solver | レンジ推定 + 搾取微調整 + 理由生成（補助的） |
| マルチウェイ（3人以上） | 使用不可 | レンジ推定 + eval7 MCエクイティ + ヒューリスティック判断 + 理由生成（主導的） |

### 6.3 マルチウェイ判断フロー

1. LLMが各相手のレンジを推定
2. eval7でMonte-Carloシミュレーション（10,000回、5-20ms）を実行しエクイティを算出
3. エクイティ＋ボード情報＋相手統計をLLMに提示
4. LLMが最適アクションを生成

### 6.4 API設定

OpenRouter経由。通常は安価・高速モデル、重要局面で高性能モデルに切替。APIキーとモデル名は.envで管理する。タイムアウト2秒、リトライ1回、失敗時はベースラインレンジにフォールバック。

### 6.5 recommendation 生成タイミング

#### 6.5.1 バックグラウンド先行計算

ソルバー呼び出しはフロップで3.72秒かかるため、自分のターンが来てから計算を開始するとHUDが数秒間空白になる。これを回避するため、**ストリート確定時にバックグラウンドで先行計算を開始**する:
```
NEW_STREET検出（ボードカード枚数増加） ↓ バックグラウンドスレッド（daemon=True, name="bg-strategy"）で recommendation_engine.generate() を実行

この時点のGameState（アクション履歴・スタック情報含む）を使用
結果は _pending_recommendation にキャッシュ ↓ 自分のターン到来（is_my_turn=True） ↓ _pending_recommendation があれば即座にHUD表示 ↓ _pending_recommendation がなければ同期で generate() を実行
```


#### 6.5.2 計算の発火条件

| トリガー | 計算内容 | 実行方式 |
|---------|---------|---------|
| NEW_STREET検出 | LLMレンジ推定 + ソルバー実行 | バックグラウンドスレッド |
| is_my_turn=True（先行計算完了済み） | キャッシュ結果を表示 | メインスレッド（即時） |
| is_my_turn=True（先行計算未完了） | 同期で generate() 実行 | メインスレッド（ブロッキング） |
| プリフロップ + is_my_turn=True | チャート参照（_pending_recommendation がない場合のみ） | メインスレッド（即時） |

**キャッシュ制御ルール:**
- `_pending_recommendation` はis_my_turnがTrue→Falseに変化した時点でクリアする
- NEW_STREET検出時に進行中のバックグラウンド計算をキャンセルし（`_bg_cancelled`フラグ）、新ストリートの計算を開始する
- バックグラウンド計算完了時にストリートが変わっていれば結果を破棄する
- プリフロップでは _pending_recommendation が存在する間は再計算しない

**ボタン制約の再適用:**
キャッシュ済み推奨に対して、毎フレーム `_apply_action_constraints()` を再適用する（セクション6.6参照）。ボタン状態がフレーム間で変化した場合に推奨を安全な方向に補正するため。

#### 6.5.3 先行計算が間に合わなかった場合

自分のターン到来時に先行計算が未完了の場合:
- 同期で recommendation_engine.generate() を実行
- HUDには計算完了後に推奨を表示
- 7秒（ソルバータイムアウト）経過しても未完了ならLLM単独判断にフォールバック

#### 6.5.4 ソルバーヘルスチェック

バックグラウンド計算開始前にソルバープロセスの生存を確認する:
- `solver_bridge.is_alive()` で確認
- プロセスが死亡していれば再起動を試みる
- 再起動失敗時はログを出力し、LLM単独判断モードで計算を続行する

### 6.6 推奨アクションのボタン制約（_apply_action_constraints）

recommendation_engine が生成した推奨アクションが、画面に表示されているボタン状態と矛盾する場合に安全な方向へ変換する。この制約はキャッシュ済み推奨に対しても毎フレーム再適用される。

#### 6.6.1 変換ルール

以下の3ルールを順に評価し、最初に合致したルールを適用する:

| # | 条件 | 変換 | 理由 |
|---|------|------|------|
| 1 | 推奨=FOLD かつ buttons.call_or_check="check" | FOLD→CHECK | チェック可能な場面でフォールドは不要 |
| 2 | 推奨=FOLD かつ buttons.call_or_check="call" かつ hero_bet ≥ max_opponent_bet | FOLD→CHECK | 追加コスト不要（BBリンプ等） |
| 3 | 推奨=CHECK かつ buttons.call_or_check="call" かつ hero_bet < max_opponent_bet | CHECK→FOLD | チェック不可能な場面。confidenceを"low"に変更 |

**hero_bet**: ヒーローの現在のベット額（dict/dataclass両対応の `_player_bet()` で取得）。
**max_opponent_bet**: ヒーロー以外の全プレイヤーの最大ベット額（`_get_max_opponent_bet()` で取得）。

#### 6.6.2 FOLD→CHECK変換の詳細

変換時にRecommendationの以下のフィールドを更新する:
- action → "CHECK"
- amount → 0
- reason → "チェック可能（ベットなし）"
- pot_percentage, amount_bb, preset_hint, raise_multiplier, raise_multiplier_label → None

#### 6.6.3 CHECK→FOLD変換の詳細

元のRecommendationを保持しつつ新しいRecommendationを作成する:
- action → "FOLD"
- amount → 0
- reason → 元のreason + "（チェック不可のためフォールド推奨）"
- confidence → "low"（元のconfidenceに関わらず）
- strategy_source, action_probabilities, solver_exploitability, latency_breakdown → 元の値を引き継ぐ
- `_enrich_recommendation()` を再適用する

### 6.7 推奨アクションの補足情報（_enrich_recommendation）

recommendation_engine が生成した推奨に対して、HUD表示用の補足情報を付与する。generate() の最終段階、および _apply_action_constraints() による変換後に適用される。

#### 6.7.1 付与するフィールド

| フィールド | 算出方法 | 対象アクション |
|-----------|---------|--------------|
| amount_bb | amount / blind_bb | BET, RAISE, CALL, ALL_IN |
| pot_percentage | (amount / pot) × 100 | BET, RAISE, CALL, ALL_IN |
| preset_hint | pot_percentage に最も近いプリセット（33%/50%/75%/100%） | BET |
| raise_multiplier | amount / base_amount | RAISE のみ |
| raise_multiplier_label | "{raise_multiplier}X" | RAISE のみ |

**raise_multiplier の base_amount:**
- プリフロップ: blind_bb（BBサイズ）
- ポストフロップ: 現在のストリートでの最大ベット額

FOLD, CHECK の場合は全補足フィールドを None に設定する。

#### 6.7.2 プリセットヒント

pot_percentage に最も近いプリセットを返す。プリセット候補: 33%, 50%, 75%, 100%。差が10%以内の場合にヒントを付与する。


### 6.8 LLM出力のバリデーション

#### 6.8.1 現行方式（v1.4時点）

LLMからのJSON応答は以下の手順で抽出する:
1. レスポンス全体を `json.loads()` で試行
2. 失敗した場合、正規表現で `{...}` ブロックを抽出し再試行
3. 抽出後、期待されるキーの存在をチェック
4. バリデーション失敗時はベースラインレンジにフォールバック

#### 6.8.2 改善計画（Phase 22-3で実装予定）

strict JSON schema + pydanticバリデーションに移行する:
- 各タスク（レンジ推定、搾取調整、マルチウェイ判断、理由生成）ごとにpydantic ModelをRequest/Response として定義
- LLM APIリクエストに `response_format: { type: "json_schema" }` を指定（OpenRouterがサポートするモデルで利用可能）
- pydantic ValidationError 発生時はベースラインへフォールバック
- バリデーション成功率を計測し、ログに記録する

---

## 7. プリフロップチャート

プリフロップはソルバー不要。事前解析済みチャートをJSONテーブルで参照する。

データ構造:
```json
{
  "6max": {
    "UTG": {
      "RFI": {
        "raise": "77+,ATs+,AJo+,KQs,KJs",
        "fold": "残り全て"
      }
    },
    "BB": {
      "vs_raise": {
        "3bet": "QQ+,AKs,AKo",
        "call": "22-JJ,A2s-AQs,ATo-AQo,KTs+,...",
        "fold": "残り全て"
      }
    }
  }
}
```

ポジション × アクション履歴ごとに最適ハンドレンジと推奨アクションを参照する。データソースはGTO Wizard等の公開チャートから作成する。
```

### 7.1 プリフロップにおけるLLM介入（Phase 22-4で実装予定）

現行はチャート参照のみだが、相手統計が十分にある場合にLLMによるチャートの微調整（delta policy）を導入する。

#### 7.1.1 基本方針: chart-anchored delta policy

チャートは常に基準方策として保持し、LLMは「どれだけ、どの方向に、どんな条件でずらすか」の差分のみを出力する。LLMがチャートの代わりにアクションを直接決定することはない。

#### 7.1.2 介入条件

| 統計サンプル数 | LLM介入 | shift cap |
|--------------|---------|-----------|
| < 30ハンド | 無効（チャートのみ） | — |
| 30〜100ハンド | 有効（小幅） | 各アクション ±5pp |
| > 100ハンド | 有効（通常） | 各アクション ±10pp |

pp = percentage point（確率の絶対値変化量）。

#### 7.1.3 入出力スキーマ

**リクエスト:**
```json
{
  "hero_position": "BTN",
  "hero_hand": "AJo",
  "effective_stack_bb": 100,
  "action_prefix": ["CO_OPEN_2.5BB"],
  "chart_anchor_probs": {"fold": 0.15, "call": 0.55, "raise": 0.30},
  "villain_stats": {
    "seat": 3,
    "sample_hands": 842,
    "vpip": 31.2,
    "pfr": 24.4,
    "three_bet_pct": 9.8,
    "fold_to_three_bet": 67.0,
    "freshness_days": 12
  }
}

レスポンス:

{
  "delta_probs": {"fold": -0.05, "call": -0.02, "raise": 0.07},
  "confidence": 0.78,
  "reason": "相手のfold_to_3betが高いためレイズ頻度を増加"
}

#### 7.1.4 バリデーション
delta_probsの合計は0（±0.01の丸め誤差を許容）
chart_anchor_probs + delta_probs の各値が 0.0〜1.0 の範囲内
チャートで確率0のアクションに正のdeltaを割り当てることは禁止
バリデーション失敗時はチャートのみにフォールバック

#### 7.1.5 レイテンシ目標
プリフロップは頻度が高いため、delta policy のレイテンシ目標は厳しめに設定する:

p50: 300ms以下
p95: 500ms以下
ハードタイムアウト: 1000ms（超過時はチャートのみにフォールバック）

---

### 追加10: セクション17「開発フェーズ」の末尾に以下を追加

```markdown
### Phase 22: 品質向上・勝率改善（SPEC.md v1.4で追加）

Phase 21（統合テスト）完了後の改善フェーズ。実装計画書（IMPLEMENTATION_PLAN.md v1.3）のPhase 22-1〜22-5として詳細化する。

| サブフェーズ | 内容 | 依存 |
|------------|------|------|
| Phase 22-1 | リプレイJSONへの推奨・レイテンシ保存 | Phase 21完了 |
| Phase 22-2 | 30分ライブテスト + ベースライン計測 | Phase 22-1 |
| Phase 22-3 | strict JSON schema / pydanticバリデーション | Phase 22-1 |
| Phase 22-4 | プリフロップ delta policy導入 | Phase 22-3 |
| Phase 22-5 | プレイヤー名匿名化 | Phase 22-1 |
---

## 8. 相手統計DB

### 8.1 スキーマ

```sql
opponents (
    player_name     TEXT PRIMARY KEY,
    long_term_style TEXT,           -- "TAG","LAG","NIT"等
    total_hands     INT,
    first_seen      DATE,
    last_seen       DATE,
    vpip            REAL,
    pfr             REAL,
    three_bet_pct   REAL,
    cbet_flop_pct   REAL,
    fold_to_three_bet REAL,
    went_to_showdown  REAL,
    freshness_note  TEXT
)

hand_history (
    hand_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name     TEXT,
    timestamp       DATETIME,
    hole_cards      TEXT,
    actions         TEXT,           -- JSON形式
    result          REAL,
    board           TEXT
)
```

### 8.2 運用ルール

鮮度ルール: last_seenから90日以上経過で「データ古い、傾向参考程度」の注釈をLLMに自動付与。統計はハンド終了時にリアルタイム更新（次ハンドに即反映）。プレイヤー名は長い名前が末尾「..」で省略表示されるため、照合は前方一致で対応。

---

## 9. GUI

### 9.1 メインウィンドウ（PyQt6、タブ形式）

**設定タブ:**
- 座標マッピング: キャプチャ開始→Snipping風オーバーレイ→ドラッグ選択→プレビュー＋座標登録。プロファイルの保存・読込（profiles/ディレクトリ）
- ゲーム設定: ブラインドサイズ（SB/BB）入力、テーブル人数6人固定、ベットサイズ選択肢設定（ストリートごと × IP/OOP × bet/raise/allin）、プリセット
- LLM設定: APIキー状態表示（.envから、マスク表示）、モデル名表示、接続テストボタン
- ソルバー設定: postflop_cliパス指定、精度パラメータ、テスト実行ボタン
- HUD設定: 表示/非表示ホットキー、フォントサイズ、背景透明度、表示項目チェックボックス
- キャプチャ設定: 方式選択（capture_card / mss）、デバイス番号

**稼働タブ:**
- START/STOPトグル＋リロード
- 最新キャプチャのサムネイル
- 認識結果のJSON表示（リアルタイム更新）
- ゲームフェーズステータス（待機/プリフロップ/フロップ/ターン/リバー）
- 異常検知アラート
- ログビューア（INFO/WARNING/ERRORフィルタ）
- リロードボタン: 押下時に以下を再読み込みする
  - 座標プロファイル（profiles/coinpoker_6max.json）
  - config.yamlの recognition セクションおよび action_estimation セクション（差分閾値等）
  - config.yamlの game セクション（ブラインドサイズ）
- リロード**しない**もの（再起動が必要）:
  - ソルバーCLIプロセス（solver セクション変更時は再起動）
  - DB接続
  - EasyOCR Readerインスタンス（ocr セクション変更時は再起動）
  - キャプチャデバイス（capture セクション変更時は再起動）

**統計タブ:**
- 対戦相手一覧テーブル（ソート可能）
- プレイヤー詳細ビュー（ハンド履歴）
- 鮮度表示（90日超は背景色変更）
- CSV/JSONエクスポート

### 9.2 HUDオーバーレイ

CoinPokerウィンドウの外側（右側または下側）に独立ウィンドウとして表示する。CoinPokerウィンドウは最大化で固定サイズのため、空きスペースに配置する。

表示内容:
- 推奨アクション: 英語表記（FOLD / CHECK / CALL / BET / RAISE + サイズ）
  - BET表示例: `BET 825 (8.2BB) [33%pot]`
  - RAISE表示例: `RAISE 300 (3.0BB) [3.0X]`
  - CALL表示例: `CALL 200 (2.0BB)`
  - FOLD/CHECK: サイズ表示なし
- ソース表示: アクション行とは別行に小フォント・グレーで `Source: Chart / Solver / AI / Fallback` と表示
- LLMの判断理由（日本語短文）
- 信頼度表示（ソルバー使用=high、LLM=medium、フォールバック/制約変換=low）


位置はドラッグ調整可能。ホットキーで表示/非表示切替。CoinPokerウィンドウに重ならないため、キャプチャ映像への映り込みは発生しない。

---

## 10. エラーハンドリング

設計原則:「判断しない方が安全」— 誤った推奨を出すよりフォールバックまたは無推奨を選ぶ。

| 障害 | 検知方法 | フォールバック | 再試行 |
|------|---------|---------------|--------|
| キャプチャ映像喪失 | ret==False | 再接続3回→STOP＋HUD警告 | 自動 |
| OCR認識失敗 | 信頼度閾値以下 or パース不能 | 前回値保持＋「認識不安定」警告 | 次フレーム |
| OCR演出ノイズ | 差分大＋信頼度低 | 前回値保持 | 時間経過後 |
| ソルバータイムアウト | 7秒上限 | 部分結果使用→なければLLM単独 | なし |
| LLM APIエラー | HTTPステータス or 2秒タイムアウト | リトライ1回→ベースラインレンジ | 1回 |
| LLMレンジ出力不正 | Range文字列パース失敗 | ベースラインレンジ | なし |
| DBアクセスエラー | SQLite例外 | ログ記録して続行（統計なし） | なし |
| ディーラーボタン検出失敗 | スコア < 0.05 | 前ハンドの座席を使用→前ハンドもなければポジション保留 | 次フレーム |
| プレイヤー名OCR失敗 | 信頼度閾値以下 | 座席番号で追跡継続、DB照合は次回成功時 | 次フレーム |
| ハンド開始検知失敗 | ホールカード5フレーム以上未検出 | waiting状態を継続 | 毎フレーム |
---

## 11. ログとデータ保存

### 11.1 ログ

保存先: `logs/poker_assistant.log`。RotatingFileHandler: 50MB × 5ファイル。
フォーマット: `[YYYY-MM-DD HH:MM:SS.mmm] [LEVEL] [MODULE] MESSAGE`

記録対象: フレーム取得タイムスタンプ、OCR認識結果と信頼度、差分検知判定、ソルバー入出力と実行時間、LLMリクエスト/レスポンスとレイテンシ、エラーとフォールバック発動、HUD表示内容。

### 11.2 ハンドリプレイJSON

保存先: `hand_replays/YYYY-MM-DD/hand_NNNNNN.json`。保持期間30日（自動クリーンアップ）。

**保存タイミング:** ハンド終了時（phase=hand_end遷移時）にまとめて全ストリートを保存する。ストリート進行中はメモリ内（hand_manager の StreetActions）に蓄積する。保存は同期で行う（現行実装。将来的に非同期化を検討）。

**推奨・レイテンシの保存状態（v1.4時点）:** StreetActions に recommendation, human_action, followed_recommendation, time_to_recommend_ms, latency_breakdown のフィールドが定義されているが、game_loop.py から hand_manager への推奨データ受け渡しが未実装のため、リプレイJSONにこれらのフィールドが保存されない。Phase 22（セクション17.1）で対応する。


**スキーマ:**

```json
{
  "meta": {
    "hand_id": 123,
    "timestamp": "2026-04-25T14:32:05+00:00",
    "table": "NLH_354257_50_100",
    "seat": 1,
    "blinds": [50, 100],
    "site": "coinpoker"
  },
  "streets": {
    "preflop": {
      "hole_cards": ["Td", "9c"],
      "actions_observed": [
        {"seat": 4, "action": "BLIND_SB", "amount": 50},
        {"seat": 5, "action": "BLIND_BB", "amount": 100},
        {"seat": 2, "action": "CALL", "amount": 100}
      ],
      "recommendation": "RAISE 300",
      "human_action": "RAISE 300",
      "followed_recommendation": true,
      "time_to_recommend_ms": 1200,
      "latency_breakdown": {
        "capture_ms": 17,
        "ocr_ms": 86,
        "llm_ms": 1050,
        "solver_ms": 0,
        "hud_ms": 47
      }
    },
    "flop": {
      "board": ["8c", "7d", "8d"],
      "actions_observed": [
        {"seat": 5, "action": "CHECK", "amount": 0},
        {"seat": 2, "action": "CHECK", "amount": 0}
      ],
      "recommendation": "BET 66%",
      "human_action": "BET 230",
      "followed_recommendation": true,
      "time_to_recommend_ms": 4100,
      "latency_breakdown": {
        "capture_ms": 17,
        "ocr_ms": 86,
        "llm_ms": 1200,
        "solver_ms": 2750,
        "hud_ms": 47
      }
    },
    "turn": null,
    "river": null
  },
  "result": {
    "outcome": "unknown",
    "profit": null,
    "showdown": true,
    "opponent_cards": null
  }
}

```

**ヒーローフォールド後のストリート記録:**

ヒーローがフォールドしたストリートには `hero_action: "FOLD"` を記録する。それ以降のストリートは null とする。ただし観戦中にボード情報が取得できた場合は、ボード情報のみ記録する:

```json
{
  "flop": {
    "board": ["8c", "7d", "8d"],
    "actions_observed": [
      {"seat": 5, "action": "BET", "amount": 200}
    ],
    "recommendation": "CALL 200",
    "human_action": "FOLD",
    "followed_recommendation": false,
    "hero_action": "FOLD",
    "spectate_only": false
  },
  "turn": {
    "board": ["8c", "7d", "8d", "Ah"],
    "spectate_only": true
  },
  "river": null
}
```

**レイテンシフィールドの定義:**

| フィールド | 説明 |
|-----------|------|
| time_to_recommend_ms | キャプチャからHUD表示までのE2Eレイテンシ（ミリ秒） |
| latency_breakdown.capture_ms | フレーム取得時間 |
| latency_breakdown.ocr_ms | 画面認識（全領域OCR）時間 |
| latency_breakdown.llm_ms | LLM APIレスポンス時間 |
| latency_breakdown.solver_ms | ソルバー実行時間（プリフロップでは0） |
| latency_breakdown.hud_ms | HUDレンダリング・表示時間 |
```
**現行の保存状態（v1.4時点）:**

hand_managerのStreetActionsにrecommendation等のフィールドが定義されているが、game_loopからhand_managerへの推奨データ受け渡しが未実装のため、リプレイJSONに以下のフィールドが保存されない:
- recommendation
- human_action
- followed_recommendation
- time_to_recommend_ms
- latency_breakdown

Phase 22-1（リプレイJSONへの推奨保存）で対応する。対応完了後にこの注記を削除する。

---

## 12. テスト戦略

**Level 1 — 静的画像テスト（ユニットテスト）:** 保存済みCoinPokerスクリーンショットを入力とし、カード認識100%、数値認識95%以上を目標。テストコード: `tests/test_recognition.py`。テスト画像: `tests/fixtures/screenshots/coinpoker/`。

**Level 2 — ライブ映像テスト（統合テスト）:** キャプチャカード経由のPractice Games映像を30分間連続稼働。目標: 認識エラー0件/30分、フォールバック発動回数計測、差分検知のスキップ率計測。

**Level 3 — エンドツーエンドレイテンシテスト:** 全パイプラインのP95 ≤ 7秒。各段階のレイテンシ内訳を計測（time.perf_counter使用）。

**回帰テスト:** 新機能追加時にLevel 1テストを自動実行し、既存認識精度の劣化がないことを確認。
```

---

## 13. ディレクトリ構成

```
poker-assistant/
├── main.py                        # エントリポイント（python main.py で起動）
├── config.yaml                    # 設定ファイル（秘匿情報以外）
├── .env                           # APIキー等の秘匿情報
├── .env.example                   # .envのテンプレート
├── AGENTS.md                      # Codex向け開発ルール
├── README.md
├── requirements.txt
├── LICENSE-AGPL-v3
│
├── recognition/                   # 画面認識モジュール
│   ├── __init__.py                # EasyOCR Readerシングルトン管理
│   ├── base_recognizer.py         # 抽象基底クラス
│   ├── card_recognizer.py         # 4色HSV + EasyOCR
│   ├── number_recognizer.py       # EasyOCR（数値）
│   ├── button_recognizer.py       # HSV色検出
│   ├── dealer_recognizer.py       # 赤+白ピクセルスコアリング
│   ├── name_recognizer.py         # EasyOCR（多言語プレイヤー名）
│   ├── action_estimator.py        # 数値変化ベースのアクション推定
│   └── diff_detector.py           # フレーム間差分検知
│
├── capture/                       # キャプチャ抽象化レイヤー
│   ├── __init__.py
│   ├── base_capture.py            # 抽象基底クラス（reconnect()含む）
│   ├── card_capture.py            # キャプチャカード（OpenCV, CAP_MSMF+MJPG）
│   ├── mss_capture.py             # mss（開発用）
│   └── file_capture.py            # ファイル読み込み（テスト用、単一/ディレクトリ対応）
│
├── profiles/                      # 座標プロファイル
│   ├── coinpoker_6max.json        # CoinPoker用（PoC検証済み、dealer_btn_1〜6全座席対応）
│   └── ggpoker_6max.json          # GGPoker用（参考保持）
│
├── solver/                        # ソルバー統合
│   ├── postflop_cli/              # Rust CLIソースコード（AGPL-v3）
│   │   ├── Cargo.toml
│   │   └── src/main.rs
│   ├── bin/
│   │   └── postflop_cli.exe       # ビルド済みバイナリ
│   ├── solver_bridge.py           # Pythonブリッジ（threading+queue方式）
│   └── README.md
│
├── strategy/                      # 戦略判断
│   ├── __init__.py
│   ├── preflop_chart.py           # プリフロップチャート参照
│   ├── solver_request_builder.py  # GameState→ソルバーJSON変換
│   ├── llm_pipeline.py            # LLMレンジ推定・搾取調整・理由生成
│   ├── multiway_engine.py         # マルチウェイ判断（eval7 + LLM）
│   ├── recommendation_engine.py   # 統合推奨エンジン（ルーティング＋制約適用＋enrichment）
│   └── baseline_ranges.json       # ベースラインレンジテーブル
│
├── preflop_charts/                # プリフロップチャートデータ
│   └── 6max_gto.json
│
├── gui/                           # GUI
│   ├── __init__.py
│   ├── main_window.py             # PyQt6メインウィンドウ（Operation/Settings/Statisticsタブ）
│   └── hud_overlay.py             # HUDオーバーレイ
│
├── core/                          # コアロジック
│   ├── __init__.py
│   ├── game_state.py              # GameState dataclass
│   ├── game_loop.py               # メインポーリングループ（QObject+moveToThread方式）
│   ├── position_calculator.py     # ポジション自動算出（座席番号降順巡回）
│   └── hand_manager.py            # ハンド開始/終了管理 + DB初期化・保存 + リプレイJSON保存
│
├── scripts/                       # 開発・デバッグ用スクリプト
│   ├── capture_dealer_btn.py      # ディーラーボタン座標キャプチャ
│   ├── measure_coordinates.py     # 座標計測ツール
│   ├── capture_hero_cards.py      # ヒーローカードキャプチャ
│   ├── test_hud_visual.py         # HUD視覚テスト
│   ├── test_llm_connection.py     # LLM接続テスト
│   ├── live_test.py               # ライブテスト実行
│   ├── analyze_screenshots.py     # スクリーンショット分析
│   ├── analyze_numbers.py         # 数値認識分析
│   └── analyze_buttons.py         # ボタン認識分析
│
├── data/                          # データ
│   └── poker_assistant.db         # SQLite DB（自動生成）
│
├── hand_replays/                  # ハンドリプレイJSON（30日自動クリーンアップ）
│   └── YYYY-MM-DD/
│
├── logs/                          # ログ（RotatingFileHandler 50MB×5）
│   └── poker_assistant.log
│
└── tests/
    ├── __init__.py
    ├── conftest.py                # pytest共通フィクスチャ
    ├── test_fixtures.py
    ├── test_setup.py
    ├── test_capture.py
    ├── test_card_recognizer.py
    ├── test_number_recognizer.py
    ├── test_button_dealer_recognizer.py
    ├── test_diff_detector.py
    ├── test_game_state.py
    ├── test_position_calculator.py
    ├── test_action_estimator.py
    ├── test_hand_manager.py
    ├── test_game_loop.py
    ├── test_name_recognizer.py
    ├── test_game_loop_integration.py
    ├── test_solver_bridge.py
    ├── test_solver_request_builder.py
    ├── test_preflop_chart.py
    ├── test_llm_pipeline.py
    ├── test_multiway_engine.py
    ├── test_recommendation_engine.py
    ├── test_recommendation_integration.py
    ├── test_hud_overlay.py
    ├── test_game_loop_hud.py
    ├── test_main_window.py
    ├── test_gui_smoke.py
    ├── test_latency.py
    ├── fixtures/
    │   ├── ground_truth/
    │   │   └── coinpoker.json
    │   ├── screenshots/
    │   │   ├── coinpoker/         # 14枚 + auto_0001〜auto_0167
    │   │   └── ggpoker/
    │   └── action_sequences/      # GameStateシーケンス（4ハンド分）
    └── integration/
        └── live_test_procedure.md

```

テンプレート画像は現在のアーキテクチャでは一切使用しない（カード認識はHSV色判定、ボタン認識はHSV色検出、シットアウト/空席は専用検出ロジック不要）。将来サイト移行でテンプレートマッチングが必要になった場合に `recognition/templates/` を追加する。

---

## 14. 設定ファイル

### 14.1 config.yaml

```yaml
capture:
  method: capture_card           # capture_card / mss / file
  device_index: 0                # キャプチャカードのデバイス番号
  width: 1920
  height: 1080
  fps: 60
  polling_interval_sec: 0.5      # ポーリング間隔（秒）

profile:
  path: profiles/coinpoker_6max.json

game:
  table_size: 6
  blind_sb: 50
  blind_bb: 100

solver:
  cli_path: solver/bin/postflop_cli.exe
  max_iterations: 200
  target_exploitability_pct: 0.5
  timeout_ms: 7000
  default_bet_sizes: "60%,a"
  default_raise_sizes: "2.5x"
  add_allin_threshold: 1.5
  force_allin_threshold: 0.15
  merging_threshold: 0.1
  rake_rate: 0.0                 # Practice Games用。リアルマネー時は0.05
  rake_cap: 0.0                  # Practice Games用。リアルマネー時は3BB相当のチップ額

llm:
  timeout_sec: 2
  retry_count: 1

hud:
  enabled: true
  font_size: 14
  opacity: 0.85

ocr:
  languages: ["en"]
  confidence_threshold: 0.4      # OCR信頼度の下限

recognition:
  diff_threshold_card: 500       # カード領域の差分検知閾値（ピクセル差分合計）
  diff_threshold_number: 300     # 数値領域の差分検知閾値
  diff_threshold_button: 200     # ボタン領域の差分検知閾値
  fold_confirm_frames: 3         # FOLD確定に必要な連続Noneフレーム数
  pot_spike_ratio: 2.0           # ポット急変の異常値判定倍率
  pot_spike_confirm_frames: 2    # ポット急変値の確定に必要な連続フレーム数

action_estimation:
  new_hand_pot_ratio: 0.3        # NEW_HAND判定: curr_pot < prev_pot × この値
  new_hand_min_pot_bb: 2         # NEW_HAND判定: prev_pot > BB × この値
  raise_threshold: 1.1           # RAISE判定: curr_bet > prev_max_bet × この値
  empty_region_std: 8            # 空領域判定: グレースケールstd < この値

logging:
  level: INFO
  max_bytes: 52428800            # 50MB
  backup_count: 5

replay:
  retention_days: 30

preflop_chart:
  path: preflop_charts/6max_gto.json
```


### 14.2 .env

```
OPENROUTER_API_KEY=sk-or-...
LLM_MODEL_DEFAULT=anthropic/claude-sonnet-4
LLM_MODEL_PREMIUM=anthropic/claude-opus-4
```

---

## 15. アンチチート対策

### 15.1 現在の構成

キャプチャカード（UGREEN）による物理映像取り込みを採用。ソフトウェアキャプチャの検知を完全に回避する。CoinPokerのAI駆動ボット検知（行動分析）への対策として、最終操作は人間が行う。

### 15.2 段階的対応（警告発生時）

**段階1:** ソルバーCLI実行ファイルを汎用名にリネーム
**段階2:** 2台PC構成に移行（HDMIスプリッター追加。キャプチャカードはそのまま使用）

```
ポーカーPC (CoinPokerのみ)
  │ HDMI出力
  ↓
[HDMIスプリッター]
  │            │
  ↓            ↓
モニター    [UGREEN キャプチャカード]
               │ USB3.0
               ↓
            分析PC（OCR + ソルバー + LLM + HUD）
```

### 15.3 プレイヤー情報の匿名化

LLM APIにプレイヤー名を送信しない。APIリクエストにはプレイヤー名の代わりに匿名化された座席識別子（"seat_2", "seat_3" 等）を使用する。

匿名化の範囲:
- LLMパイプラインへの入力: プレイヤー名 → 座席番号
- 統計データのLLMへの提示: プレイヤー名を含まず、統計値のみ（VPIP, PFR等）
- ログ出力: プレイヤー名はローカルログとDBのみに記録

DB（opponents テーブル）とリプレイJSONにはOCR取得のプレイヤー名をそのまま保存する（ローカルのみ、外部送信しない）。

---

## 16. CoinPoker固有情報

### 16.1 プラットフォーム

ライセンス: Anjouan Gaming Authority。通貨: USDT, CHP。Practice Games: 1/2、5/10チップのテーブルあり。

### 16.2 ウィンドウタイトル

形式: `NLH {テーブルID} - {SB}/{BB} ({タイムバンク秒数})`。ブラインドサイズとタイムバンク秒数を自動取得可能。

### 16.3 UI要素の表示形式

ポット表示: 「ポット○○」（緑文字＋緑チップアイコン）。スタック表示: 数字のみカンマ区切り（通貨記号なし）。ベット額表示: 数字（赤チップアイコン隣接）。ボタンラベル: 日本語（フォールド/コール/チェック/ライズ/ベット）。空席表示: 「空の」テキスト。

### 16.4 特殊機能（認識への影響）

Splash Pots（ポットサイズの急変化）は異常値フィルタで対応。Throwable Objects（演出ノイズ）は差分検知＋信頼度フィルタで対応。Bomb Pot（Double Board）とRun It Twice+1（複数ボード表示）は初期実装では非対応。

### 16.5 レーキ

ポストフロップのみ発生。レート推定5%。ソルバーの rake_rate / rake_cap パラメータに反映する。正確な値は実測で確認が必要。

### 16.6 タイマー構造

第1層: アクションタイマー15秒。第2層: タイムバンク（追加秒数の貯金、徐々に回復）。「TIME」バッジ（黄色）がタイムバンク消費開始を示す。

### 16.7 座席レイアウト（6人テーブル）

| 座席番号 | 画面位置 | 備考 |
|---------|---------|------|
| 1 | 中央下（Bottom-center） | ヒーロー（固定） |
| 2 | 右下（Lower-right） | |
| 3 | 右上（Upper-right） | |
| 4 | 上中央（Top-center） | |
| 5 | 左上（Upper-left） | |
| 6 | 左下（Lower-left） | |

---

## 17. 開発フェーズ

### Phase 1: 画面認識システム

キャプチャ抽象化レイヤー、座標プロファイル読み込み、カード認識、数値認識、ボタン検出、ディーラーボタン検出、アクション推定、ポジション自動算出、差分検知、GameState構造体、メインポーリングループ。

全認識モジュールのパラメータはPoC検証済みのため、コードを統合・リファクタリングする。

**Phase 1 完了基準:**
- Level 1テスト: カード認識100%、数値認識95%以上、ボタン検出100%
- Level 2テスト: 30分間のライブ映像でフォールバック発動率 < 5%
- GameState JSON出力が全フェーズ（waiting〜hand_end）で安定
- アクション推定が全タイプ（FOLD/CHECK/CALL/BET/RAISE/ALL_IN/BLIND）を正常検出
- ハンド境界（開始/終了）が10ハンド連続で正確に検出

### Phase 2: ソルバー統合

Rust CLI常駐ラッパーの実装・ビルド（ローカル）、Pythonブリッジ（solver_bridge.py）の実装、JSON入出力テスト、ベットサイズ動的切替。

**Phase 2 完了基準:**
- ソルバーCLI常駐プロセスの起動・停止・ヘルスチェックが安定
- solve呼び出しがフロップで7秒以内に完了（部分結果含む）
- JSON入出力の全フィールドが正常にシリアライズ/デシリアライズ
- タイムアウト時に部分結果を正しく返却

### Phase 3: LLMパイプライン

プリフロップチャートJSON整備、LLMレンジ推定プロンプト設計、ベースラインレンジテーブル作成、搾取調整、マルチウェイ判断（eval7統合）、Range文字列バリデーション。

**Phase 3 完了基準:**
- プリフロップチャートJSONが全ポジション×全ストリート分整備（2196レンジ定義）
- LLMレンジ推定の精度90%以上（100レンジ生成のうち90以上が valid）
- ベースラインレンジテーブルがDBに正常保存
- コンバイナーがベースラインレンジ＋搾取補正を正しく結合
- バリデーションが全 Range 文字列を正常にチェック

### Phase 4: HUDオーバーレイ

PyQt6透過ウィンドウ、推奨アクション表示、CoinPokerウィンドウ外側配置、ホットキー制御。

**Phase 4 完了基準:**
- HUDが推奨アクションをis_my_turn=True後1秒以内に表示（先行計算済みの場合）
- ホットキーで表示/非表示切替が動作
- CoinPokerウィンドウとの重複なし

### Phase 5: 相手統計DB統合

SQLite DB作成、ハンド終了時のリアルタイム更新、鮮度管理、統計タブUI。

**Phase 5 完了基準:**
- ハンド終了時にDB保存が安定（100ハンド連続でエラーなし）
- 統計タブにプレイヤー一覧が表示され、ソート・フィルタが動作

### Phase 6: 統合テスト・チューニング

Level 2（ライブ映像30分）、Level 3（E2Eレイテンシ P95≤7秒）テスト。回帰テスト整備。

**Phase 6 完了基準:**
- Level 2テスト: 30分間で認識エラー0件
- Level 3テスト: E2Eレイテンシ P95 ≤ 7秒
- 回帰テストが全項目PASS



---

### Phase 22: 品質向上・勝率改善（SPEC.md v1.4で追加）

Phase 21（統合テスト）完了後の改善フェーズ。実装計画書（IMPLEMENTATION_PLAN.md v1.3）のPhase 22-1〜22-5として詳細化する。

| サブフェーズ | 内容 | 依存 |
|------------|------|------|
| Phase 22-1 | リプレイJSONへの推奨・レイテンシ保存 | Phase 21完了 |
| Phase 22-2 | 30分ライブテスト + ベースライン計測 | Phase 22-1 |
| Phase 22-3 | strict JSON schema / pydanticバリデーション | Phase 22-1 |
| Phase 22-4 | プリフロップ delta policy導入 | Phase 22-3 |
| Phase 22-5 | プレイヤー名匿名化 | Phase 22-1 |

---

## 18. 将来のサイト移行対応

テンプレートとフォーマット設定をサイト別プロファイルとして分離し、座標プロファイル（profiles/）と認識パラメータの差し替えのみでサイト移行可能な設計とする。認識モジュールの抽象基底クラス（base_recognizer.py）をインターフェースとし、サイト固有の実装はサブクラスで行う。

---

## 19. 構造化データ — GameState JSON スキーマ

ポーリングループの各フレームで生成される中間データ構造。全モジュールの認識結果を統合し、戦略判断モジュールへの入力となる。

```json
{
  "timestamp": "2026-04-27T14:32:05.123Z",
  "frame_number": 1042,

  "phase": "flop",
  "hand_id": 123,

  "hero": {
    "seat": 1,
    "position": "BTN",
    "cards": ["Td", "9c"],
    "stack": 3802,
    "bet": 0,
    "is_my_turn": true
  },

  "board": ["8c", "7d", "8d"],
  "board_card_count": 3,

  "pot": 348,

  "players": {
    "2": {
      "name": "mrkrebs",
      "stack": 14439,
      "bet": 0,
      "is_seated": true,
      "in_current_hand": true
    },
    "3": {
      "name": "MilTown",
      "stack": 19890,
      "bet": 0,
      "is_seated": true,
      "in_current_hand": true
    },
    "4": {"name": null, "stack": null, "bet": 0, "is_seated": false, "in_current_hand": false},
    "5": {"name": null, "stack": null, "bet": 0, "is_seated": false, "in_current_hand": false},
    "6": {"name": null, "stack": null, "bet": 0, "is_seated": false, "in_current_hand": false}
  },

  "dealer_seat": 3,
  "active_player_count": 3,

  "buttons": {
    "fold": true,
    "call_or_check": "check",
    "raise_or_bet": "bet",
    "bet_size": 100
  },

  "actions_since_last_frame": [
    {"seat": 2, "action": "CHECK", "amount": 0, "confidence": "high"},
    {"seat": 3, "action": "CHECK", "amount": 0, "confidence": "high"}
  ],

  "hero_action": null,

  "game_event": null
}
```

**フィールド定義:**

| フィールド | 型 | 説明 |
|-----------|-----|------|
| phase | string | "waiting" / "preflop" / "flop" / "turn" / "river" / "hand_end" |
| hand_id | int or null | 現在のハンドID（グローバル連番）。waiting中はnull |
| hero.position | string or null | "BTN"/"SB"/"BB"/"UTG"/"MP"/"CO"。ディーラーボタン未検出時はnull |
| hero.is_my_turn | bool | btn_fold領域のHSV赤色検出に基づく |
| players.{seat}.is_seated | bool | stack != null（着席中） |
| players.{seat}.in_current_hand | bool | ハンド開始時にis_seated=trueだった座席。FOLD後もtrueを維持し、次NEW_HANDで再計算。ポジション割り当てはこのフィールドを使用 |
| buttons | object or null | is_my_turn=false の場合はnull |
| actions_since_last_frame | array | 前フレームからの差分で検出されたアクション（セクション4.7の出力） |
| hero_action | object or null | ヒーローがアクションを実行したフレームでのみオブジェクト値。通常のフレーム（アクション未実行）ではnull（セクション4.11の出力） |
| game_event | string or null | "NEW_HAND" / "NEW_STREET" / "BETS_COLLECTED" / null。1フレーム内で複数イベントが発生した場合は優先順位で1つのみ: NEW_HAND > NEW_STREET > BETS_COLLECTED。BLIND投入は actions_since_last_frame 配列で表現する |
| active_player_count | int | in_current_hand=true の座席数（ヒーロー含む）。ハンドに参加中のプレイヤー数を示す。FOLD後は減少する。着席中プレイヤー数（is_seated=trueの数）とは異なる |
| hero.bet | int | ヒーローの現在のベット額（チップ額）。ベットしていない場合は0 |

```

---

## 20. 設計原則

### 20.1 キャプチャ方式非依存

OCR・ソルバー・LLM・HUDのコードはキャプチャ方式（capture_card / mss / file）に依存しない。キャプチャ抽象化レイヤー（capture/base_capture.py）が統一インターフェースを提供し、config.yamlで方式を切替可能にする。

### 20.2 サイト非依存の戦略ロジック

ソルバー統合、LLMパイプライン、プリフロップチャート、エクイティ計算、DB設計はサイト非依存。サイト固有の処理は画面認識層（recognition/）と座標プロファイル（profiles/）に閉じ込める。

### 20.3 安全第一のフォールバック

誤った推奨を出すより、フォールバックまたは無推奨を選ぶ。OCR信頼度が低い場合は前回値を保持する。ソルバーがタイムアウトした場合は部分結果を使用し、部分結果もなければLLM単独判断にフォールバックする。LLM出力が不正な場合はベースラインレンジにフォールバックする。

### 20.4 リソース分離

GPU は OCR 専用（EasyOCR）。ソルバー（postflop-solver）は CPU で実行。VRAM 10GB に OCR モデル（~0.5-1GB）を配置し、ソルバーのメモリ使用量（~0.5-0.7GB）は通常RAMで処理する。

### 20.5 AGPL-v3ライセンスの取り扱い

postflop-solver はAGPL-v3ライセンスで公開されている。本システムでの取り扱い:

- postflop-solverのソースコード（`solver/postflop_cli/`）をリポジトリに同梱し、ライセンス通知（LICENSE-AGPL-v3）を配置する
- 本システム自体のライセンスはAGPL-v3の伝播範囲を考慮して決定する。postflop-solverをライブラリとして直接リンクするため、CLIラッパー（Rust部分）はAGPL-v3の影響を受ける。Python側は別プロセス通信（stdin/stdout）のため影響範囲は議論の余地がある
- リポジトリは**プライベート**とする（個人利用の検証・学習目的であり、配布しないため）
- 配布する場合はAGPL-v3に準拠しソースコード公開義務を履行する

**注意:** 将来クラウド化やマルチユーザー化（ネットワーク経由でのサービス提供）を行う場合、AGPL-v3のネットワーク利用条項によりソースコード公開義務が発生する。現時点はローカル個人利用のため問題ないが、運用形態の変更時にはライセンス影響を再検討すること。

---
## 21. 主要パラメータ一覧表

全閾値・タイムアウト値を一覧化する。「設定」列がconfig.yamlはconfig.yamlで変更可能、「固定」はコード内ハードコーディング。

**画面認識パラメータ:**

| パラメータ | 値 | 設定 | 参照セクション |
|-----------|-----|------|--------------|
| ポーリング間隔 | 0.5秒 | config.yaml | 3.3 |
| OCR信頼度閾値 | 0.4 | config.yaml | 4.4.2 |
| カード差分閾値 | 500 | config.yaml | 4.10 |
| 数値差分閾値 | 300 | config.yaml | 4.10 |
| ボタン差分閾値 | 200 | config.yaml | 4.10 |
| FOLD確定フレーム数 | 3 | config.yaml | 4.7.4 |
| ポット急変倍率 | 2.0 | config.yaml | 4.7.4 |
| 空領域std閾値 | 8 | config.yaml | 4.4.2 |
| ヒーローカードマージン | 3px | 固定 | 4.3.3 |
| ランクOCR拡大率（ヒーロー） | 5倍 | 固定 | 4.3.2 |
| ランクOCR拡大率（ボード） | 3倍 | 固定 | 4.3.2 |
| 数値OCR拡大率 | 2倍 | 固定 | 4.4.2 |

**HSV閾値（固定）:**

| 対象 | H | S | V | 参照セクション |
|------|---|---|---|--------------|
| ハート（赤） | <10 or >170 | >80 | — | 4.3.1 |
| ダイヤ（青） | 95〜140 | >70 | — | 4.3.1 |
| クラブ（緑） | 35〜85 | >50 | — | 4.3.1 |
| スペード（黒） | — | <50 | <150 | 4.3.1 |
| fold（赤） | >155 or <10 | >150 | >140 | 4.5.2 |
| call/check（緑） | 35〜90 | >150 | >100 | 4.5.2 |
| raise/bet（オレンジ） | 10〜35 | >150 | >150 | 4.5.2 |
| ポット黄色フィルタ | 15〜40 | >60 | >120 | 4.4.1 |
| ポット白色フィルタ | — | <80 | >180 | 4.4.1 |
| ディーラー赤マスク | <15 or >160 | >80 | >80 | 4.6 |
| ディーラー白マスク | — | <40 | >200 | 4.6 |
| ディーラースコア閾値 | 0.05 | — | — | 4.6 |

**アクション推定パラメータ:**

| パラメータ | 値 | 設定 | 参照セクション |
|-----------|-----|------|--------------|
| NEW_HAND ポット比率 | 0.3 | config.yaml | 4.7.2 |
| NEW_HAND 最小ポット | 2 × BB | config.yaml | 4.7.6 |
| RAISE判定倍率 | 1.1 | config.yaml | 4.7.2 |
| アクティブベットstd | >25 | 固定 | 4.5.2 |
| アクティブベットmean | >40 | 固定 | 4.5.2 |

**ソルバーパラメータ:**

| パラメータ | 値 | 設定 | 参照セクション |
|-----------|-----|------|--------------|
| 最大イテレーション | 200 | config.yaml | 5.8 |
| 目標exploitability | 0.5% pot | config.yaml | 5.8 |
| タイムアウト | 7000ms | config.yaml | 5.8 |
| デフォルトベットサイズ | 60%,a | config.yaml | 5.8 |
| デフォルトレイズサイズ | 2.5x | config.yaml | 5.8 |
| add_allin_threshold | 1.5 | config.yaml | 5.8 |
| force_allin_threshold | 0.15 | config.yaml | 5.8 |
| merging_threshold | 0.1 | config.yaml | 5.8 |
| rake_rate | 0.0 | config.yaml | 5.8 |
| rake_cap | 0.0 | config.yaml | 5.8 |

**システムパラメータ:**

| パラメータ | 値 | 設定 | 参照セクション |
|-----------|-----|------|--------------|
| LLMタイムアウト | 2秒 | config.yaml | 6.4 |
| LLMリトライ回数 | 1 | config.yaml | 6.4 |
| ログファイルサイズ | 50MB | config.yaml | 11.1 |
| ログバックアップ数 | 5 | config.yaml | 11.1 |
| リプレイ保持日数 | 30日 | config.yaml | 11.2 |
| DB鮮度閾値 | 90日 | 固定 | 8.2 |
| 統計信頼度閾値 | 10ハンド | 固定 | 4.13.3 |

**差分検知閾値の注記:**
diff_threshold_card / diff_threshold_number / diff_threshold_button の値はピクセル差分合計の絶対値であり、領域サイズに依存する。Phase 1冒頭で実際のクロップ領域サイズに対する妥当性を検証し、必要に応じてLevel 1テストで調整する。領域面積に対する正規化（diff_per_pixel = diff / area）への変更も検討候補。

**推奨制約パラメータ（固定）:**

| パラメータ | 値 | 設定 | 参照セクション |
|-----------|-----|------|--------------|
| FOLD→CHECK変換（チェック可能時） | buttons.call_or_check="check" | 固定 | 6.6.1 |
| FOLD→CHECK変換（BBリンプ時） | hero_bet >= max_opponent_bet | 固定 | 6.6.1 |
| CHECK→FOLD変換（チェック不可時） | hero_bet < max_opponent_bet | 固定 | 6.6.1 |
| CHECK→FOLD時のconfidence | "low" | 固定 | 6.6.3 |


---

### 22. モジュール間データフロー

```
capture/              recognition/           core/              strategy/         gui/
─────────            ──────────            ─────             ────────        ────
base_capture          card_recognizer        game_state         preflop_chart    main_window
  │                   number_recognizer      game_loop          llm_pipeline     hud_overlay
  │ numpy.ndarray     button_recognizer      position_calc      multiway_engine
  │ (1080p BGR)       dealer_recognizer      hand_manager       solver_bridge
  │                   action_estimator
  │                   diff_detector
  │                   name_recognizer
  │                       │
  └───────────────────────┘
          │
          │ raw frame (numpy.ndarray)
          ↓
  ┌─── recognition layer ───┐
  │ 各recognizerが領域クロップ  │
  │ + OCR/HSV判定を実行        │
  │ + diff_detectorでスキップ判定│
  └──────────┬──────────────┘
             │
             │ GameState (dict / dataclass)
             ↓
  ┌─── core layer ──────────┐
  │ game_loop:               │
  │   前回stateとの差分計算    │
  │   action_estimator呼出    │
  │   phase遷移判定           │
  │   hand_manager:           │
  │     ハンド開始/終了検知    │
  │     DB保存・リプレイ保存   │
  │   position_calculator:    │
  │     ポジション割り当て     │
  └──────────┬──────────────┘
             │
             │ GameState + actions + phase
             ↓
  ┌─── strategy layer ──────┐
  │ is_my_turn=true の場合:   │
  │   preflop → chart参照     │
  │   postflop HU → solver    │
  │   postflop MW → LLM+eval7 │
  │ → recommendation生成      │
  └──────────┬──────────────┘
             │
             │ Recommendation (action, size, reason, confidence)
             ↓
  ┌─── gui layer ───────────┐
  │ hud_overlay: 推奨表示     │
  │ main_window: 状態モニター  │
  └─────────────────────────┘
```

**モジュール間の型定義:**

| 境界 | データ型 | 内容 |
|------|---------|------|
| capture → recognition | numpy.ndarray | BGR 1920×1080 フレーム |
| recognition → core | GameState (dict) | セクション19のJSON構造 |
| core → strategy | GameState + phase + actions | 判断に必要な全情報 |
| strategy → gui | Recommendation | action, size, reason, confidence |
| core → data | HandResult | DB保存用、リプレイJSON |

---

### 23. 起動・終了シーケンス

#### 23.1 起動シーケンス

```
1. config.yaml 読み込み
2. .env 読み込み（APIキー）
3. ログ初期化（RotatingFileHandler）
4. SQLite DB 接続（data/poker_assistant.db、なければ自動作成）
5. 座標プロファイル読み込み（profiles/coinpoker_6max.json）
6. EasyOCR Reader 初期化（GPU、languages=config.ocr.languages）
   ※ 初回起動時モデルダウンロード 〜数分
   ※ 単一インスタンスをシングルトンとして管理（recognition/__init__.py）
   ※ card_recognizer, number_recognizer, name_recognizer が共有
7. ソルバーCLI常駐プロセス起動（solver/bin/postflop_cli.exe）
   ※ 起動確認: stderrに "ready" 出力を待つ。5秒タイムアウトでエラー
8. キャプチャデバイス初期化（config.capture.method に応じて）
   ※ キャプチャカード: config.capture.device_index でオープン
   ※ 自動検出（GUIの設定タブ「自動検出」ボタン使用時）:
      0-9を順に試行し、1920×1080@60fpsで開けたデバイスを採用
      成功したデバイス番号をconfig.capture.device_indexに保存
9. GUI メインウィンドウ表示
10. HUD オーバーレイ初期化（非表示状態で待機）
11. [ユーザーがSTARTボタンを押す]
12. ポーリングループ開始（別スレッド）
```

#### 23.2 終了シーケンス

```
1. [ユーザーがSTOPボタンを押す] or [ウィンドウ閉じる]
2. ポーリングループ停止
3. 進行中のハンドがあれば hand_end 処理（DB保存 + リプレイ保存）
4. ソルバーCLI常駐プロセス停止（process.terminate()）
5. キャプチャデバイス解放（cap.release()）
6. DB接続クローズ
7. ログフラッシュ
8. GUI終了
```

#### 23.3 ソルバーのヘルスチェック

ポーリングループ内で、ソルバー呼出し前にプロセスの生存を確認する:

```python
if self.solver_bridge.process.poll() is not None:
    logger.warning("Solver process died, restarting...")
    self.solver_bridge.start()
```

再起動失敗が3回連続した場合はソルバーを無効化し、LLM単独判断モードに切り替える。HUDに「ソルバー無効」の警告を表示する。

#### 23.4 GUIとポーリングループの関係

メインウィンドウとHUDオーバーレイは同一プロセス内（PyQt6のメインスレッド）で動作する。ポーリングループは別スレッド（QThread）で実行し、シグナル/スロット機構でGUIスレッドに結果を通知する。ソルバーCLIは別プロセス（subprocess.Popen）。

```
[メインプロセス]
  ├── メインスレッド: PyQt6 GUI (main_window + hud_overlay)
  ├── ポーリングスレッド (QThread): capture → recognition → core → strategy
  └── リプレイ保存スレッド (QThread): 非同期JSON書き込み

[別プロセス]
  └── postflop_cli.exe: ソルバー常駐プロセス（stdin/stdout JSON通信）
```


## 付録A: PoC検証結果サマリー

| PoC | 最終精度 | 備考 |
|-----|---------|------|
| カード認識（通常画面） | 25/25 = 100% | v1(39%)→v2(93%)→v3(100%) |
| 数値認識 | 104/104 = 100% | v1→v2(85%)→v3→v4(100%) |
| 自分ターン検出 | 8/8 = 100% | HSV色検出 |
| ボタン種別 | 9/9 = 100% | HSV色分類＋文脈判定 |
| ディーラーボタン（通常画面） | 6/6 = 100% | 赤+白ピクセルスコアリング |
| アクション推定 | 全タイプ検出確認 | 167フレーム、0.15秒/フレーム |
| バッジ検出 | **不要** | 数値変化＋ディーラーボタンで代替 |
| シットアウト/空席 | **専用ロジック不要** | stack=None / カード不在で対応 |

## 付録B: 確定済みスクリーンショットの注意事項

cp_03（`cp_03_flop_my_turn.png`）とcp_04（`cp_04_flop_not_my_turn.png`）はファイル名と実際の画面内容が逆転している。cp_03は実際にはプリアクション表示（自分の番でない）、cp_04は実際には自分の番（アクションボタン表示）。テストコードではこの逆転を考慮した正解データを使用すること。

## 付録C: 開発体制

司令塔AI（Claude Opus 4.6）が仕様書・タスク指示・アーキテクチャ判断を行い、実装役AI（Codex）が具体的なコード実装を行う分業体制。CodexはPR単位で完結するタスクを実行する。Rust CLIラッパーの実装・ビルドはローカル環境で行い、Codexはsolver_bridge.py等のPython側のみを担当する。

AGENTS.mdにCodex向け開発ルール（コーディング規約、テスト要件、PR粒度等）を記載し、リポジトリルートに配置する。

これらはPhase 1（画面認識）およびPhase 2（ソルバー統合）の実装をブロックしないため、意図的に遅延させている。

## 付録D: 将来対応項目

以下の項目はSPEC.md v1.2時点で意図的に未定義とし、該当Phaseの直前に詳細化する。

**Phase 3直前に詳細化:**
- プリフロップチャートの全パターン網羅（3bet/4bet/squeeze/cold-call/limp/スタック別調整）
- LLMプロンプトテンプレート、出力JSON Schema、トークン上限、モデル切替条件（「重要局面」の具体的定義）
- eval7マルチウェイの人数制限とサンプル数調整
→ IMPLEMENTATION_PLAN.md Phase 14.5（LLM仕様詳細化）で実施。Phase 10a完了後に着手する。


**Phase 4直前に詳細化:**
- ベットサイズプリセット（33%/50%/75%/100%、2X/2.5X/3X/4X）の認識。初期実装では推奨額（チップ額）のみ表示し、ユーザーが手動でプリセットを選ぶ

**Phase 1実装中に確定:**
- 差分検知閾値の領域サイズに対する正規化方式
- テスト用正解データの配置場所（推奨: `tests/fixtures/ground_truth/coinpoker.json` にJSON形式で集約）

**報告書から評価済みだが現段階では見送る項目:**

以下の項目は外部レビュー（2026-05-01）で提案されたが、現時点のシステム成熟度（ライブテスト未完了、推奨精度の定量データなし）では時期尚早と判断し見送った。データ蓄積後に再評価する:

- HU四層分離（Range Forecaster → Solver → Bounded Exploiter → Judge Gate）: 現行のrecommendation_engine.py内部で役割分担済み。モジュール分割はボトルネック特定後に実施
- マルチウェイ policy retrieval + rollout: multiway_engine.pyの全面刷新。現行eval7+LLMの精度測定が先
- Bounded exploit のKL距離制限: 数学的には正しいが検証データが不足
- SFT / judge model / self-play loop: 学習データの蓄積（数百ハンド）が前提
- バッチラベリング（bunching_label_job.py）: 3.42GB RAM使用、過剰
- 小型モデルSFT / ローカルLLM: RTX 3080のVRAMがOCRで使用中、API中心を維持
- offline_onlyフラグ / コンプライアンスhardening: SPEC.md 1.2「検証・学習用途」で定義済み


---
