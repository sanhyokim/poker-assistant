# Commander Snapshot

## Latest diagnostic note: 2026-05-19

- teacher standard は候補BETサイズが広すぎて3件すべて失敗
- 次は teacher_narrow を追加
- teacher_narrow:
  - max_iterations=500
  - target_exploitability_pct=0.4
  - timeout_ms=180000
  - bet_sizes=60%,a
  - raise_sizes=2.5x
- 目的:
  - まず現状primaryと同じBET候補で、より高精度なteacherが作れるか確認
- teacher_narrow 実行結果:
  - samples=3 / success_count=0 / error_count=3
  - hand_000004_req_000004_flop: 180秒timeout
  - hand_000006_req_000007_flop: 180秒timeout
  - hand_000016_req_000011_flop: 180秒timeout
  - BET候補をprimary同等に戻してもteacher作成は成功せず
- teacher standard / narrow は失敗
- 次は teacher_300_plus を検証
- teacher_300_plus:
  - max_iterations=300
  - target_exploitability_pct=0.6
  - timeout_ms=180000
  - bet_sizes=50%,60%,75%,a
  - raise_sizes=2.5x
- 目的:
  - 現状deep-SPR primaryの精度水準を維持したまま、BET候補だけ増やして完走するか確認
- teacher_300_plus 実行結果:
  - samples=3 / success_count=0 / error_count=3
  - hand_000004_req_000004_flop: 180秒timeout
  - hand_000006_req_000007_flop: Solver process closed stdout
  - hand_000016_req_000011_flop: 180秒timeout
  - 現状primary精度水準でもBET候補追加だけでteacher作成は成功せず
- HU deep-SPR flop LLM診断方針:
  - 本番採用ではない
  - 現状deep-SPR primaryを教師基準にする
  - まずaction方向が近いかを確認する
  - BETサイズ拡張は二段階目
  - dangerous flip / legal action違反 / 15秒以内率を見る
- LLM診断実行結果:
  - samples=3 / success_count=0 / error_count=3
  - error=OPENROUTER_API_KEY missing
  - baseline primary Solver結果は保存済み
  - APIキー設定後に同じ `--llm-dir` 診断を再実行する
- LLM診断の.env読み込み:
  - compare_solver_requests.py はこれまで os.getenv() のみ参照していた
  - PowerShell環境変数にOPENROUTER_API_KEYがない場合、.envに設定済みでも
    LLM診断が OPENROUTER_API_KEY missing になっていた
  - 診断スクリプト内で repo root の .env を読み込む処理を追加
  - 既存の環境変数は .env で上書きしない
  - 再実行結果:
    - OPENROUTER_API_KEY missing は解消
    - OpenRouter応答は HTTP Error 401: Unauthorized
    - 次は .env のキー値またはOpenRouter側認証状態を確認する
- LLM診断mode修正方針:
  - 既存本番LLMPipelineではOpenRouterをrequests.postで呼び出しており、
    環境変数のprovider設定を読んでいる
  - 診断スクリプトだけが独自urllib実装になっていたため、本番LLM経路と差異があった
  - compare_solver_requests.py の LLM診断呼び出しを既存LLMPipeline互換の
    requests.post方式へ修正
  - OPENROUTER_REQUIRE_PARAMETERS=false を尊重する
  - HTTPエラー時はstatus codeとresponse bodyを保存する
  - 再実行結果:
    - status_code=401
    - response_body={"error":{"message":"User not found.","code":401}}
    - `.env` 読み込みとHTTP body保存は機能しているため、次はOpenRouterキー自体を確認する

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
- 2026-05-19: compare_no_allin検証では速度改善なし
  - hand5: primary 21614ms / compare 21536ms / speedup 1.004
  - hand6: primary 28173ms / compare 29770ms / speedup 0.946
- 次の検証対象: deep_spr_light_probe相当の軽量request
  - max_iterations=80
  - target_exploitability_pct=1.5
  - flop/turn bet_sizes=50%
- light_probe検証結果:
  - hand5: primary CHECK 63.5% / BET 36.5%、light BET 58.3% / CHECK 41.7%
  - hand6: primary CHECK 60.2% / BET 36.1% / ALL-IN 3.7%、light BET 60.4% / CHECK 39.6%
  - lightは速度改善するが、primaryのCHECK優勢をBET優勢へ反転させるため、そのまま本番採用は危険
