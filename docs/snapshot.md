# poker-assistant snapshot
**Updated:** 2026-06-04 23:30 JST
**Session:** Sprint 2 進行中 — 10k区切り自動連続SFT実行中 + MW Context Engine仕様確定

---

## 0. このsnapshotの位置づけ

このsnapshotは、次セッションでポーカーAIアシスタント開発を再開するための現在地点メモである。
体系的な仕様は SPEC.md v3.7、設計判断の理由は DESIGN_NOTES.md（§59まで）を参照。

リポジトリ: https://github.com/sanhyokim/poker-assistant

**重要: sanhyokim2050 ではない。毎回この正しいURLを使うこと。**

**ドキュメント構成（3ファイル体制）:**
- snapshot.md — 現在地点（本ファイル）
- SPEC.md v3.7 — システム仕様（MW Context Engine仕様 §9.4.7〜§9.4.18を含む）
- DESIGN_NOTES.md（§59まで） — 設計判断理由 + 撤退基準§56 + GRPO仕様§57 + 評価フレームワーク§58 + Sprint計画§59

**実装指令書（`docs/PokerRL+GRPO 6-max NLHE.md`）は廃止済み。次セッションで渡す必要なし。**

**現在地: Sprint 2（Phase 1 SFT本訓練 + MW Context Engine仕様確定）。10,000件区切り自動連続SFTが実行中。seg_000_offset_26000がstep 170/939まで進行中（2026-06-04 18:33時点）。MW Context Engine実装仕様はSPEC.md §9.4.7〜§9.4.18に確定済み。コード実装は未開始。**

---

## 1. 現在地点

### 1.1 最新テスト結果

```text
pytest -q
1441 passed, 0 failed, 7 warnings
（注: SFT訓練中はGPU競合でpytestがタイムアウトする。訓練完了後に再確認すること）
```

### 1.2 GitHub push状況

最新commit:
```text
0cb7c31 docs: 実装指令書廃止、撤退基準・GRPO仕様・評価フレームワーク・Sprint計画をDESIGN_NOTES §56-59へ移動
0b3fefc docs: SPEC §9.4.10/§9.4.13/§9.4.6 修正 - combo bonus論文値、MW初期値注記、参照先更新
90333aa docs: SPEC v3.7 - §9.4 MW Context Engine完全実装仕様を追記
0c2239b docs: SPEC v3.6, 実装指令書v1.3, DESIGN_NOTES §53-55, snapshot更新
```

push待ち:
```text
- snapshot.md（本ファイルで置き換え）
```

### 1.3 Sprint 2 進捗

#### HU SFT タスク

| タスク | 状態 | 備考 |
|---|---|---|
| S2-T1a: prepare_sft_full.py作成 | ✅ 完了 | postflop 500k + preflop 63.2k = 563,200件 |
| S2-T1b: run_sft_comparison.py改修 | ✅ 完了 | resume_from, data_offset, keep_checkpoints追加 |
| S2-T1c-1: 30k SFT checkpoint-500 | ✅ 完了 | accuracy 65.0%, eval_loss 0.378。Go判定 |
| S2-T1c-2: 10k区切り自動連続SFT | 🔄 実行中 | seg_000_offset_16000完了(69.1%)。seg_000_offset_26000 step 170/939 |
| S2-T1d: 区切りごとのeval確認 | 🔄 自動実行 | min_accuracy 40%で異常検知 |
| S2-T1e: フルデータ完走 | ⬜ 未開始 | 563,200件を10k区切りで自動消化中 |

#### MW Context Engine タスク

| タスク | 状態 | 備考 |
|---|---|---|
| S2-T2-NEW-0: Context Engine仕様策定 | ✅ 完了 | SPEC.md §9.4.7〜§9.4.18に確定 |
| S2-T2-NEW-a: Context Engine実装 | ⬜ 未開始 | board texture、23 hand class、ATT/DEF計算。決定論的Python |
| S2-T2-NEW-b: 構造化プロンプト生成 | ⬜ 未開始 | Context Engine出力→LLMプロンプト変換 |
| S2-T2-NEW-c: Phase 0（5件パイプライン確認） | ⬜ 未開始 | MWスポット抽出→Context Engine→プロンプト→LLM→出力確認 |
| S2-T2-NEW-d: Phase 1（50件定性評価） | ⬜ 未開始 | GPT-5.4-mini vs Phi-4-mini出力比較 |
| S2-T2-NEW-e: Phase 2（500件定量評価） | ⬜ 未開始 | action accuracy、分布偏り、傾向分析 |

