
# poker-assistant snapshot
**Updated:** 2026-05-31 JST
**Session:** Sprint 1 実行中 — データ取得完了・multiway分析完了・Phi-4-mini 10k SFT実行中・Gemma 4 E2B候補浮上

---

## 0. このsnapshotの位置づけ

このsnapshotは、次セッションでポーカーAIアシスタント開発を再開するための現在地点メモである。
体系的な仕様は SPEC.md、設計判断の理由は DESIGN_NOTES.md を参照。
PokerRL+GRPOの訓練・統合計画は `docs/PokerRL+GRPO 6-max NLHE.md`（実装指令書v1.1）を参照。
**ただし指令書v1.1は現状と乖離している箇所あり（§12参照）。モデル選定確定後にv1.2として更新予定。**

リポジトリ: https://github.com/sanhyokim/poker-assistant

**重要: sanhyokim2050 ではない。毎回この正しいURLを使うこと。**

**現在地: Sprint 1（基盤構築＋モデル選定）の後半。データ取得・multiway分析・骨格作成が完了。Phi-4-mini 10k SFT実行中（action_accuracy 56.2% at step 200）。次にGemma 4 E2Bで同一テストを実施し、プライマリモデルを確定する。**

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
1edf9c3 追加: PokerRL推論ブリッジ骨格ファイル
```

### 1.3 Sprint 1 進捗

| タスク | 状態 | 備考 |
|---|---|---|
| S1-T1: 訓練リポジトリ作成 | ✅ 完了 | `C:\dev\pokerrl-training` (git init, .venv, requirements.txt) |
| S1-T2: PokerBench データ取得・品質確認 | ✅ 完了 | 8ファイル全て `data/pokerbench/` に保存 |
| S1-T2a: PokerBench postflop JSON構造確認 | ✅ 完了 | フィールドは `instruction`/`output`（`prompt`/`label`ではない） |
| S1-T3: Pluribus(PHH)データ取得 | ✅ 完了 | `data/phh-dataset/` に10,000 .phhファイル |
| S1-T3a: poker_datasets_ref クローン | ✅ 完了 | `tools/poker_datasets_ref/` |
| S1-T4: Phi-4-mini ダウンロード | ✅ 完了 | `models/phi-4-mini-instruct/` (FP16: 7,317 MiB) |
| S1-T4a: Qwen3.5-4B ダウンロード | ✅ 完了 | `models/qwen3.5-4b/` (FP16: 8,022 MiB) |
| S1-T4b: Qwen3.5-4B VRAM判定 | ❌ 脱落 | QLoRA batch=2でも13,120 MiB。RTX 3080で不可 |
| S1-T5: poker-assistant骨格ファイル作成 | ✅ 完了 | 6ファイル追加、1441 tests pass、commit 1edf9c3 |
| S1-T6: multiway postflop実数量確認 | ✅ 完了 | **§2参照（最重要調査結果）** |
| S1-T7: phh-dataset multiway抽出 | ✅ 完了 | 2,090万decision points抽出済み |
| S1-T8: Phi-4-mini 10k SFT | 🔄 実行中 | step 258/939、action_accuracy 56.2% (step 200) |
| S1-T9: Gemma 4 E2B 10k SFT | ⬜ 未開始 | Phi完了後に実施予定 |
| S1-T10: モデル比較・採用決定 | ⬜ 未開始 | T8+T9完了後 |
| verify_pokerrl_encode.py 設計 | ⬜ 未開始 | Sprint 2で実施 |

### 1.4 システム全体の状態

poker-assistantは、CoinPoker 6max NLHEの画面認識 → 推奨表示システムとして稼働中。
画面認識・GameState管理・Preflop Chart・HUD表示・DB/replay保存は安定動作している。

Postflop推論エンジンの状態:

| エンジン | 状態 | 詳細 |
|---|---|---|
| Rust postflop CLI（Solver） | **永久廃止確定** | フォールバック経路からも除外済み |
| Deep CFR（dberweger2017） | **品質不合格確定** | 全局面でRaise 70-80%。Stage D完了まで残す |
| PokerRL+GRPO | **Sprint 1実行中** | データ取得完了、モデル選定フェーズ |

### 1.5 ドキュメント状態

| ファイル | 状態 | 最新commit |
|---|---|---|
| docs/SPEC.md | 更新済み・push済み | 6306c86 |
| docs/DESIGN_NOTES.md | 更新済み・push済み | 6306c86 |
| docs/snapshot.md | **本ファイルで置き換え** | |
| docs/PokerRL+GRPO 6-max NLHE.md | Git未追跡。**v1.2更新待ち（モデル確定後）** | |

---

## 2. ★★★ MULTIWAY データ実数量確認結果（最重要）★★★

**この調査はSprint 1の最優先タスクとして実施された。全報告時にこのセクションを目立たせること。**

### 2.1 PokerBench postflop 500k

| 項目 | 件数 | 割合 |
|---|---|---|
| 総レコード | 500,000 | 100% |
| HU（2人） | 500,000 | **100%** |
| 3-way以上 | **0** | **0%** |

**PokerBench postflopデータは完全にHUのみ。multiway局面は1件もない。**

### 2.2 Pluribus (PHH) 10,000ハンド

| 項目 | 件数 |
|---|---|
| Flop到達ハンド | 5,338 |
| うちHU | 4,789 |
| うち3-way以上 | 549 (10.28%) |

| Street | 3-way | 4-way以上 |
|---|---|---|
| Flop | 513 | 36 |
| Turn | 253 | 20 |
| River | 115 | 6 |
| **合計** | **881** | **62** |

### 2.3 phh-dataset 21.6M（追加調査・抽出完了）

| 項目 | 件数 |
|---|---|
| 総ハンド | 21,606,087 |
| multiway postflop decision points | **20,915,640** |
| うち3-way | 11,407,661 |
| うち4-way | 2,992,676 |
| うち5-way以上 | 726,443 |
| hole cards付き | **726,570** (3.47%) |

| Street | 件数 |
|---|---|
| Flop | 11,692,440 |
| Turn | 5,835,491 |
| River | 3,387,709 |

### 2.4 判定と方針

- **PokerBench 500k は100% HU → これだけでSFTするとHU専用モデルになる**
- **phh-dataset から2,090万multiway decision points抽出済み（原材料は十分）**
- hole cards付き72万件から勝者行動を優先選別し、10万-20万件をmultiway訓練データとする
- **Layer 3（PokerKit合成データ生成）は不要と判定**（phh-datasetで十分）
- データ混合比率（HU:multiway）は事前固定せず、**active player数の分布管理**で制御
- 具体的なデータ設計はSprint 2で実験しながら詰める

### 2.5 抽出済みファイル

```text
C:\dev\pokerrl-training\data\multiway_raw\multiway_decisions.jsonl  (16.23 GB)
C:\dev\pokerrl-training\data\multiway_raw\multiway_hands.jsonl      (2.07 GB)
C:\dev\pokerrl-training\data\multiway_raw\extraction_summary.json
C:\dev\pokerrl-training\data\multiway_raw\multiway_extracted_report.md
C:\dev\pokerrl-training\scripts\extract_multiway.py
C:\dev\pokerrl-training\scripts\analyze_multiway_extracted.py
C:\dev\pokerrl-training\data\multiway_analysis_report.md
```

---

## 3. モデル選定状況

### 3.1 候補と現状

| 順位 | モデル | 状態 | 根拠 |
|---|---|---|---|
| 1 | **Gemma 4 E2B** | ⬜ 未テスト | poker_rl作者がGemma 3n E2Bをデフォルト使用。後継Gemma 4 E2BはLoRA 8-10GB、GRPO 9GBでRTX 3080対応。Unsloth動作確認済み |
| 2 | **Phi-4-mini 3.8B** | 🔄 10k SFT実行中 | action_accuracy 56.2% (step 200/939)。Go基準40%を大幅クリア中 |
| 3 | Qwen3.5-4B | ❌ VRAM超過 | QLoRA batch=2で13,120 MiB。クラウドGPU時の予備候補として保留 |
| 4 | Qwen3-4B-Instruct-2507 | ⬜ 未検証 | バックアップ |

### 3.2 Phi-4-mini 10k SFT 中間結果

```text
環境: RTX 3080 10GB, QLoRA 4-bit, LoRA r=32 alpha=32 all-linear dropout=0.1
データ: PokerBench postflop 10,000件 (instruction/output形式)
設定: epochs=3, batch=4, grad_accum=8 (effective 32), lr=1e-4, cosine, bf16
```

| step | train_loss | eval_loss | action_accuracy | perplexity | VRAM (MiB) |
|---|---|---|---|---|---|
| 9 | 2.2362 | — | — | — | 9,603 |
| 84 | 0.4906 | — | — | — | 9,698 |
| 200 | — | 0.537 | **56.2%** | 1.71 | 9,815 |
| 258 | 0.534 | — | — | — | 9,815 |

- Go基準: action_accuracy ≥ 40% → **合格** (56.2%)
- Go基準: eval_loss収束傾向 → **合格** (train 0.534 ≈ eval 0.537、過学習なし)
- Go基準: VRAM ≤ 10,000 MiB → **合格** (9,815 MiB)
- Go基準: NaN/OOMなし → **合格**
- ETA: 全939 steps完了まで残り数時間

### 3.3 Gemma 4 E2B 調査結果

| 項目 | 値 |
|---|---|
| 実効パラメータ | 2B（総パラメータ5.44B、PLE技術で実効2B） |
| hidden_size | 未確認（ダウンロード後に確認） |
| LoRA VRAM | 8-10 GB (Unsloth公式) |
| RL (GRPO) VRAM | 9 GB (Unsloth公式確認済み) |
| ポーカーRL実績 | 前世代Gemma 3n E2Bがdcaustin33/poker_rlのデフォルトモデル |
| Q4推論VRAM | 5.8 GB (RTX 3080で余裕) |
| 推論速度 | ~32 tok/s (Q4_K_M) |
| ライセンス | Gemma License |
| 注意点 | Unslothの`use_cache=False`バグ修正が必要。loss 13-15は正常（マルチモーダルモデルの特性） |

### 3.4 モデル選定プロセス（残り）

1. Phi-4-mini 10k SFT完了 → CP-2報告受領
2. Gemma 4 E2Bダウンロード → 同一10kデータで同一設定SFT実施
3. 比較レポート作成（accuracy / VRAM / 訓練時間 / 安定性）
4. プライマリモデル確定 → 指令書v1.2更新

### 3.5 10k SFTデータの特性（確認済み）

```text
PokerBenchフィールド: instruction (シナリオ) / output (アクション+サイジング)
マッピング: instruction → prompt, output → completion

