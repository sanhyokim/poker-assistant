# ポーカーAIアシスタントシステム — SPEC.md
**Version:** 3.4  
**Updated:** 2026-05-20 JST  
**Purpose:** 現在の正仕様のみを記載する。過去の経緯・判断理由・採用しなかった案は `DESIGN_NOTES.md`、現在地点と次タスクは `snapshot.md` に分離する。

---

## 1. システム概要

### 1.1 目的

本システムは、オンラインポーカーにおいて、キャプチャ映像からリアルタイムに画面状態を認識し、現在局面に対する信頼できる推奨アクションをHUDに表示する判断支援システムである。

最重要目的は、**ポーカーで勝率を上げるための正しい推奨サインを出すこと**である。

最終操作は必ず人間が行う。  
本システムは自動操作を行わない。

現在の開発・検証対象は CoinPoker 6max NLH cash table である。  
ただし将来的には、他オンラインポーカー環境にも対応できるよう、CoinPoker固有処理と汎用ロジックを分離する。

目的は以下。

```text
- 画面状態を構造化してGameStateとして管理する
- CoinPoker固有の認識処理をSite Adapter層に閉じ込める
- GameState層はサイト非依存のポーカー状態として扱う
- プリフロップはチャートを主軸に判断する
- HUポストフロップはSolverを主軸に判断する
- Multiwayポストフロップはeval7 + LLM + 数理ガードで判断する
- 相手ALL-IN対応はSolver可能ならSolver、不可ならequity / pot odds数理避難路を使う
- GameStateが安定していない場合は推奨を出さない
- 推奨は古い文脈で表示しない
- HUDには確定した推奨だけを表示する
- 処理中・待機中は推奨ではなく状態のみ表示する
```

本システムの最重要原則は「速く何かを出すこと」ではなく、勝率向上のために正しい文脈の推奨だけを表示することである。

---

### 1.2 利用条件

本システムは検証・学習用途として開発する。

以下は禁止する。

```text
- 自動クリック
- 自動ベット
- 自動フォールド
- CoinPokerクライアントへの直接操作
- 人間の操作を代替する処理
```

HUD表示は判断支援であり、最終決定・最終操作はユーザーが行う。

---

### 1.3 対象プラットフォーム

現在の検証対象:

```text
CoinPoker デスクトップクライアント
6max NLH cash table
日本語UI
4色デッキ設定
Windows 10/11
```

将来方針:

```text
- CoinPoker以外のオンラインポーカー環境にも対応できる設計を目指す
- サイト固有の座標・UI・演出・Fold badge・dealer button・bet/stack OCRはSite Adapter層に分離する
- GameState / Decision Engine / HUDは特定サイトに依存させない
```

現在の前提:

```text
- CoinPoker UIは最大化または固定サイズで運用する
- 座標プロファイルは profiles/coinpoker_6max.json を正とする
- UI変更や解像度変更があった場合は座標再調整が必要
```
---

### 1.4 用語集

| 用語 | 定義 |
|---|---|
| NLH | ノーリミットテキサスホールデム |
| GTO | Game Theory Optimal |
| HU | Heads-Up。残り2人の状態 |
| Multiway | 3人以上が参加している状態 |
| SPR | Stack-to-Pot Ratio |
| RFI | Raise First In |
| 3bet | 最初のレイズに対するリレイズ |
| 4bet | 3betに対するリレイズ |
| cbet | Continuation Bet |
| VPIP | Voluntarily Put Money In Pot |
| PFR | Pre-Flop Raise |
| HUD | Heads-Up Display |
| GameState | 画面認識結果を構造化した現在状態 |
| PlayerState | 各seatの状態 |
| ActionRecord | 検出されたアクション |
| StreetActions | street単位のアクション履歴 |
| Recommendation | 推奨アクション |
| Visual Obstruction | ウィンドウ被り・演出・遮蔽などによる一時的な視覚ノイズ |
| Showdown Guard | river/showdown中の誤Fold/NO_CARD抑制ガード |
| stale recommendation | 計算開始時と返却時で文脈が変わった古い推奨 |
| phase fast-forward | hand start時にboard枚数からflop/turn/riverへ進める処理 |
| suppress_phase_fast_forward | hand_end直後などにfast-forwardを抑制するGameStateフラグ |

---

## 2. 技術スタック

### 2.1 実行環境

| 項目 | 内容 |
|---|---|
| OS | Windows 10/11 |
| Python | 3.11系 |
| GPU | NVIDIA RTX 3080想定 |
| OCR | EasyOCR GPU mode |
| GUI | PyQt6 |
| DB | SQLite |
| Capture | HDMIキャプチャカード / mss / file入力 |
| Timezone | Asia/Tokyo |

---

### 2.2 主要ライブラリ

| 用途 | ライブラリ |
|---|---|
| 画像処理 | OpenCV |
| OCR | EasyOCR |
| エクイティ計算 | eval7 |
| GUI | PyQt6 |
| DB | SQLite |
| HU/Multiway推論 | Deep CFR (PyTorch) |
| Solver連携 | Rust postflop CLI（廃止予定） |
| LLM | OpenRouter API（exploit補正用途のみ） |
| テスト | pytest |


---

### 2.3 外部依存

外部依存:

- OpenRouter API（exploit_adjustment用途のみ）
- Deep CFR訓練済みモデル（ローカル .pt ファイル）
- CoinPokerデスクトップクライアント
- キャプチャカードまたは画面キャプチャ

廃止予定:

- postflop-solver Rust CLI（Deep CFR統合完了後に廃止）


LLM APIキー等の秘匿情報は `.env` で管理する。  
config値は `config.yaml` を正とする。

---

### 2.4 開発・テスト環境

開発時の基本コマンド:

```powershell
pytest -q
pytest tests/test_game_loop.py -q
pytest tests/test_hand_manager.py -q
pytest tests/test_game_loop_recommendation.py -q
pytest tests/test_main_window.py -q
pytest tests/test_hud_overlay.py -q
pytest tests/test_recommendation_engine.py -q
pytest tests/test_solver_bridge.py -q
```

現在の期待テスト結果:

```text
1282 passed, 7 warnings
```

---

## 3. 全体アーキテクチャ

### 3.0 アーキテクチャ原則

本システムは以下の層に分離する。

```text
1. Site Adapter層
   - CoinPoker固有の座標・UI認識
   - Fold badge
   - dealer button
   - bet / stack / pot OCR領域
   - アニメーション・遮蔽・残像ガード
   - サイト別profile管理

2. GameState層
   - hand_id
   - phase
   - players_in_hand
   - actions
   - pot
   - position
   - hero cards
   - board
   - サイト非依存のポーカー状態

3. Decision Engine層
   - Preflop Chart
   - HU Postflop Solver
   - Multiway eval7 + LLM + 数理ガード
   - all-in pot odds / equity避難路
   - GameStateが安定している場合のみ実行

4. HUD層
   - 確定推奨表示
   - Solver / LLM / Chart 処理中表示
   - WAITING / PRE-HAND / UNSTABLE 表示
   - 推奨ではない状態表示とRecommendation表示を明確に分ける
```

禁止:

```text
- CoinPoker固有の例外処理をDecision Engineへ直接混ぜること
- 認識層の揺れをHUD表示補正だけで隠すこと
- GameStateが不安定なままSolver / LLM / Chartへ渡すこと
- 局所症状ごとのguardを無制限に増やすこと
```

### 3.1 全体フロー

```text
キャプチャカード入力（OpenCV / mss / file）
↓
差分検知（前フレームと比較、変化なしなら重い処理を抑制）
↓
座標プロファイルに基づき各領域をcrop
↓
┌─────────────┬─────────────┬──────────────┐
│ カード認識  │ 数値認識    │ UI認識        │
│ HSV + OCR   │ EasyOCR GPU │ HSV色検出     │
└──────┬──────┴──────┬──────┴───────┬──────┘
       └─────────────┴─────────────┘
↓
GameState構築
↓
状態安定化・遮蔽保護
- Visual Obstruction Guard
- Showdown Guard
- stale Heroカード開始抑制
- stale Heroカード抑制解除
- Heroカード連続一致確認
- active hand中のHeroカード矛盾検出
- Heroカード不安定handの推奨停止・DB保存除外
- 新ハンド開始ガード
- 参加者観察窓
- Rejoin復活判定
- Hero Fold badge ignore latch
- Hero CHECK誤保存の短時間置換
- phase / board_count 整合ガード
- Recommendation context snapshot
- stale推奨破棄
- pot spike hold中のstrategy defer
- hand_end直後・stale解除直後のphase fast-forward抑制
- 途中離席・Stop・capture lost・table invisible handの保存除外
- Hero action保存経路の一元化
↓
局面判定
↓
┌──────────────┬────────────────────┬────────────────────┐
│ Preflop      │ HU Postflop        │ Multiway Postflop   │
│ Chart        │ Deep CFR推論       │ Deep CFR推論        │
│ + DB補正     │ + exploit補正      │ + eval7補助         │
└──────────────┴────────────────────┴────────────────────┘
↓
推奨contextが現在GameStateと一致する場合のみ採用
↓
HUD表示

↓
人間が操作
↓
hand_end検知
↓
DB保存 + replay JSON保存
↓
waitingへ戻る
```

---

### 3.2 キャプチャ構成

本番想定:

```text
PC（CoinPoker実行）
↓ HDMI
キャプチャカード
↓ USB
同じPC上のPython/OpenCV
```

開発時は以下も使用可能。

```text
- mss
- file入力
- スクリーンショット
```

キャプチャ方式は `config.yaml` で切り替える。  
OCR・Solver・LLM・HUDはキャプチャ方式に依存しない設計とする。

---

### 3.3 ポーリングループ

基本ループ:

```text
capture frame
↓
diff check
↓
recognition
↓
GameState build
↓
HandManager process_frame
↓
GameLoop strategy handling
↓
HUD update
↓
DB/replay save if hand ended
```

推奨生成は毎フレーム行わない。  
Hero turn中かつ必要な局面でのみ実行する。

---

### 3.4 時間制約

CoinPokerのアクションタイマーに間に合うよう、推奨は可能な限り数秒以内に表示する。

ただし、以下を優先する。

```text
1. 古い推奨を出さない
2. 不整合なGameStateで推奨を作らない
3. 未確定の暫定推奨を出さない
4. Solver中も画面認識を止めない
```

HU Solverは局面により10〜22秒以上かかる可能性がある。  
そのため、HU postflop SolverはGameLoopをブロックしない非同期workerで実行する。

---

### 3.5 処理中表示方針

処理中は、未確定の推奨Actionを表示してはならない。

表示してよいのは処理状態だけである。

```text
CHART CHECKING...
SOLVER THINKING...
LLM ANALYZING...
WAITING FOR STABLE POT...
HERO CARDS UNSTABLE
Computing...
PRE-HAND
WAITING FOR STABLE HAND
DEEP SPR FLOP SOLVING
SOLVER STILL RUNNING
SOLVER INPUT UNSTABLE
```
`WAITING FOR STABLE HAND` は、新hand開始直後・participant observation中・preflop入力不安定時に推奨を出さない状態を表す。  
`DEEP SPR FLOP SOLVING` は、deep-SPR flopでSolverが計算中であり、まだ信頼できる推奨がない状態を表す。  
`SOLVER INPUT UNSTABLE` は、Solverへ渡すGameState / action / position / stack / potが不安定なためSolverを起動しない状態を表す。

これらは推奨Actionではない。  
HandManagerへRecommendationとして保存してはならない。

`WAITING FOR STABLE POT...` は、pot spike hold中にstrategy計算を保留している状態を表す。  
`HERO CARDS UNSTABLE` は、Heroカードが不安定または矛盾しているため推奨を停止している状態を表す。

これらは推奨Actionではなく、処理・安全停止状態の表示である。


禁止:

```text
- 暫定推奨
- timeout時のNO SIGNAL推奨
- fallbackを古い文脈で表示
- 後から推奨を上書き
```

HUD側の処理中表示メソッド:

```python
def show_computing(self, message: str = "Computing...") -> None:
    ...
```

---

### 3.6 非同期Solver方針

HU postflopではSolverを非同期workerで実行する。

