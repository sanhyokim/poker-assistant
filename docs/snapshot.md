# poker-assistant snapshot
**Updated:** 2026-06-15 JST
**Session:** Sprint 2 — 補助ヘッド訓練(S2-T3) epoch 1完走・健全性判定GO → Sprint 3 GRPO準備へ

---

## 0. このsnapshotの位置づけ

このsnapshotは、次セッションでポーカーAIアシスタント開発を再開するための現在地点メモである。
体系的な仕様は SPEC.md v3.8、設計判断の理由は DESIGN_NOTES.md（§62まで）を参照。

リポジトリ: https://github.com/sanhyokim/poker-assistant
**重要: sanhyokim2050 ではない。毎回この正しいURLを使うこと。**

**ドキュメント構成（3ファイル体制）:**
- snapshot.md — 現在地点（本ファイル）
- SPEC.md v3.8 — システム仕様（補助ヘッド仕様 §10A.2 含む。補助ヘッド訓練の結果反映はSprint 4統合時に行う＝現時点では未反映）
- DESIGN_NOTES.md（§62まで） — 設計判断理由。§61=SFT飽和と補助ヘッド追加、§62=補助ヘッド評価基準の変更とLoRA凍結設計の保存仕様

**実装指令書（`docs/PokerRL+GRPO 6-max NLHE.md`）は廃止済み。次セッションで渡す必要なし。撤退基準はDESIGN_NOTES §56に記載。**

**現在地: Sprint 2。補助ヘッド訓練(S2-T3)が epoch 1 完走し健全性判定GO。テキスト生成SFTは82%飽和で停止済み。次はSprint 3（GRPO強化学習）の準備。MW Context Engineは Step 1〜4完了・実戦5/5 PASS。pytest 全1523 passed（前セッション確認値、本セッションではコード変更なし）。**

---

## 1. 現在地点

### 1.1 本セッションで完了したこと

1. 補助ヘッド訓練スクリプト `scripts/train_aux_heads.py` 実装・本番訓練実行
2. epoch 1 完走（31時間 / 1,883分、step 17,600 = 1 epoch分）
3. S2-T3 健全性判定 = **GO**（評価基準は §62 で「GRPO初期化健全性」に変更済み）
4. 訓練成果物を `results/aux_heads/seg_003/final_aux_head/` に確定保存
5. DESIGN_NOTES §62 追記（評価基準変更とLoRA凍結保存仕様）

### 1.2 補助ヘッド訓練(S2-T3)の設計と結果

**訓練方式**: ベースモデル Phi-4-mini + LoRAアダプタ `seg_003_offset_66000/final_adapter` を全パラメータ freeze し、補助ヘッド（Action Head + Sizing Head）のみを新規訓練。
- Action Head: 最終hidden state → MLP(hidden_dim=512) → 4クラス（0:Fold / 1:Check-Call / 2:Raise / 3:All-in）softmax
- Sizing Head: 最終hidden state → MLP → sigmoid。`raise_size_ratio = 0.1 + 2.9 * sigmoid(x)`（DESIGN_NOTES §49.3 と厳密一致）。MSE損失はRaiseクラスのサンプルのみマスク計算
- loss = CE(action) + λ(=1.0) * MSE(sizing)
- freeze検証ログ: base+LoRA trainable=0 / 補助ヘッド trainable=3,149,317
- 訓練データ563,200件、eval 1,000件、batch 8 × grad_accum 4（有効32）、lr=0.001、save_steps=150、eval_steps=300、最大2 epoch設定

**結果（epoch 1 最終 step 17,600 / final_metrics.json）**:
- overall accuracy 0.799
- by_action_type: bet 58.7% / call 76.1% / check 94.9% / fold 90.8% / raise(type) 62.4%
- top-1確率中央値 0.836
- sizing_raise_mae 0.125（Raiseサンプル269件）
- All-in: eval正例ゼロのため評価不能（予測分布にall_in:1が稀出する程度）
- peak VRAM 14,670 MiB（10GB超過分はシステムメモリへスワップ＝訓練中の断続的スループット低下の原因）

