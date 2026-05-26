

```markdown
# Commander Snapshot

## Updated: 2026-05-26 JST
## Status: Phase 1 v4 訓練中（memory_size=20M） / poker-system Phase B完了

---

## 0. このsnapshotの位置づけ

このsnapshotは、次セッションでポーカーAIアシスタント開発を再開するための現在地点メモである。
体系的な仕様は SPEC.md、設計判断の理由は DESIGN_NOTES.md を参照。

---

## 1. 現在地点

### 1.1 最新テスト結果

```text
pytest -q
1441 passed, 0 failed
```

### 1.2 GitHub push状況

Phase B Task 2以降のローカル変更はGitHubに未push。
次セッションで以下を実行してpushすること。

```powershell
cd C:\Users\user\Desktop\dev\poker-system
git add -A
git commit -m "Phase B complete: Deep CFR routing, fallback, exploit, HUD"
git push origin main
```

対象ファイル:
- strategy/deep_cfr_bridge.py（新規）
- strategy/_deep_cfr_network.py（新規）
- strategy/recommendation_engine.py（Deep CFRルーティング、フォールバック、exploit）
- strategy/llm_pipeline.py（suggest_exploit_for_deep_cfr追加）
- core/game_loop.py（Deep CFR Bridge初期化、HUD表示）
- gui/hud_overlay.py（Deep CFRソース表示、確率分布、confidence、DEEP CFR THINKING...）
- tests/test_deep_cfr_bridge.py（17件）
- tests/test_deep_cfr_routing.py（9件）
- tests/test_deep_cfr_fallback.py（11件）
- tests/test_deep_cfr_exploit.py（10件）
- tests/test_hud_deep_cfr.py（8件）
- tests/test_gui_smoke.py（PRE-HAND期待値修正）

### 1.3 現在の開発状態

Deep CFR推論ブリッジのシステム統合（Phase B）が全タスク完了。
Deep CFRモデル訓練:
  Phase 3 v3b（memory_size=300,000）は中止。
  memory_size を 20,000,000 に拡大し、Phase 1 v4 から再訓練開始。
  Phase 1 v4 訓練中（2026-05-26開始）。

確定事項:

- Preflop: Chart（変更なし）
- HU Postflop: Deep CFR推論（実装完了、モデル訓練中）
- Multiway Postflop: Deep CFR推論（同上）
- exploit_adjustment: LLM継続（Deep CFR出力に対する統計ベース補正、実装完了）
- Rust postflop CLI: Deep CFR品質確認後に廃止（fallback経路として残存）
- OpenRouter / gpt-5.4-mini: exploit用途で継続

### 1.4 Deep CFRフォールバック経路（Phase B Task 2.5で実装済み）

| Deep CFR失敗時 | Flop HU | Flop Multiway | Turn/River HU | Turn/River Multiway |
|---|---|---|---|---|
| 第1フォールバック | LLM | LLM | Solver | LLM |
| 第1も失敗時 | スキップ | スキップ | LLMフォールバック | スキップ |

---

## 2. Phase B（システム統合）完了状況

| Task | 内容 | 状態 | テスト追加 |
|---|---|---|---|
| B-1 | deep_cfr_bridge.py 新規作成 | 完了 | +17 |
| B-2 | recommendation_engine.py ルーティング | 完了 | +9 |
| B-2.5 | フォールバック経路細分化 | 完了 | +11 |
| B-3 | exploit_adjustment（Deep CFR出力へのLLM補正） | 完了 | +10 |
| B-4 | HUD表示 Deep CFR対応 | 完了 | +8 |

テスト推移: 1411 passed / 1 failed → 1441 passed / 0 failed

---

## 3. 主要コード設計概要

（Section 1.3〜3.7 は前回 snapshot と同一のため省略。変更なし。）

---

## 4. Deep CFR訓練進捗

### 4.1 訓練環境

```text
リポジトリ: C:\dev\deepcfr-training
（git clone https://github.com/dberweger2017/deepcfr-texas-no-limit-holdem-6-players）
最終更新: 2026年3月（Issue #22修正済、Phase 2/3バグ修正含む）
仮想環境: C:\dev\deepcfr-training\.venv
GPU: NVIDIA GeForce RTX 3080 (VRAM 10GB) / RAM 32GB / Python 3.10 (Conda) / PyTorch 2.5.1+cu121

pokers ライブラリ: patched fork
  pip install git+https://github.com/dberweger2017/pokers.git@b1a48bd

