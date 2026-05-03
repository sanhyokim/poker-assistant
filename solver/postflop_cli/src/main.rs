use std::collections::HashMap;
use std::io::{self, BufRead, Write};
use std::time::{Duration, Instant};

use postflop_solver::*;
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
struct SolveRequest {
    board: String,
    turn: Option<String>,
    river: Option<String>,
    range_oop: String,
    range_ip: String,
    starting_pot: i32,
    effective_stack: i32,
    flop_bet_sizes_oop: String,
    flop_bet_sizes_ip: String,
    flop_raise_sizes_oop: String,
    flop_raise_sizes_ip: String,
    turn_bet_sizes_oop: String,
    turn_bet_sizes_ip: String,
    turn_raise_sizes_oop: String,
    turn_raise_sizes_ip: String,
    river_bet_sizes_oop: String,
    river_bet_sizes_ip: String,
    river_raise_sizes_oop: String,
    river_raise_sizes_ip: String,
    rake_rate: f64,
    rake_cap: f64,
    add_allin_threshold: f64,
    force_allin_threshold: f64,
    merging_threshold: f64,
    max_iterations: u32,
    target_exploitability_pct: f64,
    timeout_ms: u64,
    bunching: Option<serde_json::Value>,
}

#[derive(Serialize)]
struct SolveResponse {
    success: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    exploitability: f64,
    exploitability_pct: f64,
    solve_time_ms: u64,
    memory_usage_bytes: u64,
    iterations_run: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    root_strategy: Option<RootStrategy>,
    queried_nodes: Vec<serde_json::Value>,
}

#[derive(Serialize)]
struct RootStrategy {
    actions: Vec<String>,
    hands: Vec<String>,
    strategy_matrix: Vec<Vec<f64>>,
    equity: Vec<f64>,
    ev: Vec<f64>,
    average_strategy: HashMap<String, f64>,
}

fn main() {
    eprintln!("ready");

    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut stdout_lock = stdout.lock();

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(value) => value.trim().to_string(),
            Err(error) => {
                eprintln!("stdin read error: {}", error);
                continue;
            }
        };

        if line.is_empty() {
            continue;
        }

        let response = process_request(&line);
        let json = serde_json::to_string(&response).unwrap_or_else(|error| {
            serde_json::json!({
                "success": false,
                "error": format!("serialize error: {}", error),
                "exploitability": 0,
                "exploitability_pct": 0,
                "solve_time_ms": 0,
                "memory_usage_bytes": 0,
                "iterations_run": 0,
                "queried_nodes": [],
            })
            .to_string()
        });

        writeln!(stdout_lock, "{}", json).ok();
        stdout_lock.flush().ok();
    }
}

