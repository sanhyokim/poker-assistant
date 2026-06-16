# pokerrl-training snapshot
**Updated:** 2026-06-16 JST
**Session:** Sprint 3 Task 1 完了 — 検証1クローズ / PokerKit導入 / 教師prompt仕様抽出 / PokerKit API調査 / PokerBench形式prompt生成器実装

---

## 1. このsnapshotの位置づけ

このsnapshotは、`C:\dev\pokerrl-training` でSprint 3（GRPO準備）を次セッションへ引き継ぐための現在地点メモである。次セッションはこのファイル単体で Task 2（`verify_pokerrl_encode.py`）に着手できる状態を目標にする。

本リポジトリはローカルのみ（リモートなし）。poker-assistant本体リポジトリの体系的な仕様は `SPEC.md v3.8`、設計判断の理由は `DESIGN_NOTES.md` を参照する。

現在地:
- Sprint 2: 補助ヘッド訓練 S2-T3 完了。GRPO初期化点は `seg_003 final_adapter + final_aux_head/aux_heads.pt`。
- Sprint 3 準備: 検証1（補助ヘッド正本ペアのロードforward）はクローズ済み。
- Sprint 3 Task 1: PokerKit state → PokerBench形式prompt生成器を新規実装し、teacher照合テスト4件を含む単体テストがPASS。
- 次は Sprint 3 Task 2: `verify_pokerrl_encode.py` の新規作成。

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
- PokerKit state生成: `create_state()` は `tests/test_pokerbench_prompt.py:28-37`。
- hole cards投入: `deal_holes()` は `tests/test_pokerbench_prompt.py:40-43`。
- preflop chips normalize: `normalize_preflop_chips()` は `tests/test_pokerbench_prompt.py:46-48`。
- teacher prompt helper: `teacher_prompt()` は `tests/test_pokerbench_prompt.py:51-78`。
- teacher照合4件: `tests/test_pokerbench_prompt.py:124-274`。
- 改行分岐テスト: `tests/test_pokerbench_prompt.py:277-310`。
- bet/raise/all-inテスト: `tests/test_pokerbench_prompt.py:313-337`。

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

---

## 9. TODO

### 9.1 Sprint 3 Task 2: `verify_pokerrl_encode.py` 新規作成

目的:
- GRPO自己対戦で生成するpromptが、補助ヘッド訓練時の入力エンコードと形式整合していることを検証する。

実装方針:
- `pokerrl_grpo.pokerbench_prompt.build_pokerbench_prompt` をimportして使う。
- `tests/test_pokerbench_prompt.py` のteacher照合4サンプルを再利用する。
- preflop/flop/turn/river各1件以上で、teacher promptと生成器promptを比較する。
- preflop raise額の `"chips"` 有無だけnormalizeする。
- tokenizer経路は `scripts/train_aux_heads.py` の訓練時経路に合わせる。
- 比較結果として、prompt文字列の差分、token ids長、attention mask長、特殊トークン付与条件を出す。

注意:
- `heads.eval()` Dropout申し送りを反映し、モデルforwardを伴う検証ではeval modeにする。
- Task 2はprompt/encode検証が主目的であり、重いモデルロードは必要最小限にする。

### 9.2 DESIGN_NOTES追記予定

Task 2完了後、poker-assistant側の `docs/DESIGN_NOTES.md` にSprint 3準備の設計判断を追記する候補:
- PokerBench形式prompt生成器を新規作成した理由。
- verifyを「完全バイト一致」ではなく「形式整合案」にした理由。
- teacher側のpreflop chips揺れをnormalize対象にした理由。
- PokerKit 0.7.4固定と `poker_datasets_ref` 依存を入れない判断。
- postflop pot再計算で死にSBを除外する互換ロジック。

### 9.3 既存collection問題

`pytest -q` 全体は `tools/poker_datasets_ref/poker_datasets/test_hand_to_text.py` のcollection errorで止まる。

現時点の判断:
- Task 1追加とは無関係。
- `tools/poker_datasets_ref` を現venvへ入れるとPokerKitダウングレードが起きるため禁止。
- 必要なら別venv分離、pytest ignore設定、または当該参照ツールのテスト除外を別タスクで検討する。

### 9.4 コミット未実施

本リポジトリはローカルのみ。Task 1関連のコミットは未実施。

主な未追跡ファイル:
- `pokerrl_grpo/`
- `tests/test_pokerbench_prompt.py`
- `snapshot.md`
- 調査用一時スクリプト群（`tmp_*.py`）
- `pip_freeze_*.txt`

コミットする場合はTask 1本体・テスト・snapshotと、一時調査ファイルを分けること。

---

## 10. 次セッション開始手順

1. 作業場所へ移動。
```powershell
cd C:\dev\pokerrl-training
```

2. 状態確認。
```powershell
git status --short
git log --oneline -5
```

3. Task 1のテスト確認。
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_pokerbench_prompt.py -q
```

期待:
```text
10 passed
```

4. 補助ヘッドforward健全性を必要に応じて再確認。
```powershell
.\.venv\Scripts\python.exe .\tmp_load_forward_check.py
```

確認点:
- `Frozen base+LoRA parameters: ... trainable=0`
- `Auxiliary head parameters: trainable=3149317`
- `action_logits_shape=(1, 4)`
- `raise_size_ratio_shape=(1,)`

5. Task 2に着手。
   - 新規作成候補: `scripts/verify_pokerrl_encode.py`
   - 参照: `pokerrl_grpo/pokerbench_prompt.py`
   - 参照: `tests/test_pokerbench_prompt.py`
   - 参照: `scripts/train_aux_heads.py` のDataset/tokenizer/collate経路

6. Task 2で必ず確認すること。
   - teacher prompt 4件と生成器promptの形式整合。
   - preflop chips揺れのみnormalize。
   - tokenizer呼び出し条件（`add_special_tokens`、`truncation`、`max_length`、padding/attention_mask）が訓練時と一致。
   - GRPO自己対戦入力が補助ヘッド訓練入力と同じprompt仕様で作れる。

