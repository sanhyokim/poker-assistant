

全4ドキュメントを精読しました。現状を整理し、更新作業に入ります。

---

## 現状認識

**GitHub上の状態（最終push: commit 87010d5, 2026-06-01）:**
- DESIGN_NOTES.md: §52まで → ローカルで§53-55追加済み（294 insertions）、push待ち
- snapshot.md: 2026-05-31版 → 今回添付されたのは2026-06-02 15:00 JST版（ローカル最新、未push）
- SPEC.md: v3.5（2026-05-22）→ push済み（commit 6306c86）
- 実装指令書: v1.2（2026-06-01）→ Git未追跡

**今回添付された4ドキュメントで反映されていない重要事項:**

1. **phh-dataset hole cards 726,570件が全件敗者** → DESIGN_NOTES §53に追記済み、snapshot §1.5のweighted SFT方針は**旧方針のまま残っている**（破綻しているのに修正されていない）
2. **PokerSkill論文分析** → DESIGN_NOTES §54に追記済み、他ドキュメントは未反映
3. **MW方針転換（weighted SFT → PokerSkill式）** → DESIGN_NOTES §55に追記済み、snapshot/実装指令書/SPECは未反映
4. **snapshotの§1.5とS2-T2a〜T2eが破綻した旧方針のまま** → 次セッションのAIが旧方針を実行してしまう危険

---

## 作業順序（確認済み）

1. **snapshot.md 全文出力** ← 今からやる（最重要：次セッションの起点）
2. **SPEC.md 編集指示書** ← snapshot後
3. **実装指令書 v1.3 編集指示書** ← SPEC後

---

## 1. snapshot.md 全文出力

以下がそのままGitHubにpushできる完成版です。

---

````markdown
# poker-assistant snapshot
**Updated:** 2026-06-02 22:00 JST
**Session:** Sprint 2 進行中 — 30k HU SFT実行中、MW方針転換確定

---

## 0. このsnapshotの位置づけ

このsnapshotは、次セッションでポーカーAIアシスタント開発を再開するための現在地点メモである。
体系的な仕様は SPEC.md、設計判断の理由は DESIGN_NOTES.md を参照。
PokerRL+GRPOの訓練・統合計画は `docs/PokerRL+GRPO 6-max NLHE.md`（実装指令書v1.2、v1.3改訂待ち）を参照。

リポジトリ: https://github.com/sanhyokim/poker-assistant

**重要: sanhyokim2050 ではない。毎回この正しいURLを使うこと。**

**現在地: Sprint 2（Phase 1 SFT本訓練 + MW方針転換）。30,000件HU SFT第1弾が実行中（PID 18392、step 6/2,813、62秒/step、約49時間見込み）。最初のeval（step 500）は起動から約8.5時間後。MW戦略はweighted SFTからPokerSkill式Context Engine + LLM比較テストへ全面転換した。**

---

## 1. 現在地点

### 1.1 最新テスト結果

```text
pytest -q
1441 passed, 0 failed, 7 warnings
```

### 1.2 GitHub push状況

DESIGN_NOTES.md §53-55 追記済み（ローカル）、push待ち。
本snapshot.mdも置き換えてpush予定。

最新push済みcommit:
```text
87010d5 docs: snapshot.md updated (Sprint 1完了、Phi-4-mini確定)
47b232c docs: DESIGN_NOTES sections 50-52 added
1edf9c3 追加: PokerRL推論ブリッジ骨格ファイル
```

push待ち:
```text
- DESIGN_NOTES.md §53-55（phh-datasetバイアス、PokerSkill論文、MW方針転換）
- snapshot.md（本ファイルで全面置き換え）
- SPEC.md（§9.4 MW方針の更新指示あり、未編集）
- 実装指令書v1.2 → v1.3（未編集、Git未追跡）
```

### 1.3 Sprint 2 進捗

#### HU SFT タスク（変更なし、継続中）