fn process_request(line: &str) -> SolveResponse {
    let started_at = Instant::now();
    let req = match serde_json::from_str::<SolveRequest>(line) {
        Ok(value) => value,
        Err(error) => return error_response(format!("json parse error: {}", error), started_at),
    };
    let _ = &req.bunching;

    let flop = match flop_from_str(&req.board) {
        Ok(value) => value,
        Err(error) => return error_response(format!("invalid board: {}", error), started_at),
    };
    let turn = match optional_card_from_str(req.turn.as_deref(), "turn") {
        Ok(value) => value,
        Err(error) => return error_response(error, started_at),
    };
    let river = match optional_card_from_str(req.river.as_deref(), "river") {
        Ok(value) => value,
        Err(error) => return error_response(error, started_at),
    };
    let initial_state = if river != NOT_DEALT {
        BoardState::River
    } else if turn != NOT_DEALT {
        BoardState::Turn
    } else {
        BoardState::Flop
    };

    let range_oop = match req.range_oop.parse::<Range>() {
        Ok(value) => value,
        Err(error) => return error_response(format!("invalid range_oop: {}", error), started_at),
    };
    let range_ip = match req.range_ip.parse::<Range>() {
        Ok(value) => value,
        Err(error) => return error_response(format!("invalid range_ip: {}", error), started_at),
    };

    let flop_bet_sizes_oop = match parse_bet_sizes(
        &req.flop_bet_sizes_oop,
        &req.flop_raise_sizes_oop,
        "flop oop",
        started_at,
    ) {
        Ok(value) => value,
        Err(response) => return response,
    };
    let flop_bet_sizes_ip = match parse_bet_sizes(
        &req.flop_bet_sizes_ip,
        &req.flop_raise_sizes_ip,
        "flop ip",
        started_at,
    ) {
        Ok(value) => value,
        Err(response) => return response,
    };
    let turn_bet_sizes_oop = match parse_bet_sizes(
        &req.turn_bet_sizes_oop,
        &req.turn_raise_sizes_oop,
        "turn oop",
        started_at,
    ) {
        Ok(value) => value,
        Err(response) => return response,
    };
    let turn_bet_sizes_ip = match parse_bet_sizes(
        &req.turn_bet_sizes_ip,
        &req.turn_raise_sizes_ip,
        "turn ip",
        started_at,
    ) {
        Ok(value) => value,
        Err(response) => return response,
    };
    let river_bet_sizes_oop = match parse_bet_sizes(
        &req.river_bet_sizes_oop,
        &req.river_raise_sizes_oop,
        "river oop",
        started_at,
    ) {
        Ok(value) => value,
        Err(response) => return response,
    };
    let river_bet_sizes_ip = match parse_bet_sizes(
        &req.river_bet_sizes_ip,
        &req.river_raise_sizes_ip,
        "river ip",
        started_at,
    ) {
        Ok(value) => value,
        Err(response) => return response,
    };

    let card_config = CardConfig {
        range: [range_oop, range_ip],
        flop,
        turn,
        river,
    };
    let tree_config = TreeConfig {
        initial_state,
        starting_pot: req.starting_pot,
        effective_stack: req.effective_stack,
        rake_rate: req.rake_rate,
        rake_cap: req.rake_cap,
        flop_bet_sizes: [flop_bet_sizes_oop, flop_bet_sizes_ip],
        turn_bet_sizes: [turn_bet_sizes_oop, turn_bet_sizes_ip],
        river_bet_sizes: [river_bet_sizes_oop, river_bet_sizes_ip],
        turn_donk_sizes: None,
        river_donk_sizes: None,
        add_allin_threshold: req.add_allin_threshold,
        force_allin_threshold: req.force_allin_threshold,
        merging_threshold: req.merging_threshold,
    };

    let action_tree = match ActionTree::new(tree_config) {
        Ok(value) => value,
        Err(error) => return error_response(format!("action tree error: {}", error), started_at),
    };
    let mut game = match PostFlopGame::with_config(card_config, action_tree) {
        Ok(value) => value,
        Err(error) => return error_response(format!("game config error: {}", error), started_at),
    };

    let (memory_usage_bytes, _) = game.memory_usage();
    game.allocate_memory(false);

    let timeout = Duration::from_millis(req.timeout_ms);
    let target_exploitability = req.starting_pot as f64 * (req.target_exploitability_pct / 100.0);
    let mut iterations_run = 0;

    for iteration in 1..=req.max_iterations {
        solve_step(&game, iteration);
        iterations_run = iteration;

        if iteration % 10 == 0 {
            let exploitability = compute_exploitability(&game) as f64;
            if exploitability <= target_exploitability || started_at.elapsed() >= timeout {
                break;
            }
        } else if started_at.elapsed() >= timeout {
            break;
        }
    }

    finalize(&mut game);
    let final_exploitability = compute_exploitability(&game) as f64;
    let exploitability_pct = if req.starting_pot > 0 {
        final_exploitability / req.starting_pot as f64 * 100.0
    } else {
        0.0
    };

    game.back_to_root();
    game.cache_normalized_weights();

    let root_strategy = match extract_root_strategy(&game) {
        Ok(value) => Some(value),
        Err(error) => return error_response(error, started_at),
    };

    SolveResponse {
        success: true,
        error: None,
        exploitability: final_exploitability,
        exploitability_pct,
        solve_time_ms: elapsed_ms(started_at),
        memory_usage_bytes,
        iterations_run,
        root_strategy,
        queried_nodes: Vec::new(),
    }
}

