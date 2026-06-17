# pokerrl-training snapshot
**Updated:** 2026-06-17 JST
**Session:** Sprint 3 Task 4 完了 — 報酬関数本実装（chip delta + ロールアウトEV + bankroll、bb正規化）+ eval7導入

---

## 1. このsnapshotの位置づけ

このsnapshotは、`C:\dev\pokerrl-training` でSprint 3（GRPO準備）を次セッションへ引き継ぐための現在地点メモである。次セッションはこのファイル単体で Task 5（GRPO本体 + opponent統合）に着手できる状態を目標にする。

本リポジトリはローカルのみ（リモートなし）。poker-assistant本体リポジトリの体系的な仕様は `SPEC.md v3.8`、設計判断の理由は `DESIGN_NOTES.md` を参照する。
docs正規パスは `C:\Users\user\Desktop\dev\poker-system\docs`。

現在地:
- Sprint 2: 補助ヘッド訓練 S2-T3 完了。GRPO初期化点は `seg_003 final_adapter + final_aux_head/aux_heads.pt`。
- Sprint 3 準備: 検証1（補助ヘッド正本ペアのロードforward）はクローズ済み。
- Sprint 3 Task 1: PokerKit state → PokerBench形式prompt生成器を新規実装し、teacher照合テスト4件を含む単体テストがPASS。
- Sprint 3 Task 2: `verify_pokerrl_encode.py` を新規作成し、生成器・teacher・訓練tokenizeの整合検証がPASS。
- Sprint 3 Task 3: PokerKitベース6-max自己対戦環境骨格、state正本化、スモークランゲートがPASS。
- Sprint 3 Task 4: 報酬関数本実装（chip delta + ロールアウトEV + bankroll、bb正規化）とeval7導入がPASS。
- 次は Sprint 3 Task 5: GRPO本体（DAPO trick + OPEFO entropy制御）+ opponent統合。DESIGN_NOTES §56-58 / §57、SPEC §10A を正とする。

---

## 2. 本セッションで完了したこと

1. 補助ヘッド正本ペアのロードforward実証を完了。
   - ベースLoRA: `results/sft_sequential/seg_003_offset_66000/final_adapter`
   - 補助ヘッド: `results/aux_heads/seg_003/final_aux_head/aux_heads.pt`
   - `tmp_load_forward_check.py` で `pooled_shape=(1, 3072)`、`action_logits_shape=(1, 4)`、`raise_size_ratio_shape=(1,)` を確認。
   - freeze検証: `Frozen base+LoRA parameters: total=2271546368 trainable=0`、`Auxiliary head parameters: trainable=3149317`。

2. `verify_pokerrl_encode.py` の所在確認を完了。
   - `poker-assistant` / `pokerrl-training` の両方に既存スクリプトは存在しない。
   - Task 2で新規作成が必要。

3. GRPO用prompt生成器の既存有無を確認。
   - PokerKit状態からPokerBench形式promptを生成する既存コードは未着手だった。
   - Task 1で新規パッケージ `pokerrl_grpo` に隔離して実装した。

4. PokerKit導入と互換確認を完了。
   - `pokerkit==0.7.4` を既存venvへ導入。
   - dry-runで重要依存（torch / transformers / peft / bitsandbytes / numpy等）の変更なしを確認してから導入。
   - 導入後も補助ヘッドforwardが通ることを確認。

5. 教師promptフォーマット仕様を抽出。
   - 冒頭固定句、末尾固定句、改行分岐、カード表記、ポジション値域、アクション履歴表記、completion/action_type値域を確認。
   - preflop raise額の `chips` 有無など、一部 teacher 側の揺れを確認。

6. PokerKit 0.7.4 state API の情報抽出を完了。
   - `state.hole_cards` / `state.get_board_cards(0)` / `state.total_pot_amount` / `state.actor_index` / `state.operations` 等の取得経路を確認。
   - 6-max index対応は `0=SB, 1=BB, 2=UTG, 3=HJ, 4=CO, 5=BTN`。

7. Sprint 3 Task 1 を実装。
   - 新規: `pokerrl_grpo/__init__.py`
   - 新規: `pokerrl_grpo/pokerbench_prompt.py`
   - 新規: `tests/test_pokerbench_prompt.py`
   - `pytest tests/test_pokerbench_prompt.py -q` は `10 passed`。

8. Sprint 3 Task 2 を実装。
   - 新規: `scripts/verify_pokerrl_encode.py`
   - コミット: `6bbb309 追加: PokerRL encode整合検証スクリプト（生成器/teacher/訓練tokenize一致）`
   - forwardなし検証: `passed=8 failed=0`、`OVERALL: PASS`
   - `--with-forward` 検証: `passed=9 failed=0`、`OVERALL: PASS`
   - forward shape: `pooled_shape=(1, 3072)` / `action_logits_shape=(1, 4)` / `raise_size_ratio_shape=(1,)`
   - 既存テスト: `pytest tests/test_pokerbench_prompt.py -q` は `10 passed` を維持。
   - 実装は `tests/test_pokerbench_prompt.py` のteacherヘルパと、`scripts/train_aux_heads.py` のtokenizer条件・`AuxCollator`・`load_model_tokenizer_heads`・`load_checkpoint`・`extract_pooled_hidden` を再利用。`train_aux_heads.py` / `pokerbench_prompt.py` / `tests/test_pokerbench_prompt.py` は無変更。

