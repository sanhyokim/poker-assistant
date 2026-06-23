# pokerrl-training snapshot
**Updated:** 2026-06-23 JST v7（★本セッション確定: 自前生成パイプラインの**基盤を実証**。(1) **正本レンジ=pekarstas単一確定**（46ライン169クラス展開、HU postflopカバレッジ39/45=86.7%、欠落6種はMP起点CO/BTN応答で許容）。パース済JSON・到達レンジ計算（SRP/3BET/4BET）・river30件E2E実証。(2) **postflop-solverを公式ドキュメントで完全理解**（TreeConfig全13フィールド、bet/raise分離、geometric `e`、merging/threshold）。**前セッションのflop 26GB crashは設定不備が真因と判明・訂正**（merging/threshold/raise/geometric未指定で木が膨張、bet sizesの数だけが原因ではない）。**DESIGN_NOTES §82に全記録。** (3) 公式設定（`60%, e, a`/`2.5x`/merging0.1/threshold1.5・0.15/32-bit）で**全street解ける**: flop 9-16GBでメモリ収束・0.5%到達平均411秒/件、turn約1.2秒、river約40ms。(4) **16-bit圧縮は却下**（river実測で最大乖離0.36、教師不可）。**並列は同一品質では不可**（timeout内iteration減少）。**低品質解4-9%も却下**（混合分布最大0.79変動）。(5) **教師数の単位が旧SFTと非互換**（旧56万=局面単位、新=hand単位で1ボード約1000例）。次セッションは生成計画の3前提（総教師例数・シナリオ配分・flop 0.5%vs1%分布差）を解明し本生成へ。**設計の正本=DESIGN_NOTES §80(方針)+§81(prompt契約)+§82(solver/flop)。§0.5参照ナビ参照。**）
**Session（前半〜中盤）:** 設計確定ステップ1-3完了（真値源・schema文書化・教師源=Rust Solver）。56万局面再利用は(b)成立だが外部供給要（§2.4）、ソルバー素性確認（§3.5）、レンジ手元になし（§2.5）、外部既製データ大量取得不可（§2.6）。
**Session（試作と方針転換）:** 無料レンジでriver30件試作→配管Goだがhero包含46.67%脱落、全件「レンジ定義の食い違い」型、対応づけ修正0件・pekarstas統一でも上限66.67%。→**PokerBench局面再利用を放棄、局面もレンジも自前で一貫生成へ転換**（§2.7、ユーザー決定）。
**Session（外部調達の総当たり調査と確定）:** 自前生成の正本レンジ候補を広く実物調査。(12) 完成教師データ（局面+混合分布セット）はGitHub/HuggingFace/Kaggle/商用に**存在せず**（PokerBench元レンジ非公開、GTO Wizard APIは対戦用、RangeConverter postflopは閲覧専用、他も灰色/未確認、§2.8）。(13) 高品質レンジ（種類B）はFreeBetRange "Original GTO"が最有力（丸めなし混合頻度・HRC由来・PioSOLVER形式export、Pro年$159/Elite年$348、ただし大量export可否と実ファイル要件は要問い合わせ・素性にユーザー不安）、RangeConverter次点（$99〜、実ファイル精度未確認）。(14) 自前solve路線の精査: **Monker無料版はturn/river限定でpreflop非対応**（preflopはフル版€499 or MonkerGuy購入が必要）、PioSOLVER Edge $475〜・preflopは64GB RAM要で手元31.8GBでは不足、flop速度の壁はソルバー替えても残る。(15) **GTONexus（無料ソルバー、Reddit発、Amazon/TikTokエンジニア2名作、0.3% exploitability自己申告でPio/GTO+同等、HU対応）を実機確認→エクスポート不可と判明し脱落**（無署名でWindows Defender警告も、クローズドソース・AWS依存）。→**エクスポート可能な無料レンジはAHTOOOXA等のみ**と確定。(16) ユーザー決定: AHTOOOXA等の無料丸めレンジで自前生成を一通り通し、強さを実測してから高精度（有料）投資の要否を判断。

**重要な前提（揺るがない）**: SFT→GRPOの順。SFT側に混合分布の正解ラベル教師が必要。GRPOの報酬はMCのEVのみでソルバー不使用（#7）。教師源=Rust Solver。**製品設計上preflop→GTOチャート（ソルバー不使用）、postflop→Rustソルバー。手元ソルバーはpostflop専用でpreflopは解けない（§3.5、§0プロジェクト全体像）。よってレンジ＝GTOチャートは外部調達が必須で、手元ソルバーでは作れない。**

**★現在のクリティカルパス**: 自前生成パイプラインの**基盤は実証完了**（pekarstas正本確定、公式設定で全streetメモリ収束、flop 0.5%到達）。次は**生成計画の3前提を解明してから本生成**: (1) 総教師例数の目標（§80.8 SFT設計と照合。旧56万=局面単位、新=hand単位で1ボード約1000例＝非互換）、(2) 39シナリオ（SRP/3BET/4BET）へのボード配分、(3) flop 0.5% vs 1% の分布差実測（1%で済めば生成時間半減）。配管・solver・レンジは全て実証済み。設計の正本=DESIGN_NOTES §80+§81+§82。

---

## 0. プロジェクト全体像（最上位目的・製品構成・本訓練の位置づけ）

**最上位目的: 強いポーカー支援システムを作ること。** 確定制約・Spot Checks・タイムボックスは全てこの目的に仕える手段であり目的そのものではない。「制約を守れたが永遠に強くならない」は目的の失敗として扱う。

製品は poker-assistant 本体（参照URL: https://github.com/sanhyokim/poker-assistant、※sanhyokim2050ではない）。戦略ルーティングは局面で分岐（SPEC §9）:
- **preflop → GTOチャート**（SPEC §9.2、Solver不使用）。
- **HU postflop（active_player_count==2）→ PokerRL+GRPO推論**（SPEC §9.3/§10A推論ブリッジ）。**これが本訓練リポジトリ pokerrl-training で訓練しているモデル。今ここ。** ローカルGPU常駐推論、T1 50-300ms目標、出力 fold/call/raise/allin_prob + raise_size_ratio。Deep CFRはStage D完了までフォールバック。
- **Multiway postflop（active_player_count>=3）→ PokerSkill式Context Engine + LLM**（SPEC §9.4、DESIGN_NOTES §54-55/§60）。**完成済み**: Context Engine（決定論的、82テストPASS）+ GPT-5.4-mini（2026-06-06確定）+ eval7数理補助。

**本訓練の手法選定根拠（§48）**: dcaustin33/poker_rl（小型LLM+補助ヘッド+SFT+GRPO自己対戦）を参考。土台データ PokerBench(560k GTO、arXiv:2501.08328)・Pluribus(60k)。base Phi-4-mini 3.8B。手法出典 PokerBench(AAAI 2025)/GRPO(arXiv:2402.03300)/DAPO(arXiv:2503.14476)。**報酬設計はどの論文にも正解レシピ無し、独自設計**（§78.2）。

※訓練環境6-max（#2）だが訓練方策はHU postflopで使用。SPEC §10Aは推論ブリッジの正仕様で報酬・curriculum設計はSPEC管轄外（§78.2、SPEC編集不要）。

---

## 0.5 参照ナビゲーション（次に読むAIへ: どの文書のどこを見るべきか）

正本3文書は `C:\Users\user\Desktop\dev\poker-system\docs`（branch `main`）。コード本体は `C:\dev\pokerrl-training`（branch `master`、HEAD `c144392`＝本セッションでコミット、pekarstas成果物含む）。**このsnapshotは引き継ぎメモであり、設計判断の正本はDESIGN_NOTES、製品仕様の正本はSPEC。** 用途別に見る場所を以下に固定する。

- **やり直しの根本＝modelに渡す情報の必要十分契約**: → **DESIGN_NOTES §81（5286行〜）**。統一prompt計算器のschema・定義契約の正本。§81.3=必要十分項目リスト（36項目）、§81.4=導出5定義（過去street別拠出9手順/common pot/effective-stack behind/SPR/pot odds）、§81.6=検算規約（15行）、§81.7=fail-closed 11条件、§81.8=player status契約、§81.9=未解決4点。**SFTやり直しの実装はこの§81に従う。** （旧`unified_prompt_schema.md`は§81へ移植し廃止済み。）
- **方針転換の経緯と理由（なぜSFTやり直しか・混合戦略へ・評価基盤欠陥）**: → **DESIGN_NOTES §80（5190行〜）**。§80.5=完全情報でも単一最適は一意化しない（混合戦略）、§80.6=方針転換（ユーザー決定3点）、§80.7=やり直し範囲、§80.8=次段設計課題、§80.9=制約整合、§80.10=全系監査方針。
- **★solver完全理解・公式設定・flop生成品質（本セッション）**: → **DESIGN_NOTES §82（5557行〜）**。§82.1=TreeConfig全13フィールド・bet/raise分離・geometric・merging/threshold、§82.2=26GB crashの真因（設定不備、前セッションの誤り訂正）、§82.3=公式設定で全streetメモリ収束、§82.4=16-bit却下、§82.5=flop品質（0.5%到達411秒/件・並列不可・低品質却下）、§82.6=教師数の単位（旧局面/新hand非互換）。**自前生成パイプラインの実装はこの§82のsolver設定に従う。**
- **GameState/PlayerState/ActionRecord等のデータ構造**: → **SPEC §4（509行〜）**。§4.1 GameState、§4.2 PlayerState、§4.3 ActionRecord。§81の「現行本番供給」列はこのSPEC §4.1-4.3を基準に`要拡張`を判定している。
- **戦略ルーティング（preflop=GTOチャート/HU postflop=PokerRL/MW=LLM）**: → **SPEC §9（2127行〜）**。§9.2 Preflop、§9.3 HU Postflop。
- **推論ブリッジ（本訓練modelの本番接続仕様）**: → **SPEC §10A**（§9.3から参照）。報酬・curriculumはSPEC管轄外（§78.2）。
- **過去の訓練失敗の層構造と対処の履歴**: → **DESIGN_NOTES §70-79**（4476行〜）。ただしSpot Checks由来数値は§80で再評価対象（額面通り信頼しない）。
- **Rust Solver実測（速度・品質・入出力契約・素性）**: → 本snapshot §3。**ソルバー本体の素性**（postflop専用/AGPL/bunching）は§3.5。
- **本セッションで完了した調査の詳細**: → 本snapshot §2.1-§2.8（特に§2.7=46.67%脱落と方針転換、§2.8=外部調達総当たり）。
- **確定制約の全リスト**: → 本snapshot §8。