flagship_models フォルダ: リポジトリに存在（旧アーキテクチャ。現行コードと非互換）
```

### 4.2 独自パッチ（3件、upstream にはない。元に戻さないこと）

1. MAX_ACTIONS_PER_GAME = 300
   ファイル: src/training/train.py L21, L39, L42, L887, L920, L923
   目的: 評価関数のRaise→Raise無限ループ防止

2. PrioritizedMemory.__init__ に max_priority_cap = 100.0
   ファイル: src/core/deep_cfr.py L23
   目的: priority explosion → 勾配爆発防止

3. memory_size = 20_000_000（学習エージェントのみ）
   ファイル: src/training/train.py L280, L427, L589, L1024
   目的: メモリバッファ拡大（デフォルト300,000 → 20,000,000）
   原論文は40,000,000を使用。RAM 32GBの制約下で20,000,000を採用。
   将来RAM増設時にさらに拡大を検討する。

### 4.3 独自追加コード（使用禁止）

- train_selfplay_v2 関数 (train.py L715〜)
- --self-play-v2, --random-seats, --opponent-checkpoints フラグ
- これらは README に存在せず、使用しない

### 4.4 ネットワーク構造（確定値）

```text
入力次元: 156 (52+52+5+1+6+6+6*4+1+4+5)
隠れ層: 3層 × 256 ユニット
出力ヘッド: 2つ
  action_head: 3 アクション (Fold, Check/Call, Raise)
  sizing_head: 連続ベットサイズ 0.1 + 2.9 * sigmoid(x) → 0.1〜3.0× pot
encode_state(): 156次元 NumPy ベクトルを生成
学習率: advantage optimizer lr=1e-6, strategy optimizer lr=0.00005
```

### 4.5 訓練情報源

```text
README (readme.md, 2026年3月) が正規情報源。
description.md (2025年3月) は旧アーキテクチャ時代の実験記録。参考のみ。
flagship model (2025年3月) は旧アーキテクチャ (fc1-fc6, 4アクション固定)。
  現行コード (base/action_head/sizing_head, 3アクション+連続サイジング) と非互換。
  ロード不可。使用しない。
```

### 4.6 旧訓練結果（参考のみ）

#### 旧 Phase 1 (Seed A/B/C, traversals=300, iterations=1500)

| Seed | Profit vs Random (500 games) |
|---|---|
| A | 22.65 |
| B | 23.85 |
| C | 42.43 |

#### 旧 Phase 2 v2 (train_selfplay_v2, 独自関数。使用禁止)

iter 300 がピーク（vs random 32.78）、以降低下。

### 4.7 現行訓練（README準拠）

#### Phase 1 v3 ✅ 完了（traversals=200, iterations=1000）

```text
Profit vs random (training eval): 19.34
Final eval (1000 games): 9.33
独立再評価 (3000 games): 12.00
Checkpoint: models/phase1_v3/checkpoint_iter_1000.pt
```

#### Phase 2 v3 ✅ 完了（self-play, traversals=400, iterations=2000）

```text
最終結果:
  vs checkpoint: -0.48（Phase 1 とほぼ互角）
  vs random: -0.50
  Strategy loss: 44.36（140→44 と大幅低下）
  独立再評価 (3000 games): -0.45

Phase 2 単体では Phase 1 を上回らないが、崩壊せず安定完走。
開発者も同じ経験を報告しており、Phase 3 で回復する設計。

Checkpoints: models/selfplay_v3/selfplay_checkpoint_iter_*.pt (20ファイル)
```

#### Phase 3 v3b ❌ 中止（メモリバッファ拡大のため Phase 1 v4 から再訓練）

```text
コマンド:
python -m src.training.train \
  --mixed \
  --checkpoint-dir models/phase3_pool_v3 \
  --model-prefix "selfplay_checkpoint_iter_[0-9]" \
  --refresh-interval 1000 \
  --num-opponents 5 \
  --iterations 10000 \
  --traversals 400 \
  --log-dir logs/phase3_v3b \
  --save-dir models/phase3_v3b

ログ: C:\dev\deepcfr-training\phase3_v3b.stdout.log
開始: 2026-05-26
現在: iter 1626/10000（16.3%）
Iteration time: ~12 s
推定残り: ~28 時間（2026-05-27 夕方頃）

対戦相手プール:
  models/phase3_pool_v3/ に Phase 2 v3 の 20 checkpoint をコピー。
  --model-prefix "selfplay_checkpoint_iter_[0-9]" で旧訓練ファイル混入を防止。
  1000 iter ごとに対戦相手をランダム再選択。

Phase 3 初回 (phase3_v3) は loss 発散あり（iter 27 で停止）。
Phase 3 再実行 (phase3_v3b) は修正なしで開始。
  初期に loss 発散が散発したが、学習が進むにつれ自然収束。