9. Sprint 3 Task 3 を実装。
   - 新規: `pokerrl_grpo/state_factory.py`（PokerKit state生成の正本）
   - 新規: `pokerrl_grpo/selfplay_env.py`（`SixMaxSelfPlayEnv`: `reset()` / `legal_actions()` / `step()`、報酬スタブ、prompt接続）
   - 新規: `tests/test_selfplay_env.py`
   - 新規: `scripts/smoke_selfplay.py`
   - 変更: `tests/test_pokerbench_prompt.py` はstateヘルパのimport差し替えのみ。アサーション・検証ロジックは維持。
   - コミット: `f04090d 追加: 6-max自己対戦環境の骨格とstate正本化（報酬スタブ・スモークラン）`
   - テスト: `pytest tests/test_pokerbench_prompt.py tests/test_selfplay_env.py -q` は `15 passed`。
   - 既存promptテスト: `pytest tests/test_pokerbench_prompt.py -q` は `10 passed` を維持。
   - スモーク: `hands=200 / total_steps=3761 / average_steps_per_hand=18.80 / zero_sum_ok=True / winners_ok=True / SMOKE: PASS`
   - 回帰確認: `python scripts/verify_pokerrl_encode.py` は `passed=8 failed=0` / `OVERALL: PASS`。
   - 設計判断はDESIGN_NOTES §64に記録済み（state正本化、§20準拠、snapshot §8-5の目的維持）。

10. Sprint 3 Task 4 を実装。
   - eval7導入: `eval7==0.1.10`。`pokerkit==0.7.4` への非干渉を確認。コミット: `6c4488a`。
   - 新規: `pokerrl_grpo/config.py`（`RewardConfig`: weights 0.7/0.2/0.1、playouts=100、clip_bb=100、window=20）。
   - 新規: `pokerrl_grpo/rollout_ev.py`（eval7ベースMC、CFR/solverなし、state非破壊、未知hole/未完成boardに対応）。
   - 新規: `pokerrl_grpo/reward.py`（`step_reward` / `terminal_reward` / `BankrollTracker`、clip後重み付け）。
   - 変更: `pokerrl_grpo/selfplay_env.py`（ステップEV項 + 終端chip delta/bankroll項 + 直近20ハンドリングバッファ）。
   - 変更: `scripts/smoke_selfplay.py`（3成分 raw/clipped/weighted 内訳ログ）。
   - 新規: `tests/test_reward.py`。
   - コミット: `caa10c1 追加: 報酬関数本実装（chip delta + ロールアウトEV + bankroll、bb正規化）`。
   - テスト: `pytest tests/test_reward.py tests/test_selfplay_env.py tests/test_pokerbench_prompt.py -q` は `22 passed`。
   - 既存promptテスト: `pytest tests/test_pokerbench_prompt.py -q` は `10 passed` 維持。
   - スモーク: `hands=200 / total_steps=3761 / zero_sum_ok=True / winners_ok=True / rewards_ok=True / SMOKE: PASS`。
   - 回帰確認: `python scripts/verify_pokerrl_encode.py` は `passed=8 failed=0` / `OVERALL: PASS`。
   - 設計判断はDESIGN_NOTES §65に記録済み。

---

## 3. 検証1: 補助ヘッドロードforwardの確定事項

正本ペアは実際にロードしてforward可能。

ロード対象:
- LoRA: `results/sft_sequential/seg_003_offset_66000/final_adapter`
- Heads: `results/aux_heads/seg_003/final_aux_head/aux_heads.pt`

`aux_heads.pt` のstate_dictキー:
```text
action_head.0.weight
action_head.0.bias
action_head.3.weight
action_head.3.bias
sizing_head.0.weight
sizing_head.0.bias
sizing_head.3.weight
sizing_head.3.bias
```

`--resume_from results\aux_heads\seg_003\final_aux_head` の単数形パス指定でロードできる。デフォルト探索名には plural の `final_aux_heads` が混じるため、GRPO初期化やeval_onlyでは正本パスを明示すること。

### 3.1 heads.eval() Dropout申し送り

`tmp_load_forward_check.py` はforward実証用の一時スクリプトで、現状では `heads.eval()` を明示していない。補助ヘッドMLPにDropoutがあるため、`raise_size_ratio` 等の数値は実行ごとに微妙に揺れる可能性がある。Task 2以降で再現性ある比較を行う場合は、ロード後に `heads.eval()` を必ず呼ぶ。

---

## 4. Task 1 実装サマリ

実装ファイル:
- `pokerrl_grpo/pokerbench_prompt.py`
- `pokerrl_grpo/__init__.py`

テストファイル:
- `tests/test_pokerbench_prompt.py`

公開関数:
- `build_pokerbench_prompt(state, hero_index: int) -> str`
- `card_to_words(card, *, board: bool) -> str`
- `position_for_index(index: int) -> str`
- `join_actions(items: list[str]) -> str`

対応範囲:
- PokerKit 0.7.4 の6-max NLHE state専用。
- `player_count != 6` は `ValueError`。
- hero hole cardsが2枚でない場合も `ValueError`。
- `state.operations` を時系列に再走査して、preflop/flop/turn/riverのアクション履歴を再構成する。

テスト結果:
```text
pytest tests/test_pokerbench_prompt.py -q
10 passed, 2 warnings in 0.20s
```

全体pytest:
- `pytest -q` は既存問題でcollection error。
- 原因は `tools/poker_datasets_ref/poker_datasets/test_hand_to_text.py` が `poker_datasets` をimportできないこと。
- 新規追加3ファイルを一時退避しても同じcollection errorが出るため、Task 1追加とは無関係な既存問題。
- `tools/poker_datasets_ref` のeditable installは dry-run で `pokerkit 0.7.4 -> 0.6.5` のダウングレードが見えたため、導入していない。

---

