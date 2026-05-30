
# poker-assistant snapshot
**Updated:** 2026-05-30 JST (Session End — Final)
**Session:** Deep CFRライブテスト → 品質不合格確定 → PokerRL+GRPO採用決定 → ドキュメント整備完了

---

## 0. このsnapshotの位置づけ

このsnapshotは、次セッションでポーカーAIアシスタント開発を再開するための現在地点メモである。
体系的な仕様は SPEC.md、設計判断の理由は DESIGN_NOTES.md を参照。
PokerRL+GRPOの訓練・統合計画は `docs/PokerRL+GRPO 6-max NLHE.md`（実装指令書v1.1）を参照。

リポジトリ: https://github.com/sanhyokim/poker-assistant

**重要: sanhyokim2050 ではない。毎回この正しいURLを使うこと。**

**推論エンジンの大幅な方針転換が完了した。Deep CFR → PokerRL+GRPO（小型LLM + 補助ヘッド）への移行が決定済み。SPEC.md / DESIGN_NOTES.mdへの反映も完了。次のアクションはSprint 1（環境構築・モデル選定）の開始。**

---

## 1. 現在地点

### 1.1 最新テスト結果

```text
pytest -q
1441 passed, 0 failed, 7 warnings
```

### 1.2 GitHub push状況

全変更push済み。最新commit:
```text
6306c86 docs(strategy): align PokerRL fallback routing
```

### 1.3 システム全体の状態

poker-assistantは、CoinPoker 6max NLHEの画面認識 → 推奨表示システムとして稼働中。
画面認識・GameState管理・Preflop Chart・HUD表示・DB/replay保存は安定動作している。

Postflop推論エンジンの状態:

| エンジン | 状態 | 詳細 |
|---|---|---|
| Rust postflop CLI（Solver） | **永久廃止確定** | deep-SPR flopで22秒タイムアウト。ユーザーが「タイムオーバー的に使えないものに認定」と明言。フォールバック経路からも除外済み。コードはStage D完了まで残すが代替手段とは見なさない。 |
| Deep CFR（dberweger2017） | **品質不合格確定** | 全局面でRaise 70-80%。GTO近似を学んでいない。Stage D完了までフォールバックとして残す。 |
| PokerRL+GRPO | **新推論エンジンとして採用決定** | 未実装。Sprint 1開始待ち。 |

### 1.4 ドキュメント状態

| ファイル | 状態 | 最新commit |
|---|---|---|
| docs/SPEC.md | 更新済み・push済み | 6306c86 |
| docs/DESIGN_NOTES.md | 更新済み・push済み | 6306c86 |
| docs/snapshot.md | **要更新**（本ファイルで置き換え） |
| docs/PokerRL+GRPO 6-max NLHE.md | 未追跡（実装指令書v1.1、ローカルに存在） |

---

## 2. 本セッションで完了したこと

### 2.1 Deep CFRライブテスト（2026-05-30）

4ハンド・9局面を観察。結果:

| Hand | Phase | Hero | Board | 状況 | F/C/R | 推奨 | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | Flop | JsQs | 3hTh8h | 4way, ノードロー | 0.5/24/76 | BET 751 (151%pot) | ❌ |
| 1 | Turn | JsQs | 3hTh8hTd | 3way | 0.7/20/79 | BET 2568 (151%pot) | ❌ |
| 1 | River | JsQs | 3hTh8hTd4s | 3way | 0.5/22/77 | BET 2566 (151%pot) | ❌ |
| 2 | Flop | 9sKh | 9h3h3c | 5way | 0.3/25/74 | BET 903 (152%pot) | ❌ |
| 2 | Turn | 9sKh | 9h3h3c2h | HU | 1.2/12/86 | BET 3633 (152%pot) | ❌ |
| 3 | Flop | 3dAh | Jh6s9d | 3way facing BET | 0.7/23/76 | RAISE 12878 (5.6X) | ❌❌ 最悪 |
| 3 | River | 3dAh | Jh6s9d2h3c | HU facing ALL_IN | 6/94/0 | CALL 10760 | △ 唯一まとも |
| 4 | Flop | KsQh | Qs6s5c | 4way facing BET+CALL | 0.3/28/72 | RAISE 3276 (7.1X) | △ 過剰 |
| 4 | Turn | KsQh | Qs6s5cTc | HU, TPTK, SPR 0.5 | 0.4/18/81 | ALL_IN 3948 | ✅ 合理的 |