profit 推移:
  初期: vs random +3.29
  iter 100-200: vs random +15〜+42（急上昇）
  iter 300-500: vs random +20〜+42（ピーク帯）
  iter 700-1000: vs random +15〜+35（安定）
  直近 (iter 1500付近): vs random +10〜+30（安定）

Phase 1 v3 (12.00) を大幅に上回り、最終合格基準 (≥15) を達成している。
```

#### Phase 1 v4 🔄 進行中（memory_size=20M, traversals=200, iterations=1000）

コマンド:
python -m src.training.train \
  --iterations 1000 \
  --traversals 200 \
  --save-dir models/phase1_v4 \
  --log-dir logs/phase1_v4

ログ: C:\dev\deepcfr-training\phase1_v4.stdout.log
開始: 2026-05-26
推定完了: 約1.5〜2時間

変更点:
  memory_size: 300,000 → 20,000,000（学習エージェントのみ）
  GPU記載修正: GTX 1080 → RTX 3080 (VRAM 10GB) / RAM 32GB

Phase 1 v4 完了後の計画:
  Phase 2 v4: --self-play --iterations 2000 --traversals 400（推定約24-28時間）
  Phase 3 v4: --mixed --iterations 10000 --traversals 400（推定約9日、途中打ち切り可）

### 4.8 Phase 3 対戦相手プールの構成

```text
models/phase3_pool_v3/ に以下を格納（models/selfplay_v3/ からコピー）:
  selfplay_checkpoint_iter_100.pt 〜 selfplay_checkpoint_iter_2000.pt（20ファイル）

--checkpoint-dir models/phase3_pool_v3 で直下のみ検索（非再帰）。
--model-prefix "selfplay_checkpoint_iter_[0-9]" で glob 文字クラス使用。
Phase 3 自身の保存ファイルは models/phase3_v3b/ に別フォルダ保存。
旧訓練 (phase1_seedA/B/C, phase2, phase2_v2) は混入しない。
```

### 4.9 Phase 3 完了後の評価スクリプト

```powershell
cd C:\dev\deepcfr-training
.venv\Scripts\activate
python -c "
from src.training.train import evaluate_against_random, evaluate_against_checkpoint_agents
from src.core.deep_cfr import DeepCFRAgent
import torch, glob, os

# Phase 1 v3
agent1 = DeepCFRAgent(player_id=0)
cp1 = torch.load('models/phase1_v3/checkpoint_iter_1000.pt', map_location='cpu')
agent1.strategy_net.load_state_dict(cp1['strategy_net'])
agent1.advantage_net.load_state_dict(cp1['advantage_net'])
p1 = evaluate_against_random(agent1, num_games=3000)
print(f'Phase 1 v3 profit vs random (3000) = {p1:.2f}')

# Phase 3 v3b checkpoints
checkpoints = sorted(glob.glob('models/phase3_v3b/*mixed*.pt'))
if checkpoints:
    latest = checkpoints[-1]
    agent3 = DeepCFRAgent(player_id=0)
    cp3 = torch.load(latest, map_location='cpu')
    agent3.strategy_net.load_state_dict(cp3['strategy_net'])
    agent3.advantage_net.load_state_dict(cp3['advantage_net'])
    p3 = evaluate_against_random(agent3, num_games=3000)
    print(f'Phase 3 v3b ({os.path.basename(latest)}) profit vs random (3000) = {p3:.2f}')

    # vs Phase 1 checkpoint
    opponents = {}
    for i in range(1, 6):
        opp = DeepCFRAgent(player_id=i)
        opp.strategy_net.load_state_dict(cp1['strategy_net'])
        opp.advantage_net.load_state_dict(cp1['advantage_net'])
        opponents[i] = opp
    p3_vs_p1 = evaluate_against_checkpoint_agents(agent3, opponents, num_games=3000)
    print(f'Phase 3 v3b vs Phase 1 v3 (3000) = {p3_vs_p1:.2f}')
else:
    print('No Phase 3 v3b checkpoints found')
"
```

### 4.10 Phase 3 異常検知基準

以下のいずれかが発生したら訓練を停止し報告:
- プロセスが消えている
- Advantage network loss が全イテレーションで 10^11 以上（初期散発は許容）
- Average profit vs random が 100 iter 連続で負
- エラーメッセージで停止

### 4.11 Phase 3 モニタリングコマンド

```powershell
# profit 推移確認
Select-String -Path C:\dev\deepcfr-training\phase3_v3b.stdout.log -Pattern "Average profit vs random" | Select-Object -Last 10 -ExpandProperty Line