## 5. 技術参照: teacher照合サンプル現物

teacher照合テストは `tests/test_pokerbench_prompt.py` に4件存在する。

| ストリート | テスト関数 | 行番号 | 照合方式 |
|---|---|---:|---|
| preflop | `test_preflop_teacher_sample_matches_exactly` | 124-152 | バイト一致 |
| flop | `test_flop_teacher_sample_matches_after_preflop_chips_normalization` | 155-187 | preflop `chips` 揺れのみnormalize |
| turn | `test_turn_teacher_sample_matches_after_preflop_chips_normalization` | 190-228 | preflop `chips` 揺れのみnormalize |
| river | `test_river_teacher_sample_matches_after_preflop_chips_normalization` | 231-274 | preflop `chips` 揺れのみnormalize |

normalize関数は `tests/test_pokerbench_prompt.py:46-48`。コメントどおり、PokerBenchには `"raise 2.0"` と `"raise 2.0 chips"` の両方があるため、Task 2でもこの揺れだけを局所的に許容する。

### 5.1 preflop teacher prompt

再現方法:
- `create_state()` は `tests/test_pokerbench_prompt.py:28-37`。
- hole cards投入は `tests/test_pokerbench_prompt.py:126-136`。
- action sequenceは `tests/test_pokerbench_prompt.py:137-141`。
- `build_pokerbench_prompt(state, hero_index=1)` は `tests/test_pokerbench_prompt.py:142`。
- expected生成は `tests/test_pokerbench_prompt.py:143-151`。

expected prompt (`repr()`):
```python
'\n\nYou are a specialist in playing 6-handed No Limit Texas Holdem. The following will be a game scenario and you need to make the optimal decision.\n\nHere is a game summary:\n\nThe small blind is 0.5 chips and the big blind is 1 chips. Everyone started with 100 chips.\nThe player positions involved in this game are UTG, HJ, CO, BTN, SB, BB.\nIn this hand, your position is BB, and your holding is [Nine of Spade and Seven of Spade].\nBefore the flop, UTG raise 2.0, CO call, and BTN call. Assume that all other players that is not mentioned folded.\n\nNow it is your turn to make a move.\nTo remind you, the current pot size is 7.5 chips, and your holding is [Nine of Spade and Seven of Spade].\n\nDecide on an action based on the strength of your hand on this board, your position, and actions before you. Do not explain your answer.\nYour optimal action is:'
```

### 5.2 flop teacher prompt

再現方法:
- hole cards投入は `tests/test_pokerbench_prompt.py:157-167`。
- preflop action sequenceは `tests/test_pokerbench_prompt.py:168-173`。
- flop deal/actionは `tests/test_pokerbench_prompt.py:174-175`。
- `build_pokerbench_prompt(state, hero_index=3)` は `tests/test_pokerbench_prompt.py:176`。
- expected生成は `tests/test_pokerbench_prompt.py:177-186`。

expected prompt (`repr()`):
```python
'\n\nYou are a specialist in playing 6-handed No Limit Texas Holdem. The following will be a game scenario and you need to make the optimal decision.\n\nHere is a game summary:\n\nThe small blind is 0.5 chips and the big blind is 1 chips. Everyone started with 100 chips.\nThe player positions involved in this game are UTG, HJ, CO, BTN, SB, BB.\nIn this hand, your position is HJ, and your holding is [Ace of Spade and Nine of Heart].\nBefore the flop, HJ raise 2.0 chips, and BB call. Assume that all other players that is not mentioned folded.\nThe flop comes Ten Of Heart, Four Of Club, and Five Of Diamond, then BB check.\n\n\nNow it is your turn to make a move.\nTo remind you, the current pot size is 4.0 chips, and your holding is [Ace of Spade and Nine of Heart].\n\nDecide on an action based on the strength of your hand on this board, your position, and actions before you. Do not explain your answer.\nYour optimal action is:'
```

### 5.3 turn teacher prompt

再現方法:
- hole cards投入は `tests/test_pokerbench_prompt.py:192-202`。
- preflop action sequenceは `tests/test_pokerbench_prompt.py:203-208`。
- flop deal/actionは `tests/test_pokerbench_prompt.py:209-211`。
- turn deal/actionは `tests/test_pokerbench_prompt.py:212-214`。
- `build_pokerbench_prompt(state, hero_index=1)` は `tests/test_pokerbench_prompt.py:215`。
- expected生成は `tests/test_pokerbench_prompt.py:216-227`。

expected prompt (`repr()`):
```python
'\n\nYou are a specialist in playing 6-handed No Limit Texas Holdem. The following will be a game scenario and you need to make the optimal decision.\n\nHere is a game summary:\n\nThe small blind is 0.5 chips and the big blind is 1 chips. Everyone started with 100 chips.\nThe player positions involved in this game are UTG, HJ, CO, BTN, SB, BB.\nIn this hand, your position is BB, and your holding is [Seven of Diamond and Six of Diamond].\nBefore the flop, BTN raise 2.5 chips, and BB call. Assume that all other players that is not mentioned folded.\nThe flop comes Ten Of Diamond, Six Of Heart, and Four Of Heart, then BB check, and BTN check.\nThe turn comes Eight Of Diamond, then BB check, and BTN bet 4.\n\n\nNow it is your turn to make a move.\nTo remind you, the current pot size is 9.0 chips, and your holding is [Seven of Diamond and Six of Diamond].\n\nDecide on an action based on the strength of your hand on this board, your position, and actions before you. Do not explain your answer.\nYour optimal action is:'
```

### 5.4 river teacher prompt

