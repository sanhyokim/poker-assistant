
# **PokerRL+GRPO 6-max NLHE 推論エンジン 実装指令書 v1.3**

**Updated**: 2026-06-03  
**For Repository**: `https://github.com/sanhyokim/poker-assistant`  
**Companion to**: `SPEC.md v3.6`, `DESIGN_NOTES.md (§53-55追記済み)`, `snapshot.md (2026-06-02)`  
**Authority Hierarchy**: 既存 SPEC.md と矛盾する場合は SPEC.md 優先。本指令書は SPEC.md §10A の Deep CFR Inference Bridge を置き換える追加章として扱う。

---

## 0. 前提と原則

### 0.1 既存システムが既に確立している原則（変更不可）

本指令書は以下の既存原則を**完全に尊重**する:

1. **Quality over Speed**: 「速く何かを出す」より「正しい文脈の推奨のみ」
2. **No provisional recommendations**: 暫定推奨は表示禁止。確定推奨のみ HUD に出す
3. **GameLoop must never freeze**: 認識ループは絶対に止めない
4. **State-only HUD when not ready**: 計算中はステータスメッセージのみ表示
5. **Stale context discard**: Context Snapshot 不一致なら結果を破棄
6. **No silent fallback**: 入力が不安定なら推奨を出さず、明示的にステータス表示

### 0.2 新エンジンの位置づけ

本指令書で構築する **PokerRL+GRPO エンジン** は、現行の Deep CFR Bridge を置き換える新コンポーネントである。具体的には:

- **対象**: HU postflop と Multiway postflop の主推論エンジン
- **位置**: 既存 `strategy/recommendation_engine.py` のルーティング下にあるバックエンドの1つ
- **共存**: 当面、Deep CFR と Rust solver は決して削除しない（フォールバック保持）
- **不変**: Preflop GTO charts、eval7、LLM exploit adjustment、HUD、GameLoop、HandManager は変更しない

### 0.3 既存リソースを最大限活用

- 既存 `request_id / active_id / cancelled_ids` 機構: そのまま流用
- 既存 Context Snapshot: そのまま流用
- 既存 Stability Guards: そのまま流用
- 既存 daemon worker thread: そのまま流用
- 既存 LLM pipeline (OpenRouter GPT-5.4-mini): exploit adjustment は当面維持

---

## 1. システム統合アーキテクチャ

### 1.1 既存システム + 新エンジンの全体図

```
[既存] HDMI Capture / mss
        ↓
[既存] Site Adapter (CoinPoker OCR + 状態認識)
        ↓
[既存] GameState (GameState, PlayerState, ActionRecord)
        ↓
[既存] HandManager (lifecycle + DB保存)
        ↓
[既存] GameLoop (戦略ルーティング)
        ↓
[既存] RecommendationEngine (routing)
        │
        ├─ Preflop → [既存] GTO Charts + DB stats + LLM exploit
        ├─ HU Postflop → ★ 新 PokerRL+GRPO Bridge ★
        │              (旧 Deep CFR Bridge を置き換え)
        ├─ Multiway Postflop → ★ PokerSkill式 Context Engine + LLM ★
        │              + 既存 eval7 数理ガード
        ├─ All-in → 既存 equity / pot odds 数理避難路
        └─ Fallback → 既存 Deep CFR / 既存 Rust Solver (decommission予定)
                ↓
        [既存] Async Worker Thread
                ↓ (request_id / context_snapshot 経由)
        [既存] HUD Overlay (PyQt6)
```

### 1.2 新規追加するモジュール

以下を `strategy/` 配下に追加する:

```
strategy/
  ├── pokerrl_bridge.py          ★ 新: PokerRL+GRPO 推論ブリッジ（既存 deep_cfr_bridge.py と同じ I/F）
  ├── pokerrl_prompt_builder.py  ★ 新: GameState → プロンプト変換
  ├── pokerrl_inference_engine.py★ 新: vLLM/llama-cpp ローダ + 推論実行
  ├── pokerrl_heads.py            ★ 新: Action / Sizing 補助ヘッド
  ├── pokerrl_output_parser.py    ★ 新: モデル出力 → Recommendation 変換
  ├── pokerrl_spot_classifier.py  ★ 新: 局面分類 (HU/Multiway, complexity)
  └── verify_pokerrl_encode.py    ★ 新: 訓練側との一致検証ツール

models/pokerrl/
  ├── base_phi4_mini/             ベースモデル重み（microsoft/Phi-4-mini-instruct）
  ├── sft_adapter/                Phase 1 LoRA アダプタ
  ├── grpo_adapter/                Phase 2 LoRA アダプタ
  └── final_quantized/             vLLM/llama-cpp 用 Q4_K_M 量子化版
```

訓練側リポジトリ: `C:\dev\pokerrl-training`（作成済み）。本指令書では訓練側は別途。

### 1.3 既存 deep_cfr_bridge との互換 I/F

新ブリッジは既存 `deep_cfr_bridge.py` と**同じ呼び出し I/F** を提供する。これにより `recommendation_engine.py` の変更は最小限ですむ:

```python
class PokerRLBridge:
    def infer(
        self,
        game_state: GameState,
        request_id: str,
        context_snapshot: ContextSnapshot,
    ) -> RecommendationResult:
        # game_state を文字列プロンプト化
        # vLLM/llama-cpp で推論
        # 出力を Recommendation オブジェクトに変換
        # context_snapshot を結果に添付（GameLoop が validate する）
        ...
```

---

## 2. レイテンシ設計（SPEC.md §0.1 "Quality over Speed" 原則準拠）

### 2.1 既存原則の再確認

SPEC.md は**速度より精度**を優先する。具体的には:

- 速いが間違った推奨 < 遅いが正しい推奨 < 計算中のステータス表示
- CoinPoker のアクションタイマー（15-30秒）内に答えを出せばよい
- **<50ms はあくまで「達成できれば理想」の目標**であり、それを超えても State-only HUD で許容される

### 2.2 推論レイテンシ予算（修正版）

snapshot.md の「<50ms」目標を達成しつつ、達成できない局面では既存 async 機構で安全に降伏する。

| Tier | 用途 | レイテンシ予算 | 出力品質 | 失敗時挙動 |
|---|---|---|---|---|
| **T0: Cache** | Preflop 標準スポット | 5-50ms | GTO ほぼ完全 | T1 へ降格 |
| **T1: Quick LLM** | Postflop 通常局面 | 50-300ms | GTO 近似 | T2 へ降格 |
| **T2: LLM + Tool** | Postflop 難局面 | 1-5秒 | GTO 高精度 | State-only HUD で「DEEP THINKING」表示 |
| **T3: LLM + Search** | 極難局面（deep SPR flop multiway 等） | 5-12秒 | GTO 最高精度 | State-only HUD で「DEEP THINKING」表示、最終的に既存ソルバーへフォールバック |

**全 Tier は既存 daemon worker thread で実行**。GameLoop は止まらない。

### 2.3 「<50ms」目標の現実性検証

snapshot.md の <50ms 目標を達成するには、以下の条件が**すべて**揃う必要がある:

| 条件 | 達成手段 |
|---|---|
| モデルサイズ | **Phi-4-mini-instruct 3.8B を Q4_K_M 量子化（確定）** |
| 推論エンジン | vLLM (CUDA graph + paged attention) または llama.cpp (GPU offload) |
| プロンプト長 | 入力 ≤ 200 tokens（圧縮必須） |
| 出力長 | **max_new_tokens = 8** (action token + sizing token のみ、no reasoning) |
| バッチサイズ | 1 (リアルタイム) |
| 常駐推論 | KV cache warm-start、cold start を排除 |
| ヘッド出力 | LLM の autoregressive 生成を回避し、**最終 hidden state から直接補助ヘッドで分類**（推論速度の支配的要素） |

**最重要設計判断**: snapshot.md で言及される「補助ヘッド」が **<50ms の鍵**。autoregressive な文字列生成では 50ms は無理なので、**LLM 末尾 hidden state → MLP ヘッド → 即出力** という構成にする。

### 2.4 補助ヘッドアーキテクチャ詳細

```
入力: GameState を文字列化 (約 100-200 tokens)
        ↓
[Phi-4-mini-instruct 3.8B] (LoRA fine-tuned, 確定)
        ↓
最終トークンの hidden state (3072 dim)
        ↓
   ┌──────────┴──────────┐
   ▼                      ▼
[Action Head]        [Sizing Head]
2層 MLP               2層 MLP
出力: 4 logits        出力: 1 sigmoid (0.1x-3.0x pot)
(Fold/Check-Call/     
 Raise/All-in)
        ↓
[Output Parser]
        ↓
Recommendation (既存形式)
```

