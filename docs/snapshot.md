
# poker-assistant snapshot
**Updated:** 2026-06-14 JST
**Session:** Sprint 2 進行中 — HU SFT 82%飽和確認 → 補助ヘッド実装フェーズへ移行

---

## 0. このsnapshotの位置づけ

このsnapshotは、次セッションでポーカーAIアシスタント開発を再開するための現在地点メモである。
体系的な仕様は SPEC.md v3.8、設計判断の理由は DESIGN_NOTES.md（§61まで）を参照。

リポジトリ: https://github.com/sanhyokim/poker-assistant

**重要: sanhyokim2050 ではない。毎回この正しいURLを使うこと。**

**ドキュメント構成（3ファイル体制）:**
- snapshot.md — 現在地点（本ファイル）
- SPEC.md v3.8 — システム仕様（MW Context Engine仕様 §9.4.7〜§9.4.18、補助ヘッド仕様 §10A.2を含む）
- DESIGN_NOTES.md（§61まで） — 設計判断理由。§61はSFT飽和と補助ヘッド追加の判断理由

**実装指令書（`docs/PokerRL+GRPO 6-max NLHE.md`）は廃止済み。次セッションで渡す必要なし。**

**現在地: Sprint 2。HU SFT は15セグメント完了（accuracy 81–82.3%で飽和確認）。テキスト生成方式SFTを停止し、補助ヘッド（Action Head + Sizing Head）実装フェーズに移行。MW Context Engine Step 1〜4 完了、実戦テスト5/5 PASS。pytest 全1523 passed。**

---

## 1. 現在地点

### 1.1 最新テスト結果

```text
poker-system:
  pytest tests/test_context_engine.py -q → 82 passed
  pytest tests/test_multiway_engine.py -q → 38 passed
  pytest -q → 1523 passed, 0 failed, 7 warnings（2026-06-09確認、GPU空き状態）
  MW実戦テスト scripts/test_mw_live.py → 5/5 PASS（3人・4人・5人全パターン）
```

### 1.2 GitHub push状況

最新commit (poker-system):
```text
(最新) 修正: test_multiway_engine.pyのMockを新LLM呼び出し経路に対応 - 20件のFAIL解消
78e839a config: llm.mw_modelにopenai/gpt-5.4-miniを追加
95667e5 docs: SPEC v3.8 + DESIGN_NOTES §60 - MW Context Engine完了、GPT-5.4-mini採用、チェックポイント修正記録
```

最新commit (pokerrl-training、ローカルのみ、リモートなし):
```text
6408985 修正: checkpoint保存時のGPUメモリ解放 - BSOD対策
1d4f6ab 改善: monitor_sft.pyのtotal_stepsを動的計算に変更
```

### 1.3 Sprint 2 進捗

#### HU SFT タスク

| タスク | 状態 | 備考 |
|---|---|---|
| S2-T1a: prepare_sft_full.py作成 | ✅ 完了 | postflop 500k + preflop 63.2k = 563,200件 |
| S2-T1b: run_sft_comparison.py改修 | ✅ 完了 | resume_from, checkpoint完全保存, BSOD対策 |
| S2-T1c-1: 30k SFT checkpoint-500 | ✅ 完了 | accuracy 65.0%, eval_loss 0.378。Go判定 |
| S2-T1c-2: 10k区切り自動連続SFT | ✅ **飽和確認・停止** | 15セグメント完了。82%で飽和。156k件消化（27.7%） |
| S2-T3: 補助ヘッド（Action/Sizing）訓練 | ⬜ **次タスク** | LoRA凍結 + ヘッドのみ訓練方式 |
| S2-T4: 量子化 | ⬜ 未開始 | |
| S2-T5: 最終Go/No-go | ⬜ 未開始 | |

#### MW Context Engine タスク

| タスク | 状態 | 備考 |
|---|---|---|
| Step 1〜4 実装 | ✅ 完了 | 82テスト PASS |
| Phase 0（15件LLM比較テスト） | ✅ 完了 | GPT-5.4-mini 採用確定 |
| MW 実戦統合テスト（モック） | ✅ 完了 | 5/5 PASS（3人・4人・5人） |
| Phase 1（50件定性評価） | ⬜ 未開始 | |
| Phase 2（500件定量評価） | ⬜ 未開始 | |

#### ドキュメント・品質タスク