再現方法:
- hole cards投入は `tests/test_pokerbench_prompt.py:233-243`。
- preflop action sequenceは `tests/test_pokerbench_prompt.py:244-249`。
- flop deal/actionは `tests/test_pokerbench_prompt.py:250-252`。
- turn deal/actionは `tests/test_pokerbench_prompt.py:253-257`。
- river deal/actionは `tests/test_pokerbench_prompt.py:258-259`。
- `build_pokerbench_prompt(state, hero_index=3)` は `tests/test_pokerbench_prompt.py:260`。
- expected生成は `tests/test_pokerbench_prompt.py:261-273`。

expected prompt (`repr()`):
```python
'\n\nYou are a specialist in playing 6-handed No Limit Texas Holdem. The following will be a game scenario and you need to make the optimal decision.\n\nHere is a game summary:\n\nThe small blind is 0.5 chips and the big blind is 1 chips. Everyone started with 100 chips.\nThe player positions involved in this game are UTG, HJ, CO, BTN, SB, BB.\nIn this hand, your position is HJ, and your holding is [King of Diamond and Jack of Spade].\nBefore the flop, HJ raise 2.0 chips, and BB call. Assume that all other players that is not mentioned folded.\nThe flop comes King Of Spade, Seven Of Heart, and Two Of Diamond, then BB check, and HJ check.\nThe turn comes Jack Of Club, then BB check, HJ bet 3, BB raise 10, and HJ call.\nThe river comes Seven Of Club, then BB check.\n\n\nNow it is your turn to make a move.\nTo remind you, the current pot size is 24.0 chips, and your holding is [King of Diamond and Jack of Spade].\n\nDecide on an action based on the strength of your hand on this board, your position, and actions before you. Do not explain your answer.\nYour optimal action is:'
```

---

## 6. 主要コードファイルと固定規則

### 6.1 `pokerrl_grpo/pokerbench_prompt.py`

重要行:
- カード表記: `card_to_words()` は `pokerrl_grpo/pokerbench_prompt.py:55-61`。holeは `"of"`、boardは `"Of"`。
- 6-maxポジション: `INDEX_TO_POSITION` は `pokerrl_grpo/pokerbench_prompt.py:23`。
- action接続詞: `join_actions()` は `pokerrl_grpo/pokerbench_prompt.py:72-80`。2個は `"A, and B"`、3個以上は `"A, B, and C"`。
- prompt組み立て: `build_pokerbench_prompt()` は `pokerrl_grpo/pokerbench_prompt.py:83-106`。
- postflop改行分岐: `separator = "\n\n\n" if board_lines else "\n\n"` は `pokerrl_grpo/pokerbench_prompt.py:97`。
- state.operations走査: `_build_history()` は `pokerrl_grpo/pokerbench_prompt.py:164-186`。

### 6.2 生成器の揺れフィールド固定規則

コードを正とする固定規則:
- preflop raise額: `_format_amount()` の `street_index == 0` 分岐（`pokerrl_grpo/pokerbench_prompt.py:152-154`）で `str(amount)` をそのまま使う。例: `2.0`、`2.5`。生成器側では `"chips"` を付けない。
- postflop bet/raise額: `_format_amount()` のpostflop分岐（`pokerrl_grpo/pokerbench_prompt.py:155-157`）で、整数なら `str(int(amount))`、非整数なら `str(amount)`。例: `4`、`10`。
- pot額: `_format_pot_amount()`（`pokerrl_grpo/pokerbench_prompt.py:160-161`）で常に `f"{amount:.1f}"`。例: `4.0`、`9.0`、`24.0`。
- all-in表記: `_append_check_or_call()` と `_append_bet_or_raise_to()`（`pokerrl_grpo/pokerbench_prompt.py:203-231`）で、stackが0になった場合は金額なしの `"all in"`。

Task 2のnormalize方針:
- §5のteacher照合と同じく、preflop raise額の `"chips"` 有無だけはteacher側に揺れがあるため局所normalizeする。
- pot小数1桁、postflop整数額、hole/boardの `of/Of`、固定句、改行分岐、ポジション、接続詞は生成器出力を正として厳密比較する。

### 6.3 死にSB除外のpot再計算ロジック

非自明な互換ロジックは `_prompt_pot_amount()`（`pokerrl_grpo/pokerbench_prompt.py:234-241`）。

コード上の挙動:
- preflopのみ（`history.board_deals` が空）の場合は `state.total_pot_amount` をそのまま使う（`pokerrl_grpo/pokerbench_prompt.py:235-236`）。
- postflopでは `state.total_pot_amount` をそのまま使わず、`history.contributions` を再集計する（`pokerrl_grpo/pokerbench_prompt.py:237-241`）。
- 集計対象は `state.statuses[player_index]` が真のプレイヤー、または `player_index in history.any_voluntary` のプレイヤー。
- つまり、プリフロップで自発的アクションをせずにfold扱いになった死にSBのブラインドは、postflop teacher prompt のpot表記に寄せるため除外される。

理由:
- PokerKitの `state.total_pot_amount` は死にSBの0.5も含むが、教師promptのpostflop potはpreflop未参加扱いの死にSBを除いた値になっているサンプルがある。
- Task 1のflopサンプルでは、PokerKit総potなら4.5相当になり得る局面を、teacher期待値 `4.0` に合わせるため、postflopだけ再計算している。

### 6.4 `tests/test_pokerbench_prompt.py`