train 10,000件 / eval 1,000件
平均prompt長: train 202.36語 / eval 202.10語
active_players: 全件2（PokerBench postflopはHU）

action_type分布 (train):
  fold   2,467 (24.67%)
  check  2,546 (25.46%)
  call   2,433 (24.33%)
  bet    1,020 (10.20%)
  raise  1,534 (15.34%)
  サイジング付き: 2,554 (25.54%)

action_accuracyの定義: completionの先頭単語の一致で判定（サイジング数値は無視）
  例: 正解"bet 18"、出力"bet 25" → 正解扱い
```

---

## 4. 本セッションで完了したこと（前回スナップショット以降）

### 4.1 データ取得・分析

- PokerBench 8ファイル全てダウンロード（`data/pokerbench/`）
- PHHデータセット 10,000ハンドクローン（`data/phh-dataset/`）
- poker_datasets_refクローン（`tools/poker_datasets_ref/`）
- multiway分析スクリプト作成・実行（`scripts/analyze_multiway.py`）
- phh-dataset multiway抽出スクリプト作成・実行（`scripts/extract_multiway.py`）
- 抽出結果分析スクリプト作成（`scripts/analyze_multiway_extracted.py`）
- multiway分析レポート作成（`data/multiway_analysis_report.md`）
- SFTデータ準備スクリプト作成（`scripts/prepare_sft_10k.py`）
- SFT比較スクリプト作成（`scripts/run_sft_comparison.py`）

### 4.2 モデルダウンロード・検証

- Phi-4-mini-instruct: hidden_size=3072, 32 layers, FP16 7,317 MiB
- Qwen3.5-4B: hidden_size=2560, 32 layers, FP16 8,022 MiB
- Qwen3.5-4B VRAM超過判定（QLoRA batch=2で13,120 MiB → 脱落）

### 4.3 poker-assistant リポジトリ変更

- 骨格ファイル6個追加: `strategy/pokerrl_bridge.py`, `pokerrl_prompt_builder.py`, `pokerrl_inference_engine.py`, `pokerrl_heads.py`, `pokerrl_output_parser.py`, `pokerrl_spot_classifier.py`
- 1441 tests pass確認
- commit 1edf9c3 push済み

### 4.4 重要な設計判断

| 判断 | 内容 | 根拠 |
|---|---|---|
| Layer 3（PokerKit合成）不要 | phh-dataset 2,090万件で十分 | 72万件のhole cards付きだけでも目標の24倍 |
| HU:multiway固定比率を廃止 | active player数分布管理に変更 | HUとmultiwayは同一ハンド内で遷移する。固定比率は不適切 |
| データ設計はSprint 2で実験 | 事前に完璧な設計を決めない | 勝者行動選別の品質、最適混合比率は実験で判断 |
| Gemma 4 E2Bを最有力候補に追加 | poker_rl作者の実績 + VRAM余裕 + GRPO 9GB確認 | Phi-4-miniより小さいがポーカー特化の実績あり |

---

## 5. 訓練リポジトリの状態

### 5.1 ディレクトリ構成

```text
C:\dev\pokerrl-training/
├── .venv/                          Python仮想環境 (torch 2.12.0+cu130)
├── .gitignore                      models/, results/ 除外
├── requirements.txt
├── data/
│   ├── pokerbench/                 PokerBench 8ファイル
│   │   ├── postflop_500k_train_set_prompt_and_label.json (560 MB)
│   │   ├── postflop_10k_test_set_prompt_and_label.json (11 MB)
│   │   ├── preflop_60k_train_set_prompt_and_label.json (59 MB)
│   │   └── ... (CSV等含む全8ファイル)
│   ├── phh-dataset/                PHH 10,000 .phhファイル
│   ├── multiway_raw/               phh-dataset抽出結果
│   │   ├── multiway_decisions.jsonl (16.23 GB)
│   │   ├── multiway_hands.jsonl    (2.07 GB)
│   │   ├── extraction_summary.json
│   │   └── multiway_extracted_report.md
│   ├── multiway_analysis_report.md
│   ├── sft_train_10k.jsonl         SFT訓練データ (10k件)
│   └── sft_eval_1k.jsonl           SFT評価データ (1k件)
├── models/
│   ├── phi-4-mini-instruct/        Phi-4-mini FP16
│   └── qwen3.5-4b/                Qwen3.5-4B FP16 (VRAM超過で使用不可)
├── scripts/
│   ├── analyze_multiway.py
│   ├── extract_multiway.py
│   ├── analyze_multiway_extracted.py
│   ├── prepare_sft_10k.py
│   ├── run_sft_comparison.py
│   └── monitor_sft.py
├── results/
│   └── sft_comparison/
│       ├── phi4/                   Phi-4-mini SFT出力 (実行中)
│       ├── data_preparation_report.txt  instruction先頭3件全文
│       └── run_status.md
└── tools/
    └── poker_datasets_ref/         dcaustin33/poker_datasets クローン