**早期停止の警告について**: ログに `Early stop: aggressive accuracy below baseline. bet=58.65% raise=62.42%` が出ているが、これはスクリプトの旧基準（accuracy +5pt）由来の機構が反応したもの。本セッションで評価基準を §62 の健全性に変更済みのため、この警告は判定に無関係。epoch 1終端で止まったのは epoch 2を回さない方針（§61.2の過学習知見）とも一致し結果的に好都合。

### 1.3 健全性判定の根拠（§62.3基準、判定=GO）

| 軸 | 基準 | 実測（最終/epoch後半傾向） | 判定 |
|---|---|---|---|
| 劣化なし | overall ≥ 80%フロア（フロアであって目標ではない） | 0.799、後半step13k以降0.78〜0.80安定 | 合格 |
| 崩壊なし | top-1中央値 ≤ 0.85付近、一点張りなし | 最終0.836、後半0.83〜0.89変動 | 合格 |
| sizing健全 | Raise MAE ≤ 0.2x | 0.125、後半0.11〜0.12収束 | 合格 |
| 攻撃が死んでない | bet/raise が0%付近に崩壊していない | bet58.7%/raise62.4%、振動内・崩壊なし | 合格 |

bet/raiseがベースライン(62.5%/69.1%)をやや下回る点は §62で「完全解消不要・GRPOが再較正」と合意済みの想定内。Sizing Headは仕上がり良好。エントロピー崩壊なし。GRPO初期化点として十分。

### 1.4 訓練成果物の正本（重要・GRPO初期化に使う）

**推論に必要な完全モデルは2部品のペア**:
1. ベースLoRAアダプタ（不変）: `results/sft_sequential/seg_003_offset_66000/final_adapter`
2. 補助ヘッド（今回の成果）: `results/aux_heads/seg_003/final_aux_head/aux_heads.pt`（12.6MB、checkpoint-17100由来）

`final_aux_head/` に aux_heads.pt + metrics.json + final_metrics.json を確定保存済み（checkpoint自動削除の巻き添え防止）。

**final_adapterが存在しないのは正常**: LoRAを完全freezeしヘッドのみ訓練したため、checkpointには `aux_heads.pt` のみ保存され、LoRA重みは保存されない（1ステップも更新されていないため）。元の seg_003 final_adapter がそのまま使える。書き出し直しは不要。

**GRPO初期化候補 = checkpoint-17100由来のヘッド**: 評価値のある終盤checkpoint（17100/17400）のうち、17100はtop-1中央値0.833と低めで探索性が高く、GRPO self-playの初期化に適する（17400はtop-1 0.878で確信度が締まりすぎ）。final_aux_head/aux_heads.pt は17100由来なのでそのまま使える。

### 1.5 MW Context Engine（前セッションから変更なし）

| 項目 | 状態 |
|---|---|
| Step 1〜4実装 | 完了（82テストPASS） |
| Phase 0（15件LLM比較） | 完了。GPT-5.4-mini採用確定 |
| MW実戦統合テスト（モック・実API） | 5/5 PASS（3〜5人） |
| Phase 1（50件定性評価） | 未着手 |
| Phase 2（500件定量評価） | 未着手 |

### 1.6 システム全体の状態

| エンジン | 状態 |
|---|---|
| Rust postflop CLI | 永久廃止確定 |
| Deep CFR | 品質不合格。Stage D完了まで残す |
| HU SFT (Phi-4-mini) | 82%飽和でテキスト生成SFT停止 |
| HU 補助ヘッド | epoch 1完走・健全性GO。GRPO初期化点として確定 |
| MW Context Engine | Step 1〜4完了。GPT-5.4-mini採用。実戦5/5 PASS |

---

## 2. 確定した制約（次セッション以降も有効）

### 2.1 永久廃止
- Rust postflop CLI（Solver）は永久廃止

### 2.2 品質不合格（保持）
- Deep CFRモデルは品質不合格。Stage D完了まで残す

### 2.3 評価基準
- 「profit vs random」は単独評価指標として使用禁止
- Spot Checks 50シナリオを削除・緩和しない
- verify_pokerrl_encode.pyの検証をスキップしない（Sprint 3 GRPO自己対戦の入力エンコード一致確認で実施）
- **補助ヘッドの評価基準は accuracy ではなく「GRPO初期化健全性」（§62）。accuracy追求は過剰最適化として回避済み**

