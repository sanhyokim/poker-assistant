"""MW Context Engine LLM comparison test.

Tests GPT-5.4-mini (OpenRouter) and Phi-4-mini (local) on 5 representative
poker scenarios.

Usage:
    cd C:\\Users\\user\\Desktop\\dev\\poker-system
    python scripts/test_llm_comparison.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
import torch
from dotenv import load_dotenv
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

from strategy.context_engine import (
    build_context_prompt,
    compute_full_budget,
    validate_llm_output,
)


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "openai/gpt-5.4-mini"
PHI4_MODEL_PATH = Path("C:/dev/pokerrl-training/models/phi-4-mini-instruct")
RESULT_PATH = Path("scripts/llm_comparison_results.json")
REQUEST_TIMEOUT_SECONDS = 60

CATEGORY_ACTIONS = {
    "value": ["bet", "raise"],
    "passive": ["check", "fold"],
    "continue": ["call", "raise"],
    "cautious": ["call", "check"],
    "aggressive": ["raise", "all_in", "bet"],
}

HERO_SEAT_BY_POSITION = {
    "BTN": 0,
    "SB": 3,
    "BB": 4,
    "CO": 5,
}

CASES: list[dict[str, Any]] = [
    {
        "name": "Dry flop TPGK IP SRP",
        "hero_cards": ["Ah", "Kd"],
        "board_cards": ["Ks", "7c", "2d"],
        "street": "flop",
        "pot_type": "SRP",
        "position": "IP",
        "active_player_count": 3,
        "pot_bb": 12,
        "effective_stack_bb": 100,
        "hero_position": "CO",
        "initiative": True,
        "players_behind": 0,
        "legal_actions": ["fold", "check", "bet"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 5, "action": "RAISE", "amount": 300},
            {"seat": 3, "action": "CALL", "amount": 300},
            {"seat": 4, "action": "CALL", "amount": 300},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
        ],
        "expected_category": "value",
    },
    {
        "name": "Wet flop flush draw OOP 3BP",
        "hero_cards": ["Jh", "Th"],
        "board_cards": ["Qh", "5h", "3c"],
        "street": "flop",
        "pot_type": "3BP",
        "position": "OOP",
        "active_player_count": 3,
        "pot_bb": 24,
        "effective_stack_bb": 85,
        "hero_position": "BB",
        "initiative": False,
        "players_behind": 1,
        "legal_actions": ["fold", "check", "bet"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 1, "action": "RAISE", "amount": 300},
            {"seat": 2, "action": "RAISE", "amount": 900},
            {"seat": 4, "action": "CALL", "amount": 900},
            {"seat": 1, "action": "CALL", "amount": 900},
        ],
        "expected_category": "aggressive",
    },
    {
        "name": "Paired board trips turn IP",
        "hero_cards": ["9s", "9d"],
        "board_cards": ["9h", "6c", "2d", "Jc"],
        "street": "turn",
        "pot_type": "SRP",
        "position": "IP",
        "active_player_count": 3,
        "pot_bb": 18,
        "effective_stack_bb": 90,
        "hero_position": "BTN",
        "initiative": True,
        "players_behind": 0,
        "legal_actions": ["fold", "check", "bet"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 0, "action": "RAISE", "amount": 250},
            {"seat": 3, "action": "CALL", "amount": 250},
            {"seat": 4, "action": "CALL", "amount": 250},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
            {"seat": 0, "action": "BET", "amount": 400},
            {"seat": 3, "action": "CALL", "amount": 400},
            {"seat": 4, "action": "CALL", "amount": 400},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
        ],
        "expected_category": "value",
    },
    {
        "name": "River weak showdown facing bet OOP",
        "hero_cards": ["Ac", "Td"],
        "board_cards": ["Ts", "7h", "3d", "Jc", "2s"],
        "street": "river",
        "pot_type": "SRP",
        "position": "OOP",
        "active_player_count": 3,
        "pot_bb": 30,
        "effective_stack_bb": 70,
        "hero_position": "BB",
        "initiative": False,
        "players_behind": 0,
        "legal_actions": ["fold", "call", "raise"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 1, "action": "RAISE", "amount": 300},
            {"seat": 4, "action": "CALL", "amount": 300},
            {"seat": 3, "action": "CALL", "amount": 300},
            {"seat": 4, "action": "CHECK", "amount": 0},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 1, "action": "BET", "amount": 500},
            {"seat": 4, "action": "CALL", "amount": 500},
            {"seat": 3, "action": "CALL", "amount": 500},
            {"seat": 4, "action": "CHECK", "amount": 0},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 1, "action": "BET", "amount": 800},
            {"seat": 4, "action": "CALL", "amount": 800},
            {"seat": 3, "action": "FOLD", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
            {"seat": 1, "action": "BET", "amount": 1500},
        ],
        "expected_category": "cautious",
    },
    {
        "name": "Monotone flop nut flush IP",
        "hero_cards": ["As", "Qs"],
        "board_cards": ["Js", "8s", "4s"],
        "street": "flop",
        "pot_type": "SRP",
        "position": "IP",
        "active_player_count": 4,
        "pot_bb": 16,
        "effective_stack_bb": 100,
        "hero_position": "BTN",
        "initiative": True,
        "players_behind": 0,
        "legal_actions": ["fold", "check", "bet"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 0, "action": "RAISE", "amount": 300},
            {"seat": 2, "action": "CALL", "amount": 300},
            {"seat": 3, "action": "CALL", "amount": 300},
            {"seat": 4, "action": "CALL", "amount": 300},
            {"seat": 2, "action": "CHECK", "amount": 0},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
        ],
        "expected_category": "value",
    },
    {
        "name": "Dry flop overpair OOP SRP",
        "hero_cards": ["Qh", "Qd"],
        "board_cards": ["9s", "5c", "2d"],
        "street": "flop",
        "pot_type": "SRP",
        "position": "OOP",
        "active_player_count": 3,
        "pot_bb": 12,
        "effective_stack_bb": 100,
        "hero_position": "BB",
        "initiative": False,
        "players_behind": 1,
        "legal_actions": ["fold", "check", "bet"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 0, "action": "RAISE", "amount": 300},
            {"seat": 3, "action": "CALL", "amount": 300},
            {"seat": 4, "action": "CALL", "amount": 300},
        ],
        "expected_category": "value",
    },
    {
        "name": "Wet flop gutshot overcards IP 4way",
        "hero_cards": ["Ah", "Kd"],
        "board_cards": ["Qs", "Jc", "5h"],
        "street": "flop",
        "pot_type": "SRP",
        "position": "IP",
        "active_player_count": 4,
        "pot_bb": 16,
        "effective_stack_bb": 95,
        "hero_position": "BTN",
        "initiative": True,
        "players_behind": 0,
        "legal_actions": ["fold", "check", "bet"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 0, "action": "RAISE", "amount": 300},
            {"seat": 1, "action": "CALL", "amount": 300},
            {"seat": 3, "action": "CALL", "amount": 300},
            {"seat": 4, "action": "CALL", "amount": 300},
            {"seat": 1, "action": "CHECK", "amount": 0},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
        ],
        "expected_category": "value",
    },
    {
        "name": "Turn two pair facing bet OOP",
        "hero_cards": ["Jh", "9h"],
        "board_cards": ["Js", "9c", "4d", "2h"],
        "street": "turn",
        "pot_type": "SRP",
        "position": "OOP",
        "active_player_count": 3,
        "pot_bb": 24,
        "effective_stack_bb": 80,
        "hero_position": "BB",
        "initiative": False,
        "players_behind": 0,
        "legal_actions": ["fold", "call", "raise"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 1, "action": "RAISE", "amount": 300},
            {"seat": 4, "action": "CALL", "amount": 300},
            {"seat": 3, "action": "CALL", "amount": 300},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
            {"seat": 1, "action": "BET", "amount": 500},
            {"seat": 4, "action": "CALL", "amount": 500},
            {"seat": 3, "action": "FOLD", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
            {"seat": 1, "action": "BET", "amount": 800},
        ],
        "expected_category": "continue",
    },
    {
        "name": "Flop trash no initiative OOP 3BP",
        "hero_cards": ["7c", "6c"],
        "board_cards": ["Ks", "Qd", "3h"],
        "street": "flop",
        "pot_type": "3BP",
        "position": "OOP",
        "active_player_count": 3,
        "pot_bb": 24,
        "effective_stack_bb": 80,
        "hero_position": "BB",
        "initiative": False,
        "players_behind": 1,
        "legal_actions": ["fold", "check", "bet"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 1, "action": "RAISE", "amount": 300},
            {"seat": 2, "action": "RAISE", "amount": 900},
            {"seat": 4, "action": "CALL", "amount": 900},
            {"seat": 1, "action": "CALL", "amount": 900},
        ],
        "expected_category": "passive",
    },
    {
        "name": "River top pair facing all-in IP",
        "hero_cards": ["Kh", "Jd"],
        "board_cards": ["Kc", "8s", "5d", "3h", "2c"],
        "street": "river",
        "pot_type": "SRP",
        "position": "IP",
        "active_player_count": 3,
        "pot_bb": 40,
        "effective_stack_bb": 60,
        "hero_position": "CO",
        "initiative": True,
        "players_behind": 0,
        "legal_actions": ["fold", "call"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 5, "action": "RAISE", "amount": 300},
            {"seat": 3, "action": "CALL", "amount": 300},
            {"seat": 4, "action": "CALL", "amount": 300},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
            {"seat": 5, "action": "BET", "amount": 500},
            {"seat": 3, "action": "CALL", "amount": 500},
            {"seat": 4, "action": "FOLD", "amount": 0},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 5, "action": "BET", "amount": 800},
            {"seat": 3, "action": "CALL", "amount": 800},
            {"seat": 3, "action": "ALL_IN", "amount": 6000},
        ],
        "expected_category": "cautious",
    },
    {
        "name": "Flop set on wet board IP 3way",
        "hero_cards": ["8h", "8d"],
        "board_cards": ["8s", "7c", "6d"],
        "street": "flop",
        "pot_type": "SRP",
        "position": "IP",
        "active_player_count": 3,
        "pot_bb": 12,
        "effective_stack_bb": 100,
        "hero_position": "CO",
        "initiative": True,
        "players_behind": 0,
        "legal_actions": ["fold", "check", "bet"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 5, "action": "RAISE", "amount": 300},
            {"seat": 3, "action": "CALL", "amount": 300},
            {"seat": 4, "action": "CALL", "amount": 300},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
        ],
        "expected_category": "value",
    },
    {
        "name": "Turn flush completed missed draw OOP",
        "hero_cards": ["Ad", "Td"],
        "board_cards": ["Kd", "7d", "3s", "Jc"],
        "street": "turn",
        "pot_type": "SRP",
        "position": "OOP",
        "active_player_count": 3,
        "pot_bb": 18,
        "effective_stack_bb": 85,
        "hero_position": "BB",
        "initiative": False,
        "players_behind": 1,
        "legal_actions": ["fold", "check", "bet"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 1, "action": "RAISE", "amount": 300},
            {"seat": 4, "action": "CALL", "amount": 300},
            {"seat": 3, "action": "CALL", "amount": 300},
            {"seat": 4, "action": "CHECK", "amount": 0},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 1, "action": "BET", "amount": 500},
            {"seat": 4, "action": "CALL", "amount": 500},
            {"seat": 3, "action": "CALL", "amount": 500},
            {"seat": 4, "action": "CHECK", "amount": 0},
        ],
        "expected_category": "value",
    },
    {
        "name": "Flop OESD IP limp 5way",
        "hero_cards": ["Ts", "9s"],
        "board_cards": ["8c", "7d", "2h"],
        "street": "flop",
        "pot_type": "limp",
        "position": "IP",
        "active_player_count": 5,
        "pot_bb": 5,
        "effective_stack_bb": 100,
        "hero_position": "BTN",
        "initiative": False,
        "players_behind": 0,
        "legal_actions": ["fold", "check", "bet"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 1, "action": "CALL", "amount": 100},
            {"seat": 2, "action": "CALL", "amount": 100},
            {"seat": 0, "action": "CALL", "amount": 100},
            {"seat": 3, "action": "CALL", "amount": 100},
            {"seat": 1, "action": "CHECK", "amount": 0},
            {"seat": 2, "action": "CHECK", "amount": 0},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
        ],
        "expected_category": "value",
    },
    {
        "name": "Turn second pair check-check IP",
        "hero_cards": ["Ac", "Jc"],
        "board_cards": ["Qs", "Jh", "5d", "8c"],
        "street": "turn",
        "pot_type": "SRP",
        "position": "IP",
        "active_player_count": 3,
        "pot_bb": 12,
        "effective_stack_bb": 94,
        "hero_position": "CO",
        "initiative": True,
        "players_behind": 0,
        "legal_actions": ["fold", "check", "bet"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 5, "action": "RAISE", "amount": 300},
            {"seat": 3, "action": "CALL", "amount": 300},
            {"seat": 4, "action": "CALL", "amount": 300},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
            {"seat": 5, "action": "CHECK", "amount": 0},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
        ],
        "expected_category": "value",
    },
    {
        "name": "River bluff catcher small bet OOP",
        "hero_cards": ["Ah", "9h"],
        "board_cards": ["Kd", "9c", "4s", "2d", "7h"],
        "street": "river",
        "pot_type": "SRP",
        "position": "OOP",
        "active_player_count": 3,
        "pot_bb": 20,
        "effective_stack_bb": 80,
        "hero_position": "BB",
        "initiative": False,
        "players_behind": 0,
        "legal_actions": ["fold", "call", "raise"],
        "action_history": [
            {"seat": 3, "action": "BLIND_SB", "amount": 50},
            {"seat": 4, "action": "BLIND_BB", "amount": 100},
            {"seat": 1, "action": "RAISE", "amount": 300},
            {"seat": 4, "action": "CALL", "amount": 300},
            {"seat": 3, "action": "CALL", "amount": 300},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
            {"seat": 1, "action": "CHECK", "amount": 0},
            {"seat": 3, "action": "CHECK", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
            {"seat": 1, "action": "CHECK", "amount": 0},
            {"seat": 4, "action": "CHECK", "amount": 0},
            {"seat": 1, "action": "BET", "amount": 500},
        ],
        "expected_category": "cautious",
    },
]


def call_openrouter(prompt: str, model: str = OPENROUTER_MODEL) -> tuple[str, float]:
    """Call OpenRouter chat completions and return response text plus latency."""
    started = time.perf_counter()
    api_key = os.getenv("OPENROUTER_API_KEY")
    if api_key is None:
        latency_ms = (time.perf_counter() - started) * 1000
        return "", latency_ms

    try:
        response = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "temperature": 0.2,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        return str(content), (time.perf_counter() - started) * 1000
    except (KeyError, requests.RequestException, ValueError):
        return "", (time.perf_counter() - started) * 1000


def load_phi4_model() -> tuple[Any, Any, float]:
    """Load Phi-4-mini with 4-bit quantization and return model, tokenizer, latency."""
    started = time.perf_counter()
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    tokenizer = AutoTokenizer.from_pretrained(PHI4_MODEL_PATH, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(
        PHI4_MODEL_PATH,
        quantization_config=quantization,
        device_map="auto",
        trust_remote_code=False,
    )
    return model, tokenizer, (time.perf_counter() - started) * 1000


def call_phi4(prompt: str, model: Any, tokenizer: Any) -> tuple[str, float]:
    """Run local Phi-4-mini inference and return response text plus latency."""
    started = time.perf_counter()
    messages = [{"role": "user", "content": prompt}]
    inputs = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
    )
    inputs = {key: value.to(model.device) for key, value in inputs.items()}
    input_length = int(inputs["input_ids"].shape[-1])
    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=200,
            temperature=0.2,
            do_sample=True,
        )
    response_ids = output_ids[0][input_length:]
    text = tokenizer.decode(response_ids, skip_special_tokens=True).strip()
    return text, (time.perf_counter() - started) * 1000


def main() -> None:
    """Run GPT-5.4-mini and Phi-4-mini through the Context Engine pipeline."""
    warnings.filterwarnings("ignore", category=FutureWarning)
    logging.getLogger("transformers").setLevel(logging.ERROR)
    load_dotenv(override=True)
    prepared_cases = [_prepare_case(case) for case in CASES]
    results = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "models": {},
    }

    gpt_cases = _run_model_cases(
        cases=prepared_cases,
        caller=lambda prompt: call_openrouter(prompt),
    )
    results["models"]["gpt-5.4-mini"] = {
        "cases": gpt_cases,
        "summary": _summarize_cases(gpt_cases),
    }

    try:
        phi_model, phi_tokenizer, phi_load_ms = load_phi4_model()
    except Exception as e:
        print(f"\n[Phi-4-mini] Load FAILED: {e}\n", flush=True)
        phi_model = phi_tokenizer = None
        phi_load_ms = 0.0
    if phi_model is None or phi_tokenizer is None:
        phi_cases: list[dict[str, Any]] = []
    else:
        phi_cases = _run_model_cases(
            cases=prepared_cases,
            caller=lambda prompt: call_phi4(prompt, phi_model, phi_tokenizer),
        )
        phi_summary = _summarize_cases(phi_cases)
        phi_summary["model_load_ms"] = round(phi_load_ms)
        results["models"]["phi-4-mini"] = {
            "cases": phi_cases,
            "summary": phi_summary,
        }

    _print_case_results(gpt_cases, phi_cases)
    _print_summary(results["models"])
    RESULT_PATH.write_text(
        json.dumps(results, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _prepare_case(case: dict[str, Any]) -> dict[str, Any]:
    game_context = {
        "street": case["street"],
        "pot_type": case["pot_type"],
        "position": case["position"],
        "pot_bb": case["pot_bb"],
        "effective_stack_bb": case["effective_stack_bb"],
        "spr": case["effective_stack_bb"] / case["pot_bb"],
        "hero_position": case["hero_position"],
        "active_players": case["active_player_count"],
        "hero_cards": case["hero_cards"],
        "board_cards": case["board_cards"],
        "legal_actions": case["legal_actions"],
        "action_history": case["action_history"],
        "initiative": case["initiative"],
        "players_behind": case["players_behind"],
    }
    budget_result = compute_full_budget(
        hero_cards=case["hero_cards"],
        board_cards=case["board_cards"],
        pot_type=case["pot_type"],
        position=case["position"],
        active_player_count=case["active_player_count"],
        street=case["street"],
        action_history=case["action_history"],
        hero_seat=HERO_SEAT_BY_POSITION[str(case["hero_position"])],
        pot_history=_build_pot_history(case["action_history"]),
    )
    return {
        "case": case,
        "game_context": game_context,
        "budget_result": budget_result,
        "prompt": build_context_prompt(budget_result, game_context),
    }


def _build_pot_history(action_history: list[dict[str, object]]) -> list[int]:
    pot = 0
    pot_history = []
    for action in action_history:
        pot_history.append(pot)
        amount = action.get("amount", 0)
        if isinstance(amount, int):
            pot += amount
    return pot_history


def _run_model_cases(
    cases: list[dict[str, Any]],
    caller: Any,
) -> list[dict[str, Any]]:
    results = []
    for prepared in cases:
        response_text, latency_ms = caller(prepared["prompt"])
        result = _evaluate_response(prepared, response_text, latency_ms)
        results.append(result)
    return results


def _evaluate_response(
    prepared: dict[str, Any],
    response_text: str,
    latency_ms: float,
) -> dict[str, Any]:
    case = prepared["case"]
    budget_result = prepared["budget_result"]
    legal_actions = case["legal_actions"]
    viable_actions = [
        action for action in budget_result["viable_actions"] if action in legal_actions
    ]
    extracted_json = _extract_json_object(response_text)
    json_parse_ok = _can_parse_json(extracted_json)
    validation = validate_llm_output(
        extracted_json,
        viable_actions=viable_actions,
        legal_actions=legal_actions,
        effective_stack_bb=float(case["effective_stack_bb"]),
    )
    action = validation["action"]
    expected_actions = CATEGORY_ACTIONS[str(case["expected_category"])]
    return {
        "name": case["name"],
        "expected_category": case["expected_category"],
        "raw_response": response_text,
        "json_payload": extracted_json,
        "json_parse": json_parse_ok,
        "action": action,
        "amount_bb": validation["amount_bb"],
        "reason": validation["reason"],
        "valid": validation["is_valid"],
        "correction": validation["correction_applied"],
        "action_viable": action in viable_actions,
        "category_match": action in expected_actions,
        "latency_ms": round(latency_ms),
        "viable_actions": viable_actions,
    }


def _extract_json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return stripped
    return stripped[start : end + 1]


def _can_parse_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except json.JSONDecodeError:
        return False


def _format_case_result(label: str, result: dict[str, Any]) -> str:
    amount = result["amount_bb"]
    amount_text = "null" if amount is None else f"{float(amount):g}"
    return (
        f"[{label}] action={result['action']}, amount_bb={amount_text}, "
        f"reason={json.dumps(result['reason'], ensure_ascii=False)}\n"
        f"  valid={result['valid']}, correction={result['correction']}, "
        f"latency={result['latency_ms']}ms, "
        f"category_match={result['category_match']}"
    )


def _print_case_results(
    gpt_cases: list[dict[str, Any]],
    phi_cases: list[dict[str, Any]],
) -> None:
    phi_by_name = {str(result["name"]): result for result in phi_cases}
    for index, gpt_result in enumerate(gpt_cases, start=1):
        print(f"=== Case {index}: {gpt_result['name']} ===")
        print(_format_case_result("GPT-5.4-mini", gpt_result))
        phi_result = phi_by_name.get(str(gpt_result["name"]))
        if phi_result is not None:
            print(_format_case_result("Phi-4-mini", phi_result))


def _summarize_cases(cases: list[dict[str, Any]]) -> dict[str, int | float]:
    total = len(cases)
    avg_latency = sum(float(case["latency_ms"]) for case in cases) / max(total, 1)
    return {
        "total": total,
        "json_parse": sum(1 for case in cases if case["json_parse"]),
        "action_viable": sum(1 for case in cases if case["action_viable"]),
        "category_match": sum(1 for case in cases if case["category_match"]),
        "corrections": sum(1 for case in cases if case["correction"] is not None),
        "avg_latency_ms": round(avg_latency),
    }


def _print_summary(models: dict[str, dict[str, Any]]) -> None:
    print("========== SUMMARY ==========")
    gpt_model = models.get("gpt-5.4-mini")
    if gpt_model is not None:
        _print_model_summary("GPT-5.4-mini (OpenRouter)", gpt_model["summary"])
    phi_model = models.get("phi-4-mini")
    if phi_model is not None:
        _print_model_summary("Phi-4-mini (local 4bit)", phi_model["summary"])
    print("==============================")


def _print_model_summary(name: str, summary: dict[str, Any]) -> None:
    total = summary["total"]
    print(f"Model: {name}")
    print(f"  JSON parse:     {summary['json_parse']}/{total}")
    print(f"  Action viable:  {summary['action_viable']}/{total}")
    print(f"  Category match: {summary['category_match']}/{total}")
    print(f"  Corrections:    {summary['corrections']}/{total}")
    print(f"  Avg latency:    {summary['avg_latency_ms']}ms")
    if "model_load_ms" in summary:
        print(f"  Model load:     {summary['model_load_ms']}ms")
    print()


if __name__ == "__main__":
    main()


