# pokerrl-training snapshot
**Updated:** 2026-06-17 JST
**Session:** Sprint 3 — opponent結線(6b) + 統合GPU健全性(6b-v) + Spot Checks v0構築 + SFT初期方策の裁定(B)。entropy「崩壊」はガード誤検知と判明。次はガード再設計 → Spot Checks 50完成 → Task 6c本訓練。

---

## 1. このsnapshotの位置づけ

`C:\dev\pokerrl-training`（ローカルのみ・リモートなし、branch `master`）でSprint 3を引き継ぐ現在地点メモ。次セッションはこのファイル単体で **ガード再設計のコード** に着手できる状態を目標にする。

docs正規パス: `C:\Users\user\Desktop\dev\poker-system\docs`（branch `main`。**docsを訓練リポジトリに置かない**＝確定制約#13）。poker-system本体仕様は `SPEC.md v3.8`、設計判断は `DESIGN_NOTES.md`。

現在地:
- Sprint 3 Task 1–5c 完了 = GRPO装置（収集→損失→監視/崩壊ガード）。Task 6a = 訓練ハーネス。**6b = opponent実ローダ、6b-v = 統合GPU健全性、6c-prep = Spot Checks v0** 完了。
- **重要発見**: 実モデル統合smokeでentropyが2ステップで0.95へ→ガードHALT。調査の結果、これは**RL崩壊ではなくガード誤検知**。SFT初期方策は無学習時点で既に高確信（top1中央値≈0.91）でtight。Spot Checks v0で裁定し**(B) 健全なタイト確信**と確定（全面退化ではない）。ただし**実欠陥3点**あり（後述）。
- 設計記録: §66/§67/§68/§69/§70 確定済み。
- 次: **ガード再設計**（§70.3、`collapse_guard`意味論変更 + Spot Checks品質ゲート + leak監視）→ **Spot Checks 50完成**（残り30）→ **Task 6c 本訓練**（all-in探索温度つき、Spot Checks/leakをゲート）。

poker-assistant本体URL（参照用）: https://github.com/sanhyokim/poker-assistant

---

## 2. 本セッションで完了したこと（コミット付き）

### 設計記録（docs `main`、コミット `2d50e07`）
- **§69**: Task 6 opponent pool構成。SFT aux-head opponentに使える正本ペアは `seg_003`のみ（14 adapter中aux head保有はseg_003だけ）。初期pool = self / canonical SFT / optional experimental / TAG / LAG。Deep CFR opponentは pokers/GameState encoding差のため分離（6d想定）。Deep CFR削除禁止。
- **§70**: SFT初期方策の裁定(B) + ガード再設計方針 + 係数sweep解釈更新 + 標的leak（下記）。

### 6b: SFT/AuxHead opponent 実ローダ（訓練 `master`、コミット `99900f3`）
- `pokerrl_grpo/sft_opponent.py`（`SFTAuxHeadOpponent`、凍結ベース共有 + PEFT multi-adapter + 凍結aux head、no_grad推論）
- `pokerrl_grpo/opponent_pool_factory.py`（`build_initial_pool`、§69構成。Deep CFRはStub維持）
- `tests/test_sft_opponent.py` / `tests/test_opponent_pool_factory.py` / `scripts/smoke_sft_opponent.py`
- GPU実機: `--with-model` で `cuda_max_memory_allocated_mb=3120.1`、`full_base_duplicate_load=False`、`decisions_ok=True`。凍結ベース共有が成立。

### 6b-v: 統合GPU健全性smoke（訓練 `master`、コミット `564cd96`）
- `pokerrl_grpo/integrated_step.py`（collect→groups→loss+sizing→backward→opt.step→monitor/guard）
- `scripts/smoke_train_integrated.py`（既定stub + `--with-model` + `--measure-only` + entropy制御係数/lr/group_sizeのCLI override）
- `tests/test_integrated_step.py`
- GPU実機: OOMなし（統合で `cuda_max_memory_allocated_mb≈3680`）、loss/grad有限、ベース二重ロードなし。**ただしentropyが2step目で0.95→HALT**（→ガード誤検知と判明）。

### 6c-prep: Spot Checks v0（訓練 `master`、コミット `78dda67` + `764e687`）
- `pokerrl_grpo/spot_checks.py`（`SpotScenario` / `build_state_for_scenario` / `evaluate_expected`〔majority/min/max〕/ `run_spot_checks`）
- `data/spot_checks/scenarios_v0.json`（**20局面**。`data/`がgitignoreのため `git add -f` で追跡＝`764e687`。50完成は後続）
- `scripts/run_spot_checks.py`（既定stub + `--with-model`）/ `tests/test_spot_checks.py`
- カテゴリ: position_sensitivity / preflop_vs_raise / overcard_no_pair / made_hand / draw / turn_barrel / river / all_in。

