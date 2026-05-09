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
    actions_played: Option<Vec<String>>,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    node_strategy: Option<RootStrategy>,
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

    let root_strategy = match extract_strategy(&game) {
        Ok(value) => Some(value),
        Err(error) => return error_response(error, started_at),
    };

    let node_strategy = if let Some(ref actions) = req.actions_played {
        if actions.is_empty() {
            None
        } else {
            match navigate_and_extract(&mut game, actions) {
                Ok(strategy) => Some(strategy),
                Err(error) => {
                    eprintln!("actions_played navigation warning: {}", error);
                    None
                }
            }
        }
    } else {
        None
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
        node_strategy,
        queried_nodes: Vec::new(),
    }
}

fn navigate_and_extract(
    game: &mut PostFlopGame,
    actions_played: &[String],
) -> Result<RootStrategy, String> {
    game.back_to_root();

    for (step, action_str) in actions_played.iter().enumerate() {
        if game.is_terminal_node() {
            return Err(format!(
                "reached terminal node at step {} before playing '{}'",
                step, action_str
            ));
        }

        if game.is_chance_node() {
            let card_str = action_str.trim();
            let card = card_from_str(card_str)
                .map_err(|e| format!("invalid chance card '{}' at step {}: {}", card_str, step, e))?;
            let possible = game.possible_cards();
            if possible & (1u64 << card) == 0 {
                return Err(format!(
                    "card '{}' is not a possible deal at step {}",
                    card_str, step
                ));
            }
            game.play(card as usize);
            continue;
        }

        let available = game.available_actions();
        let action_index = match_action(&available, action_str)
            .ok_or_else(|| {
                let available_strs: Vec<String> =
                    available.iter().map(format_action).collect();
                format!(
                    "action '{}' not found at step {}. available: {:?}",
                    action_str, step, available_strs
                )
            })?;
        game.play(action_index);
    }

    if game.is_terminal_node() {
        return Err("navigation ended at a terminal node".to_string());
    }

    if game.is_chance_node() {
        return Err(
            "navigation ended at a chance node (turn/river deal pending)".to_string(),
        );
    }

    game.cache_normalized_weights();
    extract_strategy(game)
}

fn match_action(available: &[Action], action_str: &str) -> Option<usize> {
    let normalized = action_str.trim().to_lowercase();
    let parts: Vec<&str> = normalized.split_whitespace().collect();
    let action_word = parts.first().map(|s| s.as_ref()).unwrap_or("");
    let amount: Option<i32> = parts.get(1).and_then(|s| s.parse().ok());

    for (i, action) in available.iter().enumerate() {
        if format_action(action).to_lowercase() == normalized {
            return Some(i);
        }
    }

    match action_word {
        "fold" => available.iter().position(|a| matches!(a, Action::Fold)),
        "check" => available.iter().position(|a| matches!(a, Action::Check)),
        "call" => available.iter().position(|a| matches!(a, Action::Call)),
        "bet" => {
            if let Some(target) = amount {
                find_closest_sized(available, target, |a| {
                    if let Action::Bet(v) = a { Some(*v) } else { None }
                })
            } else {
                available.iter().position(|a| matches!(a, Action::Bet(_)))
            }
        }
        "raise" => {
            if let Some(target) = amount {
                find_closest_sized(available, target, |a| {
                    if let Action::Raise(v) = a { Some(*v) } else { None }
                })
            } else {
                available.iter().position(|a| matches!(a, Action::Raise(_)))
            }
        }
        "allin" | "all_in" | "all-in" => {
            available.iter().position(|a| matches!(a, Action::AllIn(_)))
        }
        _ => None,
    }
}

fn find_closest_sized<F>(available: &[Action], target: i32, extractor: F) -> Option<usize>
where
    F: Fn(&Action) -> Option<i32>,
{
    let mut best_index: Option<usize> = None;
    let mut best_diff = i32::MAX;
    for (i, action) in available.iter().enumerate() {
        if let Some(value) = extractor(action) {
            let diff = (value - target).abs();
            if diff < best_diff {
                best_diff = diff;
                best_index = Some(i);
            }
        }
    }
    best_index
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

fn extract_strategy(game: &PostFlopGame) -> Result<RootStrategy, String> {
    let raw_actions = game.available_actions();
    let actions: Vec<String> = raw_actions.iter().map(format_action).collect();

    let current_player = game.current_player();
    let hands = holes_to_strings(game.private_cards(current_player))
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

    let equity: Vec<f64> = game.equity(current_player).iter().map(|v| *v as f64).collect();
    let ev: Vec<f64> = game
        .expected_values(current_player)
        .iter()
        .map(|v| *v as f64)
        .collect();
    let weights = game.normalized_weights(current_player);
    let total_weight: f64 = weights.iter().map(|v| *v as f64).sum();
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
        node_strategy: None,
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
