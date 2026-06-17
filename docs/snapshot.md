# pokerrl-training snapshot
**Updated:** 2026-06-17 JST
**Session:** Sprint 3 GRPO装置完成 — Task 5a/5b/5c完了、次はTask 6本訓練準備

---

## 1. 現在地

このsnapshotは、`C:\dev\pokerrl-training` のSprint 3 GRPO準備状況を次セッションへ引き継ぐための現在地点メモである。docs正規パスは `C:\Users\user\Desktop\dev\poker-system\docs`。訓練リポジトリ側にsnapshotを置かない。

体系仕様は `SPEC.md v3.8`、設計判断は `DESIGN_NOTES.md` を正とする。リポジトリURLは `https://github.com/sanhyokim/poker-assistant`。訓練リポジトリ `C:\dev\pokerrl-training` はローカルのみ。

現在地:
- Sprint 2: 補助ヘッド訓練S2-T3完了。GRPO初期化点は `results/sft_sequential/seg_003_offset_66000/final_adapter` + `results/aux_heads/seg_003/final_aux_head/aux_heads.pt`。
- Sprint 3 Task 1: PokerBench形式prompt生成器完了。
- Sprint 3 Task 2: `verify_pokerrl_encode.py` 完了。
- Sprint 3 Task 3: PokerKitベース6-max自己対戦環境骨格完了。
- Sprint 3 Task 4: 報酬関数本実装完了。
- Sprint 3 Task 5a/5b/5c: trajectory収集、GRPO損失本体、entropy監視・崩壊ガード・sizing回帰仕上げ完了。**GRPO装置は完成**。
- 次は **Task 6: 本訓練準備と100-150h訓練の開始可否確認**。長時間訓練前に、実AuxHeadPolicy forward・実optimizer少数ステップ・崩壊ガード組込みを確認する。

---

## 2. 本セッションで完了したこと

### 2.1 設計判断の確定

- DESIGN_NOTES §66 追記、コミット `c93c84c`: GRPO最適化対象をAction head categorical log-probに確定。Sizing headは方策勾配に載せず、advantage重み付き回帰の別損失で扱う。
- DESIGN_NOTES §67 追記、コミット `5b81370`: group相対advantageの単位をdecision-stateに確定。バッチ内グループ正規化は却下。

### 2.2 Task 5a: trajectory収集ループ + opponent population骨格

コミット `a05deda`。

追加:
- `pokerrl_grpo/policy.py`
- `pokerrl_grpo/opponents.py`
- `pokerrl_grpo/trajectory.py`
- `pokerrl_grpo/collect.py`
- `tests/test_opponents.py`
- `tests/test_collect.py`
- `scripts/smoke_collect.py`

内容:
- `Policy` / `ActDecision` / `RandomLegalPolicy` / `AuxHeadPolicy` shellを定義。
- `RuleBasedTAG` / `RuleBasedLAG` / `StubModelOpponent` / `OpponentPool` を追加。
- hero決定のみを `StepRecord` として収集し、Task4報酬を `env.step` 経由でrecordへ載せる。
- `Trajectory.group_id` は5b用に `None` のまま残す。

検証:
- `pytest tests/test_opponents.py tests/test_collect.py -q` PASS。
- `python scripts/smoke_collect.py` PASS。

### 2.3 Task 5b: GRPO損失本体

コミット `df4543a`。

追加:
- `pokerrl_grpo/grpo_config.py`
- `pokerrl_grpo/advantage.py`
- `pokerrl_grpo/grpo_batch.py`
- `pokerrl_grpo/grpo_loss.py`
- `tests/test_advantage.py`
- `tests/test_grpo_batch.py`
- `tests/test_grpo_loss.py`
- `scripts/smoke_grpo_step.py`

内容:
- `GRPOConfig`: group size、DAPO Clip-Higher、KL、entropy bonus、OPEFO、sizing係数を一元定義。
- `group_relative_advantages`: decision-state group内で `(r_i - mean) / (std + eps)`。
- `DecisionGroup` / `GRPOBatch`: hand単位ではなくdecision-state単位のgroupを構築。
- `grpo_loss`: categorical policy ratio、DAPO Clip-Higher、KL、entropy、OPEFO、sizing regression lossを合成。
- sizing項はaction logits経路へ勾配を流さないことをテストで確認。

検証:
- `pytest tests/test_advantage.py tests/test_grpo_batch.py tests/test_grpo_loss.py -q` → `10 passed`
- `python scripts/smoke_grpo_step.py` → `SMOKE: PASS`

### 2.4 Task 5c: entropy監視・崩壊ガード + sizing回帰仕上げ + 報酬結線検証

コミット `2400e2e`。