#### ドキュメントタスク

| タスク | 状態 | 備考 |
|---|---|---|
| SPEC.md v3.7 §9.4 MW仕様追記 | ✅ 完了 | §9.4.7〜§9.4.18（900行追加） |
| SPEC.md §9.4.10/§9.4.13修正 | ✅ 完了 | combo bonus論文値、MW初期値注記 |
| 実装指令書廃止 | ✅ 完了 | DESIGN_NOTES §56-59へ有用情報移動 |
| snapshot.md更新 | ⬜ 本ファイル | |

#### 残りタスク（変更なし）

| タスク | 状態 | 備考 |
|---|---|---|
| S2-T3: 補助ヘッド（Action/Sizing）訓練 | ⬜ 未開始 | |
| S2-T4: 量子化 | ⬜ 未開始 | |
| S2-T5: 最終Go/No-go | ⬜ 未開始 | |

### 1.4 10k区切り自動連続SFT方式

フルデータ563,200件を10,000件区切りで自動連続実行する方式。

| 項目 | 値 |
|---|---|
| 区切りサイズ | 10,000件 |
| 1区切りのstep数 | 939 steps |
| 1区切りの所要時間 | 約16-18時間（60-70秒/step） |
| save_steps | 300 |
| eval_steps | 300 |
| keep_checkpoints | 3 |
| 異常停止条件 | return code≠0、loss NaN、loss>5.0、accuracy<40%、accuracy 10pt以上低下 |
| 全区切り数 | 最大57区切り |
| 全区切り完走時の推定所要日数 | 約37日 |

**実行履歴:**
```text
seg_000_offset_16000（完了）:
  データ: 16,001件目〜26,000件目
  accuracy推移: 61.8% → 68.5% → 69.1% → 69.1%（step 300→600→900→939）
  最終accuracy: 69.1%、eval_loss: 0.296

seg_000_offset_26000（実行中、2026-06-04 18:33時点）:
  データ: 26,001件目〜36,000件目
  step: 170/939、70.9秒/step
  train/loss: 1.337
  完了予定: 2026-06-05 09:30頃
  完了後自動で次区切りへ進む
```

**起動コマンド（現在実行中のもの）:**
```powershell
.venv\Scripts\python.exe scripts\run_sft_sequential.py --start_offset 26000 --initial_resume_from results/sft_sequential/seg_000_offset_16000/final_adapter
```

実行環境: PowerShellから直接起動。ブラウザ・エディタ・Wallpaper Engine停止済み。

**異常停止からの再開コマンド例:**
```powershell
.venv\Scripts\python.exe scripts\run_sft_sequential.py --start_offset {次のoffset} --initial_resume_from results/sft_sequential/seg_NNN_offset_XXXXX/final_adapter
```

sequential_run_log.jsonlの内容:
  1件目: return_code=2 — model_name問題（修正済み）
  2件目: return_code=1 — 636MBファイル直接使用で失敗
  3件目: return_code=0 — seg_000_offset_16000の正常完了（accuracy 69.1%）
  4件目: return_code=1 — 手動停止（taskkill）。エラーではない

### 1.5 accuracy推移

```text
10k SFT最終値（step 939）:           accuracy 65.6%, eval_loss 0.326
30k SFT step 500:                    accuracy 65.0%, eval_loss 0.378
seg_000_offset_16000最終値(step 939): accuracy 69.1%, eval_loss 0.296 ★最高値
seg_000_offset_26000:                 実行中（step 170/939）
```

### 1.6 システム全体の状態

poker-assistantの画面認識・GameState管理・Preflop Chart・HUD表示・DB/replay保存は安定動作中。

| エンジン | 状態 |
|---|---|
| Rust postflop CLI | 永久廃止確定 |
| Deep CFR | 品質不合格。Stage D完了まで残す |
| PokerRL+GRPO | Sprint 2実行中。10k区切り自動連続SFT実行中 |
| MW Context Engine | 仕様確定（SPEC §9.4.7-§9.4.18）。コード実装未開始 |

### 1.7 ドキュメント状態

| ファイル | バージョン | 最新commit |
|---|---|---|
| docs/SPEC.md | v3.7 (2026-06-04) | 0b3fefc |
| docs/DESIGN_NOTES.md | §59まで | 0cb7c31 |
| docs/snapshot.md | **本ファイルで置き換え** | |
| docs/PokerRL+GRPO 6-max NLHE.md | **廃止** | 0cb7c31 |