注: DESIGN_NOTESの行番号は追記で変動しうる。節番号（§80/§81等）を一次キーとし、行番号は補助とすること。

---

## 1. このsnapshotの位置づけ・現在地

`C:\dev\pokerrl-training`（ローカルのみ・リモートなし、branch `master`）でSprint 3を引き継ぐ。

**★HEAD訂正（ステップ1で実測判明）**: 旧snapshotはHEAD=`c9dae39`と記載していたが、実体は master `2c0f744`（crash対策3層の3つ目=collect層catch、§80.0/snapshot Session参照のTask18相当）。**正しいHEADは `2c0f744`**。以後の文書・調査はこれを基準とする。次に実装役へ確認させる際は `git log --oneline` で実体を再確認すること。

docs正規パス: `C:\Users\user\Desktop\dev\poker-system\docs`（branch `main`、#13）。

**現在のフェーズ**: §80路線の**生成計画確定フェーズ**（設計確定フェーズは本セッションで完了：pekarstas正本確定・solver公式設定確立・全streetメモリ収束・flop 0.5%到達）。自前生成パイプラインの基盤実証は済み、残るは「総教師例数・シナリオ配分・flop品質要求」の3前提を確定して本生成に入る段階。実装（計算器・SFTやり直し）は3前提確定後。

**設計確定フェーズの進捗**:
- ステップ1（計算器の真値源 実測確認）= **完了**。9項目＋SPR/pot odds 全てに真値源あり、取得不能ゼロ。
- ステップ2（統一prompt schema 契約文書化）= **完了**。DESIGN_NOTES §81へ移植済み（旧`unified_prompt_schema.md`268行、現在は§81、コミット`bb8c972`）。
- ステップ3（混合戦略教師の供給源調査）= **完了**。Rust Solverの1択に確定。
- ユーザー案調査（56万局面再利用）= **完了**。(b)成立、postflop 50万再利用可、外部供給要（§2.4）。
- ソルバー素性確認（README実物）= **完了**。postflop専用・bunching effect・AGPL・重み付きレンジ対応（§3.5）。
- レンジ供給源調査（宿題1・手元）= **完了**。(c)手元になし、外部調達必須（§2.5）。
- 外部データ調査（宿題3・ウェブ）= **完了**。PokerBench元レンジ入手不可、GTO Wizard API等の既製混合分布も公式合法な大量取得不可。自前ソルバー路線が最善と確定（§2.6）。
- 無料レンジ調査（別エージェント）= **完了**。AHTOOOXA/poker-charts等が機械可読・MIT・相互整合あり、ただし0/50/100や25%丸め・出所不明（§2.6末）。
- 教師生成パイプライン動作確認試作（river30件）= **完了**。配管Go（技術的に一周成功）、ただしhero包含46.67%脱落（§2.7）。
- 脱落14件の原因切り分け= **完了**。全件分類B（レンジ定義の食い違い）、対応づけ修正0件、pekarstas統一でも上限66.67%（§2.7）。
- **最終結論（ユーザー決定）= PokerBench局面の再利用路線を放棄、自前生成へ転換**（§2.7末）。
- 外部調達の総当たり調査= **完了**（§2.8）。完成教師データ無し、有料高品質レンジ（FreeBetRange Pro年$159/Elite年$348・RangeConverter$99〜）は存在するが素性/要件未確認、自前solveはMonker無料preflop非対応・Pio Edge等はRAM不足、GTONexus（無料ソルバー）はエクスポート不可で脱落。**エクスポート可能な無料レンジはAHTOOOXA等のみ**。
- **方針確定（ユーザー決定）= 当面AHTOOOXA等の無料丸めレンジで自前生成を一通り通し、強さを実測してから高精度（有料）投資の要否を判断**（§2.8末）。
- **次にやること**: 自前生成の設計と着手。(a)局面生成の具体設計（レンジから手札サンプル→プリフロップツリー→ボード配布→postflop局面）、(b)AHTOOOXAレンジの取得・パース・正本化、(c)均衡選択規約（§3.4の未確定論点）、(d)flop速度（宿題2）、(e)必要枚数。配管は実証済み（§2.7、`data/teacher_proto/run_river_proto.py`）。

---

## 2. 本セッション（2026-06-22）で完了したこと

### 2.1 ステップ1: 統一prompt計算器の真値源 実測確認（調査のみ、コード非変更）
PokerKit 0.7.4 と `state_factory.py`（master `2c0f744`、read-only）で、計算器が出力すべき9項目＋SPR/pot odds の真値源を実測確認した。結論=**全項目に直接取得または導出可能な真値源あり、取得不能ゼロ**。
- 直接取得: 現在stack(`state.stacks[i]`)、current street bet(`state.bets[i]`)、main/side-pot(`tuple(state.pots)`、先頭main)、side-pot資格(`Pot.player_indices`)、call額(`checking_or_calling_amount`)、raise-to範囲(`min/max_completion_betting_or_raising_to_amount`)、rake(`state.rake`、`Pot.raked/unraked_amount`)。
- 導出（要統一定義）: 過去street別拠出（`state.operations`をBetCollection/street境界で区切る）、累積拠出（`-payoffs[i]`、`starting_stacks[i]-stacks[i]`で相互検算）、common pot（`total_pot_amount-sum(bets)`と`sum(pot.amount)`の2式一致）、effective-stack vector（behind=`min(hero,opp)`）、SPR、pot odds。
- side-pot実測: 非対称stack`(50,100,200,1000,200,100)`の非正本stateをインライン生成し、main 300/(0-5)・side1 250/(1-5)・side2 300/(2-4)を取得、拠出総額850と完全一致。`Pot.player_indices`が資格の直接真値。
- 通常decision実測（UTG→3 raise後HJ actor）: `call=3, min_raise_to=5, max_raise_to=100, pot=4.5, pot odds=3/7.5=0.4`。
- rake: 現stateは標準rake **0%**、全pot raked=0。`total_pot_amount`/`safe_total_pot_amount`はrake内訳非保持。
- **重要な申し送り**: 現行 `pokerbench_prompt._prompt_pot_amount` はpostflopで自前履歴集計しており、新計算器の真値源に使うべきでない（PokerKit側 `total_pot_amount`/`sum(pot.amount)` を正とする）。

### 2.2 ステップ2: 統一prompt schema・定義契約文書を作成
当初docs `main` に独立ファイルとして作成し点検（268行、品質良好）。本セッションでDESIGN_NOTES §81へ全文移植・一元化（コミット`bb8c972`、逆変換後SHA-256一致で無改変確認、独立ファイル廃止）。内容:
- §3 必要十分項目リスト（取得区分・PokerKit真値源・現行本番供給可否の3列。本番供給不能/未確定は `要拡張` と明示）。
- §4 導出5項目の一意な式（過去street別拠出のoperations走査手順／common pot 2式／effective-stack behind定義／SPR分母=playable_unraked_pot／pot odds multiway分母=heroが獲得可能なpotのみ `Σ min(C'_i, H)`）。
- §5 pot/side-pot/rake契約（真値源はPokerKitのみ、potの結合分割資格判定を計算器側で再実装禁止。non-zero rake時はplayable pot真値取得法が未実測のため生成停止）。
- §6 検算規約（全項目に独立2経路の一致、許容差で不整合を隠さない、不一致時は停止）。
- §7 fail-closed（空文`Before the flop, .`再発防止。構造検証成功後にのみ文字列生成。欠落補完・推定禁止）。
- §8 player status契約（folded/all_in/active_with_chipsの定義。本番`in_current_hand`だけでは区別不能→GameState拡張まで本番生成停止）。
- §9 未解決事項（後述4論点の一部）。
- **Commander点検で残した申し送り**: (a) `C_i=-payoffs[i]`は「chips pushing前decision snapshot限定」前提に依存。ステップ1は1局面のみ実測なので、計算器実装時に複数street decisionで再実測すべき。(b) §3でPokerKit属性名（`street_index`等）の実在は全ては未実測。実装時に属性名の実在を先に実測確認すること。(c) non-zero rakeのplayable pot取得は未実測（現状rake 0%で実害なし）。