| タスク | 状態 | 備考 |
|---|---|---|
| snapshot.md 更新 | 🔄 本ファイル | |
| DESIGN_NOTES.md §61 追加 | ⬜ 本セッションで実施 | SFT飽和+補助ヘッド判断理由 |
| accuracy内訳分析 | ✅ 完了 | bet 62.5%, raise 69.1%が弱い |
| pytest全体テスト | ✅ 完了 | 1523 passed, 0 failed |

### 1.4 HU SFT accuracy推移（全15セグメント確定）

| セグメント | データ範囲 | accuracy | eval_loss | perplexity | 所要時間 |
|---|---|---|---|---|---|
| seg_000_offset_16000 | 16k–26k | 69.1% | 0.296 | 1.344 | 16.0h |
| seg_000_offset_26000 | 26k–36k | 75.1% | 0.270 | 1.310 | 14.1h |
| seg_000_offset_36000 | 36k–46k | 78.8% | 0.249 | 1.283 | 12.9h |
| seg_001_offset_46000 | 46k–56k | 78.9% | 0.249 | 1.283 | 13.6h |
| seg_002_offset_56000 | 56k–66k | 79.9% | 0.255 | 1.290 | 13.2h |
| seg_003_offset_66000 | 66k–76k | **82.0%** | **0.225** | **1.252** | 12.2h |
| seg_004_offset_76000 | 76k–86k | **82.3%** | 0.235 | 1.265 | 20.5h |
| seg_000_offset_86000 | 86k–96k | 81.0% | 0.255 | 1.291 | 14.7h |
| seg_001_offset_96000 | 96k–106k | 81.3% | 0.244 | 1.277 | 10.1h |
| seg_000_offset_106000 | 106k–116k | 82.2% | 0.238 | 1.268 | 14.5h |
| seg_001_offset_116000 | 116k–126k | 81.6% | 0.229 | 1.257 | 12.9h |
| seg_002_offset_126000 | 126k–136k | 81.9% | 0.235 | 1.265 | 12.7h |
| seg_003_offset_136000 | 136k–146k | 81.2% | 0.226 | 1.253 | 16.4h |
| seg_004_offset_146000 | 146k–156k | 81.2% | 0.226 | 1.253 | 16.4h |

**飽和確認**: 76k–86k(seg_003_offset_66000)で82.0%に到達後、86k–156k の8セグメントは81.0%–82.3%の範囲を横ばい。データ追加による改善は限界に到達。

**epoch別過学習パターン**: 全セグメントでepoch 2がピーク、epoch 3でeval_lossが悪化。seg_004_offset_146000では epoch 1: eval_loss 0.186 → epoch 2: 0.189 → epoch 3: 0.226（+19.6%悪化）。

**結論**: テキスト生成方式SFTの改善限界に到達。補助ヘッド追加で bet/raise 精度の直接改善を図る。

### 1.5 accuracy内訳分析（seg_004_offset_76000の final_adapter で実施）

```text
action_type  correct  total  accuracy  主な誤り
bet              65    104   62.5%    check に誤分類 39件
call            212    259   81.9%    raise に誤分類 32件、fold に 15件
check           214    234   91.5%    bet に誤分類 19件
fold            211    238   88.7%    call に誤分類 20件
raise           114    165   69.1%    call に誤分類 39件
overall: 816/1000 (81.6%)
```

**パターン**: 攻撃的アクション（bet 62.5%, raise 69.1%）が弱く、パッシブ方向に偏り。bet→check、raise→callと一段階消極的に間違える傾向。補助ヘッド（Action Head 分類方式）でこの偏りを改善する。

### 1.6 補助ヘッド実装方針（S2-T3、次タスク）

**方式**: 既存LoRA重みを凍結し、補助ヘッド（Action Head + Sizing Head）のみを新規訓練。

**設計**:
- ベースモデル: Phi-4-mini + seg_004_offset_76000 の final_adapter（accuracy 82.0%、eval_loss最良）
- LoRA重み: 全パラメータ freeze（既存の表現力を保護）
- Action Head: 最終hidden state → MLP → 4クラス分類（Fold / Check-Call / Raise / All-in）
- Sizing Head: 最終hidden state → MLP → sigmoid → 0.1x–3.0x pot比率
- 訓練データ: 同一PokerBenchデータ。テキストラベルをクラスラベル + pot比率に変換
- 訓練時間見込み: 数時間〜半日（ヘッドパラメータはLoRAの数百分の一）

