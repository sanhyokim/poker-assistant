# ポーカーAIアシスタントシステム — SPEC.md
**Version:** 3.8  
**Updated:** 2026-06-06 JST  
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
- MultiwayポストフロップはPokerSkill式Context Engine + LLM判断を主軸とし、eval7を数理補助に使う
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
| HU/Multiway推論 | PokerRL+GRPO（Deep CFRはLegacy fallback） |
| Solver連携 | Rust postflop CLI（廃止予定） |
| LLM | OpenRouter API（exploit補正用途のみ） |
| ローカルLLM推論 | transformers 5.10.2（Phi-4-mini ローカル推論用） |
| 量子化 | bitsandbytes（4bit量子化用） |
| テスト | pytest |


---

### 2.3 外部依存

外部依存:

- OpenRouter API（exploit_adjustment用途のみ）
- PokerRL+GRPO訓練済みモデル（ローカル量子化モデル）
- Legacy Deep CFR訓練済みモデル（Stage D完了までのfallback）
- CoinPokerデスクトップクライアント
- キャプチャカードまたは画面キャプチャ

廃止予定:

- postflop-solver Rust CLI（PokerRL+GRPO統合完了後に廃止）


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
1441 passed, 0 failed
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
┌──────────────┬────────────────────┬─────────────────────────┐
│ Preflop      │ HU Postflop        │ Multiway Postflop        │
│ Chart        │ PokerRL+GRPO推論   │ PokerSkill式Context      │
│ + DB補正     │ + exploit補正      │ Engine + LLM + eval7補助 │
└──────────────┴────────────────────┴─────────────────────────┘
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
POKERRL THINKING...
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
→ PokerRL+GRPO推論

phase in {flop, turn, river} and active_player_count >= 3
→ PokerSkill式Context Engine + LLM判断 + eval7数理補助
  （詳細は実装指令書v1.3 §3.1.5およびDESIGN_NOTES §54-55を参照）

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

HU postflopはPokerRL+GRPO推論を主軸に判断する。
Deep CFR推論はフォールバックとして残す（Stage D完了まで）。

基本:

```text
active_player_count == 2
phase in {flop, turn, river}
```

PokerRL+GRPO推論はローカルGPU上の常駐推論プロセスで実行する。
応答速度はT1 Tierとして50-300msを目標とする。
全Postflop推論はAsyncで実行し、GameLoopを止めない。

処理中表示:

```text
POKERRL THINKING...
```

PokerRL+GRPO結果はcontext一致時のみ採用する。

PokerRL+GRPO推論失敗時のフォールバック:

```text
Flop HU: Deep CFR → LLM → スキップ（Stage D完了まで保持）
Turn/River HU: Deep CFR → LLM → スキップ（Stage D完了まで保持）
全失敗時: 推奨なし（暫定推奨は出さない）
```

注記: Rust postflop CLI（Solver）はフォールバック経路から除外する。
Deep CFRはStage D（PokerRL+GRPO統合完了）後に廃止する。

exploit_adjustment:

```text
DB統計が十分な相手（total_hands >= sample_threshold_low）に対しては、
PokerRL+GRPO出力を既存LLM exploit_adjustmentで補正する。
補正はPokerRL+GRPO推論完了後に同期的に行う。
補正後のstrategy_sourceは "pokerrl_exploit" となる。
補正失敗時はPokerRL+GRPO元推奨をそのまま返す。
```

出力形式:

```text
PokerRL+GRPOは以下を返す。
- fold_prob: float
- call_prob: float
- raise_prob: float
- allin_prob: float
- raise_size_ratio: float（ポット比）

推奨アクションは最も確率が高いアクションとする。
raise_amountはraise_size_ratioからチップ額に変換する。
```

confidence判定:

```text
- top_prob >= 0.70 → high
- top_prob >= 0.45 → medium
- top_prob < 0.45 → low
```

---

### 9.4 Multiway Postflop

Multiway postflopは、PokerSkill式Context Engine + LLMで判断する。
Context Engineは決定論的に局面ラベル、ATT/DEF budget、viable actionを算出し、
LLMは制約された行動空間内で最終アクションとサイジングを選ぶ。

適用条件:

```text
active_player_count >= 3
phase in {flop, turn, river}
hero_in_current_hand == true
```

#### 9.4.1 第1層: PokerSkill式Context Engine（決定論的、訓練不要）

GameStateから以下の特徴量を決定論的に計算する。

```text
- board texture: dry / slightly_wet / wet / very_wet + special board labels
- hand class: Made-Hand 15クラス + Draw 8クラス
- pot type: limp / SRP / 3BP / 4BP+
- position: IP / OOP / sandwich / closing_action
- initiative: preflop_aggressor / caller / postflop_aggressor / defender
- SPR bucket: low (<3) / medium (3-10) / deep (>10)
- ATT/DEF base budget: hand classごとの攻撃予算・防御予算
- pressure weight累積: street内および複数streetのbet/raise圧力
- MW修正子: active opponent数、sandwich、背後人数、pot typeによる補正
- viable actions: fold / check / call / bet / raise / all-in の許可リスト
```

Context Engineの出力は構造化プロンプトとしてLLMに渡される。
Context Engine自体はPythonスクリプトであり、モデル推論を行わない。

実装ファイル: `strategy/context_engine.py`（Step 1〜3で新規実装済み）
統合ファイル: `strategy/multiway_engine.py`（Step 4でGameLoop統合済み）
テスト: `tests/test_context_engine.py`（82テスト PASS）

設計根拠: DESIGN_NOTES §54（PokerSkill論文分析）、§55（MW方針転換）

#### 9.4.2 第2層: LLM判断（制約された行動空間内）

Context Engineが生成した構造化プロンプトを受け取り、最終判断を行う。

LLM本採用:

```text
- GPT-5.4-mini（OpenRouter API経由） ★ MW本採用モデル確定（2026-06-06）
```

LLMバックアップ:

```text
- Phi-4-mini（ローカル4bit推論）
```

選定根拠（15ケース比較テスト結果）:

```text
- GPT-5.4-mini: JSON parse 15/15, Category match 12/15 (80%), Corrections 0/15, Avg latency 993ms
- Phi-4-mini: JSON parse 15/15, Category match 11/15 (73%), Corrections 4/15, Avg latency 1811ms
- GPT-5.4-miniが判断精度・reason品質・レイテンシ・GPU不要の4点で優位
- 結果ファイル: scripts/llm_comparison_results.json

load_dotenv(override=True) を使用すること（環境変数に古いキーが残る場合があるため）
```

行動制約:

```text
- viable_actionsに含まれないactionを出力してはならない
- 残ATT > 0 → bet/raiseを候補に含めてよい
- 残DEF > 0 → callを候補に含めてよい
- 残ATT <= 0 and 残DEF <= 0 → fold/check優先
- draw classはDEF budgetではなくpot-odds thresholdでcall可否を判定する
```

LLM出力はJSONのみとし、validatorで合法アクションに補正する。

#### 9.4.3 eval7数理補助

eval7はequity補助情報として維持する。

```text
- hero equity計算
- required equity計算（pot odds）
- all-in時のequity >= pot odds判定
- LLM fold時の数理ガード（hero_equity >> required_equityならCALLへ補正）
```

eval7結果は推奨の主軸ではなく、LLM判断の検証・補正に使う。

#### 9.4.4 フォールバック

Multiway postflopのフォールバック:

```text
Context Engine + LLM → eval7数理ガードのみ → スキップ
全失敗時: 推奨なし（暫定推奨は出さない）
```

注記: Deep CFRはMultiway postflopのフォールバックから除外する。
Deep CFRは品質不合格であり、MW判断にはContext Engine + LLMが代替する。

#### 9.4.5 処理中表示

```text
POKERRL THINKING...（Context Engine + LLM処理中）
```

MW Context Engine パイプライン（実装完了、Step 1〜4）:

```text
GameState → multiway_engine.evaluate()
  → _build_game_context() → game_context dict構築
  → _detect_pot_type() → SRP/3BP/4BP/LIMP判定
  → compute_full_budget() → ATT/DEF budget計算
  → build_context_prompt() → 構造化プロンプト生成
  → _call_mw_llm() → GPT-5.4-mini (OpenRouter)
  → validate_llm_output() → 補正済み action/amount/reason
  → fold_guard / value_bet_guard 適用
  → Recommendation として返却
```

#### 9.4.6 背景

MW戦略をPokerSkill式に転換した理由:

```text
1. phh-dataset hole cards付き726,570件が全件敗者（PHH仕様上、勝者hole cardsは記録されない）
   → multiway SFT用positive exampleが構造的に入手不可
2. PokerSkill論文（arXiv 2605.30094）の発見
   → 訓練なしでLLMのポーカー判断品質を大幅改善するフレームワーク
   → Context Engine + ATT/DEF Budget設計が公開されておりMW拡張可能
```

詳細: DESIGN_NOTES §53（phh-dataset敗者バイアス）、§54（PokerSkill論文分析）、§55（MW方針転換）
実装計画: 本セクション§9.4.7〜§9.4.18に記載

#### 9.4.7 Board Texture分類

入力はboard cards（3〜5枚）。board parserはrank/suitを正規化し、以下の多次元分類を
決定論的に計算する。streetによって存在しない分類は`false`にする。

Suit分類:

```text
rainbow: flopで全スート異なる
two_tone: flopで2枚が同スート、またはturn/riverで最大同スート数が2
monotone: flopで3枚が同スート
three_flush: turn/riverで3枚同スート（flush possible）
four_flush: 4枚同スート（one-card flush danger）
five_flush: 5枚同スート（board flush）
```

Rank分類:

```text
paired: board上に同ランク2枚
trips_board: board上に同ランク3枚
quads_board: board上に同ランク4枚
double_paired: board上に2組のペア
straight_possible: 任意の2-card comboでストレート完成可能
one_card_straight: 1枚でストレート完成可能
one_card_straight_open_ended: 両端の1枚でストレート完成可能
one_card_straight_gutshot: 内側の1枚でストレート完成可能
board_straight: board 5枚のみでストレート成立
```

Texture総合ラベル:

```text
dry:
  rainbow, no straight_possible, no paired, high-card dominated
slightly_wet:
  two_tone or straight comboが1種類
wet:
  flush draw + straight drawが共存、または複数straight combo
very_wet:
  monotone, three_flush + straight pressure, four_flush, board_straight,
  multiple straight + flush combos
```

Special board typesは通常ロジックをoverrideする。

```text
trips_board
double_paired
quads_board
board_full_house
board_flush
board_straight
```

Special boardでは§9.4.11のATT/DEF override tableを優先し、通常のpair/high-card分類を
そのまま適用してはならない。

#### 9.4.8 Hand Class分類（23クラス）

入力はhero hole cards + board cards。eval7でhand evaluationを行い、絶対役とdrawを
別々に分類する。hero handがmade-hand + drawの両方に該当する場合はCombo ruleを適用する。

Made-Hand Classes（15クラス）:

```text
nuts:
  Full house+, Nut flush, Nut straight(safe board), Set(safe board)
flush:
  Non-nut flush。3-flush board / paired board / one-card flush boardで細分化
straight:
  Two-card straight / One-card straight（top-end / low-end）
set:
  Pocket pair hits board。board danger別に分類
trips:
  Board pair + one hole card
two_pair:
  No board pair。10段階rank + board texture condition-matrix
overpair:
  Pocket pair above all board cards
top_pair:
  Top board card paired with hole card。kicker: TPTK / TPSK / 3rd〜other
second_pair:
  1 board overcard above hero's pair
third_pair:
  2 board overcards above hero's pair
fourth_fifth_pair:
  3-4 board overcards above hero's pair
nuts_high:
  A-high（highest rank not on board）
second_high:
  K/Q-high（2nd-highest rank, kicker T+）
weak_showdown:
  Low-kicker high card or very marginal unpaired hand
trash:
  No pair, no overcard, no draw
```

Draw Classes（8クラス）:

```text
strong_draw:
  combo draw on non-flushy / nut+ flush draw on flushy /
  flush draw rank>=J on non-flushy / OESD on rainbow non-straighty
medium_strong_draw:
  flush draw rank<J on non-flushy / OESD on rainbow+straighty /
  OESD on two-tone non-straighty
medium_draw:
  decent flush draw on flushy / OESD on two-tone+straighty / gutshot + overcards
medium_weak_draw:
  non-flushy 2-card gutshot / moderate flush draw rank 6+ on flushy
weak_draw:
  bottom gutshot / very small flush draw rank 2-5 on flushy
strong_overcard_draw:
  AK/AQ two premium overcards / KQ or AJ + backdoor
medium_overcard_draw:
  naked dual overcards rank sum > 19 / single overcard T+ with dual backdoor
weak_overcard_draw:
  naked dual overcards rank sum <= 19 / single overcard T+ with BD but no between-pair
```

Combo rule:

```text
- made-hand ATT base + draw ATT bonusを比較し、高い方をATT採用
- made-hand DEF base + draw combo bonusをDEF候補にする
- made-hand DEF単体とcombo DEF候補を比較し、高い方をDEF採用
- draw単体がmade-handより強いATT/DEFを持つ場合はdraw側を採用
- riverでは未完成drawのDEF thresholdを無効化し、made-hand/high-card分類だけで判定する
```

---

##### 9.4.8.1 Made-Hand分類の実装ロジック

```text
入力: hero_cards[2], board_cards[3-5]（eval7カード表現）
出力: made_hand_class（15クラスのいずれか）

手順:

1. eval7で5-7枚のhand rankを取得（hand_type: straight_flush, four_of_a_kind, full_house, flush, straight, three_of_a_kind, two_pair, one_pair, high_card）

2. hand_type別の分類:
   - straight_flush → nuts（§9.4.8.5 nuts判定で最終確認）
   - four_of_a_kind → nuts（§9.4.8.5 nuts判定で最終確認）
   - full_house → 相対強度で nuts / set / two_pair を検討（後述）
   - flush → flush
   - straight → straight
   - three_of_a_kind → set or trips（後述）
   - two_pair → two_pair
   - one_pair → pair分類（後述）
   - high_card → nuts_high / second_high / weak_showdown / trash（後述）

3. Set vs Trips の判定:
   - hero_cards 2枚が同ランク and board上にそのランクが1枚 → set
   - hero_cards のうち1枚が board上の pair ランクと一致 → trips
   - board上に3枚同ランク → board trips（§9.4.11 Special Board Override参照）

4. Pair分類（one_pair の場合）:
   board_ranks_descending = board上のユニークランクを降順ソート
   hero_pair_rank = hero_cardsのうちboard上のランクと一致するカードのランク

   a) Pocket pair判定:
      hero_cards[0].rank == hero_cards[1].rank の場合:
        pp_rank = hero_cards[0].rank
        - pp_rank > max(board_ranks) → overpair
        - pp_rank == board_ranks_descending[0] → top_pair（board上の最高ランクとhit）
        - otherwise:
          overcards_on_board = board上でpp_rankより大きいユニークランクの数
          overcards_on_board == 1 → second_pair
          overcards_on_board == 2 → third_pair
          overcards_on_board >= 3 → fourth_fifth_pair

   b) Board hit判定（非pocket pair）:
      hero_pair_rank が board_ranks_descending[0] と一致 → top_pair
      hero_pair_rank が board_ranks_descending[1] と一致 → second_pair
      hero_pair_rank が board_ranks_descending[2] と一致 → third_pair
      それ以外 → fourth_fifth_pair

5. High card分類（eval7がhigh_cardを返した場合のみ本分類を使用）:
   hero_high = max(hero_cards[0].rank, hero_cards[1].rank)
   hero_second = min(hero_cards[0].rank, hero_cards[1].rank)
   - hero_high == A → nuts_high
   - hero_high == K and hero_second >= T → nuts_high
   - hero_high == K and hero_second < T → second_high
   - hero_high == Q → second_high
   - hero_high >= T → weak_showdown
   - hero_high < T → trash

6. Full house 相対強度:
   - hero pocket pair + board pair → full_house。nuts判定（§9.4.8.5）で最強か確認
   - nuts full house → nuts
   - non-nuts full house → set相当のATT/DEFを使用（DESIGN_NOTESで記録）

注意:
   - eval7のhand_typeとboard情報の組み合わせで全15クラスを決定論的に分類
   - eval7単体では overpair / top_pair / second_pair 等を区別できない
   - board上のランク順序比較は必ずユニークランクの降順で行う
   - Aは常にランク14として扱う（eval7のデフォルト）
```

##### 9.4.8.2 Kicker分類