パターン:
- ほぼ全局面でRaise 70-80%（状況に無関係）
- Fold確率が常に0.3-1.2%で極端に低い
- ハンド強度・ボード・ポジション・相手アクションへの感度が極めて低い
- 9局面中、明確に合理的なのは1局面のみ。モデル品質不合格。

### 2.2 スートマッピング不一致の発見と修正（C6）

- 訓練側: Clubs=0, Diamonds=1, Hearts=2, Spades=3
- poker-assistant旧版: Spades=0, Hearts=1, Diamonds=2, Clubs=3
- C6で`strategy/deep_cfr_bridge.py`の`_SUIT_MAP`を修正、push済み
- `tests/test_deep_cfr_bridge.py`のcard_to_index期待値も更新済み
- verify_encode.pyで "Encodings are IDENTICAL" 確認

```text
poker-assistant encoding:
  Logits: [-3.359221    0.07692789  1.4872566 ]
  Probs:  F=0.0063  C=0.1950  R=0.7988
  Sizing: raw=1.5183  ratio=1.5183

training encoding:
  Logits: [-3.359221    0.07692789  1.4872566 ]
  Probs:  F=0.0063  C=0.1950  R=0.7988
  Sizing: raw=1.5183  ratio=1.5183
```

**正しいエンコーディングでも同じ偏った出力** → モデル自体の問題確定。

### 2.3 Deep Research調査

35ソース調査。5要件（6人NLHE × ≤5秒推論 × 実戦品質 × ローカル実行 × プログラム連携）全て満たす既製エンジンは存在しない。

| 候補 | 要点 | 即時利用可否 |
|---|---|---|
| **dcaustin33/poker_rl + PokerBench** | 小型LLM+補助ヘッド、SFT→GRPO、6-max NLHE、560kデータ公開 | △ 要自前訓練 |
| **GTO Wizard AI公開API** | 2026年内に研究者向けAPI予定 | ❌ 待ち |
| **NeurIPS 2024 MCCFVFP** | 6人NLHE論文。25BBスタック限定 | ❌ コード未公開 |

除外候補: EricSteinberger/Deep-CFR, HDCFR, TexasSolver, NoRegret, cfrx, ReBel, PokerRL(旧), Shark 2.0, PioSolver, GTO Wizard AI, Deepsolver

### 2.4 PokerRL+GRPO採用決定

**アプローチ:** 小型LLMの最終隠れ層にポーカー専用の補助ヘッド（アクション分類 + ベットサイズ予測）を付けて、SFT + GRPO強化学習で訓練する。

```text
Deep CFR（不合格）:
  入力: 156次元数値ベクトル
  モデル: 3層×256ユニットFF（パラメータ数: ~300K）
  出力: 3アクション確率 + 連続サイジング
  訓練: CFR traversal + self-play

PokerRL+GRPO（新方針）:
  入力: テキストプロンプト（ゲーム状態の自然言語記述）
  モデル: 小型LLM 3.8B-4B + 補助ヘッド（パラメータ数: 3.8B-4B）
  出力: 4アクション分類（Fold/Check-Call/Raise/All-in）+ サイズ予測
  訓練: SFT on PokerBench/Pluribus → GRPO self-play
```

データ:
- PokerBench: 560k行（preflop 60k + postflop 500k）。HuggingFace公開。
- Pluribus: 10,000ハンド × 6人 = 60kトラジェクトリ。GitHub公開。
- 既知制約: PokerBench postflopは大部分がHU。Pluribusで6人multiway補完。

モデル候補:
- **Phi-4-mini 3.8B**（第1選択）: MIT、VRAM ~2.5GB (Q4)、推論~80-100 tok/s
- **Qwen3-4B**（第2選択）: Apache 2.0、VRAM ~2.7GB (Q4)、推論~70-90 tok/s
- Gemma-3-4B、SmolLM3-3B、Qwen3-1.7Bも候補

アーキテクチャ:
- LLM最終hidden state → Action Head (4クラス: Fold/Check-Call/Raise/All-in) + Sizing Head (sigmoid 0.1x-3.0x pot)
- autoregressive生成は行わない（<50ms目標のため）
- prefix cache戦略: システムプロンプトをKV cacheに事前格納、毎回re-encodeは状態依存部分のみ

速度目標: T1 50-300ms（prefix cache有、RTX 3080）