重要行:
- PokerKit state生成ヘルパはTask 3で `pokerrl_grpo/state_factory.py` に正本化済み。`tests/test_pokerbench_prompt.py` はそこからimportする。
- preflop chips normalize: `normalize_preflop_chips()` は `tests/test_pokerbench_prompt.py:46-48`。
- teacher prompt helper: `teacher_prompt()` は `tests/test_pokerbench_prompt.py:51-78`。
- teacher照合4件: `tests/test_pokerbench_prompt.py:124-274`。
- 改行分岐テスト: `tests/test_pokerbench_prompt.py:277-310`。
- bet/raise/all-inテスト: `tests/test_pokerbench_prompt.py:313-337`。

### 6.5 `scripts/verify_pokerrl_encode.py`

Sprint 3 Task 2で追加したencode整合検証スクリプト。CI/日常確認ではモデルロードなし、必要時のみ `--with-forward` で正本ペアの実forwardを確認する。

検証構成:
- Part A: 形式整合。`build_pokerbench_prompt(state, hero_index)` とteacher promptを比較し、preflop raise額の `"chips"` 有無のみ `normalize_preflop_chips()` で吸収する。
- Part B: encode整合。訓練時と同じtokenizerロード・tokenize条件で、生成器promptとteacher promptの `input_ids` / `attention_mask` を比較する。
- Part C: forward健全性。`--with-forward` 指定時のみ、代表preflopサンプルを正本ペアへ通し、`pooled_shape=(1, 3072)` / `action_logits_shape=(1, 4)` / `raise_size_ratio_shape=(1,)` を確認する。

重要な実装判断:
- `tests/test_pokerbench_prompt.py` のteacherサンプル生成ヘルパをimportし、サンプル定義の重複を避けている。
- `scripts/train_aux_heads.py` の `load_model_tokenizer_heads` / `load_checkpoint` / `AuxCollator` / `extract_pooled_hidden` を再利用し、訓練時経路とverify経路を揃えている。
- forward前に `heads.eval()` を明示する。補助ヘッドMLPのDropoutによる出力揺れを避けるため。
- Heads正本は単数形パス `results/aux_heads/seg_003/final_aux_head` を明示指定する。plural `final_aux_heads` と混同しない。

### 6.6 `pokerrl_grpo/state_factory.py`

Sprint 3 Task 3で追加したPokerKit state生成の正本。Task 1のteacher照合テストで使っていた値を完全維持し、テストと自己対戦環境の双方がここからimportする。

公開ヘルパ:
- `create_state()`: blinds 0.5/1、starting 100、player_count 6、8 automations、ante 0、min_bet 1。
- `deal_holes(state, cards_by_index)`
- `deal_board_cards(state, cards)`
- `complete_to(state, amount)`
- `call(state)`
- `fold(state)`

制約:
- state定義値はTask 1/2のprompt・encode整合に直結するため改変禁止。
- 今後state定義を別モジュールへ複製しない。変更が必要な場合はこの正本を変更し、影響テストを同時に更新する。

### 6.7 `pokerrl_grpo/selfplay_env.py`

Sprint 3 Task 3で追加した自己対戦環境骨格。`SixMaxSelfPlayEnv` がPokerKit stateを薄く包む。

主要インターフェース:
- `reset()`: `state_factory.create_state()` で新ハンドを開始し、ランダムにホールカードを配り、最初の観測を返す。
- `legal_actions()`: 現在actorのfold/check/call/raise/all-in可否とraise範囲をPokerKit APIから返す。
- `step(action)`: アクションをPokerKit stateへ適用し、次観測、正式報酬、done、infoを返す。
- `current_player_index`: 現在手番player index。

重要な設計:
- 6-max固定。`player_count != 6` は `ValueError`。
- 各意思決定局面で `build_pokerbench_prompt(state, hero_index=current_player)` を呼べる。
- pot / サイドポット / death SB / showdown処理はPokerKit automationに委譲し、環境側では独自実装しない。
- 報酬はTask 4で正式報酬へ置換済み。各意思決定時点でEV項、ハンド終端でchip delta + bankroll項を返す。
- 直近20ハンドのbankrollリングバッファを環境インスタンス内に保持する。新しい `SixMaxSelfPlayEnv` インスタンスで履歴はリセットされる。

### 6.8 `pokerrl_grpo/config.py`

Sprint 3 Task 4で追加した報酬パラメータの正本。`RewardConfig` に以下を集約する。

- `weight_chip_delta = 0.7`
- `weight_rollout_ev = 0.2`
- `weight_bankroll = 0.1`
- `rollout_playouts = 100`
- `clip_bb = 100.0`
- `bankroll_window = 20`

報酬パラメータは今後このconfig経由で参照する。重み・clip・playouts・windowを実装内でハードコードしない。

### 6.9 `pokerrl_grpo/rollout_ev.py`

Sprint 3 Task 4で追加したeval7ベースのMCロールアウトEV計算。

重要な固定規則:
- `rollout_ev(state, hero_index, playouts, rng) -> float` はbb単位のEVを返す。
- 既存PokerKit stateは読み取り専用で、ロールアウト中に破壊しない。
- 未完成boardをランダム補完し、未知の相手holeがある場合もdeckからサンプルする。
- ショーダウン評価は `eval7.evaluate()`。PokerKitカード表現は `repr(card)` ベースでeval7の `Card` に変換する。
- CFR/solver/相手最適応答は持ち込まない。報酬EVはMC近似に限定する。

### 6.10 `pokerrl_grpo/reward.py`

Sprint 3 Task 4で追加した報酬3成分の合成本体。

公開要素:
- `step_reward(state, hero_index, config, rng)`: 各意思決定時点のrollout EV項。
- `terminal_reward(hand_chip_delta_bb, bankroll_recent_bb, config)`: ハンド終端のchip delta + bankroll項。
- `BankrollTracker`: 直近 `bankroll_window` ハンドの収支リングバッファ。
- `RewardResult.to_info()`: smoke/debug用に raw_bb / clipped_bb / weighted / weight を返す。