```text
注記: kicker修正子の数値（ATT -0.3/-0.5/-0.8、DEF -0.2/-0.3/-0.5）は
論文に記載のない独自推定値であり、Phase 0-2テスト（§9.4.18）で調整する。

目的: top_pair等のMade-hand内でkicker強度を細分類し、ATT/DEF表のkicker修正子を適用

対象クラス: top_pair, overpair, second_pair, third_pair

手順:
1. pair_rank = ペアに使用されているランク
2. kicker_rank = hero_cardsのうちpairに使われていない方のランク
   （pocket pair の場合: kicker_rank = board上の最高ランクのうちpair_rankと異なるもの）
3. all_possible_kickers = {A, K, Q, J, T, 9, ..., 2} から board_ranks と pair_rank を除外し降順ソート
4. kicker_position = all_possible_kickers内でのkicker_rankの順位（0基準）

分類:
   kicker_position == 0 → top_kicker（TPTK等）
   kicker_position == 1 → second_kicker（TPSK等）
   kicker_position == 2 → third_kicker
   kicker_position >= 3 → weak_kicker

ATT/DEF適用:
   §9.4.9 の made_hand_class ベース値に以下のkicker修正子を加算:
   top_kicker: ATT +0.0, DEF +0.0（ベース値そのまま）
   second_kicker: ATT -0.3, DEF -0.2
   third_kicker: ATT -0.5, DEF -0.3
   weak_kicker: ATT -0.8, DEF -0.5

注意:
   - kicker修正子は §9.4.13 MW修正子の前に適用
   - kicker修正子適用後にATTが0未満になる場合は0にクランプ
   - DEFは0未満にクランプしない（負のDEFは「foldに強く傾く」を意味）
   - overpairのkicker分類は pocket pair のためboard最高ランクで判定
```

##### 9.4.8.3 Draw分類の実装ロジック

```text
入力: hero_cards[2], board_cards[3-5]
出力: draw_class（8クラスのいずれか、または None）
      draw_outs（推定アウツ数）

前提: Made-hand分類（§9.4.8.1）と並行して実行。両方該当する場合はCombo ruleで結合

手順:

1. Flush draw判定:
   all_cards = hero_cards + board_cards
   各スートの枚数を計数:
     suit_counts = {s: count for s in [h, d, c, s]}
     hero_suit_counts = {s: hero_cardsのうちそのスートの枚数}

   判定:
   - suit_counts[s] >= 5 → flush完成（Made-hand側で処理、draw判定不要）
   - suit_counts[s] == 4 and hero_suit_counts[s] >= 1 → flush_draw
     flush_draw_suit = s
     hero_flush_high = hero_cards内の同スートの最高ランク
     is_nut_flush_draw = hero_flush_high == A
     outs_flush = 9（基本）
   - suit_counts[s] == 3 and hero_suit_counts[s] >= 1 and len(board_cards) == 3:
     → backdoor_flush_draw（flop限定）
     outs_bd_flush = 0（直接的なoutsではなくPhase 0で確率的に扱う）

2. Straight draw判定:
   all_ranks = hero_cards + board_cards の全ランクをユニーク化
   A は 14 と 1 の両方で検査（A-2-3-4-5 wheel 対応）

   5枚スライドウィンドウ（14-10, 13-9, ..., 5-1）を検査:
   window_cards = all_ranks内でwindowに含まれるランク数
   hero_contribution = hero_cards のうちwindowに含まれる枚数

   判定（hero_contribution >= 1 を条件）:
   - window_cards == 5 → straight完成（Made-hand側で処理）
   - window_cards == 4:
     missing = window内で不在のランク
     hero_contribution >= 1:
       - missingがwindowの端 → open_ended_straight_draw (OESD)
         outs_oesd = 8
       - missingがwindow中間 → gutshot_straight_draw
         outs_gutshot = 4
   - window_cards == 3 and len(board_cards) == 3:
     → backdoor_straight_draw（flop限定）

   複数のwindowで該当する場合は最も強い（outs最多の）ものを採用

3. Draw class分類:
   flush_draw と straight_draw の組み合わせで8クラスに分類:

   - strong_draw:
     nut_flush_draw + OESD （outs ≈ 15）
     または nut_flush_draw + pair（outs ≈ 12+）
   - medium_strong_draw:
     non-nut flush_draw + OESD（outs ≈ 12-14）
     または nut_flush_draw 単独（outs = 9）
   - medium_draw:
     non-nut flush_draw 単独（outs = 9、ただしhero_flush_high < K）
     または OESD 単独（outs = 8）
   - medium_weak_draw:
     gutshot + backdoor_flush（outs ≈ 6-7）
     または flush_draw with paired board（割引 outs）
   - weak_draw:
     gutshot 単独（outs = 4）
   - strong_overcard_draw:
     AK, AQ, KQ（overcards 2枚、うち少なくとも1枚A or K）+ backdoor flush or backdoor straight
   - medium_overcard_draw:
     AJ, AT, KJ, KT 等（overcards 2枚）+ no backdoor
     または A単独overcard + backdoor flush + backdoor straight
   - weak_overcard_draw:
     A単独overcard のみ、backdoor なし
     または K単独overcard

4. アウツ割引:
   - paired board: flush outs を -1（full house で負ける可能性）
   - monotone board (3-flush): straight outs を -2（フラッシュ完成で負け）
   - 上記割引後のoutsでクラス判定を再評価
   - 割引は §9.4.7 board texture の flush possible / paired 判定と連動

注意:
   - Turn/River では backdoor は存在しない（flop限定）
   - River では draw class = None（全て Made-hand として評価）
   - hero_contribution == 0 の straight window は除外（board だけで straight が可能でも hero の draw ではない）
```

##### 9.4.8.4 Combo rule の実装詳細

```text
前提: §9.4.8.1 の made_hand_class と §9.4.8.3 の draw_class が両方 non-None

結合ルール:
1. ATT結合:
   combined_ATT = made_hand_ATT + draw_combo_bonus
   draw_combo_bonus は §9.4.10 の各draw classの "Combo bonus" 値

2. DEF結合:
   combined_DEF = made_hand_DEF + draw_combo_bonus
   （DEFにもcombo bonusを加算）

3. Draw threshold の扱い:
   made_hand が pair以上 → draw threshold は使用しない（made_hand のDEFで防御判定）
   made_hand が high_card系（nuts_high / second_high / weak_showdown / trash）→ draw threshold を使用

4. River での再分類:
   Draw が完成しなかった場合（river時点でflush_draw/straight_draw が完成しない）:
     draw_class = None に変更
     made_hand_class のみで評価
     combo bonus は消滅
   Draw が完成した場合:
     made_hand_class を完成した手役に更新（例: flush_draw → flush）
     draw_class = None
     combo bonus は不要（made_hand が更新済み）

5. 実装上の処理順序:
   a) eval7 で hand_type 取得
   b) §9.4.8.1 で made_hand_class 決定
   c) §9.4.8.2 で kicker修正子 適用
   d) §9.4.8.3 で draw_class 決定
   e) 両方 non-None なら combo rule 適用
   f) §9.4.9/§9.4.10 から ATT/DEF 取得
   g) §9.4.14 の Budget計算順序へ進む
```

##### 9.4.8.5 Nuts判定の実装ロジック

```text
目的: 現在のboard + hero_cardsが、そのboardで可能な最強handかどうかを判定

入力: hero_cards[2], board_cards[3-5]
出力: is_nuts（bool）, nuts_distance（0 = nuts, 1 = 2nd nuts, ...）

手順:
1. remaining_deck = 52枚 - board_cards - hero_cards
2. hero_strength = eval7.evaluate(hero_cards + board_cards)
3. 全可能な2枚組み合わせを列挙: C(remaining_deck, 2)
   - flop(3枚board): C(47,2) = 1081 通り
   - turn(4枚board): C(46,2) = 1035 通り
   - river(5枚board): C(45,2) = 990 通り
4. 各組み合わせ opp_cards について:
   opp_strength = eval7.evaluate(opp_cards + board_cards)
   strongest = max(strongest, opp_strength)
5. is_nuts = (hero_strength >= strongest)
6. nuts_distance: hero_strengthが全可能handの強度分布の中で何番目か
   0 = 最強（nuts）、1 = 2番目、2 = 3番目 ...

計算量:
   eval7 は ~1μs/call
   最大 1081 * 1μs ≈ 1.1ms
   リアルタイム使用に問題なし

使用箇所:
   - made_hand_class 判定: is_nuts == True → class を "nuts" に設定
   - nuts_distance <= 2 → "near_nuts" として ATT を nuts レベルに近づける（Phase 0 で検証）
   - Viable Action Logic（§9.4.15）: nuts は常に bet/raise viable
   - River bluff-catch: nuts_distance が大きい場合に defensive に

注意:
   - Board上で既にstraight/flushが完成可能な場合、nuts判定は正確にそれを考慮
   - Combo draw が nuts に近い場合は draw_class 側ではなく made_hand_class 側で nuts 判定
   - eval7 の evaluate() は7枚から最強5枚を自動選択するため、5枚board でも正しく動作
```