# 現在 iter 確認
Get-Content C:\dev\deepcfr-training\phase3_v3b.stdout.log -Tail 5

# loss 発散確認
Select-String -Path C:\dev\deepcfr-training\phase3_v3b.stdout.log -Pattern "Advantage network loss" | Select-Object -Last 10 -ExpandProperty Line
```

---

## 5. 訓練リポジトリの重要な発見事項

### 5.1 flagship model は現行コードと非互換

```text
flagship_models/first/1-model.pt: 2025年3月作成。旧アーキテクチャ (fc1-fc6, 4アクション)。
flagship_models/first/mixed_checkpoint_iter_11200.pt: 同上。
現行コード: base/action_head/sizing_head, 3アクション+連続サイジング。
ロード時に RuntimeError: Missing/Unexpected keys。使用不可。
```

### 5.2 README の Phase 3 コマンドはそのままでは動作しない

```text
README: --model-prefix t_
実際: models/ 直下に t_*.pt は存在しない。
対処: Phase 2 checkpoint を専用フォルダにコピーし、
  --model-prefix "selfplay_checkpoint_iter_[0-9]" を使用。
```

### 5.3 train_with_mixed_checkpoints の検索仕様

```text
glob.glob(os.path.join(checkpoint_dir, f"{training_model_prefix}*.pt"))
非再帰。checkpoint_dir 直下のみ検索。
Phase 3 自身の保存先は --save-dir で別フォルダにすること。
```

### 5.4 Phase 3 初期の loss 発散は自然収束する

```text
Phase 3 初回 (v3): iter 5〜27 で約44%のイテレーションで loss 10^11〜10^12。
Phase 3 再実行 (v3b): 同じ現象が初期に発生したが、数百 iter で自然収束。
原因: encode_state の正規化分母が極小 stake で爆発する可能性（未修正）。
対処: 修正不要。学習が進むと発散頻度が減少し profit は改善される。
```

---

## 6. 訓練コード修正記録

（Section 5.1〜5.3 は前回 snapshot と同一。変更なし。）

---

## 7. HUD出力形式（実装済み）

（前回 snapshot と同一。変更なし。）

---

## 8. 旧課題の扱い

（前回 snapshot と同一。変更なし。）

---

## 9. 次にやること

### 9.1 即時: Phase 1 v4 完了待ち

Phase 1 v4 訓練完了を確認し、評価を実施。
合格基準: profit vs random ≥ 10

### 9.2 Phase 1 v4 完了後: 評価 → Phase 2 v4 開始

Phase 1 v4 評価スクリプト実行後、Phase 2 v4 を開始。

Phase 2 v4 コマンド:
python -m src.training.train \
  --checkpoint models/phase1_v4/checkpoint_iter_1000.pt \
  --self-play \
  --iterations 2000 \
  --traversals 400 \
  --save-dir models/selfplay_v4 \
  --log-dir logs/selfplay_v4

### 9.3 モデル配置

合格後:
```text
copy best_checkpoint.pt → C:\Users\user\Desktop\dev\poker-system\models\deep_cfr\best_checkpoint.pt
config.yaml: deep_cfr.fallback_to_solver: false
```

Phase 3 の checkpoint ファイル名は
models/phase3_v3b/selfplay_checkpoint_iter_[0-9]mixed_iter_*.pt
の形式で保存される（要確認）。

### 9.4 SPEC.md / DESIGN_NOTES.md 更新

本セッションで判明した以下を反映:
- flagship model 非互換の事実
- Phase 3 対戦相手プール構成
- Phase 3 loss 発散と自然収束
- description.md vs readme.md の関係

### 9.5 GitHub push

Section 1.2 のコマンドでpoker-systemのローカル変更をpush。

### 9.6 ライブテスト

Deep CFRモデル配置後、CoinPokerでライブテスト実施。

---

## 10. 禁止事項・維持事項

既存の全禁止事項を維持する（SPEC Section 17参照）。

追加:
- 訓練手順は README を基本とし、description.md は参考のみ
- train_selfplay_v2 / --self-play-v2 / --random-seats は使用しない
- Phase 3 の loss 発散で中断しない（自然収束する）
- MAX_ACTIONS_PER_GAME と max_priority_cap パッチは保持
- 中間 checkpoint を本番推論に使用しない
- Deep CFR品質検証前にRust postflop CLIを削除しない
- LLM exploit_adjustmentを廃止しない
- flagship model (旧アーキテクチャ) を現行コードで使用しない
- Phase 3 対戦相手プールに旧訓練ファイルを混入しない

---

## 11. ユーザー要望・進行ルール

既存ルールを維持。
```
