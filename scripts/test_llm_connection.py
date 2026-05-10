"""Manual OpenRouter and LLMPipeline connectivity check.

Run with: python scripts/test_llm_connection.py
"""

import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
import yaml
from dotenv import load_dotenv


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DIRECT_PROMPT = (
    "You are a poker GTO expert. What is a typical UTG RFI range in 6max NLH? "
    "Reply with ONLY a PioSOLVER-format range string, nothing else. "
    "Example format: 'AA,KK,QQ,AKs,AKo'"
)


def _load_config() -> dict[str, Any]:
    """Load config.yaml from the project root."""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        print(f"ERROR: config.yaml not found: {config_path}")
        return {}
    with open(config_path, "r", encoding="utf-8") as file_obj:
        return yaml.safe_load(file_obj) or {}


def _load_env() -> tuple[str | None, str]:
    """Load environment variables required for OpenRouter."""
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
    api_key = os.environ.get("OPENROUTER_API_KEY")
    model = os.environ.get("LLM_MODEL_DEFAULT", "anthropic/claude-sonnet-4")
    return api_key, model


def run_direct_api_call(api_key: str | None, model: str) -> None:
    """Run a direct OpenRouter chat-completions request."""
    print("\n=== Test 1: Direct API Call ===")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY is not set in .env or environment.")
        return

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": DIRECT_PROMPT}],
        "max_tokens": 200,
        "temperature": 0.3,
        "reasoning": {"effort": "none"},
    }

    start = time.perf_counter()
    try:
        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=payload,
            timeout=10,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"Latency: {elapsed_ms:.1f}ms")
        print(f"Status: {response.status_code}")

        try:
            data = response.json()
        except ValueError:
            print("ERROR: Response was not JSON")
            print(response.text)
            return

        message = data.get("choices", [{}])[0].get("message", {})
        content = message.get("content")
        if content is None:
            usage = data.get("usage", {})
            details = usage.get("completion_tokens_details", {})
            print("WARNING: content is None (reasoning may have consumed all tokens)")
            print(f"Reasoning tokens: {details.get('reasoning_tokens', 'N/A')}")
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            if reasoning:
                print(f"Reasoning content preview: {str(reasoning)[:200]}")
        print(f"Response: {content}")
        print(f"Usage: {data.get('usage')}")
    except Exception:
        print("ERROR: Direct API call failed")
        traceback.print_exc()


def run_llm_pipeline_test(config: dict[str, Any]) -> None:
    """Run LLMPipeline.estimate_ranges with a synthetic flop GameState."""
    print("\n=== Test 2: LLMPipeline.estimate_ranges() ===")
    try:
        from core.game_state import create_empty_game_state
        from strategy.llm_pipeline import LLMPipeline

        game_state = create_empty_game_state()
        game_state.phase = "flop"
        game_state.board = ["Qs", "Jh", "2h"]
        game_state.board_card_count = 3
        game_state.hero.cards = ["Ah", "Kh"]
        game_state.hero.position = "BTN"
        game_state.hero.stack = 900
        game_state.hero.bet = 0
        game_state.pot = 200

        baseline_oop = "QQ-88,AJs+,KQs,AJo+,KQo"
        baseline_ip = (
            "22+,A2s+,K9s+,Q9s+,J9s+,T8s+,97s+,87s,76s,65s,"
            "A8o+,K9o+,QTo+,JTo"
        )

        pipeline = LLMPipeline(config)
        start = time.perf_counter()
        result = pipeline.estimate_ranges(
            game_state=game_state,
            opponent_stats=None,
            baseline_range_oop=baseline_oop,
            baseline_range_ip=baseline_ip,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        range_oop = str(result.get("range_oop", ""))
        range_ip = str(result.get("range_ip", ""))
        print(f"Latency: {elapsed_ms:.1f}ms")
        print(f"Source: {result.get('source')}")
        print(f"Adjustments: {result.get('adjustments_made')}")
        print(f"Range OOP ({len(range_oop)} chars): {range_oop}")
        print(f"Range IP  ({len(range_ip)} chars): {range_ip}")
    except Exception:
        print("ERROR: LLMPipeline test failed")
        traceback.print_exc()


def main() -> None:
    """Run both LLM connectivity checks."""
    api_key, model = _load_env()
    config = _load_config()

    print("LLM connection test")
    print(f"Model: {model}")
    print(f"API key configured: {bool(api_key)}")

    run_direct_api_call(api_key, model)
    run_llm_pipeline_test(config)


if __name__ == "__main__":
    main()
