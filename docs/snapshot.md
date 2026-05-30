
# Commander Snapshot

## Updated: 2026-05-30
## Status: Phase C完了（encode_game_state修正 C1-C5）/ 次: ライブテスト

---

## 0. このsnapshotの位置づけ

このsnapshotは、次セッションでポーカーAIアシスタント開発を再開するための現在地点メモである。
体系的な仕様は SPEC.md、設計判断の理由は DESIGN_NOTES.md を参照。

リポジトリ: https://github.com/sanhyokim/poker-assistant

---

## 1. 現在地点

### 1.1 最新テスト結果

```text
pytest -q
1441 passed, 0 failed
```

### 1.2 GitHub push状況

全変更push済み。

最新commit:
```text
ドキュメント更新: Phase C encode_game_state修正をSPEC.md/DESIGN_NOTES.mdに反映
```

### 1.3 現在の開発状態

Deep CFR推論ブリッジのシステム統合（Phase B）が全タスク完了。
Phase C（encode_game_state修正 C1-C5）が全タスク完了。
Deep CFRモデル訓練は Phase 3 v4 が完了し、モデル配置済み。

確定事項:

- Preflop: Chart（変更なし）
- HU Postflop: Deep CFR推論（encode修正済み、モデル配置済み）
- Multiway Postflop: Deep CFR推論（同上）
- exploit_adjustment: LLM継続（Deep CFR出力に対する統計ベース補正、実装完了）
- Rust postflop CLI: Deep CFRライブテスト確認後に廃止（fallback経路として残存）
- OpenRouter / gpt-5.4-mini: exploit用途で継続

Deep CFRモデル訓練:
  Phase 1 v4 完了（profit vs random = 24.41）
  Phase 2 v4 完了（profit vs random = -0.80）
  Phase 3 v4 完了（2026-05-29完了）

Phase 3 v4 最終評価（独立再評価3000 games）:
  Phase 3 v4 profit vs random = 46.07（合格基準≥15の3倍超）
  Phase 3 v4 vs Phase 1 v4 = +4.72（勝ち越し）

モデル配置:
  models/deep_cfr/best_checkpoint.pt に mixed_checkpoint_iter_10000.pt をコピー済み
  config.yaml deep_cfr.model_path: models/deep_cfr/best_checkpoint.pt
  config.yaml deep_cfr.fallback_to_solver: true（ライブテスト確認後にfalseへ切替予定）

残る合格基準:
  CLIプレイ / ライブテストで明らかな異常行動がないこと（未確認）

### 1.4 Phase C修正内容（encode_game_state → encode_state一致）

初回ライブテスト（2026-05-29）でDeep CFRが全ハンドでraise 70-80%・サイジング1.5x固定を返す異常を確認。
調査の結果、encode_game_stateが訓練側encode_stateと複数箇所で乖離していることが判明。

| Task | 修正内容 | 修正ファイル |
|---|---|---|
| C1 | initial_stake: `stack+bet` → `stack`のみ + DEBUGログ追加 | deep_cfr_bridge.py |
| C2 | pot_chips: 常に0 → `hand_start_stack - current_stack - current_bet` | game_state.py, hand_manager.py, deep_cfr_bridge.py |
| C3 | current_player/button: コメント追加 + dealer_seat=None WARNING | deep_cfr_bridge.py |
| C4 | legal_actions: Raise常時ON → stack>call_amount時のみ。previous_action: ストリート境界フォールバック追加 | deep_cfr_bridge.py |
| C5 | INFOレベル推論入力サマリログ追加 | deep_cfr_bridge.py |

サイジング1.5x固定の原因: sizing_headのsigmoid(0)=0.5 → 0.1+2.9*0.5=1.55。入力が壊れていたためデフォルト出力に張り付いていた。

### 1.5 Deep CFRフォールバック経路（Phase B Task 2.5で実装済み）

| Deep CFR失敗時 | Flop HU | Flop Multiway | Turn/River HU | Turn/River Multiway |
|---|---|---|---|---|
| 第1フォールバック | LLM | LLM | Solver | LLM |
| 第1も失敗時 | スキップ | スキップ | LLMフォールバック | スキップ |