#### 9.4.9 ATT/DEF Budget Table（Made-Hand）

以下の表はHU論文（PokerSkill Appendix E）のbase値。MW修正子は§9.4.13で別途定義する。
`∞`は実装上のsentinel（例: `float("inf")`）で表す。

nuts:

```text
ATT ∞ / DEF ∞。常にcall/raise。pot積極構築。
low SPR → 1 street slow-play可。
river IP nuts → 必ずraise。
```

flush:

```text
3-FLUSH BOARD:
  Nut flush ATT/DEF ∞。
  Big flush(high>9) ATT 5/DEF 6。
  Small flush(high<=9) ATT 4/DEF 5。
PAIRED BOARD:
  Nut flush ATT 5/DEF 6。
  Big flush ATT 4.3/DEF 5.3。
  Small flush ATT 3.5/DEF 4.5。
ONE-CARD FLUSH BOARD(4+ same suit):
  Nut ∞。
  2nd ATT 4/DEF 5。
  3rd ATT 3/DEF 4。
  4th ATT 2.4/DEF 3.5。
  5th ATT 2/DEF 3。
  6-7th ATT 1.5/DEF 2.5。
  8-9th ATT 1/DEF 2。
  PAIRED BOARD: nut ATT 4.5/DEF 5.5、others ATT/DEF -0.5。
```

straight:

```text
TWO-CARD STRAIGHT:
  No flush ATT 5.5/DEF 6.5。
  Flush possible(3-flush) ATT 3.5/DEF 4.5。
  4+ flush ATT 0/DEF 1。
  PAIRED BOARD: nut+no flush ATT 5/DEF 6、otherwise ATT/DEF -0.4。
ONE-CARD STRAIGHT TOP-END:
  No flush → NUTS。
  3-flush ATT 2.5/DEF 3.5。
  4+flush ATT 0/DEF 1。
ONE-CARD STRAIGHT LOW-END:
  No flush ATT 2.5/DEF 3.5。
  3-flush ATT 1.5/DEF 2.5。
  4+flush ATT 0/DEF 0.5。
  PAIRED BOARD: ATT/DEF -0.6。
```

set:

```text
No flush, no straight possible → NUTS (ATT/DEF ∞)。
No flush, 2-card straight:
  1 possibility ATT 5.5/DEF 6.5。
  2 possibilities ATT 4.5/DEF 5.5。
  3+ possibilities ATT 4/DEF 5。
3-flush(no OCS): ATT 3.8/DEF 4.8、each additional straight -0.3。
OCS 1 type: ATT 2.5/DEF 3.7。
OCS 2+: ATT 1.5/DEF 2.7。
OCS+3-flush: ATT/DEF -0.8 from OCS base。
OCS+4+flush: ATT 0/DEF 0.5。
4+ flush(no OCS): ATT 0/DEF 1。
```

trips:

```text
Dry board(no flush/OCS):
  ATT 4(2-kicker)〜4.5(A-kicker)/DEF 5.5、each 2-card str -0.3。
3-flush(no OCS):
  ATT 3〜3.5/DEF 4.5、each 2-card str -0.3。
OCS 1 type:
  ATT 2.1〜2.4/DEF 3.5。
OCS 2+:
  ATT 1.1〜1.4/DEF 2.5。
OCS+3-flush:
  ATT/DEF -0.7 from OCS base。
OCS+4+flush:
  ATT 0/DEF 0.5。
4+ flush(no OCS):
  ATT 0/DEF 1。
Kicker scales proportionally: 2→base, A→max。
```

two_pair:

```text
優先度順condition-matrix:
4+FLUSH+OCS:
  ATT 0/DEF 0.5。
4+FLUSH no OCS:
  ATT 0/DEF 1。
OCS+3-FLUSH:
  1 type ATT 1.7/DEF 2.8。
  2+ ATT 0.7/DEF 1.9。
OCS no flush:
  1 type ATT 2.2/DEF 3.3。
  2+ ATT 1.2/DEF 2.4。
3-FLUSH no OCS:
  rank-based ATT 2.7(r10)〜3.6(r1)/DEF 3.7(r10)〜4.7(r1)。
  each 2-card str -0.3。
DRY BOARD:
  R1 ATT 5/DEF 6.5。
  R2 4.7/6。
  R3 4.5/5.7。
  R4 4.3/5.5。
  R5 4.1/5.3。
  R6 3.9/5.1。
  R7 3.8/5。
  R8 3.7/4.9。
  R9 3.6/4.8。
  R10 3.5/4.7。
  Each 2-card str -0.3。
```

overpair:

```text
SRP:
  AA ATT 3.5/DEF 4.5。
  KK 3.4/4.4。
  QQ 3.3/4.3。
  JJ 3.2/4.2。
  Others 3.1/4.1。
3BP:
  AA 3.4/4.5。
  KK 3.2/4.3。
  QQ 3.0/4.1。
  JJ 2.8/3.8。
  TT 2.6/3.7。
  Others 2.5/3.5。
4BP:
  AA 3.4/4.5。
  KK 3.1/4.2。
  QQ 2.7/3.7。
  JJ 2.4/3.4。
  Others 2.1/3.1。
Board modifiers:
  SAFE BOARD: triple barrel for value。
  PAIRED: ATT/DEF -0.5。Turn pairing → CHECK that street。
  ONE-CARD FLUSH(4+suit, no flush): ATT 0/DEF capped 0.6。
  OCS OPEN-ENDED: drop 2.5 levels → WEAK SHOWDOWN。
  OCS GUTSHOT: drop 1.5 levels → THIN VALUE。
  FLUSH POSSIBLE(3+suit): drop 1.1(flop)/0.9(turn)/0.7(river)。
  STRAIGHT POSSIBLE multi: drop 0.6(flop)/0.5(turn)/0.4(river)。
  STRAIGHT POSSIBLE 1 combo: drop 0.4(flop)/0.3(turn)/0.2(river)。
  NOTE: FLUSH POSSIBLEとONE-CARD FLUSHは重複しない（重い方適用）。
        STRAIGHT系も同様。他のmodifiersはスタック。
```

top_pair:

```text
SRP:
  TPTK ATT 3/DEF 4。
  TPSK 2.8/3.8。
  3rd kicker 2.6/3.6。
  4th 2.4/3.4。
  5th 2.2/3.2。
  Other 2.1/3.1。
3BP:
  TPTK 2.9/3.9。
  TPSK 2.6/3.6。
  3rd 2.2/3.2。
  Other 1.9/2.9。
4BP+:
  TPTK 2.6/3.6。
  TPSK 2.2/3.2。
  3rd 1.8/2.8。
  Other 1.6/2.6。
Board modifiers:
  overpairと同一。
  PAIRED -0.5。
  OCF drop 3.5。
  OCS OE drop 2.5。
  OCS GS drop 1.5。
  FLUSH POSSIBLE/STRAIGHT POSSIBLEのスタック式penalty。
```

second_pair:

```text
SRP:
  PP/top kicker ATT 1.8/DEF 2.8。
  2nd 1.7/2.7。
  3rd 1.6/2.6。
  Other 1.5/2.5。
3BP:
  PP 1.3/2.5。
  Top kicker 1.5/2.3。
  2nd 1.3/2.1。
  Other 1.2/2.0。
4BP+:
  PP 0/2。
  Top kicker 1/1.8。
  Other 0.9/1.6。
Board modifiers:
  PAIRED -0.4。
  OCF drop 3.5。
  OCS OE drop 2.5。
  FLUSH POSSIBLE/STRAIGHT POSSIBLEスタック。
```

third_pair:

```text
SRP:
  Top kicker/PP ATT 1.2/DEF 2.2。
  Other 1.0/2.0。
3BP:
  DEF 1.5。
4BP+:
  DEF 1.2。
3BP+ board-hit:
  ATT 1.5（stab <=30% pot）。
PP:
  ATT 0/DEF 0.6(3BP)/0.3(4BP+)。
Board modifiers:
  PAIRED -0.3。
  OCF drop 3.5。
  OCS OE drop 2.5。
  FLUSH POSSIBLE/STRAIGHT POSSIBLEスタック。
```

fourth_fifth_pair:

```text
SRP:
  4th pair ATT 0.8/DEF 1.8。
  5th pair 0.5/1.5。
3BP:
  DEF 1.0。
4BP+:
  DEF 0.7。
3BP+ board-hit:
  ATT 1.5。
PP:
  ATT 0/DEF 0.3(3BP)/0(4BP+)。
Board modifiers:
  PAIRED -0.3。
  OCF drop 3.5。
  OCS OE drop 2.5。
  FLUSH POSSIBLE/STRAIGHT POSSIBLEスタック。
```