固定規則:
- 全成分はbb単位。
- 各成分は ±`clip_bb` でclipしてから重み付けする。
- 合成重みは `RewardConfig` の 0.7 / 0.2 / 0.1 を使う。

### 6.11 `scripts/smoke_selfplay.py`

Sprint 3 Task 3で追加し、Task 4で本報酬の3成分内訳ログを追加したスモークランゲート。軽量な探索ルールベース方策で200ハンドを回し、配管と報酬の生存を確認する。

確認項目:
- 完走すること。
- chip deltaがゼロサム保存されること。
- payoff符号が整合すること（非ゼロ決着なら勝者が正・敗者が負、全員ゼロなら許容）。
- fold / check-call / raise / all-in が全て非ゼロで、粗いアクション崩壊がないこと。
- reward3成分（rollout_ev / chip_delta / bankroll）が常時0または全clip張り付きに崩壊していないこと。

確認済み結果:
```text
hands=200
total_steps=3761
average_steps_per_hand=18.80
zero_sum_ok=True
winners_ok=True
rewards_ok=True
SMOKE: PASS
```

---

## 7. PokerKit 0.7.4 API前提

導入済みバージョン:
```text
pokerkit==0.7.4
```

Task 1で使っている生成方法:
```python
NoLimitTexasHoldem.create_state(
    automations=AUTOMATIONS,
    ante_trimming_status=True,
    raw_antes=0,
    raw_blinds_or_straddles=(0.5, 1),
    min_bet=1,
    raw_starting_stacks=100,
    player_count=6,
)
```

主要API:
- `state.hole_cards[player_index]`
- `state.operations`
- `state.starting_stacks`
- `state.player_count`
- `state.total_pot_amount`
- `state.statuses`
- `state.deal_hole(card, player_index)`
- `state.deal_board(cards)`
- `state.fold()`
- `state.check_or_call()`
- `state.complete_bet_or_raise_to(amount)`

PokerKit operation型:
- `BlindOrStraddlePosting`
- `BetCollection`
- `BoardDealing`
- `Folding`
- `CheckingOrCalling`
- `CompletionBettingOrRaisingTo`

アクション履歴はPokerKit stateの最終値だけでは十分でないため、`state.operations` を時系列に再走査して生成器側で履歴とstack/bet/contributionを再構成する。

---

## 8. 確定制約

1. `pokerkit==0.7.4` を死守する。
   - `tools/poker_datasets_ref` のeditable installは `pokerkit==0.6.5` へのダウングレードを要求するため、現venvへ入れない。
   - PokerKit API調査とTask 1実装は0.7.4前提。

2. prompt生成器は6-max固定。
   - `INDEX_TO_POSITION = {0:"SB",1:"BB",2:"UTG",3:"HJ",4:"CO",5:"BTN"}`。
   - `player_count != 6` は `ValueError`。

3. verifyは「形式整合案」で実施する。
   - 揺れのない固定句、カード綴り、`of/Of`、改行、ポジション、接続詞は厳密比較。
   - teacher側に揺れがあるpreflop raise額の `"chips"` 有無は局所normalize。
   - これはDESIGN_NOTES §56.4の方針と整合する。

4. 正本モデル成果物は読み取り専用。
   - `results/aux_heads/seg_003/final_aux_head/`
   - `results/sft_sequential/seg_003_offset_66000/final_adapter`
   - 書き出し直し不要。`final_adapter` がaux head側に無いのはLoRA freeze設計上正常。

5. `scripts/train_aux_heads.py` はTask 1では変更していない。Task 2でも既存訓練経路の読み取りを基本とする。

6. 依存追加は最小限。
   - PokerKit導入後に追加されたpytest関連依存は `pytest==9.1.0`、`iniconfig==2.3.0`、`pluggy==1.6.0` のみ。
   - torch / transformers / peft / bitsandbytes / numpy / pokerkit の重要依存変更なし。

7. `pytest -q` 全体失敗は既存collection問題。
   - Task 1追加前から `tools/poker_datasets_ref/...` の `ModuleNotFoundError: No module named 'poker_datasets'` が出る。
   - Task 1の合否は `pytest tests/test_pokerbench_prompt.py -q` を正とする。

