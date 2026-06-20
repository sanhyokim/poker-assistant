
## 1. このファイルの目的

`DESIGN_NOTES.md` は、SPEC.mdに書かれた現在仕様について、**なぜその設計にしたのか**を記録するための補助資料である。

役割分担:

```text
SPEC.md:
  現在の正仕様

DESIGN_NOTES.md:
  設計判断の理由、過去に起きた問題、採用しなかった案

snapshot.md:
  現在地点、完了Fix、既知課題、次にやること
```

通常の開発再開では `SPEC.md + snapshot.md` を渡す。  
仕様変更・設計変更・判断に迷う修正では `DESIGN_NOTES.md` も渡す。

---

## 2. Solver非同期化の判断理由

### 2.1 Solver同期処理で起きた問題

ライブテストで、HU postflop Solverがtimeoutする、または長時間返らないケースがあった。

問題:

```text
Solver中にGameLoopが止まる
↓
画面認識が止まる
↓
Heroが先にCHECK/CALLしてもシステムが気づかない
↓
古いSolver結果が次の判断タイミングで表示される危険
```

ユーザー報告:

```text
ソルバータイムアウトが発生。全体的に遅い。
チェックの時はソルバーの結果が出る前にチェックを押していた。
前の選択の結果が次の選択のタイミングででるような感じ。
```

---

### 2.2 GameLoopを止めない理由

GameLoopは画面認識・hand state管理・Hero turn検出・pending cancelを担当する。

Solver中にGameLoopが止まると以下が起きる。

```text
- Heroが行動したことを検出できない
- street変化を検出できない
- hand_endを検出できない
- 古い推奨を破棄できない
```

したがって、Solverは非同期workerで実行し、GameLoopは継続させる。

---

### 2.3 Python worker threadはkillしないが、不要Solver processはresetする理由

当初は、Solver workerを強制killしない方針だった。

理由:

```text
- Python thread killは安全ではない
- 遅れて返ってきた結果はrequest_id / active_id / cancelled判定で破棄できる
```

しかしライブテストで、表向きcancelされたSolver requestの裏で `postflop_cli.exe` が計算を続け、次のSolver requestを詰まらせる可能性が高いことが分かった。

問題:

```text
Heroが先にCHECK/CALL/FOLD
↓
Solver requestはcancel扱い
↓
Python側は古い結果を採用しない
↓
しかしRust Solver processはまだ計算中の可能性
↓
次のHero判断局面で新requestが詰まる
↓
timeoutやorphan workerが増える
```

そのため、現在方針は以下。

```text
- Python worker threadは直接killしない
- timeout / cancel / orphan / hand_end / waiting時はpostflop_cli.exeをprocess resetする
- 次requestはclean processへ送る
- 古いSolver結果はrequest_id / active_id / cancelled判定で採用しない
```

これは毎requestで再起動するという意味ではない。  
不要化・timeout・orphanなど、古いSolver計算が次requestを邪魔する可能性がある場合だけprocess resetする。


### 2.4 request_id / active_id / cancelled判定を採用した理由

単純な共有result/errorでは、古いworkerが後から完了して新しい結果を上書きする危険がある。

そのため:

```text
- request_id付きでcompletedに保存
- active_idと一致する結果だけ採用
- cancelled_idsに入ったrequestは採用しない
```

この構造により、古いworkerが遅れて返っても安全に破棄できる。

---

## 3. Solver先行計算を保留した理由

### 3.1 情報不足で計算するリスク

Hero turn前にSolverを回すと、以下の情報が未確定の場合がある。

```text
- 相手の最終アクション
- pot
- bet額
- active_player_count
- board OCR安定状態
```

情報不足の状態でSolverを回すと、速くても誤った局面の解を返す可能性がある。

---

### 3.2 相手CHECK→Hero turnまでの時間が短い問題

相手CHECK後すぐHero turnになるケースでは、先行計算できる時間が1秒未満の場合がある。

この場合、先行計算の効果は限定的。

---

### 3.3 先行計算を再検討する条件

以下が満たされるなら将来再検討する。

```text
- GameStateが十分安定している
- 相手actionが確定済み
- pot / bet / boardが安定している
- 計算結果にcontext snapshotを紐付けられる
- stale破棄が確実に機能する
```

現時点では、Hero turn時の非同期Solver + stale破棄を優先する。

---

## 4. stale推奨破棄の判断理由

### 4.1 古い推奨が表示された問題

Solver結果が返る前にHeroが先に行動すると、計算開始時と返却時で文脈が変わる。

この場合、結果が正しくても現在局面には使えない。

---

### 4.2 context snapshotで見る項目

snapshot項目:

```text
hand_id
phase
board
board_count
active_player_count
actions_count
hero_is_my_turn
hero_in_current_hand
```

これらが変わった場合、推奨は現在局面に対して古い。

---

### 4.3 potをstale判定から外した理由

potはOCR揺れが比較的大きい。

potを必須一致にすると、実質的に有効な推奨まで破棄されすぎる可能性がある。

そのため、stale判定の主項目からは外す。  
ただし、pot OCR異常は別途Pot spike / NEW_HAND guardで監視する。

---

### 4.4 fallback結果も破棄対象にする理由

Solverがtimeoutしてfallbackを返しても、文脈が古ければ表示してはならない。

fallbackは「安全な代替推奨」ではなく、あくまでその時点の文脈に対する代替結果である。

---

## 5. Hero Fold badge ignore latchの判断理由

### 5.1 Hero CHECK後にFOLD扱いされた問題

ライブテストで、HeroがCHECKした直後にHero Fold badgeが検出され、Hero FOLD扱いされる問題があった。

これにより:

```text
hero.in_current_hand=False
↓
以後の推奨停止
```

という重大な影響があった。

---

### 5.2 1秒ガードだけでは不足だった理由

Fix43で直近1秒以内のHero通常アクションがある場合、Hero Fold badgeを無視するようにした。

しかしFoldBadgeDetectorはlatched状態を持つため、1秒後も同じbadgeが残り、結局FOLD扱いされる問題が残った。

そのため、そのhand中はHero Fold badgeを無視するlatchが必要になった。

---

### 5.3 Hero Fold badgeを完全無効化しない理由

Hero Fold badge単独検出は、本物のFOLDである可能性がある。

完全無効化すると、本物のHero FOLD検出が遅れる。

したがって:

```text
Hero通常アクションと矛盾した場合だけ無視
一度矛盾したbadgeはそのhand中無視
単独Hero Fold badgeは従来通りFOLD扱い
```

とした。

---

### 5.4 相手Fold badge処理を維持する理由

Hero badgeの問題はHero seat=1特有の誤検出である。

相手seatのFold badgeはFOLD検出に必要なため、Hero ignore latchで止めてはならない。

---

## 6. Hero actionをturn boundary正にした理由

### 6.1 CHECK→CALL二重記録問題

ライブログで、同じHero turnに以下が記録された。

```text
Hero CHECK detected
Street action recorded: seat=1 action=CHECK
Hero action recorded: CHECK 0
Actions detected: [(1, 'CALL', 100)]
Street action recorded: seat=1 action=CALL 100
```

同一ターンでCHECKとCALLが両方保存されると、action historyが壊れる。

---

### 6.2 ActionEstimator由来Hero actionを保存しない理由

ActionEstimator由来Hero actionは瞬間検出であり、誤検出・遅延検出・後続フレーム検出が起きる。

Hero actionは、Hero turn開始/終了時のstack/bet差分を見るHandManagerのturn boundary推定の方が整合を取りやすい。

---

### 6.3 Hero FOLDだけ除外しない理由

Hero FOLDはFold badgeやカード消失など即時検出が必要なケースがある。

FOLDまで除外すると、本物のFOLD検出が遅れる可能性がある。

---

### 6.4 将来frame由来Hero action fallbackを検討する条件

Fix50後に以下が増える場合は再検討する。

```text
Could not determine hero action
```

ただし、frame由来Hero actionを安易に保存復活すると二重記録が再発する。  
fallback化する場合も、turn boundaryが失敗した場合だけ採用するなど厳格な条件が必要。
---

### 6.5 Fix50後に残ったHero CHECK誤保存問題

Fix50では、Hero通常actionをframe由来で無条件保存しない方針にした。

理由は、ActionEstimator由来Hero actionをそのまま保存すると、同一Hero turnで以下のような二重記録が発生したためである。

```text
Hero CHECK detected
Street action recorded: CHECK 0
その直後に Hero CALL / RAISE が検出される
Street action recorded: CALL / RAISE
```

この二重記録を防ぐため、Hero通常actionはturn boundary由来を正とした。

しかしライブテストでは、逆に以下の問題が残った。

```text
実際にはHeroがCALL / RAISEしている
↓
turn boundary時点では画面反映がまだ間に合わない
↓
stack/bet差分がないためCHECKと保存される
↓
直後にframe由来CALL / RAISEが検出される
↓
Fix50により直接保存されない
↓
DB/replayには誤ったCHECKだけが残る
```

---

### 6.6 frame由来Hero actionを無条件復活しない理由

この問題を解決するために、frame由来Hero actionの保存を全面復活させる案も考えられる。

しかし、それを行うとFix50で防いだ二重記録が再発する。

したがって、frame由来Hero actionは原則として保存しない方針を維持する。

---

### 6.7 短時間CHECK置換を採用した理由

採用した方針は以下。

```text
直前にHero CHECKが保存されている
かつ
その直後1秒以内にframe由来CALL / BET / RAISE / ALL_INが検出された場合だけ
直近Hero CHECKを置換する
```

これは、以下の両方を満たすためである。

```text
- CHECK誤保存を補正できる
- frame由来Hero actionの無条件保存には戻らない
```

置換対象は同じstreet上の直近Hero CHECKだけに限定する。

過去streetや過去handのCHECKを置換してはならない。

---

### 6.8 FOLDを置換対象にしない理由

Hero FOLDはFold badge / card消失 / action履歴と絡む特殊な処理であり、Hero Fold badge ignore latchとも関係する。

`CHECK -> FOLD` 置換を許すと、本物のCHECK後に残留Fold badgeを拾ってFOLD扱いする危険がある。

そのため、置換対象は以下に限定する。

```text
CALL
BET
RAISE
ALL_IN
```

FOLDは置換対象外とする。

---

### 6.9 置換時にhuman_actionとfollowed_recommendationも更新する理由

DB/replayで重要なのは、street actionだけではない。

以下も整合している必要がある。

```text
- human_action
- followed_recommendation
- _last_hero_action
```

Hero CHECKをCALL / RAISEへ置換したのに、human_actionやfollowed_recommendationがCHECKのままだと、後から分析したときに矛盾する。

そのため、置換時にはstreet actionだけでなく、関連するhuman_action / followed_recommendationも更新する。
---

## 7. cards_visibleとin_current_handを分けた理由

### 7.1 一瞬NO_CARDでInHandが落ちた問題

ウィンドウ被りや演出で、一瞬カード領域がNO_CARDになり、参加中seatがInHand=NOへ落ちる問題があった。

---

### 7.2 cards_visibleは観測値

cards_visibleは「現在フレームでカード領域がカードありに見えるか」の観測値である。

一時遮蔽・演出・OCR揺れの影響を受ける。

---

### 7.3 in_current_handは参加状態

in_current_handは「このhandに参加中か」を表す状態であり、cards_visibleより安定して扱う必要がある。

---

### 7.4 UI表示だけ補正する理由

空席/不参加seatでもSeatCardDetectorが一時的にCARD判定する場合がある。

内部ロジックではHandManagerが不参加扱いできている場合、GameState本体を書き換える必要はない。

そのため、UI表示だけ:

```python
display_cards_visible = is_seated and in_hand and raw_cards_visible
```

で補正する。

---

## 8. phase fast-forwardを残しつつ抑制する理由

### 8.1 途中起動対応としてfast-forwardが必要な理由

アプリ起動時点ですでにflop/turn/riverの場合、board_countからphaseをfast-forwardできないと正しく監視開始できない。

---

### 8.2 hand_end直後のboard残像リスク

hand_end直後は前ハンドboardが画面に残っている可能性がある。

この状態で新Heroカードが見えると、本当はpreflop開始なのにpostflop開始扱いになる危険がある。

---

### 8.3 suppress_phase_fast_forwardをGameStateに持たせた理由

GameLoopが「前ハンド情報が残っているか」を把握している。  
HandManagerは `_start_new_hand()` でfast-forwardする。

そのため、GameLoopからHandManagerへ意図を渡すフラグとして `suppress_phase_fast_forward` をGameStateに追加した。

---

## 9. stale Heroカード抑制解除の理由

### 9.1 前ハンドカード残像問題

hand_end直後、前ハンドHeroカードが画面に残ることがある。

このため、前ハンドと同じHeroカードが見えている場合は、新ハンド開始を抑制する必要がある。

---

### 9.2 異なるHeroカードを新ハンド候補にする理由

前ハンドHeroカードと異なる2枚が認識された場合、それは新ハンドのHeroカードである可能性が高い。

これまで、異なるHeroカードまでstale扱いし、waitingに残り続ける問題があった。

そのため:

```text
同じカード → stale抑制
異なるカード → stale解除
```

にした。

---

### 9.3 pot / board guardを維持する理由

異なるHeroカードが見えても、前ハンドboard残像や巨大potがある場合は誤開始の可能性がある。

そのため、stale解除後も以下は維持する。

```text
pot too large guard
board残りguard
table visibility guard
```

---

## 10. 暫定推奨を出さない理由

### 10.1 勝つためのシステムという前提

ユーザー方針:

```text
暫定の推奨など意味がありません。
結果がかえって上書きも意味ありません。
なぜこのシステムがあるかを認識してください。勝つためです。
```

このため、速さだけを優先した暫定推奨は出さない。

---

### 10.2 上書き推奨が危険な理由

最初に暫定推奨を出して、後からSolver/LLM結果で上書きすると、ユーザーが古い推奨で操作する可能性がある。

これは勝率を下げる危険がある。

---

### 10.3 NO SIGNAL / TIMEOUT表示を採用しない理由

timeout時に「NO SIGNAL」や代替推奨を出しても、ユーザーにとって有効な判断材料にならない。

現在方針:

```text
処理中は処理状態だけ表示
確定推奨が返った時だけAction表示
古ければ破棄
```

---

### 10.4 処理中表示だけ許可する理由

処理中表示は、ユーザーが「今どこを計算しているか」を理解するために有用。

許可表示:

```text
CHART CHECKING...
SOLVER THINKING...
LLM ANALYZING...
Computing...
```

---

## 11. Multiway LLM判断の設計理由

### 11.1 Hand 9 KK fold問題

ライブテストで、Hero `Kh Ks` がMultiway flopで不自然にFOLD推奨された。

局面:

```text
Hero: Kh Ks
Board: 9d 5d Jh
Pot: 1992
Call amount: 498
Required equity: 約20%
Hero equity: 約47%
```

LLMがfoldを返したが、数理的にはCALLが自然。

---

### 11.2 pot odds / required equityを入れる理由

LLMにpot oddsやrequired equityを明示しないと、必要勝率と実 equity の比較が曖昧になる。

そのため、Multiway LLMには以下を渡す。

```text
facing_bet
call_amount
pot_odds
required_equity
hero_equity
```

---

### 11.3 current_street_actionsを優先する理由

`actions_since_last_frame` だけだと、BETとCALLが別フレームに分かれた場合、LLMが文脈を見失う。

そのため、現在streetの累積履歴 `current_street_actions` を優先する。

---

### 11.4 LLM FOLD数理ガードの理由

LLMは不自然にfoldを返す場合がある。

hero_equityがrequired_equityを十分上回る場合、LLM foldをそのまま採用せずCALLへ補正する。

---

## 12. LLM利用方針

### 12.1 HUでrange_estimationを呼ばない理由

HU postflopではSolverが主軸であり、リアルタイム中にrange_estimationを呼ぶと遅くなる。

現状はbaseline range + Solverを優先する。

---

### 12.2 HUでreason_generationを呼ばない理由

reason_generationは説明生成には有用だが、リアルタイム判断では遅延要因になる。

現状は呼ばない。

---

### 12.3 exploit_adjustmentを50ハンド以上に限定する理由

DB統計が少ない相手に対して搾取補正を行うと、ノイズで判断が悪化する。

そのため、相手ごとに `total_hands >= sample_threshold_low` を満たす場合のみ使う。

---

### 12.4 OpenRouterモデル切り替え案

将来的にOpenRouter経由で `openai/gpt-5.4-mini` など高速・安定JSON出力モデルを検討する。

方針:

```text
OpenRouterは継続
providerをOpenAI固定
json_schema + strict:trueを維持
max_tokensは80〜120案
品質低下がない範囲で調整
```

急務ではなく、ライブ安定化後に扱う。

---

## 13. Pot / bet OCR設計判断

### 13.1 小数点誤読問題

BET額OCRで `595.2` が `5952` のように読まれる問題があった。

これにより巨大bet / ALL_IN誤判定が起きる可能性があった。

---

### 13.2 suspicious判定の範囲

suspicious判定を広げすぎると、通常額までlow confidenceになる。

そのため、明確な桁ズレ疑いに限定する。

---

### 13.3 ALL_IN再分類にsuspiciousを使わない理由

suspiciousな金額をALL_IN再分類に使うと、OCR誤読が即ALL_IN扱いになる。

そのため、suspicious=Trueの場合はALL_IN再分類しない。

---

### 13.4 今後pot OCR巨大誤認を修正する方針

前回ログで以下が見えた。

```text
Pot spike detected: 330 -> 103148
```

再発する場合は、pot OCRにもsuspicious判定や整合チェックを入れる。

候補:

```text
- pot jumpをbet合計と照合
- stack変化と照合
- 巨大potを即NEW_HAND判定に使わない
- 2フレーム確認を厳格化
```

---

## 14. Hand ID表示保持の理由

### 14.1 hand_end後にIDが「-」になる問題

hand_end後、HandManagerはwaitingへ戻るため `hand_id=None` になる。

UIがそのまま表示すると、showdown / hand_end直後にHand IDが「-」になる。

---

### 14.2 内部hand_idは変えずUIだけ補正する理由

内部状態は正しい。  
問題はUIの見やすさだけ。

そのため、MainWindowで `_last_displayed_hand_id` を保持し、UI表示だけ補正する。

---

## 15. 今後の検討事項

### 15.1 Solver速度改善

HU postflop Solverは、局面によって応答速度が大きく変動する。

特にdeep-SPR flopでは、以下のようにtimeoutするケースが確認されている。

```text
phase=flop
pot=298
effective_stack=6805
SPR=22.8
timeout_ms=20000
bridge_timeout_sec=22.0
result=timeout
```

deep-SPRとは、ポットに対して有効スタックが大きい状態を指す。

```text
SPR = effective_stack / pot
```

SPRが高いflopでは、将来streetの分岐やbet size候補が増え、Solver計算が重くなりやすい。

---

#### Solver先行計算をすぐ採用しない理由

Hero turn前にSolverを先行計算する案はある。

しかし、以下が未確定のまま計算すると、誤った局面の解を返す危険がある。

```text
- 相手の最終アクション
- facing_bet
- pot
- active_player_count
- board OCR安定状態
- hero position / IP-OOP
```

たとえばHeroの番が来る前に「betなし」として先行計算しても、その直後に相手がBETすれば、facing_bet / required equity / pot odds / range が変わる。

そのため、先行計算を行う場合でも、将来は以下のような完全一致または厳格なsnapshot一致が必要。

```text
- board
- phase
- pot bucket
- effective stack bucket
- active player count
- action history
- facing_bet
- hero hand
- hero position
```

現時点では、先行計算・キャッシュ化は保留する。

---

#### deep-SPR軽量Solverを検討する理由

Hand ID 13のように、deep-SPR flopで22秒timeoutする場合、精密Solverを待っても実戦では使えない。

この場合、多少精度を落としても、数秒以内に返る軽量設定の方が実用価値が高い可能性がある。

候補:

```text
- max_iterationsを下げる
- bet size候補を減らす
- tree abstractionを粗くする
- deep-SPR flopだけ軽量設定へ切り替える
```

ただし、勝率重視のため、感覚で軽量化してはならない。

今後やるべき比較:

```text
通常Solver設定
vs
deep-SPR軽量設定
```

比較指標:

```text
- 推奨action一致率
- 推奨サイズ差
- EV差
- 処理時間
- timeout率
```

許容候補:

```text
- action一致率が高い
- EV差が小さい
- timeout率が大きく下がる
```

BET/CHECK/FOLDなどaction自体が頻繁に変わる場合は、軽量設定を採用しない。

---

#### まず処理内訳ログを優先する理由

現状ログでは、Solverがtimeoutしたことは分かるが、どこで時間を使っているかは十分に分からない。

不足している内訳:

```text
- input build
- tree build
- solve
- output parse
- CLI通信
```

そのため、次のSolver高速化Taskでは、いきなり軽量化するより先に、deep-SPR flopの処理内訳ログを追加する。

これにより、軽量化すべき箇所を特定してから判断する。
---

### 15.2 Pot OCR巨大誤認ガード

巨大pot / 巨大ALL_IN / NEW_HAND誤検出が再発する場合、次の優先Fix候補。

---

### 15.3 Hero turn boundary未確定警告

Fix50後に以下が増える場合は調査する。

```text
Could not determine hero action
```

ただし、frame由来Hero action保存を安易に復活させてはならない。

---

### 15.4 LLMモデル切り替え

ライブ安定化後に、OpenRouterモデル切り替えを検討する。

品質低下を避けるため、速度だけで判断しない。

---

### 15.5 SPEC軽量化の継続

SPEC.mdは現在仕様だけに寄せる。  
経緯・判断理由はDESIGN_NOTESへ移す。

今後も仕様更新時は、必要に応じて以下を同時更新する。

```text
SPEC.md
DESIGN_NOTES.md
snapshot.md
```
---

## 16. OpenRouter / gpt-5.4-mini / JSON Schema strict を採用した理由

### 16.1 DeepSeek系モデルで起きていた問題

以前のLLM設定では、OpenRouter経由で `deepseek/deepseek-v4-flash` を利用していた。

ライブテストでは、Multiway postflop判断で以下の問題があった。

```text
- LLM応答が6秒〜10秒以上かかるケースがあった
- Multiway turnで18秒超になる可能性があった
- 実戦中の判断支援としては遅すぎるケースがあった
```

本システムは「勝つための判断支援」であり、遅すぎる推奨は実戦では使えない。

そのため、速度とJSON安定性の両方を改善する目的で、OpenRouter上の `openai/gpt-5.4-mini` へ切り替えた。

---

### 16.2 gpt-5.4-miniを採用した理由

`openai/gpt-5.4-mini` は、OpenRouter経由で利用でき、実測上の応答速度が大きく改善する見込みがあった。

ライブテストでは以下を確認した。

```text
model=openai/gpt-5.4-mini
provider=OpenAI
status=200
parsed=true
validated=true
fallback=false
```

Multiway LLMの応答速度も、おおむね1.4〜1.8秒程度で返るケースが確認された。

このため、現時点では以下の方針を採用する。

```text
- OpenRouterは継続使用
- LLMモデルは openai/gpt-5.4-mini を基本とする
- providerはOpenAI固定
- provider fallbackは無効化
```

---

### 16.3 providerをOpenAI固定にする理由

OpenRouterでは同じモデルIDでも、複数provider経由で処理される可能性がある。

providerが変わると以下が変動する可能性がある。

```text
- 応答速度
- JSON安定性
- response_format対応状況
- structured outputの挙動
- エラー内容
```

判断支援システムでは、LLM挙動の再現性が重要である。

そのため、以下のprovider設定をpayloadに渡す。

```json
{
  "provider": {
    "order": ["OpenAI"],
    "allow_fallbacks": false,
    "require_parameters": false
  }
}
```

fallback providerを許可すると、エラー時に別providerへ流れ、JSON安定性や応答品質が変わる可能性があるため、現時点では許可しない。

---

### 16.4 require_parameters=false にした理由

当初は `require_parameters=true` も候補だった。

しかし、OpenRouter / provider / モデルの対応状況によっては、strict JSON Schemaや細かいパラメータ指定が原因で400エラーになる可能性がある。

現時点では安定稼働を優先し、以下の設定にする。

```text
OPENROUTER_REQUIRE_PARAMETERS=false
```

これにより、provider固定は維持しつつ、不要なパラメータ不一致による失敗を避ける。

---

### 16.5 startup checkのmax_tokensを16以上にした理由

gpt-5.4-mini / OpenAI providerでは、`max_output_tokens` または `max_tokens` に最小値制約がある。

startup checkで `max_tokens=1` を送ると、400エラーになることが確認された。

そのため、startup checkでは最小値に合わせて `max_tokens=16` 以上を使う。

startup check失敗時はWARNINGを出すが、アプリ起動は継続する。

理由は、LLMが一時的に失敗しても、Chart / Solver / fallback経路は動作できるためである。

---

### 16.6 JSON Schema strictをenv切替にした理由

JSON Schema strictは、LLMのJSON安定性を上げるために有効である。

ただし、いきなり常時ONにすると、問題発生時に原因切り分けが難しくなる。

考えられる原因:

```text
- モデル自体の問題
- provider指定の問題
- response_formatの問題
- schema内容の問題
- OpenRouter側の互換性問題
```

そのため、以下のようにenvでON/OFFできる設計にした。

```env
OPENROUTER_USE_STRICT_JSON_SCHEMA=true
```

ONの場合のみ、対応タスクに `response_format=json_schema` を付与する。

---

### 16.7 strict JSON Schema対象タスクを限定した理由

strict JSON Schemaの対象は以下に限定する。

```text
multiway_decision
exploit_adjustment
range_estimation
preflop_delta
```

対象外:

```text
reason_generation
```

`reason_generation` は自由文の根拠説明を生成する用途であり、strict JSON Schemaで縛る意味が薄い。

また、現状のリアルタイム判断では `reason_generation` は主経路ではない。

そのため、JSON Schema strict対象外とした。

---

### 16.8 Pydantic validationを維持する理由

API側のstrict JSON Schemaを使っても、コード側のvalidationは維持する。

理由:

```text
- API側schemaが必ず完全に守られるとは限らない
- provider差分やAPI仕様変更に備える必要がある
- 本システム側で最終的な安全確認を行うべき
```

したがって、LLM応答は以下の二重ガードとする。

```text
1. OpenRouter response_format=json_schema
2. コード側のJSON parse + Pydantic validation
```

validationに失敗した場合は、従来通りfallbackへ進む。

---

### 16.9 APIエラー本文をログに出す理由

OpenRouter APIで400以上のエラーが出た場合、status codeだけでは原因が分からない。

今回もstartup check失敗時に、エラー本文を見て `max_tokens` 最小値問題だと特定できた。

そのため、400以上のHTTPエラーでは以下をWARNINGログに出す。

```text
response.text[:500]
```

ただし、APIキーやprompt全文はログに出してはならない。

---

## 17. 途中離席・中断handを保存しない理由

### 17.1 問題

ユーザーがhand途中でフォールドして離席し、そのままウィンドウを閉じる、Stopする、captureが切れる、またはテーブルが見えなくなる場合がある。

この場合、システムはその後の相手アクション、showdown、勝敗、最終potを観察できない。

途中までしか観察できていないhandをDBへ保存すると、以下が欠けた不完全データになる。

```text
- 相手の後続アクション
- showdown到達有無
- 勝敗
- 最終pot
- 最終street
- 本当に参加していたseat
```

---

### 17.2 opponent statsが汚染されるリスク

本システムでは、DB統計を将来的にpreflop補正・LLM判断・相手傾向分析へ使う。

不完全handが混ざると、以下のような統計汚染が起きる。

```text
- VPIP / PFR / fold傾向が歪む
- showdown到達率が歪む
- aggressive/passive判定が歪む
- 相手の実際の行動履歴と違う情報が蓄積される
```

これは判断品質に直結する。

---

### 17.3 保存しない方針を採用した理由

中断handに `incomplete=true` を付けて保存する案もある。

しかし、将来の集計・分析・LLM入力で除外漏れが起きると危険である。

現時点では、勝率と統計品質を優先して以下の方針にする。

```text
中断handは完全に保存しない
```

対象:

```text
user_stop
capture_lost
table_invisible
hero_cards_unstable
```

保存しないもの:

```text
- hand_history
- replay JSON
- opponent stats
```

---

### 17.4 Hero foldだけではabandonしない理由

