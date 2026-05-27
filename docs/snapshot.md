

# Commander Snapshot

## Updated: 2026-05-27 JST
## Status: Phase 3 v4 訓練中（memory_size=20M） / poker-system Phase B完了

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

poker-system側は全変更push済み。

最新commit:
```text
ドキュメント更新: メモリバッファ拡大の設計判断記録、v4訓練計画、snapshot現在地点更新
```

### 1.3 現在の開発状態

Deep CFR推論ブリッジのシステム統合（Phase B）が全タスク完了。
Deep CFRモデル訓練は Phase 3 v4（mixed training, memory_size=20M）が進行中。

確定事項:

- Preflop: Chart（変更なし）
- HU Postflop: Deep CFR推論（実装完了、モデル訓練中）
- Multiway Postflop: Deep CFR推論（同上）
- exploit_adjustment: LLM継続（Deep CFR出力に対する統計ベース補正、実装完了）
- Rust postflop CLI: Deep CFR品質確認後に廃止（fallback経路として残存）
- OpenRouter / gpt-5.4-mini: exploit用途で継続

Deep CFRモデル訓練:
  Phase 3 v3b（memory_size=300,000）は中止。
  memory_size を 20,000,000 に拡大し、Phase 1 v4 から再訓練。
  Phase 1 v4 完了（profit vs random = 24.41）
  Phase 2 v4 完了（profit vs random = -0.80、Phase 3で回復する設計）
  Phase 3 v4 訓練中（2026-05-27開始）

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

（前回 snapshot と同一のため省略。変更なし。）

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
   ファイル: src/training/train.py L281, L429, L592, L1029
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
Medium記事は更新されておらず、READMEと矛盾する箇所がある。READMEを優先する。
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

#### Phase 1-3 v3 / v3b（memory_size=300,000）

Phase 1 v3: profit vs random 12.00（独立再評価3000 games）
Phase 2 v3: profit vs random -0.45（独立再評価3000 games）
Phase 3 v3b: 中止（iter 2018/10000時点、profit +10〜+36で安定していたがメモリバッファ拡大のため再訓練）

### 4.7 現行訓練（v4, memory_size=20M, README準拠）

#### Phase 1 v4 ✅ 完了（traversals=200, iterations=1000）

```text
Final eval (500 games): 47.53
独立再評価 (3000 games): 24.41
Phase 1合格基準 (≥10): 大幅超過
速度: 1.6〜3.5秒/iter（メモリ増加に伴い後半やや増加）
Advantage memory最終: 約480万（2000万の24%、満杯にならず）
Checkpoint: models/phase1_v4/checkpoint_iter_1000.pt

v3比較:
  v3 独立再評価: 12.00
  v4 独立再評価: 24.41（2倍に改善）
```

#### Phase 2 v4 ✅ 完了（self-play, traversals=400, iterations=2000）

```text
Final profit vs checkpoint: -3.51
Final profit vs random: -0.80
Strategy loss: 53.14
速度: 約10-11秒/iter
Advantage memory最終: 約539万（2000万の27%）

Phase 2 単体では Phase 1 を上回らないが、崩壊せず安定完走。
開発者も同じ経験を報告しており、Phase 3 で回復する設計。

Checkpoints: models/selfplay_v4/selfplay_checkpoint_iter_*.pt (20ファイル)
```

#### Phase 3 v4 🔄 進行中（mixed training, traversals=400, iterations=10000）

```text
コマンド:
.venv\Scripts\python.exe -u -m src.training.train \
  --mixed \
  --checkpoint-dir models/phase3_pool_v4 \
  --model-prefix "selfplay_checkpoint_iter_[0-9]" \
  --refresh-interval 1000 \
  --num-opponents 5 \
  --iterations 10000 \
  --traversals 400 \
  --log-dir logs/phase3_v4 \
  --save-dir models/phase3_v4

起動方式:
  Start-Process + RedirectStandardOutput/Error（-uフラグでバッファリング無効）
  監視: 別PowerShellウィンドウで Get-Content phase3_v4.stdout.log -Wait

ログ: C:\dev\deepcfr-training\phase3_v4.stdout.log
stderr: C:\dev\deepcfr-training\phase3_v4.stderr.log
開始: 2026-05-27
速度: 約9-11秒/iter（初期）
推定完了: 約1-2日後（後半メモリ増加で遅くなる可能性あり）

対戦相手プール:
  models/phase3_pool_v4/ に Phase 2 v4 の 20 checkpoint をコピー。
  --model-prefix "selfplay_checkpoint_iter_[0-9]" で旧訓練ファイル混入を防止。
  1000 iter ごとに対戦相手をランダム再選択。

初期評価:
  Initial profit vs mixed opponents: 23.01
  Initial profit vs random: -9.34

Phase 3初期にloss発散が散発するが、数百iterで自然収束する（DESIGN_NOTES Section 43）。
cp932エンコーディングエラー（スート文字♠♥♦♣）は訓練に影響なし。
```