```

### 5.2 環境

```text
Python: .venv内
torch: 2.12.0+cu130 (CUDA対応確認済み)
cuda_available: True
cuda_version: 13.0
bf16_supported: True
GPU: NVIDIA GeForce RTX 3080 (10 GB)
空きディスク: 約358 GB
```

---

## 6. 既存システムの状態（前回から変更なし）

### 6.1 正常に動作している部分

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

### 6.2 Deep CFR関連（現状維持、将来廃止予定）

```text
- strategy/deep_cfr_bridge.py: encode_game_state修正済み（C1-C6）、スートマッピング修正済み
- strategy/_deep_cfr_network.py: PokerNetwork定義
- models/deep_cfr/best_checkpoint.pt: Phase 3 v4モデル（品質不合格）
- config.yaml deep_cfr.fallback_to_solver: true
Stage D完了まで残す。
```

### 6.3 Rust postflop CLI（廃止方針確定）

```text
- solver/solver_bridge.py: フォールバック経路からも除外済み
- Stage D完了まで残すが使わない。
```

### 6.4 戦略ルーティング現在状態

```text
全街・全人数共通フォールバック経路:
  PokerRL+GRPO → Deep CFR → LLM → スキップ（Stage D完了まで保持）
  Rust Solver はフォールバック経路から除外済み