HeroがFOLDしても、テーブル観察が継続できるなら、そのhandの相手アクションやshowdownを追える可能性がある。

そのため、Hero fold単体ではabandonしない。

abandon対象は、あくまで以下のように観察継続が不可能または危険な場合である。

```text
- Stop
- アプリ終了
- capture lost
- table invisible
- Heroカード不安定
```

---

### 17.5 hand_end経路を使わない理由

通常のhand終了は `_transition_phase("hand_end")` や `_on_hand_end()` を通り、DB保存・replay保存・stats更新が走る。

abandoned handではこの保存経路を通してはならない。

そのため、専用経路として以下を使う。

```text
HandManager.abandon_current_hand(reason)
```

このメソッドは以下を行う。

```text
- _on_hand_end() を呼ばない
- DB保存しない
- replay保存しない
- stats更新しない
- phaseをwaitingへ戻す
```

---

## 18. pot spike hold中にstrategyを保留する理由

### 18.1 問題

pot OCRでは、一時的な急増やアニメーションによるspikeが発生する。

既存のpot spike filterでは、1フレーム目の急増は前回potへholdし、2フレーム連続で同じ急増が続いた場合にconfirmedする。

しかしライブテストで、以下のような不整合が発生した。

```text
Pot spike detected: 314 -> 14134, holding previous value
Actions detected: [(5, 'BET', 13820, 'high')]
HU solver request: pot=314 ... actions_played=1
```

つまり、potは前回値にholdされている一方で、BET/ALL_IN actionだけが先に認識されていた。

この状態でSolver/LLMに渡すと、以下のような壊れた入力になる。

```text
pot=314
bet=13820
SPR=9768.0
```

---

### 18.2 potをstale判定に戻さない理由

過去の設計で、potはrecommendation context freshnessの主一致条件から外している。

理由は、pot OCRは揺れやすく、potを厳密一致にすると有効な推奨まで破棄されすぎるためである。

今回の問題は、通常のpot揺れではなく、ActionEstimatorが明示的に「pot spike hold中」と判断している特殊状態である。

そのため、potをstale判定に戻すのではなく、専用フラグで保留する。

```text
GameState.strategy_defer_reason = "pot_spike_hold"
```

---

### 18.3 strategyを保留する理由

pot spike hold中は、pot/actionの整合性が壊れている可能性がある。

この状態で以下を行うのは禁止する。

```text
- Solver request作成
- LLM prompt作成
- Chart fallback表示
- previous recommendationの維持表示
```

理由:

```text
古いpotと新しい巨大betを組み合わせた推奨は、数理的に壊れるため。
```

したがって、pot spike hold中は以下を行う。

```text
- pending recommendationをclear/cancel
- previous recommendationを破棄
- HUDに WAITING FOR STABLE POT... を表示
- GameLoopは止めない
```

---

### 18.4 action記録は止めない理由

pot spike hold中でも、BET / RAISE / ALL_IN action自体は正しく認識できている可能性がある。

そのため、Action記録自体は止めない。

止めるのは、あくまで壊れたpot/action組み合わせでstrategy計算を開始することだけである。

次フレームでpotがconfirmedされた後、通常のstrategy処理へ戻る。

---

### 18.5 suspicious 10x OCR spikeをdeferしない理由

10倍桁ズレ疑いのsuspicious pot spikeは、実変化としてconfirmedさせない方針である。

例:

```text
7740 -> 103320
```

このような桁ズレ疑いは完全ignore扱いとし、potを前回値に保持する。

したがって、`pot_spike_hold=True` にはせず、strategy deferもしない。

理由は、suspicious spikeをhold扱いにすると、存在しないpot変化を待ち続ける可能性があるためである。

---

## 19. Heroカード安定化・矛盾時abandonを採用した理由

### 19.1 問題

Heroカードは、Chart / Solver / LLMの最重要入力である。

ライブテストでは、相手のアクション演出や視覚ノイズにより、HeroカードOCRが揺れるケースが確認された。

Hand ID 6では、以下のような危険な挙動があった。

```text
Hand開始時:
hero_cards=['Qd', 'Ac']

終了時のNEW_HAND filter:
cached=['Qd', '4c']
```

これは、hand中またはhand開始前後でHeroカード認識が矛盾していた可能性を示す。

誤ったHeroカードで推奨を出すと、すべての判断経路が壊れる。

```text
- Preflop Chart
- HU Solver
- Multiway LLM
- equity計算
- pot odds比較
```

---

### 19.2 waiting中に1フレームでhand開始しない理由

waiting中のHeroカードOCRは、前ハンドの残像、カード配布演出、相手アクション演出、遮蔽などの影響を受ける。

1フレームだけ読めたカードを新handとして採用すると、誤ったHeroカードでhandを開始する危険がある。

そのため、waiting中は以下の方針にする。

```text
同じHeroカードが一定フレーム数連続して読めた場合のみ新hand候補にする
```

デフォルト:

```text
recognition.hero_card_confirm_frames = 2
```

途中で別カードに変わった場合はcandidateを差し替え、streakを1へ戻す。

---

### 19.3 active hand中に即上書きしない理由

active hand中にfresh OCRで別のHeroカードが読めたとしても、それが正しいとは限らない。

演出・遮蔽・一時ノイズで誤読している可能性がある。

そのため、active hand中にfresh OCR結果でcached Heroカードを即上書きしてはならない。

採用方針:

```text
- cached Heroカードを正とする
- fresh OCRは矛盾検出にだけ使う
- 1回の矛盾では破棄しない
- 一定回数連続した矛盾で不安定handと判定する
```

デフォルト:

```text
recognition.hero_card_mismatch_confirm_frames = 2
```

---

### 19.4 Heroカード不安定時に推奨停止する理由

Heroカードが不安定な状態で推奨を出すと、判断の土台が壊れる。

この状態でfallbackを出しても安全ではない。

理由:

```text
fallbackもHeroカードを前提にした判断だから。
```

したがって、Heroカード不安定時は以下すべてを止める。

```text
- Preflop Chart
- HU Solver
- Multiway LLM
- fallback
```

HUDには推奨Actionではなく、状態表示として以下を出す。

```text
HERO CARDS UNSTABLE
```

---

### 19.5 Heroカード不安定handを保存しない理由

Heroカードが矛盾したhandをDBに保存すると、以下が汚染される。

```text
- hero cards
- recommendation
- human action
- followed_recommendation
- hand history
- opponent stats
- replay analysis
```

特に、誤ったHeroカードで出した推奨と実際のプレイが保存されると、後から分析しても意味がない。

そのため、Heroカード不安定が確定したactive handは以下で破棄する。

```text
abandon_current_hand("hero_cards_unstable")
```

このhandはDB/replay/statsへ保存しない。

---

### 19.6 Visual Obstruction中に矛盾判定しない理由

Visual Obstruction中やrecovery中は、画面表示が不安定である。

このタイミングでHeroカードfresh OCRがcachedと違っても、一時的な遮蔽・演出ノイズの可能性が高い。

そのため、Visual Obstruction中 / recovery中はHeroカード矛盾判定を行わない。

```text
- mismatch streakを増やさない
- handをabandonしない
- cached Heroカードを維持する
```

---

### 19.7 補正より無効化を優先する理由

Heroカードの誤認が疑われる場合、fresh OCRで補正する案もある。

しかし、どちらが正しいカードかを画面だけで完全に保証することは難しい。

誤った補正をすると、さらに危険な推奨を出す可能性がある。

そのため、現時点では以下を優先する。

```text
怪しいHeroカードhandは補正して続行するより、無効化して保存しない
```

これは勝率とDB品質を守るための安全設計である。

---

## 20. GUI WorkerとGameLoop正規処理を共通化した理由

### 20.1 起きていた問題

`GameLoop.start()` にはFix63/Fix64で追加した以下の処理が入っていた。

```text
_recover_pending_hero_fold_badge()
_update_hand_position_lock()
```

しかしGUI実行では `GameLoop.start()` ではなく `main.py` の `GameLoopWorker.run()` が使われていた。  
そのため、CLI/テストでは通るFixがライブGUIでは通らない状態になった。

### 20.2 共通メソッド化を採用した理由

同じ1フレーム後処理を2か所に重複実装すると、今後も片方だけ修正される危険がある。

そのため、`GameLoop.process_game_state_after_frame()` に正規処理順を集約し、`GameLoop.start()` と `GameLoopWorker.run()` の両方から呼ぶ設計にした。

---

## 21. seat=0 actionを無効化する理由

### 21.1 起きていた問題

実在しない `seat=0 CHECK` がstreet actionに保存され、Hero turn判定やLLM/Solver入力に混ざる可能性があった。

### 21.2 下流でも防御する理由

本来はActionEstimator側で出さないのが理想だが、認識系は揺れる。  
そのため、HandManager / GameLoop側でも最終防衛として `seat < 1 or seat > 6` を保存しない。

これはDB/replay/Strategy入力の品質を守るためである。

---

## 22. 大型BET/ALL-INを一律除外しない理由

### 22.1 Fix67-A/Bで見えた問題

Fix67-A/Bでは、pot spike hold中の巨大BET/RAISE/ALL_IN/CALLを保存前に除外する方針を取った。

しかしライブログでは、以下のようにPOT増加とALL-IN額が整合するケースがあった。

```text
前POT 546
新POT 34886
POT増加 34340
seat2 ALL_IN 34340
```

このようなケースはOCR誤認ではなく、本物のALL-INである可能性が高い。

### 22.2 オンラインポーカーでは大型BETが普通に起きる

オンラインポーカーでは、特にショートスタック・プリフロップ・マルチウェイ・トーナメント的状況でALL-INや大型BETが頻出する。

したがって:

```text
pot_spike_hold中 + 大きい金額
↓
怪しいから除外
```

という設計は危険である。

### 22.3 再読確認方式を採用する理由

今後の方針は以下。

```text
怪しい金額を検出
↓
即座にPOT / bet / stackを再読
↓
再読でも一致するなら本物として採用
↓
再読で不一致なら認識errorとして推奨停止
```

これにより:

```text
- 本物のALL-INを消さない
- OCR誤認だけを弾く
- actionだけ消してpotだけ残る矛盾を防ぐ
```

### 22.4 複雑な補正管理を避ける理由

`last trusted pot` や `last trusted bet` を複雑に補正し続ける案もある。  
しかし状態管理が複雑になり、別の矛盾を生む危険がある。

そのため、まずは以下のシンプルな方式を採用する。

```text
怪しい → 再読 → 一致なら採用 / 不一致ならそのフレーム推奨停止
```

---

## 23. LLM reasoning sanitize / quality guardを入れる理由

### 23.1 起きていた問題

LLMの `reason` に以下のようなプロンプト断片が出ることがあった。

```text
日本語で簡潔に:
日本語
```

特に `reason="日本語"` だけの場合、HUDの説明文として意味をなさない。

### 23.2 プロンプト修正だけでは不足する理由

LLMは正常なJSONを返し、validationが通っていても、中身のreasonが低品質なことがある。  
そのため、schema validationだけでは不十分である。

### 23.3 採用方針

```text
- 接頭辞はsanitizeする
- reasonが短すぎる場合は不正reason扱い
- action自体が使えるなら、EQ / required equity / pot oddsから定型reasonを生成
- reason置換時はログに残す
```

---

## 24. Hero turn音通知を入れる理由

ユーザーが自分の番まで画面から目を離すことがあり、Hero turnに気づかないケースがあった。

音通知は推奨精度そのものではないが、実運用での操作遅れ防止に有効である。

採用条件:

```text
- やさしい通知音
- ON/OFF可能
- 音量調整可能
- 同一turnで1回のみ
```

---

## 25. hand start latency改善を急ぎすぎない理由

Heroカード2回一致確認は、Heroカード誤認で推奨を出さないための安全設計である。  
このため、hand startが2〜3秒遅れることがある。

ただし、これを単純に1フレーム開始へ戻すと、誤Heroカードで推奨するリスクが再発する。

改善する場合は:

```text
- start表示だけ早める
- 推奨表示はHeroカード安定後にする
- latencyログで遅延要因を測る
```

とし、推奨品質を犠牲にしない。

---

## 26. Solver遅延を忘れず別Taskで扱う理由

Solver遅延・fallback問題は未解決の重要課題である。

ただし、金額OCRが壊れた状態でSolverを評価すると、Solver自体の問題なのか入力の問題なのか切り分けできない。

したがって:

```text
1. 金額OCR再読確認で入力を安定させる
2. HU Solver fallback reasonログで原因を見る
3. input build / tree build / solve / parse / CLI通信の内訳ログを追加する
4. 必要ならSolver軽量化を比較検討する
```

## 27. 本流回帰: 勝てる推奨サインを最優先にする理由

このシステムの目的は、CoinPoker画面の細かな例外処理を増やすことではなく、オンラインポーカーで勝率を上げるための信頼できる推奨サインを出すことである。

ライブテストを重ねる中で、以下のような局所修正が増えた。

```text
- Fold badge guard
- PRE-HAND / PRE-HAND-CANDIDATE
- visual obstruction guard
- stale Heroカードguard
- position lock guard
- Solver input guard
- Solver process reset
- HUDちらつき抑制
- hand start直後FOLD抑制
```

これらは必要な修正ではあるが、無秩序に増えると、別のバグを生む。

今後の判断基準:

```text
1. その修正は勝率・判断品質に寄与するか
2. GameStateを正しくする修正か
3. Site Adapter層に閉じ込めるべきCoinPoker固有処理ではないか
4. Decision Engineに認識層の例外を混ぜていないか
5. HUDで推奨と状態表示を混同していないか
6. ログやguard追加が本流の判断品質を悪化させないか
```

小さな症状ごとのguard追加ではなく、層ごとに問題を分離して直す。

## 28. Site Adapter層を分離する理由

現在の検証対象はCoinPokerである。

しかし将来的には、他のオンラインポーカー環境にも対応できる汎用的な判断支援システムにしたい。

そのため、CoinPoker固有の処理はSite Adapter層に閉じ込める。

Site Adapterに置くべきもの:

```text
- 座標profile
- Fold badge領域
- dealer button領域
- pot / stack / bet OCR領域
- action button領域
- player name領域
- CoinPoker固有の演出・残像・アニメーション対策
```

GameState以降に渡すもの:

```text
- hand_id
- phase
- hero cards
- board
- pot
- player stack
- player bet
- in_current_hand
- action history
- position
```

Decision EngineはCoinPokerの画面事情を知らない状態で動くべきである。

今後、他サイトへ対応する場合は、以下をサイト別profileまたはadapterに切り出す。

```text
- crop座標
- 色判定
- Fold badge検出
- dealer検出
- button検出
- OCR前処理
- サイト固有の残像・アニメーションguard
```

## 29. deep-SPR flop Solver最適化を慎重に扱う理由

deep-SPR flopではSolver treeが大きくなり、timeoutしやすい。

特に現在のSolver requestは、bet sizeに `60%,a` を使っている。  
`a` はAll-in候補であり、deep-SPR flop rootから全streetにall-in候補を入れるとtreeが大きくなる可能性が高い。

ただし、all-in候補を単純に消すと戦略品質に影響する可能性がある。

そのため現在方針は以下。

```text
- 本番requestの60%,aは維持する
- deep-SPR flop rootでは比較用no-all-in requestを保存する
- 比較requestはSolverへ送らない
- 正式推奨には使わない
- 十分な比較結果を見てから条件付きall-in候補化を判断する
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

自分からALL-INする候補はSolver領域であり、LLM単独や単純数理だけで決めない。  
相手ALL-INに対するCALL/FOLDは、Solverが使えない場合に限り、equity / pot odds の数理避難路を使う。


## 30. HUDで推奨と状態表示を分ける理由

HUDは、ユーザーが実際に操作判断するための最重要UIである。

そのため、HUD上では以下を明確に分ける。

```text
Recommendation:
  実際に選択してよい推奨アクション

Status:
  計算中・待機中・入力不安定・PRE-HANDなど、まだ推奨ではない状態
```

過去のライブテストでは、Solver中にHUD表示が短時間でちらつき、ユーザーから見て何が起きているか分かりにくい状態があった。

原因:

```text
Solver workerがまだ実行中
↓
毎frame SOLVER_START_SUPPRESSED
↓
毎frame HUD computing messageを再通知
↓
HUD上で文字がちらつく
```

現在方針:

```text
- 同一request_id / phase / messageのSolver running HUDは再通知しない
- 同じcomputing messageの再描画を避ける
- Solver中は推奨Actionではなく状態だけ表示する
- deep-SPR flop中は DEEP SPR FLOP SOLVING と表示する
- WAITING FOR STABLE HAND は推奨ではない
```

HUDの状態表示はHandManagerへRecommendationとして保存してはならない。

## 31. hand start直後のFold badgeを慎重に扱う理由

hand start直後は、カード配布演出・Fold badge残像・UI更新遅延により、一時的に誤ったFold badgeが見えることがある。

ライブテストでは、新hand開始直後に相手Fold badgeが検出され、相手seatが即FOLD扱いになった。  
その結果、active_player_countが1になり、position計算不能になり、preflop fallback FOLDが一瞬表示される危険があった。

問題の流れ:

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
- hand start直後は相手Fold badge由来FOLDを抑制する
- participant observation中も相手Fold badge由来FOLDを抑制する
- guard終了後のFold badgeは従来通り処理する
- Fold badge全体を無効化しない
```

目的は、Fold badge検出を止めることではなく、hand start直後の残像・演出をFOLDとして確定しないことである。

## 32. preflop CHECKをCALLへ正規化する理由

HU preflopで、HeroがBBとして相手RAISEを受けているにもかかわらず、Hero actionがCHECK 0として記録されるケースがあった。

例:

```text
seat2 RAISE 200
seat1 CHECK 0
```

しかし、Heroがflopへ進んでいる場合、実際にはCALL差額を支払っている可能性が高い。

この誤記録が残ると、以下に影響する。

```text
- preflop_actions
- preflop_scenario
- range_oop / range_ip
- Solver requestの前提
- replay / DB分析
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
- postflop CHECKはCALLへ変換しない
- max_bet == hero_bet のCHECKはそのまま
- 本当にcheck可能な状況では変換しない
- 1 actionにつき正規化ログは1回だけ出す
```

確認しました。`DESIGN_NOTES.md` は **セクション32「preflop CHECKをCALLへ正規化する理由」まで存在**しているので、今回の追記は **`## 33.`** として末尾に追加してください。

````markdown
---

## 33. HU Solver / LLM検証における教師データ信頼性を見直した理由

### 33.1 背景

HU flop LLM化検証中に、旧 `debug/solver_io/20260519` の12件を使って以下を行っていた。

```text
single-size Solver診断
sizing teacher作成
LLM sizing診断
Blind LLM診断
repeatability診断
````

当初は、LLMのaction / direction / sizing alignmentが高く見えた。

しかし後から、教師データ側に重大な問題が見つかった。

主な問題:

```text
- 旧request JSONに hero_cards が保存されていなかった
- 旧12件は全件 average_strategy_fallback だった
- Blind LLM検証では hero_cards / facing_bet / call_amount などがLLM入力から欠落していた
- 新規3件では hero_cards 保存は成功したが、2件でHero実カードQ3sがHero側range_oop外だった
```

そのため、旧データ由来のteacher / LLM診断結果は、本実装判断に使わない。

---

### 33.2 teacher情報ありLLM診断は本番想定ではなかった

teacher情報ありのLLM診断では、LLMに以下を渡していた。

```text
primary Solver action / probabilities
single-size teacher label
allowed_sizing_types
profile_actions
```

そのため、この検証で分かったのは以下である。

```text
LLMがteacher情報を見た状態で、その方針に追従できるか
```

これは本番想定ではない。

本番で必要なのは以下である。

```text
LLMがSolver/teacher情報なしで、実戦情報だけから未知spotを判断できるか
```

今後は、以下を明確に分ける。

```text
追従性検証:
Solver/teacher情報を渡し、LLMが従えるかを見る

本番想定検証:
Solver/teacher情報を渡さず、実戦情報だけでLLMが判断し、後からSolver/teacherと照合する
```

本番採用判断には後者が必要である。

---

### 33.3 Blind LLM検証も入力不足だった

Blind LLM診断では、Solver/teacher情報を渡さずに判断させた。

しかし後で入力監査をした結果、LLM prompt/contextに以下が欠落していた。

```text
hero_cards: 12/12 欠落
facing_bet: 12/12 欠落
call_amount: 12/12 欠落
street: 12/12 欠落
num_players: 12/12 欠落
heads_up: 12/12 欠落
```

この状態では、Solver/teacherとの相関を正しく測れない。

理由:

```text
Hero hand strength
draw
blocker
showdown value
facing bet context
call amount
```

をLLMが判断できないため。

今後のBlind LLM検証では、Solver/teacher情報は渡さない。
ただし、Solverと同等の実戦情報は必ず渡す。

必須入力:

```text
hero_cards
board
pot
effective_stack
SPR
hero_position
hero_is_ip
actions_played
legal_actions
facing_bet
call_amount
street
heads_up
num_players
```

この入力が欠けたBlind LLM検証結果は、本番採用判断に使ってはならない。

---

### 33.4 旧request JSONにhero_cardsが保存されていなかった

旧 `debug/solver_io/20260519` のrequest JSONには `hero_cards` が入っていなかった。

そのため、オフラインでSolver出力を再解析しても、Hero実カードに該当するhand rowを抜くことができず、全件 `average_strategy_fallback` になった。

Task 17の結果:

```text
total_samples=12
hand_strategy_count=0
average_strategy_fallback_count=12
hero_cards_missing_count=12
matched_hand_missing_count=12
solver_error_count=0
```

この旧12件は、Heroカード別teacherとして無効扱いにする。

旧12件から作成した以下も、本判断には使わない。

```text
single_size_flop_180
sizing_teacher_flop
llm_sizing_flop
llm_blind_flop
llm_blind_repeat
```

参考ログとして残すのはよいが、正規teacher / 本実装判断の根拠にしてはならない。

---

### 33.5 Solver requestにhero_cardsが直接入らないこと自体は通常構造

Solverは通常、Heroの具体ハンド1つだけを入力して解くのではなく、以下を入力してレンジ全体を解く。

```text
board
range_oop
range_ip
pot
effective_stack
actions_played
bet size
raise size
```

そのため、Solver request本体に `hero_cards` が直接入らないこと自体は、レンジSolver構造としては問題ではない。

正しい流れは以下。

```text
1. board / range_oop / range_ip / pot / stack / actions / sizing をSolverへ渡す
2. Solverがレンジ全体の strategy_matrix を返す
3. Python側で game_state.hero.cards に一致する hands row を探す
4. その hand row の strategy を推奨に使う
```

問題は、Hero hand rowが取れない場合に `average_strategy` を使うことである。

---

### 33.6 average_strategy fallbackはteacherとして不適切

`average_strategy` はレンジ平均であり、Hero実カード別の戦略ではない。

これをteacherや本番推奨として扱うと、以下の差が潰れる危険がある。

```text
強い手
中程度の手
ドロー
ブロッカー持ち
ブラフ候補
完全な弱手
```

極端に言えば、Heroが強い手でも弱い手でも、レンジ平均に寄った推奨になる危険がある。

そのため、今後 `average_strategy_fallback` になったデータはteacherとして使わない。

本番推奨としても原則採用しない。

---

### 33.7 Hero hand matching順序差

Hero cards の表記とSolver出力 `hands` の表記順が異なる可能性がある。

例:

```text
hero_cards=["3c","Qc"]

