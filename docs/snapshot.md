

# Commander Snapshot
## Generated: 2026-05-03

## 1. 現在地点
- **最後に完了したタスク**: Phase 23-Fix5 Task 2「LLM read timeout 15秒 + リトライ廃止」
- **次に実行すべきタスク**: Phase 23-Fix6「スタック別オールインコールレンジの実装」
- **全体進捗率**: Phase 1〜22全完了 + Phase 23 Fix1〜Fix5完了。BG廃止・LLM最適化完了、戦略精度改善フェーズ

## 2. 完了済みPhase・Task一覧
| Phase | Task | 状態 | 成果物（ファイルパス） |
|-------|------|------|----------------------|
| 1 | 1-3 | ✅ | config.yaml, .env.example, .gitignore, README.md, requirements.txt, LICENSE-AGPL-v3, profiles/, tests/ |
| 2 | 1-2 | ✅ | capture/base_capture.py, card_capture.py, mss_capture.py, file_capture.py |
| 3 | 1-3 | ✅ | recognition/__init__.py, base_recognizer.py, card_recognizer.py |
| 4 | 1-2 | ✅ | recognition/number_recognizer.py |
| 5 | 1 | ✅ | recognition/button_recognizer.py, dealer_recognizer.py |
| 6 | 1 | ✅ | recognition/diff_detector.py |
| 7 | 1-2 | ✅ | core/game_state.py, position_calculator.py |
| 8 | 1-3 | ✅ | recognition/action_estimator.py, fixtures/action_sequences/ |
| 9 | 1-3 | ✅ | core/hand_manager.py |
| 10a | 1-3 | ✅ | core/game_loop.py, recognition/name_recognizer.py |
| 10b | fixes | ✅ | ライブ修正各種 |
| 11 | 1-2 | ✅ | solver/postflop_cli/, solver/bin/postflop_cli.exe |
| 12 | 1 | ✅ | solver/solver_bridge.py |
| 13 | 1 | ✅ | strategy/solver_request_builder.py |
| 14a | 1 | ✅ | preflop_charts/6max_gto.json |
| 14b | 1 | ✅ | strategy/preflop_chart.py |
| 14.5 | 1 | ✅ | LLMプロンプト・Schema（llm_pipeline.py内にハードコード） |
| 15 | 1 | ✅ | strategy/llm_pipeline.py, baseline_ranges.json |
| 16 | 1 | ✅ | strategy/multiway_engine.py |
| 17 | 1-3 | ✅ | strategy/recommendation_engine.py |
| 18 | 1+ | ✅ | core/hand_manager.py（DB統合） |
| 19 | 1-2 | ✅ | gui/hud_overlay.py |
| 20 | 1-4 | ✅ | gui/main_window.py, main.py |
| 21 | fixes | ✅ | fix1〜fix21 |
| 22-1 | 1 | ✅ | game_loop.py（推奨保存） |
| 22-2 | fixes | ✅ | 30分ライブテスト + Fix1(チャート拡充) + Fix2(BB vs_3bet) |
| 22-3 | 1 | ✅ | strategy/llm_schemas.py, pydanticバリデーション |
| 22-4 | 1 | ✅ | strategy/preflop_delta_policy.py |
| 22-5 | 1 | ✅ | llm_pipeline.py（匿名化） |
| 23-1 | 1-3 | ✅ | 統計フィールド改修（three_bet_pct等、機会カウンター、DB改修、opponent_stats接続） |
| 23-2 | 1-2 | ✅ | fallback修正（phase同期、hand_just_startedスキップ、BB deferred） |
| 23-Fix1 | 1-2 | ✅ | ALL_IN認識、vs_all_in、安全ガード、active_player_countをhand_managerベースに |
| 23-Fix2 | 1-3 | ✅ | 診断ログ、ポジション固定化（ハンド開始時にロック）、ログ頻度調整 |
| 23-Fix3 | 1-4 | ✅ | FOLD→_players_in_hand反映、active_player_count同期、NEW_HANDクールダウン5秒、BG phase強制同期 |
| 23-Fix4 | 1 | ✅ | プレイヤー名キャッシュ、HUD進捗表示、LLM合計タイムアウト |
| 23-Fix5 | 1 | ✅ | **BG計算完全廃止** — _start_bg_computation(), _cancel_bg_computation(), threading/Lock関連全削除。is_my_turn=True時のみ同期計算に一本化 |
| 23-Fix5 | 2 | ✅ | **LLM read timeout 2秒→15秒、リトライ廃止** — _call_api()のリトライループ削除、単一API呼び出しに簡素化 |