### 2.3 ステップ3: 混合戦略教師の供給源調査
教師源を**Rust postflop Solverの1択**に確定。
- **Rust Solver = (b)改修すれば使える**: HU postflopのhand別混合分布を実測取得。river実用的だがflopが重い・品質未達（後述§3）。schema adapter・range provider・固定action abstraction・hand別matrix抽出・exploitability未達label破棄・並列化/キャッシュが必要。
- **Deep CFR = (c)教師に使えない**: 分布は高速出力（CPU 0.681ms/回）だが既存モデルが品質不合格（9局面中合理的1局面、raise 70-80%偏重がモデル自体の問題、DESIGN_NOTES `C:/.../docs/DESIGN_NOTES.md:3131`）。入力も統一schema不足（最後のactionのみ、range入力なし、粗い3action+単一連続sizing）。
- **PokerBench 56万 = (c)分布復元不可**: CSV/JSONに単一`correct_decision`/`output`のみ、action頻度未保存。SFTで使った56万はそのままでは混合戦略教師に流用不可（単一正解データのため）。

### 2.4 ユーザー案調査: 56万局面の再利用（答えは捨て局面のみ再利用、Solverで混合分布を付け直す）
判定=**(b)成立するが外部供給が必要**。
- 所在・件数: postflop train `data/pokerbench/postflop_500k_*`=**500,000件**（Flop 32,428/Turn 207,153/River 260,419）、preflop train `preflop_60k_*`=63,200件、SFT前処理後`data/sft_train_full.jsonl`=563,200件、postflop test`postflop_10k_*`=10,000、preflop test 1,000。生成順 `scripts/prepare_sft_full.py:103`（postflop 500k→preflop 63.2k連結、境界0/499999/500000/563199が元JSONと一致）。`results/sft_sequential`の15 segmentは正本データ非保持（`run_sft_sequential.py:119`で一時segment削除、canonical seg_003にも学習レコード実体なし）。
- レコード形式（実物）: CSV列=`preflop_action,board_flop,board_turn,board_river,aggressor_position,postflop_action,evaluation_at,available_moves,pot_size,hero_position,holding,correct_decision`。JSON=`instruction`(自然言語局面記述)＋`output`(単一正解、例"bet 18")のみ。**JSONにIDなし、CSVとは単純row番号joinできない**（JSON先頭=CSV ID70990）。
- Solver入力突合: board/street=取得可、current pot=`pot_size`取得可、street starting pot=導出可(500k全件正整数)、effective stack=導出可(全件100bb＋履歴、min46/median90/max98bb)、actions_played=変換可(344万token検査・未知形式0件)、legal moves=`available_moves`取得可(210パターン)。**range_oop/range_ip=情報不足（外部必須）／bet abstraction=情報不足（観測actionのみ、sizing menu無）／rake=情報不足／payoff/equilibrium=情報不足**。
- 連番・再開設計: CSV先頭列は各ファイル内`0..N-1`で重複/欠番なし、postflop 500k完全重複0件。正本ID=`dataset_version+source_sha256+split+csv_row_id`、区間`[0,10000)[10000,20000)`で管理。**成功件数をoffsetに使ってはいけない（§80.4の轍）**。manifestに各IDの`pending/running/succeeded/rejected/failed`・各種hash・exploitability・attempt数を記録、完了後に全ID集合一致を検算。
- 並列上限目安: 実機31.858GiB RAM/8core16thread、flop1局面1.474GB。RAM理論上限約12 processだがOS/木構築余裕で6-8、ただしSolverはRayonで1 processでも全core使用→`RAYON_NUM_THREADS`固定前提で**実用2-4並列**（並列性能自体は未実測）。

### 2.5 レンジ供給源調査（宿題1）: 手元資産の洗い出し
判定=**(c)手元に適合資産なし、外部調達必須**。手元候補は全て非重み付き・粗い仮定・LLM推定のいずれかで、「23 preflop line×OOP/IP×100bb」の検証可能なcombo weightを供給できない。
- `strategy/baseline_ranges.json`: 8 top-level key、pot分類単位（single_raised_pot/3bet_pot等4分類のみ）、**全て非重み付き**。500k適用でhero holding 116,508件=**23.30%がrange外**。
- `preflop_charts/6max_gto.json`: 6 position・133 leaf。**weight指定0件**、version/solver/rake/stack/metadata無し、**PokerBenchの`HJ`ポジション無し**（UTG/MP/CO/BTN/SB/BB）。HJ→MP補完+履歴intersection監査でも176,732件=**35.35%がrange外**、8,768件はaction契約自体無し。
- LLM `range_estimation`（`strategy/llm_pipeline.py:84,320`）: board/position/pot/stack/SPR/履歴/相手統計+baselineをAPIへ。validator(`llm_schemas.py:48`)は`hand:0.5`形式を構文受理するが、**promptはweight出力を要求せずGTO照合なし、hero hole cards非投入で包含保証不能**。HU本番では実際呼ばれず`recommendation_engine.py:522`でbaseline直採用（range_estimation_ms全件0.0）。非リアルタイムなら速度は緩和するが真値性・再現性・包含保証の欠如は未解消。
- 過去Solverログ`debug/solver_io/20260519,20260520`51 request: range_source全件`baseline`、unique pair 4組のみ、weight付き0件。**baseline_ranges.jsonの複製で独立資産でない**。
- **hero包含問題（決定的評価軸）**: 過去にhero実カードQ3sがrange_oop外で解けなかった実障害（`solver_parse_audit...hand_000008`、`hero_range_contains_hand:false`）。外部range導入後は各recordでhero combo weightを検査し**`weight>0`だけを教師生成対象**にする。人工的にhero handをweight1追加は到達分布を改変するため不可。weight0局面はrejectまたは元rangeとの不一致として監査。
- **残る有力経路2つ（外部調達）**: (1)PokerBench作成時の元preflop solver range/tableを取得（datasetと最も整合、入手可能性は要調査）。(2)6-max100bb・同rake・同sizingで23 lineをpreflopソルバーから再生成しcombo weightを`AA:0.5`形式へ固定変換（preflopソルバー自体は手元に無く調達要）。Rust側は重み`AA:0.5`を正式対応（`postflop-solver-local/src/range.rs:16`）。
- PokerRL側・参照dataset tool・ローカルDB・installed packageにも別range資産は**無し**（実物確認済み）。

### 2.6 外部データ調査（宿題3・ウェブ実物確認）: 既製レンジ/混合分布の入手可能性
結論=**自前ソルバー路線（外部レンジ→手元Rust Solver）が現状の最善**。外部の既製混合分布を機械的に大量取得する道は無い。
- **PokerBench元レンジ＝入手不可（第一経路は閉じた）**: 論文arXiv:2501.08328、公式GitHub `pokerllm/pokerbench`（README 6コミットのみ、"Code/Dataset coming soon"でHuggingFaceリンクのみ）、HuggingFace `RZ412/PokerBench`。いずれも公開は instruction＋単一output と、それを生成した構造化CSVのみ。**生成に使ったソルバー・入力レンジ・混合頻度は非公開**。56万局面の前提と完全整合するレンジは手に入らない。
- **GTO Wizard＝教師大量取得に使えない**: `researcher-api-client`（GitHub gtowizard-ai）は対戦ベンチマーク用（論文arXiv:2603.23660「GTO Wizard Benchmark」、HU NLHEで自エージェントを強エージェントと対戦させ強さを測る、AIVAT分散低減）。混合分布を大量DLする用途でない。非公式 `ashewang/gtowizard_parser` はブラウザtoken抜き取り認証で、作者自身が「リクエスト過多でアクセス制限される」と明記。規約違反リスク＋レート制限で50万件は非現実的。rake設定はGTO Wizard AIで可能（カスタムソルブでplayer数/stack/blind/ante/rake/straddle/limp設定可、ただしHU専用・2024/09時点、SPR制限あり）。
- **他サービス**: RangeConverter（preflop DL $99〜、PioSolver/MonkerSolver形式、postflopはAggregated Reports/Viewer中心）、PeakGTO（preflop=HRC/Monker、postflop=PioSolver、exploitability 0.25-0.5%）、GTOBase（6-max Cash 100bb全局面、ただしPokerStars NL500 rake前提）。いずれも特定rake前提・閲覧/トレーナー中心で、**公式の大量機械エクスポートは確認できず**。多くがrake込みで解かれ、手元環境(rake 0%)と前提が食い違う。
- **rakeの整理**: rakeはオンラインcashで通常徴収（トーナメントはante中心）。手元訓練環境はrake 0%（§2.4実測）。既製データの多くはrakeあり前提で、そのまま使うと土台に不整合（§80型）。自前ソルバーなら rake_rate/cap を明示設定でき前提を揃えられる（§3.2入力にrake_rate/rake_cap）。
- **将来の評価環境候補（副産物）**: GTO Wizard Benchmark（公開API、HU NLHE、AIVAT）は将来modelの強さ測定の標準環境として有望。Phase2評価の選択肢として記録（今すぐでない）。同論文もPokerBench母集団の偏り（手選び・全範囲非網羅）を指摘し§80と整合。
- **結論への含意**: 既製の答え（混合分布）を買って/拾ってソルバーを省く道は、公式合法・大量取得の条件を満たさず却下。残るは外部レンジ（重み付き）を入手し手元Rust Solverで自前計算する路線。レンジは無料チャート（rangeconverter/hand2note等、混合頻度ありだが50%/25%丸めで粗い）か有料パック（Monker $139等、高精度・エクスポート可、ただしrake前提要確認）。当初ユーザー方針＝無料で小規模試作→必要なら有料、に回帰。
- **無料レンジの具体的所在（別エージェント実測）**: AHTOOOXA/poker-charts（MIT、`src/data/ranges/greenline.ts`=GreenCharts2024由来・`pekarstas.ts`=GGPoker chart pack、TS object、6ポジション、RFI/vs-open/vs-3bet/vs-4bet、ただし実データ0/50/100中心）、tyloo/poker-range-analyzer（MIT、`lib/ranges/*.ts`、explicit frequency `{raise,call,fold}`だが25%刻み・主にopening）。raw URL=`raw.githubusercontent.com/AHTOOOXA/poker-charts/main/src/data/ranges/{greenline,pekarstas}.ts`。BTN-RFI 3ソース相互整合あり（標準的GTOだが丸め）。出所(solver/rake/stack)はいずれも不明。