---

## 3. SFT初期方策の裁定（Spot Checks v0、§70）

**結論: (B) 健全なタイト確信。全面退化(A)ではない。**

Spot Checks v0（無学習、canonical `seg_003_offset_66000` + `seg_003/final_aux_head`）= **16/20 passed（0.80）**。

| category | passed/total |
|---|---|
| overcard_no_pair | 2/2（Deep CFR病理を踏んでいない＝決め手） |
| preflop_vs_raise | 2/2 |
| draw | 1/1 / turn_barrel 1/1 / river 1/1 |
| made_hand | 3/4 |
| position_sensitivity | 4/5 |
| all_in | 2/4 |

注: v0の0.80は難所偏重の診断値。§58.1 Phase 1合格（80%）/Phase 2（95%）の判定はSpot Checks 50完成後。

**確認された実欠陥3点（局所leak、GRPOで矯正対象）:**
1. **all-in head死亡**: legal 162/200でall_in_prob≈0、river nutsで `all_in≈0.000005`。既知制約（§49.2、train All-in 0.14%）。
2. **made-hand fold退化**: spot_016 でストレートを `fold 0.674913`。
3. **position過剰raise**: spot_004 SB K2o `raise 0.690254`。

無学習measure-only（200決定）: top1中央値0.91、preflop fold 73%（タイト範囲）、preflop 93.5%・postflop 6.5%（postflopサンプル僅少）。position別fold率の見かけの逆転は混在状況のノイズ寄り（targetなposition_sensitivityは4/5でおおむね妥当）。

---

## 4. ガード再設計方針（§70.3、次タスク）

`top1_median ≤ 0.85` 単発HALTはポーカーに誤検知するため廃し、`collapse_guard`（`monitor.py`）を再設計する。**当該既存ガード意味論（§56.4/§68.3/§58.1）は§70を優先**。

- `top1_median` / `fold_freq` → **情報監視（WARN）に格下げ**。単発HALTの根拠にしない。
- **真の退化HALT** = 多様な局面で単一アクション支配（行動多様性の欠如）かつ/または **Spot Checks品質の回帰**。
- **Spot Checks（v0→50）を品質ゲートの軸**に統合（§58.1/§58.2）。
- **leak別監視を新設**: all-in使用率 / value-hand fold率 / position過剰raise。
- **top1≤0.85を最適化標的にしない**（メトリクスがポーカーに不適）。

係数sweep解釈（§70.4）: entropy_bonus(0.01/0.03/0.1)・OPEFO(0.5)・KL(0.05)では top1≤0.85維持不可だったが、主因は指標不適。§56.3 Step1のうち**係数群は試行済み**、残り（LR/group_size/generation_temperature/dynamic sampling）は未消化。

### 4.1 現行 `monitor.py`（再設計の起点。コードが正、着手前に必ず `view monitor.py` / `grpo_config.py`）
- `EntropyGuardConfig`（5c実装）の既定値: `top1_median_max=0.85` / `raise_freq_max=0.60` / `fold_freq_max=0.50` / `sizing_fixed_frac_max=0.95` / `window=200` / `warn_margin=0.03`。
- `EntropyMonitor`: 直近 `window` 決定の (probs, action, sizing_ratio) を蓄積し、top-1確率中央値・4-class頻度・sizing最頻値割合・平均entropyを集計（`metrics()`）。
- `collapse_guard(metrics, EntropyGuardConfig) -> GuardStatus`: 現行は top1_median>閾値 / raise偏重 / fold偏重 / sizing固定 のいずれかで HALT、`warn_margin`帯でWARN。`GuardStatus` は OK/WARN/HALT + 理由（`guard_reasons` タプル、6b-tで `StepMetrics` にも追加済み）。
- **再設計（§70.3）で変えるのはここ**: top1_median/fold_freqを単発HALTから外しWARN化、真の退化（行動多様性欠如）+ Spot Checks回帰でHALT、leak別監視（all-in使用率/value-hand fold率/position過剰raise）追加、Spot Checks品質ゲート統合。既存の `top1>0.85即HALT` 系テストは新意味論へ更新する。

---

## 5. Task 6cでGRPOが矯正すべきleak / watch item（§70.5）