| タスク | 状態 | 備考 |
|---|---|---|
| S2-T1a: prepare_sft_full.py作成 | ✅ 完了 | postflop 500k + preflop 63.2k = 563,200件 |
| S2-T1b: run_sft_comparison.py改修 | ✅ 完了 | resume_from, data_offset, keep_checkpoints追加。検証済み |
| S2-T1c: HU SFT 第1弾（30,000件） | 🔄 実行中 | PID 18392。step 6/2,813。62秒/step、約49時間見込み。VRAM 9,809 MiB |
| S2-T1d: 第1弾eval確認 | ⬜ 未開始 | 最初のeval: step 500（起動から約8.5時間後） |
| S2-T1e: HU SFT 続き（30,001件目〜） | ⬜ 未開始 | checkpoint + data_offsetで再開 |

#### MW タスク（★★★ 全面入替 ★★★）

旧タスク S2-T2a〜T2e（confidence-weighted SFT）は**全て破棄**。
理由: phh-dataset hole cards 726,570件が全件敗者（DESIGN_NOTES §53）。
positive exampleとしてのmultiway訓練データが構造的に入手不可。

| 旧タスク | 状態 | 破棄理由 |
|---|---|---|
| ~~S2-T2a: confidence scoringスクリプト~~ | ❌ 破棄 | 勝者データが0件、スコアリング前提崩壊 |
| ~~S2-T2b: stratified sampler + HU rehearsal~~ | ❌ 破棄 | 高品質データの選別が不可能 |
| ~~S2-T2c: weighted SFT実行~~ | ❌ 破棄 | positive exampleなし |
| ~~S2-T2d: multiway Go/No-go判定~~ | ❌ 破棄 | weighted SFT自体が実行不可 |
| ~~S2-T2e: KTO検討~~ | ❌ 破棄 | desirable/undesirableの分類根拠なし |

新タスク（PokerSkill式Context Engine + LLM比較テスト）:

| タスク | 状態 | 備考 |
|---|---|---|
| S2-T2-NEW-a: Context Engine実装 | ⬜ 未開始 | board texture、23 hand class、SPR、ATT/DEF budget計算。決定論的Pythonスクリプト |
| S2-T2-NEW-b: 構造化プロンプト生成 | ⬜ 未開始 | Context Engine出力 → LLMプロンプト変換 |
| S2-T2-NEW-c: Phase 0（5件パイプライン確認） | ⬜ 未開始 | phh-datasetからMWスポット抽出 → Context Engine → プロンプト → LLM → 出力確認 |
| S2-T2-NEW-d: Phase 1（50件定性評価） | ⬜ 未開始 | GPT-5.4-mini vs Phi-4-mini（素モデル）出力比較、人間判定 |
| S2-T2-NEW-e: Phase 2（500件定量評価） | ⬜ 未開始 | action accuracy、分布偏り、ポットタイプ別・ストリート別傾向 |

#### 残りタスク（変更なし）

| タスク | 状態 | 備考 |
|---|---|---|
| S2-T3: 補助ヘッド（Action/Sizing）訓練 | ⬜ 未開始 | |
| S2-T4: 量子化 | ⬜ 未開始 | |
| S2-T5: 最終Go/No-go | ⬜ 未開始 | |

### 1.4 段階的訓練アプローチ（変更なし）

**フルデータ563,200件のSFTを一括で実行するのではなく、段階的に訓練する方針。**

| 段階 | データ | 推定時間（RTX 3080） | 目的 |
|---|---|---|---|
| 第1弾 | 30,000件 | 約49時間（約2日） | 品質確認。問題あれば早期に対処 |
| 続き | 30,001件目〜 | ローカル続行 or クラウドGPU | フルデータ訓練完了 |