### 2.7 教師生成パイプライン動作確認試作と方針転換（river30件、★最重要）
**配管=Go（技術的に一周成功）**。raw取得7/7（MIT保存）、`evaluation_at=="River"`先頭30件対象、ライン取得不能0/30、Solver投入16件全成功。確率和検証16/16（最大誤差3.88e-08）、exploitability<=0.6%目標16/16（min0.26%/median0.47%/max0.55%）、wall 2.1-10.1ms、Solver mem 195K-291K bytes。橋渡し不具合（river treeにflop全履歴を投入していた、chance card消失）を実測発見し修正（現在はriver開始pot/effective stackとriver内actionのみ投入）。再現: `data/teacher_proto/run_river_proto.py`、`data/teacher_proto/river30/{requests,responses,manifest.json}`。実物例ID0: HJ/2.0bb/BB/call, AKo, river pot=2100/eff=8900/rake0%, dist=Check0.0028/Bet1260 0.9972/AllIn0, exploitability0.326%。

**しかしhero包含14/30=46.67%脱落（核心の実測値）**。
- **原因切り分け=全14件が分類B**（キーは存在しheroの手もレンジにあるが、その手のactionがPokerBenchと食い違う）。分類A（キー欠落）=0件、分類C（対応づけ/aliasミス）=0件。
- 決定的な実例: ID3 hero=JTs（PokerBenchではBTNがコールした局面）だが、無料チャートは`JTs=raise`と定義→「コールレンジ」にJTsが入らずweight0脱落。同様にID35 ATs、ID36 KQs、ID56 AQo等も無料チャートでraise定義のためコール局面で脱落。
- **回復見込み**: 純粋な対応づけ修正で回復するC件=**0件**。pekarstasに統一すればBBコール4件（ID6,9,12,24）回復で上限66.67%、ただし既存16件への副作用未検証。残り10件は別レンジ源or局面除外が必要。HJ→MP aliasは妥当な近似だが同一レンジ保証なし。仮説1（BTNコールの構造的欠落）は否定（キーは存在した）。
- **最終結論（ユーザー決定）**: 借り物レンジ（無料/有料問わず）でPokerBench局面を再現すること自体に構造的な無理がある。PokerBench元レンジが非公開ゆえ、どの外部レンジを当てても「同じ手の打ち方の食い違い」が必ず出る。レンジ源を替えても上限66.67%が示す通り、品質（丸め）の問題ではなくレンジ定義の不整合が本質。**→PokerBench 50万局面の再利用路線を放棄。局面もレンジも自前で一貫生成し、レンジと局面を定義上一致させる（hero包含失敗が原理的に起きない）方向へ転換**。
- **有効に残るもの**: 配管（無料/任意レンジ→`AA:0.5`変換→Rust Solver→strategy_matrix→hero行抽出→確率和/exploitability検算）が技術的に動くことは実証済み。橋渡し修正（river開始状態のみ投入）も有効。自前生成に転換しても、この配管はそのまま使える。揺らいだのは「PokerBench局面を使う」前提であって「パイプラインが動く」ことではない。

### 2.8 外部調達の総当たり調査と正本レンジ方針の確定（自前生成の入力レンジを巡る）
方針転換後、「自前生成の正本レンジを何にするか」を巡り外部ソルバー/レンジ/完成データを広く実物調査。結論=**エクスポート可能な無料レンジはAHTOOOXA等のみ。当面これ（丸めあり）で自前生成を通し強さを実測してから高精度投資を判断**（ユーザー決定）。
- **種類A（完成教師データ=局面+混合分布セット）=存在せず**（別エージェント+Commander実物確認）。GitHub/HuggingFace/Kaggleは玩具データ・単一アクションラベル・教育テキストに偏在。商用（RangeConverter postflopは閲覧専用でbulk DL不可、FlushRoyal/PokerReaderのGTO+ files・Modern Poker Theory bonus sims等は実ファイル/ライセンス未確認の灰色）。混合分布の完成データを機械可読・大量・明確ライセンスで入手する道は無い。
- **種類B（高品質レンジ）=有料に有力候補あり、ただし素性/要件に留保**。最有力=**FreeBetRange "Original GTO"**（公式ヘルプで「ソルバー未加工出力・元の混合頻度（例37%raise/63%call）・PioSOLVER形式export・HRC計算・rake構造/stakes/テーブルサイズ/anteフィルタ」確認、丸めなし）。**料金（公式実測）=Pro年$159(月$13.2)/Elite年$348(月$29)、ライフタイム無し、返金ポリシーあり**。重み付きアクセスは最低Pro要、大量exportはElite要の可能性（月Elite=月100export上限/年Elite=無制限）。**ただし大量export可否・6max100bbの必要ライン×OOP/IPカバー・実ファイルが手元ソルバー読込可かは契約前未確認、ユーザーは素性に不安**。次点=RangeConverter（preflop DL $99〜会員/$198-398非会員、6-max/HU・100bb・rake別・Pio/Monker形式、ただし無料PDFは50%丸め、有料DL物の小数精度は未確認）。準候補=Deepsolver（CSV/RNG export、solver名・丸め未確認）。
- **自前solve路線の精査=ハードウェアの壁**。**MonkerSolver無料版はturn/river限定でpreflop非対応**（手元Rustと同守備範囲、preflop調達にならない）。preflopを自前solveするにはMonkerフル版€499 or PioSOLVER Edge $475-549 or MonkerGuy完成レンジ購入が必要。preflopソルバーは重く（200GB RAM要のことも、Pio機でpreflop2時間でHW error）、**手元RAM 31.8GBでは不足、クラウド費用が撤退基準$500を超えうる**。flop速度の壁（§3.3、flop20秒）はソルバーを替えても残る。Simple Preflop=年$250サブスク。
- **GTONexus（無料ソルバー）の実機確認→脱落**。Reddit r/poker発（投稿8か月前、開発者2名=Amazon/TikTokエンジニア、大学時代の趣味プロジェクト、完全無料・サインアップ不要）。HU/6max/9max対応、preflopレンジビューア+postflopソルバー+トレーナー、**0.3% exploitability自己申告でPio/GTO+/WASMと同等**（第三者tombos21も「同じCFRアルゴリズムなので有料同等のはず」と支持）、有料アドオン$8.50/月（AI解説/MTT）。クローズドソース（AWSクラウド依存で公開不可）、**無署名でWindows Defender警告**（コード署名未登録が原因、トロイ警告の実例報告も別ユーザーあり）。**実機インストールして確認→エクスポート不可と判明**（Reddit上でもユーザーが「solve保存されない」と複数質問）。教師データ大量取得に使えず**脱落**。
- **正本レンジ方針の確定（ユーザー決定）**: 上記より、エクスポート可能・無料・MIT・機械可読なレンジは**AHTOOOXA/poker-charts等のみ**（§2.6末で実物確認済み、丸めあり0/50/100や25%刻み・出所不明だが相互整合あり）。**当面これを正本にして自前生成を一通り通し（SFT→GRPO）、モデルの強さを実測してから、足りなければ高精度レンジ（FreeBetRange Pro年$159等）への投資を判断する**。理由=丸めが実際どれだけ強さに響くかは机上で決まらず実測が要る、これまでの「実物で確かめてから投資」姿勢と一貫。
- **丸めの影響の正確な切り分け**: 自前生成では「レンジから局面を作る」ので、§2.7の46.67%脱落（食い違い）は**原理的に起きない**（食い違いの原因は丸めでなくPokerBenchとの定義不一致だった）。丸めは「破綻させない」が「精度は粗くする」（粗いレンジ入力→出力混合分布も粗い→モデル強さに上限）。よって「動く（破綻しない）＝OK、最高品質か＝否、まず通して実測」が正しい切り分け。

---

## 3. ステップ3のRust Solver実測（教師源の制約。最重要）

### 3.1 資産状態
- CLI: `C:/Users/user/Desktop/dev/poker-system/solver/bin/postflop_cli.exe`（実行可能を実測確認）
- wrapper: `solver/postflop_cli/src/main.rs`、local crate `postflop-solver-local`
- upstream clone: `C:\Users\user\Desktop\dev\poker\postflop-solver` HEAD `9d1509fe...`
- `rustc/cargo 1.95.0`。**CLI SHA256（公式設定対応版）`67A520B1...2944228F`**（本セッションでdonk 2フィールド追加・全13 TreeConfigフィールド接続。旧`DE330C6D...`→16bit対応`C048B54D...`→donk対応`67A520B1...`）。ビルド契約 `solver/README.md`。solver library本体は無変更（AGPL-3.0）。

