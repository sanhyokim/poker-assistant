
# Commander Snapshot

## Updated: 2026-05-20 JST
## Status: Deep CFR統合決定 / 訓練開始準備中

---

## 0. このsnapshotの位置づけ

このsnapshotは、次セッションでポーカーAIアシスタント開発を再開するための現在地点メモである。

---

## 1. 現在地点

### 1.1 最新重要code commit

```text
775d3ab 追加: HU Solver Hero hand range membership監査を追加
```

### 1.2 最新テスト結果

```text
python -m pytest tests/test_recommendation_engine.py tests/test_compare_solver_requests.py -q
181 passed
```

### 1.3 現在の開発状態

HU postflop / Multiway postflopの判断エンジンを、
Rust postflop CLI + LLMからDeep CFR推論に切り替える方針が確定した。

確定事項:

- Preflop: Chart（変更なし）
- HU Postflop: Deep CFR推論に切り替え
- Multiway Postflop: Deep CFR推論に切り替え（LLM判断主軸を廃止）
- exploit_adjustment: LLM継続（Deep CFR出力に対する統計ベース補正）
- Rust postflop CLI: Deep CFR統合完了後に廃止
- OpenRouter / gpt-5.4-mini: exploit用途で継続

---

## 2. Deep CFR統合計画

### 2.1 訓練（並行作業A）

訓練リポジトリ: https://github.com/dberweger2017/deepcfr-texas-no-limit-holdem-6-players
訓練環境: RTX 3080 / VRAM 10GB / Windows 10/11
訓練スケジュール: 約1ヶ月（Step 0〜5）

訓練は本体システム改修と並行して進める。

### 2.2 システム改修（並行作業B）

訓練中に以下を実装する。

1. deep_cfr_bridge.py 新規作成
   - GameState → 500次元入力変換
   - モデルロード・推論
   - 出力 → Recommendation変換
   - エラーハンドリング

2. recommendation_engine.py 改修
   - Section 9.1 戦略ルーティング変更
   - HU postflop: Deep CFR呼び出し
   - Multiway postflop: Deep CFR呼び出し
   - exploit_adjustment: Deep CFR出力に対して適用

3. config.yaml 追加
   - deep_cfr セクション

4. テスト追加
   - test_deep_cfr_bridge.py
   - test_recommendation_engine.py 拡張

5. HUD表示変更
   - 確率分布＋金額表記
   - DEEP CFR THINKING... 処理中表示

### 2.3 切り替え手順

Phase A: 訓練＋bridge実装（並行、約1ヶ月）
Phase B: 訓練済みモデルでbridge統合テスト（数日）
Phase C: ライブテストでDeep CFR推奨を検証（数日）
Phase D: 問題なければRust postflop CLI廃止＋LLM Multiway廃止

fallback_to_solver=true の間は既存Solver経路が生きている。
Deep CFR品質確認後にfallback_to_solver=false へ切り替える。

---

## 3. HUD出力形式

Deep CFR出力:
  fold_prob / call_prob / raise_prob / raise_size_ratio

HUD表示例:
  RAISE 2092  (72%)
  CALL 498    (25%)
  FOLD        (3%)

推奨アクション: 最も確率が高いアクション
金額: チップ額表記
confidence: top_prob >= 0.70 → high / >= 0.45 → medium / < 0.45 → low

---

## 4. 旧課題の扱い

### 4.1 解決される課題

- deep-SPR flop Solver timeout → Deep CFRで消滅
- Hero hand range外問題 → Deep CFRで消滅
- Multiway LLM品質不安定 → Deep CFRで消滅
- Solver process reset / orphan問題 → Deep CFRで消滅

### 4.2 継続する課題

- 金額OCR再読確認方式（Section 18）
- Hero turn音通知（Section 18.7）
- hand start latency改善（Section 18.8）
- Site Adapter層分離（将来）

### 4.3 保留のまま解消される課題

- Task 18-D: Hero hand range外原因診断 → Deep CFRで不要化
- HU flop LLM化検証 → Deep CFRで不要化
- Solver先行計算検討 → Deep CFRで不要化
- deep-SPR軽量Solver検討 → Deep CFRで不要化
- 旧teacherデータ信頼性問題 → Deep CFRで不要化

---


## 5. 次にやること

### 5.1 即時: Deep CFR訓練環境構築

```text
git clone https://github.com/dberweger2017/deepcfr-texas-no-limit-holdem-6-players.git
cd deepcfr-texas-no-limit-holdem-6-players
python3 -m venv .venv
.venv\Scripts\activate  (Windows)
pip install -r requirements.txt

# 動作確認
python -m src.training.train --iterations 5 --traversals 50 --log-dir logs/test --save-dir models/test
# 成功したら削除
rmdir /s /q logs\test models\test
```