**途中再開の仕組み（run_sft_comparison.py改修済み）:**
- save_steps=500（500 step単位でcheckpoint保存、1 step=32件なので16,000件ごと）
- 直近3個のみ保持、古いものは自動削除（1 checkpoint約150MB、3個で約450MB）
- `--resume_from`: 保存済みLoRAアダプタから訓練再開。`PeftModel.from_pretrained(..., is_trainable=True)`方式
- `--data_offset`: データの途中から開始（例: 30000で30,001件目から）
- `--keep_checkpoints`: 保持するcheckpoint数を指定（デフォルト3）

**クラウドGPU判断基準:**
- 第1弾30,000件のeval結果でaccuracy ≥ 55%を確認した後に判断
- フルデータ訓練（残り533,000件）をローカルで続けると約44日
- クラウドH100なら3-5日、$150-300（予算$500上限内）
- 候補サービス: RunPod（H100 $3-4/h）、Vast.ai（A100 $1-2/h）、Lambda Labs（$1.5-3/h）

### 1.5 ★★★ MW方針: PokerSkill式Context Engine + LLM比較テスト ★★★

**旧方針（weighted SFT）は全面破棄。新方針を以下に定義する。**

#### 1.5.1 転換の理由（DESIGN_NOTES §53-55参照）

2つの発見が同時に起きた:

```text
1. phh-dataset hole cards付き726,570件が全件net_result ≤ 0（敗者）
   → PHHフォーマットの仕様上、showdown敗者のhole cardsのみ記録される
   → 勝者hole cardsは0件
   → confidence-weighted SFTの前提が構造的に崩壊

2. PokerSkill論文（arXiv 2605.30094）の発見
   → 訓練なし・ソルバーなしでLLMのポーカー判断を大幅改善するフレームワーク
   → Context Engine + ATT/DEF Budget + Skill Libraryの設計が公開
   → HU専用だがMW拡張可能な設計
```

#### 1.5.2 新MW方針の概要

PokerSkill式Context Engineを決定論的Pythonスクリプトとして実装し、構造化プロンプトを生成してLLMに渡す。

```text
第1層: Context Engine（決定論的、訓練不要）
  - board texture分類: dry / dynamic / wet / monotone / paired / connected
  - 23 hand class分類: 16 Made-Hand + 8 Drawing-Hand（PokerSkill Appendix E準拠）
  - SPR bucket: low (<3) / medium (3-10) / deep (>10)
  - ATT/DEF Budget計算: hand classごとの攻撃/防御予算
  - Pressure weight累積: 各actionのweight（25%pot≈0.3, 50%pot≈0.5, 100%pot≈1.0）

第2層: LLM判断（制約された行動空間内）
  - GPT-5.4-mini（API）またはPhi-4-mini（ローカルSFTモデル）
  - Context Engineが生成した構造化プロンプトを入力
  - ATT/DEF残予算に基づく行動制約内で最終判断
```

#### 1.5.3 PokerSkillの23 Hand Classes（Appendix E）

Made-Hand (16):
```text
Nuts, Strong set+, Overpair, Top pair good kicker,
Top pair weak kicker, Second pair, Third pair or lower,
Pocket pair below board, Ace high, King high or lower,
Two pair top, Two pair middle/bottom, Trips, Straight,
Flush, Full house+
```

Draw (8):
```text
Nut flush draw, Non-nut flush draw, Open-ended straight draw,
Gutshot, Double backdoor, Backdoor flush draw,
Backdoor straight draw, Combo draw (flush + straight)
```

Combo rule: Draw ATT bonus + Made-hand base ATT

#### 1.5.4 ATT/DEF Budget概念

各hand classにATT（攻撃予算）とDEF（防御予算）を割り当てる。
ストリートごとのaction weightを累積し、残予算で行動を制約する。

```text
残ATT > 0 → bet/raise可能
残DEF > 0 → call可能
残ATT ≤ 0 and 残DEF ≤ 0 → fold推奨
```