---

## 2. 本セッションで完了したこと

### 2.1 MW Context Engine仕様の完全策定

PokerSkill論文（arXiv 2605.30094）のAppendix Eから全ATT/DEF budget値、
pressure weight table、board modifier、hand class分類を抽出し、
MW拡張用の修正子を設計してSPEC.md §9.4.7〜§9.4.18に記載した。

追記内容:
- §9.4.7 Board Texture分類（suit/rank/texture/special board）
- §9.4.8 Hand Class分類（Made 15クラス + Draw 8クラス + Combo rule）
- §9.4.9 ATT/DEF Budget Table Made-Hand（全15クラスの全数値）
- §9.4.10 ATT/DEF Budget Table Draw（全8クラスの全threshold + combo bonus）
- §9.4.11 Special Board Override Table
- §9.4.12 Pressure Weight Table（17段階 + MW raise_count_bonus）
- §9.4.13 MW修正子（opponent数、position、pot type、wet MW補正。全て初期値）
- §9.4.14 Budget計算順序（10ステップ）
- §9.4.15 Viable Action Logic
- §9.4.16 Prompt Format
- §9.4.17 Output Validation
- §9.4.18 テスト要件

### 2.2 ドキュメント体制の3ファイル化

実装指令書v1.3を廃止し、残存有用情報をDESIGN_NOTESへ移動:
- §56: 撤退基準と段階的対処
- §57: Phase 2 GRPO訓練仕様（未実施）
- §58: 評価フレームワーク（Spot Checks、Entropy等）
- §59: 未実施Sprint計画骨格（Sprint 3-6）

次セッションで渡すファイル: snapshot.md + SPEC.md + DESIGN_NOTES.md の3つのみ。

### 2.3 SFT進捗確認

seg_000_offset_26000がstep 170/939で正常進行中（70.9秒/step、train/loss 1.337）を確認。
放置でOK。完了後は自動で次区切りへ進む。

### 2.4 PokerSkill論文の調査

- GitHub公開コード（lbn187/PokerSkill）の構造確認: コアロジックはCython .soで非公開
- 論文本文§3.1-3.5のフレームワーク設計を完全に把握
- Appendix Eの全budget table、pressure weight table、hand class定義を抽出
- Section Iのプロンプトトレースから実際のプロンプト構造を把握
- ライセンス: CC BY-NC 4.0（非商用）。直接コード使用不可だが設計思想の参考は問題なし

---

## 3. 確定した制約（次セッション以降も有効）

### 3.1 永久廃止
- Rust postflop CLI（Solver）は永久廃止

### 3.2 品質不合格（保持）
- Deep CFRモデルは品質不合格。Stage D完了まで残す

### 3.3 評価基準
- 「profit vs random」は単独評価指標として使用禁止
- Spot Checks 50シナリオを削除・緩和しない
- verify_pokerrl_encode.pyの検証をスキップしない（Sprint 4で実施）
- エントロピー崩壊対策なしに4B以上のモデルを訓練しない

### 3.4 削除禁止
- 既存Deep CFR/Solverコードは新エンジン統合完了（Stage D）まで削除禁止

### 3.5 ハードウェア・予算
- RTX 3080 (VRAM 10GB, RAM 32GB)
- クラウドは$500上限
- 全体タイムボックス: 12週間（最大15週間）

### 3.6 データ設計制約
- phh-dataset hole cards付き726,570件は全件敗者。positive example使用禁止
- MW教師ラベルの品質問題はContext Engineで回避（訓練不要のルール層）
- HU rehearsal: MW Context Engineテスト時も、HU SFTモデルの品質は維持する

### 3.7 訓練運用
- 10,000件区切り自動連続SFT（run_sft_sequential.py）
- save_steps=300、eval_steps=300、keep_checkpoints=3
- 異常検知で自動停止
- 全データ完走は必須ではない。accuracyが十分なら途中停止可能
- eval_dataは1,000件（5,000件はVRAM不足でハング）
- 636MBフルファイルを直接DataLoaderに渡さない

### 3.8 ドキュメント体制
- 3ファイル体制: snapshot.md + SPEC.md + DESIGN_NOTES.md
- 実装指令書は廃止済み。渡す必要なし
- 撤退基準はDESIGN_NOTES §56に記載

---

## 4. 主要コードファイル

