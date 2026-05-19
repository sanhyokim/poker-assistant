# Commander Snapshot
## Updated: 2026-05-18 JST
## Status: Post-Fix85 architecture reset point

---

## 0. このsnapshotの位置づけ

このファイルは、Fix85完了後の次セッション用スナップショットである。

目的は、次セッションで以下をすぐ理解できる状態にすること。

```text
- 現在の最新commit
- Fix68〜Fix85で何が変わったか
- Solver / HUD / PRE-HAND / Fold badge / deep-SPR比較requestの現状
- 今後の最優先方針
- 局所guard追加から本流設計へ戻す必要性
```

重要:

```text
- 本システムの目的は、オンラインポーカーで勝率を上げるための信頼できる推奨サインを出すこと
- CoinPoker固有の例外処理集にしない
- 以後の修正は Site Adapter / GameState / Decision Engine / HUD のどの層の問題かを分類してから行う
- Builder指示書は、ユーザーが明示的に「指示書を出して」と言うまで出さない
```

---

## 1. 現在地点

### 1.1 最新commit

Fix85まで完了。

```text
e250b99 修正: Solver中HUDちらつきとhand開始直後FOLD表示を抑制
```

### 1.2 最新テスト結果

```text
python -m pytest tests/test_game_loop.py -q -> 244 passed
python -m pytest tests/test_hand_manager.py -q -> 190 passed
python -m pytest tests/test_hud_overlay.py -q -> 22 passed
python -m pytest -q -> 1282 passed, 7 warnings
```

### 1.3 現在の開発状態

Fix68〜Fix85で以下が入った。

```text
- PRE-HAND / PRE-HAND-CANDIDATE
- position lock安定化
- HU position表記調整
- blind記録をposition計算と整合
- Solver timeout / stale / orphan worker対策
- Solver process reset
- Solver request完全JSON保存
- Solver input stability gate
- deep-SPR flop compare_no_allin request保存
- preflop Hero CHECK→CALL正規化
- Solver中HUDちらつき抑制
- hand start直後のFold badge抑制
- active_player_count < 2 / hero_position None 時のpreflop推奨ブロック
```

ただし、局所guardが増えすぎてシステムの本流から離れつつある。

次は新しいバグ修正を急がず、設計整理を優先する。

---

## 2. 最重要方針

### 2.1 システム目的

```text
オンラインポーカーで勝率を上げるための信頼できる推奨サインを出すこと。
```

速く何かを表示することが目的ではない。

禁止:

```text
- 不安定GameStateで推奨を出す
- stale推奨を出す
- fallback FOLDを安易に出す
- 推奨ではない状態表示をRecommendationとして保存する
- CoinPoker固有の例外処理をDecision Engineへ混ぜる
```

### 2.2 層分離

今後は以下の4層で考える。

```text
1. Site Adapter層
   - CoinPoker固有の座標・UI認識
   - Fold badge
   - dealer button
   - bet / stack / pot OCR領域
   - アニメーション・遮蔽・残像guard
   - 将来の他サイト対応profile

2. GameState層
   - hand_id
   - phase
   - hero cards
   - board
   - pot
   - players_in_hand
   - actions
   - position
   - サイト非依存のポーカー状態

3. Decision Engine層
   - Preflop Chart
   - HU Postflop Solver
   - Multiway eval7 + LLM + 数理ガード
   - all-in pot odds / equity避難路
   - 安定GameStateだけを入力にする

4. HUD層
   - 確定推奨表示
   - 処理中表示
   - WAITING / PRE-HAND / UNSTABLE表示
   - 推奨と状態表示を混同しない
```

### 2.3 次の実装判断ルール

今後、バグ報告やログ分析時は、実装指示の前に必ず以下を分類する。

```text
- Site Adapter層の問題か
- GameState層の問題か
- Decision Engine層の問題か
- HUD層の問題か
- CoinPoker固有か
- 汎用化できるか
- 勝率・判断品質に直接寄与するか
```

小さな症状ごとのguard追加を最初の選択肢にしない。

---

## 3. Fix68〜Fix85の重要変更サマリ

### 3.1 PRE-HAND / PRE-HAND-CANDIDATE

目的:

```text
hand start前に発生したblind / raise / callなどのpreflop actionを失わない。
```

現在の状態:

```text
- PRE-HAND表示あり
- PRE-HAND-CANDIDATEあり
- waiting中のpreflop action bufferあり
- soft timeout / hard timeoutあり
- low confidence / Hero actionの誤commit抑制あり
```