基本フロー:

```text
Hero turn中
↓
HU postflop判定
↓
GameState安定性チェック
↓
Recommendation context snapshot作成
↓
Solver request完全JSON保存
↓
daemon worker threadでSolver実行
↓
GameLoopは継続して画面認識
↓
毎フレームpending resultをpoll
↓
Solver返却
↓
request_id / active_id / cancelled / context鮮度確認
↓
有効なら採用
↓
無効なら破棄
```

GameLoopはpending stateを持ち、workerは共有result/errorを直接上書きしない。  
完了結果は `request_id` 付きで `_pending_recommendation_completed` に格納する。

pending recommendation cancel条件:

```text
- NEW_HAND
- NEW_STREET
- waiting遷移
- hand_end
- Hero turn終了
- Heroがhand外へ出た
- hand_id変化
- phase変化
- board変化
- board_count変化
- active_player_count変化
- actions_count変化
- hero_is_my_turn変化
- hero_in_current_hand変化
```

古いSolver結果は採用しない。

さらに、Solver CLIがtimeout / cancel / orphan状態で裏に残ると次requestを詰まらせるため、以下の場合はRust Solver processをresetする。

```text
- Solver timeout
- Hero turn終了でSolverが不要化
- street変更でSolverが不要化
- hand_end / waiting遷移
- orphan worker検出
```

注意:

```text
- Python threadを強制killしない
- 不要化したpostflop_cli.exe processはresetする
- timeout / solver_input_unstable はRecommendationとして保存しない
- process resetは毎requestではなく、不要化・timeout・orphan時のみ行う
```

---

## 4. データ構造

### 4.1 GameState

`GameState` は、1フレーム時点の画面認識結果と、GameLoop / HandManager間で必要な制御情報を保持する。

主要フィールド:

```python
hand_id: int | None
phase: str
board: list[str]
board_card_count: int
pot: int
hero: PlayerState
players: dict[str, PlayerState]
actions_since_last_frame: list[ActionRecord]
current_street_actions: list[ActionRecord]
preflop_actions: list[ActionRecord]
game_event: str | None
table_visible: bool
suppress_phase_fast_forward: bool = False
strategy_defer_reason: str | None = None
hero_cards_unstable_reason: str | None = None
```

`phase` の主な値:

```text
waiting
preflop
flop
turn
river
hand_end
```

---

### 4.2 PlayerState

`PlayerState` は各seatの状態を表す。

主要フィールド:

```python
seat: int
cards: list[str] | None
cards_visible: bool
stack: int | None
bet: int
is_seated: bool
in_current_hand: bool
name: str | None
is_my_turn: bool
```

重要:

```text
cards_visible は観測値
in_current_hand はハンド参加状態
```

この2つを同一視してはならない。  
一時的なNO_CARDだけで参加中seatを即 `in_current_hand=False` にしてはならない。

---

### 4.3 ActionRecord

`ActionRecord` は検出されたアクションを表す。

```python
seat: int
action: str
amount: int
confidence: str
```

action種類:

```text
FOLD
CHECK
CALL
BET
RAISE
ALL_IN
BLIND_SB
BLIND_BB
```

amount単位:

```text
チップ額
```

RAISEのamount:

```text
to-bet方式
```

例:

```text
相手BET 100
Hero RAISE TO 300
→ amount=300
```

---

### 4.4 Recommendation

`Recommendation` はHUD表示・DB保存・followed判定に使う推奨結果である。

主要フィールド:

```python
action: str
amount: int
confidence: str
source: str
reason: str
metadata: dict
```

source例:

```text
chart
solver
llm_multiway
fallback
```

重要:

```text
stale contextのRecommendationはHUD表示・previous保存・HandManager保存してはならない。
```

---

### 4.5 StreetActions

`StreetActions` はstreet単位のアクション履歴である。

```python
street: str
actions: list[ActionRecord]
```

対象street:

```text
preflop
flop
turn
river
```

Multiway LLMやSolver inputでは、現在streetの累積アクション履歴を参照する。

---

### 4.6 DB保存対象データ

DB保存対象:

```text
- hand_id
- start/end timestamp
- hero cards
- board
- participants
- participated_seats
- street actions
- recommendation
- human action
- followed_recommendation
- result
```

参加者保存は、単なる最終 `in_current_hand` ではなく、ハンド中に参加実績のある `_participated_seats` を基準にする。

---

### 4.7 Replay JSON

replay JSONには、後から監査・再現できる情報を含める。

含めるべきもの:

```text
- hand_id
- hero cards
- board
- actions
- street actions
- seat_to_name
- participated_seats
- db_participant_names
- recommendation
- human action
- GameState snapshot
```

---

### 4.8 追加制御フィールド

#### 4.8.1 suppress_phase_fast_forward

```python
suppress_phase_fast_forward: bool = False
```

目的:

```text
hand_end直後・stale Heroカード抑制解除直後など、前ハンドboard残像の可能性がある新ハンド開始時に、HandManagerのphase fast-forwardを抑制する。
```

使用箇所:

```text
GameLoop:
  waiting中の新ハンド開始候補で、前ハンド情報が残っている場合に True をセットする。

HandManager._start_new_hand():
  suppress_phase_fast_forward=True かつ board_count>=3 の場合、preflop開始のまま維持する。
```

注意:

```text
- UI表示用ではない
- Solver inputとして直接使わない
- DB保存の主目的ではない
```

---

#### 4.8.2 current_street_actions

`current_street_actions` は現在streetの累積アクション履歴を表す。

目的:

```text
- Multiway LLMに現在streetの文脈を渡す
- BETとCALLが別フレームに分かれても文脈を保持する
- full_street_actions_countのログ確認に使う
```

---

#### 4.8.3 table_visible

`table_visible` はテーブルが視覚的に認識可能かを表す。

用途:

```text
- テーブル非表示時の誤認識抑制
- UIでCLOSED表示
- table visibility復帰直後の新ハンド開始ガード
```
#### 4.8.4 strategy_defer_reason

```python
strategy_defer_reason: str | None = None
```

目的:

```text
現在フレームでstrategy計算を走らせてはいけない理由を表す。
```

現在使用する値:

```text
pot_spike_hold
```

`pot_spike_hold` の意味:

```text
ActionEstimatorがpot spikeを検出し、potを前回値に一時保持している。
この状態では、actionだけが先に反映され、potが古いままになる可能性がある。
```

挙動:

```text
- Chart / Solver / LLM requestを開始しない
- pending recommendationをclear/cancelする
- cached recommendationを破棄する
- HUDには WAITING FOR STABLE POT... を表示する
- GameLoop自体は止めない
- 次フレームでpot confirmedされれば通常のstrategy処理へ戻る
```

禁止:

```text
pot_spike_hold中に古いpotと新しいBET/ALL_INを組み合わせてSolver/LLMへ渡してはならない。
```

---

#### 4.8.5 hero_cards_unstable_reason

```python
hero_cards_unstable_reason: str | None = None
```

目的:

```text
Heroカードが不安定・矛盾しており、そのframeまたはhandで推奨を出してはいけない理由を表す。
```

使用する値:

```text
hero_cards_waiting_unstable
hero_cards_changed_during_active_hand
hero_cards_changed_after_recommendation
```

挙動:

```text
- Chart / Solver / LLM requestを開始しない
- pending recommendationをclear/cancelする
- cached recommendationを破棄する
- HUDには HERO CARDS UNSTABLE を表示する
- active hand中に矛盾が確定した場合は abandon_current_hand("hero_cards_unstable") でDB/replay/stats保存しない
```

禁止:

```text
active hand中に一時的に読めた別Heroカードで、確定済みHeroカードを即上書きしてはならない。
```


---

## 5. 画面認識

### 5.1 座標プロファイル

座標プロファイルは `profiles/coinpoker_6max.json` を正とする。

矩形形式:

```json
{
  "hero_card_1": {"x": 859, "y": 755, "w": 41, "h": 81}
}
```

キーは `w` / `h` を使用する。  
`width` / `height` は使用しない。

対象領域:

```text
- hero cards
- board cards
- pot
- hero stack
- player stacks
- hero bet
- player bets
- dealer button
- action buttons
- player names
- seat card regions
- fold badge regions
```

UI変更や解像度変更が発生した場合、座標再調整が必要。

---

### 5.2 カード認識

#### 5.2.1 スート判定

4色デッキをHSVで判定する。

| スート | 色 | 判定方針 |
|---|---|---|
| ♥ | 赤 | H<10 or H>170, S高 |
| ♦ | 青 | H=95〜140, S高 |
| ♣ | 緑 | H=35〜85, S中以上 |
| ♠ | 黒 | S低, V低 |

白背景は除外する。

---

#### 5.2.2 ランクOCR

ランク領域を切り出し、二値化・拡大後にEasyOCRで読む。

許可文字:

```text
0123456789AJQKT
```

正規化例:

```text
10 / 1O / IO / I0 → T
0 → T
O → Q
I → J
```

---
#### 5.2.3 ヒーローカードキャッシュ・安定化

Heroカードは、勝率判断の最重要入力である。

そのため、1フレームだけのOCR結果を即採用してはならない。

---

##### waiting中のHeroカード確定

waiting中にHeroカードが読めた場合、同じ2枚が連続して一定フレーム数読めた場合のみ、新hand開始候補として採用する。

デフォルト:

```text
recognition.hero_card_confirm_frames = 2
```

挙動:

```text
1フレーム目:
  candidateとして保持
  hand開始しない

2フレーム目以降:
  同一カードが連続した場合のみstable扱い
  hand開始候補として使う
```

途中で別カードに変わった場合:

```text
candidateを差し替え
streakを1へ戻す
hand開始しない
```

missing / None が含まれる場合:

```text
candidateをクリア
hand開始しない
```

Visual Obstruction中 / recovery中:

```text
candidateを採用しない
hand開始しない
```

ログ例:

```text
Waiting hero cards candidate: ['Qd', 'Ac'] streak=1/2
Waiting hero cards stable: ['Qd', 'Ac'] streak=2/2
```

---

##### active hand中のHeroカード再検証

active hand中は、確定済みHeroカードを `_cached_hero_cards` として使う。

ただし、演出・遮蔽・相手アクション連打によりHeroカードOCRが揺れる可能性があるため、active hand中もfresh OCRを行い、矛盾検出だけ行う。

重要:

```text
- fresh OCR結果でHeroカードを即上書きしない
- cached Heroカードとfresh OCRが違っても1回では破棄しない
- 同じ矛盾が一定回数連続した場合のみHeroカード不安定と判定する
```

デフォルト:

```text
recognition.hero_card_mismatch_confirm_frames = 2
```

矛盾候補ログ:

```text
Hero cards mismatch candidate: cached=['Qd', 'Ac'] fresh=['Qd', '4c'] streak=1/2 phase=preflop
```

矛盾確定ログ:

```text
Hero cards invalidated for hand: cached=['Qd', 'Ac'] fresh=['Qd', '4c'] reason=hero_cards_changed_during_active_hand
```

Visual Obstruction中 / recovery中:

```text
Heroカード矛盾判定を行わない
mismatch streakを増やさない
```

---

##### Heroカード不安定時の扱い

Heroカード不安定が確定したhandでは、誤ったHeroカードで推奨を出す危険があるため、以下を行う。

```text
- Chart / Solver / LLM requestを開始しない
- pending recommendationをclear/cancelする
- cached recommendationを破棄する
- HUDに HERO CARDS UNSTABLE を表示する
- active handは abandon_current_hand("hero_cards_unstable") で破棄する
- DB保存しない
- replay保存しない
- opponent stats更新しない
```

推奨保存後にHeroカード矛盾が確定した場合:

```text
reason = hero_cards_changed_after_recommendation
```

推奨前にHeroカード矛盾が確定した場合:

```text
reason = hero_cards_changed_during_active_hand
```

禁止:

```text
- active hand中にHeroカードを自動上書きすること
- Heroカード不安定handをDB統計に入れること
- Heroカード不安定中にfallback推奨を出すこと
```

hand_end / waiting遷移時にはHeroカードキャッシュとactive hand用の矛盾状態をリセットする。

---

### 5.3 数値認識

#### 5.3.1 ポットOCR