poker_rl作者の知見:
- Qwen-0.6B-Embedding > Gemma-3-1B（作者実験結果）
- 全実験コスト < $50
- エントロピー崩壊が4Bモデルで顕著 → DAPO + OPEFO対策必須
- 補助ヘッド方式がautoregressive生成より高速・安定

### 2.5 実装指令書v1.1完成

`docs/PokerRL+GRPO 6-max NLHE.md`としてローカルに存在（Git未追跡）。
内容:
- §0: 前提と原則（既存SPEC.md原則の尊重）
- §1: システム統合アーキテクチャ（新規モジュール一覧）
- §2: レイテンシ設計（T0-T3 Tier、補助ヘッドアーキテクチャ詳細）
- §3: データ準備とプロンプト設計（訓練時/推論時プロンプト分離）
- §4: モデル選定（Phi-4-mini第1、Qwen3-4B第2）
- §5: 訓練パイプライン（SFT Phase 1 → GRPO Phase 2）
- §6: 推論ブリッジ統合（Stage A→B→C→D段階移行）
- §7: HUD表示仕様
- §8: 評価とテスト（Spot Checks 50シナリオ、Entropy、Sensitivity）
- §9: リスク管理
- **§9.2: 失敗時の段階的対処と撤退基準**（Phase 1 SFT未達→Phase 2 GRPO未達→量子化劣化→全体撤退基準、代替案Case A-D）
- §10: 実装スプリント計画（Sprint 1-6）
- §11-15: 運用ガイド

### 2.6 ドキュメント更新完了

SPEC.md更新箇所:
- Section 2.2/2.3: PokerRL+GRPO / Legacy Deep CFR表記
- Section 3.1/3.5: PokerRL表記
- Section 9.1〜9.9: 戦略ルーティング全面書き換え（PokerRL+GRPO主軸）
- Section 9.3/9.4フォールバック: 全街・全人数で「Deep CFR → LLM → スキップ（Stage D完了まで保持）」に統一
- Section 10A新設: PokerRL+GRPO推論ブリッジ（10A.1〜10A.11）
- Section 10B新設: Legacy Deep CFR Deprecated（10B.1〜10B.11）
- Section 14.13新設: pokerrl config
- Section 10（旧HU Solver）: 廃止予定注記追加

DESIGN_NOTES.md更新箇所:
- Section 35.2修正: フォールバック経路をDeep CFR → LLM → スキップに統一
- Section 47新設: Deep CFRモデル品質不合格の事後分析
- Section 48新設: PokerRL+GRPO採用判断
- Section 49新設: 補助ヘッド設計と<50ms目標

---

## 3. 既存システムの状態

### 3.1 正常に動作している部分

```text
- 画面キャプチャ・認識パイプライン（全ストリート）
- GameState構築・状態安定化ガード群
- HandManager（ハンドライフサイクル管理、DB保存）
- Preflop Chart推奨（変更なし、正常動作）
- HUD表示（Deep CFR/PokerRL対応済み）
- LLM exploit_adjustment（OpenRouter / gpt-5.4-mini、正常動作）
- DB/Replay保存
- 全テスト1441 passed
```

### 3.2 Deep CFR関連（現状維持、将来廃止予定）

```text
- strategy/deep_cfr_bridge.py: encode_game_state修正済み（C1-C6）、スートマッピング修正済み
- strategy/_deep_cfr_network.py: PokerNetwork定義（訓練リポジトリと同一構造）
- models/deep_cfr/best_checkpoint.pt: Phase 3 v4モデル（品質不合格）
- config.yaml deep_cfr.fallback_to_solver: true

これらは新推論エンジン統合完了（Stage D）まで残す。
```

### 3.3 Rust postflop CLI（廃止方針確定）

```text
- solver/solver_bridge.py: Rust postflop CLI連携
- 廃止方針確定。フォールバック経路からも除外済み。
- コードは新推論エンジン統合完了（Stage D）まで残すが使わない。
```

### 3.4 戦略ルーティング現在状態

```text
Preflop: Chart（正常動作、変更なし）
HU Postflop: Deep CFR推論（モデル品質不合格だがフォールバックとして残存）
Multiway Postflop: Deep CFR推論（同上）
All-in: 既存 equity / pot odds 数理避難路
exploit_adjustment: LLM継続（Deep CFR/新エンジン出力に対する統計ベース補正）
```