8. **ドキュメント配置ルール（確定）**: 仕様書・設計ノート・snapshot（SPEC.md / DESIGN_NOTES.md / snapshot.md）は `C:\Users\user\Desktop\dev\poker-system\docs` に一元管理する。訓練コード・実験スクリプトは `C:\dev\pokerrl-training`。**docsを訓練リポジトリに置かない**（2026-06-16にsnapshotを訓練リポジトリへ誤保存した事例あり。Builderへ保存場所を指示する際は必ずフルパスで `...\poker-system\docs\` を明記する）。

9. 補助ヘッド訓練時promptは生成器ではなく、JSONL内のteacher prompt（`raw["prompt"]`）をそのまま使用していた。
   - `scripts/train_aux_heads.py` の `convert_record()` で `prompt = str(raw["prompt"])` として読み込む（L381）。
   - verifyは「生成器prompt ≡ teacher prompt（preflop chips揺れのみnormalize）→ 訓練tokenize一致」の連鎖で整合を担保する。

10. 訓練tokenize条件は確定済み。
   - tokenizerロード: `AutoTokenizer.from_pretrained(config.model_path, trust_remote_code=True)`
   - `pad_token_id is None` の場合のみ `tokenizer.pad_token = tokenizer.eos_token`
   - tokenizer呼び出し: `add_special_tokens=True, truncation=True, max_length=1024`
   - tokenizer呼び出し時に `padding` / `return_tensors` は指定しない。
   - collateで右padding: `input_ids + [pad_token_id] * padding`、`attention_mask + [0] * padding`
   - pooled抽出は `attention_mask.sum(dim=1)-1` で最終非padding位置を取るため、右padding前提を崩さない。

11. 注記: 実装指令書 `PokerRL+GRPO 6-max NLHE.md`（v1.3）は **2026-06-04に廃止済み**。有用情報は DESIGN_NOTES §56-59 / SPEC §9.4・§10A / 本snapshot へ移動済み。**本ファイルの添付・参照は不要**。Sprint 1-3でも実装指令書は渡さない。（Commander運用上、システムプロンプトの古いファイル一覧に「実装指令書を必ず添付」と残っていても、この注記を優先する）

12. state生成の正本は `pokerrl_grpo/state_factory.py`。
   - 今後state定義（blinds 0.5/1、starting 100、player_count 6、8 automations）を複製しない。
   - テストと自己対戦環境の双方が `state_factory` からimportする。
   - これはDESIGN_NOTES §20の重複回避方針に準拠する。

13. 自己対戦環境のpot計算はPokerKit automationに委譲する。
   - 環境側で独自pot / サイドポット / death SBロジックを書かない。
   - teacher prompt互換のpot文字列調整は `pokerbench_prompt.py` 側の責務（§63.5）。環境はPokerKit stateを正しく進行させることに集中する。

14. Sprint 3 Task 4で報酬スタブは正式報酬へ置換済み。
   - `step_reward`: rollout EV項。
   - `terminal_reward`: chip delta項 + 直近20ハンドbankroll項。
   - `selfplay_env.py` は直近20ハンドのリングバッファを保持する。

15. 報酬EVはロールアウト（MC）で計算する。
   - 反実仮想EV / CFR / solverを報酬に持ち込まない。
   - これはDESIGN_NOTES §65.1、およびソルバー永久廃止方針の延長。

16. 報酬各成分はclip後に重み付けし、bb単位に正規化する。
   - 順序: raw bb → ±`clip_bb` clip → weight適用。
   - Go/No-goのbb/100指標とスケールを揃える（DESIGN_NOTES §65.3 / §65.4）。

17. 報酬パラメータは `RewardConfig` 経由。
   - weights、rollout_playouts、clip_bb、bankroll_windowを実装内でハードコードしない。

18. eval7は導入済みで、`pokerkit==0.7.4` への非干渉を確認済み。
   - `eval7==0.1.10`
   - 追加依存は `future==1.0.0` / `pyparsing==3.3.2`。

---

## 9. TODO

### 9.1 Sprint 3 Task 2: `verify_pokerrl_encode.py` 新規作成（完了）

完了内容:
- `scripts/verify_pokerrl_encode.py` を新規作成。
- `python scripts/verify_pokerrl_encode.py`: Part A/Bが4サンプル全PASS（`passed=8 failed=0`）。
- `python scripts/verify_pokerrl_encode.py --with-forward`: Part A/B/Cが全PASS（`passed=9 failed=0`）。
- `pytest tests/test_pokerbench_prompt.py -q`: `10 passed` を維持。
- コミット済み: `6bbb309`

### 9.2 DESIGN_NOTES追記予定

Sprint 3 Task 2の設計判断は `docs/DESIGN_NOTES.md` §63 に追記済み。
- PokerBench形式prompt生成器を新規作成した理由。
- verifyを「完全バイト一致」ではなく「形式整合案」にした理由。
- teacher側のpreflop chips揺れをnormalize対象にした理由。
- PokerKit 0.7.4固定と `poker_datasets_ref` 依存を入れない判断。
- postflop pot再計算で死にSBを除外する互換ロジック。

Sprint 3 Task 3の設計判断は `docs/DESIGN_NOTES.md` §64 に追記済み。
- state生成を `pokerrl_grpo/state_factory.py` へ正本化した理由。
- `tests/test_pokerbench_prompt.py` のimport差し替えがsnapshot §8-5の目的に反しない理由。
- 報酬をスタブに留め、環境骨格を独立タスクに切った理由。

Sprint 3 Task 4の設計判断は `docs/DESIGN_NOTES.md` §65 に追記済み。
- EV計算にロールアウト（MC）を採用し、反実仮想EVを却下した理由。
- プレイアウト回数をconfig可変にした理由。
- 各成分をclipしてから重み付けする順序にした理由。
- 報酬をbb単位に正規化した理由。
- ステップ報酬と終端報酬のハイブリッド時間粒度にした理由。

### 9.3 Sprint 3 Task 3（完了）

完了内容:
- `pokerrl_grpo/state_factory.py` を新規作成し、PokerKit state生成を正本化。
- `pokerrl_grpo/selfplay_env.py` を新規作成し、`SixMaxSelfPlayEnv` の骨格を実装。
- `tests/test_selfplay_env.py` と `scripts/smoke_selfplay.py` を追加。
- `tests/test_pokerbench_prompt.py` はimport差し替えのみで、`10 passed` を維持。
- コミット済み: `f04090d`

### 9.4 Sprint 3 Task 4（完了）

完了内容:
- eval7を導入し、`pokerkit==0.7.4` への非干渉を確認。コミット済み: `6c4488a`
- `pokerrl_grpo/config.py` / `pokerrl_grpo/rollout_ev.py` / `pokerrl_grpo/reward.py` を追加。
- `pokerrl_grpo/selfplay_env.py` を正式報酬へ差し替え。
- `scripts/smoke_selfplay.py` に3成分内訳ログを追加。
- `tests/test_reward.py` を追加。
- コミット済み: `caa10c1`

検証:
- `pytest tests/test_reward.py tests/test_selfplay_env.py tests/test_pokerbench_prompt.py -q`: `22 passed`
- `pytest tests/test_pokerbench_prompt.py -q`: `10 passed`
- `python scripts/smoke_selfplay.py`: `SMOKE: PASS`
- `python scripts/verify_pokerrl_encode.py`: `passed=8 failed=0` / `OVERALL: PASS`

### 9.5 Sprint 3 Task 5（次タスク）

次セッションはSprint 3 Task 5（GRPO本体 + opponent統合）から再開する。DESIGN_NOTES §56-58 / §57、SPEC §10A を正とする。

Task 5で整理してから実装すること:
- GRPO本体（DAPO trick + OPEFO entropy制御）。
- entropy監視と崩壊対策。entropy崩壊対策なしに訓練しない。
- opponent統合（過去SFT checkpoint population / Rule-based TAG-LAG / Deep CFR失敗モデル）。
- Task 4で実装した報酬（rollout EV + chip delta + bankroll）をtrajectoryへ載せる経路。

その先の道筋:
- Task 6: 100-150h訓練 + §57 Go/No-go本判定。
- Phase 2終了Go/No-go（Spot Checks、entropy、Slumbot、self-play bb/100）はTask 6で判定する。
- 実装指令書は廃止済みで参照不要。DESIGN_NOTES §56-59 / SPEC §9.4・§10A / 本snapshotを正とする。

### 9.6 既存collection問題

`pytest -q` 全体は `tools/poker_datasets_ref/poker_datasets/test_hand_to_text.py` のcollection errorで止まる。

現時点の判断:
- Task 1/2/3追加とは無関係。
- `tools/poker_datasets_ref` を現venvへ入れるとPokerKitダウングレードが起きるため禁止。
- 必要なら別venv分離、pytest ignore設定、または当該参照ツールのテスト除外を別タスクで検討する。

### 9.7 コミット状態

訓練リポジトリはローカルのみ。Task 1/2/3/4本体はコミット済み。

主な関連コミット:
- `96aa90a` Task 1: PokerBench prompt生成器とテスト
- `01e804f` snapshot.mdを訓練リポジトリから除外
- `6bbb309` Task 2: encode整合検証スクリプト
- `f04090d` Task 3: 6-max自己対戦環境骨格とstate正本化
- `6c4488a` Task 4-pre: eval7導入
- `caa10c1` Task 4: 報酬関数本実装

未追跡のまま残っている可能性があるもの:
- 調査用一時スクリプト群（`tmp_*.py`）
- `pip_freeze_*.txt`

一時調査ファイルをコミットする場合は、本体コードとは分けること。

---

## 10. 次セッション開始手順

着手対象は Sprint 3 Task 5（GRPO本体 + opponent統合）。DESIGN_NOTES §56-58 / §57、SPEC §10A を正とし、Task 4で実装した正式報酬をGRPO trajectoryへ載せる。

1. 作業場所へ移動。
```powershell
cd C:\dev\pokerrl-training
```

2. 状態確認。
```powershell
git status --short
git log --oneline -5
```

3. Task 1/3/4のテスト確認。
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_reward.py tests/test_selfplay_env.py tests/test_pokerbench_prompt.py -q
```