追加:
- `pokerrl_grpo/monitor.py`
- `pokerrl_grpo/sizing.py`
- `tests/test_monitor.py`
- `tests/test_sizing.py`
- `tests/test_reward_wiring.py`
- `scripts/smoke_grpo_guard.py`

内容:
- `EntropyMonitor`: 直近windowのtop-1中央値、action頻度、sizing最頻値割合、平均entropyを集計。
- `collapse_guard`: `OK` / `WARN` / `HALT` を返す判定フック。訓練停止処理そのものはTask 6。
- `select_sizing_targets`: 正advantageかつRaise/All-in候補のみを対象にし、group内最大advantage候補のsizingをtargetにする。
- `sizing_objective`: 5bの `sizing_regression_loss` を使い、action logitsへ勾配を流さない。
- `test_reward_wiring`: Task4報酬が `env.step()` → `StepRecord/Trajectory` → `build_decision_groups()` → `group_relative_advantages()` へ届くことを検証。

検証:
- `pytest tests/test_monitor.py tests/test_sizing.py tests/test_reward_wiring.py -q` → `12 passed`
- 既存回帰: `pytest tests/test_opponents.py tests/test_collect.py tests/test_advantage.py tests/test_grpo_batch.py tests/test_grpo_loss.py tests/test_reward.py tests/test_selfplay_env.py tests/test_pokerbench_prompt.py -q` → `40 passed`
- `python scripts/verify_pokerrl_encode.py` → `passed=8 failed=0 / OVERALL: PASS`
- `python scripts/smoke_selfplay.py` → `SMOKE: PASS`
- `python scripts/smoke_collect.py` → `SMOKE: PASS`
- `python scripts/smoke_grpo_step.py` → `SMOKE: PASS`
- `python scripts/smoke_grpo_guard.py` → `SMOKE: PASS / guard_transitions_ok=True`

---

## 3. 正本成果物

GRPO初期化に必要な完全モデルは2部品のペア:
1. ベースLoRA: `results/sft_sequential/seg_003_offset_66000/final_adapter`
2. 補助ヘッド: `results/aux_heads/seg_003/final_aux_head/aux_heads.pt`

`final_aux_head` は単数形。`final_aux_heads` ではない。LoRAをfreezeしてヘッドのみ訓練したため、補助ヘッドcheckpointに `final_adapter` が無いのは正常。

Task 6で使う軽量検証:
- `python scripts/verify_pokerrl_encode.py`
- 必要時のみ `python scripts/verify_pokerrl_encode.py --with-forward`

forward時は `heads.eval()` を明示する。Dropoutを残したまま評価しない。

---

## 4. Task 6訓練情報

### 4.1 Task 6の目的

Task 6は、完成したGRPO装置を実AuxHeadPolicyとoptimizerループへ接続し、100-150hの本訓練へ進めるかを判定する段階である。本訓練開始前に、短時間の実forward・実optimizer少数ステップ・checkpoint保存・崩壊ガードHALT経路を確認する。

### 4.2 訓練パラメータの初期案

正とする情報源:
- DESIGN_NOTES §56-58
- DESIGN_NOTES §65-67
- SPEC §9.3 / §10A / §10A.11

初期方針:
- policy log-prob: Action head categorical 4-class。
- sizing: advantage重み付き回帰の別損失。
- group: decision-stateごと、`group_size=8`。
- DAPO: `eps_low=0.2`, `eps_high=0.28`。
- KL / entropy bonus / OPEFO: `GRPOConfig` 経由で管理。初期値は5b実装の既定値から開始し、短時間監視で調整。
- reward: Task4正式報酬。`RewardConfig` 経由。MC rollout EVのみ、CFR/solverなし。
- entropy guard: `EntropyGuardConfig` 経由。`top1_median_max=0.85`、Raise/Fold偏重、sizing固定を監視。

### 4.3 Go/No-go基準

Sprint 3終了時のGo/No-goはDESIGN_NOTES §57 / §58.1 / SPEC §10A.11を正とする。

Go:
- Spot Checks 50局面で行動分布が合理的に変動。
- Entropy健全: action head categorical top-1確率中央値 ≤ 0.85付近。
- Slumbot HU勝率 ≥ -15 bb/100。
- self-playでPhase 1ベースライン比 +3 bb/100以上。
- collapse guardが長時間訓練中に継続HALTしない。

No-go:
- entropy崩壊、Raise/Fold偏重、sizing固定。
- Slumbot < -30 bb/100、Spot Checks < 80%など§56の品質下限に抵触。
- 改善トレンド消失、タイムボックス超過、コスト超過。

### 4.4 撤退基準消化