SPEC.md Section 9.3/9.4で確定したフォールバック経路:
```text
全街・全人数共通:
  PokerRL+GRPO → Deep CFR → LLM → スキップ（Stage D完了まで保持）
  Rust Solver はフォールバック経路から除外済み
```

---

## 4. Deep CFR訓練情報（参照用）

### 4.1 訓練経緯

```text
訓練リポジトリ: C:\dev\deepcfr-training
ベース: https://github.com/dberweger2017/deepcfr-texas-no-limit-holdem-6-players (MIT)
ハードウェア: RTX 3080 (VRAM 10GB, RAM 32GB)

Phase 1 v4: --iterations 1000 --traversals 200
  → profit vs random: 28.08（独立再評価3000 games）
  → ~3秒/イテレーション

Phase 2 v4: --self-play --iterations 2000 --traversals 400
  → ~44-52秒/イテレーション（ニューラルネット対戦で~15倍遅化）
  → ~24-28時間

Phase 3 v4: --mixed --iterations 10000 --traversals 400
  → memory_size=20,000,000（デフォルト300Kから67倍拡大、原論文40Mの50%）
  → ~9日間
  → 最終profit vs random: 46.07（独立再評価3000 games）
  → Phase 1 v4との対戦: +4.72

モデル配置: models/deep_cfr/best_checkpoint.pt
  = Phase 3 v4 mixed_checkpoint_iter_10000.pt (1.76MB)
  配置日: 2026-05-29

問題: profit vs random = 46.07は「ランダム相手にひたすらレイズ」戦略でも達成可能。
GTO近似として必要なハンド強度・ボード・ポジション・相手アクションへの感度が不足。
```

### 4.2 訓練中に発見された技術的問題

```text
- evaluate_against_randomでRaise無限ループ → MAX_ACTIONS_PER_GAME=300で対処
- PrioritizedMemoryの_max_priority正帰還ループ → max_priority_cap=100.0で対処
- checkpoint再開時メモリバッファ損失 → checkpoint再開を使わずフル実行で対処
- Phase 3初期のloss散発スパイク（10^11〜10^12）→ 数百iter後に自然収束
- flagship_modelsは旧アーキテクチャ（fc1-fc6）で現行コード（base/action_head/sizing_head）と非互換
- 訓練情報源優先順位: README > description.md > Medium記事
```

### 4.3 「profit vs random」の罠（教訓）

ランダム相手への最適戦略は「常にレイズ」に近くなり得る。
この指標が高くても、GTO近似や実戦品質が高いとは限らない。
**今後は「profit vs random」を単独評価指標として使用禁止。**

代わりに以下を組み合わせて評価する:
- Spot Checks（特定局面での行動分布確認）
- Entropy（top-1確率の偏り）
- Sensitivity Tests（入力変化への反応性）
- 対GTOデータセットaccuracy
- Slumbot等の外部ベンチマーク

---

## 5. 156次元エンコーディング対応表（Deep CFR用、参照用）

Phase C修正（C1-C5）+ C6スートマッピング修正で訓練側と完全一致確認済み。
新エンジンでは不要になるが、verify_pokerrl_encode.py設計時の参考として保持。

```text
[0:52]    hero hand one-hot (52次元)
[52:104]  board one-hot (52次元)
[104:109] stage one-hot (5次元: preflop/flop/turn/river/showdown)
[109]     pot / initial_stake (1次元)
[110:116] button position one-hot (6次元)
[116:122] current player one-hot (6次元, Hero=index 0固定)
[122:146] per-player state (24次元: 6人 × 4値)
            active, bet/initial_stake, pot_chips/initial_stake, stack/initial_stake
[146]     min_bet / initial_stake (1次元)
[147:151] legal actions (4次元: Fold/Check/Call/Raise)
[151:156] previous action (5次元: 4 action type one-hot + 1 amount)
合計: 156次元

カード表記: スート Clubs=0, Diamonds=1, Hearts=2, Spades=3
           ランク 2=0, 3=1, ..., A=12
           インデックス = suit * 13 + rank

正規化分母: initial_stake = hero.stack（残りチップのみ）
pot_chips: max(0, hand_start_stack - current_stack - current_street_bet)
```

---

## 6. seat番号とテーブル配置（不変）

```text
座標プロファイル: profiles/coinpoker_6max.json

seat 1 = Hero = 下中央
seat 2 = 右下
seat 3 = 右上
seat 4 = 上中央
seat 5 = 左上
seat 6 = 左下
```