- 次の検証対象: middle_probe
  - max_iterations=150
  - target_exploitability_pct=1.0
  - flop/turn bet_sizes=60%
  - 目的: primaryに近い判断を保ちながら速度改善できるか確認
- middle_probe検証結果:
  - hand5: primary 21686ms CHECK、middle 13328ms CHECK、speedup 1.627
  - hand6: primary 29616ms CHECK、middle 18372ms BET 139、speedup 1.612
  - middleはhand5では良好だが、hand6では15秒を超え、かつBETへ反転
- 次の検証対象: fast_middle_probe
  - timeout_ms=15000
  - max_iterations=120
  - target_exploitability_pct=1.2
  - flop/turn bet_sizes=60%
  - 目的: 15秒以内でprimaryに近い判断を維持できるか確認
- Solver性能検証:
  - 個別2件の比較では判断できないため、HU flop request一括比較へ移行
  - `scripts/compare_solver_requests.py` に batch mode を追加
  - 対象: `debug/solver_io/20260519/hand_*_flop.json`
  - 除外: compare_no_allin / light / middle / fast_middle 派生request
  - 集計項目:
    - 15秒以内率
    - primaryとのaction一致率
    - primaryとのamount一致率
    - CHECK→BET反転率
    - timeout/error件数
  - 2026-05-19 batch実行結果:
    - total_primary_files=12 / compared=10 / skipped_missing_compare=2
    - PRIMARY: avg=23076ms / median=22278ms / under_15s=0.0% / errors=0
    - COMPARE: avg=23270ms / median=21906ms / under_15s=0.0% / action_match=50.0% / errors=1
    - LIGHT: avg=8690ms / median=7494ms / under_15s=90.0% / action_match=20.0% / CHECK→BET=7
    - MIDDLE: avg=15275ms / median=14293ms / under_15s=70.0% / action_match=60.0% / CHECK→BET=3
    - FAST_MIDDLE: avg=17939ms / median=17016ms / under_15s=0.0% / action_match=70.0% / CHECK→BET=2
- 2026-05-19:
  - batch比較ではMIDDLEが最有力だが、action一致率だけでは判断不可
  - primary probability marginを集計し、僅差不一致と明確な反転を分ける
  - 採用候補条件:
    - 15秒以内
    - primary action一致
    - またはprimary top_margin <= 0.10 の僅差不一致
  - 不採用寄り条件:
    - primary top_margin >= 0.20 でCHECK→BET
    - primary CALL/RAISEをFOLDへ反転
  - margin付きbatch実行結果:
    - total_primary_files=12 / compared=10 / skipped_missing_compare=2
    - PRIMARY: avg=23100ms / median=21779ms / under_15s=0.0% / errors=1
    - COMPARE: action_match=50.0% / near_tie_mismatch=2 / dangerous_flip=2 / CHECK→BET=2
    - LIGHT: under_15s=90.0% / action_match=10.0% / near_tie_mismatch=1 / dangerous_flip=7 / CHECK→BET=7
    - MIDDLE: under_15s=70.0% / action_match=50.0% / near_tie_mismatch=1 / dangerous_flip=3 / CHECK→BET=3
    - FAST_MIDDLE: under_15s=0.0% / action_match=60.0% / near_tie_mismatch=1 / dangerous_flip=2 / CHECK→BET=2
- HU deep-SPR flop最適化方針:
  - turn / LLMにはまだ進まない
  - 現状deep-SPR flop primaryを基準に、grid探索で15秒以内かつ整合性の高い設定を探す
  - max_iterations / target_exploitability_pct / bet_sizes / all-in候補有無を比較
  - 低精度・高速側から試し、20秒超え枝はpruningする
  - 本番設定は変更しない
  - 代表3件grid実行結果:
    - samples=3 / planned=504 / executed=36 / skipped_by_pruning=468
    - 上位scoreは `iter150_target0_9_bets60_allin`,
      `iter150_target1_0_bets60_allin`,
      `iter150_target1_2_bets60_allin`
    - 上位3profileはいずれも action_match_rate=100.0% / dangerous_flip=0
    - ただし avg_elapsed_ms は約23.7秒〜24.0秒で under_15s_rate=0.0%
    - 15秒以内候補は代表3件gridでは見つからず