### 3.2 入出力契約（main.rs、本セッションで全13 TreeConfigフィールド接続）
- 入力（全13 TreeConfig対応）: `board, range_oop, range_ip, starting_pot, effective_stack, flop/turn/river別bet_sizes(oop/ip), flop/turn/river別raise_sizes(oop/ip), turn/river_donk_sizes(Option、本セッション追加), add_allin_threshold, force_allin_threshold, merging_threshold, rake_rate, rake_cap, enable_compression(Option), max_iterations, target_exploitability_pct, actions_played(Option)`。
- **bet_sizesとraise_sizesは分離**（`BetSizeOptions::try_from((bet_str, raise_str))`）。bet記法: %（pot比）/x（前ベット倍数・raise専用）/c（定数）/**e（geometric=残りstreet数からall-in到達サイズを自動計算）**/a（all-in）。例 `"60%, e, a"`+raise`"2.5x"`。
- 出力 `RootStrategy`: `actions, hands, strategy_matrix(hand×action), equity, ev, average_strategy, memory_uncompressed, memory_compressed`。**hand別・sizing別の混合分布を出せる**。merging後の実効actionが返る。

### 3.3 HU実測（★公式設定での全street実測。本セッション、SRP_BTN_vs_BB pekarstas到達レンジ OOP77/IP96、starting_pot5.5・eff97.5＝100bb系）
**公式設定**: 全street bet=`60%, e, a` / raise=`2.5x` / merging=0.1 / add_allin=1.5 / force_allin=0.15 / 32-bit。
- **river**: wall約40ms / mem 0.3MB / exploitability 0.5%達成（5/5）。実効action `Check / Bet / AllIn`。→**実用的**。
- **turn**: wall約1.2秒 / mem 57MB / exploitability 0.5%達成（5/5）。→**実用的**。
- **flop**: mem **9-16GBで収束**（メモリ問題解決）。0.5%目標は3ボードすべて30分内到達、**平均411秒/件（6.8分、最大10.5分）**。1%目標なら平均211秒/件（約半分）。**flop 0.5% vs 1%の分布差は未測定**（次セッションで判断、1%で済めば生成時間半減）。
- **★旧記録の訂正（§82.2）**: 前セッションの「flop 20秒・1.21%・1.47GB」（旧設定`60%,a`単一・raise/merging/threshold/geometric未使用）と「自前テストでflop 26GB crash＝手元では解けない」は、いずれも**設定不備が真因**と判明。公式設定（merging/threshold/geometric）で9-16GBに収まる。bet sizesの数だけが原因ではなかった。bunchingは元からOFF（ON は逆にメモリ増）。
- **却下事項（§82.4-82.5）**: 16-bit圧縮=river実測で混合分布最大乖離0.36（Check 98%→62%）でNo-go。並列=同時2件はRAM可（合計19GB）だが同一timeoutでiteration減りexploitability悪化（同一品質では不可、timeout延長で1.95倍の可能性・未実測）。低品質解4-9%=混合分布最大0.73-0.79変動（Check 0.20→0.93反転）で教師不可。0.5%以下が必須。

### 3.4 統一schema突合（Rust側で外部供給が要るもの）
ソルバーが直接出す: board/street, stacks/pot(変換要), legal/call/raise範囲, action abstraction, 混合分布, exploitability。
**ソルバーに入れる前にこちらが用意**: range_oop/range_ip（**必須入力**）, 完全action履歴の`actions_played`変換, rake設定。
**ソルバー外から供給が要る**: payoff目的, equilibrium選択規約（DESIGN_NOTES §81.9の未解決と一致）。
→「Rust Solverデータで訓練すればmodelに渡す情報が全部揃う」わけではない。分布と局面情報の大半はソルバーが出すが、**レンジ供給・action履歴変換・payoff目的/abstraction規約は別途確定が必要**。

### 3.5 ソルバー本体の素性（github.com/b-inary/postflop-solver、README実物確認）
手元`postflop-solver-local`のupstream本家。HEAD`9d1509fe`。
- **postflop（flop/turn/river）専用。preflopは解かない**（READMEの対象がturn/river deal、製品もpreflop→GTOチャート分離と一致）。ただし入力にレンジ（=preflopの結果として各playerがpostflop局面に到達した重み付きレンジ）が必須。
- アルゴリズム=Discounted CFR（γ=3.0、iteration 4のべき乗でcumulative strategy reset）。
- **abstractionを内部で行わない**＝こちらが与えたbet sizeで解く（action abstractionをこちらで固定する必要、宿題4）。isomorphism（turn/river同型）は結合。
- **bunching effect**: 最大4 folded player対応（6-max）、降りた手札を正しくcombo計数。**有効化すると終端ノード計算が大幅減速**。**※本セッションで実測: bunchingは元からOFF（CLI読み捨て）。flop の重さはbunchingではなく設定不備（merging/threshold/geometric未使用）が真因と判明（§82.2）。bunching ON は逆にメモリ増。**
- 精度32bit float（compression option=16bit int）。**※16-bit圧縮は本セッションで却下（river実測で混合分布最大乖離0.36、§82.4）。** multithread（rayon）デフォルト有効（単一solveで全16論理コアをほぼ使用、§82.5）。
- **ライセンス=AGPL-3.0**（製品組込時に注意が要る論点）。2023年10月で開発停止（business solver開発のため）。WASM/Desktop GUIのbackend用途が主目的、library直接利用は設計上の主目的でなくbreaking change頻繁。
- 重み付きレンジ`AA:0.5`形式を正式対応（`src/range.rs`）。

---

## 4. 根本原因の層構造（7層、§71-79。§80で評価基盤欠陥が判明し再評価対象に）

1. reward_fn action無視(`2142101`): 解消。 2. 局面分布偏り→curriculum。 3. curriculum相手holeバグ(`9199ca9`): 解消。 4. 教材飽和(`562039a`): 解消。 5. curriculum密度不足(`4f79794`): density2で解消。 6. made_hand⇄preflopトレードオフ(§73): 第2次step6500 HALT。 7. **報酬関数のpreflop open過小評価**(§74-79、3サブ層): 7-1強hand忘却→fold equity(`b026dbb`§75)、7-2 trash誤判別→強度依存fold(`91d098c`§76)、7-3 late-position維持→reference KL/報酬3成分は不適(§78)、真因=素材の穴→late curriculum(`c9dae39`§79)で9割解消。
**※§80注記**: §70-79のSpot Checks由来数値（pass率・生確率・A5s/A9s/Q9s・made_hand 7/8）は欠陥評価基盤上の値で再評価要。crash対策3層(Task16-18)とrun_6c_3完走の運用事実は有効。

---

## 5. 残課題

### 5.1 ★最優先: 混合戦略を狙う土台再設計（SFTからやり直し、§80.6-80.10）

**(A) 設計確定フェーズ ステップ1-3＋後半調査 = 完了（本セッション）**
- 真値源確定（§2.1）／統一schema文書化（§2.2）／教師源=Rust Solver確定（§2.3,§3）／ユーザー案=(b)成立・postflop 50万再利用可（§2.4）／ソルバー素性確認（§3.5）／レンジ供給源=(c)外部調達必須（§2.5）。

**(B) 設計確定の残り論点 = 次にやること**
1. **★教師データの自前生成設計（方針転換後の最優先・正本レンジ確定）**: PokerBench局面の再利用は構造的に不可（§2.7）。→局面もレンジも自前で一貫生成。**正本レンジ=当面AHTOOOXA等の無料・MIT・機械可読レンジに確定**（外部総当たり調査でエクスポート可能な無料はこれのみ、§2.8）。丸めは破綻させないが精度は粗い→まず通して強さ実測→足りなければ高精度（有料）投資。詰めるべき設計: (a)局面生成（レンジから手札サンプル→プリフロップツリー→ボード配布→postflop局面）、(b)AHTOOOXAレンジの取得・パース・正本化、(c)均衡選択規約（複数均衡の正解規約、§3.4/§80.8未確定）、(d)hero包含が原理的に起きない構造の確認、(e)必要枚数・カバレッジ。配管（レンジ→Solver→分布→検算）は実証済み（§2.7）。
2. **flop生成速度（宿題2、未着手）**: flop 1局面20秒・品質1.21%未達。原因切り分け（bunching effect有効化/iteration/bet size menu/deep-SPRのどれが効くか、§3.5でbunchingが容疑）。iteration/exploitability妥協点、river中心化（River 26万/Turn 20.7万/Flop 3.2万の内訳活用）、並列(2-4)・キャッシュ。
3. **必要枚数（宿題3の一部、未着手）**: 「1万」は速度試算用の仮値。必要カバレッジ×flop速度で決める。postflop 50万が再利用可能な母集団（§2.4）。
4. **action abstraction / equilibrium選択規約（宿題4、未着手）**: ソルバーがabstraction内部で行わない（§3.5）ため、street/position別bet sizesをこちらで固定。複数均衡時の選択規約も。DESIGN_NOTES §81.9の未解決と一致。
5. **ライセンス（新規論点）**: ソルバーAGPL-3.0（§3.5）。教師データ生成に使う場合と製品組込の場合のライセンス影響を確認。

**(C) 再構築（設計確定後、未着手）**: 完全情報prompt・統一生成器の上でSFTやり直し→GRPO。preflop学習を確実化、shuffle/offset問題解消、all-in教師を検証可能化。

**(D) 残る全系監査（§80.10、順次）**: ステップ1で計算器真値源を監査済み。報酬計算・state構築・確率取り出しを同深度で監査し検算可能な形で健全性担保。「一つ欠陥が出れば同種が他にもある」前提。