- made-hand fold退化・SB過剰raiseは自己対戦報酬で矯正対象。Spot Checks/leak監視で改善トラッキング。
- **all-in復活は不確実**（head≈0出力でRL勾配が乗りにくい）。`generation_temperature`（§56.3 Step1）を「崩壊対策」ではなく**「all-in探索」目的に転用**し、自己対戦でall-in局面に報酬を届ける候補。watch item。
- 本訓練前に、再設計ガード + all-in探索温度を入れた統合 `--with-model` 数ステップで、HALT誤検知が消え・loss健全・VRAM内を再確認してから100–150hへ。

### 5.1 訓練必須情報（Task 6c）
- ベース初期化点: `seg_003_offset_66000`（56万ハンドSFT）+ `seg_003/final_aux_head`。学習対象=aux head（LoRA凍結、`TrainConfig.train_lora` 既定False）。
- 環境: PokerKit 6-max自己対戦。opponent=§69初期構成（self / canonical SFT / optional experimental / TAG / LAG。Deep CFRはStub、6dで結線）。
- 報酬: 0.7×chip delta + 0.2×rollout EV(eval7 MC) + 0.1×直近20bankroll、bb正規化（`RewardConfig`）。
- 訓練時間: 100–150h（§59）/ 80–120h（§57）。checkpointは `results/grpo/`。
- ハード: **RTX 3080（VRAM 10GB / RAM 32GB）**。タイムボックス **12週（最大15週）**、クラウド **$500上限**。

### 5.2 Go/No-go閾値（§58.1。Phase 2が本訓練の合格線）
| 指標 | Phase 1 | Phase 2 |
|---|---|---|
| Spot Checks（50シナリオ） | 80%合格 | 95%合格 |
| Entropy top-1中央値 | ≤0.90 | ≤0.85（※§70で単発HALT指標からは外す。品質判定はSpot Checks軸） |
| Sensitivity Tests | 70% | 90% |
| PokerBench Acc | preflop≥70%, postflop≥55% | preflop≥75%, postflop≥60% |
| Slumbot HU | N/A | ≥-15 bb/100 |
| Self-play vs Phase1 | N/A | ≥+3 bb/100 |
| Latency P95 (T1) | ≤300ms | ≤200ms |
- 「profit vs random」単独評価は禁止。

### 5.3 撤退基準（§56.6）と消化状況
- 撤退条件: タイムボックス（12週/最大15週）超過 / 品質下限未達（全Step消化後も postflop acc<50%・Slumbot<-30bb/100・Spot Checks<80%）/ 改善トレンド消失 / コスト$500超過。
- 現状: **未抵触・タイムボックス内**。§56.3 Step1は係数群（entropy/OPEFO/KL）試行済み、残り（LR/group_size/temperature/dynamic sampling）未消化。撤退判断時は §56.8テンプレで記録し snapshot+DESIGN_NOTES更新。

---

## 6. GRPO装置の主要API / 初期化点

- 初期化点（検証済、forward可）: LoRA `results/sft_sequential/seg_003_offset_66000/final_adapter` + Heads `results/aux_heads/seg_003/final_aux_head/aux_heads.pt`（**単数形パス**）。`pooled=(1,3072)/action_logits=(1,4)/raise_size_ratio=(1,)`。base+LoRA凍結（trainable=0）、aux head trainable=3,149,317。forward比較時 `heads.eval()`。
- 収集: `collect.collect_trajectories(hero_policy, pool, n_hands, rng, env=None)`。
- グループ: `grpo_batch.build_decision_groups(...)`（decision-state単位、§67）。advantage: `advantage.group_relative_advantages(...)`。
- 損失: `grpo_loss.grpo_loss(batch, policy_eval_out, config)`→`(total, metrics)`。sizingは方策勾配外（§66、detach）。
- 監視/ガード: `monitor.EntropyMonitor` / `monitor.collapse_guard(metrics, EntropyGuardConfig)`→`GuardStatus`（**§70.3で再設計予定**）。sizing: `sizing.select_sizing_targets/sizing_objective`。
- ハーネス: `train_harness.GRPOTrainer` / `TrainConfig`（train_lora既定False）/ `aux_head_policy_eval` / `checkpointing`（`results/grpo/` のみ、正本read-only拒否ガード）。
- opponent: `sft_opponent` / `opponent_pool_factory`（凍結ベース共有）。
- 統合: `integrated_step.run_integrated_steps(...)`。Spot Checks: `spot_checks.run_spot_checks(policy_eval, scenarios)` + `data/spot_checks/scenarios_v0.json`。
- 全ハイパラは `RewardConfig`/`GRPOConfig`/`EntropyGuardConfig`/`TrainConfig` 経由。