- all-in候補について:
  - grid結果ではall-in候補を外しても速度改善はほぼ見られない
  - all-in候補ありの方が現状primaryと整合するケースがある
  - 現時点ではflop deep-SPR primaryの `60%,a` は維持方針
- 次の検証:
  - 同一deep-SPR flop primary requestを複数回実行し、Solver出力の再現性を確認する
  - repeatability実行結果:
    - samples=3 / repeat_count=5 / unstable_sample_count=1
    - hand_000004_req_000004_flop: action_stable=True / action_set=[CHECK] / elapsed_spread_ms=715
    - hand_000006_req_000007_flop: action_stable=True / action_set=[CHECK] / elapsed_spread_ms=2041
    - hand_000016_req_000011_flop: action_stable=False / action_set=[FOLD, RAISE] / elapsed_spread_ms=601
    - hand_000016_req_000011_flop は top_margin_range=[0.010, 0.012] の極端な僅差でactionが揺れている
- Solver process再利用調査:
  - PostflopSolverBridgeは常駐CLI設計
  - ただし診断スクリプトでは毎回bridge生成/stopしていたため、起動コスト込みの可能性あり
  - resident modeで start_ms / solve_ms を分離して確認する
  - resident timing実行結果:
    - samples=3 / repeat_count=5 / start_ms=6
    - avg_resident_solve_ms=24328 / process_reuse_effective_count=0
    - hand_000004_req_000004_flop: avg_solve_ms=21730 / action_stable=True
    - hand_000006_req_000007_flop: avg_solve_ms=28664 / action_stable=True
    - hand_000016_req_000011_flop: avg_solve_ms=22590 / action_stable=True
  - residentでも20秒超えのため、遅さの主因はprocess起動ではなくsolve本体
- HU deep-SPR flop teacherデータ作成方針:
  - 本番速度用ではなく、LLM整合性検証用の高精度基準データ
  - 現状primaryより候補BETサイズを増やし、iterationsを増やし、exploitability目標を厳しくする
  - standard:
    - max_iterations=500
    - target_exploitability_pct=0.4
    - timeout_ms=90000
    - bet_sizes=33%,50%,60%,75%,a
    - raise_sizes=2.5x
  - high:
    - max_iterations=800
    - target_exploitability_pct=0.3
    - timeout_ms=120000
    - bet_sizes=25%,33%,50%,60%,75%,a
    - raise_sizes=2.5x,3.5x
  - 本番設定は変更しない
  - standard teacher実行結果:
    - samples=3 / success_count=0 / error_count=3
    - hand_000004_req_000004_flop: 120秒timeout
    - hand_000006_req_000007_flop: Solver process closed stdout
    - hand_000016_req_000011_flop: Solver process closed stdout
  - 現profileではteacherデータ作成に失敗。より狭い候補や長時間実行方針の再検討が必要

## 2026-05-20: Phase 86-Fix8 Task 12-A — LLM診断margin補正

- 目的: near_tie spotでLLMがconfidence=highやclear/dominant等の過剰表現を出す問題を検出する
- 変更ファイル:
  - `scripts/compare_solver_requests.py`
  - `tests/test_compare_solver_requests.py`
  - `docs/snapshot.md`