## 3. 現在のプロジェクト構造
```
poker-assistant/
├── main.py
├── config.yaml                    # llm.timeout_sec=15, retry_count=0, total_timeout_sec=15
├── .env / .env.example / .gitignore
├── AGENTS.md / README.md / requirements.txt / LICENSE-AGPL-v3
├── SPEC.md (v1.4)
├── capture/
│   ├── __init__.py, base_capture.py, card_capture.py, file_capture.py, mss_capture.py
├── recognition/
│   ├── __init__.py, base_recognizer.py, card_recognizer.py, number_recognizer.py
│   ├── button_recognizer.py, dealer_recognizer.py, name_recognizer.py
│   ├── diff_detector.py, action_estimator.py
├── core/
│   ├── __init__.py, game_state.py, game_loop.py, position_calculator.py, hand_manager.py
├── strategy/
│   ├── __init__.py, preflop_chart.py, solver_request_builder.py
│   ├── llm_pipeline.py, llm_schemas.py, multiway_engine.py
│   ├── recommendation_engine.py, preflop_delta_policy.py
│   └── baseline_ranges.json
├── solver/
│   ├── postflop_cli/ (Cargo.toml, src/main.rs)
│   ├── bin/postflop_cli.exe
│   ├── solver_bridge.py
├── preflop_charts/6max_gto.json
├── gui/
│   ├── __init__.py, main_window.py, hud_overlay.py
├── profiles/
│   ├── coinpoker_6max.json, ggpoker_6max.json
├── scripts/ (analyze_replays.py等)
├── data/poker_assistant.db
├── hand_replays/ , logs/
└── tests/ (678テスト)
```

## 4. 確定済みの設計判断

### アーキテクチャ全体
- CoinPoker 6max NLH専用。HDMIキャプチャカード経由で画面認識、GTO最適アクション算出、HUDオーバーレイで推奨表示
- キャプチャ方式（capture_card/mss/file）はconfig.yamlで切替。OCR/ソルバー/LLM/HUDはキャプチャ方式に非依存
- 0.5秒間隔でポーリング。差分検知でOCRスキップ最適化
- ソルバー(postflop-solver Rust CLI)はsubprocessで常駐。stdin/stdout JSON行通信
- LLMはOpenRouter API経由。モデルは.envで切替可能

### 画面認識
- カード: 4色HSVスート判定 + EasyOCR GPUランク認識。ヒーローカードはハンド開始時にキャッシュ
- 数値: ポットHSV色フィルタ + EasyOCR。スタック/ベットはOTSU二値化
- ボタン: HSV色検出で自分ターン判定 + ボタン種別（fold/call/check/raise/bet）
- ディーラー: 赤+白ピクセルスコアリング。ハンド開始時にロック
- プレイヤー名: ハンド開始時にのみOCR実行してキャッシュ。ハンド中は再OCRしない
- アクション推定: 前後フレームのGameState差分から推定。FOLD確定は3フレーム連続None

### 戦略判断（recommendation_engine.py）
- プリフロップ: GTOチャート参照 + delta policy（LLMによる微調整、サンプル30+ハンドで発動）
- ポストフロップ ヘッズアップ(2人): LLMレンジ推定 → ソルバー → 搾取調整 → 理由生成
- ポストフロップ マルチウェイ(3+人): eval7 MCエクイティ → LLM判断
- ルーティング: `game_state.active_player_count` で分岐（≥3→マルチウェイ、==2→ソルバー、<2→フォールバック）