nuts_high (A-high):

```text
ATT 0。
DEF:
  Limp 0.8〜1.2。
  SRP 0.6〜1.0。
  3BP 0.4〜0.7。
  4BP+ 0.1〜0.4。
  kicker position依存、高い方がDEF大。
PAIRED BOARD: DEF ×1.35。
OCF/OCS: DEF → 0。
FLUSH POSSIBLE: penalty -1.0(flop)/-0.7(turn)/-0.4(river)。
STRAIGHT POSSIBLE multi: -0.5(flop)/-0.4(turn)/-0.2(river)。
STRAIGHT POSSIBLE 1 combo: -0.3(flop)/-0.2(turn)/-0.1(river)。
```

second_high (K/Q-high):

```text
ATT 0。
DEF:
  Limp 0.4〜0.7。
  SRP 0.3〜0.5。
  3BP 0.1〜0.3。
  4BP+ 0。
Board modifiers: nuts_highと同一。
```

weak_showdown:

```text
ATT 0。
DEF:
  Limp/SRP 0.8。
  3BP 0.4。
  4BP+ 0.2。
OCF drop 3.5 → TRASH。
OCS OE drop 2.5 → TRASH。
FLUSH POSSIBLE/STRAIGHT POSSIBLEスタック。
```

trash:

```text
ATT 0〜1（flop/turn stab/c-bet small sizing）。
DEF 0。
IP STAB:
  high frequency。flop/turn 20-30% pot。river polarized bluff >60% pot。
OOP:
  range bet 20-30% pot。river bluff >60% pot。
```

#### 9.4.10 ATT/DEF Budget Table（Draw）

Draw classはDEFをpot-odds threshold方式で管理する。累積budgetではなく、
各streetで相手betサイズの%potと現在equityを比較してcall可否を判定する。

```text
strong_draw:
  ATT: 4+。
  Flop IP 500%/OOP 400%。Turn IP 190%/OOP 150%。
  All-in: equity >= 60% pot odds。
  Check-raise: CALL。
  Combo bonus: +2.0 to made-hand DEF baseline。

medium_strong_draw:
  ATT: 3+。
  Flop IP 250%/OOP 200%。Turn IP 100%/OOP 75%。
  All-in: equity >= pot odds。
  Flop check-raise: IP 150%/OOP 100%。
  Turn check-raise: IP 60%/OOP 40%。
  Combo bonus: +1.2。

medium_draw:
  ATT: 1.5-3。
  Flop IP 150%/OOP 120%。Turn IP 60%/OOP 40%。
  Flop check-raise: IP 100%/OOP 75%。
  Turn check-raise: IP 40%/OOP 28%。
  Combo bonus: +0.8。

medium_weak_draw:
  ATT: 1-2。
  Flop IP 94%/OOP 78%。Turn IP 40%/OOP 26%。
  Flop check-raise: IP 60%/OOP 42%。
  Turn check-raise: IP 20%/OOP 14%。
  Combo bonus: +0.7。

weak_draw:
  ATT: 0.5-1。
  Flop IP 68%/OOP 56%。Turn IP 24%/OOP 16%。
  Flop check-raise: IP 40%/OOP 28%。
  Turn check-raise: fold。
  Combo bonus: +0.4。

strong_overcard_draw:
  ATT: 1。
  Flop IP 80%/OOP 65%。Turn IP 35%/OOP 25%。
  Flop check-raise: IP 55%/OOP 35%。Turn: fold。
  Combo bonus: +0.3。
  3BP downgrade: AK, AQ/KQ/AJ+BD flush, AJ/KJ/AT+BD flush+BD straightは維持。
                 その他overcardsは1 tier downgrade。
  4BP+ downgrade: AK+BD flushのみ維持。その他は1 tier downgradeまたはtrash。

medium_overcard_draw:
  ATT: 0.5-1。
  Flop IP 58%/OOP 45%。Turn IP 23%/OOP 15%。
  Flop check-raise: IP 28%/OOP 18%。Turn: fold。
  Combo bonus: +0.2。
  3BP downgrade: weak_overcard_draw。
  4BP+ downgrade: weak_overcard_drawまたはtrash。

weak_overcard_draw:
  ATT: 0.5。
  Flop IP 35%/OOP 25%。Turn IP 15%/OOP 9%。
  Check-raise: fold。
  Combo bonus: +0.1。
  3BP/4BP+ downgrade: trash。
```

Texture downgrade:

```text
- flushy boardでflush drawを持たないovercardは1 tier downgrade
- straighty boardでstraight/backdoorを持たないovercardは1 tier downgrade
- riverでは未完成drawをtrashまたはhigh-card showdownに再分類する
```

#### 9.4.11 Special Board Override Table

Special boardでは通常hand classよりoverrideを優先する。

```text
trips_board:
  Quads/full-house are nuts。
  nut kicker ATT 0.5/DEF 1.5。
  second kicker DEF 0.8。
  lower kickers mostly trash。

double_paired:
  Higher-pair full house is nuts。
  lower full house ATT 2.5/DEF 3.5。
  flush/straight ATT 2/DEF 3。
  kicker-only tiersは3BP/4BPでdowngrade。

trips_plus_side_cards:
  Quads are nuts。
  matching side card or pocket pair forms full-house tiers。
  flush/straightはfull houseより下にdowngrade。

quads_board:
  kickerで判定。
  nuts high is nuts。
  second high ATT 1.5/DEF 2.5。
  third high ATT 0.5/DEF 1.5。

board_full_house:
  多くのhandはboard full houseを共有。
  quadsまたはboardより上のfull house interactionのみ積極プレイ。

board_flush:
  heroの最高private suited card rankで分類。
  2nd highest ATT 3/DEF 4。
  no suited private card ATT 0/DEF 1.5。
  間のrankは線形またはtableで補間。

board_straight:
  通常はchop想定。
  top-endを改善するprivate cardがある場合のみvalue。
  flush contextがある場合はflush優先で再評価。
```

#### 9.4.12 Pressure Weight Table

Weighted pressureは、その時点のpotに対するbet/raiseサイズ（%pot）を重みに変換し、
streetをまたいで累積する。実装は46-entry piecewise-linear tableを持ってよいが、
最小実装では以下の代表thresholdを使用する。

```text
Threshold (% pot): <5  <20  <32  <50  <67  <85  <100  <122  <150
Weight:            0.04 0.30 0.50 0.70 0.85 1.00 1.10  1.25  1.40

Threshold (% pot): <195 <300 <400 <500 <700 <1000 <1500 >=1500
Weight:             1.60 2.00 2.30 2.50 2.90  3.40  4.00   4.00
```

計算ルール:

```text
bet_percent = bet_or_raise_to_call / pot_before_action * 100
pressure_weight = lookup_weight(bet_percent)
att_spent = sum(hero proactive bet/raise weights in current line)
def_spent = sum(opponent bet/raise weights hero has faced and continued against)
remaining_att = adjusted_att_budget - att_spent
remaining_def = adjusted_def_budget - def_spent
```

複数相手の同一street圧力:

```text
- heroが直面している最終call額だけでなく、raise回数をpressure_eventとして保持する
- multiwayでbet + raiseが発生した場合、最後のcall額weightにraise_count_bonusを加える
- raise_count_bonus = 0.25 * max(0, raise_count_on_street - 1)
- capは+0.75
```

#### 9.4.13 MW修正子

PokerSkill HU baseをMultiwayへ拡張するため、active opponent数とpositionでATT/DEFを補正する。

注記: §9.4.13の全数値は初期推定値であり、Phase 0-2テスト（§9.4.18）で実験的に調整する。
論文（PokerSkill）はHU専用であり、MW修正子の「正解値」は存在しない。
初期値が大きすぎる/小さすぎる場合はPhase 0の5件テストで早期発見し調整する。
補正はbase budget算出後、pressure subtraction前に適用する。

```text
opponents_remaining = active_player_count - 1
extra_opponents = max(0, opponents_remaining - 1)
```

基本MW補正:

```text
made hand:
  ATT -= 0.35 * extra_opponents
  DEF -= 0.25 * extra_opponents
draw:
  ATT -= 0.25 * extra_opponents
  DEF threshold *= (1.0 - 0.08 * extra_opponents)
high-card / weak_showdown:
  ATT unchanged（原則0）
  DEF -= 0.35 * extra_opponents
trash:
  DEF remains 0
```

Position補正:

```text
IP and closing_action:
  ATT +0.15
  DEF +0.20
sandwich:
  ATT -0.30
  DEF -0.45
OOP with players behind:
  ATT -0.20
  DEF -0.30
```