```

---

## 7. Deep CFR訓練情報（参照用、前回から変更なし）

前回スナップショット §4 と同一。要約のみ記載:

```text
Phase 3 v4: mixed 10,000 iterations, memory 20M
最終profit vs random: 46.07
ライブテスト: 9局面中合理的1局面のみ → 品質不合格確定
正しいエンコーディングでも同じ偏った出力 → モデル自体の問題
「profit vs random」は単独評価指標として使用禁止（教訓）
```

---

## 8. 156次元エンコーディング対応表（Deep CFR用、参照用）

前回スナップショット §5 と同一。新エンジンでは不要だが参照用に保持。

```text
[0:52] hero hand, [52:104] board, [104:109] stage, [109] pot,
[110:116] button, [116:122] current player, [122:146] per-player,
[146] min_bet, [147:151] legal actions, [151:156] previous action
スート: Clubs=0, Diamonds=1, Hearts=2, Spades=3
```

---

## 9. seat番号とテーブル配置（不変）

```text
座標プロファイル: profiles/coinpoker_6max.json
seat 1 = Hero = 下中央, seat 2 = 右下, seat 3 = 右上,
seat 4 = 上中央, seat 5 = 左上, seat 6 = 左下
```

---

## 10. 主要コードファイル

```text
core/game_state.py          GameState/PlayerState/HeroState/ActionRecord定義
core/hand_manager.py        ハンドライフサイクル管理、DB保存
core/game_loop.py           メインループ、戦略ルーティング
strategy/deep_cfr_bridge.py Deep CFR推論ブリッジ（品質不合格）
strategy/_deep_cfr_network.py PokerNetwork定義
strategy/recommendation_engine.py 戦略ルーティング
strategy/llm_pipeline.py    OpenRouter API呼び出し
strategy/pokerrl_bridge.py          ★ 新: 骨格のみ (commit 1edf9c3)
strategy/pokerrl_prompt_builder.py  ★ 新: 骨格のみ
strategy/pokerrl_inference_engine.py★ 新: 骨格のみ
strategy/pokerrl_heads.py           ★ 新: 骨格のみ
strategy/pokerrl_output_parser.py   ★ 新: 骨格のみ
strategy/pokerrl_spot_classifier.py ★ 新: 骨格のみ
solver/solver_bridge.py     Rust postflop CLI連携（廃止予定）
gui/main_window.py          PyQt6メインウィンドウ
gui/hud_overlay.py          HUDオーバーレイ
profiles/coinpoker_6max.json 座標プロファイル
config.yaml                 設定ファイル
docs/SPEC.md                正仕様（v3.5、PokerRL+GRPO反映済み）
docs/DESIGN_NOTES.md        設計判断理由（Section 49まで）
docs/snapshot.md            本ファイル
docs/PokerRL+GRPO 6-max NLHE.md  実装指令書v1.1（Git未追跡、v1.2更新待ち）
```

---

## 11. ファイル変更履歴（本セッション）

### poker-assistant リポジトリ

| ファイル | 変更 | commit |
|---|---|---|
| strategy/pokerrl_bridge.py | 新規: 骨格 | 1edf9c3 |
| strategy/pokerrl_prompt_builder.py | 新規: 骨格 | 1edf9c3 |
| strategy/pokerrl_inference_engine.py | 新規: 骨格 | 1edf9c3 |
| strategy/pokerrl_heads.py | 新規: 骨格 | 1edf9c3 |
| strategy/pokerrl_output_parser.py | 新規: 骨格 | 1edf9c3 |
| strategy/pokerrl_spot_classifier.py | 新規: 骨格 | 1edf9c3 |

### 訓練リポジトリ (C:\dev\pokerrl-training)

| ファイル | 変更 |
|---|---|
| scripts/analyze_multiway.py | 新規 |
| scripts/extract_multiway.py | 新規 |
| scripts/analyze_multiway_extracted.py | 新規 |
| scripts/prepare_sft_10k.py | 新規 |
| scripts/run_sft_comparison.py | 新規 |
| scripts/monitor_sft.py | 新規 |
| data/sft_train_10k.jsonl | 新規 |
| data/sft_eval_1k.jsonl | 新規 |
| data/multiway_raw/* | 新規（抽出結果） |
| data/multiway_analysis_report.md | 新規 |
| results/sft_comparison/data_preparation_report.txt | 新規 |

---

## 12. 実装指令書v1.1との乖離点（v1.2更新時に反映）

| 指令書v1.1の記述 | 現状 | v1.2で反映すべき変更 |
|---|---|---|
| §4.1 モデル候補: Phi-4-mini / Qwen3-4B / Gemma 3-4B / SmolLM3 / Qwen3-1.7B | Gemma 4 E2B最有力、Qwen3.5-4B脱落 | モデル候補表を全面更新 |
| §4.2 第1選択: Phi-4-mini | Gemma 4 E2Bとの比較待ち | 比較結果で確定 |
| §3.1 データ: PokerBench 560k + Pluribus 60k | + phh-dataset 2,090万multiway | データソースにphh-datasetを追加 |
| §3.1 データ比率未言及 | active player数分布管理に決定 | データ設計方針セクション追加 |
| §5.1 訓練リポジトリ未作成 | `C:\dev\pokerrl-training`作成済み | パス確定 |
| §5.2 LoRA r=64, alpha=128 | r=32, alpha=32で実行中 | poker_rl作者設定に合わせて修正 |
| §2.4 Sizing Head: sigmoid | カテゴリカルへ変更提案済み | 確定後に反映 |
| §10 Sprint 1: 未着手 | 大半完了 | 完了タスクをチェック |
| §1.2 models/pokerrl/base_qwen3_4b/ | モデル未確定 | 確定モデルのパスに変更 |
| Layer 3 (PokerKit合成) 言及なし | 不要と判定 | 明示的に「不要」と記載 |

---

## 13. 確定した制約（次セッション以降も有効）

### 13.1 永久廃止

- **Rust postflop CLI（Solver）は永久廃止**: フォールバック経路からも除外済み

### 13.2 品質不合格（保持）

- **Deep CFRモデルは品質不合格**: Stage D完了までフォールバックとして残す

### 13.3 評価基準

- **「profit vs random」は単独評価指標として使用禁止**
- PokerRL+GRPOの品質評価基準（SPEC 10A.11）:
  - Spot Checks 50シナリオで95%合格
  - Entropy健全（top-1確率中央値 ≤ 0.85）
  - PokerBench Postflop accuracy ≥ 60%
  - Slumbot HU ≥ -15 bb/100

### 13.4 削除禁止

- 既存Deep CFR/Solverコードは新エンジン統合完了（Stage D）まで削除禁止
- verify_pokerrl_encode.pyの検証をスキップしない
- Spot Checks 50シナリオを削除・緩和しない
- エントロピー崩壊対策なしに4B以上のモデルを訓練しない
- PokerBench/Pluribusデータの品質を確認せずに訓練を開始しない

### 13.5 ハードウェア・予算

- RTX 3080 (VRAM 10GB, RAM 32GB)
- クラウドは$500上限
- 全体タイムボックス: 12週間（最大15週間）

### 13.6 データ設計制約（本セッションで確定）

- PokerBench postflop 500kは100% HU。multiway訓練にはphh-dataset抽出データが必須
- データ混合はactive player数の分布管理で行う（HU:multiway固定比率は使わない）
- phh-datasetの勝者行動を優先選別するが、ラッキー勝利の問題は実験で検証
- Layer 3（PokerKit合成データ）は不要（phh-datasetで十分）

### 13.7 モデル選定制約（本セッションで確定）

- Qwen3.5-4BはRTX 3080 10GBでQLoRA SFT不可（13,120 MiB必要）
- クラウドGPU使用時のみ第3候補として残す

### 13.8 既存原則（不変）

- Quality over Speed
- No provisional recommendations
- GameLoop must never freeze
- State-only HUD when not ready
- Stale context discard
- No silent fallback

---

## 14. PokerRL+GRPO実装計画（指令書v1.1要約 + 更新）

### 14.1 Stage移行計画（変更なし）

```text
Stage A → B → C → D（指令書v1.1 §6.1と同一）
```

### 14.2 Sprint計画（更新版）

| Sprint | 期間 | 内容 | 状態 |
|---|---|---|---|
| Sprint 1 | Week 1-3 | データ取得 + multiway分析 + モデル選定 | 🔄 実行中 |
| Sprint 2 | Week 4-6 | Phase 1 SFT本訓練（データ設計含む） | ⬜ 未開始 |
| Sprint 3 | Week 7-10 | Phase 2 GRPO強化学習 | ⬜ 未開始 |
| Sprint 4 | Week 11-12 | 推論ブリッジ統合 + Shadow Mode | ⬜ 未開始 |
| Sprint 5 | Week 13 | 本番切替 + モニタリング | ⬜ 未開始 |
| Sprint 6 | Week 14 | 旧コンポーネント削除（オプション） | ⬜ 未開始 |

**変更点**: Sprint 1を2週間→3週間に延長（multiway分析 + Gemma 4 E2B比較追加のため）

### 14.3 撤退基準

指令書v1.1 §9.2に記載。変更なし。

---

## 15. 次セッションの開始手順

1. 本snapshot.mdの内容を確認
2. **最優先: Phi-4-mini 10k SFTが完了しているか確認**
   - 完了していれば CP-2報告を取得（final_metrics.json、学習曲線、instruction先頭3件全文）
3. **Gemma 4 E2Bダウンロード + 同一10kデータでSFT実施**
   - Gemma 4 E2Bのモデルパス: `models/gemma-4-e2b/`
   - Unslothの`use_cache`バグに注意
   - LoRA設定はPhi-4-miniと同一 (r=32, alpha=32, all-linear, dropout=0.1)
4. 比較レポート作成 → プライマリモデル確定
5. 指令書v1.2更新（§12の乖離点を全て反映）
6. Sprint 2（Phase 1 SFT本訓練）開始

### 15.1 Gemma 4 E2B SFT実施時の注意事項

- ダウンロード: `huggingface_hub.snapshot_download("google/gemma-4-E2B-it")`
- Unslothを使う場合: `FastModel.from_pretrained("unsloth/gemma-4-E2B-it")`
- `use_cache=False`のバグ（KV shared layers問題）に注意。Unsloth経由なら修正済み
- loss 13-15は正常（マルチモーダルモデルの特性）
- E2B固有のLoRAターゲットモジュールはpoker_rlのコードを参照:
  ```
  q_proj, v_proj, k_proj, o_proj, up_proj, down_proj, gate_proj,
  linear_left, linear_right, per_layer_projection
  ```
- hidden_stateが4次元テンソルの場合あり（`hidden_states[-1]`で最終層のみ取得）

### 15.2 CP-2で必要な報告内容

1. `final_metrics.json` の全内容
2. eval_loss と action_accuracy の推移（100 stepsごとの数値テーブル）
3. 訓練中にNaN/異常スパイクがなかったか
4. `data_preparation_report.txt` の instruction先頭3件全文

**最重要:**
- リポジトリURL: https://github.com/sanhyokim/poker-assistant（sanhyokim2050ではない）
- Deep CFRの失敗パターンを繰り返さないこと
- **multiwayデータの取り扱いは全報告で目立たせること（§2参照）**
- verify_pokerrl_encode.pyはSprint 2で最初に作ること
- 品質評価にはSpot Checks 50シナリオを必ず含めること