### Phase 23-Fix5 で確定した設計判断（重要）

**BG計算完全廃止:**
- `_start_bg_computation()`、`_cancel_bg_computation()` メソッドを削除
- `_pending_recommendation`、`_pending_recommendation_street`、`_bg_computation_thread`、`_bg_computation_lock`、`_bg_street`、`_bg_cancelled` インスタンス変数を全削除
- `import threading` を削除
- `stop()` と `reset()` からBG関連処理を削除
- `current_recommendation` プロパティは `_previous_recommendation` を返すように変更

**新しい `_handle_strategy()` フロー:**
```
phase=waiting/hand_end → HUDクリア、return
hand_just_started → スキップ、return
is_my_turn=False → ターン終了時（True→False）のみクリア、return
is_my_turn=True（初回、_last_strategy_is_my_turn=False）:
  → _notify_hud_computing()
  → _generate_recommendation()（同期、ブロッキング）
  → _previous_recommendation にセット
  → HUD表示
is_my_turn=True（継続、_last_strategy_is_my_turn=True）:
  → _apply_action_constraints_to_recommendation()（制約のみ再適用）
  → HUD表示
```

**`_apply_pending_action_constraints()` → `_apply_action_constraints_to_recommendation(recommendation, game_state)` にリネーム:**
- 引数で recommendation を受け取る形に変更（`self._pending_recommendation` への直接参照を排除）
- `self._previous_recommendation = constrained` で結果を保存

**LLM API通信（簡素化）:**
- read timeout: 15秒（config.yaml `llm.timeout_sec`）
- connect timeout: 5秒（ハードコーディング）
- リトライ: 0回（config.yaml `llm.retry_count`）
- `_call_api()`: 単一の `requests.post()` 呼び出し。失敗時はNone返却
- リトライループ、`total_timeout_sec`のwall-clock計測ロジックを削除
- 初期化ログ: `LLMPipeline initialized: model=..., timeout=15.0s`（retry/total_timeout表示を削除）

### 以前から確定済みの設計判断（維持）
- active_player_count: `_sync_game_state_with_hand_manager()`内でhand_manager.get_players_in_hand()から毎フレーム再計算
- NEW_HANDクールダウン: 5秒（hand_manager.NEW_HAND_COOLDOWN_SEC）
- プレイヤー名キャッシュ: `_cached_player_names` + `_player_names_captured_for_hand`（hand_idが変わったら再取得）
- ポジションはハンド開始時にロック（`_hand_positions`）、ハンド中は変更しない

## 5. 既知の課題・TODO

### 次に実装すべき（Phase 23-Fix6）
1. **スタック別オールインコールレンジ** — 現在の `vs_all_in` を3段階に分割
   - `vs_all_in_short`（20BB以下）: `"22+,A2s+,A9o+,K9s+,KTo+,QTs+,JTs"`
   - `vs_all_in_medium`（21-50BB）: `"55+,ATs+,AJo+,KQs"`
   - `vs_all_in_deep`（51BB以上）: `"99+,AQs+,AKo"`
   - 修正ファイル: `preflop_charts/6max_gto.json`、`strategy/preflop_chart.py`
   - preflop_chart.py でeffective_stackに応じて適切なキーを選択するロジックを追加
   - delta policyが相手統計に基づき微調整（既存の仕組みがそのまま動く）

### 低優先度
2. **hero_card_2のOCR失敗（waiting中のみ）** — gray_mean=143.3はカード裏面の固定パターン。ハンド開始時には毎回成功するため実害なし。WARNINGログレベルをDEBUGに下げる対応で十分
3. **ソルバー未到達** — 全てactive≥3でマルチウェイ。Practice Gamesの特性。1対1の局面に遭遇すれば自動的にソルバーが使用される
4. **HUD終了時のKeyboardInterrupt** — gui/hud_overlay.py line 96付近。機能に影響なし
5. **eval7/pyparsing DeprecationWarning** — 7件。機能に影響なし