### 6.1 Config既定値（着手前に各定義を `view` で再確認。以下は記録時点）
- `RewardConfig`: weight_chip_delta=0.7 / weight_rollout_ev=0.2 / weight_bankroll=0.1 / rollout_playouts=100 / clip_bb=100.0 / bankroll_window=20。
- `GRPOConfig`: group_size=8 / eps_low=0.2 / eps_high=0.28（Clip-Higher）/ kl_coef=0.0 / entropy_bonus_coef=0.0 / opefo_balancing_coef=0.0 / opefo_balancing_max=1.0 / sizing_loss_coef=0.1 / advantage_eps=1e-8 / generation_temperature=1.0。
  ※係数sweepの知見: entropy/OPEFO/KLの非ゼロ化単独ではtop1≤0.85を維持できない（が、それは指標不適が主因＝§70.4。追いかけない）。
- `EntropyGuardConfig`: §4.1参照。
- `TrainConfig`: train_lora=False（既定、aux headのみ学習）/ lr・checkpoint_every・eval_every・max_steps（着手前に定義確認）。

### 6.2 健全性テストの目安（個別ファイル指定で実行）
- GRPO装置＋opponent＋統合＋Spot Checks＋ハーネス＋Task4系の各テストファイルが全PASS。直近の主なまとまり: 5a 8 / 5b 10 / 5c 12 / 6a 6 + 6b・6b-v・Spot Checks分 + Task4系22。`verify_pokerrl_encode` passed=8/PASS、各smoke PASS。**`pytest -q` 全体は `tools/poker_datasets_ref` のcollection errorで止まるため、必ず個別ファイル指定で判定**。

---

## 7. 技術参照

### 7.1 PokerKit / state正本
- `pokerkit==0.7.4` 死守。`tools/poker_datasets_ref` を現venvへ入れない（0.6.5降格要求）。
- state正本 `state_factory.py`（blinds 0.5/1、starting 100、player_count 6、8 automations、ante 0、min_bet 1）。複製しない。
- 6-max index `{0:SB,1:BB,2:UTG,3:HJ,4:CO,5:BTN}`。`player_count != 6`→`ValueError`。

### 7.2 prompt生成器 / 出力契約
- `pokerbench_prompt.build_pokerbench_prompt(state, hero_index)`。hole `"of"` / board `"Of"`、preflop raise額は `str(amount)`（chips無）、potは `f"{amount:.1f}"`。teacher照合: `tests/test_pokerbench_prompt.py:124-274`。
- 出力契約（SPEC §9.3/§10A）: `fold/call/raise/allin_prob` + `raise_size_ratio`。argmax推奨。

### 7.3 環境差・gitなど
- 訓練repo branch `master`、docs repo branch `main`。
- `data/` がgitignore。Spot Checksシナリオは `git add -f` で追跡（50拡張時も同様、または`.gitignore`に `data/spot_checks/` 例外を入れる＝`.gitignore`の記法確認後）。
- 未追跡で残る重要物: `.gitignore` 本体、`scripts/train_aux_heads.py`（snapshot参照の中核ロードスクリプト）。いずれ別コミットで追跡推奨。`tmp_*.py`/`pip_freeze_*`/`analyze_*` は一時物。
- `pytest -q` 全体は `tools/poker_datasets_ref/.../test_hand_to_text.py` のcollection errorで止まるため、合否は個別テストファイル指定で判定。

---

## 8. 確定した制約（次セッション以降も有効）