ポット表示では、ラベル色を除外し、数字部分のみを読む。

pot値が急増した場合は、OCR誤読の可能性がある。

既存方針:

```text
1フレームだけの急増:
  前回値を保持

2フレーム連続の急増:
  実変化として採用候補
```

ただし、巨大potがNEW_HAND誤検出に繋がる可能性があるため、次回ライブでも監視対象。

---

#### 5.3.2 スタックOCR

スタックOCRは、空領域・暗転・一時的なNoneを考慮する。

方針:

```text
stack=None 1フレーム:
  OCR失敗として保持

stack=None 2フレーム:
  WARNING候補

stack=None 3フレーム:
  離席/表示消失候補
```

FOLD確定はstackだけでは行わない。  
カード有無・FoldBadge・action履歴と併せて判断する。

---

#### 5.3.3 BET額OCR正規化

BET額OCRでは、小数点・カンマ・桁ズレを明示的に扱う。

ルール:

```text
"1,980"  → 1980
"1980.4" → 1980
"595.2"  → 595
```

禁止:

```text
"1980.4" → 19804 として扱うこと
"595.2"  → 5952 として扱うこと
```

---

#### 5.3.4 suspicious金額ガード

小数点欠落・桁ズレ疑いがある値は `suspicious=True` とする。

suspicious時:

```text
- WARNINGログ
- confidence="low"
- ALL_IN再分類しない
- Safety Guardの巨大bet扱いに使わない
- 金額を自動補正して確定しない
```

通常額はsuspiciousにしてはならない。

通常額例:

```text
50
100
200
448
1100
1600
```

---

#### 5.3.5 pot spike holdとstrategy defer

potが急増した場合、OCR誤読またはチップアニメーションの可能性がある。

通常のpot spike処理:

```text
1フレーム目:
  potを前回値にhold
  pot_spike_hold=True

2フレーム目:
  同じ急増が継続した場合、実変化としてconfirmed
  pot_spike_hold=False
```


`pot_spike_hold=True` のframeでは、actionだけが先に反映され、potが古いままになる可能性がある。

例:

```text
pot=314
BET=13820
SPR=9768.0
```

この状態でSolver / LLMへ渡してはならない。

GameState:

```python
strategy_defer_reason = "pot_spike_hold"
```

GameLoopの挙動:

```text
- Chart / Solver / LLM requestを開始しない
- pending recommendationをclear/cancelする
- cached recommendationを破棄する
- HUDに WAITING FOR STABLE POT... を表示する
- action記録自体は止めない
- pot confirmed後に通常のstrategy処理へ戻る
```

suspicious 10x OCR spikeの場合:

```text
完全ignore扱い
pot_spike_hold=False
strategy deferしない
```

理由:

```text
10倍桁ズレ疑いは実変化としてconfirmさせず、前回potを維持するため。

```

---


### 5.4 ボタン検出

#### 5.4.1 自分ターン判定

自分ターン判定は、fold赤色とcall/check緑色の二重確認で行う。

```text
fold_is_red and call_is_green
→ is_my_turn=True

fold_is_red only
→ is_my_turn=False
```

理由:

```text
チップ演出などでfold領域だけ赤く見える誤検出を防ぐため。
```

---

#### 5.4.2 ボタン種別分類

| ボタン | 色 | 文脈 |
|---|---|---|
| fold | 赤 | 常にfold |
| call/check | 緑 | アクティブbetあり→call、なし→check |
| raise/bet | オレンジ | アクティブbetあり→raise、なし→bet |

---

### 5.5 ディーラーボタン検出

ディーラーボタンは赤＋白ピクセルのスコアリングで判定する。

```text
red_ratio * 0.7 + white_ratio * 0.3
```

最もスコアが高いseatをdealer seatとする。

---

### 5.6 SeatCardDetector

#### 5.6.1 検出方式

相手seatのカード領域を、以下の複合条件で判定する。

```python
has_card = (
    edge_density >= card_edge_threshold
    and gray_mean >= card_gray_mean_min
    and gray_std >= card_gray_std_min
)
```

デフォルト閾値:

| パラメータ | config key | 値 |
|---|---|---|
| edge density | recognition.card_edge_threshold | 0.02 |
| gray mean | recognition.card_gray_mean_min | 80.0 |
| gray std | recognition.card_gray_std_min | 20.0 |

---

#### 5.6.2 役割

SeatCardDetectorの役割:

```text
- 相手seatのcards_visible観測
- ハンド開始時の参加者判定材料
- 参加者観察窓での参加者昇格材料
- FoldBadgeDetector / ActionEstimatorの補助情報
- Visual Obstruction Guardの検出材料
```

SeatCardDetector単独でFOLD確定しない。

---

#### 5.6.3 参加者観察窓

ハンド開始直後は、1フレームだけで参加者を確定しない。

観察窓:

```text
participant_observation_duration_sec = 1.5
```

観察窓中、以下を満たしたseatを参加者とする。

```text
- cards_visible == True
- bet > 0
- BET / CALL / RAISE / ALL_IN / BLIND_SB / BLIND_BB
```

観察窓終了後はlate recoveryしない。

---

#### 5.6.4 _seat_card_confirmed

ハンド中にカード検出が安定したseatを `_seat_card_confirmed` に登録する。

条件:

```text
detected_visible=True
and in_current_hand=True
```

confirmed seatは一時的NO_CARDから保護される。

---

#### 5.6.5 cards_visibleとin_current_handの違い

```text
cards_visible:
  現フレームでカード領域がカードありに見えるか

in_current_hand:
  当該handに参加中か
```

一時的なNO_CARDだけで `in_current_hand=False` にしてはならない。

---

### 5.7 FoldBadgeDetector

#### 5.7.1 通常Fold badge処理

FoldBadgeはFOLD補助情報として扱う。

相手seatのFold badgeは、Visual Obstruction Guard / Showdown Guardの影響を受ける。

---

#### 5.7.2 Hero Fold badge誤検出ガード

Hero seat=1について、同一フレームでHero通常アクションがある場合、Hero Fold badgeを無視する。

対象:

```text
CHECK
CALL
BET
RAISE
ALL_IN
```

---

#### 5.7.3 Hero Fold badge ignore latch

Hero通常アクションと矛盾してHero Fold badgeを一度無視した場合、そのhand中はHero seat=1のFold badgeをFOLD扱いしない。

GameLoop状態:

```python
_hero_fold_badge_ignored_for_hand: bool
_hero_fold_badge_ignored_reason: str | None
```

クリア条件:

```text
- hand start
- reset()
- stop()
```

Hero Fold badge単独検出は従来通りFOLD扱いしてよい。

---

#### 5.7.4 Showdown中のFold badge抑制

Showdown Guard中は、相手seatのFoldBadge由来FOLDを無視する。  
HeroのFOLD検出は通常通り扱う。

---

### 5.8 Visual Obstruction Guard

Visual Obstruction Guardは、一時的な遮蔽・演出・ウィンドウ被りによる誤更新を防ぐ。

発動候補:

```text
- 複数seatのcards_visibleが同時変化
- 複数seatの名前/カード/FoldBadgeが同時に不自然変化
```

保護内容:

```text
- cards_visible True→False のNO方向更新を凍結
- FoldBadge由来FOLDを抑制
- Name None / "" / "-" への更新を抑制
- in_current_hand=False 強制を抑制
```

Obstruction終了後も短いrecovery windowを設ける。

---

### 5.9 Showdown Guard

Showdown Guard発動条件:

```text
phase == "river"
board_card_count >= 5
active player >= 2
```

保護内容:

```text
- 相手seatのFoldBadge由来FOLDを無視
- 相手seatのNO_CARDによるin_current_hand=Falseを抑制
- Hero FOLDは通常通り扱う
```

---

### 5.10 Rejoin復活判定

Rejoinボタンは、誤ってOUTになったseatを手動復活させるために使う。

復活許可:

```text
- 直近seat card状態がTrue
- _seat_card_confirmedにseatが含まれる
- 3回re-scanして1回でも成功
```

拒否:

```text
- 直近検出なし
- confirmed cacheなし
- 3回re-scanして全失敗
```

fold済みseatを無条件復活してはならない。


## 6. ハンドライフサイクル

### 6.1 基本フェーズ

HandManagerは以下のphaseを管理する。

```text
waiting
preflop
flop
turn
river
hand_end
```

基本遷移:

```text
waiting
↓
preflop
↓
flop
↓
turn
↓
river
↓
hand_end
↓
waiting
```

---

### 6.2 新ハンド開始条件

waiting中にHeroカード2枚が認識された場合、新ハンド開始候補とする。

ただし、以下のガードを通過する必要がある。

```text
- 前ハンドと同じHeroカードではない
- board残像が危険でない
- potが不自然に大きすぎない
- table visibility復帰直後の誤認識ではない
- stale Heroカード抑制中ではない、または抑制解除条件を満たす
```

---

### 6.3 stale Heroカード抑制

hand_end直後、前ハンドHeroカードが画面に残る場合がある。

前ハンドと同じHeroカードが見えている場合、新ハンド開始を抑制してよい。

```text
current_hero_cards == last_ended_hero_cards
→ staleとして抑制
```

---

### 6.4 stale Heroカード抑制解除

前ハンドと異なるHeroカードが2枚認識された場合、それはstaleではなく新ハンド候補として扱う。

```text
current_hero_cards != last_ended_hero_cards
→ stale抑制解除
→ 通常の新ハンド開始ガードへ進む
```

ただし以下は維持する。

```text
- pot too large guard
- board残りguard
- table visibility guard
```

ログ例:

```text
Stale hero card suppression cleared: new hero cards differ from last ended hand current=['7c', '6d'] last=['As', '2s']
```

---

### 6.5 hand_start時のphase fast-forward

途中起動・途中監視開始に対応するため、hand start時にboard_countが3以上ならphaseをfast-forwardできる。

```text
board_count >= 3 → flop
board_count >= 4 → turn
board_count >= 5 → river
```

これは、アプリ起動時点ですでにpostflopだった場合に必要。

---

### 6.6 phase fast-forward抑制

hand_end直後やstale解除直後は、前ハンドboard残像が残っている可能性がある。

この場合、board_countだけでfast-forwardしてはならない。

GameState:

```python
suppress_phase_fast_forward: bool = False
```

GameLoopが以下の場合にTrueをセットする。

```text
- waiting中
- 前ハンド情報が残っている
- 新Heroカードが見えている
- 前ハンドboard残像の可能性がある
```

HandManagerは以下を行う。

```text
suppress_phase_fast_forward=True and board_count>=3
→ preflop開始のまま維持
```

ログ例:

```text
Phase fast-forward suppressed at hand start: board_count=3 reason=recent_hand_end_or_stale_clear
```

---

### 6.7 NEW_STREET判定

board_card_countが増えた場合、NEW_STREET候補とする。

期待遷移:

```text
preflop + board_count 3 → flop
flop + board_count 4 → turn
turn + board_count 5 → river
```

postflop推奨生成前には、phase / board_count整合ガードを必ず通す。

---

### 6.8 hand_end判定

hand_end候補:

```text
- pot decrease / payout
- active playerが1人以下
- NEW_HAND confirmed during active hand
- showdown終了
```

pot decreaseはOCR・遮蔽・演出の影響を受けるため、Visual Obstruction中やrecovery中は慎重に扱う。

---

### 6.9 hand_end後のwaiting遷移

pot decrease / payout由来でhand_endが確定した場合、同一 `process_frame()` 内でwaitingへ遷移してよい。

目的:

```text
次ハンド開始を取り逃がさないため
```

ただしUI上はhand_end表示が一瞬しか見えない可能性がある。  
Hand IDはFix49によりUI表示上は直近IDを保持する。

---

### 6.10 showdown / payout中の扱い

river board5枚かつactive playerが2人以上の場合、Showdown Guardを有効化する。

Showdown Guard中:

```text
- 相手FoldBadge由来FOLDを無視
- 相手NO_CARDによるin_current_hand=Falseを抑制
- Hero FOLDは通常通り扱う
```

---

## 7. アクション推定

### 7.1 入力と出力

入力:

```python
prev_state: GameState | None
curr_state: GameState
```

出力:

```python
game_event: str | None
actions: list[ActionRecord]
```

例:

```json
{
  "game_event": "NEW_STREET",
  "actions": [
    {"seat": 2, "action": "CALL", "amount": 200, "confidence": "high"}
  ]
}
```

---

### 7.2 game_event判定

主なgame_event:

```text
NEW_HAND
NEW_STREET
BETS_COLLECTED
NO_CHANGE
```

判定優先度:

```text
1. NEW_HAND
2. NEW_STREET
3. BETS_COLLECTED
4. seat別action
```

---

### 7.3 アクション判定優先順

主な判定:

```text
FOLD
ALL_IN
BET
CALL
RAISE
CHECK
BLIND_SB
BLIND_BB
```

ALL_IN再分類:

```text
bet_curr >= stack_prev * 0.9
```

ただし、suspicious金額はALL_IN再分類に使わない。

---

### 7.4 複数アクションの同一フレーム検出

ポーリング間隔中に複数人が行動する場合がある。

方針:

```text
- 各seatを独立に分析
- 変化があった全seatのActionRecordを生成
- seat番号昇順で並べる
- 同時に3人以上変化した場合はconfidence low候補
```

---

### 7.5 OCR失敗時のスキップ

OCR失敗を即アクション扱いしない。

例:

```text
stack=None 1フレーム
→ OCR失敗として保持

stack=None 2フレーム
→ warning候補

stack=None 3フレーム
→ 離席/表示消失候補
```

---

### 7.6 Hero action保存経路の一元化

Hero通常actionは、ActionEstimator由来のframe actionとして直接street actionに保存しない。

対象:

```text
CHECK
CALL
BET
RAISE
ALL_IN
```

HandManager定義:

```python
_HERO_BOUNDARY_ACTIONS = {"CHECK", "CALL", "BET", "RAISE", "ALL_IN"}
```

通常 `_add_actions()` では以下を保存しない。

```text
action.seat == 1
and action.action.upper() in _HERO_BOUNDARY_ACTIONS
and allow_hero_boundary_actions == False
```

正規保存経路:

```text
_update_hero_turn_boundary()
↓
_detect_hero_action()
↓
_record_hero_action()
↓
_add_actions([action], allow_hero_boundary_actions=True)
```


### 7.7 Hero action遅延補正

Hero通常actionは、原則としてHero turn boundary由来の推定を正とする。

対象:

```text
CHECK
CALL
BET
RAISE
ALL_IN
```

frame由来のHero通常actionを無条件にstreet actionへ保存してはならない。

理由:

```text
ActionEstimator由来Hero actionを無条件保存すると、
同一Hero turnで CHECK → CALL のような二重記録が発生するため。
```

ただし、Hero turn終了直後に画面反映が遅れ、boundary時点では `CHECK` と保存された後、短時間内にframe由来の `CALL / BET / RAISE / ALL_IN` が検出される場合がある。

この場合のみ、直近のHero CHECKを置換してよい。

置換条件:

```text
- 直前のHero boundary actionがCHECK
- CHECK保存から hero_check_replace_window_sec 以内
- 同じstreet上の直近Hero CHECKである
- frame由来actionが CALL / BET / RAISE / ALL_IN
- FOLDは置換対象外
```

デフォルト:

```text
hero_check_replace_window_sec = 1.0
```

置換時に更新するもの:

```text
- _all_actions 内の直近Hero CHECK
- 現在street actions内の直近Hero CHECK
- human_action
- followed_recommendation
- _last_hero_action
```

ログ例:

```text
Hero delayed action replaced boundary CHECK: CHECK 0 -> CALL 300 age=0.42s street=preflop
```

禁止:

```text
- frame由来Hero通常actionを無条件保存すること
- CHECK -> FOLD 置換を行うこと
- 過去street / 過去handのHero CHECKを置換すること
```

---

### 7.8 Hero FOLDの扱い

Hero FOLDは除外対象にしない。

理由:

```text
- Fold badgeやカード消失から即時検出するケースがある
- FOLDまで除外すると、本物のFOLD検出が遅れる可能性がある
```

ただし、Hero通常アクションと矛盾したHero Fold badgeは無視する。

---

### 7.9 相手actionの保存

相手seatのactionは従来通りframe actionとして保存してよい。

対象:

```text
seat 2〜6
CHECK
CALL
BET
RAISE
ALL_IN
FOLD
BLIND_SB
BLIND_BB
```

---

### 7.10 Duplicate action判定

同じseat/action/amountが連続フレームで重複検出された場合、duplicateとして無視する。

ただし、保存しなかったHero通常actionは `_last_frame_actions` に入れない。

理由:

```text
保存していないactionをduplicate履歴に入れると、後続の正規Hero action記録に影響する可能性があるため。
```

---

## 8. 状態安定化ガード

### 8.1 Recommendation context snapshot

推奨生成前に、判断時点のGameStateからsnapshotを作成する。

含める項目:

```text
hand_id
phase
board
board_count
active_player_count
actions_count
hero_is_my_turn
hero_in_current_hand
```

potはOCR揺れが大きいため、stale判定の必須項目には含めない。

---

### 8.2 stale推奨破棄

推奨返却時、現在GameStateとsnapshotを比較する。

不一致なら推奨を破棄する。

破棄時に行わないこと:

```text
- HUD表示
- previous_recommendation保存
- HandManagerへのrecommendation保存
```

破棄対象例:

```text
- Solver中にHeroが先に行動した
- NEW_STREETへ進んだ
- hand_idが変わった
- hand_end / waitingへ遷移した
- active_player_countが変わった
- actions_countが変わった
- board_countが変わった
- hero_is_my_turnがFalseになった
- hero_in_current_handがFalseになった
```

---

### 8.3 phase / board_count整合ガード

postflop推奨生成前にphaseとboard_countを確認する。

期待値:

```text
flop  → board_count == 3
turn  → board_count == 4
river → board_count == 5
```

不一致ならSolver / LLM / recommendation生成をskipする。

ログ例:

```text
Strategy skipped: phase/board_count mismatch
```

preflopは対象外。

---

### 8.4 pending recommendation cancel条件

pending recommendationは以下でcancel扱いにする。

```text
- NEW_HAND
- NEW_STREET
- waiting遷移
- hand_end
- Hero turn終了
- Heroがhand外へ出た
- hand_id変化
- phase変化
- board変化
- board_count変化
- active_player_count変化
- actions_count変化
- hero_is_my_turn変化
- hero_in_current_hand変化
```

---

### 8.5 古いSolver / fallback結果の破棄

Solver結果だけでなく、fallback結果も古い文脈なら破棄する。

禁止:

```text
- 古いfallbackをHUD表示する
- 古いfallbackをprevious_recommendationに保存する
- 古いfallbackをHandManagerへ保存する
```

---

### 8.6 pot OCR急変ガード

potの急増・急減はOCR誤読や演出の可能性がある。

既存方針:

```text
1フレーム急増:
  保持・再確認

2フレーム連続:
  実変化候補

Visual Obstruction / recovery中のpot decrease:
  hand_end判定に使わない
```

今後の課題:

```text
pot OCR巨大誤認が再発した場合、pot専用suspicious判定を追加する。
```

---

### 8.7 NEW_HAND誤検出ガード

NEW_HANDはpot減少だけで確定しない。

考慮するもの:

```text
- pot変化
- previous pot
- blind size
- phase
- table visibility
- Hero cards
- board残像
- Visual Obstruction / recovery
```

active hand中にNEW_HAND confirmedが出た場合は、ログを重視して再確認する。

---

## 9. 戦略ルーティング

### 9.1 基本ルーティング

戦略ルーティングは `GameLoop._handle_strategy()` で行う。

基本分岐:

phase == preflop
→ Chart

phase in {flop, turn, river} and active_player_count == 2
→ Deep CFR推論

phase in {flop, turn, river} and active_player_count >= 3
→ Deep CFR推論 + eval7補助

その他
→ skip / fallback

推奨生成前に必ず確認する。

- Heroがmy_turnである
- Heroがin_current_handである
- phase / board_countが整合している
- stale previous recommendationではない
- strategy_defer_reason がない
- hero_cards_unstable_reason がない
- GameLoop内部のHeroカードinvalid状態が立っていない

---

### 9.2 Preflop

PreflopはGTOチャートを主軸に判断する。

補正:

```text
- DB統計が十分な相手に対してのみ補正
- facing_betが大きい場合は安全ガード
```

PreflopではSolverを使わない。

処理中表示:

```text
CHART CHECKING...
```

---

### 9.3 HU Postflop

HU postflopはDeep CFR推論を主軸に判断する。

基本:

active_player_count == 2
phase in {flop, turn, river}

Deep CFR推論はローカルGPU上で実行する。
応答は1ミリ秒以下のため、同期呼び出しで問題ない。
ただし既存の非同期worker構造は維持してよい。

処理中表示:

DEEP CFR THINKING...

Deep CFR結果はcontext一致時のみ採用する。

exploit_adjustment:

DB統計が十分な相手（total_hands >= sample_threshold_low）に対しては、
Deep CFR出力をLLM exploit_adjustmentで補正する。
補正はDeep CFR推論完了後に同期的に行う。

出力形式:

Deep CFRは以下を返す。
- fold_prob: float
- call_prob: float
- raise_prob: float
- raise_size_ratio: float（ポット比）

推奨アクションは最も確率が高いアクションとする。
raise_amountはraise_size_ratioからチップ額に変換する。

confidence判定:
- top_prob >= 0.70 → high
- top_prob >= 0.45 → medium
- top_prob < 0.45 → low


---

### 9.4 Multiway Postflop

Multiway postflopはDeep CFR推論を主軸に判断する。

基本:

active_player_count >= 3
phase in {flop, turn, river}

Deep CFR推論はHU postflopと同じモデル・同じブリッジを使用する。
Deep CFR 6-playerモデルは6人テーブルを前提に訓練されているため、
HU/Multiwayで別モデルを使い分ける必要はない。

eval7はequity補助情報として維持する。
ただしeval7結果は推奨の主軸ではなく、HUD表示やreason生成の補助とする。

処理中表示:

DEEP CFR THINKING...

LLMはMultiway判断の主軸としては使用しない。
exploit_adjustmentとしてのLLM利用は、HUと同様にDB統計十分な相手に対してのみ行う。

出力形式はHU postflopと同一。

---

### 9.5 DB統計利用条件

DB統計は、相手ごとに十分なサンプルがある場合のみ使用する。

基本条件:

```text
opponent_stats.total_hands >= preflop_delta.sample_threshold_low
```

複数相手のハンド数を合算して判定してはならない。

---

### 9.6 推奨採用条件

推奨は返却時点でcontextが有効な場合のみ採用する。

採用条件:

```text
hand_id一致
phase一致
board一致
board_count一致
active_player_count一致
actions_count一致
hero_is_my_turn一致
hero_in_current_hand一致
```

postflopではさらに以下も必要。

```text
phase / board_count整合
```

---

### 9.7 推奨を表示しない条件

以下の場合は推奨を表示しない。

```text
- Heroのターンではない
- Heroがhand外
- hand_idが変わった
- phaseが変わった
- boardが変わった
- board_countが変わった
- active_player_countが変わった
- actions_countが変わった
- NEW_STREETへ進んだ
- hand_end / waitingへ遷移した
- pending requestがcancel済み
- phase / board_count不整合
```

古いSolver / fallback / LLM結果も表示しない。
---

### 9.8 pot spike hold中のstrategy保留

`GameState.strategy_defer_reason == "pot_spike_hold"` の場合、strategy計算を開始しない。

対象:

```text
- Preflop Chart
- HU Solver
- Multiway LLM
- fallback
```

この状態では、potが前回値にholdされている一方で、BET / RAISE / ALL_IN actionだけが先に記録されている可能性がある。

そのため、以下は禁止する。

```text
- 古いpot + 新しい巨大betでSolver requestを作る
- 古いpot + 新しい巨大betでLLM promptを作る
- fallback推奨を出す
- previous recommendationを維持表示する
```

GameLoopは以下を行う。

```text
- pending recommendationをclear/cancel
- previous recommendationを破棄
- HUDに WAITING FOR STABLE POT... を表示
- GameLoopは継続
```