## 6. Phase間インターフェース情報

### Recommendation dataclass
```python
@dataclass
class Recommendation:
    action: str                      # FOLD/CHECK/CALL/BET/RAISE/ALL_IN
    amount: int = 0
    reason: str = ""
    confidence: str = "low"          # high/medium/low
    strategy_source: str = "fallback"  # preflop_chart/preflop_chart_delta/preflop_chart_fallback/solver/llm_multiway/deferred/fallback
    action_probabilities: dict = field(default_factory=dict)
    solver_exploitability: float | None = None
    latency_breakdown: dict = field(default_factory=dict)
    pot_percentage: float | None = None
    amount_bb: float | None = None
    preset_hint: str | None = None
    raise_multiplier: float | None = None
    raise_multiplier_label: str | None = None
```

### hand_manager 公開インターフェース
- `get_players_in_hand() -> set[int]` — 参加中座席（FOLD除外）
- `hand_just_started -> bool` — ハンド開始直後フラグ
- `get_opponent_stats(game_state) -> dict[str, dict]` — seat別統計
- `get_preflop_actions() -> list` — ブラインド除外のプリフロップアクション
- `set_recommendation(...)` / `set_human_action(...)` — 推奨/ヒーローアクション保存
- `phase -> str` / `hand_id -> int | None`

### game_loop 内部状態（BG廃止後）
- `_previous_recommendation: Recommendation | None` — 現在表示中の推奨
- `_last_strategy_is_my_turn: bool` — 前フレームのis_my_turn
- `_last_strategy_phase: str | None` — 前フレームのphase
- `_last_recommendation_log: str | None` — ログ重複排除用
- `_cached_player_names: dict[str, str | None]` — ハンド開始時にキャッシュ
- `_player_names_captured_for_hand: int | None` — キャッシュ対象hand_id
- `_hand_positions: dict[int, str] | None` — ハンド開始時にロックされたポジション
- `_hand_dealer_seat: int | None` — ハンド開始時にロックされたディーラー座席

### 戦略ルーティング（recommendation_engine.generate()）
```
phase == "preflop" → _generate_preflop() → チャート + delta policy
phase in {flop,turn,river}:
  active >= 3 → _generate_postflop_multiway() → eval7 + LLM(MULTIWAY_DECISION_PROMPT)
  active == 2 → _generate_postflop_headsup() → LLM(RANGE_ESTIMATION_PROMPT) + ソルバー + LLM(EXPLOIT_ADJUSTMENT_PROMPT) + LLM(REASON_GENERATION_PROMPT)
  active < 2  → _generate_fallback()
```

### LLMプロンプト一覧（strategy/llm_pipeline.py内にハードコード）
| プロンプト名 | 用途 | max_tokens | 主要入力パラメータ |
|-------------|------|-----------|------------------|
| RANGE_ESTIMATION_PROMPT | 相手レンジ推定（PioSOLVER形式） | 400 | board, street, positions, pot, stack, SPR, action_history, opponent_stats, baseline_ranges |
| EXPLOIT_ADJUSTMENT_PROMPT | ソルバー出力の搾取調整 | 250 | solver_actions, GTO_strategy, hero_hand/equity/EV, opponent_stats, board, pot, stack |
| MULTIWAY_DECISION_PROMPT | マルチウェイ判断 | 250 | board, street, hero_hand/position/stack, pot, num_players, equity(MC 10k), action_history, opponent_profiles |
| REASON_GENERATION_PROMPT | HUD用日本語理由（40文字以内） | 80 | action, reasoning, hero_hand, board |
| PREFLOP_DELTA_PROMPT | プリフロップチャート微調整 | 250 | hero_position/hand, scenario, chart_probs, villain_stats, effective_stack_bb, action_prefix |