Pressure weight table（46段階、論文Appendix E準拠）:
```text
25% pot ≈ 0.3
33% pot ≈ 0.4
50% pot ≈ 0.5
75% pot ≈ 0.8
100% pot ≈ 1.0
150% pot ≈ 1.5
All-in ≈ 2.0+（SPR依存）
```

#### 1.5.5 MW拡張の未確定事項

PokerSkill論文はHU専用。MW拡張には以下の調整が必要:

```text
要調整（Phase 0-2で検証）:
  - ATT/DEFバジェット値のMW修正子（推定: ATT -1.0〜-1.5 per extra player）
  - プリフロップレンジ（6-max用に拡張）
  - アクションラインシナリオ（HU 60 → MW追加）
  - Viable Action Logic（複数opponent考慮）
  - ブラフ頻度の低下（MW特有）
```

これらはPhase 0-2のテストで実験的に決定する。論文の値をそのまま適用しない。

#### 1.5.6 テスト計画

```text
Phase 0（パイプライン確認）: 5-10件
  - multiway_decisions.jsonlからMWスポットを手動選択
  - Context Engine → 構造化プロンプト → LLM → 出力
  - スクリプトが動くか確認

Phase 1（定性評価）: 50件
  - GPT-5.4-mini vs Phi-4-mini（未SFT素モデル）の出力比較
  - 人間が読んで品質差を判定
  - ATT/DEF制約が機能しているか確認

Phase 2（定量評価）: 500-1,000件
  - action accuracy（phh-dataset実プレイヤー行動との一致率、参考値）
  - fold/call/raise分布の偏り
  - ポットタイプ別・ストリート別の傾向分析
  - GPT-5.4-mini vs Phi-4-mini（SFT後）の比較
```

#### 1.5.7 敗者データの限定的活用可能性

phh-dataset hole cards全件敗者は、positive exampleには使えないが:

```text
検討可能:
  - 「やってはいけない行動」の負例（将来KTOのundesirable側）
  - action history理解の補助学習
  - opponent modelingの訓練データ

使用禁止:
  - positive example（良い行動の教師ラベル）
  - confidence-weighted SFTの入力
```

### 1.6 フルSFT実行時の問題記録（変更なし）

| 問題 | 原因 | 対処 |
|---|---|---|
| フルデータepochs=3で47日 | 563,200件×3epochs÷effective_batch32=52,800steps、77秒/step | 段階的訓練に切り替え |
| step 500でeval中にハング | eval_data 5,000件でVRAM不足（残り173 MiB） | eval_dataを1,000件に縮小 |
| phi4_r2もstep 104で停止 | 段階的訓練切り替えのため手動停止 | checkpoint未保存（save_steps=2000で未到達） |

### 1.7 システム全体の状態（変更なし）

poker-assistantの画面認識・GameState管理・Preflop Chart・HUD表示・DB/replay保存は安定動作中。

| エンジン | 状態 |
|---|---|
| Rust postflop CLI | 永久廃止確定 |
| Deep CFR | 品質不合格。Stage D完了まで残す |
| PokerRL+GRPO | Sprint 2実行中。30k SFT第1弾実行中 |

### 1.8 ドキュメント状態

| ファイル | 状態 | 最新commit |
|---|---|---|
| docs/SPEC.md | push済み。§9.4 MW方針が旧式（PokerRL+GRPO推論+eval7補助のまま）→ 編集指示書で更新予定 | 6306c86 |
| docs/DESIGN_NOTES.md | §53-55追記済み（ローカル）、push待ち | ローカル |
| docs/snapshot.md | **本ファイルで全面置き換え** | |
| docs/PokerRL+GRPO 6-max NLHE.md | v1.2作成済み。v1.3編集指示書で更新予定。Git未追跡 | |

---

## 2. MULTIWAY データ品質分析結果（変更なし）

### 2.1 phh-dataset multiway抽出データのレコード構造