---

## 2. 156次元エンコーディング対応表（確定版）

| 区間 | 次元数 | 内容 | 状態 |
|---|---|---|---|
| [0:52] | 52 | Hero手札 one-hot | ✅ |
| [52:104] | 52 | ボード one-hot | ✅ |
| [104:109] | 5 | ステージ one-hot | ✅ |
| [109] | 1 | pot / initial_stake | ✅ C1で修正 |
| [110:116] | 6 | ボタン位置 one-hot | ✅ C3で検証 |
| [116:122] | 6 | 現在プレイヤー one-hot | ✅ C3で検証（Hero=0固定） |
| [122:146] | 24 | 6人×4(active,bet,pot_chips,stack) | ✅ C1+C2で修正 |
| [146] | 1 | min_bet / initial_stake | ✅ C1で正規化修正 |
| [147:151] | 4 | legal_actions(Fold,Check,Call,Raise) | ✅ C4で修正 |
| [151:156] | 5 | previous_action(4 type + 1 amount) | ✅ C4で修正 |

正規化分母: initial_stake = hero.stack（残りチップのみ）。0以下なら1.0。
pot_chips計算: max(0, hand_start_stack - current_stack - current_bet)
hand_start_stackはHandManagerがハンド開始時に記録、観察窓1.5秒で補完。

---

## 3. seat番号とテーブル配置

```text
座標プロファイル: profiles/coinpoker_6max.json

seat 1 = Hero = 下中央
seat 2 = 右下
seat 3 = 右上
seat 4 = 上中央
seat 5 = 左上
seat 6 = 左下

アクション順: seat 1→2→3→4→5→6→1
BTN=seat 6の場合: SB=seat 1, BB=seat 2

Deep CFRインデックス対応:
  index 0 = Hero (seat 1)
  index 1 = seat 2
  index 2 = seat 3
  index 3 = seat 4
  index 4 = seat 5
  index 5 = seat 6

button変換: button_idx = (dealer_seat - 1) % 6
```

---

## 4. Phase B（システム統合）完了状況

| Task | 内容 | 状態 | テスト追加 |
|---|---|---|---|
| B-1 | deep_cfr_bridge.py 新規作成 | 完了 | +17 |
| B-2 | recommendation_engine.py ルーティング | 完了 | +9 |
| B-2.5 | フォールバック経路細分化 | 完了 | +11 |
| B-3 | exploit_adjustment（Deep CFR出力へのLLM補正） | 完了 | +10 |
| B-4 | HUD表示 Deep CFR対応 | 完了 | +8 |

---

## 5. Deep CFR訓練情報

### 5.1 訓練環境

```text
訓練リポジトリ: C:\dev\deepcfr-training
（git clone https://github.com/dberweger2017/deepcfr-texas-no-limit-holdem-6-players）
最終更新: 2026年3月（Issue #22修正済、Phase 2/3バグ修正含む）
仮想環境: C:\dev\deepcfr-training\.venv
GPU: NVIDIA GeForce RTX 3080 (VRAM 10GB) / RAM 32GB / Python 3.10 (Conda) / PyTorch 2.5.1+cu121

pokers ライブラリ: patched fork
  pip install git+https://github.com/dberweger2017/pokers.git@b1a48bd
```

### 5.2 独自パッチ（3件、upstream にはない。元に戻さないこと）

1. MAX_ACTIONS_PER_GAME = 300
   ファイル: src/training/train.py L21, L39, L42, L887, L920, L923
   目的: 評価関数のRaise→Raise無限ループ防止

2. PrioritizedMemory.__init__ に max_priority_cap = 100.0
   ファイル: src/core/deep_cfr.py L23
   目的: priority explosion → 勾配爆発防止

3. memory_size = 20_000_000（学習エージェントのみ）
   ファイル: src/training/train.py L281, L429, L592, L1029
   目的: メモリバッファ拡大（デフォルト300,000 → 20,000,000）

### 5.3 ネットワーク構造（確定値）