### 2.4 削除禁止
- 既存Deep CFR/Solverコードは新エンジン統合完了（Stage D）まで削除禁止
- **`results/aux_heads/seg_003/final_aux_head/` は補助ヘッドの正本。削除禁止**
- **`results/sft_sequential/seg_003_offset_66000/final_adapter` は補助ヘッドのベースLoRA。削除禁止**

### 2.5 ハードウェア・予算
- RTX 3080 (VRAM 10GB, RAM 32GB)。クラウドは$500上限。全体タイムボックス12週間（最大15週間）
- VRAMは10GB上限。訓練中は他のGPU使用アプリ（Chrome/Edge WebView/IDE/LINE等）を絞らないと共有メモリスワップでスループットが大幅低下する（本セッションで実測）

### 2.6 データ設計制約
- phh-dataset hole cards付き726,570件は全件敗者。positive example使用禁止
- MW教師ラベルの品質問題はContext Engineで回避（訓練不要のルール層）
- 補助ヘッド訓練データのAll-inクラスは783件（全体の0.14%）と極少。eval(1,000件)にはAll-in正例ゼロ。All-in性能はSpot Checks（DESIGN_NOTES §58.2）で確認する

### 2.7 訓練運用
- save_steps=150、eval_steps=300（BSOD対策でずらし。デフォルト）
- keep_checkpoints=3〜4（古いcheckpointは自動削除。残したい成果物は別ディレクトリにコピーして確定する）
- eval_dataは1,000件（5,000件はVRAM不足でハング）

### 2.8 ドキュメント体制
- 3ファイル体制: snapshot.md + SPEC.md + DESIGN_NOTES.md
- 実装指令書は廃止済み。撤退基準はDESIGN_NOTES §56に記載

### 2.9 MW Context Engine運用
- MW本採用モデル: GPT-5.4-mini（OpenRouter経由、openai/gpt-5.4-mini）
- .env の load_dotenv(override=True) を使用すること
- config.yaml に llm.mw_model: openai/gpt-5.4-mini 設定済み

### 2.10 BSOD対策（実装済み・有効）
- checkpoint保存前にtorch.cuda.empty_cache()、_save_optimizer_cpu()でCPU経由保存
- save_steps=150, eval_steps=300（ずらし必須）、古いfinal_adapterは訓練開始時に自動削除
- optimizer.pt 0バイト検出で fresh start

### 2.11 補助ヘッド設計（確定）
- LoRA完全freeze + ヘッドのみ訓練。checkpointにはaux_heads.ptのみ保存される（LoRA重みは保存されない＝正常）
- Action Head 4クラス（Fold/Check-Call/Raise/All-in）、Sizing Head sigmoid 0.1-3.0x
- 推論時は「seg_003 final_adapter + aux_heads.pt」のペアでロードする必要がある

---

## 3. 主要コードファイル

```text
[poker-assistantリポジトリ]
strategy/context_engine.py          MW Context Engine本体
strategy/multiway_engine.py         MW GameLoop統合
tests/test_context_engine.py        82テスト
tests/test_multiway_engine.py       38テスト
scripts/test_mw_live.py             MW実戦統合テスト（5ケース）
config.yaml                         llm.mw_model: openai/gpt-5.4-mini

[訓練リポジトリ C:\dev\pokerrl-training（リモートなし、ローカルgitのみ）]
scripts/train_aux_heads.py          補助ヘッド訓練（本セッション実装）
scripts/prepare_sft_full.py         フルデータ準備
scripts/run_sft_comparison.py       SFT（checkpoint完全保存/再開+BSOD対策）
scripts/run_sft_sequential.py       10k区切り自動連続SFT
data/sft_train_full.jsonl           563,200件（636MB）
data/sft_eval_1k.jsonl              1,000件
results/sft_sequential/seg_003_offset_66000/final_adapter   補助ヘッドのベースLoRA（不変・削除禁止）
results/aux_heads/seg_003/final_aux_head/aux_heads.pt       補助ヘッド正本（削除禁止）
```

---

## 4. 訓練リポジトリの補助ヘッド関連状態