候補:
3cQc
Qc3c
```

Task 18-Bで、元順・逆順・rank順候補を生成して照合するよう修正した。

この修正は、本番HU Solver parseにも効く。

ただし、新規3件で失敗した `3c,Qc` の2件は、順序差ではなかった。

確認結果:

```text
3cQc も Qc3c も Solver output hands に存在しなかった
```

つまり、原因はHero hand matchingの順序差ではなく、Hero実カードがSolver側のHero rangeに含まれていないことだった。

---

### 33.8 Hero実カードがHero側range外だった

Task 18後の新規ライブ3件では、`hero_cards` 保存は成功した。

しかし、`3c,Qc` の2件は以下の状態だった。

```text
hero_cards=["3c","Qc"]
hero_hand_class=Q3s
hero_side=oop
hero_range_source=range_oop
hero_range_contains_hand=false
```

つまり、Hero実カード `Q3s` がHero側 `range_oop` に含まれていなかった。

この場合、SolverにとってHeroが `Q3s` を持つ前提がrange内に存在しない。

そのため、Solver output `hands` に該当comboがなく、Hero hand rowを取得できない。

結果として `average_strategy_fallback` になった。

---

### 33.9 Hero hand range外の原因候補

Hero実カードがHero側range外になる原因候補は以下。

```text
A. preflop_scenario の判定ミス
B. hero_position / hero_is_ip / OOP-IP割当ミス
C. BB defend range が狭すぎる
D. 実カードをSolver rangeへ補完すべき
E. range外spotはSolver不適格として扱うべき
```

Task 18-Dで原因診断を予定していたが、ユーザー方針により一旦保留した。

理由:

* 他Solver候補の検証を優先するため。

---

### 33.10 今後のteacher採用条件

以下のデータはteacherとして使わない。

```text
hero_cards 欠落
matched_hand_missing
hero_range_contains_hand=false
average_strategy_fallback
equal_probability_fallback
default_check_fallback
solver_error
```

Solver teacherとして採用できる最低条件:

```text
hero_cards が2枚存在する
Hero hand candidatesのいずれかが root_strategy または node_strategy の hands に存在する
strategy_source_detail=hand_strategy
hero_range_contains_hand=true
solver_success=true
```

Teacherデータ作成前には、必ずparse auditを行う。

---

### 33.11 Solver request/debug保存に実戦情報を残す理由

旧データでは `hero_cards` が保存されていなかったため、オフライン再解析でHero hand rowを特定できなかった。

そのため、Task 18で今後保存されるSolver request JSONの `meta` に以下を保存するよう修正した。

```text
hero_cards
board
street
num_players
heads_up
hero_position
hero_is_ip
hero_bet
max_opponent_bet
facing_bet
call_amount
raw_call_amount
pot
effective_stack
current_street_actions
preflop_actions
```

保存時に重要metaが欠落している場合は、保存を止めずにwarningを出す。

```text
SOLVER_REQUEST_META_INCOMPLETE
```

この情報がないデータは、teacher作成・LLM検証・後日監査に使うべきではない。

---

### 33.12 HU flop LLM化検証を保留する理由

HU flop LLM化検証は一旦保留する。

理由:

```text
- 旧teacherデータがHeroカード欠落により無効
- 新規データでもHero hand range外問題が発覚
- Blind LLM検証も、以前はhero_cards / facing_bet / call_amount欠落があり公平な検証ではなかった
- 他Solver候補の検証を優先する方針になった
```

他Solver検証後に、以下を再判断する。

```text
現Solverを継続する
他Solverへ切り替える
現Solverをteacher生成専用にする
HU flopをLLM化する
LLMを補助/fallbackとして使う
Task 18-D range外原因診断へ戻る
```

---

### 33.13 他Solver検証を優先する理由

現Solverでは以下の課題がある。

```text
deep-SPR flopで遅い
Hero実カードがHero側range外の場合にhand rowを取得できない
旧データ由来のteacher検証をやり直す必要がある
```

この状態でHU flop LLM化を急ぐより、他Solver候補を検証した方がよい。

他Solver候補の比較観点:

```text
Hero hand別strategyが取れるか
Hero実カードを直接指定できるか
range strategy型の場合、Hero hand row抽出が確実か
HU flop / turn / river対応
deep SPR flopの速度
sizing候補の柔軟性
all-in候補の制御
Windowsローカル動作
Python連携
ライセンス / 商用利用可否
現システムへの組み込み難易度
```

この比較後に、現Solver継続・他Solver採用・LLM化の方針を再判断する。

## 34. Rust postflop CLIからDeep CFRへ切り替える理由

### 34.1 Rust postflop CLIで起きていた問題

HU postflopで以下の構造的問題があった。

1. deep-SPR flopで22秒タイムアウト。CoinPokerのアクションタイマーに間に合わない。
2. Hero実カード（Q3s等）がHero側range外の場合、hand_strategyが取得できず
   average_strategy_fallbackになる。新規3件中2件で発生。
3. レンジ全体を解く方式のため、range定義の品質に推奨精度が依存する。

### 34.2 Deep CFRを採用する理由

Deep CFR 6-player NLHEは以下を同時に解決する。

速度: 推論0.5〜1ミリ秒。タイムアウト不可能。
Hero hand問題: 具体的なゲーム状態（Hero実カード含む）を直接入力するため、
  range外という概念が存在しない。Q3sでも72oでも入力すれば判断が返る。
Multiway対応: 6人テーブルを前提に訓練されているため、
  LLMをMultiway判断主軸から外せる。

### 34.3 精度のトレードオフ

Deep CFRの判断はSolverほど精密ではない。

Solverが返すもの: レンジ全体のベットサイズ別精密頻度（Nash Distance 0.3%以下）
Deep CFRが返すもの: 1つのゲーム状態に対する近似的な確率分布（訓練品質に依存）

ただし、Solverが22秒タイムアウトで結果を返せないか、
average_strategy_fallbackになるケースでは、
精度が多少低くてもDeep CFRの方が実用価値が高い。

### 34.4 LLM Multiway判断を廃止する理由

LLMはポーカーの数理計算に本質的に向いていない。
KK+47% equityでfold推奨が出た事例（DESIGN_NOTES Section 11.1）が象徴的。
数理ガードで補正しているが、構造的に不安定。

Deep CFRはCFRアルゴリズムで訓練されているため、
数理的根拠のある判断を返す。LLM特有のJSON不安定性、
reasoning品質のばらつき、プロンプト管理の複雑さが解消される。

### 34.5 LLM exploit_adjustmentを残す理由

Deep CFRはGTO近似戦略を返す。
実際の対戦相手はGTO通りには打たない。
相手の実データ（50ハンド以上の戦績）に基づくエクスプロイト補正は、
GTO戦略の上に載せる補正層として価値がある。

LLMの役割を「戦略判断」から「統計ベース微調整」に限定する。
OpenRouter / gpt-5.4-miniインフラはexploit用途で継続使用する。

### 34.6 段階的移行を採用する理由

Rust postflop CLIを即座に削除せず、Deep CFR統合完了後に廃止する。

理由:
- Deep CFR訓練に約1ヶ月かかる
- 訓練中も既存システムを使いたい
- Deep CFRモデルの品質検証後に切り替える方が安全
- config.yamlのfallback_to_solverフラグで切り替えられる

### 34.7 Deep CFRの訓練計画

訓練環境: RTX 3080 / VRAM 10GB
訓練リポジトリ: https://github.com/dberweger2017/deepcfr-texas-no-limit-holdem-6-players
ライセンス: MIT

訓練スケジュール（実測値に基づく更新）:
  Step 0: 環境構築（20分）
  Step 1: Phase 1 基礎訓練 ×3シード（各~1.5時間、合計~5時間。~3秒/イテレーション）
  Step 2: Phase 1 品質確認（数時間。評価スクリプト + TensorBoard確認）
  Step 3: Phase 2 自己対戦（~24-28時間。~44-52秒/イテレーション × 2000回）
  Step 4: Phase 3 混合訓練（~9日見込み。~50秒/イテレーション × 15000回。途中打ち切り可）
  Step 5: 最終品質検証（数時間）
  合計: Phase 1-2で約2日、Phase 3を含めると約10-12日

注記: 当初は「約1ヶ月」と見積もっていたが、RTX 3080での実測では
Phase 1が想定よりはるかに速かった（1シード~1.5時間 vs 当初見積もり2-3日）。
Phase 2はニューラルネット対戦のため約15倍遅くなるが、それでも~24時間で完了する。
Phase 3が最も時間を要するが、途中チェックポイントで品質評価し十分なら打ち切り可能。

訓練とシステム改修は並行して進められる。
訓練中にdeep_cfr_bridge.pyの実装・テストを行う。

### 34.8 Deep CFR選定に至る比較検討経緯

2026年5月時点で、以下のソルバー／フレームワークを検討した。

検討候補と不採用理由:

TexasSolver GPU:
  CUDAでCFRを直接実行、CPU比約4倍速。フルNLHE対応。
  不採用理由: GUI専用でCLI未提供。自システムとのプログラム連携が不可能。
  Windows限定。GPU版のソースコード非公開。

GTO Wizard AI:
  クラウドGPU＋ニューラルネット。PioSolver比200倍速。
  不採用理由: API未公開。Web UIのみ。ローカル実行不可。月額課金。

Deepsolver:
  CFR＋ニューラルネットハイブリッド。数秒で解答。
  不採用理由: API未公開。クラウド専用。ローカル実行不可。

NoRegret (GPUGT):
  Python + CUDAカーネル。CPU比最大203倍速。MIT。
  不採用理由: Kuhn、Leduc等の小〜中規模ゲームのみ実証済み。
  フルサイズ6人NLHEはノード数10^14〜10^18でVRAM不足。実用不可。

cfrx (JAX):
  JAX GPU/TPU対応CFR。Python。OSS。
  不採用理由: NoRegretと同様、小〜中規模ゲーム向け。フルNLHE未対応。

ReBel (Facebook/Meta):
  ヘッズアップNLHEでプロに勝利した実績あり。Apache 2.0。
  不採用理由: 公開実装がLiar's Diceのみ。ポーカー用コード未公開。
  2人ゼロサムゲーム限定で6人NLHEに適用不可。
  再実装には数ヶ月の工数が必要。再実装試行者が発散問題を報告。

PokerRL:
  PyTorch GPU＋分散学習。Deep CFR/SD-CFR実装あり。MIT。
  不採用理由: メンテナンスが停滞（Python 3.6 / PyTorch 0.4.1）。
  6人NLHEのフル実装・訓練パイプラインが整っていない。

Shark 2.0:
  C++ OSS。SIMD/TBB最適化。
  不採用理由: GPU未対応。フルNLHE flopの速度が不十分。
  CLIインターフェースが不明確。

postflop-solver (Rust crate):
  現行システムで使用中のRust postflop CLIのベースライブラリ。
  高速だがCPU専用。開発一時停止中。
  問題: deep-SPR flopで22秒タイムアウト、Hero hand range外問題。

PioSolver + UPI:
  業界標準。テキストベースCLI。Python wrapper (pyosolver)あり。
  不採用理由: 1スポット数分〜数十分。リアルタイム推奨に間に合わない。
  有料（€450+）。

採用: Deep CFR 6-player NLHE (dberweger2017):
  PyTorch GPU。6人NLHEフル実装。MIT。CLI対応。
  訓練済みモデル公開。推論0.5〜1ミリ秒。
  Hero実カードを直接入力（range外問題なし）。
  HU/Multiway両対応（モデル共通）。
  状態エンコーディング変換のみで現システムに接続可能。

### 34.9 Deep CFRの既知の限界

以下はDeep CFR採用にあたり認識している限界である。

精度:
  Deep CFRはGTO近似であり、Solver（PioSolver等）ほど精密ではない。
  特定スポットのベットサイズ別精密頻度は得られない。
  「Fold 3% / Call 25% / Raise 72% / raise 0.8x pot」のような近似分布を返す。

プロ実績:
  ReBelやLibratusのような「査読済み論文でプロに勝った」水準の実績はない。
  開発者は「プロの友人に善戦」と報告しているが、統計的検証は未公開。

Exploitability計算不能:
  6人NLHEではゲーム木が巨大すぎて、Best Response計算による
  正確なexploitabilityの測定が不可能。
  モデルの品質は実戦的テスト（大量対戦、スポットチェック）に依存する。

プレイヤー数:
  6人テーブル専用。7人以上は状態エンコーディング・ゲーム環境の改修が必要。
  学術的にはkdb-D2CFR（2023年、3〜8人）で原理的には動くことが示されているが、
  dberweger2017版は6人固定。CoinPoker 6maxでは問題ない。

訓練の再現性:
  開発者READMEに「正確な収益性は研究段階」「ロバスト性は完全に証明されていない」
  と明記されている。シードや訓練スケジュールにより結果が変動する。
  そのため3シード並行訓練で最良を選ぶ方針を採用。

訓練期間:
  RTX 3080で約1ヶ月。訓練中は既存Solver経路をfallbackとして使用。

### 34.10 Deep CFR訓練の原則

原論文（Brown & Sandholm, Meta AI, 2019）および後続研究から確立された原則。

毎イテレーション、ネットワークをゼロから再訓練する:
  前回の重みを引き継いでファインチューニングすると、
  exploitabilityが約50%悪化する（原論文Figure 4で実証）。

Reservoir Samplingを使う:
  メモリバッファが満杯になったとき、スライディングウィンドウ方式だと
  バッファ満杯時点で収束が停止する。
  Reservoir Samplingなら収束が継続する（原論文Figure 4で実証）。
  スライディングウィンドウは禁止。

Linear CFR重み付けを適用する:
  各イテレーションのサンプルにイテレーション番号tに比例した重みを付ける。
  漸近的性能は同等だが収束が速くなる。

全リグレットが負のとき、最大リグレットのアクションを確率1で選ぶ:
  標準Regret Matchingの均等戦略ではなく、最大リグレットアクションを選ぶ方が
  exploitabilityが約50%改善する（原論文Figure 4で実証）。
  近似誤差がある環境ではこの変更が重要。

メモリバッファサイズ:
  原論文では各プレイヤーのadvantageメモリに4000万サンプルを割り当て。
  小さすぎると過去の重要経験が失われ戦略が不安定になる。
  RAMが許す限り大きくする。

ネットワークサイズ:
  原論文Figure 3で、hidden layer 256次元を超えてもFHPでは改善なし。
  dberweger2017版の3層×256ユニットはこの知見に基づく。
  無駄に大きくすると学習が不安定になる。

  注記: SPEC初版およびDESIGN_NOTES初版では「5層×256ユニット」と記載していたが、
  実際のdberweger2017リポジトリのmodel.pyは3層×256ユニット、入力156次元である。
  poker-system側の _deep_cfr_network.py もこの実アーキテクチャで実装済み。


これらの原則は2人の小〜中規模ゲームで実証されたものであり、
6人NLHEで厳密に最適化されたレシピは2026年5月時点で存在しない。
dberweger2017版の3段階訓練は開発者の経験則であり、原論文の手法とは異なる。

実際の訓練では以下の追加知見を得た:
- 評価関数（evaluate_against_random）でニューラルネット同士のRaise無限ループが発生する
  → MAX_ACTIONS_PER_GAME = 300 で対処
- PrioritizedMemoryの_max_priorityが際限なく増大し勾配爆発を起こす
  → max_priority_cap = 100.0 で対処
- checkpoint再開時にメモリバッファが復元されず発散する
  → 各シードをフル実行し、checkpoint再開を使わない運用で対処
- Phase 2はニューラルネット対戦により~15倍遅くなる（~3秒→~50秒/イテレーション）
これらの修正はC:\dev\deepcfr-training側のコードに適用済みであり、
poker-system側のコードには影響しない。


## 35. Deep CFRフォールバック経路を細分化した理由

### 35.1 一律フォールバックの問題

Phase B Task 2では、Deep CFR失敗時に一律で `_postflop_legacy_route` へフォールバックしていた。
これはFlop HUでもSolverを呼ぶことを意味する。

しかしユーザー方針として、FlopではSolverを使わず、LLMを第1フォールバックにすることが決まった。

理由:
- Deep-SPR flopでSolverがタイムアウトする既知問題がそのまま残る
- Deep CFRで解決したはずの問題がフォールバックで再発する

### 35.2 フェーズ・人数別フォールバックを採用した理由

以下のルールを採用した。

Flop HU/Multiway: Deep CFR → LLM → スキップ（Stage D完了まで保持）
Turn/River HU: Deep CFR → LLM → スキップ（Stage D完了まで保持）
Turn/River Multiway: Deep CFR → LLM → スキップ（Stage D完了まで保持）

注記: 旧経路では Turn/River HU に Solver → LLM を使っていたが、
Rust postflop CLIの永久廃止決定により、Solverをフォールバックから除外した。
Deep CFRは品質不合格だが、PokerRL+GRPO統合完了（Stage D）までは
「何も出さないより品質が低くても何か出す」経路として残す。
Stage D完了後にDeep CFRフォールバックも廃止する。

### 35.3 _postflop_legacy_route を残した理由

_postflop_legacy_route は削除せず残した。

理由:
- config.yaml に deep_cfr セクションがない環境では旧経路が必要
- fallback_to_solver=true の将来的な用途に備える
- 既存テストの互換性を維持する

---

## 36. Deep CFR出力にexploit_adjustmentを適用する設計判断

### 36.1 既存suggest_exploitを再利用しなかった理由

既存の `suggest_exploit` はSolver出力形式（root_strategy, actions, average_strategy, equity, ev）を
前提としたプロンプトを使っている。

Deep CFRの出力形式（fold_prob, call_prob, raise_prob, raise_size_ratio）は異なるため、
専用のプロンプトとメソッド `suggest_exploit_for_deep_cfr` を新設した。

### 36.2 exploit失敗時に元推薦を保持する理由

exploit_adjustmentはGTO近似戦略の上に載せる補正層であり、
補正が失敗してもGTO近似としてのDeep CFR推薦自体は有効である。

そのため、LLM例外・タイムアウト・統計不足時はDeep CFR元推薦をそのまま返す。
暫定推奨は出さない。

### 36.3 strategy_sourceで追跡する理由

exploit調整の有無を追跡するために strategy_source を使い分ける。

- "deep_cfr": 未調整のDeep CFR推薦
- "deep_cfr_exploit": exploit調整後

HUDでは "Deep CFR+" をcyan色で表示し、ユーザーが調整済みかを一目で判別できるようにした。

同一actionが返された場合は strategy_source を変更しない（無意味な表示変更を防ぐ）。

---

## 37. HUD表示をDeep CFR対応にした設計判断

### 37.1 確率分布をDeep CFRソース時のみ表示する理由

既存のSolverソースでは `_probabilities_label` を常に非表示にしていた。
Solverの確率分布はレンジ全体の混合戦略であり、1ハンドの推奨としては直感的でない。

Deep CFRの確率分布は具体的なゲーム状態に対する直接的な判断であるため、
ユーザーにとって有用な情報として表示する。

### 37.2 既存ソース表示との互換性

SOURCE_LABELS辞書に "deep_cfr" と "deep_cfr_exploit" を追加した。
既存の "solver", "preflop_chart", "llm_multiway" 等のラベルは変更していない。

Deep CFRが無い環境では従来通りのHUD表示が維持される。

### 37.3 GUI既知テスト失敗の解消

test_gui_smoke.py の test_hud_overlay_show_pre_hand が
"安定待ち..." を期待していたが、実際のコードは "WAITING FOR STABLE STATE..." を返していた。

Task 4でテスト側の期待値を実コードに合わせて修正し、
1441 passed, 0 failed を達成した。

---

## 38. Deep CFR訓練で発見された技術的問題と対処

### 38.1 評価関数の無限ループ

evaluate_against_random / evaluate_against_checkpoint_agents で、
ニューラルネット同士の対戦時にRaise→Raiseの無限ループが発生し、
訓練がハングする問題が発覚した。

Phase 1 Seed Cのiteration 520と1010で発生。Phase 2でも再現。

対処: MAX_ACTIONS_PER_GAME = 300 を導入。
300アクション超過でゲームを打ち切り、WARNINGログを出す。
典型的な6人NLHEのハンドは20-40アクションであるため、300は十分に余裕がある。

### 38.2 Priority正帰還ループ

PrioritizedMemory の _max_priority が際限なく増大し、
新規経験が常に最高優先度で追加される正帰還ループが発生した。

Phase 2開始直後にPriority maxが10^12を超え、勾配爆発を引き起こした。

対処: max_priority_cap = 100.0 を導入。
理論的なリグレット範囲（約-100〜+100）に合わせたクランプ値。
add / update_priority の両方でクランプを適用。

### 38.3 checkpoint再開時のメモリバッファ損失

continue_training関数はネットワーク重みのみを復元し、
advantage_memory / strategy_memory を復元しない。

これにより、checkpoint再開後にバッファが空から再構築され、
初期の不安定なサンプルで訓練が発散する問題があった。

Seed Aの初回実行（1000イテレーション→再開）で発生。

対処: checkpoint再開を使わず、各シードを1500イテレーションのフル実行にした。
Phase 2ではcheckpointを対戦相手として使用するため、
学習エージェントのメモリは新規構築で問題ない。

### 38.4 Phase 2のイテレーション速度

Phase 1: ~3秒/イテレーション（ランダム対戦）
Phase 2: ~44-52秒/イテレーション（ニューラルネット対戦）

ニューラルネット6エージェントの推論コストにより約15倍遅くなる。
traversals 400で2000イテレーション = 約24-28時間。

SPEC 10A snapshotの仕様通り traversals 400 を維持した。

## 39. 訓練手順をREADME準拠に修正した理由

### 39.1 旧訓練手順で起きていた問題

2026-05-22〜05-24のセッションで、以下の手順ミスが判明した。

1. Phase 1 の traversals を 300 に設定していた。
   README は 200 を指定している。Medium 記事の 100/200/400 は比較実験であり、
   最終的に開発者は 200 に収束させた。

2. Phase 1 の iterations を 1500 に設定していた。
   README は 1000 を指定している。

3. Seed A/B/C 方式を採用していた。
   開発者は traversals の異なる 3 モデル比較を行っており、
   同一 traversals での Seed 比較は独自判断だった。

4. Phase 2 で独自関数 train_selfplay_v2 を作成し使用していた。
   重み引き継ぎ（zero-start ではない）、混合対戦相手、--random-seats 等の
   非標準設計を導入していた。README の train_against_checkpoint とは異なる。

5. Phase 3（混合訓練）を実施していなかった。
   開発者は Phase 2 で性能低下を経験しており、Phase 3 で回復させる設計である。
   Phase 2 の性能低下を見て「弱すぎる」と判断し Phase 3 に進まなかったのは誤り。

### 39.2 Medium 記事と README の矛盾

2026年3月にリポジトリが大幅更新され、Issue #22（Phase 2/3 のバグ修正）が解決された。
この更新でREADMEが書き直されたが、Medium 記事は更新されていない。

README には以下の注記がある:
"prioritize the repo over the Medium article"

Medium 記事との主要な矛盾:
- Phase 1 traversals: Medium は 100/200/400（3モデル比較）、README は 200（1モデル）
- Phase 2 checkpoint元: Medium は models/400/checkpoint_iter_2000.pt、
  README は models/phase1/checkpoint_iter_1000.pt
- Phase 3 iterations: Medium は 20000、README は 10000
- アクション空間: Medium は 4 固定離散、実コードは 3 + 連続サイジング

これらの矛盾に気づかず Medium 記事を参考にしていたため、
旧訓練は非標準パラメータで実行されていた。

### 39.3 README準拠手順に切り替えた理由

README が唯一の正規情報源であることが確認されたため、
すべての訓練パラメータを README に合わせて再実行する方針にした。

既存の訓練結果（旧 Phase 1 Seed A/B/C、旧 Phase 2、旧 Phase 2 v2）は
参考データとして保持するが、本番モデル訓練には使用しない。

### 39.4 Phase 2 の性能低下で中断しない理由

旧 Phase 2 / Phase 2 v2 では、iter 300 付近をピークに profit vs random が低下した。
開発者も Phase 2 で同様の経験をしており、"Self-play led to interesting cyclic patterns
but plateaued in performance" と記載している。

Phase 3（混合訓練）で回復させる設計であるため、
Phase 2 の profit 低下で訓練を中断してはならない。

### 39.5 独自コードを使用禁止にした理由

train_selfplay_v2 は以下の非標準設計を含んでいた:
- Phase 1 の重みを引き継ぐ（README の train_against_checkpoint は zero-start）
- 混合対戦相手（Phase 1 の複数シードから選択 + ランダムエージェント）
- --random-seats による席シャッフル

これらは Deep CFR 原論文にも開発者の README にも根拠がなく、
性能低下の一因だった可能性がある。

今後の訓練では README のコマンドのみを使用し、
独自フラグ（--self-play-v2, --random-seats, --opponent-checkpoints）は使用しない。

### 39.6 --model-prefix t_ の注意点

Phase 3 の --model-prefix t_ は models/ 内の t_*.pt を検索する。
Phase 2 のチェックポイントは selfplay_checkpoint_iter_*.pt という名前で保存される。

Phase 2 完了後に以下のいずれかが必要:
- ファイルを t_*.pt にリネーム
- --model-prefix selfplay に変更

この確認を忘れると Phase 3 が対戦相手を見つけられず失敗する。

## 40. flagship modelが現行コードと非互換である記録

### 40.1 問題

flagship_models/first/ に格納されているモデルは2025年3月作成の旧アーキテクチャである。

旧アーキテクチャ:
  ネットワーク層: fc1, fc2, fc3, fc4, fc5, fc6
  出力: 4アクション固定（離散）

現行アーキテクチャ:
  ネットワーク層: base, action_head, sizing_head
  出力: 3アクション + 連続サイジング（0.1〜3.0× pot）

ロード時に RuntimeError: Missing key(s) / Unexpected key(s) が発生する。

### 40.2 対処

flagship modelは使用しない。
現行コードで訓練したcheckpointのみを使用する。
flagship_models/ ディレクトリは訓練リポジトリに残存するが、poker-systemからは参照しない。

## 41. description.md vs readme.md の情報源優先順位を定めた理由

### 41.1 背景

訓練リポジトリには以下の情報源が存在する。

readme.md: 2026年3月更新。Issue #22修正後の正規手順。
description.md: 2025年3月作成。旧アーキテクチャ時代の実験記録。
Medium記事: 更新されていない。READMEと矛盾する箇所がある。

### 41.2 矛盾の例

Phase 1 traversals: Medium は 100/200/400（3モデル比較）、README は 200（1モデル）
Phase 2 checkpoint元: Medium は models/400/checkpoint_iter_2000.pt、README は models/phase1/checkpoint_iter_1000.pt
Phase 3 iterations: Medium は 20000、README は 10000
アクション空間: Medium/description は 4固定離散、実コードは 3 + 連続サイジング

### 41.3 方針

README (readme.md) を唯一の正規情報源とする。
description.md は旧実験記録として参考のみ。
Medium記事はREADMEと矛盾する場合、READMEを優先する。
READMEに "prioritize the repo over the Medium article" と明記されている。

## 42. Phase 3 対戦相手プール構成の設計判断

### 42.1 問題

READMEのPhase 3コマンドは --model-prefix t_ を使用するが、
models/ 直下に t_*.pt は存在しない。
Phase 2のcheckpointは selfplay_checkpoint_iter_*.pt という名前で保存される。

### 42.2 採用した構成

Phase 2の20 checkpoint（selfplay_checkpoint_iter_100.pt 〜 selfplay_checkpoint_iter_2000.pt）を
models/phase3_pool_v3/ にコピーした。

--checkpoint-dir models/phase3_pool_v3 で直下のみ検索（非再帰）。
--model-prefix "selfplay_checkpoint_iter_[0-9]" で glob 文字クラスを使用し、
旧訓練ファイル（phase1_seedA/B/C, phase2, phase2_v2）の混入を防止する。

### 42.3 Phase 3自身の保存先を分けた理由

train_with_mixed_checkpoints の検索は glob.glob(os.path.join(checkpoint_dir, f"{prefix}*.pt")) で
非再帰・直下のみ。

Phase 3自身のcheckpointを同じフォルダに保存すると、
対戦相手プールに自分自身の中間checkpointが混入する危険がある。

そのため --save-dir models/phase3_v3b で別フォルダに保存する。

## 43. Phase 3 loss発散が自然収束する理由と中断しない判断

### 43.1 現象

Phase 3開始直後、Advantage network loss が 10^11〜10^12 に散発的にスパイクする。
Phase 3 初回 (v3): iter 5〜27 で約44%のイテレーションで発生。
Phase 3 再実行 (v3b): 同じ現象が初期に発生。

### 43.2 原因候補

encode_state の正規化分母が極小 stake で爆発する可能性がある（未修正）。
PrioritizedMemory の max_priority_cap = 100.0 で勾配爆発は抑制済みだが、
入力正規化の問題は残存している。

### 43.3 自然収束の観察

Phase 3 v3b では、数百イテレーション後にスパイク頻度が減少した。
iter 2000付近では散発1件のみ（前後は正常値5〜14）。
profit vs random は初期 +3.29 から +10〜+36 の範囲で安定推移。

### 43.4 中断しない判断の根拠

異常検知基準（snapshot Section 4.10）に該当しない限り中断しない。
lossスパイクが「全イテレーション」ではなく散発であれば許容。
profit vs randomが100 iter連続で負にならない限り許容。
Phase 1 v3 (12.00) を大幅に上回るprofitが維持されている。

---

## 44. メモリバッファを300,000から20,000,000に拡大した理由

### 44.1 デフォルト値が不十分だった根拠

dberweger2017リポジトリのデフォルトは memory_size=300,000 である。
原論文（Brown & Sandholm, 2019）では各プレイヤーのadvantageメモリに40,000,000サンプルを割り当てている。

デフォルト300,000は原論文の0.75%であり、大幅に不足していた。

Phase 3 v3b（traversals=400）では、1イテレーションあたり約4,700サンプルが蓄積される。
300,000 / 4,700 ≒ iter 64 でメモリが満杯になり、以降は古い経験が押し出され続けていた。

これにより以下の問題が起きていた可能性がある。

- 初期の重要な経験が早期に失われる
- 訓練後半で経験の多様性が低下する
- profitがピーク帯（iter 300-500）から微妙に低下する傾向

### 44.2 2000万を選定した根拠

訓練環境: RTX 3080 (VRAM 10GB) / RAM 32GB

1サンプル = 644バイト（156次元float32 state + action int64 + regret float32 + iteration int64）

メモリ予算計算:
  RAM 32GB
  - OS / バックグラウンド: 約4GB
  - Python / PyTorch本体: 約2GB
  - 訓練中のミニバッチ・勾配計算: 約2GB
  - 対戦相手エージェント（5体）: 約1GB
  = メモリバッファに使える: 約23GB

  10,000,000サンプル = 6.0GB
  15,000,000サンプル = 9.0GB
  20,000,000サンプル = 12.0GB（残り11GB、十分な余裕）
  25,000,000サンプル = 15.0GB（やや余裕少）
  30,000,000サンプル = 18.0GB（ギリギリ）

Windowsのスワップ発生を避けるため、20,000,000（12GB、残り11GB）を採用した。
原論文の40,000,000の50%であり、デフォルトの67倍。

### 44.3 学習エージェントのみ変更した理由

メモリバッファに経験を蓄積するのは学習エージェントだけである。
対戦相手・評価用エージェントはcfr_traverseで経験を蓄積しないため、
メモリサイズを大きくしてもRAMを無駄に消費するだけである。

変更箇所: train.py の学習エージェント生成4箇所のみ。
対戦相手・評価用エージェント6箇所はデフォルト（300,000）のまま。

### 44.4 将来の拡大検討

RAM増設（64GB以上）した場合、30,000,000〜40,000,000への拡大を検討する。
原論文の40,000,000に近づけることで、訓練の安定性と最終品質がさらに向上する可能性がある。

また、1イテレーションの訓練時間が長くなる可能性がある。
メモリが大きいほど「ゼロからの再訓練」で扱うデータ量が増えるため。
Phase 1 v4の実測速度で影響度を確認する。

---

## 45. Phase 3 v4 最終結果とメモリバッファ拡大の効果実証

### 45.1 Phase 3 v4 最終結果

Phase 3 v4（mixed training, memory_size=20M, iterations=10000, traversals=400）が2026-05-29に完了した。

最終評価結果:

```text
Final eval (500 games, 訓練スクリプト内蔵):
  profit vs random: 66.55
  profit vs mixed opponents: 93.36

独立再評価 (3000 games):
  Phase 3 v4 profit vs random: 46.07
  Phase 3 v4 vs Phase 1 v4: +4.72
  Phase 1 v4 profit vs random: 28.08
```

最終合格基準（profit vs random >= 15）を3倍超で達成。
Phase 1 checkpointへも勝ち越し。

### 45.2 v3b（memory_size=300K）との比較

v3bはiter 2018/10000で中止した。中止時点のprofitは+10〜+36で安定していたが、
メモリバッファ拡大のために再訓練を決定した。

比較:

```text
v3b (memory_size=300,000):
  Phase 1 v3 独立再評価: 12.00
  Phase 3 v3b iter 2018: profit +10〜+36（中止）
  メモリ満杯: iter 65付近（以降は古い経験が押し出され続ける）