```json
{
  "source_file": "data\\phh-dataset\\data\\handhq\\...",
  "source_key": "16",
  "hand_id": 3406285766,
  "venue": "iPoker Network",
  "street": "flop",
  "acting_player": "p2",
  "acting_position": "BB",
  "hole_cards": ["5d", "4h"],
  "board_cards": ["6h", "7s", "3d"],
  "pot_size": 35.0,
  "active_player_count": 3,
  "flop_active_player_count": 3,
  "required_call": 0.0,
  "action_history": ["d dh p1 ????", "d dh p2 5d4h", "d dh p3 ????", "p3 cc", "p4 f", "p5 cc", "p1 f", "p2 cc", "d db 6h7s3d"],
  "actual_action": "check",
  "actual_amount": 0.0,
  "pot_type": "limped_pot",
  "winnings": 0.0,
  "net_result": -10.0
}
```

### 2.2 品質分析

- **教師ラベルはGTO正解ではなく実プレイヤー行動**
- hole cards付きは全体の3.47%（726,570件）のみ
- **★ 726,570件全件が net_result ≤ 0（敗者または引き分け）。勝者0件 ★**
- 59%がlimped pot（実戦6maxより偏り大）
- 相手のhole cardsは`????`で不明 → GTO正解を後から算出不可

### 2.3 PokerBench vs phh-dataset の品質差

| 項目 | PokerBench | phh-dataset multiway |
|---|---|---|
| 教師ラベルの出典 | GTOソルバー正解 | 実プレイヤーの行動 |
| 品質 | 高（GTO） | 不明（プレイヤーレベル依存）、**全件敗者** |
| multiway | 0件（100% HU） | 2,090万件 |
| hole cards | 全件あり | 3.47%のみ（726,570件、**全件敗者**） |
| pot type | 多様 | 59% limped pot |

### 2.4 統計

| Player count | Flop | Turn | River | Total |
|---|---|---|---|---|
| 2way | 1,396,103 | 2,389,989 | 2,002,768 | 5,788,860 |
| 3way | 7,552,759 | 2,712,534 | 1,142,368 | 11,407,661 |
| 4way | 2,177,423 | 608,352 | 206,901 | 2,992,676 |
| 5way+ | 566,155 | 124,616 | 35,672 | 726,443 |

| Pot type | 件数 | 割合 |
|---|---|---|
| limped_pot | 12,385,378 | 59% |
| srp | 7,990,511 | 38% |
| 3bet_plus_pot | 539,751 | 3% |

---

## 3. モデル選定結果（Sprint 1で確定、変更なし）

**Phi-4-mini-instruct 3.8B正式採用。**

10k SFT最終値: eval_loss 0.326, action_accuracy 65.6%, perplexity 1.385

訓練設定（全SFTで共通）:

| 設定 | 値 |
|---|---|
| ベースモデル | microsoft/Phi-4-mini-instruct |
| 量子化 | QLoRA 4-bit NF4 (bfloat16 compute) |
| LoRA | r=32, alpha=32, all-linear, dropout=0.1 |
| Optimizer | paged_adamw_8bit |
| Scheduler | cosine |
| LR | 1e-4 |
| Batch | 4 (gradient_accumulation=8, effective=32) |
| max_seq_len | 1024 |

---

## 4. 本セッションで完了したこと

### 4.1 DESIGN_NOTES.md §53-55 追記

- §53: phh-dataset hole cards全件敗者の発覚と影響分析
- §54: PokerSkill論文（arXiv 2605.30094）の構成・ATT/DEF Budget・23 hand class・MW拡張可能性
- §55: MW方針転換の宣言（weighted SFT → PokerSkill式Context Engine + LLM比較テスト）

### 4.2 MW方針転換の確定

- 旧タスクS2-T2a〜T2e全面破棄
- 新タスクS2-T2-NEW-a〜e定義
- テスト計画Phase 0-2定義