**選定理由**:
- seg_003_offset_66000 の final_adapter（82.0%）を採用。eval_loss 0.225 が全セグメント中最良
- seg_004_offset_76000（82.3%）は accuracy は最高だが eval_loss 0.235 で微劣
- eval_loss が低い方がモデルの内部表現の質が高く、補助ヘッドの入力として適切

**フォールバック**: 補助ヘッドの品質が不十分な場合、LoRA凍結を解除して同時ファインチューニングに進む。最悪の場合でも既存LoRA重みは保存済みのため、テキスト生成方式にいつでも戻せる。

**仕様参照**: SPEC §10A.2（Action Head 4クラス + Sizing Head sigmoid）、DESIGN_NOTES §49（autoregressive生成ではなく補助ヘッドを採用した理由）

### 1.7 MW実戦統合テスト結果

scripts/test_mw_live.py で5ケースを実行（実際のOpenRouter API呼び出し）:

| ケース | 人数 | 状況 | action | size | latency | 妥当性 |
|---|---|---|---|---|---|---|
| 1 | 3人 | AhKh フロップ ノーベット | bet | 220 | 2078ms | 妥当 |
| 2 | 3人 | 9s8s フロップ facing bet | fold | - | 1469ms | 妥当 |
| 3 | 3人 | KsKh ターン セット | bet | 700 | 3940ms | 妥当 |
| 4 | 4人 | QdJd フロップ ノーベット | check | - | 2201ms | 妥当 |
| 5 | 5人 | AcTc フロップ facing bet | call | - | 1036ms | 妥当 |

### 1.8 システム全体の状態

| エンジン | 状態 |
|---|---|
| Rust postflop CLI | 永久廃止確定 |
| Deep CFR | 品質不合格。Stage D完了まで残す |
| HU SFT (Phi-4-mini) | 82%飽和。テキスト生成SFT停止。補助ヘッドフェーズへ |
| MW Context Engine | Step 1〜4完了（82テスト）。GPT-5.4-mini採用確定。実戦テスト5/5 PASS |

---

## 2. 確定した制約（次セッション以降も有効）

### 2.1 永久廃止
- Rust postflop CLI（Solver）は永久廃止

### 2.2 品質不合格（保持）
- Deep CFRモデルは品質不合格。Stage D完了まで残す

### 2.3 評価基準
- 「profit vs random」は単独評価指標として使用禁止
- Spot Checks 50シナリオを削除・緩和しない
- verify_pokerrl_encode.pyの検証をスキップしない（Sprint 4で実施）

### 2.4 削除禁止
- 既存Deep CFR/Solverコードは新エンジン統合完了（Stage D）まで削除禁止

### 2.5 ハードウェア・予算
- RTX 3080 (VRAM 10GB, RAM 32GB)
- クラウドは$500上限
- 全体タイムボックス: 12週間（最大15週間）

### 2.6 データ設計制約
- phh-dataset hole cards付き726,570件は全件敗者。positive example使用禁止
- MW教師ラベルの品質問題はContext Engineで回避（訓練不要のルール層）

### 2.7 訓練運用
- save_steps=150、eval_steps=300（BSOD対策でずらし。デフォルトとする）
- keep_checkpoints=3
- eval_dataは1,000件（5,000件はVRAM不足でハング）

### 2.8 ドキュメント体制
- 3ファイル体制: snapshot.md + SPEC.md + DESIGN_NOTES.md
- 実装指令書は廃止済み。渡す必要なし
- 撤退基準はDESIGN_NOTES §56に記載

### 2.9 MW Context Engine 運用
- MW本採用モデル: GPT-5.4-mini（OpenRouter経由、openai/gpt-5.4-mini）
- .env の load_dotenv(override=True) を使用すること
- config.yaml に llm.mw_model: openai/gpt-5.4-mini 設定済み

### 2.10 BSOD対策（実装済み・有効）
- checkpoint保存前にtorch.cuda.empty_cache()
- _save_optimizer_cpu()でCPU経由保存
- save_steps=150, eval_steps=300（ずらし必須）
- 古いfinal_adapterは訓練開始時に自動削除
- optimizer.pt 0バイト検出で fresh start

---

## 3. 主要コードファイル