---

## 7. 主要コードファイル

```text
core/game_state.py          GameState/PlayerState/HeroState/ActionRecord定義
core/hand_manager.py        ハンドライフサイクル管理、DB保存
core/game_loop.py           メインループ、戦略ルーティング
strategy/deep_cfr_bridge.py Deep CFR推論ブリッジ（C1-C6修正済み、モデル品質不合格）
strategy/_deep_cfr_network.py PokerNetwork定義
strategy/recommendation_engine.py 戦略ルーティング（preflop chart / Deep CFR / LLM / fallback）
strategy/llm_pipeline.py    OpenRouter API呼び出し（exploit_adjustment用）
solver/solver_bridge.py     Rust postflop CLI連携（廃止予定）
gui/main_window.py          PyQt6メインウィンドウ
gui/hud_overlay.py          HUDオーバーレイ
profiles/coinpoker_6max.json 座標プロファイル
config.yaml                 設定ファイル
docs/SPEC.md                正仕様（v3.5、PokerRL+GRPO反映済み）
docs/DESIGN_NOTES.md        設計判断理由（Section 49まで）
docs/snapshot.md            本ファイル
docs/PokerRL+GRPO 6-max NLHE.md  実装指令書v1.1（Git未追跡）
```

---

## 8. ファイル変更履歴（本セッション）

### poker-assistant リポジトリ

| ファイル | 変更 | commit |
|---|---|---|
| strategy/deep_cfr_bridge.py | C6: _SUIT_MAP修正 | push済み（セッション前半） |
| tests/test_deep_cfr_bridge.py | card_to_index期待値更新 | push済み（セッション前半） |
| docs/SPEC.md | Section 9.3/9.4/10A/10B/14.13追加・更新 | push済み |
| docs/SPEC.md | Section 9.3/9.4 フォールバック経路修正 | 6306c86 |
| docs/DESIGN_NOTES.md | Section 47/48/49 追加 | push済み |
| docs/DESIGN_NOTES.md | Section 35.2 フォールバック経路修正 | 6306c86 |

### 訓練リポジトリ (C:\dev\deepcfr-training)

| ファイル | 変更 |
|---|---|
| verify_encode.py | 新規作成。訓練側/推論側エンコーディング一致検証 |

### 未コミット/未追跡

| ファイル | 状態 |
|---|---|
| docs/snapshot.md | 要更新（本ファイルで置き換え） |
| docs/PokerRL+GRPO 6-max NLHE.md | 実装指令書v1.1。Git未追跡 |

---

## 9. 確定した制約（次セッション以降も有効）

### 9.1 永久廃止

- **Rust postflop CLI（Solver）は永久廃止**: 代替として検討しない。フォールバック経路からも除外済み。コードはStage D完了まで残すが使わない。

### 9.2 品質不合格（保持）

- **Deep CFRモデルは品質不合格**: 正しいエンコーディングでもRaise 70-80%。改善の見込みなし。Stage D完了までフォールバックとして残す。

### 9.3 評価基準

- **「profit vs random」は単独評価指標として使用禁止**: 46.07でも実戦不可だった教訓。
- PokerRL+GRPOの品質評価基準（SPEC 10A.11）:
  - Spot Checks 50シナリオで95%合格
  - Entropy健全（top-1確率中央値 ≤ 0.85）
  - PokerBench Postflop accuracy ≥ 60%
  - Slumbot HU ≥ -15 bb/100

### 9.4 削除禁止

- 既存Deep CFR/Solverコードは新エンジン統合完了（Stage D）まで削除禁止
- verify_pokerrl_encode.pyの検証をスキップしない（Deep CFRの教訓）
- Spot Checks 50シナリオを削除・緩和しない
- エントロピー崩壊対策なしに4B以上のモデルを訓練しない
- PokerBench/Pluribusデータの品質を確認せずに訓練を開始しない

### 9.5 ハードウェア

- RTX 3080 (VRAM 10GB, RAM 32GB)が唯一のハードウェア
- クラウドは$500上限

### 9.6 タイムボックス

- PokerRL+GRPOアプローチ: 12週間タイムボックス（補正含め最大15週間）
- 撤退基準は実装指令書v1.1 §9.2に記載
- Sprint中間点でもGo/No-go判定を実施

### 9.7 既存原則（不変）