各プロンプトは "Respond with JSON only" で終わり、pydanticバリデーション（llm_schemas.py）で出力を検証。匿名化済み（プレイヤー名→seat番号）。

### DBスキーマ（opponents）
```sql
opponents (
    player_name TEXT PRIMARY KEY,
    total_hands INTEGER, first_seen TEXT, last_seen TEXT,
    vpip REAL, pfr REAL, three_bet_pct REAL, cbet_flop_pct REAL,
    fold_to_three_bet REAL, went_to_showdown REAL,
    long_term_style TEXT, freshness_note TEXT,
    three_bet_opportunities INTEGER, three_bet_count INTEGER,
    cbet_flop_opportunities INTEGER, cbet_flop_count INTEGER,
    fold_to_three_bet_opportunities INTEGER, fold_to_three_bet_count INTEGER,
    wtsd_opportunities INTEGER, wtsd_count INTEGER
)
```

### テスト基盤
- 全テスト数: **678**
- `pytest -q` → 678 passed, 7 warnings（eval7/pyparsing DeprecationWarning）

## 7. 直近の作業コンテキスト

### Phase 23-Fix5 Task 1: BG計算廃止

**問題の根本原因:** NEW_STREET時にBG事前計算を開始するが、相手のアクションによって局面が変わるため、自分のターン到来時にはBG結果が古くなり、ほぼ毎回同期計算にフォールバックしていた。API料金の無駄、コードの複雑さの原因。

**実装内容:**
- `core/game_loop.py` の `_handle_strategy()` を完全書き換え
- `_start_bg_computation()`（約90行）、`_cancel_bg_computation()`（約7行）を削除
- BG関連インスタンス変数6個（`_pending_recommendation`, `_pending_recommendation_street`, `_bg_computation_thread`, `_bg_computation_lock`, `_bg_street`, `_bg_cancelled`）を削除
- `import threading` を削除
- `_apply_pending_action_constraints()` → `_apply_action_constraints_to_recommendation(recommendation, game_state)` にリネーム
- テスト: 4ファイル修正（test_game_loop.py, test_game_loop_hud.py, test_game_loop_recommendation.py, test_recommendation_integration.py）
- 結果: 679→678テスト（BG関連テスト削除+同期テスト追加の差分）

### Phase 23-Fix5 Task 2: LLM read timeout 15秒 + リトライ廃止

**問題の根本原因:** read timeout 2秒がDeepSeek v4 Flashの実測応答時間（p50≈2秒）に対して短すぎ、約45%の確率で1回目がタイムアウト。リトライで4〜5秒の遅延が発生。

**実装内容:**
- `config.yaml`: timeout_sec=2→15, retry_count=1→0, total_timeout_sec=8→15
- `strategy/llm_pipeline.py`: `_call_api()` のリトライループを単一呼び出しに置換。wall_start/total_timeout未使用変数削除。初期化ログ簡素化
- `tests/test_llm_pipeline.py`: TEST_CONFIG更新、リトライテスト2個→失敗テスト1個に置換
- 結果: 678テスト全PASS

### ライブテスト結果（2026-05-03 23:45〜23:52、Fix5実装後）

**7ハンド実施。主要な検証結果:**

| 検証項目 | 結果 |
|---------|------|
| BG計算廃止 | ✅ `BG thread started` ログ一切なし |
| LLMタイムアウト | ✅ `Read timed out` ログ一切なし |
| LLM応答時間 | ✅ 全3回とも1.6〜2.7秒で1回目成功 |
| 推奨表示速度 | ✅ 自分ターンから1.6〜2.7秒で推奨表示 |
| 推奨の正確性 | ✅ 11推奨中10個が明確に正しい、1個がボーダーライン |

**LLM応答時間の実測:**
| # | phase | active | 応答時間 | 推奨 |
|---|-------|--------|---------|------|
| 1 | flop | 6 | 2.7秒 | CHECK ✅ |
| 2 | flop | 5 | 2.0秒 | FOLD ✅ |
| 3 | flop | 5 | 1.6秒 | FOLD ✅ |