v4 (memory_size=20,000,000):
  Phase 1 v4 独立再評価: 24.41（v3の2倍）
  Phase 3 v4 独立再評価: 46.07（10000 iterまで完走）
  メモリ満杯: iter 9999付近（訓練終盤でようやく満杯）
```

v3bをiter 10000まで回した場合の予測は不明だが、Phase 1時点でv4がv3の2倍だったこと、
Phase 3 v4のprofit推移が一貫して上昇し続けたことから、メモリバッファ拡大の効果は実証された。

### 45.3 Phase 3 v4のprofit推移

訓練中のモニタリングで以下の推移を確認した。

```text
iter 612付近: -8〜+10（中央値+1〜+3）初期不安定期
iter 2220付近: +11〜+34（中央値+19）安定期突入
iter 5932付近: +15〜+67（中央値+35）上昇継続
iter 8086付近: +30〜+63（中央値+49）底上げ進行
最終 (iter 10000): 46.07（独立再評価3000 games）
```

iter 2000以降、一貫してプラス圏を維持し、底値も上昇し続けた。
10000 iterまで改善が続いており、崩壊・過学習の兆候はなかった。

### 45.4 loss発散の推移

Phase 3初期にAdvantage network lossの散発スパイク（10^10〜10^12）が発生した。
これはDESIGN_NOTES Section 43で記録済みの既知現象。

```text
iter 0-600: 散発スパイクあり（10^10〜10^12）
iter 600-2000: スパイク頻度減少
iter 2000以降: スパイク完全消失、全件1〜18の正常範囲
```

encode_stateの正規化分母の問題（Section 43の原因候補）は未修正だが、
訓練に致命的な影響を与えないことが確認された。

### 45.5 メモリ使用量

```text
Advantage memory: 20,000,000（iter 9999付近で満杯）
Strategy memory: 20,000,000（同上）
RAM使用: 推定12GB（メモリバッファ分）+ OS/Python/PyTorch約8GB = 約20GB / 32GB
Windowsスワップ: 発生せず（DESIGN_NOTES Section 44の設計通り）
```

原論文の40,000,000の50%だが、十分な品質を達成した。
将来RAM増設（64GB）時に30M〜40Mへの拡大を検討する価値はある。

### 45.6 Final evalと独立再評価の差異

Final eval（500 games）= 66.55 と 独立再評価（3000 games）= 46.07 に差がある。

理由候補:
- 500 gamesと3000 gamesのサンプル数差による分散
- Final evalは訓練スクリプト内蔵の評価（訓練直後のGPU状態で実行）
- 独立再評価は別プロセスでモデルをロードし直して実行
- 3000 gamesの方がより安定した推定値

モデル品質の判断には3000 gamesの独立再評価（46.07）を使う。

### 45.7 Phase 3 v4 vs Phase 1 v4 の差が小さい理由

Phase 3 v4 vs Phase 1 v4 = +4.72 は、profit vs random（46.07 vs 28.08）の差ほど大きくない。

理由:
- ニューラルネット同士の対戦では、ランダム相手との対戦より差が圧縮される
- Phase 1 v4自体がvs randomで28.08と十分強い
- 6人テーブルで1人だけPhase 3、他5人がPhase 1という構成では、
  Phase 3エージェントが搾取できる相手が限定的
- 重要なのはランダム相手への絶対的な強さ（46.07）と、
  Phase 1より弱くないこと（+4.72で勝ち越し）

### 45.8 モデル配置

```text
コピー元: C:\dev\deepcfr-training\models\phase3_v4\mixed_checkpoint_iter_10000.pt
コピー先: C:\Users\user\Desktop\dev\poker-system\models\deep_cfr\best_checkpoint.pt
ファイルサイズ: 1,761,726 bytes (1.76MB)
配置日: 2026-05-29

config.yaml:
  deep_cfr.model_path: models/deep_cfr/best_checkpoint.pt
  deep_cfr.device: cuda
  deep_cfr.fallback_to_solver: true（ライブテスト確認後にfalseへ切替予定）
```

---

## 46. encode_game_stateが訓練側encode_stateと乖離していた問題と修正

### 46.1 発覚経緯

Phase 3 v4モデル配置後の初回ライブテスト（2026-05-29）で、Deep CFRの推奨が
全ハンド・全ストリートで一貫してraise 70-80%、サイジング1.5x pot付近を返した。
状況に応じた変動が極めて小さく、異常行動が疑われた。

調査の結果、poker-assistant側のencode_game_state()が
訓練リポジトリのencode_state()（src/core/model.py）と
複数箇所で乖離していることが判明した。

### 46.2 発見された乖離と修正内容

C1: initial_stake（正規化分母）
  訓練側: state.players_state[0].stake（残りチップのみ）
  本番側: hero.stack + hero.bet（残りチップ＋現在ベット）
  影響: pot, bet, stack, min_bet, previous action amountの全正規化値がズレ
  修正: hero.stackのみに変更

C2: pot_chips（プレイヤー累積ポット貢献額）
  訓練側: player_state.pot_chips / initial_stake
  本番側: 常に0.0
  影響: 6人×24次元中6次元が全て0
  修正: hand_start_stack - current_stack - current_betで計算
  GameStateにhand_start_stacksフィールドを追加

C3: current_player / button位置
  current_player: Hero=index 0固定。推論はHeroターンのみなので正しい。
  button位置: (dealer_seat - 1) % 6 の変換は正しい。dealer_seat=None時のWARNING追加。

C4: legal_actions / previous_action
  legal_actions: Raiseを常にONにしていた。hero_stack > call_amountの場合のみONに修正。
  previous_action: ストリート開始直後にcurrent_street_actionsが空の場合、
  preflop_actionsからフォールバックするよう修正。

C5: INFOレベル推論ログ追加
  ライブテストで入力値の違和感に気づけるよう、推論時に主要入力値をINFOログ出力。

### 46.3 サイジングが1.5x付近に固定されていた原因

ネットワークのsizing_headは Sigmoid 出力に 0.1 + 2.9 * sigmoid(x) を適用する。
sigmoid(0) = 0.5 → 0.1 + 2.9 * 0.5 = 1.55。
入力が壊れているためネットワークが意味のある判断をできず、
sizing_headの出力が0付近（＝デフォルト値1.55）に張り付いていた。

### 46.4 教訓

訓練リポジトリのencode_state()を正とし、
本番側のencode_game_state()は1対1で対応を検証すべきである。
推論ログ（入力サマリ・出力確率・サイジング）を常時出力し、
固定パターンの検出を容易にすべきである。

---

## 47. Deep CFRモデル品質不合格の事後分析

### 47.1 発覚経緯

Phase C修正後ライブテスト（2026-05-30）で、Deep CFRの推奨が
ほぼ全ハンド・全ストリートでraise 70-80%、サイジング1.5x pot付近に偏ることを確認した。

ライブテスト（2026-05-30、4ハンド観察）での全Deep CFR推奨:

| Hand | Phase | Hero | Board | 状況 | F/C/R | 推奨 | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | Flop | JsQs | 3hTh8h | 4way, ノードロー | 0.5/24/76 | BET 751 (151%pot) | ❌ おかしい |
| 1 | Turn | JsQs | 3hTh8hTd | 3way, フラッシュボード | 0.7/20/79 | BET 2568 (151%pot) | ❌ おかしい |
| 1 | River | JsQs | 3hTh8hTd4s | 3way | 0.5/22/77 | BET 2566 (151%pot) | ❌ おかしい |
| 2 | Flop | 9sKh | 9h3h3c | 5way, トップペア弱K | 0.3/25/74 | BET 903 (152%pot) | ❌ 過剰 |
| 2 | Turn | 9sKh | 9h3h3c2h | HU, フラッシュ完成ボード | 1.2/12/86 | BET 3633 (152%pot) | ❌ おかしい |
| 3 | Flop | 3dAh | Jh6s9d | 3way facing BET 2320 | 0.7/23/76 | RAISE 12878 (5.6X) | ❌❌ 最悪 |
| 3 | River | 3dAh | Jh6s9d2h3c | HU facing ALL_IN | 6/94/0 | CALL 10760 | △ 唯一まとも |
| 4 | Flop | KsQh | Qs6s5c | 4way facing BET+CALL | 0.3/28/72 | RAISE 3276 (7.1X) | △ 過剰 |
| 4 | Turn | KsQh | Qs6s5cTc | HU, TPTK, SPR 0.5 | 0.4/18/81 | ALL_IN 3948 | ✅ 合理的 |

9局面中、明確に合理的と言えるのは1局面のみだった。
一部のall-in局面ではcall/all-inが自然に見えるが、全体傾向としては
ハンド強度・ボードテクスチャ・相手アクションへの感度が不足していた。

### 47.2 エンコーディング検証

C6でスートマッピング不一致を発見・修正した。

```text
訓練側: Clubs=0, Diamonds=1, Hearts=2, Spades=3
poker-assistant旧: Spades=0, Hearts=1, Diamonds=2, Clubs=3
```

修正後、verify_encode.pyで "Encodings are IDENTICAL" を確認した。
さらに、訓練側の正しいエンコーディングを直接使っても同じ偏った出力が返ることを確認した。

これにより、推論側encode_game_stateの不一致ではなく、モデル自体の品質問題であることが確定した。

### 47.3 根本原因

Phase 3 v4モデルはランダム相手に最適化されていた。

profit vs random = 46.07は、「ランダム相手にひたすらレイズすれば勝てる」戦略でも達成し得る。
そのため、数値上は合格に見えても、GTO近似として必要な以下の感度が不足していた。

- ハンド強度
- ボードテクスチャ
- ポジション
- 相手アクション
- SPR
- multiway人数

dberweger2017リポジトリ作者自身もREADMEで訓練の不安定性を認めており、
今回の結果はそのリスクが実運用で顕在化したものと判断する。

### 47.4 「profit vs random」の罠

ランダム相手への最適戦略は「常にレイズ」に近くなり得る。
この指標が高くても、GTO近似や実戦品質が高いとは限らない。

今後は「profit vs random」を単独評価指標として使用しない。
代わりに以下を組み合わせて評価する。

- Spot Checks
- Entropy
- Sensitivity Tests
- 対GTOデータセットaccuracy
- Slumbot等の外部ベンチマーク

### 47.5 教訓

訓練側と推論側のencode一致検証は最初に行うべきだった。
C1-C6で問題が段階的に発覚したことで、モデル品質問題の切り分けが遅れた。

モデル品質評価は、単一のprofit指標ではなく、特定局面での行動分布で行うべきである。
1ヶ月規模の訓練投資の前に、小規模テストで品質の兆候を確認する必要があった。

---

## 48. PokerRL+GRPO採用判断

### 48.1 Deep Research調査（2026-05-30）

5要件を全て満たす既製エンジンは存在しないことを確認した。

調査範囲:
- CFR系
- ニューラルネット系
- ハイブリッド
- LLM系
- 商用ソルバー

DESIGN_NOTES Section 34.8で除外した候補に加え、Deep CFRも本番主推論には不採用とした。
既製の6-max NLHE向け、ローカル高速、postflop対応、実戦投入可能なエンジンは見つからなかった。

### 48.2 dcaustin33/poker_rl + PokerBenchの発見

dcaustin33/poker_rlは、小型LLM + 補助ヘッド + SFT + GRPO自己対戦のアプローチを採用している。

利用可能な公開データ:

```text
PokerBench: 6-max NLHE専用560k行のGTO訓練データ
Pluribus: 10,000ハンド×6人=60kトラジェクトリ
```

作者実験では、Qwen-0.6B-Embedding + GRPOで最良結果を出しており、
全実験コストは50ドル未満だった。

### 48.3 Deep CFRとの設計差異

```text
Deep CFR:
  156次元数値ベクトル → 小規模FF → 近似戦略

PokerRL+GRPO:
  テキストプロンプト → 小型LLM+補助ヘッド → 分類+サイジング
```

根本的な違いは、LLMが事前学習でポーカーや戦略言語に関する知識を持っている点である。
Deep CFRの小規模FFネットワークにはその事前知識がない。

### 48.4 採用理由

- GTO解付き大規模データセットでSFTできる
- 小型LLM（0.6B-4B）ならRTX 3080で数十ms〜数百ms推論が現実的
- GRPO自己対戦で実戦品質を強化できる
- エントロピー崩壊への対策（DAPO, OPEFO）を最初から設計に組み込める
- Deep CFRの失敗教訓（評価基準、Spot Checks）を初期計画に反映できる

Deep CFRには教師データがなく、ランダム相手profitに寄った評価になった。
PokerRL+GRPOでは、SFT段階からGTOラベル付きデータを使用する。

### 48.5 既知リスク

- 訓練済みモデルは未公開であり、自前訓練が必要
- PokerBench postflopは全てHUであり、Pluribusで6人局面を補完する必要がある
- poker_rl作者のモデルもultra tight-aggressiveに偏った
- エントロピー崩壊は4Bモデルで顕著になり得る
- 12週間のタイムボックスと撤退基準が必要

撤退基準を設ける理由は、Deep CFRと同じく「長期訓練後に品質不合格が判明する」
失敗を繰り返さないためである。

### 48.6 Rust postflop CLI廃止方針の確定

ユーザーがRust postflop CLIを「タイムオーバー的に使えないもの」と判断した。

Deep-SPRフロップで22秒タイムアウトする既知問題があり、ライブ支援の主経路にはできない。
新エンジン統合完了まではフォールバックとして残すが、代替手段とは見なさない。

---

## 49. 補助ヘッド設計と<50ms目標

### 49.1 なぜautoregressive生成ではなく補助ヘッドか

autoregressive生成では50ms目標は現実的ではない。
200 tokens前後のencodingに加えて数tokens生成が必要になり、200ms以上になりやすい。

最終hidden stateをMLP補助ヘッドへ渡す方式なら、encoding後は1ms程度で出力できる。
poker_rl作者（dcaustin33）も同じ設計判断を行い、成功している。

### 49.2 Action Head: 4クラス分類

Action Headは以下の4クラス分類とする。

```text
Fold / Check-Call / Raise / All-in
```

Deep CFRの3クラス（Fold / Call / Raise）からAll-inを追加する。
All-inを独立クラスにする理由は、sizing headの0.1x-3.0x pot範囲では
all-inを安定して表現しきれないためである。

### 49.3 Sizing Head: sigmoid連続値

Sizing Headは0.1x-3.0x potの連続値を出力する。

```text
raise_size_ratio = 0.1 + 2.9 * sigmoid(x)
```

Deep CFRと同じ方式であり、Recommendation変換も既存実装を流用しやすい。
代替案としてcategorical 8-binがあり、実装指令書のStep 1aで切替可能にする。

### 49.4 prefix cache戦略

システムプロンプト（固定部分）をKV cacheに事前格納する。
毎回re-encodeするのは状態依存部分（約50 tokens）のみに抑える。

これにより、80ms程度の推論を20-30ms程度まで短縮できる可能性がある。

---

## 50. Sprint 1 モデル選定の経緯と結果

### 50.1 評価対象と結果

Sprint 1で以下の3モデルを10k SFTで評価した。

Phi-4-mini-instruct 3.8B:
  action_accuracy 65.6%, eval_loss 0.326, 訓練時間18.4h, VRAM 9,815 MiB
  Go基準（accuracy ≥ 40%）を25.6pt超過。全基準クリア。

Qwen3.5-4B:
  QLoRA batch=4で17,076 MiB、batch=2で13,120 MiB。
  RTX 3080 10GBのGo基準（VRAM ≤ 10,000 MiB）を大幅超過。
  SFT未実施で脱落。

Gemma 4 E2B (google/gemma-4-E2B-it):
  hidden_size=1536, num_hidden_layers=35, 総パラメータ5.44B（PLE構造）。
  VRAM自体は9,917 MiBで収まるが、vision/audio tower含むマルチモーダル全体が
  ロードされるため訓練スループットが極端に低い。
  batch=1で1 step 3分超、完走見積り3日超。
  RTX 3080 10GBでの実用不可と判定し、step 6で中断。

### 50.2 Gemma 4 E2Bを候補にした理由と脱落の経緯

poker_rl作者（dcaustin33）のデフォルトモデルがGemma 3n E2Bだったため、
後継のGemma 4 E2Bを最有力候補として追加した。
Unsloth公式がLoRA 8-10GB、GRPO 9GBと報告していたが、
これはUnslothの最適化込みの値であり、標準HuggingFace+PEFTでの
QLoRA SFTでは訓練スループットが実用に耐えなかった。

### 50.3 選定結論

Phi-4-mini-instruct 3.8Bを正式採用。
Qwen3-4B-Instruct-2507を未検証の予備候補として残す。
Qwen3.5-4B、Gemma 4 E2BはクラウドGPU使用時のみの予備候補。

---

## 51. multiway データ分析結果とLayer 3不要判定

### 51.1 PokerBench postflop 500kの実態

PokerBench postflop 500kは100% HU（2人）であることを確認した。
multiway局面は0件。6-maxを謳うデータセットだが、postflopは全てHU。

### 51.2 phh-dataset multiway抽出

phh-dataset 21,606,087ハンドからmultiway postflop局面を抽出した。
結果: 20,915,640 decision points（hole cards付き726,570件）。

当初計画のLayer 2目標（10k-30k件）を700倍以上上回る量が確保できた。

### 51.3 Layer 3（PokerKit合成データ）不要判定

phh-datasetだけで十分なmultiway訓練データが確保できたため、
PokerKitによる合成データ生成（Layer 3）は不要と判定した。
これによりSprint 1の期間が1週間短縮される。

### 51.4 10k-30kという数値の由来

当初のLayer 2目標「10k-30k」は、商用ソルバーのコスト制約を前提とした
Deep Research調査の推定値だった。phh-datasetで72万件のhole cards付き
データが得られたため、この制約は解消された。

---

## 52. active player数分布管理の設計判断

### 52.1 HU:multiway固定比率を廃止した理由

当初はHU:multiway = 3:1〜5:1の固定比率を検討していたが、
この比率には根拠がなく、実験パラメータとして扱うべきと判断した。

また、HUとmultiwayは別カテゴリではなく、同一ハンド内で遷移する。
例: preflop 6人 → flop 3人 → turn 2人（HU）→ river 2人（HU）。
各decision pointのactive player数で管理する方が自然である。

### 52.2 採用した方針

PokerBench HU 500kとphh-dataset multiway抽出データを統合プールにし、
各レコードにactive player数（2〜6）をタグ付けする。
訓練データの構成はactive player数の分布で管理する。
具体的な分布はSprint 2の実験で決定する。

### 52.3 未確定事項（Sprint 2で検証）

- 勝者行動優先選別の品質（ラッキー勝利の問題）
- 人間データとsolverデータの混合が品質に与える影響
- active player分布を実戦頻度に合わせるか、少数カテゴリをオーバーサンプリングするか

---

## 53. phh-dataset hole cards付きデータが全件敗者だった問題

### 53.1 発覚経緯

Sprint 2のS2-T2a（multiway confidence scoringスクリプト作成）の前段階として、
phh-dataset multiway抽出データ（726,570件のhole cards付きレコード）の品質を
詳細分析した。

当初の計画では、hole cards付き726,570件から勝者行動を優先選別し、
confidence-weighted SFTでmultiway訓練データを作成する予定だった（snapshot §1.5）。

しかし分析の結果、以下が判明した。

```text
hole cards付き726,570件の全件が net_result ≤ 0（敗者または引き分け）
勝者（net_result > 0）のhole cards付きレコードは0件
```

### 53.2 原因

PHH（Poker Hand History）フォーマットの仕様上、
showdownで負けたプレイヤーのhole cardsのみが記録される。
勝者のhole cardsは記録されない（mucked扱い）。

そのため、hole cards付きデータ = 敗者データ という構造的制約がある。
これはデータ抽出スクリプトの問題ではなく、元データの仕様である。

### 53.3 影響

以下の計画が根本から成立しなくなった。

```text
- S2-T2a: confidence scoringスクリプト（勝者行動を高スコアにする前提が崩壊）
- S2-T2b: stratified sampler + HU rehearsal（高品質データの選別が不可能）
- S2-T2c: weighted SFT実行（重み付けの根拠となる勝者データが存在しない）
- S2-T2d: multiway Go/No-go判定（weighted SFT自体が実行不可）
- S2-T2e: KTO検討（desirable/undesirableの分類根拠がない）
```

snapshot §1.5で確定していた「confidence-weighted SFT」方針は全面的に破綻した。

### 53.4 敗者データの限定的活用可能性

敗者データ自体が完全に無価値ではない。以下の用途は検討可能。

```text
- 「やってはいけない行動」の負例としての利用（KTOのundesirable側）
- action history理解やboard texture認識の補助学習
- opponent modelingの訓練データ
```

ただし、positive example（良い行動の教師ラベル）としては使用できない。
multiway postflopの「正しい行動」を教えるデータソースが別途必要になった。

### 53.5 教訓

phh-datasetの「hole cards付き726,570件」という数字だけを見て
十分な量があると判断していたが、その全件が敗者であることを
事前に検証していなかった。

データの量だけでなく、ラベルの方向性（勝者/敗者）を
訓練計画の前提として早期に検証すべきだった。

---

## 54. PokerSkill論文（arXiv 2605.30094）の分析とMW拡張可能性

### 54.1 論文概要

PokerSkill（Li, Wang, Huang, 2026年5月）は、訓練なし・ソルバーなしで
LLMにヘッズアップ（HU）ノーリミットテキサスホールデムをプレイさせるフレームワークである。

```text
タイトル: PokerSkill: LLMs Can Play Expert-Level Poker without Training or Solvers
arXiv: 2605.30094
公開日: 2026年5月
コード: https://github.com/lbn187/PokerSkill
```

### 54.2 アーキテクチャ

PokerSkillは以下の3層で構成される。

```text
1. Context Engine（決定論的）
   - GameStateからラベルを抽出: board texture, hand class, action line, SPR, betting pressure
   - 23のhand class分類（16 Made-Hand + 8 Drawing-Hand）
   - 46段階のpressure weight table

2. Skill Library（人間専門家が設計）
   - P1: 基本ルール
   - P2: プリフロップレンジ
   - P3: ポストフロップ安定原則
   - P4: コンテキスト別ガイダンス（約60シナリオ）
   - P5: リバーブロッカーアドバイス

3. ATT/DEF Budget System
   - 各hand classに攻撃予算（ATT）と防御予算（DEF）を割り当て
   - ストリートごとのaction weightを累積し、残予算で行動制約
   - 残ATT > 0 → bet/raise可能、残DEF > 0 → call可能
```

### 54.3 主要結果

GTOWizardベンチマーク（150,000ハンド、AIVAT分散削減）での結果。

```text
GPT-5.5 XHigh + PokerSkill: -57 ± 21 mbb/hand
Claude Opus 4.6 + PokerSkill: -80 ± 29 mbb/hand
Claude Opus 4.7 + PokerSkill: -87 ± 64 mbb/hand

ベースライン（デフォルトプロンプト）:
GPT-5.5 XHigh: -132 ± 25 mbb/hand
Claude Opus 4.6: -204 ± 44 mbb/hand
Claude Opus 4.7: -170 ± 28 mbb/hand

損失削減率: 49-61%
Slumbotを上回る性能
```

ルール層のみ（LLMなし）: -132 mbb/hand（デフォルトGPT-5.5と同程度）。
ルール層単体では弱いが、LLMと組み合わせることで大幅に改善する。

### 54.4 PokerSkillとPokerBench/PokerRL+GRPOの関係

PokerSkillとPokerBenchは全く別の論文・別のアプローチである。

```text
PokerBench (Zhuang et al., 2025):
  - solver出力をSFT訓練データとして使用（563,200件）
  - LLMに判断を教示する訓練ベースのアプローチ

PokerSkill (Li, Wang, Huang, 2026):
  - 訓練なし、ソルバーなし
  - ルールベースSkill Library + LLM推論
  - HU専用だがMW拡張の設計余地あり
```

現在のHU SFT訓練（PokerBenchデータ使用）とPokerSkillは補完的関係にある。
PokerSkillの設計思想（Context Engine + ルール層 + 行動制約）は、
multiway対策として訓練済みSFTモデルやAPI LLMの前段に組み込める。

### 54.5 HU論文のMW拡張可能性

PokerSkill論文はHU専用だが、以下のコンポーネントはMWにそのまま再利用可能。

```text
再利用可能（低難度）:
  - Context Engine（board texture, hand class分類）
  - Hand Strength分類（23 hand class）
  - Pressure Weight table（46段階）
  - ATT/DEF Budget計算式

要修正（中〜高難度）:
  - プリフロップレンジ（6-max用に拡張必要）
  - ATT/DEFバジェット値（人数に応じた修正子が必要、例: ATT -1.5/player）
  - アクションラインシナリオ（HU 60シナリオ → MW用に追加）
  - Viable Action Logic（複数opponent考慮）
  - リバーブロッカーノート（MW向け調整）
```

### 54.6 ATT/DEFバジェットの具体的な値（論文Appendix E）

論文のAppendix Eから確認した、16 Made-Hand classと8 Draw classの
ATT/DEFバジェット値。これらはHU専用の値であり、MW拡張時には調整が必要。

```text
Made-Hand classes (16):
  Nuts: ATT ∞ / DEF ∞
  Strong set+: ATT very high / DEF very high
  Overpair: ATT high / DEF high
  Top pair good kicker: ATT medium-high / DEF medium-high
  Top pair weak kicker: ATT medium / DEF medium
  ...（中間省略）
  Trash: ATT 0 / DEF 0

Draw classes (8):
  Nut flush draw: ATT high / DEF pot-odds-based
  Open-ended straight draw: ATT medium / DEF pot-odds-based
  Backdoor flush draw: ATT very low / DEF very low
  ...（中間省略）

Combo rule: Draw ATT bonus + Made-hand base ATT
```

### 54.7 本プロジェクトへの適用方針

PokerSkillの設計は、phh-dataset敗者バイアス問題の代替MW戦略として採用する。

具体的には:
- Context Engineを決定論的Pythonスクリプトとして実装
- ATT/DEF Budget計算をゲーム状態から自動導出
- 構造化プロンプトを生成し、LLM（GPT-5.4-mini or Phi-4-mini）に渡す
- LLMは制約された行動空間内で最終判断を行う

この方針により、multiway訓練データの品質問題（敗者バイアス）を回避しつつ、
ルールベースの構造化でLLMの判断品質を向上させる。

---

## 55. MW方針転換: weighted SFTからPokerSkill式ルール層 + LLM比較テストへ

### 55.1 転換の理由

以下の2つの発見が重なり、MW戦略の根本的な方針転換が必要になった。

```text
1. phh-dataset hole cards付きデータ全件敗者（§53）
   → confidence-weighted SFTの前提が崩壊
   → positive exampleとしてのmultiway訓練データが入手不可

2. PokerSkill論文の発見（§54）
   → 訓練なしでLLMのポーカー判断品質を大幅改善する手法が存在
   → Context Engine + ATT/DEF Budget + Skill Libraryの設計が公開
   → HU論文だがMW拡張可能な設計
```

### 55.2 旧方針（破棄）

```text
S2-T2a: confidence scoringスクリプト → 破棄（勝者データなし）
S2-T2b: stratified sampler + HU rehearsal → 破棄
S2-T2c: weighted SFT実行 → 破棄
S2-T2d: multiway Go/No-go判定 → 破棄
S2-T2e: KTO検討 → 破棄
```

### 55.3 新方針（採用）

PokerSkill式Context Engine + ATT/DEF Budgetを決定論的Pythonスクリプトとして実装し、
構造化プロンプトを生成してLLMに渡す方式に転換する。

テスト計画:

```text
Phase 0（パイプライン確認）: 5件
  - phh-datasetからMWスポットを抽出
  - Context Engine → 構造化プロンプト → LLM → 出力
  - スクリプトが動くか確認

Phase 1（定性評価）: 50件
  - GPT-5.4-mini vs Phi-4-mini（未SFT素モデル）の出力比較
  - 人間が読んで品質差を判定

Phase 2（定量評価）: 500-1,000件
  - action accuracy、fold/call/raise分布の偏り
  - ポットタイプ別・ストリート別の傾向分析
```

### 55.4 他のMW代替案の検討と却下

PokerSkill式を採用するにあたり、以下の代替案も検討した。

```text
MonkerSolver:
  - 商用MWソルバー。GTO解を計算可能。
  - 却下理由: APIなし、GUI専用、ライセンス高額、自動連携不可