```text
入力次元: 156 (52+52+5+1+6+6+6*4+1+4+5)
隠れ層: 3層 × 256 ユニット
出力ヘッド: 2つ
  action_head: 3 アクション (Fold, Check/Call, Raise)
  sizing_head: 連続ベットサイズ 0.1 + 2.9 * sigmoid(x) → 0.1〜3.0× pot
学習率: advantage optimizer lr=1e-6, strategy optimizer lr=0.00005
```

### 5.4 訓練情報源

```text
README (readme.md, 2026年3月) が正規情報源。
description.md (2025年3月) は旧アーキテクチャ時代の実験記録。参考のみ。
Medium記事は更新されておらず、READMEと矛盾する箇所がある。READMEを優先する。
flagship model (2025年3月) は旧アーキテクチャ (fc1-fc6, 4アクション固定)。
  現行コード (base/action_head/sizing_head, 3アクション+連続サイジング) と非互換。
  ロード不可。使用しない。
```

### 5.5 独自追加コード（使用禁止）

- train_selfplay_v2 関数 (train.py L715〜)
- --self-play-v2, --random-seats, --opponent-checkpoints フラグ
- これらは README に存在せず、使用しない

### 5.6 Phase 3 v4 最終結果

```text
完了: 2026-05-29
速度: 約9-22秒/iter
Advantage memory最終: 20,000,000（満杯）
Strategy memory最終: 20,000,000（満杯）

Final eval (500 games): profit vs random = 66.55, profit vs mixed = 93.36
独立再評価 (3000 games): profit vs random = 46.07
Phase 3 v4 vs Phase 1 v4 = +4.72（勝ち越し）

モデル配置: mixed_checkpoint_iter_10000.pt → models/deep_cfr/best_checkpoint.pt
```

---

## 6. 主要コードファイル

```text
core/game_state.py          GameState/PlayerState/HeroState/ActionRecord定義
                            hand_start_stacks フィールド追加済み（C2）
core/hand_manager.py        ハンドライフサイクル管理、DB保存、参加者観察
                            hand_start_stacks記録・補完ロジック追加済み（C2）
core/game_loop.py           メインループ、戦略ルーティング、非同期推奨管理
strategy/deep_cfr_bridge.py Deep CFR推論ブリッジ（encode_game_state、infer、generate_recommendation）
                            Phase C (C1-C5) で大幅修正済み
strategy/_deep_cfr_network.py PokerNetwork定義（訓練リポジトリのmodel.pyと同一構造）
strategy/recommendation_engine.py 戦略ルーティング（preflop chart / Deep CFR / LLM / fallback）
strategy/llm_pipeline.py    OpenRouter API呼び出し（exploit_adjustment用）
solver/solver_bridge.py     Rust postflop CLI連携（廃止予定）
gui/main_window.py          PyQt6メインウィンドウ
gui/hud_overlay.py          HUDオーバーレイ（Deep CFR / Deep CFR+ ソース表示対応済み）
profiles/coinpoker_6max.json 座標プロファイル
config.yaml                 設定ファイル
```

---

## 7. HUD出力形式

Deep CFRソース時:
```text
Action: BET 2000
Confidence: high
Source: Deep CFR（または Deep CFR+：exploit調整後）
Probabilities:
  RAISE 74%
  CALL 25%
  FOLD 1%
Reason: Deep CFR: F=1% C=25% R=74% size=1.5x pot
```

確率分布はDeep CFR / Deep CFR+ ソース時のみ表示。
Solver / Chart / AI ソース時は従来通り非表示。

---

## 8. LLM設定

```text
モデル: openai/gpt-5.4-mini（OpenRouter経由）
用途: exploit_adjustment のみ（Multiway判断の主軸としては使用しない）
呼び出し条件: opponent_stats.total_hands >= sample_threshold_low (50)
provider: OpenAI固定、fallback無効
strict JSON Schema: ON（multiway_decision, exploit_adjustment, range_estimation, preflop_delta）
startup check: max_tokens=16以上、失敗時WARNING（アプリ起動継続）
```

---

## 9. 次にやること

### 9.1 即時: ライブテスト（Phase C修正後）