### 4.3 ドキュメント更新方針の確定

更新順序:
1. snapshot.md全文出力（本ファイル） ← 完了
2. SPEC.md編集指示書 ← 次
3. 実装指令書v1.3編集指示書 ← 次

### 4.4 前セッションからの継続事項（変更なし）

以下は前セッションで完了済みだが、本セッションでの変更はない:

- prepare_sft_full.py作成完了（postflop 500k + preflop 63.2k = 563,200件）
- run_sft_comparison.py改修完了（resume_from, data_offset, keep_checkpoints）
- 30k HU SFT第1弾起動（PID 18392）
- 段階的訓練方針確定

---

## 5. 訓練リポジトリの状態

### 5.1 ディレクトリ構成

```text
C:\dev\pokerrl-training/
├── .venv/                          Python仮想環境 (torch 2.12.0+cu130)
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
│   ├── sft_train_10k.jsonl         10k SFT訓練データ
│   ├── sft_eval_1k.jsonl           eval用データ（1,000件、全SFTで共用）
│   ├── sft_train_full.jsonl        フル訓練データ (563,200件、postflop→preflop連結順、シャッフルなし)
│   ├── sft_eval_full.jsonl         (5,000件、VRAM不足で使用不可)
│   └── sft_eval_preflop_1k.jsonl   preflop評価データ (1,000件)
├── models/
│   ├── phi-4-mini-instruct/        Phi-4-mini FP16 (確定採用)
│   └── qwen3.5-4b/                (VRAM超過で使用不可)
├── scripts/
│   ├── prepare_sft_10k.py          確認済み
│   ├── prepare_sft_full.py         フルデータ準備（完了）
│   ├── run_sft_comparison.py       改修完了（resume_from, data_offset, keep_checkpoints追加、検証済み）
│   ├── extract_multiway.py         確認済み（275行）
│   ├── analyze_multiway.py
│   ├── analyze_multiway_extracted.py
│   └── monitor_sft.py
├── results/
│   ├── sft_comparison/phi4/        10k SFT結果（完了、accuracy 65.6%）
│   ├── sft_full/phi4/              フルSFT第1回（eval 5kでハング、破棄）
│   ├── sft_full/phi4_r2/           フルSFT第2回（step 104で手動停止、checkpoint未保存）
│   ├── sft_full/phi4_30k/          ★ 30k SFT第1弾（実行中、PID 18392）
│   └── sft_full/data_preparation_report.txt
└── tools/
    └── poker_datasets_ref/
```

### 5.2 環境

```text
Python: .venv内
torch: 2.12.0+cu130, CUDA 13.0, bf16対応
GPU: RTX 3080 (10 GB)
空きディスク: 約358 GB
```

---

## 6. 既存システムの状態（変更なし）

```text
正常動作: 画面キャプチャ・認識、GameState、HandManager、Preflop Chart、HUD、LLM exploit、DB/Replay
戦略ルーティング: PokerRL+GRPO → Deep CFR → LLM → スキップ（Rust Solver除外済み）
テスト: 1441 passed
```

---

## 7. 確定した制約（次セッション以降も有効）

### 7.1 永久廃止
- Rust postflop CLI（Solver）は永久廃止

### 7.2 品質不合格（保持）
- Deep CFRモデルは品質不合格。Stage D完了まで残す

### 7.3 評価基準
- 「profit vs random」は単独評価指標として使用禁止
- Spot Checks 50シナリオを削除・緩和しない
- verify_pokerrl_encode.pyの検証をスキップしない（Sprint 4で実施）
- エントロピー崩壊対策なしに4B以上のモデルを訓練しない

### 7.4 削除禁止
- 既存Deep CFR/Solverコードは新エンジン統合完了（Stage D）まで削除禁止

### 7.5 ハードウェア・予算
- RTX 3080 (VRAM 10GB, RAM 32GB)
- クラウドは$500上限
- 全体タイムボックス: 12週間（最大15週間）