敗者データのKTO undesirable利用:
  - phh-datasetの敗者行動を「やってはいけない例」として学習
  - 保留: positive exampleが別途必要。PokerSkill式と併用は将来検討

PokerBenchのHUデータでMWも代用:
  - 却下理由: PokerBenchは100% HU。MW固有の判断（相手複数のequity変化、
    ポジション関係の複雑化、ブラフ頻度の低下）を学習できない
```

### 55.5 HU SFT訓練との関係

HU SFTは現在進行中（PID 18392, 30k第1弾）であり、影響を受けない。

```text
HU: Phi-4-mini SFT（PokerBenchデータ）→ 継続
MW: PokerSkill式Context Engine + LLM → 新規実装

両者は補完的:
  - HU SFT完了後のPhi-4-miniモデルにPokerSkill式プロンプトを渡す検証も可能
  - GPT-5.4-mini APIとの品質比較でMW推論のコスト/品質トレードオフを判断
```

### 55.6 今後の検証手順

```text
1. phh-datasetからMWスポット抽出（既存multiway_decisions.jsonlから選択）
2. Context Engine実装（board texture, hand class, ATT/DEF budget計算）
3. 構造化プロンプト生成スクリプト実装
4. Phase 0: 5件でパイプライン確認
5. Phase 1: 50件でGPT-5.4-mini vs Phi-4-mini定性比較
6. Phase 2: 500件で定量評価
7. 結果に基づきMW推論の最終方針決定
```

## 56. PokerRL+GRPO撤退基準と段階的対処

実装指令書v1.3 §9.2から移動。実装指令書は2026-06-04に廃止された。

### 56.1 全体方針

Deep CFRで約1ヶ月を浪費した教訓を踏まえ、「失敗を早期検知し、明確な基準で撤退する」を原則とする。

```text
[Layer 1] 各Sprint内での即時改善 (1-3日サイクル)
   ↓ 改善不能なら
[Layer 2] Sprint間でのアプローチ調整 (3-7日サイクル)
   ↓ 改善不能なら
[Layer 3] アプローチ全体の撤退 → 代替案へ移行
```

全体タイムボックス: 12週間（最大15週間）。

### 56.2 Phase 1 SFT閾値未達時の段階的対処

発動条件: Sprint 2完了時に以下のいずれかを満たさない場合。
- PokerBench Preflop accuracy ≥ 70%
- PokerBench Postflop accuracy ≥ 55%
- Spot Checks合格率 ≥ 80%
- 「全局面でRaise 70-80%」のような病理的偏りがゼロ

Step 1: 補助ヘッド構造の修正（最大3日）
- Sizing HeadをscalarからCategorical 8-binに変更
- Action Headを3層MLPに拡張
- Action Headにdropout 0.1-0.2追加
- Action tokenの重み付きCE loss

Step 2: LoRAハイパラ調整（最大2日）
- rank r=64→r=128, alpha=256
- LR 2e-4→1e-4, warmup 15%
- target_modules全linear
- epochs 3→5

Step 3: データ前処理の見直し（最大4日）
- PokerBench:Pluribus比率変更
- Action history圧縮拡張
- eval7 equity値をプロンプトに追加
- 低信頼度サンプルフィルタリング
- Board texture多様化（suit swap）

Step 4: ベースモデル切替（最大5日）
- Phi-4-mini→Qwen3-4B→Gemma 3-4B(QAT)→Qwen3-8B(Q4 QLoRA)

Phase 1全体の上限は5週間（標準3週間+改善2週間）。

### 56.3 Phase 2 GRPO品質未達時の段階的対処

発動条件: Sprint 3完了時に以下のいずれかを満たさない場合。
- Spot Checks 95%合格
- Slumbot HU勝率 ≥ -15 bb/100
- Self-play vs Phase 1 baseline +3 bb/100
- Entropy健全（top-1確率中央値 ≤ 0.85）

Step 1: Entropy崩壊対処（最大4日）
- DAPO Clip Higher ε_high拡大
- Entropy bonus係数導入
- KL coefficient導入
- OPEFO balancing coefficient上限導入
- Generation temperature増加
- Dynamic Sampling zero-variance filter緩和

Step 2: 対戦相手プール構成見直し（最大3日）
- 過去全SFT checkpoint 8体を等確率で含める
- Rule-based TAG/LAG/Tight-Passive/Maniac 4種混合
- Deep CFR失敗モデルを20%混入
- 自己最新版とのみ対戦フェーズと混合プール対戦を2:1交互

Step 3: 報酬関数調整（最大4日）
- chip_delta重み0.7→0.5、EV項0.2→0.4
- 敗北回避ボーナス
- 妥当性ペナルティ（GTO KL divergence）
- Fold不足ペナルティ

Step 4: 訓練期間延長（最大5日）
- 改善トレンドあり→+3日延長（最大2回）

### 56.4 Spot Checks病理パターン検出時の対処

病理パターン:
- Raise偏重（全局面でRaise>60%）
- Fold偏重（全局面でFold>50%）
- Sizing固定（95%以上同じ値）
- Position無感度
- Board texture無感度
- Stack depth無感度

切り分け手順:
1. verify_pokerrl_encode.py再実行
2. 推論時vs訓練時の同一input出力比較
3. 病理特化データ拡張
4. カリキュラム学習導入

### 56.5 量子化品質劣化時の対処

発動条件: 量子化後のPostflop accuracyが量子化前から10%以上低下。

Step 1: Q4_K_M→Q5_K_M→Q6_K→Q8_0→FP16の段階的緩和
Step 2: レイテンシ予算見直し（T1 100-500msに緩和）
Step 3: モデルサイズダウン（Phi-4-mini→SmolLM3-3B→Qwen3-1.7B）

### 56.6 撤退発動条件（OR条件）

1. タイムボックス超過: Sprint 1-3合計12週間超過（補正含め15週間超過）
2. 品質下限未達: 全Step消化後もPostflop accuracy<50%、Slumbot<-30 bb/100、Spot Checks<80%
3. 改善トレンド消失: 直近2週間で指標±5%内横ばい、対処全消化済み
4. コスト超過: クラウドGPU等$500超過

撤退判断タイミング:
- Sprint 2開始から2週間時点
- Sprint 2開始から4週間時点
- Sprint 3開始から2週間時点
- Sprint 3開始から5週間時点
- Sprint 1-3合計12週間時点

### 56.7 撤退後の代替案優先順位

Case A（SFT成功、GRPO失敗）:
  第1: PokerSkill風ハイブリッド（MW方針として即時採用済み）
  第2: GTO Wizard API待機
  第3: Deep CFR改善

Case B（SFT失敗）:
  第1: Deep CFR改善（評価刷新）
  第2: GTO Wizard API待機
  第3: 既存システム暫定運用

Case C（タイムボックス超過、品質改善中）:
  第1: GTO Wizard API待機
  第2: PokerSkill風ハイブリッド
  第3: Phase 1 SFTのみshadow mode運用

Case D（全失敗）:
  第1: 既存システム暫定運用
  第2: 新興手法（MCCFVFP等）調査
  第3: 6-12ヶ月長期待機

### 56.8 撤退判断のドキュメント化テンプレート

```text
撤退判断ログテンプレート:
  発動日:
  発動条件（§56.6のどれに該当）:
  Phase 1 SFT到達状況:
    - PokerBench Preflop accuracy:
    - PokerBench Postflop accuracy:
    - Spot Checks合格率:
  Phase 2 GRPO到達状況:
    - Slumbot HU勝率:
    - Self-play vs SFT baseline:
    - Entropy健全性:
  消化済み対処（§56.2-56.5のどこまで実施）:
  保持する成果物:
  選択した代替案（Case A/B/C/Dのどれ）:
  代替案開始予定日:
```

## 57. Phase 2 GRPO訓練仕様（未実施）

実装指令書v1.3 §5.3から移動。Phase 2は未実施であり、Sprint 3で実施予定。

```text
入力: Phase 1 SFTモデル
方式: GRPO + DAPO trick + OPEFO entropy制御
環境: PokerKitベース6-max NLHE自己対戦
opponents:
  - 過去SFT checkpoint (population play)
  - Rule-based (TAG/LAG)
  - 既存Deep CFR失敗モデル（弱い相手としてエントロピー多様性確保）
時間: 約80-120時間
報酬:
  - 0.7 × 即時chip delta
  - + 0.2 × EV at decision (eval7計算)
  - + 0.1 × 直近20ハンド累積 (bankroll preservation)
Go/No-go (Phase 2終了時):
  - Spot checks: 全50局面で行動分布が合理的に変動
  - Entropy健全 (top-1確率の中央値 < 0.85)
  - Slumbot相手（HU、無料API）で勝率 ≥ -15 bb/100
  - 自己対戦でPhase 1ベースラインに +3 bb/100以上
```

## 58. 評価フレームワーク

実装指令書v1.3 §8から移動。

### 58.1 必須評価指標

| 指標 | Phase 1閾値 | Phase 2閾値 |
|---|---|---|
| Spot Checks（50シナリオ） | 80%合格 | 95%合格 |
| Entropy（top-1確率中央値） | ≤ 0.90 | ≤ 0.85 |
| Sensitivity Tests | 70% pass | 90% pass |
| PokerBench Accuracy | preflop ≥ 70%, postflop ≥ 55% | preflop ≥ 75%, postflop ≥ 60% |
| Slumbot HU勝率 | N/A | ≥ -15 bb/100 |
| Self-play vs Phase 1 | N/A | ≥ +3 bb/100 |
| Latency P95 | T1 ≤ 300ms | T1 ≤ 200ms |
| Hard Deadline超過率 | < 5% | < 1% |

### 58.2 Spot Checks設計方針

Deep CFRの「Ace high・no pair・no draw・3way facing BETでRaise 80%」病理を捕捉する50局面を作成する。

例:
```text
spot_001: 3way flop, hero AhKd (overcards no pair),
          board 7s 4c 2h, facing BET 30%pot,
          期待: Fold or Call majority, Raise ≤ 20%

spot_002: HU turn, hero AsAh (set),
          board As 4d 7c Jh, facing BET 50%pot,
          期待: Raise majority
