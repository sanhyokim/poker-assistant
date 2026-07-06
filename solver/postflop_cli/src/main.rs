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
    turn_donk_sizes: Option<String>,
    river_bet_sizes_oop: String,
    river_bet_sizes_ip: String,
    river_raise_sizes_oop: String,
    river_raise_sizes_ip: String,
    river_donk_sizes: Option<String>,
    rake_rate: f64,
    rake_cap: f64,
    add_allin_threshold: f64,
    force_allin_threshold: f64,
    merging_threshold: f64,
    max_iterations: u32,
    target_exploitability_pct: f64,
    timeout_ms: u64,
    bunching: Option<serde_json::Value>,
    enable_compression: Option<bool>,
    actions_played: Option<Vec<String>>,
    actions_played_many: Option<Vec<Vec<String>>>,
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
    memory_uncompressed: u64,
    memory_compressed: u64,
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
    action_ev_matrix: Vec<Vec<f64>>,
    equity: Vec<f64>,
    ev: Vec<f64>,
    average_strategy: HashMap<String, f64>,
}

struct ExtractedNode {
    strategy: RootStrategy,
    weights: Vec<f64>,
    hands_oop: Vec<String>,
    hands_ip: Vec<String>,
    raw_weights_oop: Vec<f64>,
    raw_weights_ip: Vec<f64>,
    normalized_weights_oop: Vec<f64>,
    normalized_weights_ip: Vec<f64>,
    current_player: String,
    pot: i32,
    effective_stack_oop: i32,
    effective_stack_ip: i32,
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
    let turn_donk_sizes =
        match parse_donk_sizes(req.turn_donk_sizes.as_deref(), "turn donk", started_at) {
            Ok(value) => value,
            Err(response) => return response,
        };
    let river_donk_sizes =
        match parse_donk_sizes(req.river_donk_sizes.as_deref(), "river donk", started_at) {
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
        turn_donk_sizes,
        river_donk_sizes,
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

    let (memory_uncompressed, memory_compressed) = game.memory_usage();
    let compress = req.enable_compression.unwrap_or(false);
    let memory_usage_bytes = if compress {
        memory_compressed
    } else {
        memory_uncompressed
    };
    game.allocate_memory(compress);

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
    let queried_nodes = if let Some(ref paths) = req.actions_played_many {
        extract_queried_nodes(&mut game, paths, req.starting_pot, req.effective_stack)
    } else {
        Vec::new()
    };

    SolveResponse {
        success: true,
        error: None,
        exploitability: final_exploitability,
        exploitability_pct,
        solve_time_ms: elapsed_ms(started_at),
        memory_usage_bytes,
        memory_uncompressed,
        memory_compressed,
        iterations_run,
        root_strategy,
        node_strategy,
        queried_nodes,
    }
}

fn extract_queried_nodes(
    game: &mut PostFlopGame,
    paths: &[Vec<String>],
    starting_pot: i32,
    effective_stack: i32,
) -> Vec<serde_json::Value> {
    paths
        .iter()
        .map(
            |path| match navigate_and_extract_node(game, path, starting_pot, effective_stack) {
                Ok(node) => {
                    let strategy = node.strategy;
                    let available_actions = strategy.actions.clone();
                    serde_json::json!({
                        "path": path,
                        "available_actions": available_actions,
                        "weights": node.weights,
                        "current_player": node.current_player,
                        "hands_oop": node.hands_oop,
                        "hands_ip": node.hands_ip,
                        "raw_weights_oop": node.raw_weights_oop,
                        "raw_weights_ip": node.raw_weights_ip,
                        "normalized_weights_oop": node.normalized_weights_oop,
                        "normalized_weights_ip": node.normalized_weights_ip,
                        "pot": node.pot,
                        "effective_stack_oop": node.effective_stack_oop,
                        "effective_stack_ip": node.effective_stack_ip,
                        "strategy": strategy,
                    })
                }
                Err(error) => serde_json::json!({
                    "path": path,
                    "error": error,
                }),
            },
        )
        .collect()
}

fn navigate_and_extract(
    game: &mut PostFlopGame,
    actions_played: &[String],
) -> Result<RootStrategy, String> {
    navigate_to_node(game, actions_played)?;
    game.cache_normalized_weights();
    extract_strategy(game)
}

fn navigate_and_extract_node(
    game: &mut PostFlopGame,
    actions_played: &[String],
    starting_pot: i32,
    effective_stack: i32,
) -> Result<ExtractedNode, String> {
    navigate_to_node(game, actions_played)?;
    game.cache_normalized_weights();
    let current_player = game.current_player();
    let hands_oop = holes_to_strings(game.private_cards(0))
        .map_err(|error| format!("OOP hand conversion error: {}", error))?;
    let hands_ip = holes_to_strings(game.private_cards(1))
        .map_err(|error| format!("IP hand conversion error: {}", error))?;
    let raw_weights_oop = weights_to_f64(game.weights(0));
    let raw_weights_ip = weights_to_f64(game.weights(1));
    let normalized_weights_oop = weights_to_f64(game.normalized_weights(0));
    let normalized_weights_ip = weights_to_f64(game.normalized_weights(1));
    let weights = if current_player == 0 {
        normalized_weights_oop.clone()
    } else {
        normalized_weights_ip.clone()
    };
    let total_bet_amount = game.total_bet_amount();
    let current_player = if current_player == 0 { "OOP" } else { "IP" }.to_string();
    let strategy = extract_strategy(game)?;
    Ok(ExtractedNode {
        strategy,
        weights,
        hands_oop,
        hands_ip,
        raw_weights_oop,
        raw_weights_ip,
        normalized_weights_oop,
        normalized_weights_ip,
        current_player,
        pot: starting_pot + total_bet_amount[0] + total_bet_amount[1],
        effective_stack_oop: effective_stack - total_bet_amount[0],
        effective_stack_ip: effective_stack - total_bet_amount[1],
    })
}

fn weights_to_f64(weights: &[f32]) -> Vec<f64> {
    weights.iter().map(|v| *v as f64).collect()
}

fn navigate_to_node(game: &mut PostFlopGame, actions_played: &[String]) -> Result<(), String> {
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
            let card = card_from_str(card_str).map_err(|e| {
                format!("invalid chance card '{}' at step {}: {}", card_str, step, e)
            })?;
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
        let action_index = match_action(&available, action_str).ok_or_else(|| {
            let available_strs: Vec<String> = available.iter().map(format_action).collect();
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
        return Err("navigation ended at a chance node (turn/river deal pending)".to_string());
    }

    Ok(())
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
                    if let Action::Bet(v) = a {
                        Some(*v)
                    } else {
                        None
                    }
                })
            } else {
                available.iter().position(|a| matches!(a, Action::Bet(_)))
            }
        }
        "raise" => {
            if let Some(target) = amount {
                find_closest_sized(available, target, |a| {
                    if let Action::Raise(v) = a {
                        Some(*v)
                    } else {
                        None
                    }
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

fn parse_donk_sizes(
    donk_sizes: Option<&str>,
    label: &str,
    started_at: Instant,
) -> Result<Option<DonkSizeOptions>, SolveResponse> {
    match donk_sizes.map(str::trim) {
        Some(value) if !value.is_empty() => {
            DonkSizeOptions::try_from(value).map(Some).map_err(|error| {
                error_response(
                    format!("invalid donk sizes for {}: {}", label, error),
                    started_at,
                )
            })
        }
        _ => Ok(None),
    }
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

    let action_ev_raw = game.expected_values_detail(current_player);
    let mut action_ev_matrix = vec![vec![0.0; num_actions]; num_hands];
    for action_idx in 0..num_actions {
        for hand_idx in 0..num_hands {
            let raw_idx = action_idx * num_hands + hand_idx;
            action_ev_matrix[hand_idx][action_idx] = action_ev_raw[raw_idx] as f64;
        }
    }

    let equity: Vec<f64> = game
        .equity(current_player)
        .iter()
        .map(|v| *v as f64)
        .collect();
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
        action_ev_matrix,
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
        memory_uncompressed: 0,
        memory_compressed: 0,
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};

    fn base_request() -> Value {
        json!({
            "board": "QsJh2h",
            "turn": null,
            "river": null,
            "range_oop": "66+,A8s+,AJo+",
            "range_ip": "66+,A8s+,AJo+",
            "starting_pot": 200,
            "effective_stack": 900,
            "flop_bet_sizes_oop": "60%,a",
            "flop_bet_sizes_ip": "60%,a",
            "flop_raise_sizes_oop": "2.5x",
            "flop_raise_sizes_ip": "2.5x",
            "turn_bet_sizes_oop": "60%,a",
            "turn_bet_sizes_ip": "60%,a",
            "turn_raise_sizes_oop": "2.5x",
            "turn_raise_sizes_ip": "2.5x",
            "river_bet_sizes_oop": "60%,a",
            "river_bet_sizes_ip": "60%,a",
            "river_raise_sizes_oop": "2.5x",
            "river_raise_sizes_ip": "2.5x",
            "rake_rate": 0.0,
            "rake_cap": 0.0,
            "add_allin_threshold": 1.5,
            "force_allin_threshold": 0.15,
            "merging_threshold": 0.1,
            "max_iterations": 20,
            "target_exploitability_pct": 99.0,
            "timeout_ms": 3000,
            "bunching": null
        })
    }

    fn solve_value(request: Value) -> Value {
        let response = process_request(&request.to_string());
        serde_json::to_value(response).expect("response serializes")
    }

    #[test]
    fn actions_played_many_returns_nodes_in_input_order() {
        let mut request = base_request();
        request["actions_played_many"] = json!([["Check"], ["Check", "Check", "2c"],]);

        let response = solve_value(request);
        assert_eq!(response["success"], true);
        let nodes = response["queried_nodes"]
            .as_array()
            .expect("queried_nodes array");
        assert_eq!(nodes.len(), 2);
        assert_eq!(nodes[0]["path"], json!(["Check"]));
        assert_eq!(nodes[1]["path"], json!(["Check", "Check", "2c"]));
        assert!(nodes[0].get("strategy").is_some());
        assert!(nodes[1].get("strategy").is_some());
        assert_eq!(
            nodes[0]["available_actions"],
            nodes[0]["strategy"]["actions"]
        );
        assert_eq!(
            nodes[1]["available_actions"],
            nodes[1]["strategy"]["actions"]
        );
        assert_strategy_action_ev_shape_and_weighted_ev(&nodes[0]["strategy"]);
        assert_strategy_action_ev_shape_and_weighted_ev(&nodes[1]["strategy"]);
        assert_eq!(
            nodes[0]["weights"].as_array().expect("weights array").len(),
            nodes[0]["strategy"]["hands"]
                .as_array()
                .expect("hands array")
                .len()
        );
        assert_eq!(
            nodes[1]["weights"].as_array().expect("weights array").len(),
            nodes[1]["strategy"]["hands"]
                .as_array()
                .expect("hands array")
                .len()
        );
        for node in nodes {
            let hands_oop_len = node["hands_oop"].as_array().expect("hands_oop array").len();
            let hands_ip_len = node["hands_ip"].as_array().expect("hands_ip array").len();
            assert_eq!(
                node["raw_weights_oop"]
                    .as_array()
                    .expect("raw_weights_oop array")
                    .len(),
                hands_oop_len
            );
            assert_eq!(
                node["raw_weights_ip"]
                    .as_array()
                    .expect("raw_weights_ip array")
                    .len(),
                hands_ip_len
            );
            assert_eq!(
                node["normalized_weights_oop"]
                    .as_array()
                    .expect("normalized_weights_oop array")
                    .len(),
                hands_oop_len
            );
            assert_eq!(
                node["normalized_weights_ip"]
                    .as_array()
                    .expect("normalized_weights_ip array")
                    .len(),
                hands_ip_len
            );
            let current_player = node["current_player"]
                .as_str()
                .expect("current_player string");
            let current_normalized = if current_player == "OOP" {
                &node["normalized_weights_oop"]
            } else {
                &node["normalized_weights_ip"]
            };
            assert_eq!(node["weights"], *current_normalized);
            assert!(node["pot"].as_i64().expect("pot integer") > 0);
            assert!(
                node["effective_stack_oop"]
                    .as_i64()
                    .expect("effective_stack_oop integer")
                    >= 0
            );
            assert!(
                node["effective_stack_ip"]
                    .as_i64()
                    .expect("effective_stack_ip integer")
                    >= 0
            );
        }
    }

    #[test]
    fn actions_played_many_keeps_success_when_one_path_errors() {
        let mut request = base_request();
        request["actions_played_many"] = json!([["Check"], ["NotARealAction"],]);

        let response = solve_value(request);
        assert_eq!(response["success"], true);
        let nodes = response["queried_nodes"]
            .as_array()
            .expect("queried_nodes array");
        assert_eq!(nodes.len(), 2);
        assert!(nodes[0].get("strategy").is_some());
        assert!(nodes[0].get("error").is_none());
        assert!(nodes[1].get("strategy").is_none());
        assert!(nodes[1]["error"]
            .as_str()
            .expect("error string")
            .contains("action 'NotARealAction' not found"));
    }

    #[test]
    fn request_without_actions_played_many_keeps_legacy_response_shape() {
        let response = solve_value(base_request());

        assert_eq!(response["success"], true);
        assert!(response.get("root_strategy").is_some());
        assert_strategy_action_ev_shape_and_weighted_ev(&response["root_strategy"]);
        assert!(response.get("node_strategy").is_none());
        assert_eq!(
            response["queried_nodes"]
                .as_array()
                .expect("queried_nodes array")
                .len(),
            0
        );
    }

    fn assert_strategy_action_ev_shape_and_weighted_ev(strategy: &Value) {
        let actions_len = strategy["actions"].as_array().expect("actions array").len();
        let hands_len = strategy["hands"].as_array().expect("hands array").len();
        let strategy_matrix = strategy["strategy_matrix"]
            .as_array()
            .expect("strategy_matrix array");
        let action_ev_matrix = strategy["action_ev_matrix"]
            .as_array()
            .expect("action_ev_matrix array");
        let ev = strategy["ev"].as_array().expect("ev array");

        assert_eq!(strategy_matrix.len(), hands_len);
        assert_eq!(action_ev_matrix.len(), hands_len);
        assert_eq!(ev.len(), hands_len);

        for hand_idx in 0..hands_len {
            let strategy_row = strategy_matrix[hand_idx]
                .as_array()
                .expect("strategy row array");
            let action_ev_row = action_ev_matrix[hand_idx]
                .as_array()
                .expect("action ev row array");
            assert_eq!(strategy_row.len(), actions_len);
            assert_eq!(action_ev_row.len(), actions_len);

            let weighted_ev = strategy_row
                .iter()
                .zip(action_ev_row.iter())
                .map(|(probability, action_ev)| {
                    probability.as_f64().expect("probability number")
                        * action_ev.as_f64().expect("action ev number")
                })
                .sum::<f64>();
            let scalar_ev = ev[hand_idx].as_f64().expect("scalar ev number");
            assert!(
                (weighted_ev - scalar_ev).abs() < 1e-3,
                "weighted_ev={} scalar_ev={} hand_idx={}",
                weighted_ev,
                scalar_ev,
                hand_idx
            );
        }
    }
}