potがconfirmedされた次フレーム以降、通常通りstrategy処理へ戻る。

---

### 9.9 Heroカード不安定時のstrategy停止

`GameState.hero_cards_unstable_reason` がある場合、またはGameLoop内部でHeroカードinvalid状態が立っている場合、strategy計算を開始しない。

対象:

```text
- Preflop Chart
- HU Solver
- Multiway LLM
- fallback
```

理由:

```text
HeroカードはChart / Solver / LLMの最重要入力であり、誤ったカードで推奨を出すと勝率に直撃するため。
```

GameLoopは以下を行う。

```text
- pending recommendationをclear/cancel
- previous recommendationを破棄
- HUDに HERO CARDS UNSTABLE を表示
- active hand中に矛盾が確定した場合は abandon_current_hand("hero_cards_unstable") を呼ぶ
```

禁止:

```text
- Heroカード不安定中にfallback推奨を出すこと
- active中のfresh OCRでcached Heroカードを即上書きすること
- Heroカード不安定handをDB統計に保存すること
```
---

## 10. HU Postflop Solver（廃止予定）

本セクションの内容は、Deep CFR統合完了後に廃止する。
Deep CFR統合が完了するまでは、既存のRust postflop CLI連携を維持する。
Deep CFR統合完了後は、本セクションをDESIGN_NOTES.mdへ移動する。

Deep CFR統合後の判断経路は Section 9.3 を参照。

----

HU postflopでは、原則としてSolverを主軸に判断する。

ただし、Solver推奨として採用できるのは、Solver出力からHero実カードに対応するhand row strategyを取得できた場合のみである。

`average_strategy` はHero実カード別の戦略ではないため、原則として本番推奨・teacherデータ・LLM評価基準として使ってはならない。

---

### 10.0 Solver入力安定性条件

HU Solverは、以下を満たす場合のみ起動する。

```text
- active_player_count == 2
- hero cardsが安定している
- board枚数がphaseと一致している
- hero_positionが確定している
- hero_is_ipがTrue/Falseで確定している
- effective_stackが取得できる
- street_start_potが異常値ではない
- current_street_actionsからactions_playedを構築できる
- active seats / position lock / folded seats が矛盾していない
```

不安定な場合:

```text
- Solver requestを作らない
- fallback FOLD/CALLを出さない
- HUDにはSOLVER INPUT UNSTABLEまたはWAITING状態を表示する
- HandManagerへRecommendation保存しない
```

### 10.1 Solver統合方式

SolverはRust postflop CLIをPythonから呼び出す。

用途:

```text
HU postflop判断
```

Preflopでは使わない。
Multiwayでは使わない。

HU SolverはGameLoopをブロックしない非同期workerで実行する。

---

### 10.2 Solver request構造

Solver request本体には、原則として以下を含める。

```text
board
phase
starting_pot / pot
effective_stack
position / hero_is_ip
actions_played
range_oop
range_ip
bet_sizes
raise_sizes
timeout_ms
max_iterations
target_exploitability_pct
```

単位はチップ額で統一する。

重要:

```text
Solver本体はHeroの具体ハンド1つだけを入力して解くのではなく、OOP/IPのrange全体を解く。
そのため、Solver request本体にhero_cardsが直接入らない構造自体は許容する。
```

ただし、後からHero実カードに対応するhand rowを抽出するため、Solver request/debug保存の `meta` には必ず `hero_cards` を保存する。

---

### 10.3 Solver response構造

Solver responseには以下を含める。

```text
success
error
root_strategy
node_strategy
metadata
```

`node_strategy` がある場合は、現在nodeの戦略として優先する。

`root_strategy` / `node_strategy` には以下が含まれる想定。

```text
actions
hands
strategy_matrix
average_strategy
```

重要:

```text
Solver推奨として採用するのは、Hero実カードに一致するhands rowのstrategy_matrixを取得できた場合のみ。
```

---

### 10.4 actions_played

`actions_played` は現在ノードまでのアクション履歴である。

目的:

```text
Solver game tree上の正しいnodeへ到達するため
```

StreetActionsから構築する。

禁止:

```text
- 不安定なcurrent_street_actionsからactions_playedを作ること
- pot_spike_hold中に古いpotと新しいactionを組み合わせてSolverへ渡すこと
- Hero actionが未確定の状態でSolver nodeを進めること
```

---

### 10.5 node_strategy優先

Solver responseに `node_strategy` がある場合、現在nodeの戦略として優先する。

理由:

```text
root strategyではなく、現在局面のnode strategyが必要なため
```

`node_strategy` がない場合のみ `root_strategy` を参照する。

---

### 10.6 timeout_ms / bridge_timeout_sec

Solver requestの `timeout_ms` とPython bridge側のtimeoutは整合させる。

例:

```text
timeout_ms=20000
bridge_timeout_sec=22.0
```

bridge側はrequest timeoutより少し長くする。

ログ例:

```text
HU solver request: timeout_ms=20000 bridge_timeout_sec=22.0
```

---

### 10.7 深SPRフロップ設定

深SPR flopではSolverが重くなるため、専用設定を使う。

例:

```text
phase == flop
and effective_stack / starting_pot > 10
```

設定例:

```text
timeout_ms = 20000
max_iterations = 300
```

注意:

```text
deep-SPR flop Solverは規定時間内に返らない場合がある。
timeout時に暫定推奨やNO SIGNAL推奨を出してはならない。
```

---

### 10.8 HU Solver非同期worker

HU SolverはGameLoopをブロックしない。

非同期フロー:

```text
Hero turn中
↓
HU postflop判定
↓
GameState安定性チェック
↓
Recommendation context snapshot作成
↓
Solver request/debug JSON保存
↓
daemon worker threadでSolver実行
↓
GameLoopは継続して画面認識
↓
毎フレームpending resultをpoll
↓
Solver返却
↓
request_id / active_id / cancelled / context鮮度確認
↓
有効なら採用
↓
無効なら破棄
```

古いSolver結果は採用しない。

cancel条件:

```text
- NEW_HAND
- NEW_STREET
- waiting遷移
- hand_end
- Hero turn終了
- Heroがhand外へ出た
- hand_id変化
- phase変化
- board変化
- board_count変化
- active_player_count変化
- actions_count変化
- hero_is_my_turn変化
- hero_in_current_hand変化
```

---

### 10.9 Solver process reset

Solver CLIがtimeout / cancel / orphan状態で裏に残ると次requestを詰まらせるため、以下の場合はRust Solver processをresetする。

```text
- Solver timeout
- Hero turn終了でSolverが不要化
- street変更でSolverが不要化
- hand_end
- waiting遷移
- orphan worker検出
```

注意:

```text
- Python threadを強制killしない
- 不要化したpostflop_cli.exe processはresetする
- process resetは毎requestではなく、不要化・timeout・orphan時のみ行う
```

---

### 10.10 Solver request/debug保存

Solver request/debug保存には、後から再解析できるように以下を必ず保存する。

```text
hero_cards
board
street
num_players
heads_up
hero_position
hero_is_ip
hero_bet
max_opponent_bet
facing_bet
call_amount
raw_call_amount
pot
effective_stack
current_street_actions
preflop_actions
range_oop
range_ip
actions_played
preflop_scenario
range_source
actions_played_status
```

欠落がある場合は保存を止めずにwarningを出す。

```text
SOLVER_REQUEST_META_INCOMPLETE
```

禁止:

```text
- hero_cardsなしのdebug requestをteacher作成に使うこと
- facing_bet / call_amountなしのdebug requestをLLM Blind検証に使うこと
- 保存済みmeta不足を無視してteacher化すること
```

---

### 10.11 Hero hand row抽出

Solverはrange全体のstrategyを返す。

そのため、Solver結果を本番推奨として採用する前に、Python側でHero実カードに対応するhand rowを必ず抽出する。

正しい流れ:

```text
1. Solverへ board / range_oop / range_ip / pot / stack / actions / sizing を渡す
2. Solverがrange全体のstrategy_matrixを返す
3. Python側でhero_cardsに一致するhands rowを探す
4. そのhand rowのstrategyを推奨に使う
```

Solver推奨として採用できる最低条件:

```text
solver_success=true
hero_cards が2枚存在する
Hero実カードに対応するhand rowが root_strategy または node_strategy の hands に存在する
strategy_source_detail=hand_strategy
hero_range_contains_hand=true
```

Hero実カードに対応するhand rowが見つからない場合は、Solver推奨として扱わない。

---

### 10.12 Hero hand matching

Solver出力の `hands` とHero実カードを照合する際は、カード順序差を吸収する。

例:

```text
hero_cards=["3c","Qc"]

候補:
3cQc
Qc3c
```

候補生成では以下を考慮する。

```text
- 元順
- 逆順
- rank順
```

Hero hand candidatesのいずれかが `hands` に存在する場合、そのhand rowの `strategy_matrix` を使う。

Heroカードがあるのに候補が `hands` に存在しない場合はwarningを出す。

```text
HU_SOLVER_HERO_HAND_NOT_FOUND
```

この場合、`average_strategy` へ黙ってfallbackしてはならない。  
fallbackした場合でも、teacherデータとして使ってはならない。

---

### 10.13 Hero hand range membership

Hero実カードは、Solver requestのHero側rangeに含まれていなければならない。

Hero側rangeは以下で判定する。

```text
hero_is_ip=false → range_oop
hero_is_ip=true  → range_ip
```

Hero実カードがHero側rangeに含まれない場合、そのSolver結果はHeroカード別teacherとして使ってはならない。

range外の場合は診断対象とする。

```text
hero_range_contains_hand=false
```

range外原因候補:

```text
- preflop_scenario の判定ミス
- hero_position / hero_is_ip / OOP-IP割当ミス
- range定義が狭すぎる
- 実カードをSolver rangeへ補完すべき
- そのspotをSolver不適格として扱うべき
```

この原因分類が済むまで、range外データをteacher化してはならない。

---

### 10.14 average_strategy fallbackの扱い

`average_strategy_fallback` は、Hero実カード別の戦略ではない。

そのため、以下に使ってはならない。

```text
- 本番Solver推奨
- teacherデータ
- LLM評価基準
- sizing teacher
- Solver/LLM整合性の正解ラベル
```

以下の場合は診断対象とする。

```text
hero_cards_missing
matched_hand_missing
hero_range_contains_hand=false
average_strategy_fallback
equal_probability_fallback
default_check_fallback
solver_error
```

Teacherデータとして使ってはいけない条件:

```text
hero_cards_missing
matched_hand_missing
hero_range_contains_hand=false
average_strategy_fallback
equal_probability_fallback
default_check_fallback
solver_error
```

Teacherデータ作成前に、必ずparse auditを行う。

---

### 10.15 HU Solver結果の採用可否

Solver結果は以下の場合のみ採用する。

```text
solver_success=true
context snapshotが現在GameStateと一致
strategy_source_detail=hand_strategy
matched_hand_missing=false
hero_range_contains_hand=true
legal actionである
```

以下の場合は採用しない。

```text
solver_error
solver_timeout
solver_input_unstable
stale context
hero_cards_missing
matched_hand_missing
hero_range_contains_hand=false
average_strategy_fallback
```

採用しない場合、未確定推奨を出さない。  
HUDには状態表示のみ出す。

例:

```text
SOLVER INPUT UNSTABLE
SOLVER THINKING...
SOLVER STILL RUNNING
```


## 10A. Deep CFR推論ブリッジ

### 10A.1 概要

Deep CFR推論ブリッジは、GameStateをDeep CFRモデルの入力形式に変換し、
推論結果をRecommendation形式に変換する中間層である。

ファイル: deep_cfr_bridge.py

位置づけ: solver_bridge.py と同等のDecision Engine層コンポーネント。

### 10A.2 モデル

モデル: Deep CFR 6-player NLHE
リポジトリ: https://github.com/dberweger2017/deepcfr-texas-no-limit-holdem-6-players
ライセンス: MIT
形式: PyTorch .pt チェックポイント
アーキテクチャ: 5層フィードフォワード（入力500次元、隠れ層256ユニット×5、出力3アクション＋1サイジング）
推論速度: 0.5〜1ミリ秒（RTX 3080）