注意:

```text
PRE-HAND系はCoinPokerのカード配布・blind演出に強く依存している可能性がある。
将来的にはSite Adapter層へ整理する候補。
```

---

### 3.2 position lock / blind記録

完了内容:

```text
- active hand中のdealer OCR mismatchではposition lockをclear/relockしない
- POSITION_LOCK_APPLIED / IGNORED / CLEARED / SKIPPEDログ追加
- HU表示はBTN / BBのまま
- HU blind記録ではBTN側をBLIND_SB、BB側をBLIND_BBとして扱う
- blind記録をcalculate_positions()ベースへ変更
```

現在方針:

```text
- active hand中はhand開始時のdealer / positionを原則固定
- waiting / hand_end / 明確なnew hand時のみposition更新
```

---

### 3.3 Solver input stability gate

Fix82で実装済み。

目的:

```text
不安定なGameStateをSolverへ渡さない。
```

Solver起動前に確認するもの:

```text
- active_player_count == 2
- board_countとphaseが一致
- hero cardsが安定
- hero_positionが確定
- hero_is_ipが確定
- effective_stackが取得可能
- street_start_potが異常でない
- actions_playedが構築可能
- active seats / position lock / folded seatsが矛盾していない
```

不安定時:

```text
strategy_source=solver_input_unstable
HUD表示のみ
HandManagerへ保存しない
Solverを起動しない
```

---

### 3.4 Solver process reset

Fix83で実装済み。

背景:

```text
表向きcancelされたSolver requestの裏でpostflop_cli.exeが計算を続け、
次のSolver requestを詰まらせる可能性が高かった。
```

現在方針:

```text
- Python worker threadは直接killしない
- timeout / cancel / orphan / hand_end / waiting時はpostflop_cli.exeをprocess resetする
- 次requestはclean processへ送る
- timeout / solver_input_unstable はRecommendation保存しない
```

reset条件:

```text
- Solver timeout
- Hero turn終了
- street変更
- hand_end
- waiting
- orphan worker
```

---

### 3.5 Solver request完全JSON保存

Fix83〜Fix84で実装済み。

保存先:

```text
debug/solver_io/YYYYMMDD/
```

保存内容:

```text
- hand_id
- phase
- request_id
- created_at
- hero_position
- hero_is_ip
- active_seats
- preflop_scenario
- range_source
- range_oop
- range_ip
- raw_preflop_actions
- normalized_preflop_actions
- current_street_actions
- actions_played_status
- street_start_pot
- street_start_effective_stack
- SPR
- 完全なSolver request
```

目的:

```text
Solver timeout時に、実際にpostflop_cli.exeへ渡したrequestを単体再現できるようにする。
```

---

### 3.6 deep-SPR flop compare_no_allin request

Fix84で実装済み。

背景:

```text
deep-SPR flopではSolver treeが大きくなり、timeoutしやすい。
現行requestのbet sizeは 60%,a。
a はAll-in候補。
```

現在方針:

```text
- 本番requestの60%,aは維持
- deep-SPR flop rootでは比較用no-allin requestを保存
- compare_no_allinはSolverへ送らない
- 正式推奨には使わない
- 十分な比較結果を見てから条件付きall-in候補化を判断
```

候補ルール:

```text
flop:
  SPRが高くstreet初手ならall-in候補なしを検討
  SPRが低い場合、またはfacing bet / raise後はall-in候補維持

turn:
  状況次第で条件付き

river:
  all-in候補維持

相手ALL-IN:
  Solver可能ならSolver
  Solver不可ならequity / pot odds数理避難路
```

---

### 3.7 preflop Hero CHECK→CALL正規化

Fix84で実装済み。

問題:

```text
HU preflopでHeroがBBとして相手RAISEを受けているのに、
Hero actionがCHECK 0として記録されるケースがあった。
```

現在方針:

```text
phase == preflop
seat == 1
detected_action == CHECK
max_bet > hero_bet
call_amount = max_bet - hero_bet > 0
```

この条件を満たす場合、Hero CHECKをCALL call_amountとして正規化する。

注意:

```text
- postflop CHECKは変換しない
- max_bet == hero_bet のCHECKはそのまま
- 1 actionにつきログは1回だけ
```

---

### 3.8 HUDちらつき抑制

Fix85で実装済み。

問題:

```text
deep-SPR flop Solver中に
SOLVER_START_SUPPRESSED
SOLVER_HUD_RUNNING_DETAIL
が毎frame出て、HUD文字がちらついていた。
```

現在方針:

```text
- 同一request_id / phase / messageのSolver running HUDは再通知しない
- HUD側も同一computing messageを再描画しない
- SOLVER_START_SUPPRESSEDの同一key連続INFOログは3秒以内は間引く
```

---

### 3.9 hand start直後FOLD表示抑制

Fix85で実装済み。

問題:

```text
New hand started
↓
相手Fold badge残像を検出
↓
相手seatをFOLD扱い
↓
active_player_count=1
↓
position計算不能
↓
preflop fallback FOLD
↓
HUDに一瞬FOLDが出る
```

現在方針:

```text
- hand start直後は相手Fold badge由来FOLDを抑制
- participant observation中も相手Fold badge由来FOLDを抑制
- guard終了後のFold badgeは従来通り処理
- active_player_count < 2 / hero_position None ではpreflop推奨を出さない
- 新hand開始時は前hand推奨をクリアしてWAITING FOR STABLE HAND表示
```

---

## 4. 現在の既知課題

### 4.1 deep-SPR flop Solver timeout

現象:

```text
flopだけ遅い / timeoutしやすい。
turn / riverはかなり速い。
```

有力原因:

```text
- deep-SPR flop treeが大きい
- 60%,a のall-in候補がtreeを膨らませている可能性
- flopはturn/river全分岐を含むため重い
```

現在状態:

```text
- compare_no_allin request保存中
- 本番requestは未変更
- 次回以降、新しいdebug/solver_io JSONで比較確認が必要
```

やるべきこと:

```text
- 本番requestとcompare_no_allin requestを比較
- 可能なら単体CLIで60%,a vs 60%の速度比較
- 勝率影響を考慮して条件付きall-in候補化を判断
```

---

### 4.2 ログ量と本流からの逸脱

現象:

```text
局所guardと検証ログが増え、通常運用ログが読みづらい。
```

現在方針:

```text
- 通常運用ログと検証ログを分ける
- 毎frame級ログはDEBUGへ落とす
- 初回・状態変化・一定時間経過時のみINFO
- 重要状態変化ログは維持
```

今後の注意:

```text
ログ追加だけで問題を解決した気にならない。
ログは判断品質を上げるためのものに限定する。
```

---

### 4.3 CoinPoker依存の整理

現状:

```text
Fold badge / PRE-HAND / obstruction / card animation / dealer OCRなど、
CoinPoker固有の現象に対するguardがGameLoop / HandManagerに増えている。
```

今後:

```text
- Site Adapter層を定義する
- profile / recognition / animation guardを分離する
- GameState以降はサイト非依存を目指す
```

---

### 4.4 Heroカード不安定

継続監視対象。

現状:

```text
- waiting中Heroカードは連続一致確認
- active hand中のHeroカード矛盾は複数frame確認
- 矛盾確定時はhand abandon
- DB/replay保存除外
```

注意:

```text
Heroカードは勝率判断の最重要入力。
怪しい場合は推奨を出さない。
```

---

### 4.5 range推定

現状:

```text
HU postflopではbaseline rangeを使用
LLM range_estimationはリアルタイムでは呼ばない
preflop_scenarioに応じてrange_oop/range_ipを決める
```

課題:

```text
- preflop_scenarioが誤るとSolver rangeも誤る
- normalized_preflop_actionsとrange_sourceをdebug JSONに保存済み
- 今後はrange選択の品質検証が必要
```

---

## 5. 次にやること

### 5.1 最優先: 書類更新

現在は実装ではなく、書類更新フェーズ。

対象:

```text
SPEC.md
DESIGN_NOTES.md
snapshot.md
```

目的:

```text
- Fix85後の正仕様に更新
- Solver process reset方針を旧記述から更新
- Site Adapter / GameState / Decision Engine / HUD の層分離を明記
- deep-SPR flop compare_no_allin方針を明記
- 勝てる推奨サインを最優先にする設計へ戻す
```

---

### 5.2 書類更新後の次ライブ確認

Fix85後のライブ確認項目:

```text
1. hand start直後に一瞬FOLDが出ないか
2. deep-SPR flop Solver中にHUDがちらつかないか
3. SOLVER_START_SUPPRESSED / SOLVER_HUD_RUNNING_DETAIL が連続INFOで出ないか
4. preflopでactive_player_count < 2 / hero_position None時にFOLD推奨が出ないか
5. PREFLOP_HERO_CHECK_NORMALIZED_TO_CALL が1 actionにつき1回だけか
6. debug/solver_ioに本番requestとcompare_no_allin requestが保存されるか
7. compare_no_allinがSolverへ送られていないか
```