### 4.8 Phase 3 対戦相手プールの構成

```text
models/phase3_pool_v4/ に以下を格納（models/selfplay_v4/ からコピー）:
  selfplay_checkpoint_iter_100.pt 〜 selfplay_checkpoint_iter_2000.pt（20ファイル）

--checkpoint-dir models/phase3_pool_v4 で直下のみ検索（非再帰）。
--model-prefix "selfplay_checkpoint_iter_[0-9]" で glob 文字クラス使用。
Phase 3 自身の保存ファイルは models/phase3_v4/ に別フォルダ保存。
旧訓練 (phase1_seedA/B/C, phase2, phase2_v2, v3系) は混入しない。
```

### 4.9 Phase 3 完了後の評価スクリプト

```powershell
cd C:\dev\deepcfr-training
.venv\Scripts\activate
python -c "
from src.training.train import evaluate_against_random, evaluate_against_checkpoint_agents
from src.core.deep_cfr import DeepCFRAgent
import torch, glob, os

# Phase 1 v4
agent1 = DeepCFRAgent(player_id=0, device='cuda')
cp1 = torch.load('models/phase1_v4/checkpoint_iter_1000.pt', map_location='cuda')
agent1.strategy_net.load_state_dict(cp1['strategy_net'])
agent1.advantage_net.load_state_dict(cp1['advantage_net'])
p1 = evaluate_against_random(agent1, num_games=3000)
print(f'Phase 1 v4 profit vs random (3000) = {p1:.2f}')

# Phase 3 v4 checkpoints
checkpoints = sorted(glob.glob('models/phase3_v4/*mixed*.pt'))
if checkpoints:
    latest = checkpoints[-1]
    agent3 = DeepCFRAgent(player_id=0, device='cuda')
    cp3 = torch.load(latest, map_location='cuda')
    agent3.strategy_net.load_state_dict(cp3['strategy_net'])
    agent3.advantage_net.load_state_dict(cp3['advantage_net'])
    p3 = evaluate_against_random(agent3, num_games=3000)
    print(f'Phase 3 v4 ({os.path.basename(latest)}) profit vs random (3000) = {p3:.2f}')

    # vs Phase 1 checkpoint
    opponents = {}
    for i in range(1, 6):
        opp = DeepCFRAgent(player_id=i, device='cuda')
        opp.strategy_net.load_state_dict(cp1['strategy_net'])
        opp.advantage_net.load_state_dict(cp1['advantage_net'])
        opponents[i] = opp
    p3_vs_p1 = evaluate_against_checkpoint_agents(agent3, opponents, num_games=3000)
    print(f'Phase 3 v4 vs Phase 1 v4 (3000) = {p3_vs_p1:.2f}')
else:
    print('No Phase 3 v4 checkpoints found')
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
Select-String -Path C:\dev\deepcfr-training\phase3_v4.stdout.log -Pattern "Average profit vs random" | Select-Object -Last 10 -ExpandProperty Line

# 現在 iter 確認
Get-Content C:\dev\deepcfr-training\phase3_v4.stdout.log -Tail 5

# loss 発散確認
Select-String -Path C:\dev\deepcfr-training\phase3_v4.stdout.log -Pattern "Advantage network loss" | Select-Object -Last 10 -ExpandProperty Line

# リアルタイム監視（別ウィンドウで）
Get-Content C:\dev\deepcfr-training\phase3_v4.stdout.log -Wait
```

---

## 5. 訓練リポジトリの重要な発見事項

### 5.1 flagship model は現行コードと非互換

```text
flagship_models/first/1-model.pt: 2025年3月作成。旧アーキテクチャ (fc1-fc6, 4アクション)。
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
Phase 3 v3b/v4 ともに初期にloss 10^11〜10^12の散発スパイクが発生。
数百iterで自然収束する。
原因候補: encode_stateの正規化分母が極小stakeで爆発する可能性（未修正）。
DESIGN_NOTES Section 43 に詳細記録。
```

### 5.5 メモリバッファのデフォルト300,000は不十分

```text
原論文は40,000,000を使用。デフォルト300,000は原論文の0.75%。
v3系ではiter 65でメモリが満杯になり、古い経験が早期に失われていた。
v4で20,000,000に拡大。Phase 1 v4の独立再評価がv3の2倍に改善（12.00→24.41）。
DESIGN_NOTES Section 44 に詳細記録。
```