DESIGN_NOTES §56.3の段階的対処を正とする。Step1 entropy崩壊対処、Step2 opponent pool見直し、Step3報酬関数調整、Step4訓練延長。Task 5cで分布ベースの崩壊ガードは実装済みだが、position/board/stack無感度はSpot Checks/Task 6範囲。

---

## 5. opponent population

DESIGN_NOTES §57の3系統を枠として保持する。

1. 過去SFT checkpoint population
   - 現状は `StubModelOpponent` の枠。
   - Task 6前に実ローダ結線が必要。
2. Rule-based TAG/LAG
   - `RuleBasedTAG` / `RuleBasedLAG` 実装済み。
   - モデルロード不要のテスト・スモーク用にも使う。
3. Deep CFR失敗モデル
   - 弱い相手として多様性確保に使う枠。
   - Deep CFRコード・成果物は削除しない。

§56.3 Step2の未達時対処（8体等確率、4種rule-based、Deep CFR 20%など）はまだ入れない。品質未達時の拡張として保留。

---

## 6. 主要コードファイルとAPI

### 6.1 prompt / encode

- `pokerrl_grpo/pokerbench_prompt.py`: PokerKit state → PokerBench形式prompt。
- `scripts/verify_pokerrl_encode.py`: 生成器prompt、teacher prompt、訓練tokenize条件の整合検証。
- 訓練時promptは生成器ではなくJSONL内の `raw["prompt"]` をそのまま使用していた。verifyは「生成器≡teacher（preflop chips揺れのみnormalize）→訓練tokenize一致」の連鎖で整合を担保する。

訓練tokenize条件:
- `add_special_tokens=True`
- `truncation=True`
- `max_length=1024`
- `padding` / `return_tensors` はtokenizer呼び出し時未指定
- collateで右padding
- `pad_token_id is None` の場合のみ `pad_token = eos_token`
- pooled抽出は `attention_mask.sum(dim=1)-1`

### 6.2 state / environment / reward

- `pokerrl_grpo/state_factory.py`: PokerKit state生成の正本。blinds 0.5/1、starting stack 100、player_count 6、8 automations。
- `pokerrl_grpo/selfplay_env.py`: `SixMaxSelfPlayEnv`。`reset()` / `legal_actions()` / `step()`。pot/サイドポット/death SBはPokerKit automationへ委譲。
- `pokerrl_grpo/config.py`: `RewardConfig`。
- `pokerrl_grpo/rollout_ev.py`: eval7ベースMC rollout EV。CFR/solverなし、state非破壊。
- `pokerrl_grpo/reward.py`: `step_reward` / `terminal_reward` / `BankrollTracker`。clip後に重み付け、全成分bb単位。

### 6.3 collection / opponents

- `pokerrl_grpo/policy.py`: `Policy` protocol、`ActDecision`、`RandomLegalPolicy`、`AuxHeadPolicy` shell。
- `pokerrl_grpo/opponents.py`: `RuleBasedTAG` / `RuleBasedLAG` / `StubModelOpponent` / `OpponentPool`。
- `pokerrl_grpo/trajectory.py`: `StepRecord` / `Trajectory`。
- `pokerrl_grpo/collect.py`: `collect_trajectories()`。hero決定のみを記録し、報酬はenv.step由来。

### 6.4 GRPO損失

- `pokerrl_grpo/grpo_config.py`: `GRPOConfig`。GRPO専用ハイパラ。
- `pokerrl_grpo/advantage.py`: `group_relative_advantages()`。
- `pokerrl_grpo/grpo_batch.py`: `DecisionGroup` / `GRPOBatch` / `build_decision_groups()` / `flatten_decision_groups()`。
- `pokerrl_grpo/grpo_loss.py`: `policy_ratio()` / `dapo_clipped_policy_loss()` / `entropy_term()` / `kl_penalty()` / `sizing_regression_loss()` / `grpo_loss()`。

### 6.5 GRPO安全レール

- `pokerrl_grpo/monitor.py`: `EntropyGuardConfig` / `EntropyMonitor` / `collapse_guard()`。
- `pokerrl_grpo/sizing.py`: `select_sizing_targets()` / `sizing_objective()`。
- `scripts/smoke_grpo_guard.py`: OK → WARN → HALTの合成遷移確認。

---

## 7. 確定制約