Pot type補正:

```text
limp:
  second_pair以下 DEF +0.20
  value hand ATT +0.10
SRP:
  補正なし
3BP:
  top_pair以下 ATT -0.20
  top_pair以下 DEF -0.30
  two_pair+ ATT +0.20
4BP+:
  low SPR commitmentを優先
  top_pair weak kicker以下 DEF -0.40
  overpair+ ATT +0.20
```

Wet multiway補正:

```text
wet / very_wet and opponents_remaining >= 2:
  one-pair ATT -0.40
  one-pair DEF -0.40
  strong_draw ATT +0.20（semi-bluff equityがある場合のみ）
  weak_draw DEF threshold *= 0.85
monotone / four_flush:
  no-flush made-handは該当hand class tableの重いpenaltyを優先
```

下限・上限:

```text
finite ATT/DEFは0未満にしない
∞はMW補正対象外
draw thresholdは0%未満にしない
```

#### 9.4.14 Budget計算順序

Context Engineは以下の順序でbudgetを計算する。

```text
1. board texture / special boardを分類
2. made-hand classとdraw classを分類
3. made-hand base ATT/DEFを§9.4.9から取得
4. draw ATT/threshold/combo bonusを§9.4.10から取得
5. special boardなら§9.4.11でoverride
6. board modifierを適用
7. pot type / position / SPR / MW修正子を適用
8. pressure weightを累積
9. remaining_att / remaining_defを算出
10. viable action logicへ渡す
```

数値丸め:

```text
budgetは小数第2位で保持し、prompt表示は小数第1位に丸める
threshold %potは整数に丸める
validator判定は丸め前の内部値を使う
```

#### 9.4.15 Viable Action Logic

Viable actionは単純なlookupではなく、position、street、SPR、role、board texture、draw status、
remaining ATT/DEF、legal actionsを組み合わせて計算する。

共通ルール:

```text
if check is legal:
  check is always viable unless nuts river IP raise spot
if facing bet and remaining_def <= 0 and no draw threshold call:
  call is not viable
if remaining_att <= 0:
  bet/raise is not viable except trash river bluff and forced low-SPR value jam
if action not in GameState.legal_actions:
  remove from viable_actions
```

Bet/Raise候補:

```text
value bet:
  remaining_att >= 1.0 and made_hand >= top_pair
thin value:
  0.4 <= remaining_att < 1.0 and safe board and IP/closing_action
semi-bluff:
  draw ATT > 0 and fold equity exists and not sandwich
trash stab:
  checked to hero, remaining_att >= 0, flop/turn 20-30% pot
river bluff:
  no showdown value, blocker advantage or villain weakness, sizing >60% pot
```

Call候補:

```text
made-hand:
  facing bet and remaining_def > 0
draw:
  facing bet and bet_percent <= street_position_draw_threshold
all-in:
  equity >= pot odds（strong_drawのみ equity >= 60% pot odds）
high-card:
  paired board bonus適用後 remaining_def > 0 and bet size small
```

Raise候補:

```text
nuts:
  always viable when legal
strong value:
  remaining_att >= 2.0 and board not dominated by special-board danger
strong_draw:
  flop check-raise viable unless sandwich or SPR too low
OOP check-raise:
  IP raiseより1 tier強いhand/drawを要求
multiway raise:
  opponents_remaining >= 2ではvalue-heavyに制限
```

Low-SPR commitment:

```text
SPR <= 1.5:
  nuts/two_pair+/overpair strong kicker/strong_draw equity sufficient → all-in viable
  marginal made-hand → call or checkを残し、raiseを削除
SPR <= 0.7:
  top_pair+ and sufficient equity → foldを削除してcommit
```

Paired-board / special-board override:

```text
- paired/trips/double-pairedではthin value betを削除しやすくする
- board_full_house/board_straight/board_flushではchopまたはkicker-onlyを明示
- special board overrideでATT <= 0なら通常value bet候補は出さない
```

#### 9.4.16 Prompt Format

Context EngineはLLMへ以下の構造化プロンプトを渡す。通常ログにprompt全文を出してはならない。

```text
SYSTEM:
You are a poker decision assistant. Choose only from viable_actions.
Return JSON only. Do not explain unless "reason" field is requested.

SITUATION:
street: {flop|turn|river}
pot_bb: {pot}
effective_stack_bb: {effective_stack}
spr: {spr}
hero_position: {position}
active_players: {active_player_count}
hero_cards: {hero_cards}
board_cards: {board_cards}
legal_actions: {legal_actions}
action_history: {compact_action_history}

COMPUTED_CONTEXT:
pot_type: {limp|SRP|3BP|4BP+}
initiative: {initiative}
board_texture: {dry|slightly_wet|wet|very_wet}
board_flags: {board_flags}
special_board: {special_board_or_none}
made_hand_class: {made_hand_class}
draw_class: {draw_class_or_none}
kicker_class: {kicker_class_or_none}
mw_context: {ip|oop|sandwich|closing_action, players_behind}

BUDGET:
base_att: {base_att}
base_def: {base_def}
adjusted_att: {adjusted_att}
adjusted_def: {adjusted_def}
pressure_att_spent: {att_spent}
pressure_def_spent: {def_spent}
remaining_att: {remaining_att}
remaining_def: {remaining_def}
draw_threshold: {draw_threshold_or_none}
budget_verdict: {short_verdict}

SKILL_GUIDANCE:
{selected_hand_class_guidance}
{selected_board_texture_guidance}
{selected_line_guidance}
{river_bluffcatch_guidance_if_river}

VIABLE_ACTIONS:
{numbered viable action list with sizing bounds}

OUTPUT_SCHEMA:
{"action":"fold|check|call|bet|raise|all_in","amount_bb":number|null,"reason":"short"}
```

LLMに渡す`VIABLE_ACTIONS`は最大5個に制限する。候補が多い場合は次の優先度で圧縮する。

```text
1. mandatory action（nuts raise、forced call/fold）
2. value action
3. pot-control action
4. draw/semi-bluff action
5. exploitative bluff action
```

#### 9.4.17 Output Validation

LLM出力は必ずvalidatorを通す。

```text
- JSON parse失敗 → フォールバック
- actionがviable_actions外 → 最も近いviable actionへ補正
- amount_bbがlegal sizing外 → min/maxへclip
- fold/check/callのamount_bbはnullへ正規化
- all_inはeffective_stack_bb以内へclip
- reasonはHUD表示に使わず、debug logのみ（通常ログでは短縮）
```

補正優先順位:

```text
invalid bet/raise with call viable → call
invalid bet/raise with check viable → check
invalid call with fold viable → fold
invalid action and no safe fallback → 推奨なし
```

#### 9.4.18 テスト要件

Context Engine実装時は以下をpytestで検証する。

```text
- board texture分類: rainbow/two_tone/monotone/three_flush/four_flush/special board
- straight_possible / one_card_straight open-ended / gutshot分類
- 23 hand class分類
- made-hand ATT/DEF table lookup
- draw threshold lookup
- combo rule
- pressure weight lookup
- MW修正子
- viable action filtering
- promptにlegal_actions外のactionが含まれないこと
- validatorが不正JSON/不正action/不正sizingを補正すること
```

実施状況（2026-06-06時点）:

```text
- Step 1（board texture, hand class, draw）: 34テスト PASS
- Step 2a（ATT/DEF budget, pressure, MW修正子）: 50テスト PASS
- Step 3（prompt builder, output validator）: 78テスト PASS
- Step 4（GameLoop統合）: 82テスト PASS
- LLM比較テスト: 15ケース完了、GPT-5.4-mini採用確定
```

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

本セクションの内容は、PokerRL+GRPO統合完了後（Stage D）に廃止する。
PokerRL+GRPO統合が完了するまでは、既存のRust postflop CLI連携を維持する。
PokerRL+GRPO統合完了後は、本セクションをDESIGN_NOTES.mdへ移動する。

PokerRL+GRPO統合後の判断経路は Section 9.3 を参照。

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


## 10A. PokerRL+GRPO推論ブリッジ

### 10A.1 概要

PokerRL+GRPO推論ブリッジは、GameStateをテキストプロンプトに変換し、
小型LLM（Phi-4-mini 3.8B or Qwen3-4B）+ 補助ヘッドで推論し、
Recommendation形式に変換する中間層である。

ファイル: strategy/pokerrl_bridge.py

位置づけ: deep_cfr_bridge.pyと同等のDecision Engine層コンポーネント。

### 10A.2 モデル