1. `pokerkit==0.7.4` 死守。`tools/poker_datasets_ref` を現venvへ入れない。
2. prompt生成器・環境は6-max固定。
3. verifyは「形式整合案」（固定句・カード綴り厳密、preflop chips有無のみnormalize）。
4. 正本モデル成果物 read-only（`seg_003_offset_66000/final_adapter`、`seg_003/final_aux_head`）。訓練checkpointは `results/grpo/`。
5. state正本 `state_factory.py`。複製しない。
6. ハイパラは各Config経由（Reward/GRPO/EntropyGuard/Train）。ハードコード禁止。
7. 報酬EVはMCのみ。CFR/solver/反実仮想を持ち込まない（§65.1/§67.4）。
8. GRPO最適化対象=action head categorical（§66）。sizingは方策勾配外（detach）。案2却下。
9. group=decision-state単位、group内正規化（§67）。案B却下。
10. **entropy崩壊対策なしに長時間訓練しない**。ただし健全判定は `top1≤0.85` 単独ではない（§70.3で再設計）。Spot Checksを品質軸に。
11. **「profit vs random」単独評価禁止。Spot Checks 50を削除・緩和しない**（再設計するのはentropy/foldガード側、品質判定はSpot Checksへ寄せる）。`verify_pokerrl_encode.py` スキップ禁止。
12. PokerRL品質検証（Stage D）前にDeep CFR/Rust Solverを削除しない。Deep CFR失敗モデルはopponent枠で参照（6dで結線予定）。
13. docs一元管理（`...\poker-system\docs`）。訓練リポジトリに置かない。
14. 実装指令書v1.3は2026-06-04廃止。添付・参照不要（古いファイル一覧の指示より本注記優先）。
15. 自己対戦のpot/サイドポット/death SBはPokerKit automation委譲。
16. 候補r_i評価はstate非破壊。
17. opponentは凍結ベース共有 + PEFT multi-adapter（フルベース二重ロードしない＝3080 VRAM対策）。opponent推論は勾配なし。
18. **top1中央値≤0.85を最適化標的にしない**（§70.4。ポーカーでは確信プレイが正常）。

---

## 9. 未解決の課題・TODO

1. **次: ガード再設計**（§70.3）。`collapse_guard` 意味論変更（top1/fold→WARN、真の退化＝行動多様性欠如/Spot Checks回帰、leak別監視新設、Spot Checks品質ゲート統合）。コード変更タスク。
2. **Spot Checks 50完成**（残り30、formal Go/No-go用、§58.1 Phase2 95%）。
3. **Task 6c 本訓練**（100–150h + §57 Go/No-go）。all-in探索温度、Spot Checks/leakをゲート。
4. all-in head復活（§70.5、不確実。探索温度）。made-hand fold/SB過剰raiseの矯正トラッキング。
5. Deep CFR opponent結線（6d、PokerKit→500-dim encoding adapter + 3→4class写像）。
6. `.gitignore`/`scripts/train_aux_heads.py` の追跡整理。

主要コミット（時系列）:
- `2400e2e` 5c / `9696ebd` 6a / `99900f3` 6b / `564cd96` 6b-v / `78dda67` Spot Checksランナー / `764e687` scenarios_v0.json（訓練 `master`）
- `2d50e07` §69+§70（docs `main`）。先行doc: §66`c93c84c`/§67`5b81370`/§68`71872dd`、snapshot`a2d5ff4`。

---

## 10. 次セッション開始手順

着手対象: **ガード再設計のコード**（§70.3）。DESIGN_NOTES §56.4/§58.1/§58.2/§68.3/§70、SPEC §9.3 を正とする。

1. 移動・状態確認。
```powershell
cd C:\dev\pokerrl-training
git status
git log --oneline -6
```
期待: HEADが `764e687`。

2. 健全性確認。
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_spot_checks.py tests/test_sft_opponent.py tests/test_opponent_pool_factory.py tests/test_integrated_step.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_monitor.py tests/test_advantage.py tests/test_grpo_batch.py tests/test_grpo_loss.py tests/test_sizing.py tests/test_reward.py tests/test_selfplay_env.py tests/test_pokerbench_prompt.py tests/test_opponents.py tests/test_collect.py tests/test_checkpointing.py tests/test_train_harness.py -q
.\.venv\Scripts\python.exe scripts\run_spot_checks.py
.\.venv\Scripts\python.exe scripts\verify_pokerrl_encode.py
```
期待: 各PASS。

3. ガード再設計（§70.3）に着手。
   - `monitor.collapse_guard` を、top1/fold単発HALTから「真の退化（行動多様性欠如）+ Spot Checks回帰」判定へ。top1/foldはWARN情報監視。
   - leak別監視（all-in使用率/value-hand fold率/position過剰raise）を追加。
   - Spot Checks（`run_spot_checks`）を品質ゲートとして訓練ループ評価に統合できるフックを用意。
   - 既存テスト全PASSを受け入れ基準に。`top1≤0.85`単独HALTのテストは新意味論へ更新。
4. その後: Spot Checks 50完成 → Task 6c（all-in探索温度、Spot Checks/leakゲート、本訓練前に再設計ガードで統合smoke再確認）。
   - `pokerkit==0.7.4`・6-max・state正本・各Config経由・docs配置・正本read-only・凍結ベース共有を維持。