### 5.2 Q9s(最薄late-open)・SB K2o過多・spot_040（製品評価後に判断、§79.3）
Q9s最薄late-openはFAIL継続、近傍教材強化はoverfitリスク。SB K2o過多(spot_004)はtrash過剰openで別系統。spot_040(8/8鍵)は別軸。※いずれも§80で評価基盤が再評価対象になったため、新評価基盤確定後に再測定。

### 5.3 all_in head死亡（別軸、未解決、§78.4同系統）
生all_in確率低下、curriculum all_in 1件のみ＝素材不足。対策(late+made_hand安定後): generation_temperature/all_in候補強制サンプル/all_in教材追加。

### 5.4 PokerKit極小負crash（§77.4）
self-play chips pushingで unraked -2.13e-14 crash。crash対策3層(Task16-18, `c6c460c`/`dda8275`/`2c0f744`)で本訓練run_6c_3は完走実績あり。SFTやり直し後の新GRPOでも防御維持。

### 5.5 剪定候補（§78.5）
terminal帰属構想(§67)追わない。未使用chip_delta/bankroll weight整理検討。reference KL機構(`095ae32`)害なく残置可。

---

## 6. Go/No-go・撤退・再設計（§57/§58.1/§56.6/§78.5/§80）

### 6.1 旧baseline = 43/50 = 0.86。**※§80で評価基盤(canonical 50)に構造欠陥が判明、この値は独立GTO真値でなく再評価対象。新評価基盤（HU postflopに母集団を絞った確率分布評価）の確定が(B)論点に含まれる。**
### 6.2 旧Phase2合格線: Spot Checks≥95%/Sensitivity≥90%/PokerBench preflop≥75%postflop≥60%/Slumbot≥-15bb/100/self-play vs Phase1≥+3bb/100。「profit vs random」単独禁止(#11)。**※評価指標自体が§80で見直し対象。0.5閾値→分布間距離等へ（§80.8-3）。canonical 50は診断用に残すが主指標にしない（#11は「削除」でなく「正しい母集団選択」と整理、§80.9）。**
### 6.3 撤退基準(§56.6): タイムボックス12週(最大15週)/品質下限/改善トレンド消失/$500。**未抵触**。ただしSFTやり直しは重い決定（§80.7、§21が想定する最後の手段に近い）でユーザー明示決定に基づく。
### 6.4 再設計判断ライン(§78.5/§80.9): §80のSFTやり直しは却下済み対処の再試行ではなく、評価基盤欠陥という新事実に基づく土台是正。#21禁止対象（報酬3成分復活・reference KL再試行・position prior）とは別。

---

## 7. 起動・監視運用知見（既出、次回も有効）