ベース: Phi-4-mini 3.8B（第1選択） / Qwen3-4B（第2選択）
訓練: SFT on PokerBench 560k + Pluribus 60k → GRPO self-play
補助ヘッド: Action Head（4-class: Fold/Check-Call/Raise/All-in）+
Sizing Head（sigmoid 0.1x-3.0x pot）
量子化: AWQ 4-bit or GGUF Q4_K_M
推論速度: T1 50-300ms（RTX 3080）

注記: PokerRL+GRPO推論はHU postflopの主推論エンジンとして使用する。
Multiway postflopにはPokerSkill式Context Engine + LLMを使用し、
PokerRL+GRPOモデルはMultiway主推論には使用しない。
PokerRL+GRPOのMultiway対応は将来検討事項として保留する。

### 10A.3 モデルロード

アプリ起動時にモデルをGPUにロードする（1回のみ）。
vLLM or llama.cppの常駐推論プロセスを使用する。
KV cache warm-startを行い、システムプロンプトをprefix cacheする。

ロード失敗時はWARNINGログを出し、fallback経路（既存Deep CFR → LLM）へ進む。

### 10A.4 入力変換

GameStateからテキストプロンプトを構築する。

変換関数: pokerrl_prompt_builder.py

推論時は圧縮フォーマット（約100-200 tokens）を使用する。
Context Engineラベルとして、board_texture, hand_class, spr_bucket を事前計算する。
autoregressive生成は行わない。
最終hidden stateを補助ヘッドに渡す。

### 10A.5 出力変換

Action Head:

```text
softmax → fold_prob, call_prob, raise_prob, allin_prob
```

Sizing Head:

```text
sigmoid → 0.1x-3.0x pot ratio
```

推奨アクションはargmax(probabilities)とする。

raise_amount:

```text
raise_amount = facing_bet + call_amount + int(pot * raise_size_ratio)
```

### 10A.6 Recommendation生成

既存Deep CFRと同じRecommendation形式を使用する。

```text
source: "pokerrl" / "pokerrl_exploit"（LLM exploit適用後）
confidence:
  top_prob >= 0.70 → high
  top_prob >= 0.45 → medium
  top_prob < 0.45 → low
```

action_probabilitiesを含め、HUD表示に使用する。

### 10A.7 exploit補正

既存LLM exploit_adjustment（GPT-5.4-mini）をそのまま適用する。

適用条件:

```text
opponent_stats.total_hands >= sample_threshold_low (50)
```

### 10A.8 エラーハンドリング

```text
モデルロード失敗: WARNING、fallback経路へ
推論タイムアウト: WARNING、Noneを返す
入力変換失敗: WARNING、Noneを返す
```

暫定推奨は出さない。

### 10A.9 レイテンシTier

```text
T0 (Cache): Preflop標準、5-50ms
T1 (Quick): Postflop通常、50-300ms
T2 (Tool): Postflop難、1-5秒
T3 (Search): 極難、5-12秒
```

全TierはAsyncで実行し、GameLoopは止めない。

注記: 上記Tier設計はHU postflop向けである。
Multiway postflopのレイテンシはContext Engine（<10ms）+ LLM API呼び出し（1-3秒）
またはローカルPhi-4-mini推論（50-300ms）となり、別途管理する。

### 10A.10 訓練済みモデル管理

```text
models/pokerrl/
├── final_quantized/    ← 本番推論用
└── training_log.md     ← 訓練経過記録
```

### 10A.11 品質評価基準

- Spot Checks 50シナリオで95%合格
- Entropy健全（top-1確率中央値 ≤ 0.85）
- PokerBench Postflop accuracy ≥ 60%
- Slumbot HU ≥ -15 bb/100
- 「profit vs random」は単独評価指標として使用禁止

---

## 10B. Legacy Deep CFR (Deprecated)

本セクションの内容は非推奨（Deprecated）である。
PokerRL+GRPO統合完了後（Stage D）に削除する。
新主推論エンジンはSection 10Aを参照。

### 10B.1 概要

Deep CFR推論ブリッジは、GameStateをDeep CFRモデルの入力形式に変換し、
推論結果をRecommendation形式に変換する中間層である。

ファイル: deep_cfr_bridge.py

位置づけ: solver_bridge.py と同等のDecision Engine層コンポーネント。

### 10B.2 モデル

モデル: Deep CFR 6-player NLHE
リポジトリ: https://github.com/dberweger2017/deepcfr-texas-no-limit-holdem-6-players
ライセンス: MIT
形式: PyTorch .pt チェックポイント
アーキテクチャ: 3層フィードフォワード（入力156次元、隠れ層256ユニット×3、出力3アクション＋1サイジング）
推論速度: 0.5〜1ミリ秒（RTX 3080）

注記: SPEC初版では「5層FF、入力500次元」と記載していたが、
実際のdberweger2017リポジトリのmodel.pyは3層×256ユニット、入力156次元である。
poker-system側の _deep_cfr_network.py もこの実アーキテクチャに合わせて実装済み。



### 10B.3 モデルロード

アプリ起動時にモデルをGPUにロードする（1回のみ）。
ロード失敗時はWARNINGログを出し、fallback経路へ進む。
fallback経路は既存のRust postflop CLI（廃止までの暫定）。

config.yaml:

deep_cfr:
  model_path: models/deep_cfr/best_checkpoint.pt
  device: cuda
  fallback_to_solver: true

### 10B.4 入力変換

GameStateから以下を取得し、156次元入力ベクトルに変換する。

変換関数: encode_game_state(game_state: GameState) → numpy.ndarray (156,)

正規化分母: initial_stake = hero.stack（残りチップ）。0以下の場合は1.0にフォールバック。
訓練側（src/core/model.py encode_state()）と同一の定義。

156次元の内訳:
```text
[0:52]    hero hand one-hot:           52次元（カード2枚をone-hot）
[52:104]  board one-hot:               52次元（カード最大5枚をone-hot）
[104:109] stage one-hot:                5次元（preflop/flop/turn/river/showdown）
[109]     pot / initial_stake:          1次元
[110:116] button position one-hot:      6次元（dealer_seat→0始まりインデックス変換）
[116:122] current player one-hot:       6次元（Hero=index 0固定、推論はHeroターンのみ）
[122:146] per-player state:            24次元（6人 × 4値）
            active:     1.0 if in_current_hand else 0.0
            bet:        current_street_bet / initial_stake
            pot_chips:  (hand_start_stack - current_stack - current_bet) / initial_stake
            stack:      current_stack / initial_stake
[146]     min_bet / initial_stake:      1次元（テーブル上の最大ベット額）
[147:151] legal actions:                4次元（Fold=0, Check=1, Call=2, Raise=3）
            Fold: 常に1
            Check: max_bet <= hero_betのとき1
            Call: max_bet > hero_betのとき1
            Raise: hero_stack > call_amountのとき1
[151:156] previous action:             5次元（4 action type one-hot + 1 amount）
            current_street_actionsの最後のアクション
            ストリート開始直後はpreflop_actionsからフォールバック
            両方空なら全て0
合計: 156次元
```

カード表記変換:
```text
スート: Clubs=0, Diamonds=1, Hearts=2, Spades=3
ランク: 2=0, 3=1, ..., A=12
インデックス = suit * 13 + rank
```

pot_chips計算:
```text
pot_chips = max(0, hand_start_stack - current_stack - current_street_bet)
hand_start_stackはHandManagerがハンド開始時に記録。
観察窓（1.5秒）の間にOCR補完。
hand_start_stackが不明なプレイヤーはpot_chips=0。
```


### 10B.5 出力変換

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

### 10B.6 Recommendation生成

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

### 10B.7 exploit補正後

exploit_adjustment適用後:

metadata["exploit_adjusted"] = True
metadata["exploit_source"] = "llm"
metadata["original_action"] = original_top_action
metadata["adjusted_action"] = adjusted_action

### 10B.8 エラーハンドリング

モデルロード失敗: WARNINGログ、fallback_to_solver=trueなら既存Solver経路へ
推論例外: WARNINGログ、そのフレームの推奨をスキップ
入力変換失敗: WARNINGログ、推奨をスキップ

暫定推奨は出さない。
エラー時にfallback推奨を出さない。
既存の安全原則を維持する。

### 10B.9 訓練済みモデル管理

訓練済みモデルは以下に配置する。

models/deep_cfr/
├── best_checkpoint.pt      ← 本番推論用
├── phase1_seedA/           ← 訓練Phase 1
├── phase1_seedB/
├── phase1_seedC/
├── phase2/                 ← 訓練Phase 2
├── phase3/                 ← 訓練Phase 3
└── training_log.md         ← 訓練経過記録