**速度の試算**（Phi-4-mini Q4 on RTX 3080）:
- プロンプト encoding (200 tokens): ~80ms
- Hidden state 抽出: ~20ms
- MLP heads: ~1ms
- 合計: **~100ms** ← <50ms は厳しい

**真の <50ms 達成のための工夫**:
- プロンプト encoding を **完全 prefix cache** 化（システムプロンプト + 共通指示は予め KV cache に格納）
- 状態依存部分のみ毎回エンコード（実質 ~50 tokens）
- これで encoding 20-30ms + heads 1ms = **~30ms** を目標

### 2.5 Tier 階層化

```
[GameState 到着]
        ↓
[Spot Classifier] (~1ms, decision tree)
        │
        ├─ Preflop 標準 → T0: Cache lookup → 5-50ms で返却
        │
        ├─ Postflop 通常 → T1: PokerRL+GRPO (heads only) → 30-300ms
        │
        ├─ Postflop 難 → T2: PokerRL+GRPO + tool/search → 1-5秒
        │   (LLM が「複雑」と判定 or sizing 信頼度低)
        │
        └─ 極難 → T3: PokerRL+GRPO + 既存 Rust Solver 並列 → 5-12秒
            (deep SPR flop multiway, etc.)
```

各 Tier の予算超過時は **既存の async 機構** が context_snapshot を比較し、ステートが進んでいたら破棄。HUD には「PokerRL THINKING...」を表示。

注記: 上記Tier設計はHU postflop（PokerRL+GRPOモデル推論）向けである。
Multiway postflopはTier体系に含まず、以下の独立したレイテンシ設計を持つ:
  - Context Engine: <10ms（決定論的Python計算）
  - LLM判断（API）: 1-3秒（GPT-5.4-mini）
  - LLM判断（ローカル）: 50-300ms（Phi-4-mini、SFT後に検証）

---

## 3. データ準備とプロンプト設計

### 3.1 訓練データセット

#### 3.1.1 PokerBench (主) — 560k rows

- ソース: `huggingface.co/datasets/RZ412/PokerBench`
- preflop 60k + postflop 500k
- フィールド: `instruction`（シナリオ）/ `output`（アクション+サイジング）
- 既知の制約: **postflop 500kは100% HU。multiway局面は0件。**
- 保存先: `C:\dev\pokerrl-training\data\pokerbench\`（8ファイル取得済み）

#### 3.1.2 Pluribus (PHH) 10,000ハンド (副)

- ソース: `github.com/uoftcprg/phh-dataset`
- 10,000ハンドのPHH形式
- multiway postflop: flop到達5,338ハンドのうち549件（10.28%）が3-way以上
- 保存先: `C:\dev\pokerrl-training\data\phh-dataset\`（取得済み）

#### 3.1.3 phh-dataset multiway抽出 (Layer 2) — 2,090万 decision points

- ソース: phh-dataset 21,606,087ハンドから抽出済み
- multiway postflop decision points: 20,915,640件
  - 3-way: 11,407,661件
  - 4-way: 2,992,676件
  - 5-way以上: 726,443件
- hole cards付き: 726,570件（3.47%）
- Street別: flop 11,692,440 / turn 5,835,491 / river 3,387,709
- 保存先:
  - `C:\dev\pokerrl-training\data\multiway_raw\multiway_decisions.jsonl` (16.23 GB)
  - `C:\dev\pokerrl-training\data\multiway_raw\multiway_hands.jsonl` (2.07 GB)

**★ 重要: hole cards付き726,570件は全件net_result ≤ 0（敗者または引き分け）。勝者0件。**
PHHフォーマットの仕様上、showdown敗者のhole cardsのみが記録され、勝者のhole cardsは記録されない。
このデータをpositive example（良い行動の教師ラベル）として使用してはならない。
詳細: DESIGN_NOTES §53

#### 3.1.4 Layer 3（PokerKit合成データ）— 不要と判定

phh-datasetのmultiway抽出で十分な量が確保できたため、
PokerKitによる合成データ生成（当初計画のLayer 3）は不要と判定した。

#### 3.1.5 Multiway データ設計方針（★ v1.3で全面改訂 ★）

**旧方針（v1.2、破棄）:**
v1.2では「hole cards付き72万件から勝者行動を優先選別し、confidence-weighted SFTでmultiway訓練データを作成する」計画だった。
しかし§3.1.3の通り、hole cards付きデータ全件が敗者であることが判明し、この計画は構造的に成立しない。

**新方針（v1.3）:**
Multiway postflop判断にはSFT/重み付け訓練を使用しない。
代わりにPokerSkill式Context Engine + LLM比較テストを採用する。

```text
第1層: Context Engine（決定論的Pythonスクリプト、訓練不要）
  - board texture分類
  - 23 hand class分類（PokerSkill Appendix E準拠）
  - SPR bucket計算
  - ATT/DEF budget計算
  - Pressure weight累積
  - MW修正子（追加プレイヤーによるATT/DEF調整）

第2層: LLM判断（制約された行動空間内）
  - GPT-5.4-mini（API）またはPhi-4-mini（ローカル）
  - Context Engineが生成した構造化プロンプトを入力
  - ATT/DEF残予算で行動を制約
```

**HUデータ設計（変更なし）:**
- PokerBench HU 500k + preflop 63.2k = 563,200件でSFT（継続中）
- active player数タグ付けはHU SFTデータには適用しない

**phh-dataset multiway 2,090万件の扱い:**
- positive exampleとしてのSFT訓練には使用しない
- Phase 0-2のテストスポット抽出源として使用する
- 将来的にaction history理解・opponent prior補助学習に限定使用を検討可能
- 敗者データのKTO undesirable利用は将来検討事項として保留

設計根拠: DESIGN_NOTES §53（敗者バイアス）、§54（PokerSkill論文）、§55（MW方針転換）
SPEC準拠: SPEC.md v3.6 §9.4

#### 3.1.6 既存システムからの再生データ (追加)

- 既存 SQLite + JSON replay に蓄積されたユーザー実戦ハンド
- 評価とファインチューニング用（ただし量は少ない見込み）

### 3.2 プロンプト設計（推論時 vs 訓練時で分離）

#### 3.2.1 訓練時プロンプト（reasoning あり、品質重視）

```
[SYSTEM]
You are a 6-max NLHE expert specializing in cash games (100bb deep).
Generate reasoning, then output the action and sizing.

[USER]
Position: {hero_pos}    Stack: {hero_stack}bb
Hole: {hole_cards}      Pot: {pot}bb
Board: {board}          SPR: {spr_bucket}
Texture: {board_texture}
HandClass: {hand_class}
History: {action_history_compressed}
Players: {active_players}
LegalActions: {legal_actions}
ATT/DEF: {att_def_budget}

[ASSISTANT]
<think>
{reasoning trace from PokerBench solver output explanation}
</think>
<answer>{action} {sizing}</answer>
```

#### 3.2.2 推論時プロンプト（reasoning なし、速度重視）

```
[SYSTEM] (prefix cached, never re-encoded)
You are a 6-max NLHE GTO recommendation engine. Output action only.

[USER] (only this part is re-encoded each call)
P:{hero_pos} S:{hero_stack} H:{hole_cards} 
Pot:{pot} B:{board} SPR:{spr}
Tx:{texture} HC:{hand_class}
Hist:{history} Active:{n_players} Legal:{legal}

[ASSISTANT]
```

**重要**: 推論時は autoregressive 生成を**しない**。最終トークンの hidden state を直接抽出して補助ヘッドに渡す。

#### 3.2.3 PokerSkill 風 Context Engine ラベル

既存 SPEC.md で確認した GameState から、以下のラベルを Python 側で事前計算してプロンプトに埋め込む:

- **board_texture**: dry / dynamic / wet / monotone / paired / connected
- **hand_class**: overpair / top_pair / middle_pair / draw / air / nutted etc.
- **spr_bucket**: low (<3) / medium (3-10) / deep (>10)
- **att_def_budget**: PokerSkill 流の累積圧力指標（実装は §3.3.5 参照）

### 3.3 既存 GameState からのプロンプト構築

新規追加: `strategy/pokerrl_prompt_builder.py`

```python
class PromptBuilder:
    def build_inference_prompt(self, game_state: GameState) -> str:
        # 1. 必須フィールド取り出し
        # 2. Context Engine ラベル計算
        # 3. Action history を圧縮（直近 5 actions）
        # 4. 文字列フォーマット
        return prompt_text