### 10A.3 モデルロード

アプリ起動時にモデルをGPUにロードする（1回のみ）。
ロード失敗時はWARNINGログを出し、fallback経路へ進む。
fallback経路は既存のRust postflop CLI（廃止までの暫定）。

config.yaml:

deep_cfr:
  model_path: models/deep_cfr/best_checkpoint.pt
  device: cuda
  fallback_to_solver: true

### 10A.4 入力変換

GameStateから以下を取得し、500次元入力ベクトルに変換する。

hero_cards: list[str]
board: list[str]
phase: str
pot: int
hero_stack: int
hero_bet: int
hero_position: int
hero_is_ip: bool
active_player_count: int
players: dict（各seatのstack/bet/in_hand/is_seated）
current_street_actions: list[ActionRecord]
preflop_actions: list[ActionRecord]
min_bet: int
legal_actions: list[str]

変換関数: encode_game_state(game_state: GameState) -> torch.Tensor

カード表記変換:
"Qd" → 52次元one-hotの該当インデックス
スート: ♠=0, ♥=1, ♦=2, ♣=3
ランク: 2=0, 3=1, ..., A=12

### 10A.5 出力変換

モデル出力を以下に変換する。

raw出力: [fold_logit, call_logit, raise_logit, raise_size_ratio]

変換後:
fold_prob: float（softmax後）
call_prob: float（softmax後）
raise_prob: float（softmax後）
raise_size_ratio: float（sigmoid後、0.1x〜3.0x pot）

チップ額変換:
raise_amount = facing_bet + call_amount + int(pot * raise_size_ratio)

推奨アクション:
top_action = argmax(fold_prob, call_prob, raise_prob)

### 10A.6 Recommendation生成

Recommendation(
    action=top_action,
    amount=raise_amount if top_action == "RAISE" else call_amount if top_action == "CALL" else 0,
    confidence=confidence_from_top_prob(top_prob),
    source="deep_cfr",
    reason=format_reason(fold_prob, call_prob, raise_prob, raise_amount, call_amount),
    metadata={
        "fold_prob": fold_prob,
        "call_prob": call_prob,
        "raise_prob": raise_prob,
        "raise_size_ratio": raise_size_ratio,
        "raise_amount": raise_amount,
        "call_amount": call_amount,
        "pot": pot,
        "model": model_name,
        "exploit_adjusted": False
    }
)

### 10A.7 exploit補正後

exploit_adjustment適用後:

metadata["exploit_adjusted"] = True
metadata["exploit_source"] = "llm"
metadata["original_action"] = original_top_action
metadata["adjusted_action"] = adjusted_action

### 10A.8 エラーハンドリング

モデルロード失敗: WARNINGログ、fallback_to_solver=trueなら既存Solver経路へ
推論例外: WARNINGログ、そのフレームの推奨をスキップ
入力変換失敗: WARNINGログ、推奨をスキップ

暫定推奨は出さない。
エラー時にfallback推奨を出さない。
既存の安全原則を維持する。

### 10A.9 訓練済みモデル管理

訓練済みモデルは以下に配置する。

models/deep_cfr/
├── best_checkpoint.pt      ← 本番推論用
├── phase1_seedA/           ← 訓練Phase 1
├── phase1_seedB/
├── phase1_seedC/
├── phase2/                 ← 訓練Phase 2
├── phase3/                 ← 訓練Phase 3
└── training_log.md         ← 訓練経過記録

本番推論用モデルの切り替えはconfig.yamlで行う。
訓練中の中間checkpointは本番推論に使わない。


### 10A.10 訓練原則

Deep CFRモデルの訓練・再訓練時は以下の原則を遵守する。

必須:
- 毎イテレーション、ネットワークをゼロから再訓練する（ファインチューニング禁止）
- メモリバッファはReservoir Samplingを使う（スライディングウィンドウ禁止）
- Linear CFR重み付けを適用する（イテレーション番号tに比例した重み）
- 全リグレットが負のとき、均等戦略ではなく最大リグレットアクションを確率1で選ぶ
- メモリバッファサイズはRAMが許す限り大きくする（最低数百万サンプル）
- 訓練は最低3シードで並行実行し、最良シードを選定する

禁止:
- 1シードだけで本番モデルを決定すること
- Phase 2（自己対戦）を5000イテレーション以上引っ張ること
- Phase 3でメモリバッファサイズを縮小すること
- TensorBoardを監視せず訓練を放置すること
- 1つの指標（例：ランダム相手勝率）だけで品質を判断すること

品質検証方法:
- checkpoint間トーナメント（visualize_tournament.py）
- CLIプレイ（play.py）による手動スポットチェック
- 複数指標の組み合わせ（ランダム勝率、相互勝率、異常行動有無）

根拠:
- 原論文 Brown & Sandholm, 2019, ICML
  https://proceedings.mlr.press/v97/brown19b/brown19b.pdf
- 原論文Figure 3: ネットワークサイズとtraversal数の影響
- 原論文Figure 4: 再訓練・Reservoir Sampling・Linear CFRの効果
- dberweger2017版の訓練経験則（3段階訓練、学習率半減、混合訓練）

### 10A.11 モデル品質の評価基準

Phase 1合格基準:
- advantage lossが安定的に低下
- ランダム相手への利益 >= 10チップ/ゲーム

最終合格基準:
- ランダム相手への利益 >= 15チップ/ゲーム
- Phase 1 checkpointへの勝率 >= 60%
- CLIプレイで明らかな異常行動がない
  （ナッツでフォールド、ブラフキャッチャーでオーバーベット等がない）
- 異なるcheckpoint間の成績のばらつきが小さい


## 11. LLM

### 11.1 LLM利用方針

LLMはexploit補正に使用する。
LLMをMultiway postflop判断の主軸としては使用しない。
LLM単体の出力を無検証で採用しない。

用途:

- HU exploit adjustment（Deep CFR出力に対する統計ベース補正）
- Multiway exploit adjustment（同上）

廃止した用途:

- Multiway postflop判断の主軸（Deep CFRに置き換え）

---

### 11.2 OpenRouter設定

LLMはOpenRouter API経由で呼び出す。

現在の推奨モデル:

```text
openai/gpt-5.4-mini
```

`.env` 例:

```env
OPENROUTER_API_KEY=sk-or-v1-...
LLM_MODEL_DEFAULT=openai/gpt-5.4-mini
LLM_MODEL_PREMIUM=openai/gpt-5.4-mini
OPENROUTER_PROVIDER_ORDER=OpenAI
OPENROUTER_ALLOW_FALLBACKS=false
OPENROUTER_REQUIRE_PARAMETERS=false
OPENROUTER_USE_STRICT_JSON_SCHEMA=true
```

provider設定:

```json
{
  "provider": {
    "order": ["OpenAI"],
    "allow_fallbacks": false,
    "require_parameters": false
  }
}
```

注意:

```text
- APIキーをログに出してはならない
- prompt全文を通常ログに出してはならない
- モデルIDをコードにハードコードしてはならない
- .env実ファイルをcommitしてはならない
```

startup check:

```text
- 起動時にOpenRouter接続確認を行う
- gpt-5.4-mini / OpenAI providerでは max_tokens は最小16以上にする
- startup check失敗時はWARNINGログを出すが、アプリ起動は継続する
- 400以上のHTTPエラーでは response.text の先頭500文字をログに出す
```

---

### 11.3 multiway_decision

用途:

```text
Multiway postflop判断
```

入力:

```text
hero_cards
board
phase
pot
hero_stack
active_players
facing_bet
call_amount
pot_odds
required_equity
hero_equity
current_street_actions
opponent_profiles
```

出力:

```json
{
  "action": "fold/call/check/bet/raise",
  "amount": 0,
  "confidence": "low/medium/high",
  "reason": "..."
}
```

LLMがfoldを返した場合でも、hero_equityがrequired_equityを十分に上回るならCALLへ補正する。

---

### 11.4 exploit_adjustment

用途:

```text
HU Solver結果に対するDB統計ベースの搾取補正
```

呼び出し条件:

```text
opponent_stats.total_hands >= preflop_delta.sample_threshold_low
```

HUリアルタイム判断では最大1回。

---

### 11.5 range_estimation

リアルタイムHU判断では呼ばない。

現状:

```text
保留
```

---

### 11.6 reason_generation

リアルタイムHU判断では呼ばない。

現状:

```text
保留
```

---

### 11.7 LLM timeout

LLM timeout時は未確定推奨を出さない。

古いLLM結果もcontext不一致なら破棄する。

---

### 11.8 LLM出力JSON制約

LLMの構造化出力は、プロンプト指示だけに依存しない。

既存方針:

```text
- promptでJSON onlyを要求する
- 返答をJSON parseする
- Pydantic schemaでvalidationする
- validation失敗時はfallbackへ進む
```

`OPENROUTER_USE_STRICT_JSON_SCHEMA=true` の場合、対応タスクではOpenRouter payloadに `response_format=json_schema` を追加する。

対象タスク:

```text
multiway_decision
exploit_adjustment
range_estimation
preflop_delta
```

対象外:

```text
reason_generation
```

理由:

```text
reason_generation は自由文出力であり、strict JSON Schemaの対象にしない。
```

payload例:

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "multiway_decision",
      "strict": true,
      "schema": {}
    }
  }
}
```

挙動:

```text
- strict JSON SchemaがONでも、既存のPydantic validationは維持する
- API側で400等が返った場合、アプリは落とさずfallbackへ進む
- 400以上では response.text の先頭500文字をWARNINGログに出す
```

禁止:

```text
- strict JSON Schema失敗時にアプリを落とすこと
- reason_generationにresponse_formatを付けること
```
---

### 11.9 LLMを呼ばない条件

以下ではLLMを呼ばない。

```text
- HUで相手DB統計が不足
- Hero turnではない
- Heroがhand外
- phase / board_count不整合
- preflop chartで十分
- stale context
```
追加でLLMを呼ばない条件:

```text
- GameState.strategy_defer_reason がある
- GameState.strategy_defer_reason == "pot_spike_hold"
- GameState.hero_cards_unstable_reason がある
- GameLoop内部でHeroカードinvalid状態が立っている
- Heroカードが不安定または矛盾している
```

これらの場合、LLMだけでなくChart / Solver / fallbackも開始しない。

理由:

```text
pot/action不整合やHeroカード不安定の状態では、どの推奨経路でも誤った判断になるため。
```

---

### 11.10 LLM Blind検証の入力条件

LLM Blind検証では、Solver/teacher情報をLLMに渡してはならない。

渡してはいけない情報:

```text
primary Solver action
primary Solver probabilities
primary_top_margin
primary_margin_class
teacher_label
allowed_sizing_types
profile_actions
range membership audit result
```

ただし、Solverと同等の実戦情報は必ず渡す。

必須入力:

```text
hero_cards
board
pot
effective_stack
SPR
hero_position
hero_is_ip
actions_played
legal_actions
facing_bet
call_amount
street
heads_up
num_players
```

この入力が欠けたBlind LLM検証結果は、本番採用判断に使ってはならない。

重要:

```text
Solver/teacher情報なしで判断させることと、実戦情報を欠落させることは別である。
Blind LLM検証では、答えは見せないが、実戦で見えている情報は必ず渡す。
```

Blind LLM検証後は、裏側でSolver/teacherと照合してよい。

ただし、照合結果をpromptに入れてはならない。
---

## 12. GUI / HUD

### 12.1 メインウィンドウ

メインウィンドウはPyQt6で実装する。

役割:

```text
- 現在GameState表示
- 推奨表示
- ログ表示
- Start / Stop
- Rejoin操作
- DB / replay確認
```

---

### 12.2 Operation画面

Operation画面では、現在状態を確認しやすく表示する。

表示対象:

```text
- Summary
- Current State
- seat別状態
- Recommendation
- reason
```

---

### 12.3 Current State表示

seat別に表示する。

```text
Seat
Name
Stack
Bet
Cards
In Hand
Status
```

---

### 12.4 Cards列の表示補正

UIでは `player.cards_visible` をそのまま表示しない。

表示用Cards:

```python
raw_cards_visible = bool(player is not None and player.cards_visible)
is_seated = bool(player is not None and player.is_seated)
in_hand = bool(player is not None and player.in_current_hand)
display_cards_visible = bool(is_seated and in_hand and raw_cards_visible)
```

表示ルール:

```text
is_seated=False
→ Cards=NO