現在配置済みモデル:
  best_checkpoint.pt = Phase 3 v4 mixed_checkpoint_iter_10000.pt
  訓練: Phase 1 v4 → Phase 2 v4 → Phase 3 v4 (memory_size=20M, iterations=10000, traversals=400)
  独立再評価 (3000 games): profit vs random = 46.07
  配置日: 2026-05-29

本番推論用モデルの切り替えはconfig.yamlで行う。
訓練中の中間checkpointは本番推論に使わない。

注意事項:
- flagship_models/ は旧アーキテクチャ（fc1-fc6, 4アクション固定）であり、
  現行コード（base/action_head/sizing_head, 3アクション+連続サイジング）と非互換。
  ロード不可。使用禁止。
- 訓練情報源の優先順位: README (readme.md) > description.md > Medium記事。
  READMEが唯一の正規情報源。description.mdは旧実験記録として参考のみ。


### 10B.10 訓練原則

Deep CFRモデルの訓練・再訓練時は以下の原則を遵守する。

必須:
- 毎イテレーション、ネットワークをゼロから再訓練する（ファインチューニング禁止）
- メモリバッファはReservoir Samplingを使う（スライディングウィンドウ禁止）
- Linear CFR重み付けを適用する（イテレーション番号tに比例した重み）
- 全リグレットが負のとき、均等戦略ではなく最大リグレットアクションを確率1で選ぶ
- メモリバッファサイズはRAMが許す限り大きくする（最低数百万サンプル）

現在の設定:
  memory_size = 20,000,000（学習エージェントのみ）
  対戦相手・評価用エージェントはデフォルト（300,000）のまま。
  RAM 32GB環境で12GB使用、残り約11GBの余裕を確保。
  デフォルト300,000は原論文の0.75%であり不十分だったため拡大した。
  将来RAM増設時にさらに拡大を検討する。

禁止:
- Phase 2（自己対戦）を5000イテレーション以上引っ張ること
- Phase 3でメモリバッファサイズを縮小すること
- TensorBoardを監視せず訓練を放置すること
- 1つの指標（例：ランダム相手勝率）だけで品質を判断すること

訓練情報源:
- README (readme.md) が唯一の正規情報源
- Medium記事はREADMEと矛盾する箇所があり、根拠としない
- train_selfplay_v2 等の独自追加コードは使用しない

訓練手順（README準拠）:
- Phase 1: --iterations 1000 --traversals 200
- Phase 2: --self-play --iterations 2000 --traversals 400
- Phase 3: --mixed --refresh-interval 1000 --num-opponents 5 --iterations 10000 --traversals 400

Phase 3 対戦相手プール構成:
- Phase 2のcheckpointを専用フォルダ（例: models/phase3_pool_v3/）にコピーする
- --checkpoint-dir で専用フォルダを指定し、--model-prefix で対象ファイルを限定する
- Phase 3自身の保存先は --save-dir で別フォルダにする（自己混入防止）
- 旧訓練ファイル（Phase 1各シード、旧Phase 2等）をプールに混入させない

Phase 3 初期loss発散の扱い:
- Phase 3開始直後にAdvantage network lossが散発的にスパイク（10^11〜10^12）することがある
- 数百イテレーションで自然収束する（encode_stateの正規化分母が原因候補、未修正で許容）
- 異常検知基準に該当しない限り訓練を中断しない
- 異常検知基準: プロセス消失 / loss全iterで10^11以上 / profit 100iter連続負 / エラー停止

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
- dberweger2017リポジトリ readme.md（2026年3月更新版）


### 10B.11 モデル品質の評価基準

Phase 1合格基準:
- advantage lossが安定的に低下
- ランダム相手への利益 >= 10チップ/ゲーム

Phase 2判断基準:
- profit vs random が正の値なら最良checkpointでPhase 3開始
- profit vs random がPhase 1以下でもPhase 3へ進む（Phase 3で回復設計）
- 異常停止なら最後の正常checkpointでPhase 3開始

最終合格基準:
- ランダム相手への利益 >= 15チップ/ゲーム
- Phase 1 checkpointへの勝率 >= 60%
- CLIプレイで明らかな異常行動がない
  （ナッツでフォールド、ブラフキャッチャーでオーバーベット等がない）
- 異なるcheckpoint間の成績のばらつきが小さい

Phase 3 v4 実績値（2026-05-29）:
- ランダム相手への利益: 46.07チップ/ゲーム（基準15の3倍超） ✅
- Phase 1 checkpointへの勝ち越し: +4.72 ✅
- CLIプレイ / ライブテストでの異常行動確認: 未実施（次タスク）


## 11. LLM

### 11.1 LLM利用方針

LLMはexploit補正に使用する。
LLMをMultiway postflop判断の主軸としては使用しない。
LLM単体の出力を無検証で採用しない。

用途:

- HU exploit adjustment（PokerRL+GRPO出力に対する統計ベース補正）
- Multiway exploit adjustment（同上）

廃止した用途:

- Multiway postflop判断の主軸（PokerRL+GRPOに置き換え）

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
source（Solver / AI / Chart / Deep CFR / Deep CFR+ / PokerRL / PokerRL+）
reason
processing status
action_probabilities（Deep CFR / Deep CFR+ / PokerRL / PokerRL+ ソース時のみ）
```

Deep CFR / PokerRLソース表示:

```text
strategy_source="deep_cfr" → Source: Deep CFR
strategy_source="deep_cfr_exploit" → Source: Deep CFR+（cyan色）
strategy_source="pokerrl" → Source: PokerRL
strategy_source="pokerrl_exploit" → Source: PokerRL+（cyan色）
```

Deep CFR / PokerRL確率分布表示:

```text
PokerRL:
  RAISE 72%
  CALL 25%
  FOLD 3%
```

確率分布はDeep CFR / Deep CFR+ / PokerRL / PokerRL+ソース時のみ表示する。
Solver / Chart / AI ソース時は従来通り非表示。

---

### 12.8 HUD処理中表示

処理中は未確定推奨を出さず、statusのみ表示する。

表示例:

```text
CHART CHECKING...
SOLVER THINKING...
LLM ANALYZING...
POKERRL THINKING...
POKERRL DEEP THINKING...
POKERRL FALLBACK...
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
  mw_model: openai/gpt-5.4-mini    # MW Context Engine用LLMモデル
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

### 14.13 pokerrl

PokerRL+GRPO推論設定。

```yaml
pokerrl:
  model_path: models/pokerrl/final_quantized/
  device: cuda
  inference_engine: llama_cpp  # or vllm
  inference_timeout_ms: 5000
  prefix_cache_enabled: true
  fallback_to_deep_cfr: true
```

| パラメータ | 用途 |
|---|---|
| pokerrl.model_path | 本番推論用量子化モデルのディレクトリ |
| pokerrl.device | 推論デバイス（cuda / cpu） |
| pokerrl.inference_engine | 常駐推論エンジン（llama_cpp / vllm） |
| pokerrl.inference_timeout_ms | 推論タイムアウト |
| pokerrl.prefix_cache_enabled | システムプロンプトのprefix cacheを有効化するか |
| pokerrl.fallback_to_deep_cfr | PokerRL利用不可時にLegacy Deep CFRへfallbackするか |
| pokerrl.context_engine_enabled | MW用Context Engineを有効化するか |
| pokerrl.mw_llm_provider | MW判断用LLM（openrouter / local） |
| pokerrl.mw_att_modifier_per_player | MW ATT修正子/追加プレイヤー（実験値、デフォルト-1.0） |
| pokerrl.mw_def_modifier_per_player | MW DEF修正子/追加プレイヤー（実験値、デフォルト-0.5） |

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
Deep CFR routing:
deep_cfr_bridge initialized
Deep CFR recommendation generated
Deep CFR recommendation failed, falling back
Deep CFR fallback: phase={phase} active={count} route={route}
Deep CFR fallback skipped: no available fallback
PokerRL model loaded
PokerRL recommendation generated
PokerRL recommendation failed, falling back
PokerRL encode summary
POKERRL THINKING...
POKERRL DEEP THINKING...
POKERRL FALLBACK...
exploit_adjustment applied: original={action} adjusted={action}
exploit_adjustment skipped: reason={reason}
exploit_adjustment failed: {error}
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
1441 passed, 0 failed

pytest tests/test_context_engine.py -q
82 passed
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
- PokerRL訓練済みモデルの品質検証前にDeep CFR/Rust Solverを削除しない
- 「profit vs random」のみでモデル品質を判断しない
- Spot Checks 50シナリオを削除・緩和しない
- verify_pokerrl_encode.pyの検証をスキップしない
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