```

入力は既存 SPEC.md §3.x の `GameState` オブジェクトそのまま。新規ラベル計算ロジックのみ追加。

---

## 4. モデル選定（Sprint 1で確定済み）

### 4.1 選定結果

**Phi-4-mini-instruct 3.8B をプライマリモデルとして正式採用。**

Sprint 1で以下の候補を評価した:

| モデル | 評価結果 | 状態 |
|---|---|---|
| **Phi-4-mini-instruct 3.8B** | 10k SFT action_accuracy 65.6%, eval_loss 0.326, 18.4h完走, VRAM 9,815 MiB | **確定採用** |
| Qwen3.5-4B | QLoRA batch=2で13,120 MiB。RTX 3080 10GBで不可 | VRAM超過で脱落 |
| Gemma 4 E2B (5.44B) | batch=1で1 step 3分超。完走見積り3日超。VRAM 9,917 MiB | 訓練速度不可で脱落 |
| Qwen3-4B-Instruct-2507 | 未検証 | 予備候補（Phi-4-miniで問題発生時のみ） |

### 4.2 Phi-4-mini 10k SFT Go/No-go結果

| 基準 | Go条件 | 実測値 | 判定 |
|---|---|---|---|
| action_accuracy | ≥ 40% | 65.6% | **合格** |
| eval_loss | 収束傾向 | 0.326（単調減少） | **合格** |
| VRAM | ≤ 10,000 MiB | 9,815 MiB | **合格** |
| エラー | NaN/OOMなし | lossスパイク4回（自然回復） | **合格** |

学習曲線:

| step | eval_loss | action_accuracy | perplexity |
|---|---|---|---|
| 100 | 0.638 | 53.9% | 1.893 |
| 200 | 0.537 | 56.2% | 1.711 |
| 300 | 0.481 | 57.0% | 1.618 |
| 400 | 0.426 | 55.4% | 1.531 |
| 500 | 0.385 | 61.6% | 1.469 |
| 600 | 0.358 | 63.8% | 1.431 |
| 700 | 0.352 | 64.0% | 1.422 |
| 800 | 0.333 | 65.2% | 1.395 |
| 900 | 0.326 | 65.5% | 1.386 |
| 939 | 0.326 | 65.6% | 1.385 |

### 4.3 10k SFT訓練設定（Sprint 2本番SFTのベースライン）

| 設定 | 値 |
|---|---|
| ベースモデル | microsoft/Phi-4-mini-instruct |
| 量子化 | QLoRA 4-bit NF4 (bfloat16 compute) |
| LoRA | r=32, alpha=32, target_modules=all-linear, dropout=0.1 |
| Optimizer | paged_adamw_8bit |
| Scheduler | cosine |
| 精度 | bf16=True |
| LR | 1e-4 |
| Batch | 4 (gradient_accumulation=8, effective=32) |
| max_seq_len | 1024 |
| Epochs | 3 |

注記: v1.1ではLoRA r=64, alpha=128を記載していたが、
poker_rl作者（dcaustin33）の設定およびSprint 1の実績に基づきr=32, alpha=32に修正した。

### 4.4 モデル選定の Go/No-go プロセス（完了）

Sprint 1で完了。比較レポート: `C:\dev\pokerrl-training\results\sft_comparison\comparison_report.md`

---

## 5. 訓練パイプライン（snapshot.md の SFT + GRPO 二段階）

### 5.1 訓練リポジトリ分離原則

訓練は `C:\dev\pokerrl-training`（Sprint 1で作成済み）で管理する。
環境: Python .venv, torch 2.12.0+cu130, CUDA 13.0, bf16対応確認済み。

理由:
- snapshot.md と DESIGN_NOTES.md が確立した分離原則
- 訓練と推論で依存ライブラリが大きく異なる
- Phase C で確立した encode_game_state 同期パターンを継承

### 5.2 Phase 1: Supervised Fine-Tuning

```
入力: PokerBench 560k + Pluribus 60k 統合データセット
ベース: Phi-4-mini-instruct 3.8B (ダウンロード済み: models/phi-4-mini-instruct/)
方式: QLoRA (4-bit nf4, bfloat16 compute) + LoRA r=32, alpha=32, all-linear, dropout=0.1
ハードウェア: RTX 3080 (10GB)
時間: 約 40-60 時間
損失: 言語モデリング loss + 0.5 × action head CE + 0.2 × sizing MSE
出力: LoRA アダプタ (~150MB)
Go/No-go (Phase 1 終了時):
  - PokerBench Preflop accuracy ≥ 70%
  - PokerBench Postflop accuracy ≥ 55%
  - 6-max self-play で「Raise 70-80% 偏重」がないことを spot check で確認
```

### 5.3 Phase 2: GRPO 強化学習

```
入力: Phase 1 SFT モデル
方式: GRPO + DAPO trick + OPEFO entropy 制御
環境: PokerKit ベースの 6-max NLHE 自己対戦
opponents:
  - 過去 SFT checkpoint (population play)
  - Rule-based (TAG/LAG)
  - 既存 Deep CFR 失敗モデル（弱い相手としてエントロピー多様性確保）
時間: 約 80-120 時間
報酬:
  - 0.7 × 即時 chip delta
  - + 0.2 × EV at decision (eval7 計算)
  - + 0.1 × 直近20ハンド累積 (bankroll preservation)
Go/No-go (Phase 2 終了時):
  - Spot checks: 全 50 局面で行動分布が合理的に変動
  - Entropy 健全 (top-1 確率の中央値 < 0.85)
  - Slumbot 相手（HU、無料 API）で勝率 ≥ -15 bb/100
  - 自己対戦で Phase 1 ベースラインに +3 bb/100 以上
```

### 5.4 量子化と推論最適化

訓練後、本リポジトリで使うために以下を作成:

```
Phase 2 LoRA アダプタ → ベースモデルにマージ
                       ↓
                  AWQ 4-bit 量子化 (vLLM 用)
                  または GGUF Q4_K_M (llama.cpp 用)
                       ↓
            poker-assistant/models/pokerrl/final_quantized/
```

### 5.5 既存 encode との同期検証

Phase C で確立した `verify_encode.py` のパターンを継承し、新規 `verify_pokerrl_encode.py` を作成:

```
訓練側プロンプト構築コード   ←→   推論側プロンプト構築コード
              ↓                          ↓
        同一 GameState を流す
              ↓                          ↓
        生成されるプロンプト文字列を比較
              ↓
        完全一致しなければ FAIL
```

---

## 6. 推論ブリッジ統合（既存 deep_cfr_bridge の置き換え）

### 6.1 共存期間中のルーティング

実装中は **新旧両方が動く**よう、以下の段階移行:

```
[Stage A] (実装開始～Phase 1 完了)
  recommendation_engine.py:
    HU/Multiway postflop → Deep CFR Bridge (既存、失敗品質)
                          + PokerRL Bridge (新、テスト用、HUD 非表示)
  → 両方の出力をログ保存、推奨表示は既存 Deep CFR のまま

[Stage B] (Phase 1 完了～Phase 2 完了)
  PokerRL Bridge を shadow mode で稼働
  spot checks をリアルプレイで蓄積
  → まだ Deep CFR 表示

[Stage C] (Phase 2 完了後)
  HU postflop → PokerRL Bridge (主)
                + Deep CFR Bridge (フォールバック)
  Multiway postflop → PokerRL Bridge (主)
                      + eval7 + LLM exploit (既存)
  → 切り替え後、Deep CFR は 1 ヶ月間フォールバック保持

[Stage D] (品質安定後)
  Deep CFR Bridge と Rust Solver を削除
```

### 6.2 ブリッジ I/F の厳格な仕様

```python
# strategy/pokerrl_bridge.py

class PokerRLBridge:
    """既存 DeepCFRBridge と完全に同じ I/F を提供"""
    
    def __init__(self, config: dict):
        # vLLM または llama.cpp の常駐推論プロセスを起動
        # ベースモデル + LoRA アダプタを VRAM にロード
        # KV cache warm-up
        # 補助ヘッド (action/sizing) を別途ロード
        ...
    
    def infer(
        self,
        game_state: GameState,
        request_id: str,
        context_snapshot: ContextSnapshot,
    ) -> Optional[RecommendationResult]:
        """
        既存 deep_cfr_bridge.py と同じ I/F:
        - game_state を受け取る
        - request_id でトラッキング
        - context_snapshot を返却値に添付
        - 入力不安定なら None を返す
        - 推論失敗時は None を返す
        - 既存 GameLoop が context_snapshot を validate
        """
        # 1. プロンプト構築
        prompt = PromptBuilder().build_inference_prompt(game_state)
        
        # 2. 推論実行 (timeout 付き)
        try:
            with timeout(self.config['inference_timeout_ms']):
                hidden_state = self.engine.encode(prompt)
                action_logits = self.action_head(hidden_state)
                sizing_logit = self.sizing_head(hidden_state)
        except TimeoutError:
            return None
        
        # 3. 出力パース
        recommendation = self.parser.parse(
            action_logits, sizing_logit, game_state.legal_actions
        )
        
        # 4. context_snapshot を添付して返却
        recommendation.request_id = request_id
        recommendation.context_snapshot = context_snapshot
        recommendation.source = 'PokerRL+GRPO'  # HUD で表示
        recommendation.confidence = self._compute_confidence(action_logits)
        
        return recommendation
    
    def reset(self):
        """既存 solver_bridge.py と同じく、プロセスリセット用"""
        # KV cache クリア
        # 必要なら推論プロセス再起動
        ...