Phase C修正により、encode_game_stateが訓練側と一致した状態での初回ライブテスト。

確認項目:
- Deep CFRの推奨がハンド・ストリート・ボードに応じて変動するか（1.5x固定が解消されたか）
- fold/call/raise確率が状況に応じて変化するか
- INFOログ「Deep CFR encode summary」の数値に違和感がないか
- INFOログ「Deep CFR recommendation」のサイジングが固定値でないか
- hand_start_stacksが正しく記録されているか
- pot_chipsが正しく計算されているか
- legal_actionsのRaise判定がスタック不足時に正しく0になるか
- previous_actionがストリート境界で途切れないか
- 推奨アクション・サイジングが実戦的に妥当か
- 応答速度（1-3ms想定）

### 9.2 ライブテスト後: config.yaml切替

ライブテストでDeep CFRの動作が確認できたら:
  config.yaml: deep_cfr.fallback_to_solver: false

### 9.3 ライブテスト後: Rust postflop CLI廃止判断

Deep CFRが安定稼働することを確認後、Rust postflop CLIの廃止を検討する。

---

## 10. 禁止事項・維持事項

既存の全禁止事項を維持する（SPEC Section 17参照）。

追加:
- encode_game_stateを変更する場合、訓練リポジトリのencode_state()と必ず1対1で照合する
- 推論ログ（INFOレベル入力サマリ、推奨結果）を削除しない
- hand_start_stacksの記録・補完ロジックを削除しない
- pot_chipsの計算式を変更する場合は訓練側と照合する
- 訓練手順は README を基本とし、description.md は参考のみ
- train_selfplay_v2 / --self-play-v2 / --random-seats は使用しない
- MAX_ACTIONS_PER_GAME と max_priority_cap パッチは保持
- memory_size = 20_000_000 パッチは保持
- Deep CFR品質検証前にRust postflop CLIを削除しない
- LLM exploit_adjustmentを廃止しない
- flagship model (旧アーキテクチャ) を現行コードで使用しない

---

## 11. 今セッションで完了した作業

1. ライブテスト実施（Phase B完了後初回、2026-05-29）
2. Deep CFR推奨がraise 70-80%・サイジング1.5x固定の異常を発見
3. encode_game_stateと訓練側encode_stateの全156次元比較調査
4. 8箇所の乖離を特定
5. Phase C修正計画策定（C1-C5）
6. C1: initial_stake修正 + DEBUGログ追加
7. C2: pot_chips計算追加（GameStateフィールド追加、HandManager記録・補完追加）
8. C3: current_player/button検証 + WARNINGログ追加
9. C4: legal_actions Raise判定修正 + previous_actionストリート境界対応
10. C5: INFOレベル推論入力サマリログ追加
11. GitHub push（Phase C全修正）
12. SPEC.md Section 10A.4 更新
13. DESIGN_NOTES.md Section 46 追加
14. GitHub push（ドキュメント更新）
15. スナップショット出力

---

## 12. ドキュメント更新状況

| ファイル | 更新有無 | 内容 |
|---|---|---|
| SPEC.md | ✅ 更新済み | Section 10A.4 encode_game_state仕様書き換え |
| DESIGN_NOTES.md | ✅ 更新済み | Section 46 追加（encode乖離問題と修正） |
| snapshot.md | ✅ 本snapshot | Phase C完了・ライブテスト前 |

次セッションで渡すファイル:
- 通常再開: SPEC.md + snapshot.md
- 設計判断が絡む場合: SPEC.md + DESIGN_NOTES.md + snapshot.md

---

## 13. 次のセッションへの引継ぎ

次セッションの最初に行うべきこと:

1. CoinPokerでライブテストを実施する
2. ログの「Deep CFR encode summary」行と「Deep CFR recommendation」行を確認する
3. サイジングが1.5x固定から変動するようになったか確認する
4. 違和感のある数値があれば、encode_game_stateの該当次元を訓練側と再照合する
5. 問題なければconfig.yaml fallback_to_solver: falseに切り替え、数セッション安定動作確認後にRust postflop CLI廃止を判断する