1. `pokerkit==0.7.4` 死守。`poker_datasets_ref` は入れない。
2. 6-max固定。`player_count != 6` は `ValueError`。
3. verifyは形式整合案。preflop raise額の `chips` 有無のみnormalize。他は緩和しない。
4. 正本モデル成果物 `final_adapter` / `final_aux_head` は読み取り専用。書き出し直し不要。
5. `pokerbench_prompt.py` とteacher照合テストの意味を壊さない。
6. `state_factory.py` がstate生成の正本。state定義を複製しない。
7. pot/サイドポット/death SBはPokerKit automationへ委譲。環境側で独自実装しない。
8. 報酬EVはMC rolloutのみ。CFR/solver/反実仮想EVを報酬へ持ち込まない。
9. 報酬各成分はbb単位、clip後に重み付け。
10. 報酬パラメータは `RewardConfig` 経由。ハードコード禁止。
11. Action head categoricalを方策log-probの正とする。DAPO/OPEFO/KL/entropyはこの分布に適用。
12. Sizing headは方策勾配に載せない。advantage重み付き回帰の別損失で扱う。
13. ドキュメント配置ルール: SPEC.md / DESIGN_NOTES.md / snapshot.md は `C:\Users\user\Desktop\dev\poker-system\docs` に一元管理する。訓練コードは `C:\dev\pokerrl-training`。docsを訓練リポジトリに置かない。
14. group_idはdecision-state単位。batch内グループ正規化は使わない。
15. GRPOハイパラは `GRPOConfig` 経由。実装内ハードコード禁止。
16. entropy崩壊対策なしに長時間訓練を開始しない。Task 6で `collapse_guard` を訓練ループへ組み込む。
17. 実装指令書 `PokerRL+GRPO 6-max NLHE.md` は2026-06-04に廃止済み。DESIGN_NOTES §56-59 / SPEC §9.4・§10A / 本snapshotを正とする。

---

## 8. Task 6前のTODO

1. `StubModelOpponent` 実ローダ結線
   - 過去SFT checkpoint population枠。
   - Deep CFR失敗モデル枠。
   - モデルロードはデフォルトpytestに含めない。

2. `AuxHeadPolicy` 実forward訓練統合
   - `verify_pokerrl_encode.py --with-forward` と同じロード経路を正とする。
   - `final_adapter + final_aux_head/aux_heads.pt` のペア。
   - `heads.eval()` / train mode切替の扱いを明確化。

3. 崩壊ガードの訓練ループ組込み
   - `EntropyMonitor` にaction probs/action/sizingを流す。
   - `collapse_guard()` の `WARN` / `HALT` をログ・早期停止へ接続。
   - top-1中央値、Raise/Fold頻度、sizing固定を保存。

4. 実optimizer少数ステップ健全性
   - 長時間訓練前に、合成または少量実trajectoryでforward/backward/optimizer stepを数回だけ確認。
   - loss finite、grad finite、sizing detach、checkpoint保存、resumeを確認。
   - 本番checkpoint保存はこの確認後。

5. Spot Checks準備
   - position/board/stack無感度はTask 5cの分布ガードでは検出できない。
   - Task 6でSpot Checks 50局面を維持し、削除・緩和しない。

---

## 9. 次セッション開始手順

1. docs確認
   - `C:\Users\user\Desktop\dev\poker-system\docs\snapshot.md`
   - `C:\Users\user\Desktop\dev\poker-system\docs\DESIGN_NOTES.md`
   - `C:\Users\user\Desktop\dev\poker-system\docs\SPEC.md`

2. 訓練リポジトリ確認
   ```powershell
   cd C:\dev\pokerrl-training
   git log --oneline -8
   git status
   ```

3. 軽量健全性チェック
   ```powershell
   .\.venv\Scripts\python.exe scripts\verify_pokerrl_encode.py
   .\.venv\Scripts\python.exe -m pytest tests\test_monitor.py tests\test_sizing.py tests\test_reward_wiring.py -q
   .\.venv\Scripts\python.exe -m pytest tests\test_opponents.py tests\test_collect.py tests\test_advantage.py tests\test_grpo_batch.py tests\test_grpo_loss.py tests\test_reward.py tests\test_selfplay_env.py tests\test_pokerbench_prompt.py -q
   .\.venv\Scripts\python.exe scripts\smoke_selfplay.py
   .\.venv\Scripts\python.exe scripts\smoke_collect.py
   .\.venv\Scripts\python.exe scripts\smoke_grpo_step.py
   .\.venv\Scripts\python.exe scripts\smoke_grpo_guard.py
   ```

4. Task 6設計整理
   - 長時間訓練をいきなり開始しない。
   - `AuxHeadPolicy` 実forward、opponent実ローダ、optimizer少数ステップ、崩壊ガード組込み、checkpoint save/resumeを先に確認する。
   - その後、100-150h本訓練へ進む。

5. Task 6本訓練に進む条件
   - 実forward + 実optimizer少数ステップがPASS。
   - `collapse_guard` が訓練ループで機能。
   - checkpoint保存・resumeがPASS。
   - Spot Checksを削除・緩和していない。