---

### 5.3 ライブ後の判断

ライブ後、いきなり実装指示を出さない。

まず以下を分類する。

```text
- 問題はSite Adapter層か
- GameState層か
- Decision Engine層か
- HUD層か
- CoinPoker固有か
- 汎用ロジックにすべきか
- 勝率・判断品質に関係するか
```

分類後、必要ならBuilder指示書を出す。

---

## 6. Git / テスト状態

最新commit:

```text
e250b99 修正: Solver中HUDちらつきとhand開始直後FOLD表示を抑制
```

最新テスト:

```text
python -m pytest tests/test_game_loop.py -q -> 244 passed
python -m pytest tests/test_hand_manager.py -q -> 190 passed
python -m pytest tests/test_hud_overlay.py -q -> 22 passed
python -m pytest -q -> 1282 passed, 7 warnings
```

作業ツリーには、過去から以下が残ることがある。

```text
data/poker_assistant.db-shm
data/poker_assistant.db-wal
.test_tmp/
debug/solver_io/
```

これらは通常、commit対象外。

---

## 7. 次回セッション最初の手順

### 7.1 ユーザー側で実行するGit更新

次回セッション開始時、ユーザーにはまず以下のみ案内する。

```powershell
git pull origin main
```

その後、必要に応じて最新commitを確認する。

期待最新commit:

```text
e250b99 修正: Solver中HUDちらつきとhand開始直後FOLD表示を抑制
```

### 7.2 Commander側の進め方

```text
1. SPEC.md / DESIGN_NOTES.md / snapshot.md の更新状態を確認
2. GitHub main が e250b99 以降であることを確認
3. 追加実装ではなく、まずライブテスト方針を確認
4. ログが来たら層分類して分析
5. ユーザーが「指示書を出して」と明示するまでBuilder指示書は出さない
```

---

## 8. ユーザー要望・進行ルール

```text
- 説明は短く、結論→原因→次にやること
- 冗長な長文説明は避ける
- snapshot / SPEC / DESIGN_NOTESは次セッションが再開できる粒度で詳細に書く
- Builder指示書は、ユーザーが「指示書を出して」と明示するまで出さない
- コード調査が必要な場合、BuilderではなくCommanderがGitHubを先に確認する
- GitHub調査前には必要ならgit更新コマンドだけ提示する
- 実装完了後はcommit / pushまで行う
- ユーザーが手動編集する書類差し替え本文は、必ずコードブロックで出す
```

---

## 9. 禁止事項・維持事項

```text
- Solver中もGameLoopを止めない
- 古いSolver / fallback / LLM結果を表示しない
- 暫定推奨を出さない
- timeoutをRecommendationとして保存しない
- solver_input_unstableをRecommendationとして保存しない
- Hero Fold badgeを完全無効化しない
- hand start直後の相手Fold badgeを即FOLD確定しない
- Hero通常actionはturn boundary由来を正規保存する
- frame由来Hero CHECK/CALL/BET/RAISE/ALL_INをstreet actionへ直接保存しない
- cards_visibleとin_current_handを同一視しない
- UI表示補正のためにGameState本体を書き換えない
- phase fast-forwardは残すが、hand_end直後・stale解除直後は抑制する
- Multiway LLMにはpot odds / required equity / current_street_actionsを渡す
- LLM foldは数理ガードで検証する
- strict_json=true運用を安易にfalseへ逃がさない
- GUI WorkerとGameLoop.startの処理順を再び分岐させない
- seat=0 actionを保存しない
- suspicious金額をALL_IN再分類に使わない
- 大型BET/ALL-INを一律除外しない
- actionだけ除外してpot/max_betだけ巨大値を残す矛盾を作らない
- CoinPoker固有処理をDecision Engineへ直接混ぜない
- 局所guardを無制限に増やさない
```

---

## Temporary Diagnostic Tool

一時診断ツール:
- `scripts/compare_solver_requests.py`
- 目的: deep-SPR flopのprimary requestとcompare_no_allin requestの速度差確認
- 本番GameLoop / RecommendationEngine / Solverルーティングからは呼ばない
- CLIで手動実行したときだけ動く
- Solver速度調査完了後、削除またはdiagnostics用として残すか判断する