```

### 6.3 既存ガードレールとの統合

`recommendation_engine.py` での呼び出し前に、既存システムが以下を保証している:

- Hero cards stable (2 frames 連続一致)
- Pot spike hold 解除済み
- Visual obstruction なし
- Hero is_my_turn = True
- Board count と phase が整合
- Active player count 確定

**これらが既に揃った上で呼ばれる**ので、新ブリッジ側で再チェックは不要。ただし「異常な値」（例: pot=0 で flop など論理矛盾）が来た場合は None を返して既存フォールバックに任せる。

### 6.4 Stale 検出の継承

新ブリッジは context_snapshot を結果に**ただ添付するだけ**。validate するのは既存 GameLoop の責務。これにより既存の stale 検出機構がそのまま機能する。

---

## 7. HUD 表示仕様（既存原則準拠）

### 7.1 ソースラベル

既存 SPEC.md §x の HUD ラベリングルールに従う:

| 状態 | HUD 表示 |
|---|---|
| Preflop chart hit | `Source: Chart` |
| HU postflop, PokerRL 単独 | `Source: PokerRL` |
| HU postflop, LLM exploit 適用後 | `Source: PokerRL+` (cyan、既存 Deep CFR+ と同様) |
| Multiway, PokerRL + eval7 mathematical guard | `Source: PokerRL+Guard` |
| Multiway, PokerRL + LLM exploit | `Source: PokerRL+` |
| Fallback to Deep CFR | `Source: DeepCFR` (deprecated 印付き) |
| Fallback to Rust Solver | `Source: Solver` (deprecated 印付き) |

### 7.2 ステータスメッセージ

既存原則の通り、計算中は推奨を表示せずステータスのみ:

| 状態 | ステータスメッセージ |
|---|---|
| T1 推論中 (< 300ms) | (表示不要、十分速い) |
| T2 推論中 (1-5s) | `POKERRL THINKING...` |
| T3 推論中 (5-12s) | `POKERRL DEEP THINKING...` |
| LLM exploit 追加中 | `EXPLOIT ADJUSTING...` |
| Stale で破棄 | （既存通り、次フレームで自動更新） |
| 推論失敗、フォールバック中 | `POKERRL FALLBACK...` |

### 7.3 Action 確率と Sizing の表示形式

既存 Deep CFR 表示と同じ:

```
RAISE 72% / CALL 25% / FOLD 3%
Sizing: 1.5x pot (≈ 4.8 BB)
Source: PokerRL+
Confidence: High (top action 72%)
```

Confidence は既存ルール踏襲:
- top action ≥ 70% → High
- top action ≥ 45% → Medium
- top action < 45% → Low

---

## 8. 評価とテスト（Deep CFR 失敗の教訓を活かす）

### 8.1 「Profit vs Random は信用不可」原則

snapshot.md で確認された通り、profit vs random = 46.07 でも実戦不可だった事例があるため、**この指標は単独では使わない**。

### 8.2 必須評価指標

| 指標 | 内容 | Phase 1 閾値 | Phase 2 閾値 |
|---|---|---|---|
| **Spot Checks** | 50 シナリオで action 分布が合理的に変動 | 80% 合格 | 95% 合格 |
| **Entropy** | top-1 action 確率の中央値 | ≤ 0.90 | ≤ 0.85 |
| **Sensitivity Tests** | hand 強度・board texture・position・facing action に対する反応性 | 70% pass | 90% pass |
| **PokerBench Accuracy** | 11k テストでの action accuracy | preflop ≥ 70%, postflop ≥ 55% | preflop ≥ 75%, postflop ≥ 60% |
| **Slumbot HU 勝率** | 1000 hands、無料 API | N/A | ≥ -15 bb/100 |
| **Self-play vs Phase 1** | 5000 hands、6-max | N/A | ≥ +3 bb/100 |
| **Latency P95** | 各 Tier 別の実測 | T1 ≤ 300ms | T1 ≤ 200ms |
| **Hard Deadline 超過率** | 既存タイマー内に降伏できる割合 | < 5% | < 1% |

### 8.3 Spot Checks の具体例

snapshot.md で問題視された「Ace high・no pair・no draw・3way facing BET で Raise 80%」のような病理を捕捉する 50 局面を作る:

```
spot_001: 3way flop, hero AhKd (overcards no pair),
          board 7s 4c 2h, facing BET 30%pot,
          期待: Fold or Call majority, Raise ≤ 20%

spot_002: HU turn, hero AsAh (set),
          board As 4d 7c Jh, facing BET 50%pot,
          期待: Raise majority

... (48 more)
```

これらは Phase 1 完了時点で自動回帰テストとして組み込む。

### 8.4 既存テストスイートとの統合

既存 1441 tests に加え、以下を新規追加:

- `tests/test_pokerrl_bridge.py`: ブリッジ I/F
- `tests/test_pokerrl_prompt_builder.py`: プロンプト構築
- `tests/test_pokerrl_output_parser.py`: モデル出力パース
- `tests/test_pokerrl_spot_classifier.py`: 局面分類
- `tests/test_verify_pokerrl_encode.py`: 訓練側との一致
- `tests/test_pokerrl_spot_checks.py`: 50 spot 自動回帰

最終的に `pytest -q` で**全テスト pass**を維持。

### 8.5 シャドウモード評価

Stage B (Phase 1 完了～Phase 2 完了) の間、新ブリッジを **shadow mode** で稼働:

- 実プレイ中、既存 Deep CFR が表示される
- 同時に新 PokerRL ブリッジも推論を実行
- 両者の出力をログ保存
- 差分が大きい局面を人間レビュー
- 「実プレイで遭遇する局面分布」での品質を測定

---

## 9. リスク管理

### 9.1 主要リスクと対策

| リスク | 影響 | 対策 |
|---|---|---|
| <50ms 達成失敗 | T1 が機能しない | T2/T3 で吸収。既存原則「Quality over Speed」で許容 |
| Entropy collapse 再発 | Deep CFR と同じ失敗 | DAPO + OPEFO 必須、Spot Checks で早期検出 |
| Multiway データ不足 | 6-max で性能出ない | phh-dataset 2,090万multiway decision points抽出済み。Layer 3不要。active player数分布管理で対応 |
| 既存システム破壊 | 全機能停止 | Stage A/B/C の段階移行、Deep CFR と Rust Solver は削除しない |
| 量子化品質劣化 | accuracy 5-10% 低下 | AWQ INT8 + Q8_K も評価候補に |
| 訓練と推論の encode 不一致 | Deep CFR と同じ罠 | verify_pokerrl_encode.py で常時監視 |
| LLM exploit との競合 | HUD で source 混乱 | Stage C で source 表示ルール明確化 |


## §9.2 失敗時の段階的対処と撤退基準

### §9.2.0 全体方針

Deep CFR で約1ヶ月を浪費した教訓を踏まえ、本プロジェクトでは **「失敗を早期検知し、明確な基準で撤退する」** を原則とする。具体的には以下の3層構造で対処する:

```
[Layer 1] 各 Sprint 内での即時改善 (1-3日サイクル)
   ↓ 改善不能なら
[Layer 2] Sprint 間でのアプローチ調整 (3-7日サイクル)
   ↓ 改善不能なら