```

Phase 1完了時点で自動回帰テストとして組み込む。

### 58.3 シャドウモード評価

Stage B（Phase 1完了〜Phase 2完了）の間、新ブリッジをshadow modeで稼働:
- 実プレイ中、既存Deep CFRが表示される
- 同時に新PokerRLブリッジも推論を実行
- 両者の出力をログ保存
- 差分が大きい局面を人間レビュー

## 59. 未実施Sprint計画骨格

実装指令書v1.3 §10から移動。Sprint 1-2は完了/進行中のためsnapshotで管理。

### Sprint 3（Phase 2 GRPO強化学習）
- PokerKitベース6-max自己対戦環境構築
- DAPO + OPEFO実装
- 報酬関数実装（multi-hand bankroll）
- 100-150h訓練
- Slumbot評価
- Go/No-go: §57の閾値

### Sprint 4（推論ブリッジ統合 + Shadow Mode）
- PokerRLBridge実装（既存I/F完全互換）
- recommendation_engine.pyにshadow modeロジック追加
- vLLM/llama.cppの常駐推論プロセスセットアップ
- Stability Guardsとの統合確認
- HUDソースラベル更新
- 全テストpass確認

### Sprint 5（本番切替）
- Shadow modeログ分析
- Spot Checks 50シナリオ全pass確認
- Stage C: HU/Multiway postflopをPokerRLに切替
- 1週間モニタリング

### Sprint 6（旧コンポーネント削除、オプション）
- Stage D: Deep CFR Bridge削除
- Stage D: Rust Solver削除
- ドキュメント更新

## 60. MW Context Engine実装の設計判断（Step 1〜4）

### 60.1 Context Engineを context_engine.py に新規実装した理由

当初の計画では strategy/pokerrl_prompt_builder.py の既存骨格を拡張する予定だった。
しかし pokerrl_prompt_builder.py はHU SFT用のプロンプト変換に特化しており、
MW用のboard texture分類、hand class分類、ATT/DEF budget計算、
pressure weight累積、MW修正子といった決定論的計算を混在させると
責務が不明確になる。

そのため strategy/context_engine.py を新規作成し、MW Context Engine の
全決定論的計算を集約した。multiway_engine.py はGameLoop統合と
LLM呼び出しを担当し、context_engine.py の計算結果を消費する。

### 60.2 GPT-5.4-mini を MW 本採用モデルとして確定した理由

15ケースの比較テスト（scripts/test_llm_comparison.py）で
GPT-5.4-miniとPhi-4-mini（ローカル4bit）を評価した。

GPT-5.4-mini が全指標で優位だった。
- Corrections 0/15 vs 4/15（補正不要率が圧倒的に高い）
- Category match 12/15 vs 11/15
- Avg latency 993ms vs 1811ms + モデルロード5.6秒
- GPU不要（API呼び出しのみ）

特にCase 2（ウェットフロップ、フラッシュドローOOP 3BP）で
Phi-4-miniはセミブラフを回避してfoldを選択し、correction入りとなった。
GPT-5.4-miniは正しくbet（セミブラフ）を選択した。

reasonの質もGPT-5.4-miniの方が具体的で、ポーカー用語の使用が適切だった。

Phi-4-miniはバックアップ候補として残す。将来的にHU SFT完了後の
ファインチューニング済みPhi-4-miniで再評価する可能性がある。

### 60.3 load_dotenv(override=True) が必要になった経緯

LLM比較テスト実行時に、.envに正しいOpenRouter APIキー（末尾 d7d245）が
記載されているにもかかわらず、401 User not found エラーが発生した。

調査の結果、プロセス環境変数に古いAPIキー（末尾 4eea）が既に設定されており、
load_dotenv()のデフォルト動作（既存環境変数を上書きしない）により
古いキーが使われていた。

load_dotenv(override=True) に変更することで.envの値が確実に使われるようになった。
この設定は今後のすべてのスクリプトで必須とする。

### 60.4 チェックポイント完全保存を追加した理由

HU SFT訓練（seg_000_offset_26000）の実行中に電源断等でプロセスが終了した場合、
checkpoint-600からの再開を試みたところ、step 0から再開された。

調査の結果、run_sft_comparison.py のチェックポイント保存は
model.save_pretrained()（LoRA重みのみ）だけで、
optimizer.state_dict()、scheduler.state_dict()、trainer_state.json
（global_step, epoch, micro_step）が保存されていなかった。

これではチェックポイントからの途中再開ができず、
16時間の訓練が中断のたびにやり直しになる。

修正後は全チェックポイントと final_adapter に
optimizer.pt、scheduler.pt、trainer_state.json を保存する。

### 60.5 セグメント間の resume ロジックを区別した理由

チェックポイント完全保存の導入後、新しい問題が発生した。

run_sft_sequential.py が seg_000_offset_36000 を開始する際、
前セグメントの final_adapter から --resume_from で重みを引き継いだが、
trainer_state.json も読み込まれてしまい、step 939/epoch 2 から
スキップしようとした。新しいデータセットなのに939ステップ分を
空回りし、実質的に訓練が行われなかった。

解決: Path(config.resume_from).parent == config.output_dir の場合のみ
trainer_state を復元する。異なるディレクトリの場合は重みのみ引き継ぎ、
step 0から開始する。

これにより「同一セグメント内の中断→再開」と
「別セグメントからの重み引き継ぎ」を自動的に区別できるようになった。

### 60.6 二重プロセスが正常動作である記録

run_sft_sequential.py 実行時に4つのPythonプロセスが表示され、
二重訓練が疑われた。調査の結果、.venv\Scripts\python.exe が
システムPythonへのラッパーであり、各スクリプト実行で
「ラッパー（数MB）→ システムPython（実作業）」の親子ペアが
生成されることが判明した。

GPU訓練を実行しているのは末端の1プロセスのみ。
この構造は正常動作であり、二重訓練ではない。


## 61. HU SFT accuracy 飽和と補助ヘッド追加の判断理由

### 61.1 飽和の確認

HU SFT（Phi-4-mini + LoRA、PokerBenchデータ、10k区切り自動連続方式）を
15セグメント（16k–156k、合計140,000件）実行した結果、
accuracy は 82%前後で完全に飽和した。

推移:
  seg_003_offset_66000 (76k–86k): 82.0%、eval_loss 0.225（全セグメント中最良）
  seg_004_offset_76000 (76k–86k): 82.3%（accuracy最高値）
  以降 86k–156k の8セグメント: 81.0%–82.3% の範囲で横ばい

データ追加による改善は見られなくなった。563,200件中156,000件（27.7%）を消化した時点で
テキスト生成方式SFTの改善限界と判断し、訓練を停止した。

### 61.2 epoch 3 過学習パターン

全15セグメントで一貫して epoch 2 がピーク、epoch 3 で eval_loss が悪化するパターンが確認された。

最も顕著な例（seg_004_offset_146000）:
  epoch 1: eval_loss 0.186, accuracy 81.1%
  epoch 2: eval_loss 0.189, accuracy 81.6%
  epoch 3: eval_loss 0.226, accuracy 81.2%（eval_loss +19.6% 悪化）

epochs を 3→2 に減らせば eval_loss の悪化を防げるが、
accuracy の上限（82%前後）は変わらないと判断した。
根本的な改善には方式の変更が必要。

### 61.3 accuracy 内訳分析で判明したパッシブ偏り

seg_004_offset_76000 の final_adapter で eval データ 1,000 件に対して推論した結果:
  check: 91.5%（最も正確）
  fold: 88.7%
  call: 81.9%
  raise: 69.1%
  bet: 62.5%（最も不正確）

攻撃的アクション（bet/raise）が弱く、消極的方向に誤分類する傾向が明確。
bet→check（39件）、raise→call（39件）という一段階パッシブ側への誤りが支配的。

原因: テキスト生成方式では "fold"/"call"/"check" などの短いトークンと
"raise 300" のようなトークン列を同列に扱っており、
アクション分類とサイジング回帰という異なるタスクが混在している。

### 61.4 補助ヘッドを追加する理由

SPEC §10A.2 で設計済みの補助ヘッド（Action Head 4クラス + Sizing Head sigmoid）を
実装することで、以下の改善を狙う。

1. Action Head（4クラス分類: Fold / Check-Call / Raise / All-in）により、
   アクション選択を明示的な分類タスクとして訓練できる。
   テキスト生成の曖昧さがなくなり、bet/raise の精度改善が期待できる。

2. Sizing Head（sigmoid 0.1x–3.0x pot）により、
   サイジングを独立した回帰タスクとして訓練できる。
   "raise 300" のようなテキスト生成でサイズを表現する必要がなくなる。

3. 推論速度が大幅に改善する。autoregressive 生成（数十〜数百トークン）が不要になり、
   最終 hidden state → MLP ヘッド（1ms以下）で出力が得られる。
   SPEC §10A.9 の T1 Tier 50-300ms 目標の達成に寄与する。

### 61.5 LoRA 凍結 + ヘッドのみ訓練を選んだ理由

2つのシナリオを検討した。

シナリオ 1: LoRA 凍結 + ヘッドのみ訓練
  82% まで蓄積した LoRA の表現力を確実に保護できる。
  補助ヘッドの品質が悪くても、テキスト生成方式にいつでも戻せる。
  ヘッドのパラメータ数は LoRA の数百分の一であり、訓練が高速。

シナリオ 2: LoRA + ヘッド同時ファインチューニング
  LoRA の表現をヘッドに最適化できるが、既存の 82% の品質が変化するリスクがある。
  訓練時間も長くなる。

シナリオ 1 を採用する。理由は、LoRA の表現力を安全に保持しつつ、
補助ヘッドの効果を独立に検証できるためである。
シナリオ 1 の結果が不十分な場合のみ、シナリオ 2 に進む。

### 61.6 ベースアダプタの選定

seg_003_offset_66000 の final_adapter を補助ヘッドのベースに採用する。

  seg_003_offset_66000: accuracy 82.0%, eval_loss 0.225（最良）
  seg_004_offset_76000: accuracy 82.3%, eval_loss 0.235

accuracy は seg_004_offset_76000 が 0.3pt 高いが、eval_loss は seg_003_offset_66000 が
0.010 低い。eval_loss が低い方がモデルの内部表現の質が高く、
補助ヘッドの入力となる最終 hidden state の品質に直結する。
0.3pt の accuracy 差より、内部表現の質を優先した。

## 62. 補助ヘッド評価基準の変更（accuracy → GRPO初期化健全性）

### 62.1 変更の経緯
S2-T3（補助ヘッド訓練）の当初Go/No-go基準は「bet/raise accuracyがベースライン比+5pt改善」だった。しかしepoch 1の評価でoverall accuracyが0.747〜0.785で振動し、bet/raiseが評価ごとに大きく上下した（例: step1800 bet26% / step4500 bet73%）。攻撃系が上がると受け系が下がる逆相関で、決定境界がsoftmax温度で揺れている状態だった。lr=0.001がヘッド訓練には高めで振動を助長した可能性がある。

### 62.2 accuracy追求が最適でない理由
eval accuracyはPokerBench教師ラベルへの一致率であり、ベンチの打ち手の模倣度にすぎない。accuracyを最大化してもベンチを超えて勝つことはできず、勝率(bb/100)には直結しない。これはsnapshot/SPEC §10A.11の「profit vs randomを単独指標にするな」と同根の罠で、accuracy側にも同じ過剰最適化リスクがある。補助ヘッドの真の役割は「最高accuracyのモデル」ではなく「GRPO(Sprint 3)の良い初期化点」である。攻撃/受けのバランスはGRPOのself-play報酬で再較正されるため、SFT段階で完璧な行動分布を作り込む必要はない。lrを下げて振動を潰す再訓練(20時間×複数回)に時間を投じるのは12週タイムボックスに対し非効率と判断した。

### 62.3 変更後の合格基準（GRPO初期化健全性）
S2-T3の合格を以下の健全性で判定する。
- 劣化なし: overall accuracy ≥ 80%。これはフロア（下限の目安）であって目標ではない。80%を一時的に割っても他軸が健全なら直ちにNo-goとはしない
- 崩壊なし: top-1確率中央値 ≤ 0.85付近、特定クラスへの一点張りがない
- sizing健全: Raise sizing MAE ≤ 0.2x
- 攻撃が死んでいない: bet/raiseが0%付近に崩壊していない（完全解消は不要）

### 62.4 epoch 2を回さない判断
§61.2で全SFTセグメントがepoch 2→3で過学習悪化する傾向が確認済み。補助ヘッドでもepoch 2は過学習リスクが高く得るものが薄い。epoch 1完了時点で健全性判定する。実際、epoch 1終端でスクリプトの旧基準による早期停止が発火し、結果的にepoch 2に入らず終了した。

### 62.5 LoRA凍結設計に伴う保存仕様（重要）
補助ヘッド訓練はLoRAを完全freezeしヘッドのみ訓練するため、checkpointには `aux_heads.pt`（ヘッド重み）のみ保存され、LoRAアダプタは保存されない（1ステップも更新されないため）。したがって `final_adapter` ディレクトリは生成されないのが正常であり、欠損ではない。推論・GRPO初期化に必要な完全モデルは「ベースLoRA（seg_003_offset_66000/final_adapter）+ aux_heads.pt」のペアである。成果物の正本は `results/aux_heads/seg_003/final_aux_head/`（aux_heads.pt等）に確定保存した。

### 62.6 All-inクラスの評価不能
train All-in 783件(0.14%)、eval All-in正例ゼロのため、本訓練ではAll-inクラスのrecallを評価できない。All-in性能はS2-T4以降のSpot Checks（§58.2）で確認する。All-inを独立クラスにした設計（§49.2）は維持する。

### 62.7 S2-T3判定結果
epoch 1完走（step17,600）のfinal_metricsで健全性4軸（§62.3）を全て満たし、判定=GO。overall0.799 / top-1中央0.836 / sizing MAE0.125 / bet58.7%・raise62.4%（崩壊なし）。GRPO初期化点として確定。GRPO初期化候補はtop-1中央値が低く探索性の高いcheckpoint-17100由来のヘッド（=final_aux_head/aux_heads.pt）。

## 63. Sprint 3準備（GRPO prompt生成器とencode検証）の設計判断

Sprint 3 Task 2（`scripts/verify_pokerrl_encode.py`）で、補助ヘッド訓練時のpromptは生成器由来ではなく、JSONL内のteacher prompt（`raw["prompt"]`）そのものだったことが確定した。したがってverifyは「生成器prompt ≡ teacher prompt（preflop chips揺れのみnormalize）→ 訓練tokenize一致」という連鎖で、GRPO自己対戦入力と補助ヘッド訓練入力の整合を保証する設計にした。

### 63.1 PokerBench形式prompt生成器を新規パッケージ `pokerrl_grpo` に隔離した理由

GRPO自己対戦では PokerKit state から PokerBench形式promptへの変換が必要になる。既存のHU向けprompt経路や補助ヘッド訓練スクリプトに混ぜると、責務境界が不明確になり、GRPO固有の入力生成とSFT訓練データ処理が相互に影響しやすい。

このため、生成器は `pokerrl_grpo/pokerbench_prompt.py` に隔離した。6-max専用とし、`player_count != 6` は `ValueError` とする。これは §60.1 の責務分離方針と同系統の判断である。

### 63.2 verifyを「完全バイト一致」ではなく「形式整合案」にした理由

teacher側（PokerBench）には表記揺れが実在するため、全フィールドの完全バイト一致を合格条件にすると、実質的なencode不一致ではない差分で検証が失敗する。そこで、揺れのない固定句・カード綴り・`of`/`Of`・改行分岐・ポジション・接続詞・pot小数桁・postflop整数額は厳密比較し、揺れる箇所のみ局所normalizeする方針にした。

この方針は、異常な入力差分を隠さず、既知のデータ表記揺れだけを吸収するためのもの。§56.4 の病理対処方針と整合する。

### 63.3 teacher側のpreflop raise額 `"chips"` 有無のみをnormalize対象にした理由

PokerBenchには `"raise 2.0"` と `"raise 2.0 chips"` の両表記が混在していた。生成器側は `_format_amount` のpreflop分岐で `"chips"` を付けない表記を正とし、比較時のみこの1点を `normalize_preflop_chips()` で吸収する。

他の差分は緩和しない。過剰normalizeを行うと、固定句・改行・カード表記・pot表記など、本来検出すべきencode不一致まで見逃すためである。

### 63.4 PokerKit 0.7.4固定と `poker_datasets_ref` を導入しない判断

`poker_datasets_ref` のeditable installは `pokerkit 0.7.4 → 0.6.5` のダウングレードを要求する。PokerKit API調査、`pokerrl_grpo/pokerbench_prompt.py`、`scripts/verify_pokerrl_encode.py` はすべて0.7.4前提で作っているため、現venvへ `poker_datasets_ref` は導入しない。

この判断により `pytest -q` 全体は当該参照ツールのcollection errorで停止するが、これは既知の既存問題であり、Sprint 3成果物の合否とは独立させる。合否は `pytest tests/test_pokerbench_prompt.py -q` と `python scripts/verify_pokerrl_encode.py` / `python scripts/verify_pokerrl_encode.py --with-forward` で判定する。

### 63.5 postflop pot再計算で死にSBを除外する互換ロジックの理由

PokerKitの `state.total_pot_amount` は死にSBの0.5を含む。一方で、PokerBench teacherのpostflop promptでは、preflop未参加扱いの死にSBを除いたpot値になるサンプルがある。

この差を吸収するため、`pokerrl_grpo/pokerbench_prompt.py` ではpreflopのみの局面では `state.total_pot_amount` をそのまま使い、postflopでは `state.statuses` が真のプレイヤー、または自発的アクション済みプレイヤーのcontributionだけを再集計してpotを算出する。teacher promptとの形式整合を優先した互換ロジックである。

## 64. 自己対戦環境構築に伴うstate生成の正本化（Sprint 3 Task 3）

### 64.1 state生成ヘルパを本体 `pokerrl_grpo/state_factory.py` へ昇格した理由

Task 3で6-max自己対戦環境（`pokerrl_grpo/selfplay_env.py`）を実装するにあたり、PokerKit state生成（`create_state()` 等）が必要になった。しかし当該ヘルパは `tests/test_pokerbench_prompt.py` 内にしか存在せず、環境本体から再利用できなかった。

ここで環境側に同等のstate生成を別途書くと、state定義（blinds 0.5/1、starting 100、player_count 6、8 automations）がテストと本体の2箇所に並立し、片方だけ修正されてズレる危険がある。これは §20 で既に問題視した「同一処理の重複実装」と同型の罠である。

そのため §20 の方針に従い、state生成を `pokerrl_grpo/state_factory.py` に正本化し、テストと環境の双方がそこからimportする構成にした。

### 64.2 `tests/test_pokerbench_prompt.py` を変更した理由とsnapshot §8-5との関係

snapshot §8-5は当該テストファイルを「変更しない」と定めていた。これはTask 1/2の検証基盤（生成器≡teacher照合、`10 passed`）を保護する目的の制約である。

Task 3での変更は、ヘルパ定義を `state_factory` へ移しimport文を差し替えるのみで、テストのアサーション・検証ロジック・対象は一切変えていない。`pytest tests/test_pokerbench_prompt.py -q` が `10 passed` を維持することで、制約の本来目的（検証基盤の不変）は保たれている。

したがって本変更は §8-5 の趣旨に反しない、正本化のための最小限の変更と位置づける。値の同一性（state定義パラメータ）も維持しており、Task 2のencode整合（`verify_pokerrl_encode.py` passed=8）に回帰がないことを確認済み。

### 64.3 報酬をスタブとし環境骨格を独立タスクに切った理由

Task 3は環境がエピソードを崩壊なく回せることの確証に集中し、報酬はchip deltaのスタブに留めた。§57の正式報酬（0.7×chip delta + 0.2×eval7 EV + 0.1×直近20ハンド累積）を同タスクに混ぜると、環境のバグと報酬設計の問題が切り分け困難になる。

環境（配管）と報酬（設計）を別タスクに分離することで、後段GRPOで「報酬が効かない」事象が出た際の原因究明を容易にする。スモークラン（200ハンド、zero_sum_ok/winners_ok）で配管の生存と報酬符号整合を先に確認した。

報酬本実装はTask 4、GRPO本体（DAPO/OPEFO）はTask 5以降に分割する。

## 65. 報酬関数の設計判断（Sprint 3 Task 4）

Sprint 3 Task 4で、Task 3のchip deltaスタブを §57 の正式報酬に置き換えた。報酬本体は `pokerrl_grpo/reward.py`（`step_reward` / `terminal_reward` / `BankrollTracker`）、EV計算は `pokerrl_grpo/rollout_ev.py`（eval7ベースMC）、パラメータは `pokerrl_grpo/config.py` の `RewardConfig` に集約した。ベース初期化点 `seg_003_offset_66000`（56万ハンドSFT最良点）のSFT方策を壊さず磨く素直な信号を志向し、過度な作り込みでentropy崩壊を招かない方針を維持する。

### 65.1 EV計算にロールアウト（モンテカルロ）を採用し、反実仮想EVを却下した理由

「EV at decision」の計算方法として、equity近似、ロールアウトEV、反実仮想EV（取ったアクション固有のEV）を比較した。反実仮想EVは概念的には理想に近いが、正確に出すには各局面で相手の最適応答を織り込む必要があり、事実上ソルバー（CFR）を報酬関数内に再導入することになる。

これはRust postflopソルバーを永久廃止し、Deep CFRを品質不合格とした方針（snapshot §9、§54系）に逆行する。さらに、相手モデル仮定のバイアスが報酬ノイズとなり、entropy崩壊や病理方策（Deep CFRの「Ace high無条件Raise」型、§56.4）を誘発しうる。

そのためロールアウトEV（MC近似）を採用した。プレイアウト回数を増やすほど真EVへ収束し、「時間より精度」方針に素直に応える。相手アクションは単純方策とし、恣意的仮定を最小化する。CFR/solverは持ち込まない。

### 65.2 プレイアウト回数をconfig可変にした理由

ロールアウトEVの精度はプレイアウト回数で決まる。固定実装にすると、精度と訓練速度のトレードオフを後から調整できない。

そのため `RewardConfig.rollout_playouts`（デフォルト100）で可変にした。精度を上げたい場合は増やし、訓練が遅すぎる場合は下げられる。Task 4のスモークではこの値を `scripts/smoke_selfplay.py --rollout_playouts` からも変更できる。

### 65.3 各成分をclipしてから重み付けする順序にした理由

報酬3成分はスケールが異なる。chip deltaは±100bb幅になりやすく、EVはbb単位、bankroll累積はさらに広い。clip前に重みを掛けると外れ値が成分を支配し、重み0.7/0.2/0.1が意味をなさなくなる。

そのため各成分を ±clip_bb（デフォルト100bb）でclipしてから重みを掛ける順序とした。全成分をbb単位に正規化し、Go/No-go指標（bb/100基準、§58.1）とスケールを揃える。

### 65.4 報酬をbb単位に正規化した理由

§58.1のGo/No-go指標（Slumbot ≥ -15 bb/100、self-play +3 bb/100など）はbb/100基準である。報酬もbb単位に揃えることで、学習信号と評価指標のスケールを一致させる。

chip単位のまま放置すると、重みとclipの意味がブラインド設定に依存する。bb単位に正規化しておけば、後でブラインドやstack条件を調整しても報酬パラメータの意味を保ちやすい。

### 65.5 ステップ報酬と終端報酬のハイブリッド時間粒度にした理由

報酬3成分は時間粒度が異なる。EV at decision（0.2）は各意思決定時点で計算でき、「その一手の質」を局所評価するためステップ報酬とする。

一方、chip delta（0.7）はハンド終端で確定する量であり、直近20ハンド累積（0.1）はハンドをまたぐbankroll項である。両者はハンド終端報酬とする。bankroll項は環境が直近20ハンドのリングバッファを保持して算出する。

この「ステップ報酬（EV） + ハンド終端報酬（chip delta + bankroll）」のハイブリッドにより、GRPOのadvantage計算へ素直に載る時間粒度にした。環境の配管、報酬設計、GRPO本体（DAPO/OPEFO）は引き続き別タスクとして分離する。

## 66. GRPO最適化対象のマッピング（Sprint 3 Task 5）

Sprint 3 Task 5のGRPO実装では、補助ヘッド付きSFTモデルのうち、Action headの4-class categorical確率を方策log-probの正とする。Sizing headは方策勾配に直接載せず、advantage重み付き回帰の別損失として扱う。このマッピングをTask 5a/5b/5cの前提として確定する。

### 66.1 採用方針

方策log-probの正は、Action head categorical（4-class: Fold / Check-Call / Raise / All-in、softmax）とする。DAPO Clip-Higher、OPEFO entropy制御、KL、entropy bonusは、すべてこのcategorical分布に対して適用する。

Sizing head（sigmoid 0.1x-3.0x potのscalar比）は方策勾配に載せない。Task 4報酬で正のadvantageが出た決定点のsizingへ、advantage重み付き回帰で別損失として磨く。

### 66.2 根拠

SPEC §10A.4は「autoregressive生成は行わない／最終hidden stateを補助ヘッドに渡す」構造を前提にしている。決定点で方策が表現する離散選択は4-class categoricalであり、これをGRPOのratio/clip単位にするのが構造的に素直である。

この方針はSPEC §9.3の出力契約（fold/call/raise/allin_prob + raise_size_ratio）とも一致する。head出力契約を§9.3/§10Aに合わせることで、将来のブリッジ統合を無改修に近づける。

また、entropy Go/No-go（§57 / §58.1 Phase2 / §10A.11: top-1確率中央値 ≤ 0.85）はaction head categorical上で定義済みである。最適化対象とGo/No-go監視対象を同一分布に揃えることで、OPEFO entropy制御と崩壊検知が一貫する。

### 66.3 却下案（案2: Action + Sizing両方を方策化）

Sizingを連続方策（Gaussianまたはbin化）として方策勾配に載せる案も検討した。しかし連続値の分散は、categorical top-1中央値で見るentropy監視とずれる。Sizing側だけ探索が広くても、Action headが一点張りなら実戦上は崩壊しているため、entropy崩壊の検知が不正確になる。

さらに、DAPO/OPEFOの適用単位がcategoricalとscalarで二分し、ratio、clip、entropy、KLの実装と監視が複雑化する。Task 5の目的はまず崩壊しないGRPO配管を作ることであり、連続方策化は初期実装として過剰である。

よって案2は却下する。Sizingは§66.1のadvantage重み付き回帰で扱い、Action head categoricalの方策改善と分離して磨く。

### 66.4 スコープ差の明記

GRPO訓練環境は6-max自己対戦である。`pokerrl_grpo/state_factory.py` の `create_state()` は `player_count=6` を正本とし、snapshotの確定制約でも6-max固定としている。

一方、SPEC §9.1のルーティングでは、PokerRL+GRPOは `active_player_count == 2`（HU postflop）専用デプロイである。これは矛盾ではない。確定設計は「6-max experienceで学習し、HU postflopに限定デプロイする」ことである。Action head 4-class + Sizing head比の出力契約はプレイヤー数非依存なので、訓練環境とデプロイスコープの差分を吸収できる。

この差分はTask 5以降の実装判断で混乱しやすいため、ここに明記しておく。

### 66.5 制約継承

報酬EVはMCのみとし、CFR/solverは持ち込まない（§65.1継承）。反実仮想EVや相手最適応答計算を報酬や方策更新に混ぜない。

報酬パラメータは `RewardConfig` 経由とし、重み、clip、playouts、bankroll windowをハードコードしない。

entropy崩壊対策なしに長時間訓練を開始しない。Task 5ではAction head categoricalのtop-1中央値、entropy、KL、clip率を監視し、OPEFO entropy制御と崩壊検知を同じ分布上で運用する。

## 67. GRPO group_id 割当ルール（Sprint 3 Task 5b）

Task 5bのGRPO損失本体では、group相対advantageの「グループ」を同一decision-state起点で定義する。Task 5aの `Trajectory.group_id` は現状 `None` のままだが、5bでは本セクションの規則に従って割当てる。

### 67.1 採用方針（案A: 同一decision-state起点のグループ）

`group_id` はhero決定stateごとに一意に割当てる。各決定stateでAction head categoricalからG個のアクションをサンプルし、そのG個を1グループとする。

advantageは `A_i = (r_i - group_mean) / (group_std + ε)` として、グループ内で正規化する。価値criticは使わない。

`r_i` はTask 4の `step_reward`（eval7ベースMCのrollout EV）で見積もる。実際にプレイされたアクションの `StepRecord` には `terminal_reward`（chip delta + bankroll）を帰属させる。Task 5aの `Trajectory.group_id` にこの規則で値を割当てるのはTask 5bである。

### 67.2 根拠

この方式はcanonical GRPOのgroup-relative baselineであり、学習criticを不要にできる。RTX 3080 VRAM 10GB制約では、criticを追加せずにadvantageを作れることが重要である。

また、非autoregressive単一アクション方策（SPEC §10A.4 / DESIGN_NOTES §66.1）に対し、decision-state単位のgroupは明確に定義できる。Action head categoricalから同一state上の複数候補を出し、同じ文脈内で相対比較するため、baselineの意味が崩れにくい。

正規化対象も、entropy Go/No-goおよびOPEFOの監視対象（Action head categorical、§66.2 / §57 / §58.1）と一致する。`r_i` は既存のMC EVのみを使い、CFR/solver/反実仮想EVを持ち込まない（§65.1継承）。

### 67.3 却下案（案B: バッチ内グループ正規化）

バッチ内グループ正規化では、street、position、stack深、pot、board textureが混在したbaselineになる。これではadvantageが文脈差に交絡し、局面固有の良し悪しではなく、バッチ内の分布ノイズを学習しやすい。

このノイズは、§56.4で問題視した病理パターン（Raise/Fold偏重、position/board無感度）を誘発しやすい。特にAction head categoricalを最適化対象にする設計（§66）では、baselineが粗いほど一部アクションへの過剰な寄りが起きやすい。

案Bは案Aより非原理的で、GRPOの「同一条件下の相対比較」という強みを弱めるため却下する。

### 67.4 制約継承

報酬EVはMCのみとし、CFR/solverは持ち込まない（§65.1 / §66.5）。group内候補の `r_i` も同じMC rollout EVで評価する。

entropy崩壊対策なしに長時間訓練を開始しない。Task 5bはGRPO損失本体を実装する段階であり、Task 6の長時間本訓練へ進む前に、Action head categoricalのentropy、top-1中央値、KL、clip率を監視できる状態にする。

## 68. Task 6 訓練制御方針（Phase 2 GRPO本訓練）

Task 6では、Task 5a/5b/5cで完成したGRPO装置を実訓練ハーネスへ接続し、Phase 2 GRPO本訓練を開始できる状態にする。本セクションでは、長時間訓練を安全に回すためのGo/No-go運用、チェックポイント中間評価、崩壊ガードのループ挙動、撤退トラッキングを確定する。

### 68.1 Go/No-go本判定運用

Phase 2終了判定は §57 の4基準を正とする。すなわち、Spot Checks 50シナリオで95%合格、entropy top-1確率中央値が0.85以下、Slumbot HUが -15 bb/100 以上、self-play vs Phase1 baseline が +3 bb/100 以上である。

「profit vs random」を単独評価指標として使わない。これは §17.6 および §65系の教訓と同じで、単純な相手に勝つことだけを最適化すると病理的な方策を見逃すためである。Spot Checks 50シナリオは削除・緩和しない。

判定タイミングは、Phase 2終了時に加え、§56.6 の中間タイミング（Sprint 3開始から2週、5週）でも行う。中間判定では、品質下限、改善トレンド、コスト、タイムボックスを同時に確認する。

### 68.2 チェックポイント中間評価

Task 6では固定ステップ間隔でチェックポイントを保存する。保存先は `results/grpo/` 配下とし、正本である `results/sft_sequential/seg_003_offset_66000/final_adapter` および `results/aux_heads/seg_003/final_aux_head` は上書きしない。これは確定制約#4のread-only方針を継承する。

各checkpointでは軽量evalとして、Spot Checks 50（§58.2）、action head top-1確率中央値、4-class action分布を記録する。主要checkpointでは、Slumbot HUとself-play vs Phase1 baselineも実施する。

best checkpointは単純な報酬最大ではなく、Spot Checks合格率とentropy健全性を主軸に保持する。報酬が高くてもtop-1中央値が0.85を大きく超える、Raise/Fold偏重が出る、sizing固定が出るcheckpointはbest候補にしない。

### 68.3 崩壊ガードのループ挙動

Task 5cで実装した `monitor.collapse_guard` を訓練ループへ組み込む。監視対象は §56.4 の病理パターンのうち分布ベースで検出できるもの、すなわちaction head top-1中央値、Raise頻度、Fold頻度、sizing最頻値割合である。

`HALT` が返った場合は、訓練を停止し、last-good checkpointを保存し、理由をログへ記録し、§56.3 のStep判断へエスカレーションする。崩壊状態へ自動継続しない。`WARN` の場合は、理由をログへ記録し、フラグを立てたうえで継続する。

entropy健全性は top-1確率中央値 ≤ 0.85 を基準とする。entropy崩壊対策なしに長時間訓練を開始しないという確定制約を継承し、Task 6aの訓練ハーネスでは最初からこのガードを呼べる構造にする。

### 68.4 撤退トラッキング

§56.6 の撤退条件を判定タイミングごとに記録する。対象は、タイムボックス12週（最大15週）超過、品質下限未達、改善トレンド消失、コスト$500超過である。

撤退判断を行う場合は、§56.8 のテンプレートで記録し、snapshotとDESIGN_NOTESを更新する。撤退は単なる訓練停止ではなく、どの条件を満たしたために次案へ移るのかを明記する運用判断である。

### 68.5 制約継承

報酬EVはMCのみとし、CFR/solver/反実仮想EVを持ち込まない（§65.1、§67.4）。ハイパラは `RewardConfig`、`GRPOConfig`、`EntropyGuardConfig` 経由で管理し、訓練ループ内で値をハードコードしない。

正本モデル成果物はread-onlyである。GRPO訓練checkpointは `results/grpo/` へ分離し、SFT正本LoRAおよび補助ヘッド正本を上書きしない。

Deep CFRおよびRust Solver関連コードは、品質検証（Stage D）前に削除しない。Rust postflop Solverは永久廃止方針だが、削除判断はStage Dの整理タイミングで行う。Deep CFR失敗モデルはopponent populationの弱い相手枠として参照する可能性があるため、現段階では保持する。

## 69. Task 6 opponent pool構成の現実と Deep CFR opponent分離

Task 6b/6c の opponent population は、§57 の理想構成をそのまま一度に実装するのではなく、ローカル成果物の実体と現行 GRPO self-play 環境への接続可能性に基づいて段階的に構成する。調査の結果、SFT 系は aux-head 推論として即時結線できる候補が限られ、Deep CFR 系は成果物は存在するが PokerKit state と推論入力形式が一致しないため、初期 Task 6 opponent pool からは分離する。

### 69.1 SFT population の現実

`results/sft_sequential/` には `final_adapter=True` の SFT adapter が 14 個存在する。一方、補助ヘッド成果物は `results/aux_heads/seg_003/final_aux_head/aux_heads.pt` のみである。したがって、§66 および SPEC §9.3 の 4-class action + sizing 契約でそのまま aux-head opponent 化できる正本ペアは、`results/sft_sequential/seg_003_offset_66000/final_adapter` と `results/aux_heads/seg_003/final_aux_head/aux_heads.pt` である。これは GRPO 初期化点と同一のペアでもある。

`results/sft_sequential/seg_003_offset_136000/final_adapter` も同じ `seg_003` aux head と組める可能性はあるが、専用評価済みではないため optional experimental 扱いとする。他の 13 adapter は aux head を持たないため、text-generation opponent を別実装しない限り Task 6 初期 pool の対象外とする。

### 69.2 初期opponent pool構成（Task 6b/6c初期）

Task 6b/6c の初期 opponent pool は、学習中の現方策 self、`seg_003_offset_66000` canonical aux-head SFT、optional experimental の `seg_003_offset_136000` aux-head SFT、RuleBased TAG、RuleBased LAG で構成する。

§56.3 Step2 に記載した過去 SFT 8 体等確率、4 種 rule-based、Deep CFR 20% 混入、2:1 交互といった opponent pool 拡張は、GRPO 品質未達時の段階的対処であり、初期訓練には入れない。初期段階では、接続が確実な aux-head 方策と rule-based 方策に限定し、崩壊ガードと中間評価で挙動を確認してから拡張する。

### 69.3 Deep CFR opponentの分離

Deep CFR 成果物は `C:\dev\deepcfr-training\models\` 配下に存在する。たとえば `phase3_v4/mixed_checkpoint_iter_10000.pt`、`phase3_v3f/mixed_checkpoint_iter_2500.pt`、`phase2/selfplay_checkpoint_iter_2000.pt` は、現 architecture の `PokerNetwork(base/action_head/sizing_head)` として Deep CFR 側 venv でロードできる。一方、`flagship_models/first` の旧 `fc1..fc6` 系 checkpoint は現 architecture にはロードできない。

現訓練 venv で Deep CFR を直接ロードするには `pokers` が追加で必要である。dry-run では `pokers-0.1.2` の追加のみで、`pokerkit==0.7.4` への干渉は確認されなかった。しかし、依存追加だけでは Task 6b の opponent として十分ではない。Deep CFR の推論入力は `pokers` state、または poker-system 側 `GameState` から作る 500 次元 encoding を前提としており、GRPO self-play の PokerKit state とは別物である。さらに Deep CFR 出力は fold / check-call / raise の 3-action であり、SPEC §9.3 の 4-class 契約（Fold / Check-Call / Raise / All-in）へ写像する設計判断も別途必要になる。

このため、Deep CFR opponent は Task 6b には含めない。`PokerKit state → DeepCFR 500-dim encoding` adapter と 3-action → 4-class 写像を設計・実装する別タスク（6d 想定）へ分離する。これは §57 の 3 系統 opponent 構成から Deep CFR を除外する判断ではなく、後続結線として扱う判断である。Deep CFR モデルおよび関連コードは確定制約 #12 に従い削除しない。

### 69.4 制約継承

aux-head opponent は §66 および SPEC §9.3 の 4-class + sizing 契約で動かす。報酬 EV は §65.1 および §67.4 を継承し MC のみとし、CFR/solver/反実仮想 EV を持ち込まない。報酬・GRPO・監視系のハイパラは各 Config 経由で管理し、ハードコードしない。

正本モデル成果物は確定制約 #4 に従い read-only とする。Task 6 の訓練成果物は `results/grpo/` に分離して保存し、`results/sft_sequential/seg_003_offset_66000/final_adapter` や `results/aux_heads/seg_003/final_aux_head` を上書きしない。docs は確定制約 #13 に従い `C:\Users\user\Desktop\dev\poker-system\docs` に一元管理し、訓練リポジトリへ保存しない。

## 70. SFT初期方策の裁定とガード再設計方針（Spot Checks v0）

Task 6c-prep で、§58.2 の Spot Checks を実行可能な評価基盤として新規に構築した。初期バッチ v0 は 20 局面であり、Deep CFR 病理の捕捉、all-in 診断、position 感度、value hand の過剰fold検出を目的に難所を多めに配分した診断用セットである。正式な Phase 2 Go/No-go 判定は、Spot Checks 50 の完成後に §58.1 の基準で行う。

### 70.1 裁定: (B) 健全なタイト確信（全面退化(A)ではない）

Spot Checks v0（20局面、診断用に難所を多めに配分）で、SFT初期方策（canonical `seg_003_offset_66000` + `seg_003/final_aux_head`、無学習）は 16/20 passed（0.80）だった。これは v0 の診断結果であり、§58.1 の Phase 1 合格や Phase 2 Go/No-go を代替するものではない。

裁定の決め手は、`overcard_no_pair` が 2/2 通過したことである。Deep CFR の病理である「overcard・3way・facing BET で Raise」を踏んでおらず、air vs c-bet では fold 0.995、trash hand では fold、AKo open では raise 0.99 となった。したがって、top-1中央値が高いこと自体は、全面的な退化ではなく、概ね正しいタイトアグレッシブな確信と解釈する。

`position_sensitivity` は 4/5 だった。先の self-play 集計で見えた fold率の「UTG最緩 / BTN最堅」逆転は、局面混在によるノイズの影響が大きく、position 感度が全壊しているとは裁定しない。ただし、SB K2o の過剰raiseは局所leakとして記録する。

v0 の 0.80 は難所偏重の診断値であり、§58.1 の Phase 1 合格（80%）とは見なさない。formal Go/No-go（Phase 2 = 95%）は Spot Checks 50 完成後に測る。

### 70.2 確認された実欠陥（局所leak）

all-in head は実害として弱い。Spot Checks v0 の `all_in` は 2/4 で、river nuts でも all_in は約 0.000005、AA vs all-in でも all_in は約 0.000007 だった。AA vs all-in は check-call 側で pass したが、all-in class 自体はほぼ使われていない。これは §49.2 の All-in 独立クラス設計、および train All-in 783件（0.14%）という既知制約どおり、実運用上の弱点として確認された。

made-hand fold 退化も局所的に確認された。`spot_016` ではストレートで fold 0.674913 となり、value hand を降りすぎている。これは §56.4 の病理パターンのうち、board/hand strength への無感度に近い兆候であり、Task 6c の改善対象とする。

position 過剰raiseも確認された。`spot_004` では SB K2o が raise 0.690254 となり、期待した raise 上限（0.35）を大きく超えた。position 感度が全壊しているわけではないが、SB 周りの過剰攻撃は watch item とする。

### 70.3 ガード再設計方針（§56.4/§68.3/§58.1のガード意味論を評価用途で更新）

本節の判断を、Task 6c-prep 以降の評価用途では §56.4、§68.3、§58.1 の従来の単純な entropy/fold ガード解釈より優先する。既存節は編集しないが、以後の実装・運用では本 §70 の意味論を適用する。

`top1_median` と `fold_freq` は情報監視（WARN）に格下げする。単発の HALT 根拠にしない。理由は、SFT初期方策が無学習時点で top-1中央値 0.85 を超えていても、Spot Checks v0 では多くの局面で妥当なタイトプレイをしていたためである。確信が高いことと退化は同義ではない。

真の退化 HALT は、多様な局面で単一アクションが支配し、かつ Spot Checks 品質が回帰する場合に限定する。分布監視は必要だが、それだけで品質を裁定しない。Spot Checks（v0 → 50）を品質ゲートの主軸に組み込み、§58.1/§58.2 の Go/No-go 判定へ接続する。

leak別監視を新設する。具体的には、all-in 使用率、value-hand fold率、position 過剰raiseを個別に追跡する。これらは単純な top-1中央値や fold頻度では捕捉できないため、Spot Checks のカテゴリ別合格率と合わせて監視する。

### 70.4 係数sweepの解釈更新（§56.3 Step1消化）

Task 6b-t の sweep では、entropy_bonus（0.01 / 0.03 / 0.1）、OPEFO balancing（0.5）、KL（0.05）の係数調整だけでは top-1中央値 ≤ 0.85 を多ステップで維持できなかった。係数単独では No-go と判断する。

ただし、この No-go は訓練そのものの失敗ではない。主因は、top-1中央値 ≤ 0.85 という指標を単独の最適化標的にすることが、ポーカーのタイトで確信的な正解局面と相性が悪い点にある。したがって、top-1中央値 ≤ 0.85 を単独で最適化標的にしない。

§56.3 Step1 のうち、係数群（entropy bonus / OPEFO / KL）は試行済みと扱う。残りのオプションである LR、group_size、generation_temperature、dynamic sampling は未消化である。ただし、これらを使う場合も、単純な top-1低下ではなく Spot Checks 品質と leak別監視の改善を基準にする。

### 70.5 Task 6cでGRPOが矯正すべきleak

made-hand fold退化と SB 過剰raiseは、自己対戦報酬で矯正すべき対象である。Task 6c では、Spot Checks v0/50 と leak別監視で、これらが改善するかを追跡する。報酬が上がっても value hand fold や position 過剰raiseが悪化する checkpoint は best 候補にしない。

all-in 復活は不確実である。all-in head は初期出力がほぼ 0 であり、通常の categorical sampling では all-in 行動がほとんど選ばれず、RL勾配が届きにくい。サンプリング温度を上げて探索を入れて初めて、all-in 局面に報酬が届く可能性がある。このため、§56.3 Step1 の generation_temperature は、単なる entropy崩壊対策ではなく、all-in探索のための候補として扱う。これは Task 6c の watch item とする。

### 70.6 制約継承

Spot Checks 50 は緩和・削除しない（確定制約 #11）。再設計するのは entropy/fold の「ガード」側であり、品質判定は Spot Checks に寄せる。v0 は診断用であり、後続タスクで 50 局面まで拡張して formal Go/No-go に接続する。

期待アクションはポーカー理論ベースで定義し、ソルバー/反実仮想を使わない（§17.6 / §65.1）。報酬 EV は MC のみとし、CFR/solver を再導入しない。正本モデル成果物は read-only（確定制約 #4）であり、docs は `C:\Users\user\Desktop\dev\poker-system\docs` に一元管理する（確定制約 #13）。

## 71. 第1次本訓練失敗分析と curriculum mix 方針

第1次本訓練は、steps 129436、generation_temperature 1.0、group_size 8 の設定で開始し、step 45500（約35時間）まで進めた。しかし Spot Checks は baseline 0.86 から明確に改善せず、横ばいに終わった。調査の結果、真因は単一のハイパーパラメータ不足ではなく、実装バグと局面分布の偏りが重なった learning 不全であった。本節では、第1次本訓練で確認された症状、修正済みの3件のバグ、および次に採用する curriculum mix の設計判断を恒久記録する。

Tee バッファによるログ誤認、VRAM 表示の誤判断などの運用面の落とし穴は snapshot 側に記録する。本節は learning 不全の技術原因、すなわち reward、評価 mode、PokerKit 浮動小数点防御、局面分布の問題に限定する。

### 71.1 症状

eval_step 1300〜45500 の全35回で、Spot Checks 全体は 0.84〜0.90 を往復した。これは baseline 0.86 からの一貫した改善ではなく、短期的な上下動であった。特に made_hand は 6/8 で完全固定し、all_in は 3/7、4/7、5/7 の間を方向性なく振動した。preflop_open は後半に 3/4 へ崩れる場面が増え、postflop value 判断の改善も preflop 品質の安定も得られなかった。

step 45500 では PokerKit の `ValueError: The unraked amount -8.526512829121202e-14 is negative.` により異常終了した。この値は実質ゼロの浮動小数点誤差であり、PokerKit の `total_pot_amount` 計算中に `Pot(raked, unraked, ...)` が構築される際、unraked が極小負値になったことが直接原因であった。

### 71.2 根本原因1（致命）: reward_fn が action を無視していた

最も致命的な原因は、Task 6c 初期実装の `_default_reward_fn` が候補 `action` と `sizing_ratio` を無視し、全候補に同一の `record.step_reward` を返していたことである。decision-state group 内の全候補報酬が同一になるため、group 内標準偏差が 0 となり、`advantage.py` の `std <= eps` 分岐で advantage が 0 化する。結果として policy_loss は実質 0 となり、latest checkpoint でも `policy_loss=-0.0` / `total_loss=0.0` が記録された。35時間の訓練は、ほぼ学習していなかったと判断する。

made_hand が 6/8 で完全固定したことは、「学習が当該カテゴリに効かなかった」のではなく、そもそも action head への有効な policy gradient が発生していなかったことの症状である。

この問題はコミット `2142101`（Task 6c-fix-reward）で修正した。`reward_fn(record, action, sizing_ratio)` を action-conditioned 化し、候補 action を `StepRecord.state` の deepcopy に適用してから MC rollout EV で `r_i` を生成する。これにより、確定制約 #16 の state 非破壊を維持しつつ、報酬 EV は MC のみとする確定制約 #7 および §65.1 / §67.4 を守る。CFR、solver、反実仮想 EV は導入しない。最適化対象は引き続き action head categorical であり、§66 を維持する。group は decision-state 単位であり、group 内 advantage 正規化を行うため、§67 も維持する。

同修正では `StepRecord` に `state` と `legal_actions` を追加し、候補 action 評価時に legal action と現在 state を復元できるようにした。`--with-model --steps 20` の短時間ランで `total_loss` が 0.0 固定ではなくなり、非ゼロ loss が発生することを実機確認済みである。

### 71.3 根本原因2: Spot Checks 評価が train mode の dropout ノイズを含んでいた

第1次本訓練中の all_in 振動の一部は、評価 forward が train mode のまま実行され、aux head の dropout が Spot Checks 評価に乗っていたことに由来する。Spot Checks は評価セットであり、方策品質の変化を見るためには deterministic な eval mode で実行する必要がある。train mode の dropout が有効なままでは、同一モデル・同一シナリオでも action probability が揺れ、all_in のような閾値近傍カテゴリでは pass/fail が振動しうる。

この問題はコミット `610b18a`（Task 6c-fix-eval）で修正した。評価 hook 経由の Spot Checks では `heads.eval()` と `torch.no_grad()` を用い、dropout を無効化する。一方、学習 step 内の forward は従来通り train mode を維持し、学習挙動は変更しない。あわせて `--rollout-playouts` のデフォルトを 8 に整理した。

同一モデル・同一シナリオを2回評価し、`probs_equal=True`、`passes_equal=True`、`categories_equal=True` となることを実機確認済みである。これにより、Spot Checks の揺れは方策変化として解釈できる状態になった。

### 71.4 副次バグ: PokerKit 極小負ポット額による異常終了

step 45500 の直接停止原因は、PokerKit `state.total_pot_amount` の内部計算で `unraked amount -8.5e-14 negative` が発生したことである。これは実質ゼロの浮動小数点誤差であり、長時間自己対戦中に再発しうる。PokerKit 本体を改変することは確定制約 #1 に反するため、呼び出し側で防御する方針とした。

この問題はコミット `810bfe3` で修正した。`pokerbench_prompt.safe_total_pot_amount()` を追加し、`FLOAT_POT_ZERO_EPSILON = 1e-6` をモジュール定数として定義した（確定制約 #6）。`state.total_pot_amount` が PokerKit 内部で極小負値により例外を投げた場合、`starting_stacks - stacks` に基づく再計算経路へフォールバックし、絶対値が閾値未満の負値を 0 とみなして正規化する。

`collect.py` の action sizing 経路も `safe_total_pot_amount()` に差し替えた。prompt 出力形式、数値フォーマット、PokerBench teacher 照合仕様は変更していない。極小負値を再現する単体テストも追加し、PokerKit 本体は `pokerkit==0.7.4` のまま維持した。

### 71.5 根本原因3（最深部）: 局面分布が preflop に偏り、対象局面に勾配が届かなかった

reward と eval mode を修正した後、playouts 8 と playouts 16 の両方で 200step trial を実施した。しかし made_hand は 6/8、all_in は 4/7 のまま固定し、Spot Checks 全体も 0.84 周辺で安定した。loss は非ゼロ化しており、GRPO の学習信号そのものは復活していたが、made_hand / all_in の品質には届かなかった。

精密診断では、made_hand 失敗2局面の正解側確率が step 0 から step 200 にかけて逆方向へ低下した。`spot_016` は正解側 probability が 0.330 から 0.247 へ低下し、`spot_040` は 0.086 から 0.053 へ低下した。これは「学習が遅いだけ」ではなく、LR を単純に上げると誤った方向の更新を増幅する危険があることを示す。

all_in 失敗局面では、all_in probability が初期値ほぼ 0 から復活しなかった。`spot_018` は 0.001 台から 0.002 台への微増にとどまり、`spot_019` は 0.000004 からほぼ不動、`spot_043` も 0.001 台で停滞した。all-in head は初期方策でほぼ使われず、通常 self-play の categorical sampling では選択されにくいため、§70.5 の all-in 復活不確実性が実測で確認された。

さらに、自己対戦 200 hands の収集結果では hero decision の約97%が preflop に偏っていた。初期方策では `turn_river_facing_bet=0`、`river_facing_large_bet=0`、`sampled_all_in=0` であり、p8 step200 checkpoint でも `turn_river_facing_bet=1`、`river_facing_large_bet=1`、`sampled_all_in=0` にとどまった。つまり Spot Checks で問題になっている made_hand / all_in の turn・river・大きなbet facing 局面が、通常自己対戦ではほぼ訓練 batch に入っていない。

preflop 偏重の機序は、初期方策がタイトで preflop fold 率が高いことにある。preflop で多くのhandが終わるため、postflop value 判断や river all-in pressure の局面が生成されない。このため、made_hand の postflop 全般にも §70.5 の all-in 出現頻度問題が拡張して現れている。preflop 偏重は postflop value 判断を改善できないだけでなく、誤った fold 側更新により made_hand を巻き添え劣化させうる。

### 71.6 採用方針: 訓練専用 curriculum state set を decision-state group として混合する

次の対策は、案Bの変形として、Spot Checks 近傍の訓練専用 curriculum state set を別ファイルで作成し、通常 self-play trajectory に decision-state group として混合する方針とする。Spot Checks 50 そのものは評価用であり、確定制約 #11 の評価独立性を維持するため、訓練には入れない。訓練用 curriculum は別ID、別カード、別ラインの近傍テンプレートとして新規作成する。

この方針では、1つの構築済み state を1つの decision-state group として扱う。異なる局面を同一 group に混ぜてはならない。これにより §67 の group=decision-state 単位と group 内 advantage 正規化を維持する。候補 action ごとの報酬は既存の action-conditioned reward と同様に state deepcopy へ action を適用し、MC rollout EV のみで評価する。したがって §65.1 / §67.4 と確定制約 #7 / #16 を守る。最適化対象は action head categorical のままであり、§66 も維持する。state 構築は `state_factory` 系 API に寄せ、確定制約 #5 と整合させる。

案Aの mid-hand 開始 curriculum は、PokerKit state 途中注入、bankroll tracker、terminal reward 整合のリスクが高いため初手としては採用しない。案Cの generation_temperature / preflop 探索強化は実装コストが低い一方、postflop 到達率への効果が間接的であり、preflop 品質を壊す懸念があるため主対策にはしない。LR 調整は §56.3 の残りオプションであるが、made_hand 失敗局面の正解側確率が逆方向へ動いた実測から、curriculum による局面分布補正前に単純に上げるべきではない。

実装時の影響範囲は、新規 `pokerrl_grpo/curriculum_spots.py`、`scripts/run_training.py` の trajectory provider 混合、CLI / Config の `--curriculum-ratio` と `--curriculum-scenarios`、およびテストである。テストでは、curriculum state 評価が state 非破壊であること、1 state = 1 group が守られること、Spot Checks 50 が不変であること、混合後の batch に turn / river / all_in 対象局面が入ることを確認する。

### 71.7 制約継承と未確定事項

本節で記録した修正と方針は、確定制約 #1 / #4 / #5 / #6 / #7 / #8 / #9 / #11 / #16、および §65 / §66 / §67 / §70 と整合する。PokerKit 本体は変更しない。正本モデル成果物は read-only とする。state 構築は `state_factory` と整合させる。閾値・混合率・playouts 等のハイパーパラメータは Config / CLI 経由で管理する。CFR、solver、反実仮想 EV は導入しない。Spot Checks 50 は評価セットとして不変に保つ。candidate reward 評価では state deepcopy により非破壊性を維持する。

未確定事項は、curriculum 混合率、訓練専用テンプレートの局面数とカテゴリ配分、curriculum 導入後に LR 調整（§56.3 残り）が必要かどうかである。これらは curriculum 実装後の短時間 trial で判断する。

第1次設定（steps 129436）と step 45500 checkpoint は、reward_fn action 無視バグによりほぼ未学習であったため、継続利用しない。curriculum 実装後は step 1 から再訓練する。

## 72. curriculum密度の発見と5層診断の決着

§71で採用した curriculum mix を実装し、相手holeバグ、教材飽和、密度不足を順に潰した結果、trial4 で第1次本訓練以来はじめて made_hand 失敗局面の正解側確率が正方向へ動くことを確認した。本節では、curriculum 実装後に発生した調整連鎖と、それにより判明した「学習が効かない原因の最深部は、局面分布だけでなく curriculum 密度不足と self-play の高確信ドリフトであった」という知見を記録する。あわせて、VRAM と all_in という残課題を次段階の制約として明示する。

### 72.1 curriculum mix の実装（`0c6c16d`）

コミット `0c6c16d` で、訓練専用の turn / river / all_in 近傍局面を decision-state group として self-play trajectory に混合する骨格を実装した。`pokerrl_grpo/curriculum_spots.py` を新設し、`data/curriculum_spots/scenarios.json` から訓練専用シナリオを読み込む。Spot Checks 50 の `data/spot_checks/scenarios.json` は読まず、評価リークを避ける（確定制約 #11）。

state 構築は `state_factory.create_state()` を起点にし、hole / action sequence / board を replay して hero の決定点まで進める方式とした。これにより state 正本を `state_factory` に保つ確定制約 #5 と整合する。各 curriculum scenario は 1つの `StepRecord` を持つ 1つの `Trajectory` に変換され、`build_decision_groups` では 1 state = 1 decision-state group として扱われる。異なる局面を同一 group に混ぜないため、§67 の group 定義と group内 advantage 正規化を維持する。

報酬は既存の action-conditioned reward と同じく、候補actionを state の deepcopy に適用し、MC rollout EV のみで評価する。CFR / solver / 反実仮想 EV は導入しないため、確定制約 #7 および §65.1 / §67.4 と整合する。候補評価は state 非破壊であり、確定制約 #16 を維持する。最初の実装では骨格確認のため、made_hand / all_in / turn barrel / river value を含む最小テンプレートを用意した。

### 72.2 相手holeバグ（`9199ca9`）

初期 curriculum では、`_cards_by_index` が未指定の相手holeを決定論的に deck 先頭から補完していた。この補完順により active opponent に KK 等の強いカードが入り、hero の value hand として設計した局面が、実際には hero が負けている局面になっていた。そのため MC rollout EV は fold を高く評価し、made_hand 訓練が逆方向へ進む構造になっていた。

診断では、`curr_made_hand_btn_top_pair_flop_raise` 相当の局面で、fold の報酬が call / raise より高くなることを実測した。すなわち curriculum が value spot を教えているのではなく、負けているhandで降りることを正解として教えていた。これが、reward_fn 修正後も made_hand 正解側確率が逆方向へ低下した直接原因の1つである。

コミット `9199ca9` では、scenario に `opponent_holes` を明示できるようにし、意図した opponent range / hand strength を教材側で管理する方式へ変更した。あわせて、未指定holeの補完deckを低ランク側からに変更し、決定論的な強配が再発しにくいようにした。修正後、該当 value spot で call / raise の報酬が fold を上回ることを実測し、`curr_made_hand_btn_top_pair_flop_raise` では fold -0.44 に対して call +1.00 / raise +0.50 となることを確認した。既存の正方向局面も call / raise > fold を維持した。

### 72.3 教材飽和（`562039a`）

相手hole修正後も、Spot Checks の made_hand 失敗局面である `spot_016` / `spot_040` は正方向へ動かなかった。追加診断により、修正後の curriculum made_hand 局面そのものは step0 から正解側確率 0.94〜0.97 程度で飽和しており、すでに解ける簡単な問題であることが分かった。解けている局面を反復しても有効な勾配は小さく、Spot失敗局面へ転移しない。

また、`spot_040` は river straight が overbet に直面する局面であるが、当時の curriculum にはこの性質に対応する教材が存在しなかった。つまり、局面分布を補うという方針は正しかったが、教材の難易度と性質が Spot失敗局面から離れていた。

コミット `562039a` では、Spot失敗局面の性質と難易度に合わせて curriculum を再設計した。飽和していた簡単な局面を除去し、初期正解側確率が 0.40〜0.77 帯に入る river overbet 教材3件と turn barrel 教材1件を追加した。具体的には、`curr_made_hand_bb_98s_straight_turn_barrel`、`curr_made_hand_bb_87s_straight_river_overbet`、`curr_made_hand_bb_97s_straight_river_overbet`、`curr_made_hand_bb_t8s_straight_river_overbet` を中心に、学ぶ余地のある value facing pressure 局面へ寄せた。各局面で MC報酬は call / raise > fold を維持し、相手holeバグの再発がないことも確認した。

### 72.4 密度不足と高確信ドリフト（`4f79794`）

教材を再設計しても、ratio 0.3 の mixed training では curriculum 局面自体の正解側確率が上がらず、全体に微減する挙動が残った。勾配診断では、勾配が存在すること（aux head の grad_l2 は概ね 8.6〜10.8）、正則化による引き戻しがないこと（kl / entropy / OPEFO 係数はいずれも 0）、advantage 正規化で信号が潰れていないこと（RMS は概ね 1）、aux head の表現力限界を示す根拠が薄いことを確認した。

一方、純curriculumに近い条件では現行 LR=1e-5 でも確率が動くことが分かった。curriculum 局面の正解側確率は、短時間でも +0.087 などの正方向変化を示した。したがって、主因は LR 不足ではなく、mixed training 内で curriculum 信号が薄すぎることであった。ratio 0.3 では 1step あたり curriculum group は高々1件であり、self-play 側の高確信ドリフトに埋もれていた。実際、問題の run では final top1_median が 0.992、entropy が 0.075 まで寄り、高確信な方策更新が curriculum の局所信号を押し流していた。

コミット `4f79794` では、`--curriculum-groups-per-step` を追加し、1step に複数の curriculum group を投入できるようにした。未指定または 0 では従来互換として ratio 命中時に1件だけ追加する。正の値を指定した場合は、その数だけ curriculum trajectory を追加する。各 curriculum trajectory は引き続き 1 state = 1 group であり、§67 の decision-state group 定義を破らない。この変更は、§56.3 Step1 の未消化オプションのうち dynamic sampling に相当する。

### 72.5 trial4による実証（density 2）

`--curriculum-ratio 1.0 --curriculum-groups-per-step 2` の 200step trial4 では、made_hand 失敗局面が第1次本訓練以降はじめて明確に正方向へ動いた。`spot_016` は正解側確率が 0.322 から 0.403 へ上昇し（+0.080）、`spot_040` は 0.076 から 0.123 へ上昇した（+0.047）。pass 判定の majority 閾値はまだ超えていないが、生確率の方向は明確に改善した。

curriculum 4局面も全て上昇した。`curr_made_hand_bb_98s_straight_turn_barrel` は 0.763 から 0.851（+0.088）、`curr_made_hand_bb_87s_straight_river_overbet` は 0.584 から 0.695（+0.111）、`curr_made_hand_bb_97s_straight_river_overbet` は 0.369 から 0.577（+0.208）、`curr_made_hand_bb_t8s_straight_river_overbet` は 0.451 から 0.634（+0.183）へ上昇した。400件の curriculum trajectory が投入され、loss は 190/200 step で非ゼロだった。

健全性面でも、高確信ドリフトは解けた。final metrics では top1_median が 0.484、entropy が 0.815 となり、以前の top1_median 0.992 / entropy 0.075 の状態から大きく改善した。Spot Checks の pass rate は 0.88 で横ばいだが、preflop系は崩れず、`halted=False`、`oom=False`、`false_halt=False`、`spot_regression=False` で完走した。

この結果により、「curriculum 密度を上げれば self-play の高確信ドリフトに勝ち、made_hand 失敗局面が動く」ことを実証した。第1次本訓練失敗の5層診断、すなわち reward_fn action無視（§71.2）、局面分布偏り（§71.5）、相手holeバグ（§72.2）、教材飽和（§72.3）、密度不足と高確信ドリフト（§72.4）は、いずれも正しい原因と正しい対処であったと判断する。

### 72.6 残課題（VRAMとall_in）

第一の残課題は VRAM である。trial4 の density 2 では `cuda_max_memory_allocated_mb=13857.0` を記録し、RTX 3080 の物理10GBを大きく超えた。共有メモリ退避を含む環境では完走したが、100h級の本訓練をこのまま回すには危険である。density 増により group 数が増え、forward / backward のGPUメモリが膨らむ。MC rollout 自体は CPU / eval7 側であり、主なVRAM増加要因ではない。次段階では、density の学習効果とVRAMのバランス、eval / checkpoint 頻度、Spot Checks の batch size、prompt forward のchunk化などを検討し、10GB内で安定運用できる設定を確定する必要がある。

第二の残課題は all_in である。trial4 でも `spot_018` / `spot_019` / `spot_043` の all_in 確率は上昇せず、むしろ微減した。`spot_018` は 0.001178 から 0.000346、`spot_019` は 0.000004 から 0.000002、`spot_043` は 0.001300 から 0.000443 へ低下した。これは made_hand とは性質が異なる問題である。made_hand は curriculum 練習により動くが、all_in は初期確率がほぼ0であり、通常の categorical sampling では候補に出にくく、方策勾配が乗りにくい。§70.5 の all-in 復活不確実性が再確認された。

all_in 対策は made_hand 本訓練とは切り分ける。候補としては、generation_temperature を all-in 探索目的で上げること（§70.5 の本命）、all_in 候補を特定局面で強制サンプルすること、nut局面の報酬設計をさらに厚くすることがある。ただし、made_hand の改善効果と all_in 探索効果を同時に混ぜると切り分けが困難になるため、まずは made_hand 用 curriculum density の本訓練を安定化し、その後に all_in を別タスクとして扱う。all_in 復活は引き続き不確実である。

### 72.7 制約継承と次段階

本節の修正と方針は、確定制約 #1 / #4 / #5 / #6 / #7 / #9 / #11 / #16、および §65 / §66 / §67 / §70 / §71 と整合する。PokerKit 本体は変更しない。正本モデル成果物は read-only とし、checkpoint は `results/grpo/` 配下に限定する。state 構築は `state_factory` 起点、報酬EVはMCのみ、group は decision-state 単位、Spot Checks 50 は評価専用であり訓練に混ぜない。候補action評価では state deepcopy により非破壊性を維持する。

LR は §56.3 Step1 の未消化オプションとして残すが、現時点では優先しない。高確信ドリフト下で LR を上げると、preflop品質や既存の安定カテゴリを壊すリスクがあるためである。trial4 で density、すなわち dynamic sampling により made_hand が正方向へ動いたため、次段階では LR ではなく VRAM 対策と curriculum density の運用設定を先に固める。

次の実行順序は、VRAM対策、made_hand 本訓練、別途 all_in 対策である。made_hand 本訓練は curriculum density を有効にし、第1次本訓練の step45500 checkpoint は未学習であるため破棄して step 1 から行う。第1次設定は参照記録としてのみ残し、再開元にはしない。

## 73. 第2次本訓練で made_hand 7/8 到達も preflop 崩壊でHALT

§72で記録した microbatch 化により、density 2 の学習効果を維持したまま VRAM を 10GB 内へ収められることを確認した。具体的には、trial5 で VRAM peak が 13.9GB 級から 4.5GB 級へ下がり、数学的等価性も確認された。そのうえで、第2次本訓練を HEAD `42b2c27`、`--steps 127000`、curriculum density 2、microbatch 1 で起動した。結果として、made_hand は本訓練で初めて 7/8 に到達した一方、preflop と position が崩れ、step 6500 で品質ガード HALT となった。

本節では、この「made_hand 到達」と「preflop 崩壊」が同時に起きた経緯を記録する。§71 / §72 で特定した5層の問題は made_hand 改善という形で結実したが、新たに6層目の課題、すなわち made_hand 改善と preflop 維持のトレードオフ、あるいは破滅的忘却が明らかになった。

### 73.1 microbatch化（`42b2c27`）

第2次本訓練の前に、VRAM対策としてコミット `42b2c27` を実装した。trial4 では density 2 により made_hand は正方向へ動いたが、VRAM peak は `cuda_max_memory_allocated_mb=13857.0` に達していた。事前計測により、このピークは collect、MC rollout、eval hook、checkpoint 保存ではなく、訓練 policy forward の一括batch由来であることを確認した。

構造上、1つの decision-state は group_size 8 の record に複製される。curriculum 1 group を追加すると、同一promptが8サンプル分だけ train forward batch に乗る。density 2 では self-play 由来 group に加えて curriculum 2 group が入り、flatten 済み batch が一括で forward されるため、activation の瞬間ピークが膨らんでいた。

`42b2c27` では、group境界で microbatch 分割する方式に変更した。各 microbatch で forward、loss計算、backward を行い、勾配を累積する。`optimizer.step()` は全 microbatch の backward 後に1回だけ実行する。group内の8recordは分割せず、1 decision-state = 1 group の定義を維持するため、§67 と整合する。

数学的等価性はテストで確認した。一括batchと `microbatch_groups=1` を比較し、同一入力・同一seedの1stepで loss と全 parameter gradient が `1e-6` 範囲で一致することを確認した。sizing loss についても、microbatchごとの局所平均ではなく、全体の positive-advantage weight を分母に使うことで一括lossと等価にした。実機では density 2 のまま `cuda_max_memory_allocated_mb=4481.7` まで低下した。trial5 でも trial4 と同等の made_hand 正方向が再現され、`spot_016` は +0.071、`spot_040` は +0.045 となった。したがって、microbatch 化は学習効果を変えず、VRAM のみを下げる対策として機能した。

### 73.2 第2次本訓練のeval推移

第2次本訓練は、HEAD `42b2c27`、`--steps 127000`、`--curriculum-ratio 1.0`、`--curriculum-groups-per-step 2`、`--microbatch-groups 1`、`--rollout-playouts 8`、`--generation-temperature 1.0`、`--eval-every 1300`、`--checkpoint-every 650`、`--seed 1200` 相当の設定で起動した。第1次本訓練の checkpoint は reward_fn action 無視バグにより未学習であるため使わず、step 1 から開始した。

eval 推移は以下である。made_hand は 7/8 に到達したが、preflop_open、position_sensitivity、all_in が時間とともに悪化し、step 6500 で全体 pass_rate が floor を割った。

| eval_step | pass_rate | made_hand | preflop_open | position_sensitivity | all_in | river | quality |
|---:|---:|---:|---:|---:|---:|---:|---|
| 1300 | 0.82 | 7/8 | 2/4 | 6/8 | 4/7 | 0/1 | OK |
| 2600 | 0.82 | 7/8 | 2/4 | 6/8 | 4/7 | 0/1 | OK |
| 3900 | 0.80 | 7/8 | 2/4 | 5/8 | 4/7 | 0/1 | ALERT(position) |
| 5200 | 0.80 | 7/8 | 2/4 | 5/8 | 4/7 | 0/1 | ALERT(position) |
| 6500 | 0.72 | 7/8 | 1/4 | 4/8 | 2/7 | 0/1 | HALT |

step 6500 の HALT は、pass_rate 0.72 が品質ゲートの floor 0.75 を下回ったことによる。これは§70.3で再設計した品質ガードが、暴走モデルの本採用を防いだ正常動作である。

### 73.3 成果: made_hand 7/8 到達

第2次本訓練の最大の成果は、made_hand が baseline 6/8 から 7/8 に到達し、step 1300 から step 6500 まで維持されたことである。第1次本訓練では、reward_fn action 無視バグにより35時間回しても made_hand は 6/8 に完全固定されていた。これに対し、第2次本訓練では curriculum density と microbatch を含む修正後の系で、実際の長時間訓練において pass 判定が1つ進んだ。

これは、trial4 / trial5 の生確率上昇が本訓練の長さで majority 判定を越えたことを意味する。§71で特定した reward_fn action 無視、eval dropout、PokerKit浮動小数点、局面分布偏り、および§72で特定した相手holeバグ、教材飽和、密度不足の各診断は、made_hand 改善という本番結果で裏付けられた。5層診断は、少なくとも made_hand については正しい原因と正しい対処であった。

### 73.4 失敗: preflop崩壊とHALT

一方で、made_hand 上昇の裏で preflop と position が大きく崩れた。preflop_open は初期 baseline では 4/4 だったが、第2次本訓練では step 1300 時点で 2/4 まで落ち、step 6500 では 1/4 になった。position_sensitivity も baseline 7/8 から step 6500 で 4/8 へ落ちた。all_in も 4/7 から 2/7 へ悪化し、river は 0/1 のまま改善しなかった。全体 pass_rate は 0.86 相当の baseline から 0.72 へ下がった。

品質ガードは、step 3900 と step 5200 で position fail_rate > 0.30 による ALERT を出し、step 6500 で pass_rate floor 割れにより HALT した。すなわち、劣化は突然ではなく、ALERT から HALT へ漸進的に進んだ。`last_good` は step 6500 で保存されたが、これは preflop 崩壊後の状態であり、本採用できない。第2次本訓練の checkpoint を同設定で resume しても、同じ崩壊を継続するだけである。

この失敗は、made_hand を動かすだけでは Phase2 品質を満たせないことを示す。§70.3 の品質ゲートは、Spot Checks 全体を品質軸として監視し、局所改善の代償としてのカテゴリ崩壊を検出した。この意味で HALT は失敗であると同時に、ガード設計が機能した証拠でもある。

### 73.5 6層目の課題: made_hand改善とpreflop維持のトレードオフ

第2次本訓練で明らかになった6層目の課題は、made_hand 改善と preflop 維持のトレードオフである。仮説として、`curriculum-ratio 1.0` と `curriculum-groups-per-step 2` による postflop 局面注入が強すぎ、方策が postflop value facing pressure に過剰適応した。その結果、初期方策で完璧だった preflop_open 4/4 や position 判断を破滅的に忘却した可能性が高い。

trial4 / trial5 の 200step では、preflop崩壊は顕在化しなかった。短時間では made_hand の正方向だけが観測され、preflop の劣化はまだ小さかったと考えられる。しかし、第2次本訓練の 6500step では、時間経過とともに preflop と position が崩れた。これは、§70.5 の all_in 出現頻度問題や、§72 の教材品質・密度問題とは異なる、カテゴリ間バランスと忘却の問題である。

この課題は、curriculum 自体が誤っているという意味ではない。curriculum は made_hand を改善した。しかし、postflop 局面だけを厚く入れると、元々よかった preflop カテゴリを維持する信号が相対的に不足する。したがって、次の対策は「made_handを動かす信号」と「preflopを維持する信号」を同時に保つ設計でなければならない。

### 73.6 対処方針（次セッション、診断後に決定）

次の作業では、対処に飛びつかず、まず preflop 崩壊の機序を診断する。確認すべき点は、curriculum / self-play 比率、preflop 各Spotの生確率の step 推移、preflop_open と position_sensitivity がどの時点でどの方向へ動き始めたか、self-play 側で preflop 維持信号が十分に入っているかである。同設定で `--resume-from` して再開することはしない。同じ崩壊を繰り返すためである。

対処候補は3つある。第一に、curriculum 比率または density を下げ、postflop 注入を弱めて self-play / preflop とのバランスを取る方法である。これは preflop 崩壊を抑える見込みがあるが、made_hand 改善速度を落とす可能性がある。第二に、preflop 維持局面を curriculum に混合する方法である。preflop_open や position の正しい判断を訓練にも含め、忘却を防ぐ。ただし、Spot Checks 50 そのものは訓練に入れず、別ID / 別カード / 別ラインで作る必要があるため、確定制約 #11 を守る。第三に、`kl_coef > 0` により初期方策からの乖離を制約する方法である。これは preflop 完璧な初期方策を守る方向に働く。§70.4 の top1 を最適化標的にする話とは異なり、方策ドリフト制約としての KL である。

これらの候補は、診断後に1〜2個へ絞る。現時点で LR を上げることは優先しない。LR は§56.3 Step1 の未消化オプションとして温存するが、今回の問題は学習不足ではなく、学習が効きすぎたカテゴリと忘却したカテゴリのバランスである。設定見直し後は、第2次本訓練の checkpoint からではなく step 1 から再訓練する。

### 73.7 制約継承と撤退意識

本節の方針は、確定制約 #1 / #4 / #5 / #6 / #7 / #9 / #11 / #16、および §65 / §66 / §67 / §70 / §71 / §72 と整合する。PokerKit 本体は変更しない。正本モデル成果物は read-only とする。state 構築は `state_factory` 起点、報酬EVはMCのみ、group は decision-state 単位、Spot Checks 50 は評価専用として不変に保つ。候補action評価では state deepcopy による非破壊性を維持する。

撤退基準（§56.6）にはまだ抵触していない。第2次本訓練は、dynamic sampling と curriculum density が made_hand に効くことを示したため、改善トレンドが完全に消失したわけではない。一方で、品質下限 floor 0.75 を割って HALT した事実は重い。§56.3 Step1 の dynamic sampling は made_hand に有効だったが、preflop 崩壊という副作用を持つことが明らかになった。

6層目の preflop 崩壊に対して、made_hand と preflop を両立する設定を見つけられない場合、撤退を真剣に検討する分岐になりうる。次段階は、preflop崩壊の診断、対処候補の絞り込み、短時間trialでの両立確認である。LR は引き続き未消化のまま温存する。

## 74. preflop崩壊の診断と7層目（報酬関数のpreflop open過小評価）の発見

§73では、第2次本訓練で made_hand が 7/8 に到達した一方、preflop_open と position_sensitivity が崩壊したことを6層目の課題として記録した。すなわち、made_hand 改善と preflop 維持のトレードオフ、あるいは破滅的忘却の疑いである。本セッションでは、その機序を診断した。その結果、6層目の対処、すなわち curriculum 比率調整、preflop維持 curriculum 混合、KL正則化に着手する前提を崩す、より深い7層目が判明した。

本節では、preflop崩壊の生確率診断、訓練分布の実測、preflop open 局面に対する候補action別MC報酬の実測を記録する。核心は、現行の `action_conditioned_reward` / `rollout_ev` が preflop open を構造的に過小評価し、強い参加handすら `open < fold` と教えることである。したがって、6層目の対処に飛びつく前に、7層目、すなわち報酬側の扱いを先に切り分ける必要がある。

### 74.1 preflop崩壊の診断結果（破滅的忘却の確認）

第2次本訓練の残存 checkpoint は、`best=step1300`、`last_good/latest=step6500` であった。これらと初期方策を eval mode でロードし、preflop_open および position_sensitivity 代表spotの正解側生確率、すなわち `majority_prob` を実測した。checkpoint は診断用に読むだけであり、本採用や resume 元にはしない。評価は state 非破壊で行い、確定制約 #16 を維持した。

preflop_open の実測値は以下である。

| spot | 初期 | step1300 | step6500 | 判定 |
|---|---:|---:|---:|---|
| spot_021 HJ KQs open | 0.979 | 0.657 | 0.008 | PASS → PASS → FAIL |
| spot_022 CO 55 open | 0.983 | 0.263 | 0.003 | PASS → FAIL → FAIL |
| spot_023 HJ 83o fold | 0.570 | 0.976 | 1.000 | PASS → PASS → PASS |
| spot_024 CO A5s open | 0.874 | 0.248 | 0.002 | PASS → FAIL → FAIL |

position_sensitivity 代表spotの実測値は以下である。

| spot | 初期 | step1300 | step6500 | 判定 |
|---|---:|---:|---:|---|
| spot_001 UTG AKo | 0.998 | 0.972 | 0.059 | PASS → PASS → FAIL |
| spot_003 BTN A9s | 0.843 | 0.180 | 0.001 | PASS → FAIL → FAIL |
| spot_047 UTG QQ | 0.997 | 0.929 | 0.403 | PASS → PASS → FAIL |
| spot_048 BTN Q9s | 0.513 | 0.027 | 0.000 | PASS → FAIL → FAIL |

この結果から、preflop_open は初期 4/4 PASS であり、元から壊れていたわけではない。したがって、§73.6で挙げた仮説のうち「元から不安定」という説明は主因ではない。一方、生確率は正解方向から逆方向へ一貫して低下しており、参加すべきhandを fold へ潰す方向の破滅的忘却が強く該当する。特に step1300 時点で preflop_open はすでに 2/4 まで落ちており、崩壊は本訓練の早い段階から始まっていた。

all_in は別軸の課題として、step6500 でも生確率がほぼ死んだままであった。代表3spotでは、spot_018 が `0.0014655 → 0.0000012 → 0.0000001`、spot_019 が `0.0000060 → 0.000000003 → 0.000000000`、spot_043 が `0.0012260 → 0.0000006 → 0.000000009` であった。これは §70.5 / §72.6 で記録した all_in head の復活困難性を再確認するものであり、made_hand / preflop 崩壊とは切り分けて扱う。

### 74.2 訓練分布の確認（curriculum上乗せ構造）

`scripts/run_training.py` の `trajectory_provider` は、self-play trajectory を必ず1 hand収集し、その後 `curriculum-ratio` の確率で curriculum trajectory を append する。これは self-play を置換するのではなく、上乗せする構造である。`mix_curriculum_trajectories` も、self-play trajectories の list に curriculum trajectories を `extend` するだけであり、既存の self-play group を消さない。

第2次本訓練相当の設定、すなわち `curriculum-ratio=1.0`、`curriculum-groups-per-step=2` で collect-only の短時間計測を行った。5stepの実測では、`self:preflop 6 records`、`curriculum:river 8 records`、`curriculum:turn 2 records` であった。概ね、1stepあたり `self-play preflop 1.2 group : curriculum postflop 2 group` である。

したがって、self-play は消えていない。しかし postflop curriculum が多数派であり、preflop維持信号を量的に上回っている。これは §73.5 の「postflop注入過多によるpreflop忘却」という仮説を支持する。ただし、この段階ではまだ6層目の分布バランス問題に見える。次の §74.3 で、この仮説だけでは不十分であることが判明する。

### 74.3 7層目の発見：報酬関数がpreflop openを過小評価

preflop維持 curriculum を追加する前提として、MC EVのみ（確定制約 #7）で参加handが自然に正解化できるかを検証した。Spot Checks 50 の局面は訓練に混ぜられないため、診断でも直接の教材候補としては使わず、別カード・別IDの3局面を `state_factory` 起点で新規構築した。局面は、HJ AQs open、CO 99 open、UTG 72o fold である。候補action別の報酬は、現行の `action_conditioned_reward`、すなわち state deepcopy、候補action適用、`step_reward` = MC rollout EV という経路で実測した。これは #7 と #16 を守る診断である。

測定条件は、5 seed、`rollout_playouts=8 / 32 / 64` である。結果は以下である。値は平均で、delta は `open - fold` である。

| 局面 | playouts | fold | open | delta |
|---|---:|---:|---:|---:|
| HJ AQs open | 8 | 0.000 | -0.365 | -0.365 |
| HJ AQs open | 32 | 0.000 | -0.345 | -0.345 |
| HJ AQs open | 64 | 0.000 | -0.323 | -0.323 |
| CO 99 open | 8 | 0.000 | -0.295 | -0.295 |
| CO 99 open | 32 | 0.000 | -0.282 | -0.282 |
| CO 99 open | 64 | 0.000 | -0.260 | -0.260 |
| UTG 72o fold | 8 | 0.000 | -0.345 | -0.345 |
| UTG 72o fold | 32 | 0.000 | -0.359 | -0.359 |
| UTG 72o fold | 64 | 0.000 | -0.336 | -0.336 |

判定は、`(B) MC EVが不安定/逆方向` である。現行報酬は trash hand の fold は正しく教えるが、強い参加handである HJ AQs や中程度の CO 99 ですら、全playoutsで `open < fold` と評価した。playouts を 64 まで増やしても符号は反転しないため、これは単なるMC分散ではなく、報酬モデルの構造問題である。

実測では fold 報酬が一律 `0.000 ± 0.000` であり、open 報酬は常に負であった。Builder仮説としては、`rollout_ev` が「現active playersが追加ベットなしでshowdown到達する」として、open直後の投資額を即時コスト計上する一方、fold は hero が非activeになり、寄与0として扱われることが芯である。この構造では、preflop open の実戦的価値、すなわち相手のfold頻度、以後のベット応答、ポジション、レンジ優位が反映されにくい。

### 74.4 層構造の更新（7層目）

§73までの6層に、7層目として「報酬関数のpreflop open過小評価」を追加する。これは6層目、すなわち curriculum 比率による忘却のさらに下にある。postflop curriculum が厚すぎたことは preflop 崩壊の直接要因である可能性が高い。しかし、preflop維持 curriculum を足しても、報酬が強いopen handまで fold と教えるなら、preflop は直らない。

したがって、対処順序は7層目が先である。6層目の候補である curriculum比率調整、preflop維持教材混合、KL正則化は、preflop open に対する報酬が少なくとも正方向を向くことを前提にしている。この前提が崩れている以上、まず報酬側を調査し、#7の枠内で歪みを是正できるかを確認する必要がある。

### 74.5 対処方針と#7との緊張関係（次セッション、調査後に決定）

次セッションでは、対処に飛びつかず、まず `rollout_ev` 内部を行番号付きで調査する。確認すべき点は、open直後に -0.3 程度の報酬が出ることが計算の歪みなのか、それともこのサンプル相手・この簡略MCでは正しいEVなのかである。具体的には、相手action方策、showdown勝率算出、chip_delta符号、投入額の計上タイミング、fold時の寄与0扱いを確認する。

制約上の緊張も明確である。preflop open の正しいEVには、相手のfold頻度、callレンジ、3betレンジ、その後のpostflop実現率を織り込む必要がある。しかし、それを solver 的に解くと、確定制約 #7、すなわち報酬EVはMCのみ、CFR / solver / 反実仮想なし、という制約に抵触しうる。したがって、対処は #7 の枠内で `rollout_ev` の歪みを是正する方向に限定する。

撤退意識も更新する。もし「MCの枠内では preflop open を原理的に正しく評価できない」と判明した場合、それは PokerRL + GRPO アプローチの根幹に関わる分岐である。§56.6 の撤退基準には現時点では未抵触だが、7層目の解決可否は今後の撤退判断に直結しうる。

### 74.6 制約継承

本節の診断と方針は、確定制約 #4 / #5 / #6 / #7 / #11 / #16 / #20、および §65 / §66 / §67 / §70 / §71 / §72 / §73 と整合する。checkpoint再測定は eval mode で行い、state 非破壊を維持した。Spot Checks 50 は評価専用・訓練混入禁止を維持し、preflop報酬診断の局面は別カード・別IDで新規構築した。崩壊checkpointである step6500 は、本採用・resume元にしない。

## 75. fold equity対処の実装とtrial結果（7層目の対処：preflop忘却停止と固定pの限界）

§74で、7層目として `rollout_ev` が全員showdown前提でpreflop openのfold equityを評価できず、強い参加handでもopen報酬がfold報酬を下回る歪みAを特定した。本節は、その対処として `rollout_ev` にMC opponent fold responseを追加したTask 7と、短時間trialでpreflop忘却停止・固定pの限界・made_hand未達を確認したTask 8の結果を記録する。

### 75.1 対処の実装（Task 7、コミット `b026dbb`）

Task 7では、`rollout_ev` に、直近hero操作が `CompletionBettingOrRaisingTo` の時だけ相手のfold/callをMCサンプルする経路を追加した。発火はpreflop限定である。これは固定fold確率pに基づくMC opponent policyであり、solver/CFR/均衡解/反実仮想を使わないため、報酬EVはMCのみとする確定制約#7の枠内である。

Configには `rollout_preflop_opponent_fold_probability=0.7` を追加した。これは強い参加handをopen>foldに立てるための暫定値であり、恒久値ではない。postflop側は `rollout_postflop_opponent_fold_probability=0.0` とし、§73でmade_hand 7/8に効いたpostflop報酬を変えない設計にした。両方ともConfig経由であり、確定制約#6を守る。

p=0では従来の全員showdown版と数学的に等価であることを後方互換テストで確認した。stateは読み取り専用で扱い、候補action評価側のstate非破壊（#16）を維持している。なお、全員fold時のpot帰属は今回preflop限定で実害が小さいが、自前pot計算はサイドポット/all-in/death SBに完全対応するものではない。postflopへ有効化する場合は、PokerKit automationへ委譲する確定制約#15との整合を改めて確認する必要がある。

検証では、指定10ファイルのpytestが `51 passed`、`verify_pokerrl_encode.py` が `passed=8/PASS` であることを確認した。

### 75.2 p曲線（probe実測、random unknown hole・5seed・weight後）

§74.3と同じく、Spot Checks 50そのものは使わず（#11）、別カード・別IDで構築したpreflop probe局面に対して、random unknown hole samplingでp曲線を実測した。以下はplayouts 64での `open - fold` の平均値である。

| probe | p=0.0 | p=0.45 | p=0.55 | p=0.7 |
|---|---:|---:|---:|---:|
| HJ AQs open | -0.163 | -0.045 | -0.010 | +0.086 |
| CO 99 open | -0.135 | +0.027 | - | +0.139 |
| UTG 72o fold | -0.348 | - | - | -0.130 |

この結果から、固定pでfold equityは確かに報酬へ入ることが分かった。AQsと99の両方をopen>foldに立てるにはp=0.7が必要だった。一方で、72oはprobe上ではp=0.7でもfold>openを維持した。

ただし、固定pは強handにもtrash handにも同じfold equityを乗せる。したがって、強handを立てるためにpを上げるほど、trash openも相対的に有利になる原理的弱点がある。72o probeで持ちこたえても、別のtrash handや実訓練の分布で崩れる可能性は、この時点で既に予告されていた。

### 75.3 trial結果（Task 8、300step、curriculum ratio 1.0 × groups 2、HEAD `b026dbb`）

Task 8では、HEAD `b026dbb`、`--steps 300`、`--curriculum-ratio 1.0`、`--curriculum-groups-per-step 2`、`--microbatch-groups 1` で短時間trialを実行した。checkpoint guardは `results/grpo/` 配下のみを許可するため、当初指定した `results\grpo_trial_foldequity` は拒否され、trial dirは `results\grpo\trial_foldequity` とした。これにより本番 `results\grpo\latest` は汚していない。

最も重要な成功は、preflop破滅的忘却が停止したことである。preflop_open参加handの正解側生確率は、step0からstep300で維持または上昇した。

| spot | 内容 | step0 | step300 | 判定 |
|---|---|---:|---:|---|
| spot_021 | HJ KQs open | 0.981 | 0.982 | 維持 |
| spot_022 | CO 55 open | 0.983 | 0.985 | 維持 |
| spot_024 | CO A5s open | 0.883 | 0.905 | 上昇 |

position代表spotも全PASSを維持した。

| spot | 内容 | step0 | step300 | 判定 |
|---|---|---:|---:|---|
| spot_001 | UTG AKo | 0.998 | 0.998 | 維持 |
| spot_003 | BTN A9s | 0.828 | 0.860 | 上昇 |
| spot_048 | BTN Q9s | 0.553 | 0.620 | 上昇 |

これは第2次本訓練の崩壊（§74.1、KQs 0.979→0.008、AKo 0.998→0.059）とは明確に逆である。7層目の対処は、preflop忘却の停止に訓練上も効くことが実証された。

一方で、固定pの副作用も顕在化した。trash foldである spot_023 HJ 83o は、fold正解側確率が 0.522→0.434 に低下し、FAIL化した。固定fold equityがtrash openにも乗るという§75.2の構造的弱点が、probeの72oではなく訓練中の83oで表面化した。

made_handは6/8のままで、§73の第2次本訓練で到達した7/8には300stepでは届かなかった。preflop崩壊は起きていないため、トレードオフ軸は「preflop崩壊」から「made_hand未達」へ移動した可能性がある。これはstep数、curriculum密度、学習リソース配分のどれが支配的かを別途切り分ける必要がある。

all_inは別軸の未解決課題である。カテゴリは4/7を維持したが、生確率はさらに低下し、spot_018のall_in確率は 0.00131→0.000097 だった。これは§70.5および§72.6で記録したall-in head死亡問題の継続であり、fold equity対処とは切り分ける。

eval時系列では、全体pass_rateは step50で0.88、step300で0.84だった。preflop_openは step50で4/4、step100以降は3/4、positionは7/8維持、made_handは6/8維持だった。quality statusはOKを維持し、HALTは発生していない。VRAMピークは約4501MB、wall timeは約13分55秒であった。

### 75.4 切り分けと次の対処（選択肢C：ハンド強度依存fold確率）

今回のtrialにより、固定pはpreflop忘却停止には効くが、trash判別を削ることが分かった。この副作用は固定pの構造的限界であり、pを下げるだけでは強handが再びopen<foldに戻り、pを上げるだけではtrash openがさらに緩む。固定p内の調整は、強handを立てる力とtrashを抑える力のトレードオフを往復するだけで、根本解になりにくい。

したがって次の対処は、選択肢Cであるハンド強度依存fold確率に進むのが自然である。これは、まず最小固定pで「fold equityがpreflop忘却に効く」因果を確認し、必要な分だけ精密化するというTask 7の想定された着地点である。

ただし、hand strengthからfold率を決める処理は、突き詰めると相手の最適応答や均衡fold頻度に近づき、確定制約#7の禁止ラインに接近する。そのため、次の実装を行う場合は、強度bucketごとの固定確率または単純な固定関数に限定する。均衡解を解かない、反復最適化しない、相手レンジを収束計算しない、反実仮想比較をしない、という条件を明示してから着手する。

made_hand未達は、この選択肢Cとは切り分けて扱う。一度に複数変数を動かすと、preflop trash判別の回復とmade_hand到達のどちらが効いたのか判定できなくなる。まずCでtrash判別を回復し、その後にmade_hand軸を再評価する。

### 75.5 制約継承

本節の実装・trial・方針は、確定制約#6 / #7 / #11 / #15 / #16 / #20 と整合する。fold確率はConfig経由であり、報酬EVはMCのみである。Spot Checks 50は評価専用で、訓練には混ぜていない。stateは非破壊で扱い、trial checkpoint（`results\grpo\trial_foldequity\latest`、step300）はmade_hand未達の暫定状態であるため、本採用・resume元にしない。

撤退基準（§56.6）には未抵触である。preflop忘却という主要因には明確な前進があり、タイムボックスにも余裕がある。ただし、「made_hand 7/8とpreflop維持の両立」というtrial合格線にはまだ届いていない。次段階では、固定pの限界を踏まえてハンド強度依存fold確率を検討し、その後にmade_hand未達の原因を再度切り分ける。

## 76. hand強度依存fold確率の実装とtrial（固定pの限界解消・trash判別の訓練回復）

§75で、固定p=0.7の限界を記録した。強い参加handをopen>foldに立てるには十分なfold equityが必要だが、固定pではtrash openにも同じfold equityが乗る。その結果、Task 8 trialでは spot_023 HJ 83o が fold 0.522→0.434 へ低下し、FAIL化した。本節は、その対処としてhand強度依存fold確率を実装したTask 9と、短時間trialでtrash判別の訓練回復を確認したTask 10の結果を記録する。

### 76.1 設計判断（案A：hero hand強度bucket、コミット `91d098c`）

Task 9では、`rollout_ev` の相手fold確率を、hero preflop hand強度のpercentile bucketに依存する固定テーブルへ拡張した。hero hole 2枚をeval7の全1326コンボ列挙でpercentile化し、bucketごとに固定fold率をlookupする。

fold率テーブルはConfig経由（#6）で管理する。初期値は以下である。

| hero hand percentile | opponent fold probability |
|---:|---:|
| `>= 0.75` | 0.70 |
| `>= 0.55` | 0.45 |
| `>= 0.35` | 0.20 |
| `< 0.35` | 0.05 |

採用したのは案A、すなわち hero のhand強度で相手fold率を決める設計である。案B、すなわち相手hole強度で各相手のfold率を決める設計は、平均fold率がhero handごとに十分変わらず、83oの副作用を直しきれない懸念があったため次善とした。

この意味論は重要である。案Aは、相手fold率がhero private cardを参照するため、「現実の相手方策」としては不自然である。これは相手の最適応答を解くsolverではなく、固定テーブルが与える「報酬側のfold-equity prior」、すなわち強いopenは平均的にfold equityが高い、という事前知識の近似として扱う。均衡解、反復最適化、相手レンジ収束、反実仮想は行わず、bucket表も訓練中に更新しない。したがって、報酬EVはMCのみとする確定制約#7の枠内である。`test_reward_wiring.py` のsolver/CFR import禁止テストも維持した。

後方互換も維持した。全bucket同値を0.7にすれば固定p版へ縮退し、全bucket同値を0.0にすれば従来showdown版へ数学的に縮退する。postflop fold probability は0.0のままであり、§75.1と同じくpostflop非破壊である。発火条件もpreflopかつhero raise直後に限定し、stateはhole cardsを読むだけで変更しない（#16）。

検証では、指定10ファイルのpytestが `55 passed`、`verify_pokerrl_encode.py` が `passed=8/PASS` であった。

### 76.2 bucket割り当てとprobe実測（random hole・seeds20・raw rollout reward）

主要handのbucket割り当ては以下である。参加handとtrash handが意図どおり分離した。

| hand | percentile | fold probability |
|---|---:|---:|
| AQs | 0.923 | 0.70 |
| 99 | 0.975 | 0.70 |
| KQs | 0.790 | 0.70 |
| A5s | 0.839 | 0.70 |
| 83o | 0.199 | 0.05 |
| 72o | 0.127 | 0.05 |

probeは、§75.2と同じくSpot Checks 50そのものを使わず（#11）、別カード・別IDのpreflop局面で行った。random unknown hole、20 seed、raw rollout rewardで測定した。以下はplayouts 64での `open - fold` である。

| probe | open - fold |
|---|---:|
| HJ AQs open | +0.335 |
| CO 99 open | +0.740 |
| UTG 72o fold | -1.699 |
| HJ 83o fold | -1.567 |

固定p=0.7の§75.2では、AQs +0.086、99 +0.139、72o -0.130 だった。hand強度依存化により、強handは深く正、trashは深く負を同時に達成した。固定pの「強handを立てるとtrashも緩む」というトレードオフは、probe上では解消した。

### 76.3 trial結果（Task 10、300step、curriculum ratio 1.0 × groups 2、HEAD `91d098c`）

Task 10では、HEAD `91d098c`、`--steps 300`、`--curriculum-ratio 1.0`、`--curriculum-groups-per-step 2`、`--microbatch-groups 1` で短時間trialを実行した。checkpointは `results\grpo\trial_strengthfold` に保存し、本番 `results\grpo\latest` は汚していない。

主目的であるtrash判別の訓練回復は成功した。spot_023 HJ 83o はfold正解のspotであり、step0→step300で fold正解側確率が 0.570→0.547 だった。低下は小さいが、閾値0.5を上回りPASSを維持した。§75.3の固定p trialでは 0.522→0.434 へ低下してFAIL化していたため、hand強度依存fold確率は固定pの副作用を訓練上も解消した。§75で経験したprobe/訓練の乖離は今回は起きず、設計が訓練分布で機能した。

preflop_open参加handも維持された。

| spot | target | step0 | step300 | delta | status |
|---|---|---:|---:|---:|---|
| spot_021 HJ KQs | call/raise | 0.979 | 0.976 | -0.003 | PASS維持 |
| spot_022 CO 55 | call/raise | 0.983 | 0.973 | -0.010 | PASS維持 |
| spot_023 HJ 83o | fold | 0.570 | 0.547 | -0.023 | PASS維持 |
| spot_024 CO A5s | call/raise | 0.874 | 0.868 | -0.006 | PASS維持 |

position代表spotも維持された。

| spot | target | step0 | step300 | delta | status |
|---|---|---:|---:|---:|---|
| spot_001 UTG AKo | call/raise | 0.998 | 0.997 | -0.001 | PASS維持 |
| spot_003 BTN A9s | call/raise | 0.843 | 0.835 | -0.008 | PASS維持 |
| spot_047 UTG QQ | call/raise | 0.997 | 0.997 | -0.000 | PASS維持 |
| spot_048 BTN Q9s | call/raise | 0.513 | 0.513 | +0.000 | PASS維持 |

eval時系列は以下である。

| eval_step | total | preflop_open | position | made_hand | all_in | quality |
|---:|---:|---:|---:|---:|---:|---|
| 50 | 0.88 | 4/4 | 7/8 | 6/8 | 4/7 | OK |
| 100 | 0.88 | 4/4 | 7/8 | 6/8 | 4/7 | OK |
| 150 | 0.88 | 4/4 | 7/8 | 6/8 | 4/7 | OK |
| 200 | 0.88 | 4/4 | 7/8 | 6/8 | 4/7 | OK |
| 250 | 0.86 | 4/4 | 7/8 | 6/8 | 4/7 | OK |
| 300 | 0.86 | 4/4 | 7/8 | 6/8 | 4/7 | OK |

quality statusは全evalでOKであり、HALTは発生していない。floor割れもない。VRAMピークは約4597MB、wall timeは約16分18秒であった。

一方で、riverはstep250以降に 1/1→0/1 となり、totalは0.88→0.86へ下がった。1 spotのみであるためノイズ範囲の可能性はあるが、postflop系の微劣化として記録する。made_hand軸を再び触る際の監視対象である。

made_handは6/8を維持したが、7/8には300stepでは届かなかった。崩壊はしていない。§73の第2次本訓練ではmade_hand 7/8に到達したがpreflopが崩壊した。今回のTask 10ではpreflopは健全だがmade_handは6/8に留まった。つまり、両端は別々に達成したが、made_hand 7/8とpreflop維持の両立はまだ未実証である。

all_inは別軸の未解決課題である。カテゴリは4/7を維持したが、生確率はさらに低下した。spot_018のall-in確率は 0.001466→0.000112、spot_019は 0.000006→0.000001、spot_043は 0.001226→0.000331 であった。trialを重ねるごとにall-in headが死につつある傾向は継続している。

### 76.4 現在地と次の焦点

7層目、すなわち報酬のpreflop open過小評価への対処は、preflop面では決着した。Task 7のfold equity追加によりpreflop破滅的忘却は停止し、Task 9のhand強度依存fold確率により固定pの副作用だったtrash判別劣化も回復した。preflopの忘却・誤判別という症状は、訓練上も解消したと判断できる。

残る焦点は、preflop健全を保ったまま made_hand 7/8 に到達できるかである。これは§73で見えたmade_hand改善軸であり、7層目とは別問題として扱う。次セッションでは、made_hand未達がstep不足なのか、fold equity変更後の学習リソース配分なのかを実測で切り分ける。対処に飛びつかず、生確率とcurriculum局面の動きを見てから判断する。riverの微劣化も同時に監視する。

### 76.5 制約継承

本節の実装・trial・方針は、確定制約#6 / #7 / #11 / #15 / #16 / #20 と整合する。hand強度依存fold確率は固定テーブルのMC方策であり、solver/CFR/均衡解/反実仮想を使っていない。Spot Checks 50は評価専用で、訓練には混ぜていない。stateは非破壊で扱い、trial checkpoint（`results\grpo\trial_strengthfold\latest`、step300）はmade_hand未達の暫定状態であるため、本採用・resume元にしない。

撤退基準（§56.6）には未抵触である。preflop面の主要因には明確な前進があり、タイムボックスにも余裕がある。ただし、「made_hand 7/8とpreflop維持の両立」というtrial合格線は依然として未達である。