fn parse_bet_sizes(
    bet_sizes: &str,
    raise_sizes: &str,
    label: &str,
    started_at: Instant,
) -> Result<BetSizeOptions, SolveResponse> {
    BetSizeOptions::try_from((bet_sizes, raise_sizes)).map_err(|error| {
        error_response(
            format!("invalid bet sizes for {}: {}", label, error),
            started_at,
        )
    })
}

fn optional_card_from_str(value: Option<&str>, label: &str) -> Result<Card, String> {
    match value {
        Some(card) if !card.trim().is_empty() => {
            let card = card.trim();
            card_from_str(card).map_err(|error| format!("invalid {} card: {}", label, error))
        }
        _ => Ok(NOT_DEALT),
    }
}

fn extract_root_strategy(game: &PostFlopGame) -> Result<RootStrategy, String> {
    let raw_actions = game.available_actions();
    let actions: Vec<String> = raw_actions.iter().map(format_action).collect();
    let hands = holes_to_strings(game.private_cards(0))
        .map_err(|error| format!("private card conversion error: {}", error))?;
    let num_actions = actions.len();
    let num_hands = hands.len();

    let strategy_raw = game.strategy();
    let mut strategy_matrix = vec![vec![0.0; num_actions]; num_hands];
    for action_idx in 0..num_actions {
        for hand_idx in 0..num_hands {
            let raw_idx = action_idx * num_hands + hand_idx;
            strategy_matrix[hand_idx][action_idx] = strategy_raw[raw_idx] as f64;
        }
    }

    let equity: Vec<f64> = game.equity(0).iter().map(|value| *value as f64).collect();
    let ev: Vec<f64> = game
        .expected_values(0)
        .iter()
        .map(|value| *value as f64)
        .collect();
    let weights = game.normalized_weights(0);
    let total_weight: f64 = weights.iter().map(|value| *value as f64).sum();
    let mut average_strategy = HashMap::new();

    for (action_idx, action_name) in actions.iter().enumerate() {
        let weighted_sum = (0..num_hands)
            .map(|hand_idx| {
                let probability = strategy_matrix[hand_idx][action_idx];
                let weight = weights[hand_idx] as f64;
                probability * weight
            })
            .sum::<f64>();
        let average = if total_weight > 0.0 {
            weighted_sum / total_weight
        } else {
            0.0
        };
        average_strategy.insert(action_name.clone(), average);
    }

    Ok(RootStrategy {
        actions,
        hands,
        strategy_matrix,
        equity,
        ev,
        average_strategy,
    })
}

fn format_action(action: &Action) -> String {
    match action {
        Action::None => "None".to_string(),
        Action::Fold => "Fold".to_string(),
        Action::Check => "Check".to_string(),
        Action::Call => "Call".to_string(),
        Action::Bet(amount) => format!("Bet {}", amount),
        Action::Raise(amount) => format!("Raise {}", amount),
        Action::AllIn(amount) => format!("AllIn {}", amount),
        Action::Chance(card) => format!("Chance {}", card),
    }
}

fn error_response(error: String, started_at: Instant) -> SolveResponse {
    eprintln!("{}", error);
    SolveResponse {
        success: false,
        error: Some(error),
        exploitability: 0.0,
        exploitability_pct: 0.0,
        solve_time_ms: elapsed_ms(started_at),
        memory_usage_bytes: 0,
        iterations_run: 0,
        root_strategy: None,
        queried_nodes: Vec::new(),
    }
}

fn elapsed_ms(started_at: Instant) -> u64 {
    started_at
        .elapsed()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}