- **`step=...` はsummaryのみ**。走行中は `eval_step=...` と checkpoint(`latest/trainer_state.json`)。
- **checkpoint guard**: `results/grpo/` 配下のみ。trial dirも `results\grpo\<name>`。
- **PokerKit極小負crash(§5.4/§77.4)**: self-play chips pushingで -2.13e-14 crash。crash対策3層で完走実績。
- プロセス生死 `nvidia-smi`、ハング `py-spy dump`。ログ `*> file`。末尾`SMOKE: PASS`なら正常。
- **品質ガードHALT**: pass_rate<floor 0.75でHALT+last_good保存(§70.3)。
- VRAM: density 2で約4.5-4.7GB。本訓練中はGPUアプリ閉じる(#19)。
- バックアップ: `backups/6c-run`へrobocopyループ。
- **resume注意**: 新規訓練は`--resume-from`付けずstep1から。**未学習/崩壊/未達checkpointからは再開しない(#20)**。SFTやり直し後は旧checkpoint(第2次step6500/各trial)から再開しない。

---

## 8. 確定した制約

1. `pokerkit==0.7.4`死守。PokerKit本体改変禁止(防御は呼び出し側§71.4)。
2. prompt生成器・環境は6-max固定（訓練環境。製品適用はHU postflop=SPEC §9.3/§10A）。
3. verifyは形式整合案。
4. 正本モデル成果物read-only。訓練checkpointは`results/grpo/`。
5. state正本`state_factory.py`。複製しない。curriculum stateもこれで生成（preflop局面はfold列で構築可、§79.1）。
6. ハイパラは各Config経由。ハードコード禁止（playouts/curriculum-ratio/groups-per-step/microbatch-groups/fold確率テーブル/reference_kl_coef/curriculum_category_slots等）。
7. 報酬EVはMCのみ。CFR/solver/反実仮想なし(§65.1/§67.4)。fold equity・強度依存fold確率も枠内(fold-equity prior)。reference KLは損失正則化。報酬3成分のGRPO候補報酬接続は不適と確定、追わない(§78.3)。**※§80.9注記: solverから混合戦略教師を得る場合、これは「教師データ生成レイヤー」であり「報酬計算レイヤー」とは別。#7は報酬計算レイヤーの制約。両レイヤーを混同しない。**
8. GRPO最適化対象=action head categorical(§66)。sizingは方策勾配外(detach)。
9. group=decision-state単位、group内正規化(§67)。curriculum slotも1 scenario=1 state=1 group。
10. entropy崩壊対策なしに長時間訓練しない。健全判定はtop1≤0.85単独でない(§70.3)。品質ガード(floor 0.75/カテゴリfail_rate)有効に保つ。
11. 「profit vs random」単独評価禁止。Spot Checks 50を削除・緩和しない・訓練に混ぜない。`verify_pokerrl_encode.py`スキップ禁止。**※§80.9注記: canonical 50は診断用に残す。Go/No-go母集団の見直しは「緩和」でなく「正しい母集団選択・評価指標是正」（§80.8-3）。削除はしない。**
12. PokerRL品質検証(Stage D)前にDeep CFR/Rust Solverを削除しない。**※§80.9注記: 両者は混合戦略教師の供給源候補として重要性が増した。特にRust Solverは教師源確定（§2.3）。**
13. docs一元管理(`...\poker-system\docs`、main)。訓練リポジトリに置かない。SPEC.mdは報酬・curriculum設計を扱わない(§78.2)。
14. 実装指令書v1.3は2026-06-04廃止。
15. 自己対戦のpot/サイドポット/death SBはPokerKit automation委譲。fold equityの自前pot計算はサイドポット非対応、postflop有効化時は委譲必須(§75.1)。
16. 候補r_i評価はstate非破壊(deepcopy)。
17. opponentは凍結ベース共有+PEFT multi-adapter。reference aux headも凍結共有。
18. top1中央値≤0.85を最適化標的にしない(§70.4)。
19. 本訓練中はGPU使用アプリを開かない(VRAM、#19)。
20. resume運用: 停止時`--resume-from results\grpo\latest`。未学習/崩壊/未達checkpointからは再開しない。
21. 全体再設計は最後の手段(§78.5)。報酬3成分復活・reference KLのlate-position再試行・position考慮priorは却下済み、再試行しない。残課題は素材補充(curriculum)で対処(§79実証)。**※§80はこの#21の禁止対象とは別の、評価基盤欠陥に基づく土台是正（§80.9）。**
22. **正本レンジ=pekarstas単一**（AHTOOOXA/poker-charts、MIT）。greenlineと混在させない（§80土台整合性）。46ライン169クラス展開、HU postflopカバレッジ39/45=86.7%。欠落6種はMP起点CO/BTN応答（実戦頻度低・許容）。パース済`data/ranges/pekarstas_parsed.json`。
23. **solver公式設定**: 全street bet=`60%, e, a`/raise=`2.5x`/merging_threshold=0.1/add_allin_threshold=1.5/force_allin_threshold=0.15/32-bit（圧縮無効）。この設定でflop 9-16GB収束（§82.3）。**16-bit圧縮禁止**（教師品質不可、§82.4）。
24. **flop教師のexploitability下限=0.5%**（turn/riverと同基準）。低品質解4-9%は混合分布が最大0.79変動し教師不可（§82.5）。0.5% vs 1%の許容判断は次セッションの実測待ち。
25. **同一品質での並列flop solve禁止**（同一timeoutでiteration減少しexploitability悪化、§82.5）。timeout延長での並列は未実測。solver単一実行で全コア使用済み。

---

## 8.5 技術参照（次の設計・実装で必要。粒度維持のため削らない）

### 統一prompt計算器の真値源（ステップ1実測、§2.1）
- state正本 `state_factory.py`: `NoLimitTexasHoldem.create_state`、8 automations(ANTE/BET_COLLECTION/BLIND/CARD_BURNING/HOLE_SHOW_MUCK/HAND_KILLING/CHIPS_PUSHING/CHIPS_PULLING)、blinds(0.5,1)、min_bet=1、starting 100、player_count 6、ante_trimming=True、raw_antes=0。death SB専用automationなし（blind posting+fold資格喪失+collection+pushing/pullingの組合せ）。
- 真値源マップ: stack=`state.stacks[i]`／street bet=`state.bets[i]`／累積拠出=`-state.payoffs[i]`(decision snapshot限定)／pot内訳=`tuple(state.pots)`(先頭main)／資格=`Pot.player_indices`／call=`state.checking_or_calling_amount`／raise範囲=`state.min/max_completion_betting_or_raising_to_amount`／rake=`state.rake`,`Pot.raked/unraked_amount`／gross pot=`state.total_pot_amount`。
- 導出定義はDESIGN_NOTES §81.4を正とする（過去street別拠出のoperations走査／common pot 2式／effective behind=min/SPR分母=playable_unraked_pot/pot odds multiway分母=Σmin(C'_i,H)）。
- **実装時の必須確認（Commander申し送り）**: (a)`-payoffs`累積拠出を複数street decisionで再実測、(b)PokerKit属性名(`street_index`等)の実在を実測確認してから実装、(c)`_prompt_pot_amount`の自前集計を新計算器で使わない。

### Rust Solver（教師源、本セッションで公式設定実測、§3・§82）
- CLI `solver/bin/postflop_cli.exe`（**公式設定対応版 SHA `67A520B1...2944228F`**）、wrapper `solver/postflop_cli/src/main.rs`、crate `postflop-solver-local`、upstream `...\dev\poker\postflop-solver` HEAD`9d1509fe`、rustc/cargo 1.95.0。
- 入力（全13 TreeConfig対応）: board/range_oop/range_ip/starting_pot/effective_stack/street別bet_sizes(oop/ip)/street別raise_sizes(oop/ip)/turn・river_donk_sizes(Option)/add_allin_threshold/force_allin_threshold/merging_threshold/rake_rate/rake_cap/enable_compression(Option)/max_iterations/target_exploitability_pct/actions_played。
- **公式設定（制約#23）**: bet=`60%, e, a`/raise=`2.5x`/merging=0.1/add_allin=1.5/force_allin=0.15/32-bit。bet/raise分離、geometric `e`は残りstreet数からall-in到達サイズ自動計算。
- 出力RootStrategy: actions/hands/strategy_matrix/equity/ev/average_strategy/memory_uncompressed/memory_compressed。
- 実測（§3.3）: river約40ms / turn約1.2秒 / flop 9-16GB・0.5%到達平均411秒/件。必須外部入力=range_oop/range_ip。
- **素性（README §3.5、§82.1）**: postflop専用（preflop不可）、Discounted CFR（γ=3.0）、abstraction内部で行わず与えたbet sizeで解く、bunching effect（元からOFF、ONは減速・メモリ増）、**AGPL-3.0**、2023/10開発停止、重み`AA:0.5`正式対応（`src/range.rs`）。公式doc=b-inary.github.io/postflop_solver。

### 自前生成パイプライン成果物（本セッション、`data/ranges/`配下・gitignore対象）
- **pekarstasパース**: `parse_pekarstas.py`→`pekarstas_parsed.json`（46ライン、各169クラス、`raise+call+fold==1.0`検算済、キーは`-`→`_`正規化・原キーも保持）。`-`正規化、`allin`は`raise`統合。
- **HUカバレッジ**: `hu_postflop_coverage.py`→`hu_postflop_coverage_report.json`（45シナリオ=SRP15+3BET15+4BET15、covered39/partial2/uncovered4、欠落はMP起点CO/BTN応答）。
- **到達レンジ**: `reach_ranges.py`（SRP/3BET/4BET積算。OOP/IPはseat順 SB0<BB1<UTG2<HJ3<CO4<BTN5で小さい方OOP。欠落ラインはKeyError停止）。SRP_BTN_vs_BBで OOP77/IP96クラス。
- **river30件E2E実証**: `generate_river_proto.py`→`river_proto/`（30/30成功、exploitability median0.44%、28,170教師候補、コミット`c144392`）。
- **flop計画調査**: `flop_planning_study.json`（並列・必要数試算・品質差）、`flop_05_target_study.json`（0.5%到達411秒/件）、`official_settings_test.json`（全street公式設定実測）、`river_comparison.json`（16bit却下根拠）。**いずれも未コミット**（pekarstas成果物本体は`c144392`でコミット済）。
- 1ボードあたり教師例数（公式設定実測、hand単位）: flop約1038 / turn約967 / river約928（OOP+IP合計）。
- **教師数の単位（制約・§82.6）**: 旧SFT=563,200件は局面単位（1局面=単一正解action）。新方式=hand単位（1ボード約1000例）。**直接比較不可。必要総教師例数は§80.8 SFT設計と照合して次セッション確定。**

### PokerBenchデータ所在（§2.4実測、教師局面の母集団）
- postflop train `data/pokerbench/postflop_500k_train_set_game_scenario_information.csv`＋`...prompt_and_label.json`=**500,000件**（Flop 32,428/Turn 207,153/River 260,419）。preflop train `preflop_60k_*`=63,200件（postflop Solver適用外）。SFT前処理後`data/sft_train_full.jsonl`=563,200件。test=postflop10k/preflop1k。
- CSV列=preflop_action/board_flop/turn/river/aggressor_position/postflop_action/evaluation_at/available_moves/pot_size/hero_position/holding/correct_decision。JSON=instruction＋output(単一正解)のみ、ID無し（JSON先頭=CSV ID70990、単純row join不可）。
- 生成順 `scripts/prepare_sft_full.py:103`、segment削除 `run_sft_sequential.py:119`。
- 教師再生成の正本ID=`dataset_version+source_sha256+split+csv_row_id`、manifest管理、成功件数をoffsetにしない（§80.4の轍）。

### レンジ資産（§2.5実測、全て教師真値レンジには不適合＝外部調達要）
- `strategy/baseline_ranges.json`（pot分類4・非重み付き・500k適用で23.3%range外）／`preflop_charts/6max_gto.json`（133 leaf・weight0件・HJ無し・35.35%range外）／`strategy/llm_pipeline.py`のrange_estimation（LLM推定・weight非要求・hero非包含・GTO照合なし、本番未使用）／`debug/solver_io/*`過去ログ（baseline複製）。
- hero包含問題: 過去にhero実カードがrange_oop外で解けず（`solver_parse_audit`）。外部range導入後はhero combo `weight>0`のみ教師対象、人工追加不可。
- Rust側重み形式 `postflop-solver-local/src/range.rs:16`（`AA:0.5`）。

### Deep CFR（教師には不可、フォールバック用に保持#12）
- repo `C:\dev\deepcfr-training` HEAD`1067168`、checkpoint `models/phase3_v4/mixed_checkpoint_iter_10000.pt`＝製品`poker-system/models/deep_cfr/best_checkpoint.pt`(SHA一致`0165BFB7...`)。Python3.11.9/torch2.5.1+cu121。
- model.py: 156次元encoding(`52+52+5+1+num_players*...`)、num_actions=3、sizing連続0.1-3.0。bridge `deep_cfr_bridge.py:377`がraw logits→softmax。既存監査で9局面中合理1のみ・raise偏重（DESIGN_NOTES:3131）。

### GRPO装置の主要API / 初期化点（既存、SFTやり直し後に置換予定）
- 初期化点: LoRA `results/sft_sequential/seg_003_offset_66000/final_adapter` + Heads `results/aux_heads/seg_003/final_aux_head/aux_heads.pt`。base+LoRA凍結、aux head trainable=3,149,317。※SFTやり直しでこの初期化点は刷新される見込み。
- 収集 `collect.collect_trajectories`。グループ `grpo_batch.build_decision_groups`(L76-91、1 StepRecord=1 decision-state、group_size=8再サンプル)。advantage `advantage.group_relative_advantages`(L23-30、group内mean/std)。
- 損失 `grpo_loss.grpo_loss`→`(total,metrics)`。sizingは方策勾配外(detach)。KL2系統: `kl`(old policy,kl_coef,L120)+`reference_kl`(初期SFT係留,reference_kl_coef,`095ae32`§78.1)。
- 報酬 `train_harness.action_conditioned_reward`→`reward.step_reward`=rollout EVのみ(reward.py L72-85)。terminal_reward(chip_delta0.7+bankroll0.1)はGRPO候補報酬未接続(§78.3)。
- fold equity(`b026dbb`→強度依存`91d098c`§75-76): `rollout_ev(...preflop_opponent_fold_probability/preflop_fold_probability_by_strength/postflop_opponent_fold_probability)`。
- late curriculum(`c9dae39`§79): `scenarios.json`に`preflop_late_open`5件、`--curriculum-category-slots made_hand,preflop_late_open`。
- ハーネス `train_harness.GRPOTrainer`/`TrainConfig`。監視 `monitor.EntropyMonitor`/`collapse_guard`。品質 `quality_gate.spot_check_gate`(floor0.75/regression_tol0.10)。
- Spot Checks `spot_checks.run_spot_checks`+`scenarios.json`(50、評価専用#11)。**※§80で母集団見直し対象。**

### Config既定値（着手前に各定義を `view` で再確認）
- `RewardConfig`: weight_chip_delta=0.7/weight_rollout_ev=0.2/weight_bankroll=0.1/rollout_playouts=100/clip_bb=100.0/bankroll_window=20/rollout_preflop_opponent_fold_probability=0.7/rollout_preflop_fold_probability_by_strength/rollout_postflop_opponent_fold_probability=0.0。
- `GRPOConfig`: group_size=8/eps_low=0.2/eps_high=0.28/kl_coef=0.0/reference_kl_coef=0.0/entropy_bonus_coef=0.0/sizing_loss_coef=0.1/generation_temperature=1.0。LR=1e-5。
- 健全性テスト: `pytest -q`全体はcollection errorで停止→個別指定(§10.1)。`verify_pokerrl_encode` passed=8/PASS。

### PokerKit / state正本 / prompt出力契約
- `pokerkit==0.7.4`。state正本`state_factory.py`(上記)。6-max index `{0:SB,1:BB,2:UTG,3:HJ,4:CO,5:BTN}`。position=`pokerbench_prompt.position_for_index`。
- prompt `pokerbench_prompt.build_pokerbench_prompt`。hole`"of"`/board`"Of"`、preflop raise額`str(amount)`、pot`f"{amount:.1f}"`、pot取得`safe_total_pot_amount`(FLOAT_POT_ZERO_EPSILON=1e-6)。**※統一schema導入で出力契約は刷新予定（DESIGN_NOTES §81）。**
- git: 訓練repo`master`(**HEAD`c144392`**＝本セッション)、docs`main`。`data/``results/``backups/`はgitignore。

### docs設計文書
- **DESIGN_NOTES §81（5286行〜、本セッションで一元化、コミット`bb8c972`）**: 統一prompt計算器のschema・定義契約の正本。SFTやり直しの実装はこれに従う。旧独立ファイル`unified_prompt_schema.md`は§81へ全文移植し廃止（逆変換後SHA-256一致で無改変確認）。

---

## 9. 主要コミット（時系列）

- 訓練`master`: 〜`4f79794`(密度)/`42b2c27`(microbatch)/`b026dbb`(fold equity§75)/`91d098c`(強度依存§76)/`095ae32`(reference KL§78.1)/`c9dae39`(late curriculum§79)/crash対策3層`c6c460c`(amount入口丸め)/`dda8275`(境界クランプ)/`2c0f744`(collect層catch)/**`c144392`(pekarstasパーサ+HUカバレッジ+到達レンジ計算+river30件E2E=現HEAD、本セッション)**。
- docs`main`: §71-79追記済み(`118cc5a`〜`17f78bc`)、§80・§81(`bb8c972`)。**§82=solver完全理解・公式設定・flop品質（本セッション、`docs: add §82...`でコミット予定または済）。**
- **solver側(poker-system)**: main.rsにdonk 2フィールド追加・16bit対応（SHA`67A520B1...`）。**未コミット**（結果確認後に判断、コミットメッセージ案`solver: full TreeConfig parameter support`）。

---

## 10. 次セッション開始手順

着手対象: **生成計画の3前提を解明してから教師データ本生成**。自前生成パイプラインの基盤は本セッションで実証完了（pekarstas正本確定、公式設定で全streetメモリ収束、flop 0.5%到達、river30件E2E）。残るは「どれだけ・どの配分で・どの品質で生成するか」の計画確定。**設計の正本＝DESIGN_NOTES §80（方針）+§81（prompt契約）+§82（solver/flop設定）。** 参照ナビは§0.5。

### 10.1 状態確認
```powershell
cd C:\dev\pokerrl-training
git rev-parse --show-toplevel   # C:/dev/pokerrl-training
git branch --show-current       # master
git log --oneline -8            # HEAD=c144392（本セッションでコミット。pekarstas成果物。前は2c0f744）
git status                      # tracked clean(scratch未追跡のみ)
```
health check:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_train_harness.py tests/test_training_loop.py tests/test_grpo_batch.py tests/test_advantage.py tests/test_grpo_loss.py tests/test_curriculum_spots.py tests/test_spot_checks.py tests/test_integrated_step.py tests/test_reward.py tests/test_reward_wiring.py -q
.\.venv\Scripts\python.exe scripts\verify_pokerrl_encode.py
```

### 10.2 生成計画の3前提を解明（最優先・本生成の前提）
自前生成パイプラインの基盤は実証済み（pekarstas正本、公式設定で全street解ける、§82）。本生成の前に以下3前提を実測・設計で確定する。**この3点が揃うまで本生成を始めない**（壊れた前提で大量生成すると無駄になる）。

1. **総教師例数の目標（要§80.8照合）**: 旧SFT=56.3万件は局面単位（1局面=単一action）。新方式はhand単位（1ボード約1000例＝OOP/IP全hand×全action混合分布）で**非互換**（§82.6）。**先に§80.8のSFT設計を実物確認**し、Phi-4-mini 3.8B+aux headの学習に必要な教師例数（hand単位）を見積もる。旧56万を額面でゴールにしない。
2. **シナリオ配分の設計**: 本セッションのテストはSRP_BTN_vs_BB 1種のみ。本番は39シナリオ（SRP/3BET/4BET、カバレッジ§8.5の`hu_postflop_coverage_report.json`）に拡大。各シナリオに何ボード割るか。PokerBench street比率（Flop6.5%/Turn41%/River52%）に沿ってボード数を配分すればflop総時間が圧縮される（flopは少数派）。実戦頻度（どのプリフロップラインが頻出か）も加味。
3. **flop 0.5% vs 1% の分布差実測**: 本セッションでflop 0.5%=411秒/件、1%=211秒/件（約半分）と判明。**0.5%と1%の混合分布差は未測定**。river/turnの16-bit比較と同じ手法（同一ボードを2品質で解き、全hand×全actionの最大/平均絶対差）で測り、1%で十分なら生成時間が半減する。turn/riverは0.5%確定。

**試算の手がかり（本セッション実測）**: 1ボードあたり教師例 flop約1038/turn約967/river約928。flop 0.5%=411秒/件・1%=211秒/件（逐次）。turn約1.2秒・river約40ms。並列は同一品質では不可（#25）。

### 10.2b 3前提確定後の本生成（未着手）
- 39シナリオ×配分ボード数で生成。専用出力dir、manifest管理（正本ID=`scenario+board+seed`、各IDのpending/running/succeeded/rejected/failed・exploitability・hash記録、§2.4の轍回避＝成功件数をoffsetにしない）。
- solver出力（hand×action混合分布）を§81統一prompt schemaに沿ったSFT教師形式へ変換。この変換器の設計が本生成と並行で必要。
- 品質ガード: exploitability下限0.5%（#24、flopは3前提次第で1%許容の可能性）、確率和検算（誤差<1e-5）、hero包含は自前生成で原理的に起きない（§2.7）。

### 10.3 設計後（未着手）
- 統一計算器の実装（**DESIGN_NOTES §81準拠**、§81.4末の申し送り3点〔(a)`C_i=-payoffs[i]`を複数street decisionで再実測、(b)PokerKit属性名の実在を実装時に先に実測確認、(c)non-zero rakeのplayable pot取得は未実測〕を先に実測確認）。
- 自前生成パイプラインの本実装 → SFTやり直し → GRPO。
- §80.10の残る全系監査（報酬計算・state構築・確率取り出し）を同深度で。

### 10.4 AHTOOOXAがダメだった場合のフォールバック（両分岐、ユーザー合意済み）
正本レンジをAHTOOOXAで着手するが、ダメだった場合の次の手を「ダメの種類」で分岐させる。「ダメ」と判断する基準を先に定めること。

**分岐1: 作る段階でダメ（局面生成→ソルバーまで技術的に通らない／カバレッジ不足）**
- レンジのパース・形式問題（tupleの50/50正規化が想定と違う、必要ラインがAHTOOOXAに無い）→ tyloo等の別の無料レンジで相互補完、またはAHTOOOXA内でgreenline⇄pekarstas切り替え。
- 局面生成ロジックの問題（自前実装の不備）→ 設計を見直す。**これは外部データの問題でなく自前実装の問題なのでレンジを替えても解決しない**（切り分け注意）。
- カバレッジ不足（AHTOOOXAのライン数が足りず生成局面が偏る）→ 欠ける局面タイプを定量化し、別レンジで補うか当面諦める。

**分岐2: 作れたがモデルが弱いまま（SFT→GRPOで期待した強さに届かない）**
- **まず弱さの原因が「丸め」か「別の要因」かを切り分ける**（これを飛ばして有料を買うと、丸めのせいでない場合に無駄金）。教師の混合分布品質を点検し、丸めレンジ起因で分布が粗いのか、報酬・state構築・確率取り出し等の別系統欠陥か（＝§80.10全系監査と連動）を特定。
- **丸めが主因と判明 → 高精度レンジ（有料）投資を検討**。順序=FreeBetRange（Pro年$159/Elite年$348、契約前に「大量export可否・6max100bbの必要ライン×OOP/IPカバー・実ファイル形式が手元ソルバー読込可か・rake設定・返金規約」を販売元へ問い合わせ）→ RangeConverter（$99〜）。**買うなら最小単位で実物確認してから本格利用、撤退基準$500枠内、返金不可前提。** FreeBetRangeは素性にユーザー不安あり（§2.8）。
- **丸めが主因でない → 有料を買っても解決しない。別系統（報酬・state・確率取り出し）の監査へ**（§80.10）。

### 10.5 docsコミット（完了済み）
- **§80・§81はコミット済み（`bb8c972`、本セッション）。** 旧`unified_prompt_schema.md`は§81へ一元化し廃止。**追加のコミット作業は当面なし**（次に設計文書を書く場合はDESIGN_NOTES新節に追記する方針＝別ファイルを作らない）。

### 10.6 維持事項
- `pokerkit==0.7.4`・6-max・state正本・各Config経由・docs配置・正本read-only・凍結ベース共有。Spot Checks 50緩和/訓練混入禁止・verify_pokerrl_encodeスキップ禁止。GPUアプリ閉じる(#19)。品質ガード有効。報酬3成分復活・reference KL再試行・position prior却下済み再試行しない(#21)。**Rust Solver/Deep CFRを削除しない(#12)。教師源=Rust Solver確定、外部既製データは公式合法な大量取得不可、自前ソルバー路線が最善。**