### 7.6 データ設計制約（★ MW方針転換で変更あり ★）
- Phase 1 SFTは2段階: 第1段階HU（継続中）→ 第2段階MW（旧weighted SFT → 新PokerSkill式）
- **phh-dataset hole cards付き726,570件は全件敗者。positive exampleとして使用禁止**
- **MW教師ラベルの品質問題はPokerSkill式Context Engineで回避する（訓練不要のルール層）**
- HU rehearsal: PokerSkill式テスト時も、HU SFTモデルの品質は維持する
- limped pot偏り(59%)はPhase 2定量評価で考慮する
- hole cardsなし2,090万件は、action history理解・opponent prior・public-state encoderの補助学習にのみ使用可能

### 7.7 プロンプトフォーマット
- HU SFT: PokerBenchの自然言語フォーマットをそのまま使用（精度最優先）
- MW: PokerSkill式構造化プロンプト（Context Engineが生成）

### 7.8 モデル選定
- Phi-4-mini-instruct 3.8Bが正式採用

### 7.9 訓練運用（変更なし）
- 段階的訓練: まず30,000件で品質確認、問題なければ続行
- save_steps=500
- keep_checkpoints=3
- eval_dataは1,000件
- クラウドGPU判断タイミング: 第1弾30k eval結果確認後

---

## 8. 主要コードファイル

```text
[poker-assistantリポジトリ]
strategy/pokerrl_bridge.py          骨格のみ (commit 1edf9c3)
strategy/pokerrl_prompt_builder.py  骨格のみ
strategy/pokerrl_inference_engine.py 骨格のみ
strategy/pokerrl_heads.py           骨格のみ
strategy/pokerrl_output_parser.py   骨格のみ
strategy/pokerrl_spot_classifier.py 骨格のみ

[訓練リポジトリ C:\dev\pokerrl-training]
scripts/prepare_sft_10k.py          確認済み
scripts/prepare_sft_full.py         完了
scripts/run_sft_comparison.py       改修完了（resume_from, data_offset, keep_checkpoints）
scripts/extract_multiway.py         確認済み（275行）
scripts/monitor_sft.py              訓練モニタリング
```

---

## 9. 未解決の課題・TODO

| 課題 | 対処タイミング | 備考 |
|---|---|---|
| **30k SFT step 500 eval結果確認** | **次セッション最優先** | accuracy ≥ 55%でGo |
| **SPEC.md §9.4 MW方針更新** | **次セッション（編集指示書あり）** | 旧「PokerRL+GRPO推論+eval7補助」→PokerSkill式を反映 |
| **実装指令書v1.2→v1.3更新** | **次セッション（編集指示書あり）** | §3.1.5 MW方針、Sprint 2タスク表の入替 |
| Context Engine実装（S2-T2-NEW-a） | 次セッション（30k SFT実行中に並行） | board texture, hand class, ATT/DEF計算 |
| MW ATT/DEF修正子の実験的決定 | Phase 0-2 | HU論文の値をそのままMWに適用しない |
| multiway_decisions.jsonlからMWテストスポット抽出 | Phase 0前 | 5-10件を手動選択 |
| 補助ヘッド設計（sigmoid vs categorical） | S2-T3 | 実装指令書v1.2 §9.2.1 Step 1a |
| クラウドGPU選定・コスト比較 | 30k eval後 | RunPod/Vast.ai/Lambda Labs |
| prepare_sft_full.pyでのシャッフル検討 | 次回SFT前 | 現在はpostflop→preflop連結順 |
| GitHub push（DESIGN_NOTES §53-55 + snapshot） | 次セッション | |

---

## 10. 実装指令書v1.2との乖離点