### 5.6 cp932エンコーディングエラーは訓練に影響なし

```text
Windows日本語環境でスート文字（♠♥♦♣）をログファイルに書けない。
log_game_error関数のtxtファイル作成が失敗するだけ。
訓練の計算・学習・チェックポイント保存には一切影響なし。
```

### 5.7 Phase 3起動方法の注意

```text
Tee-Object方式: stdoutは流れるがstderrの警告がエラー表示される。Phase 3では止まる場合あり。
Start-Process方式: -uフラグ（バッファリング無効）が必須。ないとstdoutがファイルに書かれない。
推奨: Start-Process + -u + 別ウィンドウで Get-Content -Wait で監視。
```

---

## 6. 訓練コード修正記録

（前回 snapshot と同一。変更なし。）

---

## 7. HUD出力形式（実装済み）

（前回 snapshot と同一。変更なし。）

---

## 8. 旧課題の扱い

（前回 snapshot と同一。変更なし。）

---

## 9. 次にやること

### 9.1 即時: Phase 3 v4 モニタリング

Phase 3 v4 訓練を監視し、完了または異常停止を確認。
モニタリングコマンドは Section 4.11 を参照。
異常検知基準は Section 4.10 を参照。

### 9.2 Phase 3 完了後: 最終評価

Section 4.9 の評価スクリプトを実行。

最終合格基準:
- ランダム相手への利益 >= 15チップ/ゲーム
- Phase 1 checkpointへの勝率 >= 60%
- CLIプレイで明らかな異常行動がないこと

### 9.3 モデル配置

合格後:
```text
copy best_checkpoint.pt → C:\Users\user\Desktop\dev\poker-system\models\deep_cfr\best_checkpoint.pt
config.yaml: deep_cfr.fallback_to_solver: false
```

### 9.4 SPEC.md / DESIGN_NOTES.md 更新

Phase 3完了後の最終結果を反映。

### 9.5 ライブテスト

Deep CFRモデル配置後、CoinPokerでライブテスト実施。

### 9.6 プロンプト改訂

Commanderプロンプトのタスク種別判定（コード変更/ドキュメント更新/調査）を改訂済み。
改訂版はセッション内で合意済みだが、ファイルとしての保存は未実施。

---

## 10. 禁止事項・維持事項

既存の全禁止事項を維持する（SPEC Section 17参照）。

追加:
- 訓練手順は README を基本とし、description.md は参考のみ
- train_selfplay_v2 / --self-play-v2 / --random-seats は使用しない
- Phase 3 の loss 発散で中断しない（自然収束する）
- MAX_ACTIONS_PER_GAME と max_priority_cap パッチは保持
- memory_size = 20_000_000 パッチは保持（学習エージェントのみ）
- 中間 checkpoint を本番推論に使用しない
- Deep CFR品質検証前にRust postflop CLIを削除しない
- LLM exploit_adjustmentを廃止しない
- flagship model (旧アーキテクチャ) を現行コードで使用しない
- Phase 3 対戦相手プールに旧訓練ファイルを混入しない

---

## 11. ユーザー要望・進行ルール

既存ルールを維持。

追加記録:
- メモリバッファは将来RAM増設時にさらに拡大（30M〜40M）を検討する
- 訓練ログはリアルタイムで確認できる方式を優先する（Get-Content -Wait）
- Commanderプロンプトはタスク種別に応じてテスト要件を分ける（コード変更のみpytest必須）

---

## 12. 今セッションで完了した作業

1. Phase 3 v3b モニタリング（iter 2018/10000、profit +10〜+36で安定）
2. poker-system Phase B変更のGitHub push
3. DESIGN_NOTES.md Section 40-43 追加（訓練知見4件）
4. SPEC.md 10A.9/10A.10 追記（flagship非互換、情報源優先順位、Phase 3設定）
5. ドキュメント変更のGitHub push
6. Commanderプロンプト改訂（タスク種別判定テーブル追加）
7. メモリバッファ拡大の調査・設計判断（30万→2000万）
8. train.py memory_size変更（4箇所）
9. snapshot.md GPU記載修正（GTX 1080→RTX 3080）
10. DESIGN_NOTES.md Section 44 追加（メモリバッファ拡大理由）
11. SPEC.md 10A.10 追記（memory_size現在設定）
12. Phase 3 v3b 中止
13. Phase 1 v4 実行・完了（profit 24.41、v3の2倍）
14. Phase 2 v4 実行・完了（profit -0.80、正常）
15. Phase 3 v4 開始（進行中）
16. poker_error_*.txt 7495件削除