### 5.2 訓練開始: Phase 1 ×3シード

```text
# シードA
python -m src.training.train --iterations 1500 --traversals 300 --log-dir logs/phase1_seedA --save-dir models/phase1_seedA

# シードB
python -m src.training.train --iterations 1500 --traversals 300 --log-dir logs/phase1_seedB --save-dir models/phase1_seedB

# シードC
python -m src.training.train --iterations 1500 --traversals 300 --log-dir logs/phase1_seedC --save-dir models/phase1_seedC
```

1シードずつ順番実行を推奨（RTX 3080 VRAM 10GBの安全運用）。
1シードあたり推定2〜3日。合計約1週間。

TensorBoard監視:
```text
tensorboard --logdir=logs
```

Phase 1合格基準:
- advantage lossが安定的に低下していること
- ランダム相手への利益が10チップ/ゲーム以上

### 5.3 Phase 1品質確認

```text
python scripts/visualize_tournament.py \
  --checkpoints models/phase1_seedA/checkpoint_iter_1500.pt models/phase1_seedB/checkpoint_iter_1500.pt models/phase1_seedC/checkpoint_iter_1500.pt \
  --num-games 5000
```

最も勝率が高い（または最も負けが少ない）シードを選定。

### 5.4 Phase 2: 自己対戦

```text
python -m src.training.train \
  --checkpoint models/phase1_seedX/checkpoint_iter_1500.pt \
  --self-play \
  --iterations 2000 \
  --traversals 400 \
  --log-dir logs/phase2 \
  --save-dir models/phase2
```

推定2〜3日。lossが激しく振動し始めたらその直前のcheckpointが最良。

Phase 2中間検証:
```text
python scripts/visualize_tournament.py \
  --checkpoints models/phase1_seedX/checkpoint_iter_1500.pt models/phase2/checkpoint_iter_1000.pt models/phase2/checkpoint_iter_1500.pt models/phase2/checkpoint_iter_2000.pt \
  --num-games 3000
```

Phase 2がPhase 1に負け越している場合、Phase 1 checkpointのままPhase 3へ進む。

### 5.5 Phase 3: 混合訓練

```text
python -m src.training.train \
  --mixed \
  --checkpoint-dir models \
  --model-prefix "*" \
  --refresh-interval 1000 \
  --num-opponents 5 \
  --iterations 15000 \
  --traversals 400 \
  --log-dir logs/phase3 \
  --save-dir models/phase3
```

重要: 学習率が0.0001に半減されることを確認。されていなければ手動調整。
推定1〜2週間。PCを止める場合は--checkpointオプションで再開可能。

### 5.6 最終品質検証

```text
python scripts/visualize_tournament.py \
  --checkpoints models/phase1_seedX/checkpoint_iter_1500.pt models/phase2/checkpoint_iter_2000.pt models/phase3/checkpoint_iter_5000.pt models/phase3/checkpoint_iter_10000.pt models/phase3/checkpoint_iter_15000.pt \
  --num-games 10000
```

合格基準:
- ランダム相手への利益: 15チップ/ゲーム以上
- Phase 1 checkpointへの勝率: 60%以上
- CLIプレイ（python scripts/play.py）で明らかな異常行動がないこと

最良checkpointを models/deep_cfr/best_checkpoint.pt へコピー。

### 5.7 並行: deep_cfr_bridge.py 設計・実装

訓練中に実装する。ダミーモデル（ランダム出力）でテスト可能。

### 5.8 RTX 3080に関する注記

当初RTX 4080と聞いていたが、RTX 3080（CUDA 8704コア、VRAM 10GB）と訂正された。
推論は問題なし（0.5〜1ms）。訓練はRTX 4080比で2〜3割遅くなる見込み。
VRAM 10GBでリプレイバッファ全GPUロードはできない可能性があるため、
CPU側メモリ管理で対応する。
```

---

## 6. 禁止事項・維持事項

既存の全禁止事項を維持する（snapshot v前回 Section 11参照）。

追加:

- Deep CFR訓練中の中間checkpointを本番推論に使わない
- Deep CFR品質検証前にRust postflop CLIを削除しない
- LLM exploit_adjustmentを廃止しない（Multiway主軸LLMのみ廃止）
- Deep CFR推論失敗時に暫定推奨を出さない

---

## 7. ユーザー要望・進行ルール

既存ルールを維持（snapshot v前回 Section 10参照）。
```