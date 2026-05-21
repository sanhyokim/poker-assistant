
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

訓練スケジュール:
  Step 0: 環境構築（20分）
  Step 1: Phase 1 基礎訓練 ×3シード（3〜6日）
  Step 2: Phase 1 品質確認（数時間）
  Step 3: Phase 2 自己対戦（2〜3日）
  Step 4: Phase 3 混合訓練（1〜2週間）
  Step 5: 最終品質検証（1日）
  合計: 約1ヶ月

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
  dberweger2017版の5層×256ユニットはこの知見に基づく。
  無駄に大きくすると学習が不安定になる。

これらの原則は2人の小〜中規模ゲームで実証されたものであり、
6人NLHEで厳密に最適化されたレシピは2026年5月時点で存在しない。
dberweger2017版の3段階訓練は開発者の経験則であり、原論文の手法とは異なる。