in_current_hand=False
→ Cards=NO

is_seated=True and in_current_hand=True and raw_cards_visible=True
→ Cards=YES
```

GameState本体は変更しない。

---

### 12.5 Hand ID表示保持

`game_state.hand_id` がNoneになっても、`phase in {"hand_end", "waiting"}` の間は直近Hand IDを表示する。

MainWindow:

```python
_last_displayed_hand_id: int | None = None
```

表示ルール:

```text
game_state.hand_id がある:
  表示し、_last_displayed_hand_idを更新

game_state.hand_id is None and phase in {"hand_end", "waiting"}:
  _last_displayed_hand_idを表示

clear_live_state():
  _last_displayed_hand_id = None
  Hand ID = "-"
```

---

### 12.6 推奨根拠文表示

推奨根拠文は専用エリアに表示する。

目的:

```text
ログやJSONに紛れず、ユーザーが読めるようにする
```

---

### 12.7 HUDオーバーレイ

HUDはプレイ画面上に推奨を表示する。

表示対象:

```text
action
amount
confidence
source
reason
processing status
```

---

### 12.8 HUD処理中表示

処理中は未確定推奨を出さず、statusのみ表示する。

表示例:

```text
CHART CHECKING...
SOLVER THINKING...
LLM ANALYZING...
Computing...
```

メソッド:

```python
def show_computing(self, message: str = "Computing...") -> None:
    ...
```

---

### 12.9 Start / Stop

Start/Stopは多重起動・停止中競合を避ける。

Stop時:

```text
- GameLoop停止
- HUD終了
- live state clear
- UI表示を安全状態へ戻す
```

---

### 12.10 Rejoinボタン

Rejoinは誤OUT化したseatを手動復活させるための補助機能。

Rejoinは無条件復活ではない。

許可条件:

```text
- 直近カード検出True
- _seat_card_confirmedあり
- 3回re-scanで1回以上成功
```

---

## 13. DB / Replay

### 13.1 SQLite DB

DBはSQLiteを使用する。

保存対象:

```text
hands
players
actions
recommendations
results
stats
```

---

### 13.2 ハンド保存

hand_end時にハンドを保存する。

保存するもの:

```text
hand_id
timestamps
hero cards
board
phase
participants
actions
recommendation
human action
result
```

---

### 13.3 参加者保存

参加者保存は `_participated_seats` を基準にする。

理由:

```text
最終in_current_handだけを見ると、fold済み参加者が漏れるため
```

---

### 13.4 street actions保存

street単位でactionsを保存する。

```text
preflop
flop
turn
river
```

Hero通常actionはturn boundary由来のみ保存する。

---

### 13.5 recommendation保存

Recommendationはcontextが有効な場合のみ保存する。

保存しないもの:

```text
- stale recommendation
- 古いSolver結果
- 古いfallback
- Hero turn終了後の結果
```

---

### 13.6 replay JSON保存

replay JSONには再現・監査に必要な情報を保存する。

含めるもの:

```text
seat_to_name
participated_seats
db_participant_names
street_actions
recommendation
human_action
GameState snapshot
```

---

### 13.7 audit_db_integrity.py

DBとreplayの整合性確認に使う。

用途:

```text
- participant count確認
- replay JSON確認
- DB保存漏れ確認
```
---

### 13.8 abandoned handの保存除外

以下の理由でactive handが中断・破棄された場合、そのhandはDB / replay / opponent statsへ保存しない。

理由:

```text
user_stop
capture_lost
table_invisible
hero_cards_unstable
```

対象状況:

```text
- ユーザーがhand途中でStopする
- アプリ終了・ウィンドウ終了
- capture lostで停止
- table invisibleが確定
- Heroカード矛盾が確定
```

挙動:

```text
- HandManager.abandon_current_hand(reason) を使う
- _transition_phase("hand_end") は使わない
- _on_hand_end() を通さない
- hand_historyへ保存しない
- replay JSONを保存しない
- opponent statsを更新しない
- phaseをwaitingへ戻す
```

注意:

```text
Hero foldだけではabandonしない。
Hero fold後もテーブル観察できる限り、handは継続観察する。
```

禁止:

```text
- 中断handを通常hand_endとして保存すること
- incomplete handを相手統計に混ぜること
```
---


## 14. config.yaml

### 14.1 capture

キャプチャ方式設定。

```yaml
capture:
  source: capture_card
```

候補:

```text
capture_card
mss
file
```

---

### 14.2 profile

座標プロファイル設定。

```yaml
profile:
  path: profiles/coinpoker_6max.json
```

---

### 14.3 game

ゲーム設定。

```yaml
game:
  blind_bb: 10
```

NEW_HAND閾値などに使用する。

---

### 14.4 recognition

```yaml
recognition:
  card_edge_threshold: 0.02
  card_gray_mean_min: 80.0
  card_gray_std_min: 20.0
  hero_card_confirm_frames: 2
  hero_card_mismatch_confirm_frames: 2
```

---

### 14.5 solver

Solver設定。

```yaml
solver:
  timeout_ms: 20000
  bridge_timeout_sec: 22.0
  max_iterations: 300
```

---

### 14.6 llm

LLM設定。

```yaml
llm:
  provider: openrouter
  model_default: openai/gpt-5.4-mini
  model_premium: openai/gpt-5.4-mini
  timeout_sec: 15.0
  openrouter_provider_order: OpenAI
  openrouter_allow_fallbacks: false
  openrouter_require_parameters: false
  openrouter_use_strict_json_schema: true
```

---

### 14.7 preflop_delta

DB統計補正しきい値。

```yaml
preflop_delta:
  sample_threshold_low: 50
```

---

### 14.8 logging

ログ設定。

```yaml
logging:
  level: INFO
```

---

### 14.9 replay

replay保存設定。

```yaml
replay:
  enabled: true
```

---

### 14.10 UI

UI設定。

```yaml
ui:
  hud_enabled: true
```

---

### 14.11 主要パラメータ表

| パラメータ | 用途 |
|---|---|
| blind_bb | NEW_HAND閾値 |
| participant_observation_duration_sec | 参加者観察窓 |
| card_edge_threshold | SeatCardDetector |
| card_gray_mean_min | SeatCardDetector |
| card_gray_std_min | SeatCardDetector |
| solver.timeout_ms | Solver request timeout |
| solver.bridge_timeout_sec | Python bridge timeout |
| sample_threshold_low | DB統計利用条件 |
| recognition.hero_card_confirm_frames | waiting中Heroカードを新hand候補として採用するために必要な連続一致フレーム数 |
| recognition.hero_card_mismatch_confirm_frames | active hand中にcached Heroカードとfresh OCRが矛盾した場合、Heroカード不安定と確定するために必要な連続矛盾フレーム数 |
| OPENROUTER_PROVIDER_ORDER | OpenRouter provider固定順 |
| OPENROUTER_ALLOW_FALLBACKS | OpenRouter provider fallback許可 |
| OPENROUTER_REQUIRE_PARAMETERS | OpenRouter provider parameter必須指定 |
| OPENROUTER_USE_STRICT_JSON_SCHEMA | 対応LLMタスクでresponse_format=json_schemaを使うか |


### 14.12 deep_cfr

Deep CFR推論設定。

```yaml
deep_cfr:
  model_path: models/deep_cfr/best_checkpoint.pt
  device: cuda
  fallback_to_solver: true
```

| パラメータ | 用途 |
|---|---|
| deep_cfr.model_path | 本番推論用チェックポイントのパス |
| deep_cfr.device | 推論デバイス（cuda / cpu） |
| deep_cfr.fallback_to_solver | Deep CFR利用不可時にRust postflop CLIへfallbackするか |
```

---

## 15. ログ

### 15.1 ログ方針

重要な状態遷移・推奨生成・破棄・OCR異常はログに残す。

`print()` は使わずloggingを使う。

---

### 15.2 重要ログ一覧

```text
HUD computing callback failed
Hero fold badge ignored
Hero fold badge ignore latched
Hero FOLD detected via badge for seat 1
Hero action from frame actions ignored
Hero action recorded
Could not determine hero action
Waiting: hero cards recognized
Waiting: hero cards recognized but suppressed as stale cards
Stale hero card suppression cleared
Phase fast-forwarded
Phase fast-forward suppressed
Strategy skipped: phase/board_count mismatch
Async recommendation started
Async recommendation accepted
Async recommendation discarded
HU solver request
HU solver success
HU solver failed
source=fallback
source=solver
Pot spike detected
Pot spike confirmed
NEW_HAND confirmed
NEW_HAND during active hand
LLMPipeline initialized
LLM startup check: OK
LLM startup check: FAILED
LLM request start
LLM API response
LLM API error response
LLM validation passed
strict_json=True
strict_json=False
Hero delayed action replaced boundary CHECK
Hand abandoned without saving
Strategy deferred: reason=pot_spike_hold
WAITING FOR STABLE POT...
Waiting hero cards candidate
Waiting hero cards stable
Hero cards mismatch candidate
Hero cards invalidated for hand
Active hand abandoned because hero cards became unstable
Strategy skipped: hero cards unstable
HERO CARDS UNSTABLE
SOLVER_REQUEST_META_INCOMPLETE
HU_SOLVER_HERO_HAND_NOT_FOUND
solver_parse_audit
strategy_source_detail=hand_strategy
strategy_source_detail=average_strategy_fallback
hero_cards_missing
matched_hand_missing
hero_range_contains_hand=false
hero_range_missing_reason
```

---

### 15.3 ライブテストで確認するログ

次回ライブでは以下を重点確認する。

```text
Hero delayed action replaced boundary CHECK
Hand abandoned without saving
Strategy deferred: reason=pot_spike_hold
WAITING FOR STABLE POT...
Waiting hero cards candidate
Waiting hero cards stable
Hero cards mismatch candidate
Hero cards invalidated for hand
Strategy skipped: hero cards unstable
HERO CARDS UNSTABLE
LLM request start
strict_json=True
LLM API response
LLM validation passed
fallback=false
HU solver request
HU solver failed
Async recommendation discarded
SOLVER_REQUEST_META_INCOMPLETE
HU_SOLVER_HERO_HAND_NOT_FOUND
strategy_source_detail=hand_strategy
strategy_source_detail=average_strategy_fallback
hero_range_contains_hand=false
hero_range_missing_reason
```

---

### 15.4 WARNING / ERROR の扱い

WARNING / ERRORは必ず原因を確認する。

ただし、既知の一時OCR揺れやeval7初回ウォームアップ系は、再発頻度と影響範囲で判断する。


### 15.5 ログ方針

ログは以下に分ける。

```text
通常運用ログ:
  hand start / hand end / phase transition / recommendation / major guard

検証ログ:
  Solver request detail / OCR detail / range context / debug JSON / compare request

重複抑制対象:
  SOLVER_START_SUPPRESSED
  SOLVER_HUD_RUNNING_DETAIL
  POSITION_LOCK_SKIPPED
  同一request_idのHUD computing message
```

方針:

```text
- 同じ状態を毎frame INFOで出さない
- 初回・状態変化・一定時間経過時だけINFOにする
- 毎frame級の確認ログはDEBUGへ落とす
- ログ削減で重要な状態変化を消さない
```

---

## 16. テスト方針

### 16.1 単体テスト

対象:

```text
recognition
hand_manager
recommendation_engine
solver_request_builder
multiway_engine
```

---

### 16.2 統合テスト

対象:

```text
game_loop
recommendation routing
hand lifecycle
stale recommendation
async solver
```

---

### 16.3 GUIテスト

対象:

```text
main_window
hud_overlay
Operation UI表示
Hand ID表示
Cards表示補正
show_computing(message)
```

---

### 16.4 Solverテスト

対象:

```text
solver_request_builder
solver_bridge
actions_played
node_strategy
timeout
```
追加確認項目:

```text
Solver request meta保存:
  hero_cards
  board
  street
  heads_up
  num_players
  facing_bet
  call_amount
  hero_position
  hero_is_ip
  current_street_actions
  preflop_actions

Solver parse audit:
  strategy_source_detail=hand_strategy
  strategy_source_detail=average_strategy_fallback
  hero_cards_missing
  matched_hand_missing
  hero_hand_candidates
  matched_hand
  matched_hand_index

Hero hand matching:
  元順
  逆順
  rank順

Hero range membership:
  hero_range_contains_hand=true
  hero_range_contains_hand=false
  hero_range_missing_reason

Teacher採用除外:
  average_strategy_fallback
  matched_hand_missing
  hero_range_contains_hand=false
  solver_error
```

---

### 16.5 LLMテスト

対象:

```text
multiway_decision JSON
pot odds input
required equity guard
LLM latency log
```
追加確認項目:

```text
OpenRouter provider payload
strict JSON Schema ON/OFF
reason_generation excludes response_format
400 response body logging
```

Blind LLM検証の追加確認項目:

```text
Solver/teacher情報をpromptに含めない:
  primary Solver action
  primary Solver probabilities
  teacher_label
  allowed_sizing_types
  profile_actions
  range membership audit result

Solverと同等の実戦情報をpromptに含める:
  hero_cards
  board
  pot
  effective_stack
  SPR
  hero_position
  hero_is_ip
  actions_played
  legal_actions
  facing_bet
  call_amount
  street
  heads_up
  num_players

入力不足時:
  Blind LLM検証結果を本番採用判断に使わない
```
---

### 16.6 ライブテスト

ライブテストでは、テストで再現しづらい以下を確認する。

```text
OCR揺れ
CoinPoker演出
チップアニメーション
Showdown
Hero turn timing
Solver timeout
UI表示
```
追加確認項目:

```text
pot_spike_hold中のstrategy defer
Heroカード安定化
Heroカード矛盾時のabandon
OpenRouter strict_json=True
Multiway LLM入力補強
途中離席/Stop/table invisible hand保存除外
```

HU Solver / teacher監査の追加確認項目:

```text
新規Solver request JSONに以下が保存されている:
  hero_cards
  facing_bet
  call_amount
  street
  heads_up
  num_players
  hero_position
  hero_is_ip

solver_parse_auditで確認する:
  hand_strategy_count
  average_strategy_fallback_count
  hero_cards_missing_count
  matched_hand_missing_count
  hero_range_contains_count
  hero_range_missing_count

teacherとして使わない:
  hero_cards_missing
  matched_hand_missing
  average_strategy_fallback
  hero_range_contains_hand=false
  solver_error
```
---

### 16.7 現在の期待テスト結果

```text
pytest -q
1282 passed, 7 warnings
```
---

### 16.8 追加重点テスト

```text
- OpenRouter provider設定がpayloadへ入る
- strict JSON Schema ON/OFFでresponse_format有無が切り替わる
- reason_generationにはresponse_formatを付けない
- Hero CHECK直後の遅延CALL/BET/RAISE/ALL_INでCHECKが置換される
- Stop / capture_lost / table_invisibleでactive handがDB保存されない
- pot_spike_hold中はstrategy requestを開始しない
- waiting中Heroカードは連続一致するまでhand開始しない
- active hand中Heroカード矛盾が2回連続したらabandonされる
- visual obstruction中のHeroカード矛盾は無視される
```
---

## 17. 禁止事項・安全制約

### 17.1 自動操作禁止

本システムは自動操作しない。

禁止:

```text
- 自動クリック
- 自動ベット
- 自動フォールド
- 自動入力
```

---

### 17.2 暫定推奨禁止

未確定の推奨を表示しない。

禁止:

```text
- 暫定CALL
- 暫定CHECK
- timeout時NO SIGNAL推奨
- 後から上書きする推奨
```

---

### 17.3 古い推奨表示禁止

stale contextの推奨を表示しない。

対象:

```text
Solver
LLM
fallback
chart cache
previous recommendation
```

---

### 17.4 GameState本体を書き換えないUI補正

UI表示補正のためにGameState本体を書き換えない。

例:

```text
Cards列の表示補正では player.cards_visible を変更しない。
```

---

### 17.5 Builderが変更してはいけない領域

個別指示がない限り、Builderは以下に触らない。

```text
- DB schema
- replay形式
- Solver Rust CLI
- LLM prompt
- RecommendationEngine routing
- GameState構造
- config.yaml
```

ただし、Commander指示で明示された場合は除く。

---

### 17.6 追加禁止事項

```text
- Heroカード1フレーム認識だけで新handを開始すること
- active hand中にfresh OCRのHeroカードでcached Heroカードを即上書きすること
- Heroカード不安定handでChart / Solver / LLM / fallback推奨を出すこと
- Heroカード不安定handをDB/replay/opponent statsへ保存すること
- pot_spike_hold中に古いpotと新しい巨大betを組み合わせてSolver/LLMへ渡すこと
- frame由来Hero通常actionを無条件でstreet actionへ保存すること
- interrupted / abandoned handを通常hand_endとして保存すること
- OpenRouter APIキーやprompt全文を通常ログに出すこと
```

---

## 18. 2026-05-16 追補仕様: 金額OCR再読確認・直近Fix反映

この章は、Fix65〜Fix67-B後のライブテストと設計見直しを反映した現在仕様である。  
過去の「大きい金額を一律除外する」発想は、オンラインポーカーのALL-IN頻度を考えると危険であるため、今後の正仕様は**再読確認方式**とする。

---

### 18.1 GameLoop正規1フレーム後処理の共通化

GUIライブ実行では `GameLoop.start()` ではなく `main.py` の `GameLoopWorker.run()` が使われる。  
そのため、両者は必ず同じ後処理メソッドを使う。

正規順序:

```text
process_one_frame()
↓
process_game_state_after_frame(game_state)
  1. 無効seat action除外
  2. 金額OCR再読確認/金額状態確認
  3. HandManager.process_frame(game_state)
  4. Hero Fold badge pending recovery
  5. HandManager同期
  6. position lock更新/適用
  7. strategy処理
↓
GUI signal emit
```

禁止:

```text
- GameLoop.start() と GameLoopWorker.run() に別々の処理順を持たせること
- GUI Worker側だけ正規後処理を通さないこと
- _handle_strategy() を二重実行すること
```

---

### 18.2 無効seat action除外

`ActionRecord.seat` は `1〜6` のみ有効とする。  
`seat=0` は実プレイヤーではないため、保存・推奨材料化してはならない。

対象外にするもの:

```text
- street actions
- _all_actions
- current_street_actions
- preflop_actions
- DB
- replay
- Solver input
- LLM input
```

ログ例:

```text
Ignored invalid action seat=0: action=CHECK amount=0 confidence=low ...
```

---

### 18.3 Hero turn / Recommendation latencyログ

Hero turn認識の遅延と、推奨計算そのものの遅延を切り分けるため、以下をログ出力する。

```text
Hero turn started context:
  hand_id
  phase
  pot
  hero_bet
  max_bet
  current_street_actions
  preflop_action_count

Preflop recommendation:
  turn_to_recommendation_ms
```

判断:

```text
turn_to_recommendation_ms が小さい場合:
  Chart計算は遅くない。Hero turn検出や状態安定化が遅い。

turn_to_recommendation_ms が大きい場合:
  Chart処理・context破棄・再計算・defer要因を調査する。
```

---

### 18.4 大口金額OCRの再読確認方式

#### 18.4.1 怪しい金額の定義

怪しい金額とは、即エラーではなく、**再読確認が必要な大きな金額変化**である。

初期判定条件:

```text
- 新POT - 前POT >= 50BB
- action amount >= 50BB
- 新POT >= 前POT * 5
```

対象action:

```text
BET
RAISE
CALL
ALL_IN
```

対象phase:

```text
preflop
flop
turn
river
```

重要:

```text
大きい金額 = 誤認 ではない。
大きい金額 = 再読確認対象。
```

オンラインポーカーでは大型BET/ALL-INが頻出するため、金額の大きさだけで除外してはならない。

---

#### 18.4.2 再読確認フロー

怪しい金額を検出した場合、次の順で処理する。

```text
怪しい金額を検出
↓
即座にPOT / player bet / stackを再読
↓
再読値が初回値と整合
  → 本物として採用
↓
再読値が初回値と不一致
  → 認識error扱い
  → そのフレームでは推奨を出さない
  → 次フレームで通常認識へ戻す
```

ログ例:

```text
Amount recheck requested: hand_id=... phase=... reason=large_pot_jump pot_old=... pot_new=... actions=...
Amount recheck accepted: hand_id=... phase=... pot=... actions=...
Amount recheck failed: hand_id=... phase=... first_pot=... reread_pot=... first_actions=... reread_actions=...
Strategy deferred: reason=amount_recheck_failed
```

---

#### 18.4.3 採用してよいケース

以下のようにPOT増加とaction額が整合する場合、再読でも一致すれば本物として採用する。

```text
前POT 546
新POT 34886
POT増加 34340
seat2 ALL_IN 34340
```

このケースを「大きいから」という理由だけで除外してはならない。

---

#### 18.4.4 認識error時の処理

再読で不一致だった場合、そのフレームの怪しい金額は採用しない。

行うこと:

```text
- 怪しいactionをstreet actionsへ保存しない
- 怪しいpotをstrategy入力へ使わない
- 怪しいbet / max_betをstrategy入力へ使わない
- DB/replayへ保存しない
- Solver/LLM/Chartを開始しない
- HUDには処理状態を表示する
```

表示候補:

```text
WAITING FOR STABLE AMOUNT...
```

禁止:

```text
- actionだけ消してpotだけ巨大値を残すこと
- potだけ採用してaction履歴が空のまま推奨を出すこと
- 大型ALL-INを一律除外すること
```

---

### 18.5 Solver fallback理由ログ

HU postflopでfallbackが出た場合、必ず理由をログで判定可能にする。

ログ例:

```text
HU solver fallback reason=solver_unavailable
HU solver fallback reason=request_unavailable
HU solver fallback reason=solver_failed
HU solver fallback reason=parse_exception
HU fallback entered: ...
Async fallback recommendation accepted: ...
```

Solver遅延調査では、いきなりSolver設定を軽量化せず、まず処理内訳ログを追加する。

見るべき内訳:

```text
input build
tree build
solve
output parse
CLI通信
async stale判定
```

---

### 18.6 LLM reasoning品質ガード

LLMの `reason` が以下の場合、不十分な説明として扱う。

```text
- "日本語"
- "日本語:"
- "日本語で簡潔に:"
- "Reason:"
- "Reasoning:"
- 空文字
- 極端に短い文字列
```

対応:

```text
1. 接頭辞をsanitizeする
2. sanitize後に説明が空/短すぎる場合はquality error
3. action自体が妥当なら、metricsから定型reasonを生成する
4. ログに reason_sanitized / reason_replaced を出す
```

定型reasonには、可能な限り以下を含める。

```text
hero_equity
required_equity
pot odds
facing_bet
active_player_count
source
fallback有無
```

---

### 18.7 Hero turn音通知

Hero turn開始時、ユーザーが画面から目を離していても気づけるように音通知を導入する。

仕様:

```text
- Hero turn started の瞬間にやさしい通知音を鳴らす
- ON/OFF設定を持つ
- 音量0〜100を調整可能にする
- 同一Hero turn中は1回だけ鳴らす
- waiting / hand_end / Heroがhand外では鳴らさない
```

---

### 18.8 hand start latency改善方針

Heroカードは誤認防止のため2回連続一致を要求する。  
このため、カード配布からhand startまで2〜3秒遅れる場合がある。

改善方針:

```text
- まずhand start latencyログを追加する
- start表示と推奨表示を分離する
- Heroカード高信頼時のみ仮startを検討する
- 推奨表示はHeroカード確定後を維持する
```

禁止:

```text
Heroカード1フレーム認識だけで推奨を出すこと
```

---

### 18.9 現在の優先順位

```text
1. 金額OCR再読確認方式への変更
2. Solver遅延 / fallback実原因の調査
3. LLM reasoning sanitize / quality guard
4. Hero turn音通知
5. hand start latency改善
6. active hand中のdealer再ロック抑制
7. EQ / EV / Source表示改善
```