期待:
```text
22 passed
```

4. Task 1のteacher照合テスト単体確認。
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_pokerbench_prompt.py -q
```

期待:
```text
10 passed
```

5. Task 2のencode整合確認（軽量・モデルロードなし）。
```powershell
.\.venv\Scripts\python.exe scripts\verify_pokerrl_encode.py
```

期待:
```text
SUMMARY: passed=8 failed=0
OVERALL: PASS
```

6. Task 4の自己対戦スモーク確認。
```powershell
.\.venv\Scripts\python.exe scripts\smoke_selfplay.py
```

期待:
```text
SMOKE: PASS
zero_sum_ok=True
winners_ok=True
rewards_ok=True
```

7. Task 2のforward健全性確認（必要時・モデルロードあり）。
```powershell
.\.venv\Scripts\python.exe scripts\verify_pokerrl_encode.py --with-forward
```

期待:
```text
SUMMARY: passed=9 failed=0
OVERALL: PASS
pooled_shape=(1, 3072)
action_logits_shape=(1, 4)
raise_size_ratio_shape=(1,)
```

8. 補助ヘッドforward健全性を旧一時スクリプトで必要に応じて再確認。
```powershell
.\.venv\Scripts\python.exe .\tmp_load_forward_check.py
```

確認点:
- `Frozen base+LoRA parameters: ... trainable=0`
- `Auxiliary head parameters: trainable=3149317`
- `action_logits_shape=(1, 4)`
- `raise_size_ratio_shape=(1,)`

9. Sprint 3 Task 5に着手。
   - DESIGN_NOTES §56-58 / §57、SPEC §10A を正としてGRPO本体を実装する。
   - DAPO trick、OPEFO entropy制御、entropy監視、opponent統合の方針を先に整理する。
   - entropy崩壊対策なしに長時間訓練を開始しない。
   - opponent候補は §57 の3系統（過去SFT checkpoint population / Rule-based TAG-LAG / Deep CFR失敗モデル）を正とする。
   - Task 6で100-150h訓練 + §57 Go/No-go本判定に進む。
   - `pokerkit==0.7.4`、6-max固定、state正本 `pokerrl_grpo/state_factory.py`、RewardConfig経由、docs配置ルールを維持する。
