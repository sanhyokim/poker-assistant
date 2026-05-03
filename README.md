# Poker AI Assistant

CoinPokerの6人テーブル（NLH）において、キャプチャカード経由で画面認識し、
GTO最適アクションを算出してHUDオーバーレイで推奨手を表示するシステム。

## 技術スタック
- Python 3.11.9 + PyTorch 2.11.0+cu130
- EasyOCR GPU / OpenCV / PyQt6
- postflop-solver (Rust CLI) / eval7
- OpenRouter LLM API
- SQLite3

## セットアップ
```bash
pip install -r requirements.txt
cp .env.example .env
# .env にAPIキーを設定
```

## 実行
```bash
python main.py
```

## テスト
```bash
pytest
pytest -v
pytest --cov=recognition --cov=core
```

## ライセンス
本プロジェクトはpostflop-solver（AGPL-v3）を含みます。
詳細はLICENSE-AGPL-v3を参照してください。