[Layer 3] アプローチ全体の撤退 → 代替案へ移行
```

各層に **明確な期間上限と判断基準**を設けることで、「もう少しやれば改善するかも」という Deep CFR 時の過剰投資を防ぐ。

**全体タイムボックス**: PokerRL+GRPO アプローチに投入する総期間の上限は **12週間 (Sprint 1-3 の合計上限)** とする。それを超えた場合は §9.2.5 の撤退判断を発動する。

---

### §9.2.1 Phase 1 SFT 閾値未達時の段階的対処

**発動条件**: Sprint 2 完了時に以下のいずれかを満たさない:
- PokerBench Preflop accuracy ≥ 70%
- PokerBench Postflop accuracy ≥ 55%
- Spot Checks の合格率 ≥ 80%
- 「全局面で Raise 70-80%」のような病理的偏りがゼロ

**改善ステップ** (順番に試す、各ステップで判定):

#### Step 1: 補助ヘッド構造の修正（最大 3日間）

**前提**: モデル本体ではなく補助ヘッドの設計が原因のケースが最も多い。

| 試行 | 変更内容 | 判定基準 |
|---|---|---|
| 1a | Sizing Head を scalar (sigmoid) から **categorical (8-bin: 0.33x/0.5x/0.66x/1.0x/1.5x/2.0x/3.0x/all-in)** に変更 | sizing 多様性回復 |
| 1b | Action Head を 2層 MLP から **3層 MLP (4096→2048→512→4)** に拡張 | Action 分布の鋭さ改善 |
| 1c | Action Head に **dropout 0.1-0.2** を追加 | 過学習指標 (train/val gap) 縮小 |
| 1d | Action token の **重み付き CE loss**（Fold/Call/Raise/All-in を 1.5/1.0/1.0/0.8 に重み付け）でクラス不均衡対策 | Fold 頻度が現実的水準に回復 |

**Go/No-go**: ここまでで Postflop accuracy ≥ 55% 達成なら Phase 1 完了。未達なら Step 2 へ。

#### Step 2: LoRA ハイパラ調整（最大 2日間）

**重要原則** (検索結果より): 「LR と rank を変えるほうが alpha を変えるより効く」。

| 試行 | 変更内容 | 判定基準 |
|---|---|---|
| 2a | LoRA rank **r=64 → r=128**, alpha=256 (rank ≤ alpha 原則維持) | val loss 改善 |
| 2b | Learning rate **2e-4 → 1e-4**, warmup ratio 10% → 15% | train loss 安定化 |
| 2c | target_modules を attention のみから **MLP 層含む全 linear** に拡張 | accuracy 向上 |
| 2d | epochs を 3 → 5 に延長 | overfit せず accuracy 向上するか |

**Go/No-go**: ここまでで Postflop accuracy ≥ 55% 達成なら完了。未達なら Step 3 へ。

#### Step 3: データ前処理の見直し（最大 4日間）

**仮説**: モデル能力ではなくデータ品質の問題。

| 試行 | 変更内容 | 判定基準 |
|---|---|---|
| 3a | PokerBench: Pluribus 比率を **9:1 → 7:3** に変更（multiway 強化） | multiway spot accuracy 改善 |
| 3b | Action history の圧縮を「直近5」から「直近10」に拡張 | Turn/River accuracy 改善 |
| 3c | プロンプトに **eval7 計算済み equity 値** を明示的に追加 | equity 依存判断の accuracy 改善 |
| 3d | PokerBench の **低信頼度サンプル**（solver iteration 不足のもの）をフィルタリング | val accuracy 改善 |
| 3e | Pluribus データに人為的な **board texture 多様化**（同じハンドの suit swap）でデータ拡張 | board テクスチャ感度向上 |

**Go/No-go**: ここまでで未達なら Step 4 へ。

#### Step 4: ベースモデル切替（最大 5日間）

**前提**: モデル能力の根本的不足の可能性。

| 試行 | 変更内容 | 判定基準 |
|---|---|---|
| 4a | 第1選択 Phi-4-mini 3.8B → 第2選択 **Qwen3-4B** に変更 | accuracy が 5% 以上改善 |
| 4b | 4a でも未達なら **Gemma 3-4B (QAT 版)** に変更 | 同上 |
| 4c | 4b でも未達なら **Qwen3-8B (Q4 QLoRA、VRAM ギリギリ)** に切替 | 同上 |

**Step 4 への切替判断基準**: Step 1-3 を尽くしても Postflop accuracy < 50% の場合、Step 4 へ。Step 4 で1モデルあたり最大 60時間 SFT。

**最終 Go/No-go**: Step 1-4 を全て尽くしても閾値未達なら、**§9.2.5 撤退判断**を発動。Phase 1 全体の上限は **5週間** (Sprint 2 標準 3週間 + 改善 2週間)。

---

### §9.2.2 Phase 2 GRPO 品質未達時の段階的対処

**発動条件**: Sprint 3 完了時に以下のいずれかを満たさない:
- Spot Checks 95% 合格
- Slumbot HU 勝率 ≥ -15 bb/100
- Self-play vs Phase 1 baseline で +3 bb/100
- Entropy 健全 (top-1 確率中央値 ≤ 0.85)

#### Step 1: Entropy 崩壊への対処（最大 4日間）

**発動条件**: 訓練中に top-1 確率中央値 > 0.85 を観測。

| 試行 | 変更内容 | 標準値 → 調整範囲 |
|---|---|---|
| 1a | **DAPO Clip Higher の ε_high 拡大** | 0.28 → 0.30 → 0.32 |
| 1b | **Entropy bonus 係数を明示的に増加** | 0.0 (DAPO) → 0.001 → 0.005 → 0.01 |
| 1c | **KL coefficient を導入**（DAPO で omit していたなら復活） | 0.0 → 0.001 → 0.005 |
| 1d | OPEFO の **balancing coefficient λ\* の上限** を導入 | unlimited → max ±0.5 |
| 1e | **Generation temperature を訓練時に増加** | 1.0 → 1.2 → 1.5 |
| 1f | Dynamic Sampling で **zero-variance filter の閾値を緩める** | 0% reward variance → 5% 未満を除外 |

**Go/No-go**: top-1 確率中央値が 0.85 以下に戻れば Step 2 へ。3日試して戻らなければ Step 4 へ。

#### Step 2: 対戦相手プール構成の見直し（最大 3日間）

**仮説**: opponents の構成が偏り、特定戦略への過剰最適化を招いている。

| 試行 | 変更内容 |
|---|---|
| 2a | Population play に **過去全 SFT checkpoint 8体** を等確率で含める（現状は最新3体） |
| 2b | Rule-based opponent に **TAG / LAG / Tight-Passive / Maniac の4種** を混合 |
| 2c | **既存 Deep CFR 失敗モデル**を「弱い相手」として 20% 混入（多様性確保用） |
| 2d | **自己の最新版とのみ対戦**するフェーズと、**混合プールとの対戦**フェーズを **2:1 で交互**に実施 |
| 2e | **PokerSkill 風 rule-based GTO bot** を opponent として追加（PokerBench solver 出力を確率分布として直接サンプリング） |

**Go/No-go**: Self-play で entropy 維持されつつ Phase 1 baseline +3 bb/100 を達成すれば Step 3 へ。

#### Step 3: 報酬関数の調整（最大 4日間）

**現状の標準報酬** (本指令書 §5.3):
```
reward = 0.7 × instant_chip_delta
       + 0.2 × ev_at_decision
       + 0.1 × multi_hand_running_total