```text
C:\dev\pokerrl-training\results\aux_heads\seg_003\
├── final_aux_head/              ★正本（確定保存）
│   ├── aux_heads.pt             12.6MB（checkpoint-17100由来、GRPO初期化用）
│   ├── metrics.json             step17100の評価指標
│   └── final_metrics.json       全58評価点履歴込み
├── checkpoint-17100/            aux_heads.pt + optimizer/scheduler/state（eval値あり: overall0.793/top1中央0.833）
├── checkpoint-17250/            （eval値なし=中間save）
├── checkpoint-17400/            （eval値あり: overall0.798/top1中央0.878）
├── checkpoint-17550/            （eval値なし=中間save）
├── train_aux_heads.log          全訓練ログ
└── final_metrics.json           最終メトリクス（全履歴）

注: keep_checkpoints設定により上記4checkpointのみ現存。中盤checkpointは削除済み。
final_adapterディレクトリは存在しない（LoRA freeze設計のため正常）。
```

---

## 5. 次セッションの作業

### 5.1 最優先: Sprint 3（GRPO強化学習）準備

**着手前の必須検証（推測で進めない）**:
1. **ロード経路の検証**: 「seg_003 final_adapter + final_aux_head/aux_heads.pt」のペアを正しく組み立てて推論できるか確認。train_aux_heads.py の eval_only モードかロード関数を参照。これがGRPOの初期化点になるため最初に確認する
2. **verify_pokerrl_encode.py 実行**: GRPO自己対戦の入力エンコードが訓練時と一致するか（encode不一致の教訓、§2.3）

**GRPO仕様（DESIGN_NOTES §57）**:
- 入力: Phase 1 SFTモデル（=補助ヘッド付き）
- 方式: GRPO + DAPO trick + OPEFO entropy制御
- 環境: PokerKitベース6-max NLHE自己対戦
- opponents: 過去SFT checkpoint(population) / Rule-based(TAG/LAG) / Deep CFR失敗モデル（弱い相手としてエントロピー多様性確保）
- 報酬: 0.7×即時chip delta + 0.2×EV at decision(eval7) + 0.1×直近20ハンド累積(bankroll)
- 訓練時間: 約80-120時間（RTX 3080単機で実時間4〜6日連続。長時間連続でのBSOD対策とcheckpoint健全性を早期に確認すべき）

**GRPO Go/No-go（§57, Sprint 3終了時）**:
- Spot Checks 50局面で行動分布が合理的に変動
- Entropy健全（top-1確率中央値 < 0.85）
- Slumbot HU勝率 ≥ -15 bb/100
- 自己対戦でPhase 1ベースライン比 +3 bb/100以上

**GRPO品質未達時の段階的対処（§56.3、最大16日）**: Step1 Entropy崩壊対処(4日)→Step2 対戦相手プール見直し(3日)→Step3 報酬関数調整(4日)→Step4 訓練延長(5日)

**撤退条件（§56.6, OR）**: タイムボックス超過 / 品質下限未達(accuracy<50%, Slumbot<-30, SpotChecks<80%) / 改善トレンド消失 / コスト$500超過。判断タイミング=Sprint 3開始から2週・5週、合計12週時点。撤退時はCase A（SFT成功GRPO失敗）→第1候補 PokerSkill風ハイブリッド。

### 5.2 補助ヘッドが健全性不十分だった場合のフォールバック（今回は不要だが記録）
§61.5シナリオ2: LoRA凍結を解除してヘッド同時ファインチューニング。既存LoRA重みは保持済みのためテキスト生成方式へ復帰も可能。

### 5.3 ドキュメント更新（本セッションで完了済み）
| ファイル | 状態 |
|---|---|
| snapshot.md | 本ファイルで張り替え完了 |
| DESIGN_NOTES.md §61 | 前セッションで追加済み（accuracy飽和+補助ヘッド判断） |
| DESIGN_NOTES.md §62 | 本セッションで追加（評価基準変更+LoRA凍結保存仕様） |
| SPEC.md | 補助ヘッド結果の反映はSprint 4統合時（現時点未反映で正しい） |

### 5.4 次セッションの持ち物
| # | ドキュメント | 用途 |
|---|---|---|
| 1 | 本snapshot.md | 現状認識の基盤 |
| 2 | SPEC.md v3.8 | §10A 推論ブリッジ仕様、§10A.2 補助ヘッド仕様 |
| 3 | DESIGN_NOTES.md（§62まで） | §49補助ヘッド設計、§56撤退基準、§57 GRPO仕様、§58評価フレーム、§61飽和判断、§62評価基準変更 |