- 触らない: core/game_loop.py, strategy/*, solver/*, config.yaml
- 実装内容:
  - `_margin_class()`: top_margin → clear(>=0.20) / moderate(>0.10) / near_tie / unknown に分類
  - `build_llm_flop_prompt()`:
    - Context JSONに primary_top_margin, primary_margin_class, primary_second_action, primary_second_probability を追加
    - promptにmargin別ルールを追加（near_tieではconfidence=low/mediumのみ、clear/dominant等の表現禁止）
  - `evaluate_llm_decision()`:
    - primary_margin_class, confidence_overstated, reason_overclaim を追加
    - confidence_overstated: near_tie かつ llm_confidence=high
    - reason_overclaim: near_tie かつ reasonに clear / strongly prefers / dominant / obvious / 明確 / 強い / 優勢 を含む
  - `build_llm_diagnostic_summary()`: confidence_overstated_count, reason_overclaim_count を集計
  - `print_llm_summary()`: 新集計カウントを表示
- テスト追加:
  - test_llm_prompt_includes_margin_class
  - test_evaluate_llm_near_tie_high_confidence_flags_overstatement
  - test_evaluate_llm_near_tie_medium_confidence_no_overstatement

## Phase 86-Fix8 Task 12-A2 — LLM診断CLIの.env優先化

背景:
- Task 12-Aの実装は tests/test_compare_solver_requests.py で 44 passed。
- margin補正関連の primary_margin_class / confidence_overstated / reason_overclaim は実装済み。
- しかし実データLLM再実行では、PowerShell側に残った古い OPENROUTER_API_KEY が優先され、HTTP 401 User not found になった。
- 原因は load_env_file() が os.environ.setdefault() を使っており、既存環境変数を上書きしなかったため。

対応:
- load_env_file(env_path=None, *, override=False) に変更。
- CLI main() 実行時は load_env_file(override=True) とし、診断スクリプトでは repo root の .env を強制優先する。
- 直接関数利用時のデフォルトは override=False のまま維持。
- 本番 GameLoop / RecommendationEngine / LLMPipeline は変更しない。

## Phase 86-Fix8 Task 13-A — HU flop LLM診断のreason_overclaim誤検出抑制

背景:
- HU flop全12 sample診断は action_match_rate=100.0%、direction_match_rate=100.0%、dangerous_flip_count=0、legal_action_invalid_count=0。
- 一方で reason_overclaim_count=2 が残った。
- 対象2件はいずれも near_tie で confidence=low、LLM action は primary Solver と一致していた。

精査:
- hand_000016_req_000011_flop: "not because it clearly dominates" に含まれる clearly/dominates を誤検出。
- hand_000020_req_000017_flop: "rather than treating it as dominant" および再実行時の "not a clear or dominant solver result" を誤検出。
- どちらも過剰主張ではなく、near-tieを抑制的に説明する否定文だった。

対応:
- reason_overclaim 判定を _reason_overclaims_near_tie() に分離。
- not clear / not clearly / not a clear or dominant / does not dominate / rather than treating it as dominant などの否定パターンを除外してから、clear / strongly prefers / dominant / obvious を検出する。
- test_reason_overclaim_ignores_negated_clear_language を追加し、否定文はFalse、肯定的な "Fold clearly dominates this node." はTrueのまま確認。

再確認:
- tests/test_compare_solver_requests.py: 46 passed。
- 全HU flop LLM診断: total_samples=12 / success_count=12 / under_15s_rate=100.0% / action_match_rate=100.0% / direction_match_rate=100.0% / dangerous_flip_count=0 / legal_action_invalid_count=0 / confidence_overstated_count=0 / reason_overclaim_count=0。
- 本番 GameLoop / RecommendationEngine / LLMPipeline は変更しない。

## Phase 86-Fix8 Task 14 — HU flop single-size Solver診断

背景:
- 現状deep-SPR primaryは主に60%,a候補で判断している。
- 60%でCHECKでも、33%や50%ならBETできるspotがある可能性がある。
- 複数サイズ同時Solverは重すぎて失敗している。
- そのため、単一サイズごとにSolverを回し、サイズ別の増額可否を診断する。

目的:
- LLM sizing拡張前に、33/50/60/75/all-inそれぞれでSolverがBET/RAISE/ALL_IN方向を許容するか確認する。
- 本番設定は変更しない。
- HU flop限定。
- multiway / turn / river には触らない。

## Phase 86-Fix8 Task 14-C — HU flop sizing teacher label作成

背景:
- timeout 180秒のsingle-size Solver診断では全60runが成功。
- 33/50/60/75/all-inでaction差が出た。
- 現Solverの60%,aだけでは小さめBET/RAISEの可能性を取り逃がす。
- LLM sizing診断前に、single-size結果からteacher labelを作る。

目的:
- LLMのsizing出力とteacher labelの相関を見る準備。
- 本番実装はまだしない。
- multiway / turn / river は対象外。