- Quality over Speed: 速いが間違った推奨 < 遅いが正しい推奨
- No provisional recommendations: 暫定推奨は表示禁止
- GameLoop must never freeze: 認識ループは絶対に止めない
- State-only HUD when not ready: 計算中はステータスメッセージのみ
- Stale context discard: Context Snapshot不一致なら結果を破棄
- No silent fallback: 入力不安定なら推奨を出さず明示的にステータス表示

---

## 10. PokerRL+GRPO実装計画（実装指令書v1.1の要約）

### 10.1 Stage移行計画

```text
[Stage A] 実装開始〜Phase 1完了
  → Deep CFR表示のまま、PokerRLをテスト用に並行稼働

[Stage B] Phase 1完了〜Phase 2完了
  → PokerRLをshadow modeで稼働、推奨表示はまだDeep CFR

[Stage C] Phase 2完了後
  → HU/Multiway postflopをPokerRLに切替
  → Deep CFRはフォールバック保持（1ヶ月間）

[Stage D] 品質安定後
  → Deep CFR Bridge / Rust Solver削除
```

### 10.2 Sprint計画

| Sprint | 期間 | 内容 |
|---|---|---|
| Sprint 1 | Week 1-2 | 基盤構築 + モデル選定（両モデルで10k SFT比較） |
| Sprint 2 | Week 3-5 | Phase 1 SFT本訓練（560k+60k、RTX 3080で40-60h） |
| Sprint 3 | Week 6-9 | Phase 2 GRPO強化学習（自己対戦、100-150h） |
| Sprint 4 | Week 10-11 | 推論ブリッジ統合 + Shadow Mode |
| Sprint 5 | Week 12-13 | 本番切替 + モニタリング |
| Sprint 6 | Week 14 | 旧コンポーネント削除（オプション） |

### 10.3 新規追加モジュール（予定）

```text
strategy/
  ├── pokerrl_bridge.py          推論ブリッジ（deep_cfr_bridgeと同じI/F）
  ├── pokerrl_prompt_builder.py  GameState → プロンプト変換
  ├── pokerrl_inference_engine.py vLLM/llama-cpp ローダ + 推論実行
  ├── pokerrl_heads.py            Action / Sizing 補助ヘッド
  ├── pokerrl_output_parser.py    モデル出力 → Recommendation変換
  ├── pokerrl_spot_classifier.py  局面分類 (Tier振り分け)
  └── verify_pokerrl_encode.py    訓練側との一致検証

models/pokerrl/
  ├── base_model/                 ベースモデル重み
  ├── sft_adapter/                Phase 1 LoRAアダプタ
  ├── grpo_adapter/               Phase 2 LoRAアダプタ
  └── final_quantized/            量子化版（本番推論用）
```

### 10.4 撤退後の代替案（§9.2.6要約）

| 撤退時の状況 | 第1優先 | 第2優先 |
|---|---|---|
| SFT成功、GRPO失敗 | PokerSkill風ハイブリッド | GTO Wizard API待機 |
| SFT失敗 | Deep CFR改善（評価刷新） | GTO Wizard API待機 |
| タイムボックス超過、品質改善中 | GTO Wizard API待機 | SFTモデルshadow mode運用 |
| 全失敗 | 既存システム暫定運用 | 新興手法(MCCFVFP等)調査 |

---

## 11. 次セッションの開始手順

1. 本snapshot.mdの内容を確認
2. SPEC.md Section 9（戦略ルーティング）と Section 10A（PokerRL+GRPO推論ブリッジ）を参照
3. `docs/PokerRL+GRPO 6-max NLHE.md`（実装指令書v1.1）を読む
4. Sprint 1から開始:
   - PokerBenchデータ取得・前処理
   - Pluribusデータ取得・変換
   - Phi-4-mini 3.8BとQwen3-4Bを両方ダウンロード
   - 10kサンプルで小規模SFTを両モデルで実施（Go/No-go: 採用モデル確定）
   - verify_pokerrl_encode.py設計
   - 訓練側リポジトリ `C:\dev\pokerrl-training` 作成

**最重要:**
- リポジトリURL: https://github.com/sanhyokim/poker-assistant（sanhyokim2050ではない）
- Deep CFRの失敗パターン（ランダム相手最適化、profit vs randomだけの評価）を繰り返さないこと
- verify_pokerrl_encode.py（訓練側/推論側の一致検証）は最初に作ること
- 品質評価にはSpot Checks 50シナリオを必ず含めること
```