**推奨の正確性（11判定中）:**
- 10/11 が明確に正しい（プリフロップチャート+マルチウェイLLM）
- 1/11 がボーダーライン: Hand 7でTT vs ALL_IN(100BB)をCALL推奨 → スタック深度による調整が必要（次の改善タスク）

**ボーダーラインの詳細（Hand 7）:**
- Ts Th、BBポジション
- seat3がRAISE 300に対してALL_IN 9984（約100BB）
- チャートの`vs_all_in`にTTが含まれているためCALL推奨
- 100BBのオールインに対してTTでCALLは攻撃的。相手がタイトなら必要エクイティ不足
- → スタック別オールインコールレンジ（vs_all_in_short/medium/deep）で解決

## 8. 次のセッションへの引継ぎ事項

### 最初に行うべきこと
Phase 23-Fix6「スタック別オールインコールレンジ」を実装する。

**実装対象ファイル:**
1. `preflop_charts/6max_gto.json` — 各ポジションの `vs_all_in` を `vs_all_in_short` / `vs_all_in_medium` / `vs_all_in_deep` の3段階に分割
2. `strategy/preflop_chart.py` — effective_stack（BB単位）に応じて適切なキーを選択するロジック追加
3. テストファイル — 3段階のスタック別分岐が正しく動作することを検証

**概算レンジ（初期値、実戦データ蓄積後に調整）:**
```
vs_all_in_short（20BB以下）: "22+,A2s+,A9o+,K9s+,KTo+,QTs+,JTs"
vs_all_in_medium（21-50BB）: "55+,ATs+,AJo+,KQs"
vs_all_in_deep（51BB以上）: "99+,AQs+,AKo"
```

**実装の流れ:**
1. まずBuilderに調査指示: `preflop_charts/6max_gto.json` の `vs_all_in` の現在の構造と、`strategy/preflop_chart.py` の `vs_all_in` 参照箇所を確認
2. JSONチャートの修正
3. preflop_chart.py にeffective_stack分岐ロジックを追加
4. テスト追加
5. ライブテストで検証

**設計上の注意:**
- effective_stackはGameStateから取得（hero_stack vs 相手のオールイン額で計算）
- delta policyは既存の仕組みがそのまま動く（プロンプトにeffective_stack_bbが既に含まれている）
- 閾値（20BB/50BB）はconfig.yamlで管理可能にすると将来調整しやすい

### Fix6実装後にSPEC.md改訂を実施
Fix5とFix6の設計変更をSPEC.md v1.4に反映する。改訂箇所：
- **セクション3.4** — 時間内訳テーブルのLLM行を実測値（1.5〜3.5秒）に更新
- **セクション6.4** — タイムアウト15秒、リトライ0回に更新
- **セクション6.5〜6.5.4** — BG先行計算の記述を全面書き換え（「is_my_turn=True時のみ同期計算」）
- **セクション7** — スタック別オールインレンジ（vs_all_in_short/medium/deep）の記述を追加
- **セクション14.1** — config.yaml llmセクションの値を更新
- **セクション21** — LLMタイムアウト15秒、リトライ0回に更新

IMPLEMENTATION_PLAN.mdは改訂不要（全Phase完了済み）。

### ユーザーからのフィードバック記録
- 「スタックサイズによる調整はLLMの勝率をあげるロジックとして必須」
- 「バランスよく設計できますか？」→ チャート側の3段階分岐 + delta policyの微調整の組み合わせで対応
- 「概算レンジで始めて、実戦データが溜まったら精度を検証・調整するのが現実的」→ ユーザー合意済み
- 「hero_card_2のOCR失敗はwaiting中のみで、カードの見え隠れ（演出）が原因かも」→ 実害なし、無視でOK
- 「opponentsの数値はDB蓄積の全件数で正常」→ ユーザー理解済み
- 「仕様書の改訂はFix6実装後にまとめて行う」→ ユーザー合意済み