| 指令書v1.2の記述 | 現状の方針 | 備考 |
|---|---|---|
| §3.1.5: hole cards付き72万件から勝者行動を優先選別 | **全件敗者のため不可能。PokerSkill式に転換** | v1.3で修正必要 |
| §3.1.5: active player数分布管理 | **MW訓練データ自体を使わない。ルール層で対応** | v1.3で修正必要 |
| §5.2 Phase 1: 40-60時間 | RTX 3080では47日。段階的訓練+クラウドGPU併用 | |
| §5.5 verify_pokerrl_encode.py: Sprint 2 | Sprint 4に移動 | |
| §10 Sprint 2: save_steps=2000 | save_steps=500に変更 | |
| §9.2.6 Case A: PokerSkill風ハイブリッド | **撤退後の代替案ではなく、MW主方針として即時採用** | v1.3で修正必要 |

---

## 11. 次セッションの開始手順

### 11.1 最初に確認すること

1. 本snapshot.mdの内容を確認
2. **30k SFTの進捗確認**
   ```powershell
   cd C:\dev\pokerrl-training
   .venv\Scripts\python.exe scripts\monitor_sft.py --run_dir results\sft_full\phi4_30k
   ```
   - step 500以上 → eval結果（eval_loss, action_accuracy）を確認
   - accuracy ≥ 55% → Go。HU SFT続行判断 + クラウドGPU検討 + MW作業開始
   - accuracy < 55% → 実装指令書§9.2.1の対処確認
   - まだstep 500未満 → 待機。並行してContext Engine作業開始
   - 訓練完了（step 2,813） → final_metrics.json確認 + Go/No-go判定

### 11.2 並行作業（30k SFT実行中に可能）

1. **SPEC.md編集**（編集指示書に従う）
2. **実装指令書v1.3編集**（編集指示書に従う）
3. **GitHub push**（DESIGN_NOTES §53-55 + snapshot + SPEC）
4. **Context Engine実装開始**（S2-T2-NEW-a）
5. **multiway_decisions.jsonlからMWテストスポット5-10件を手動選択**

### 11.3 30k SFT完了後のHU SFT続行コマンド

```powershell
python scripts/run_sft_comparison.py --model_name phi4 --model_path models/phi-4-mini-instruct --train_data data/sft_train_full.jsonl --eval_data data/sft_eval_1k.jsonl --output_dir results/sft_full/phi4_60k --resume_from results/sft_full/phi4_30k/final_adapter --data_offset 30000 --epochs 3 --batch_size 4 --gradient_accumulation_steps 8 --lr 1e-4 --max_seq_len 1024 --eval_steps 500 --save_steps 500 --train_limit 30000
```

### 11.4 途中停止からの再開コマンド例

step 500のcheckpointから再開、30k目標の残り:
```powershell
python scripts/run_sft_comparison.py --model_name phi4 --model_path models/phi-4-mini-instruct --train_data data/sft_train_full.jsonl --eval_data data/sft_eval_1k.jsonl --output_dir results/sft_full/phi4_30k_resumed --resume_from results/sft_full/phi4_30k/checkpoint-500 --data_offset 16000 --epochs 3 --batch_size 4 --gradient_accumulation_steps 8 --lr 1e-4 --max_seq_len 1024 --eval_steps 500 --save_steps 500 --train_limit 14000
```

### 11.5 次セッションの持ち物

| # | ドキュメント | 用途 | 状態 |
|---|---|---|---|
| 1 | **本snapshot.md** | 現状認識の基盤 | 本ファイル |
| 2 | **SPEC.md** | システム全体の公式仕様 | v3.5、§9.4のMW方針が旧式 → 編集指示書で更新 |
| 3 | **DESIGN_NOTES.md** | 設計判断の根拠 | §53-55追記済み（ローカル） |
| 4 | **実装指令書v1.2** | 訓練・推論の詳細仕様 | v1.3編集指示書で更新 |
| 5 | **PokerSkill論文の概要**（オプション） | Context Engine設計の参照 | DESIGN_NOTES §54に要約済み |
````