```text
[poker-assistantリポジトリ]
strategy/pokerrl_bridge.py          骨格のみ
strategy/pokerrl_prompt_builder.py  骨格のみ（Context Engine実装先）
strategy/pokerrl_inference_engine.py 骨格のみ
strategy/pokerrl_heads.py           骨格のみ
strategy/pokerrl_output_parser.py   骨格のみ
strategy/pokerrl_spot_classifier.py 骨格のみ

[訓練リポジトリ C:\dev\pokerrl-training]
scripts/prepare_sft_full.py         フルデータ準備完了
scripts/run_sft_comparison.py       改修完了（resume_from, data_offset, keep_checkpoints）
scripts/run_sft_sequential.py       10k区切り自動連続実行
scripts/extract_multiway.py         PHH→multiway抽出済み
scripts/monitor_sft.py              時刻・速度・ETA表示
```

---

## 5. 訓練リポジトリの状態

```text
C:\dev\pokerrl-training/
├── data/
│   ├── pokerbench/                 PokerBench 8ファイル
│   ├── phh-dataset/                PHH 10,000 .phhファイル
│   ├── multiway_raw/               phh-dataset抽出結果（16.23 GB）
│   ├── sft_train_full.jsonl        563,200件（636 MB、直接使わない）
│   ├── sft_eval_1k.jsonl           1,000件（全SFTで共用）
│   └── sft_train_10k.jsonl         10k SFT訓練データ
├── models/phi-4-mini-instruct/     Phi-4-mini FP16
├── results/
│   ├── sft_sequential/             自動連続SFT結果
│   │   ├── sequential_run_log.jsonl
│   │   ├── seg_000_offset_16000/   完了（accuracy 69.1%）
│   │   └── seg_000_offset_26000/   実行中
│   └── sft_full/phi4_30k/checkpoint-500/
├── scripts/                        上記参照
└── .venv/                          torch 2.12.0+cu130
```

---

## 6. 未解決の課題・TODO

| 課題 | 対処タイミング | 備考 |
|---|---|---|
| **seg_000_offset_26000以降の進捗確認** | 随時 | 自動進行中。monitor_sft.pyで確認 |
| **Context Engine実装（S2-T2-NEW-a）** | 次セッション | SPEC §9.4.7-§9.4.18の仕様に基づく。SFT並行可能 |
| **Phase 0テスト用MWスポット5件選定** | Context Engine実装前 | multiway_decisions.jsonlから手動選択 |
| monitor_sft.py total_steps引数対応 | 低優先度 | 現在2813固定、実際は939 |
| snapshot.md GitHub push | 次セッション冒頭 | |

---

## 7. 次セッションの開始手順

### 7.1 最初に確認すること

1. 本snapshot.mdの内容を確認
2. **自動連続SFTの進捗確認**
   ```powershell
   cd C:\dev\pokerrl-training
   dir results\sft_sequential\
   type results\sft_sequential\sequential_run_log.jsonl
   nvidia-smi
   ```
   python.exeプロセスがあれば実行中。なければ停止している。
3. 最新segフォルダのmonitor:
   ```powershell
   .venv\Scripts\python.exe scripts\monitor_sft.py --run_dir results\sft_sequential\{最新segフォルダ}
   ```

### 7.2 正常に進行中の場合

- 放置でOK。自動で次の区切りに進む
- sequential_run_log.jsonlでaccuracy推移を確認
- accuracyが十分（目安: 65%以上で安定）なら停止を検討
- **Context Engine実装（S2-T2-NEW-a）を並行開始**

### 7.3 異常停止していた場合

- sequential_run_log.jsonlの最後のエントリでreturn_codeと異常理由を確認
- 最後に成功した区切りのfinal_adapterから再開:
  ```powershell
  .venv\Scripts\python.exe scripts\run_sft_sequential.py --start_offset {次のoffset} --initial_resume_from results\sft_sequential\seg_NNN_offset_XXXXX\final_adapter
  ```

### 7.4 次セッションの作業優先順

1. SFT進捗確認（5分）
2. Context Engine実装開始（S2-T2-NEW-a）— SPEC §9.4.7〜§9.4.18に基づく
3. Phase 0テスト用MWスポット選定（5件）

### 7.5 次セッションの持ち物

| # | ドキュメント | 用途 |
|---|---|---|
| 1 | **本snapshot.md** | 現状認識の基盤 |
| 2 | **SPEC.md v3.7** | システム仕様（MW Context Engine §9.4.7-§9.4.18含む） |
| 3 | **DESIGN_NOTES.md** | 設計判断理由（撤退基準§56含む） |

**実装指令書は渡す必要なし（廃止済み）。**