```text
[poker-assistantリポジトリ]
strategy/context_engine.py          MW Context Engine本体
strategy/multiway_engine.py         MW GameLoop統合
tests/test_context_engine.py        82テスト
tests/test_multiway_engine.py       38テスト（Mock修正済み）
scripts/test_mw_live.py             MW実戦統合テスト（5ケース）
config.yaml                         llm.mw_model: openai/gpt-5.4-mini

[訓練リポジトリ C:\dev\pokerrl-training（リモートなし、ローカルgitのみ）]
scripts/prepare_sft_full.py         フルデータ準備完了
scripts/run_sft_comparison.py       checkpoint完全保存/再開 + BSOD対策済み
scripts/run_sft_sequential.py       10k区切り自動連続実行
scripts/monitor_sft.py              total_steps動的計算対応済み
analyze_accuracy.py                 accuracy内訳分析（使い捨て）
data/sft_train_full.jsonl           563,200件（636 MB）
data/sft_eval_1k.jsonl              1,000件（全SFTで共用）
```

---

## 4. 訓練リポジトリの状態

```text
C:\dev\pokerrl-training/
├── data/
│   ├── sft_train_full.jsonl        563,200件（636 MB）
│   └── sft_eval_1k.jsonl           1,000件
├── models/phi-4-mini-instruct/     Phi-4-mini FP16
├── results/sft_sequential/
│   ├── sequential_run_log.jsonl    全15セグメント記録
│   ├── seg_000_offset_16000/       完了（69.1%）
│   ├── seg_000_offset_26000/       完了（75.1%）
│   ├── seg_000_offset_36000/       完了（78.8%）
│   ├── seg_001_offset_46000/       完了（78.9%）
│   ├── seg_002_offset_56000/       完了（79.9%）
│   ├── seg_003_offset_66000/       完了（82.0%）★eval_loss最良 → 補助ヘッドベース
│   ├── seg_004_offset_76000/       完了（82.3%）★accuracy最高
│   ├── seg_000_offset_86000/       完了（82.3%）
│   ├── seg_001_offset_96000/       完了（81.0%）
│   ├── seg_000_offset_106000/      完了（81.3%）
│   ├── seg_001_offset_116000/      完了（82.2%）
│   ├── seg_002_offset_126000/      完了（81.6%）
│   ├── seg_003_offset_136000/      完了（81.9%）
│   ├── seg_004_offset_146000/      完了（81.2%）
│   └── seg_005_offset_156000/      途中停止（Pythonプロセス手動停止）
├── analyze_accuracy.py
└── .venv/                          torch 2.11.0+cu130, transformers 5.10.2
```

---

## 5. 次セッションの作業

### 5.1 補助ヘッド実装手順（S2-T3）

1. **DESIGN_NOTES §61 確認** — SFT飽和判断と補助ヘッド方針の理由
2. **訓練スクリプト作成** — LoRA凍結 + Action Head + Sizing Head の訓練スクリプト
   - ベースアダプタ: `results/sft_sequential/seg_003_offset_66000/final_adapter`
   - 訓練データ: `data/sft_train_full.jsonl`（ラベル変換が必要）
   - eval データ: `data/sft_eval_1k.jsonl`
3. **ラベル変換** — テキストラベル（"fold" / "call" / "raise 300"）→ クラスID（0-3）+ pot比率
4. **訓練実行** — 数時間〜半日の見込み
5. **評価** — accuracy内訳の再分析（特にbet/raiseの改善度）
6. **結果が不十分な場合** — LoRA凍結解除して同時ファインチューニングを検討

### 5.2 ドキュメント更新

| ファイル | 更新内容 | 状態 |
|---|---|---|
| snapshot.md | 本ファイルで張り替え | 本セッションで完了 |
| DESIGN_NOTES.md §61 | SFT飽和+補助ヘッド判断理由 | 本セッションで実施 |
| SPEC.md | 補助ヘッド実装後に結果反映 | 後日 |

### 5.3 次セッションの持ち物

| # | ドキュメント | 用途 |
|---|---|---|
| 1 | **本snapshot.md** | 現状認識の基盤 |
| 2 | **SPEC.md v3.8** | §10A.2 補助ヘッド仕様 |
| 3 | **DESIGN_NOTES.md（§61まで）** | §49 補助ヘッド設計理由、§61 SFT飽和判断 |
```