```

| 試行 | 変更内容 |
|---|---|
| 3a | **chip_delta 重みを 0.7 → 0.5** に下げ、**EV 項を 0.2 → 0.4** に上げる |
| 3b | EV 項の計算を eval7 単純から **postflop-solver 出力ベース**に変更（重い局面のみ） |
| 3c | **「敗北回避ボーナス」**を追加: ALL_IN を打って負けた場合に追加ペナルティ -0.5 |
| 3d | **「妥当性ペナルティ」**を追加: GTO solver 出力との KL divergence が大きいアクションに -0.1 (実質的な soft imitation regularization) |
| 3e | **「Fold 不足ペナルティ」**: 直近100ハンドで Fold 率 < 15% なら追加ペナルティ |

**Go/No-go**: Slumbot 勝率改善が見られれば Step 4 へ進まず終了。

#### Step 4: 訓練期間延長と判断（最大 5日間）

**判断基準**:

- 過去 24時間の評価指標が **改善トレンドを示しているか** を確認
  - 改善中（直近2チェックポイントで指標が単調改善）→ +3日延長
  - 横ばい（指標が ±3% 内）→ Step 5 へ
  - 悪化→ 即座に Step 5 へ
- 延長は **最大2回まで**、合計 +6日

**Go/No-go**: +6日延長しても Slumbot ≥ -15 bb/100 を達成しなければ、**§9.2.5 撤退判断**へ。

---

### §9.2.3 Spot Checks で病理パターン検出時の対処

**発動条件**: 50 spot checks のうち、**特定の病理パターン**が検出された場合（accuracy 数値とは別に独立で発動）。

#### 病理パターンと診断

| 病理 | 症状 | 第1疑い |
|---|---|---|
| **Raise 偏重** | 全局面で Raise > 60% | 訓練データの multiway 不足 or Reward の chip_delta 過重 |
| **Fold 偏重** | 全局面で Fold > 50% | Entropy collapse 反対方向 or EV 項過重 |
| **Sizing 固定** | Bet size が 95% 以上同じ値 | Sizing Head の出力分布崩壊 |
| **Position 無感度** | UTG と BTN で同じ戦略 | Position 入力の encode 不一致 |
| **Board texture 無感度** | Dry/Wet で同じ aggression | Board feature が prompt から欠落 or 訓練不足 |
| **Stack depth 無感度** | 25bb と 200bb で同じ戦略 | SPR の normalize ミス |

#### 切り分け手順（順番厳守）

**Step 1: verify_pokerrl_encode.py を再実行**（数時間）

- 訓練側 vs 推論側のプロンプト差分を厳密確認
- **完全一致でなければ即座に推論側 encode を修正**
- Deep CFR で起きた `encode_game_state` 不一致と同じ罠を回避

**Step 2: 推論時 vs 訓練時の同一 input 出力比較**（半日）

- 訓練データから 100 サンプルを抜き出し、訓練側モデルと推論側モデルで予測
- 不一致 > 5% なら **量子化または LoRA マージ過程に問題** → §9.2.4 へ

**Step 3: 病理に特化したデータ拡張**（2-3日）

| 病理 | 対処データ拡張 |
|---|---|
| Raise 偏重 | PokerBench から **「Fold が GTO の局面」を 5倍 oversampling** して追加 SFT |
| Fold 偏重 | **「Raise が GTO の局面」を 3倍 oversampling** |
| Sizing 固定 | **異なる sizing の局面**を Sizing Head 重み 0.5 で追加訓練 |
| Position 無感度 | Position を変えただけのペア data を生成、対比学習 |
| Board 無感度 | 同じハンドで board texture のみ変えたペア data を生成 |
| Stack 無感度 | 同じ局面で stack depth のみ変えたペア data を生成 |

**Step 4: カリキュラム学習の導入**（4-5日）

通常 SFT で改善しない場合:

- 簡単な局面 → 難しい局面の順に SFT
- 段階1: HU postflop river（最も明確）
- 段階2: HU postflop turn
- 段階3: HU postflop flop
- 段階4: Multiway postflop
- 段階5: 全局面混合

**Go/No-go**: Step 1-4 を尽くしても 95% spot checks 達成しなければ §9.2.2 Step 4 に戻り、訓練を継続。それでも未達なら §9.2.5 撤退判断。

---

### §9.2.4 量子化による品質劣化への対処

**発動条件**: 量子化後の Postflop accuracy が **量子化前から 10% 以上低下**。

検索情報によれば QLoRA は通常 95-98% の性能を維持するため、**10% 以上の低下は異常事態**。原因切り分けと対処:

#### Step 1: 量子化レベルの段階的緩和（1日）

```
試行順:
  Q4_K_M (現状) → Q5_K_M → Q6_K → Q8_0 → FP16
判定: 
  各レベルで PokerBench テスト accuracy を測定
  10% 低下が解消する最も小さいレベルを採用
```

| レベル | VRAM (Phi-4-mini 3.8B) | 想定 accuracy 損失 |
|---|---|---|
| Q4_K_M | ~2.5 GB | 通常 2-5% |
| Q5_K_M | ~2.9 GB | 通常 1-3% |
| Q6_K | ~3.3 GB | 通常 < 1% |
| Q8_0 | ~4.1 GB | 通常 < 0.5% |
| FP16 | ~7.6 GB | 0% |

RTX 3080 (10GB) は **FP16 まで全て収まる**。VRAM 余裕がある場合は迷わず Q8_0 または FP16 を選択。

#### Step 2: レイテンシ予算の見直し（半日）

量子化緩和でレイテンシが目標を超える場合、**§2.2 の Tier 予算を以下のように緩和**:

```
旧 (Q4_K_M 前提):
  T1 (Quick LLM): 30-300ms
  T2 (LLM + Tool): 1-5秒
  T3 (LLM + Search): 5-12秒

新 (Q8_0 or FP16):
  T1 (Quick LLM): 100-500ms ← <50ms 目標は諦める
  T2 (LLM + Tool): 1-5秒    ← 変更なし
  T3 (LLM + Search): 5-12秒  ← 変更なし
```

**判断基準**: SPEC.md §0.1 「Quality over Speed」原則により、accuracy 10% 改善は latency 200ms 増加に値する。Tier 1 が 500ms 以内に収まれば許容。

#### Step 3: モデルサイズダウン（2-3日）

それでも accuracy が出ない場合:

| 試行 | 内容 | VRAM @ FP16 | 想定 latency |
|---|---|---|---|
| 3a | Phi-4-mini 3.8B → **SmolLM3-3B** に変更、FP16 で再 SFT | ~6 GB | T1 ~200ms |
| 3b | 3a でも不足なら **Qwen3-1.7B** へ。FP16 だと VRAM 余裕 | ~3.4 GB | T1 ~150ms |

**注意**: モデルダウンサイズは accuracy 上限も下がるため、Phase 1 から再訓練が必要。最大 1週間追加。

**最終 Go/No-go**: モデルダウン後も accuracy 未達なら §9.2.5 撤退へ。

---

### §9.2.5 アプローチ全体の撤退基準

PokerRL+GRPO 全体の不合格判断は、**以下のいずれか1つでも満たした時点で発動**する:

#### 撤退発動条件（OR 条件）

1. **タイムボックス超過**:
   - Sprint 1-3 の合計が **12週間を超過**
   - 補正期間 (§9.2.1-§9.2.4 の延長) を含めて **15週間を超過**

2. **品質下限未達**:
   - §9.2.1-§9.2.3 の全 Step を尽くしても以下のいずれかを満たさない:
     - PokerBench Postflop accuracy ≥ 50% (絶対下限)
     - Slumbot HU 勝率 ≥ -30 bb/100 (絶対下限)
     - Spot Checks 80% 合格 (絶対下限)
     - 「Raise 偏重」「Fold 偏重」のいずれかの病理が改善しない

3. **改善トレンド消失**:
   - 直近 **2週間で評価指標が ±5% 内**で横ばい
   - かつ、§9.2.1-§9.2.4 で試せる対処がすべて消化済み

4. **コスト超過**:
   - 電気代以外のコスト（クラウド GPU 等）が **$500 を超過**
   - これは予期せぬ訓練長期化の signal

#### 撤退判断のタイミング

撤退判断は **Sprint 終了時のみ**ではなく、**Sprint 中でも以下の中間点で判断**:

| タイミング | 判断内容 |
|---|---|
| Sprint 2 開始から **2週間時点** | §9.2.1 Step 1-2 で改善見込みあるか |
| Sprint 2 開始から **4週間時点** | §9.2.1 Step 3-4 全て試したか、Phase 1 撤退判断 |
| Sprint 3 開始から **2週間時点** | §9.2.2 Step 1-2 で entropy 健全化したか |
| Sprint 3 開始から **5週間時点** | §9.2.2 Step 3-4 全て試したか、Phase 2 撤退判断 |
| Sprint 1-3 合計 **12週間時点** | 全体撤退の最終判断 |

各中間点で **指令役エージェントが Go/No-go 判定**を実施し、判定結果をログに残す。

#### 撤退時の状態保持

撤退時も以下は **削除しない、必ず保持**:

- Phase 1 SFT モデル checkpoint（PokerSkill 風ハイブリッドや LLM exploit 強化版で再利用可能）
- 訓練データ前処理スクリプト（他アプローチでも流用可能）
- Spot Checks 50 シナリオと評価ハーネス（評価基盤は撤退とは無関係に有用）
- verify_pokerrl_encode.py（次アプローチでも同様の検証が必要）

---

### §9.2.6 撤退後の代替案優先順位

撤退時の状況によって最適な代替案が変わる。**SFT がどこまで成功していたか**で分岐する:

#### Case A: Phase 1 SFT は成功、Phase 2 GRPO で失敗した場合

**★ v1.3注記: Case Aの「PokerSkill風ハイブリッド」は、撤退後の代替案ではなく、
MW方針の主戦略として即時採用された（Sprint 2のS2-T2-NEW-a〜e）。
Phase 2 GRPOが成功した場合でも、MW判断にはContext Engine + LLMを使用する。
PokerRL+GRPOモデルのMW対応は将来検討事項として保留する。★**

**症状**: PokerBench accuracy ≥ 55% は達成、しかし自己対戦や Slumbot で品質出ない

**最優先**: **PokerSkill 風ハイブリッド (6人テーブル拡張)**

理由:
- SFT モデルが「教師(GTO)を模倣する能力」は獲得済み
- 強化学習で entropy 崩壊した部分を、**rule-based context engine** で補強する設計が相性良い
- 既存 LLM exploit (GPT-5.4-mini) と組み合わせる素地が既にある

実装方針:
- SFT モデルを「policy proposer」として使う
- PokerSkill 風 skill library + Context engine を Python で実装（板テクスチャ、SPR、累積 pressure 等）
- 既存 eval7 と LLM exploit を組み合わせる
- 想定期間: **4-6週間**

#### Case B: Phase 1 SFT も Postflop で 50% を下回った場合

**症状**: 教師データを学べていない、根本的な訓練問題

**最優先**: **dberweger2017 Deep CFR の改善に戻る**

理由:
- LLM ベースアプローチで PokerBench データを学習できない場合、**LLM 自体が poker 状態の構造化が苦手**な可能性が高い
- Deep CFR の数値ベース feedforward 設計のほうが向いている可能性
- ただし、Deep CFR 時の失敗 (profit vs random 過信) は繰り返さない

具体的改善方針 (Deep CFR 改善ルート):
- **対戦相手プールの強制多様化**: random 相手だけでなく PokerSkill rule-base、TAG/LAG、過去 checkpoint を混合
- **評価指標を spot checks ベースに変更** (profit vs random の罠を回避)
- **Sizing Head を categorical 化** (現状の sigmoid 連続値が偏りの原因)
- **訓練期間を長期化**せず、6週間で品質判定して再撤退
- 想定期間: **6-8週間 (上限明確化)**

#### Case C: タイムボックス超過だが他は健全

**症状**: 12-15週間を消費したが、accuracy 等は改善中

**最優先**: **GTO Wizard AI 公開 API 待ち + 暫定運用**

理由:
- GTO Wizard が「near future で researcher API 公開」を明言（既に benchmark.gtowizard.com で eval API は無料公開）
- 蒸留教師 API が出れば PokerRL を再開する条件が劇的に改善
- それまでは暫定手段で運用継続が可能

暫定運用方針:
- 現行 SFT モデル（不完全だが Deep CFR よりはマシ）を Stage B (shadow mode) で運用
- HUD は既存 LLM exploit (GPT-5.4-mini) と Preflop charts を主軸に
- Multiway は eval7 + LLM 数理ガード (既存の Stage A 構成) を継続
- 想定待機期間: **2-6ヶ月**（GTO Wizard API 公開タイミング次第）

#### Case D: 全部失敗した場合（複合的）

**症状**: SFT/GRPO/Deep CFR 改善のいずれもダメ、12週間以上経過

**最優先**: **段階的縮小と既存システムの暫定運用**

具体策:
- Preflop: 既存 GTO charts のみで運用（最も信頼できる layer）
- Multiway postflop: eval7 + LLM 数理ガード（既存 Stage A）で暫定運用
- HU postflop: 既存 Rust postflop solver (廃止予定だった) を**条件付き再活性化**
  - 「Deep SPR Flop」のみ skip
  - その他は 5-10秒以内に answer 出るので使える
- 並行調査: **MCCFVFP (NeurIPS 2024)** や **GPU-accelerated CFR** などの新興手法を 2-3ヶ月かけて再評価

#### 代替案の優先順位サマリ表

| 撤退時の状況 | 第1優先 | 第2優先 | 第3優先 |
|---|---|---|---|
| Case A: SFT 成功、GRPO 失敗 | PokerSkill風ハイブリッド | GTO Wizard API 待機 | Deep CFR 改善 |
| Case B: SFT 失敗 | Deep CFR 改善（評価刷新） | GTO Wizard API 待機 | 既存システム暫定運用 |
| Case C: タイムボックス超過、品質改善中 | GTO Wizard API 待機 | PokerSkill風ハイブリッド | Phase 1 SFT のみ shadow mode 運用 |
| Case D: 全失敗 | 既存システム暫定運用 | 新興手法 (MCCFVFP 等) 調査 | 6-12ヶ月の長期待機 |

---

### §9.2.7 撤退判断のドキュメント化

撤退判断時には以下を **必ず記録**:

```
撤退判断ログテンプレート:
  発動日:
  発動条件 (§9.2.5 のどれに該当):
  Phase 1 SFT 到達状況:
    - PokerBench Preflop accuracy:
    - PokerBench Postflop accuracy:
    - Spot Checks 合格率:
  Phase 2 GRPO 到達状況:
    - Slumbot HU 勝率:
    - Self-play vs SFT baseline:
    - Entropy 健全性:
  消化済み対処 (§9.2.1-§9.2.4 のどこまで実施):
  保持する成果物:
  選択した代替案 (Case A/B/C/D のどれ):
  代替案開始予定日:
```

このログを `DESIGN_NOTES.md` に永続化し、将来の意思決定に活用する。Deep CFR の失敗を曖昧に「ダメだった」と片付けた反省を生かす。


### 9.3 各 Stage の予算

| Stage | 期間 | 主要コスト |
|---|---|---|
| Stage A (Phase 1 SFT) | 2-3 週間 | RTX 3080 占有 60h、電気代 $5、開発工数 |
| Stage B (Phase 2 GRPO) | 4-6 週間 | RTX 3080 占有 100-150h、電気代 $10、開発工数 |
| Stage C (本番切替) | 1-2 週間 | 開発工数のみ |
| Stage D (旧削除) | 1 週間 | 開発工数のみ |
| **合計** | **8-12 週間** | **電気代 $15-20 + 開発工数** |

クラウド代替（Spheron Spot H100 等）で短縮する場合は $100-200 程度。

---

## 10. 実装スプリント計画

### Sprint 1 (Week 1-3): 基盤構築 + モデル選定 ✅ 完了

- [x] 訓練リポジトリ `C:\dev\pokerrl-training` 作成
- [x] 新規ファイル骨格 (`strategy/pokerrl_*.py`) を作成、空 I/F → commit 1edf9c3
- [x] PokerBench データダウンロード（8ファイル）
- [x] PHHデータセット取得（10,000 .phh）
- [x] ★★★ multiway postflop実数量確認: PokerBench 0件、Pluribus 943件、phh-dataset 2,090万件 ★★★
- [x] phh-dataset multiway抽出（20,915,640 decision points）
- [x] Layer 3（PokerKit合成）不要判定
- [x] Phi-4-mini ダウンロード + FP16ロード確認 (hidden=3072, layers=32, 7,317 MiB)
- [x] Qwen3.5-4B ダウンロード + VRAM超過確認 → 脱落
- [x] Gemma 4 E2B ダウンロード + 訓練速度不可確認 → 脱落
- [x] Phi-4-mini 10k SFT実施: action_accuracy 65.6%, eval_loss 0.326
- [x] Gemma 4 E2B 10k SFT中断: batch=1でstep 6/939、完走見積り3日超
- [x] 比較レポート作成
- [x] **Go/No-go: Phi-4-mini-instruct 3.8Bを正式採用**

実績:
  期間: 約3週間（当初計画2週間 + multiway分析・Gemma比較で1週間延長）
  pytest: 1441 passed
  最新commit: 1edf9c3

### Sprint 2 (Week 3-6): Phase 1 HU SFT本訓練 + MW Context Engine

#### HU SFTタスク

- [x] 訓練側リポジトリ `pokerrl-training` 作成（完了）
- [x] prepare_sft_full.py 作成（563,200件、完了）
- [x] run_sft_comparison.py 改修（resume_from, data_offset, keep_checkpoints、完了）
- [ ] 30k HU SFT 第1弾（実行中、PID 18392）
- [ ] 第1弾 eval 確認（accuracy ≥ 55% で Go）
- [ ] HU SFT 続き（30,001件目〜、ローカル or クラウドGPU）
- [ ] 補助ヘッド (action / sizing) 訓練
- [ ] 量子化 (AWQ または Q4_K_M)
- [ ] PokerBench テスト評価
- [ ] **Go/No-go**: §5.2 閾値

#### MW Context Engineタスク（★ v1.3で新設 ★）

- [ ] S2-T2-NEW-a: Context Engine実装（board texture, 23 hand class, SPR, ATT/DEF budget）
- [ ] S2-T2-NEW-b: 構造化プロンプト生成スクリプト
- [ ] S2-T2-NEW-c: Phase 0（5件パイプライン確認）
- [ ] S2-T2-NEW-d: Phase 1（50件、GPT-5.4-mini vs Phi-4-mini定性比較）
- [ ] S2-T2-NEW-e: Phase 2（500件定量評価）
- [ ] **Go/No-go**: Phase 2結果でMW推論の最終方針決定

#### 旧MWタスク（v1.2、全て破棄）

~~S2-T2a: confidence scoringスクリプト~~ → 破棄（勝者データ0件）
~~S2-T2b: stratified sampler + HU rehearsal~~ → 破棄
~~S2-T2c: weighted SFT実行~~ → 破棄
~~S2-T2d: multiway Go/No-go判定~~ → 破棄
~~S2-T2e: KTO検討~~ → 破棄

Sprint 2期間を3週間→4週間に延長（MW Context Engine作業を追加したため）。

### Sprint 3 (Week 6-9): Phase 2 GRPO 強化学習

- [ ] PokerKit ベース 6-max 自己対戦環境構築
- [ ] DAPO + OPEFO 実装
- [ ] 報酬関数実装（multi-hand bankroll）
- [ ] 100-150h 訓練
- [ ] Slumbot 評価
- [ ] **Go/No-go**: §5.3 閾値

### Sprint 4 (Week 10-11): 推論ブリッジ統合 + Shadow Mode

- [ ] `PokerRLBridge` 実装（既存 I/F 完全互換）
- [ ] `recommendation_engine.py` に shadow mode ロジック追加
- [ ] vLLM/llama.cpp の常駐推論プロセスをセットアップ
- [ ] Stability Guards との統合確認
- [ ] HUD ソースラベル更新
- [ ] 1441 tests + 新テスト全 pass 確認
- [ ] 実プレイで shadow mode 稼働

### Sprint 5 (Week 12-13): 本番切替

- [ ] Shadow mode のログ分析、差分大局面の人間レビュー
- [ ] Spot Checks 50 シナリオ全 pass 確認
- [ ] Stage C: HU postflop を PokerRL に切替
- [ ] Stage C: Multiway postflop を PokerRL に切替
- [ ] 1 週間モニタリング
- [ ] **Go/No-go**: 実プレイでの bb/100 改善確認

### Sprint 6 (Week 14): 旧コンポーネント削除（オプション）

- [ ] Stage D: Deep CFR Bridge 削除
- [ ] Stage D: Rust Solver 削除
- [ ] ドキュメント更新 (SPEC.md, DESIGN_NOTES.md, snapshot.md)
- [ ] GitHub push

---

## 11. 指令役エージェントへのスプリント分割方針

本指令書は実装役エージェント直接渡しでも動作するが、指令役エージェントが介在する場合は以下のように分割推奨:

| Sprint | 指令役の役割 | 実装役への指示粒度 |
|---|---|---|
| Sprint 1 | モデル比較タスクを並列発注 | 「Phi-4-mini で 10k SFT、Qwen3-4B で 10k SFT、両方の accuracy/latency を測れ」 |
| Sprint 2 | 訓練監視と中間チェックポイント評価 | 「Phase 1 訓練を 5000 step ごとにチェックポイント、PokerBench 1k で評価」 |
| Sprint 3 | GRPO の hyperparameter tuning 監視 | 「DAPO clip_higher を 0.25-0.30 で3 seed 試せ」 |
| Sprint 4 | 統合テスト | 「既存 1441 tests に加え、新規 spot checks 50 が全 pass を確認」 |
| Sprint 5 | 本番切替判断 | 「shadow mode ログをレビューし、Stage C に進むかを判定」 |

各 Sprint 終了時に指令役が **Go/No-go ゲート**で進捗を制御。失敗時は前 Sprint への戻りも許容。

---

## 12. 既存 SPEC.md・DESIGN_NOTES.md の更新依頼

本指令書実装と並行して、以下のドキュメント更新が必要:

### SPEC.md 更新箇所

- §10A "Deep CFR Inference Bridge" → 全面書き換え、"PokerRL+GRPO Inference Bridge" として再記述
- §10A の Deep CFR 部分は §10B "Legacy Deep CFR (Deprecated)" として保持
- §10 のルーティング図を新ブリッジに合わせて更新
- HUD ソースラベル仕様に `PokerRL`, `PokerRL+`, `PokerRL+Guard` を追加

### DESIGN_NOTES.md 更新箇所

- 新規セクション "PokerRL+GRPO 採用判断" を追加
- 新規セクション "Deep CFR 失敗の事後分析" を追加（profit vs random の罠について）
- 新規セクション "<50ms 目標と補助ヘッド設計" を追加

### snapshot.md 更新箇所

- "Updated: 2026-05-30" → Sprint 完了時に随時更新
- Stage A/B/C/D の進捗を「現在地点」として記録

---

## 13. 用語集（既存と新規の対応）

| 既存 SPEC.md | 本指令書 | 備考 |
|---|---|---|
| GameState | 同じ | 入力データ構造、変更なし |
| HandManager | 同じ | 変更なし |
| RecommendationEngine | 同じ + 新ブリッジ統合 | 内部ルーティング拡張 |
| Deep CFR Bridge | Legacy / Fallback | 削除せず保持 |
| Rust Solver | Legacy / Fallback | 削除せず保持 |
| LLM (gpt-5.4-mini) | 同じ | exploit adjustment 用、当面維持 |
| Context Snapshot | 同じ | そのまま流用 |
| Request ID system | 同じ | そのまま流用 |
| **新規**: PokerRL Bridge | 主推論エンジン | Phi-4-mini-instruct 3.8B（確定） |
| **新規**: Action Head | 補助ヘッド | 4-class softmax |
| **新規**: Sizing Head | 補助ヘッド | sigmoid 0.1x-3.0x pot |
| **新規**: Spot Classifier | 局面分類器 | Tier 振り分け |
| **新規**: Prompt Builder | GameState → 文字列 | 既存 GameState 流用 |

---

## 14. 完了条件

本指令書のすべての Sprint 完了時点で以下が達成されていれば成功:

- [ ] PokerRL Bridge が `recommendation_engine.py` の HU postflop と Multiway postflop の主推論器として稼働
- [ ] 1441 既存 tests + 新規 spot checks 50 が全 pass
- [ ] 実プレイで Stage C 切替後 1 週間モニタリング、明らかな品質低下なし
- [ ] HUD ソースが `PokerRL` または `PokerRL+` で表示
- [ ] Spot Checks 50 で 95% 合格、Deep CFR の「raise 70-80%」病理がない
- [ ] 推論レイテンシ P95: T1 ≤ 300ms、T2 ≤ 5s、T3 ≤ 12s
- [ ] Slumbot HU で -15 bb/100 以上、自己対戦で SFT baseline +3 bb/100 以上
- [ ] SPEC.md, DESIGN_NOTES.md, snapshot.md 更新済み
- [ ] GitHub `sanhyokim/poker-assistant` に全変更 push 済み

---

## 15. 指令書 v1.3 と旧版からの主な変更点

| 観点 | 旧版 | v1.3（本書） |
|---|---|---|
| ベースモデル | Phi-4-mini (第1) / Qwen3-4B (第2) | **Phi-4-mini-instruct 3.8B 確定。Qwen3.5-4B/Gemma 4 E2B脱落** |
| LoRA設定 | r=64, alpha=128 | **r=32, alpha=32（poker_rl作者設定準拠、Sprint 1実績）** |
| 訓練データ | PokerBench 560k + Pluribus 60k | **+ phh-dataset 2,090万multiway、Layer 3不要** |
| データ設計 | HU:multiway比率未定 | **active player数分布管理。固定比率廃止** |
| レイテンシ目標 | T1 600ms | **T1 30-300ms (<50ms 目標を補助ヘッドで)** |
| LLM API 利用 | 不要 | **既存 GPT-5.4-mini OpenRouter exploit を維持** |
| Async 機構 | これから設計 | **既存 request_id/active_id/cancelled_ids 流用** |
| Stability Guards | 言及なし | **既存 4-5 種を尊重** |
| Preflop 戦略 | LLM 置換 | **既存 GTO Charts を主軸として残す** |
| Multiway 戦略 | LLM 一本化 | **既存 eval7 + LLM の数理ガード設計を継承** |
| 訓練リポジトリ | 未作成 | **`C:\dev\pokerrl-training` 作成済み** |
| Sprint 1 | 未開始 | **完了。Phi-4-mini 65.6% accuracy** |
| Sizing Head | sigmoid 0.1x-3.0x | **カテゴリカルへの変更をSprint 2で検討（候補として残す）** |
| 既存 deep_cfr_bridge | 削除 | **Stage D まで保持** |
| 評価指標 | accuracy 中心 | **Spot Checks + Entropy + Sensitivity** |
| HUD 仕様 | 新規 | **既存原則 (Quality over Speed, state-only) 準拠** |
| リポジトリ構造 | ゼロから | **既存 `core/`, `strategy/`, `solver/`, `gui/` を拡張** |
| Multiway戦略 | PokerRL+GRPO + eval7 | **PokerSkill式Context Engine + LLM + eval7。SFT不要** |
| MW訓練データ | hole cards 72万件からweighted SFT | **全件敗者のため使用不可。ルール層で代替** |
| Sprint 2期間 | Week 3-5（3週間） | **Week 3-6（4週間、MW Context Engine追加）** |
| Sprint 2 MWタスク | S2-T2a〜e（weighted SFT系） | **S2-T2-NEW-a〜e（Context Engine + テスト）** |
| SPEC準拠 | v3.5 | **v3.6** |
| phh-dataset hole cards | 勝者選別SFT | **全件敗者。positive example使用禁止** |

---
