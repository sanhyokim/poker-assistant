"""Audit DB opponent stats against recent hand replay participation.

This script is intended for live-test verification. It prints DB schemas,
recent replay participation, current opponent totals, and optional before/after
total_hands deltas.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path("data/poker_assistant.db")
DEFAULT_REPLAY_DIR = Path("hand_replays")
DEFAULT_SNAPSHOT_DIR = Path("data/audit_snapshots")
OPPONENT_COLUMNS = [
    "player_name",
    "total_hands",
    "vpip",
    "pfr",
    "three_bet_pct",
    "cbet_flop_pct",
    "fold_to_three_bet",
    "went_to_showdown",
    "last_seen",
]
MISSING_NAMES = {"", "-"}


@dataclass(frozen=True)
class ReplayAudit:
    """Parsed replay information used by the audit."""

    path: Path
    hand_id: int | None
    hero_cards: list[Any] | None
    participated_seats: list[str] | None
    seat_to_name: dict[str, str]
    actions: list[dict[str, Any]]
    board: list[Any]
    warnings: list[str]


def emit(text: str = "") -> None:
    """Write one line to stdout."""
    sys.stdout.write(f"{text}\n")


def load_json(path: Path) -> dict[str, Any] | None:
    """Load a JSON object, returning None on parse failure."""
    try:
        with path.open("r", encoding="utf-8") as json_file:
            data = json.load(json_file)
    except (OSError, json.JSONDecodeError) as exc:
        emit(f"WARNING: failed to load {path}: {exc}")
        return None
    if not isinstance(data, dict):
        emit(f"WARNING: {path} is not a JSON object")
        return None
    return data


def connect_db(db_path: Path) -> sqlite3.Connection | None:
    """Open the SQLite DB if it exists."""
    if not db_path.exists():
        emit(f"WARNING: DB not found: {db_path}")
        return None
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as exc:
        emit(f"WARNING: failed to open DB {db_path}: {exc}")
        return None
    conn.row_factory = sqlite3.Row
    return conn


def print_schema(conn: sqlite3.Connection) -> None:
    """Print opponents and hand_history schemas."""
    for table in ["opponents", "hand_history"]:
        emit(f"=== {table} schema ===")
        try:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        except sqlite3.Error as exc:
            emit(f"WARNING: failed to read schema for {table}: {exc}")
            continue
        if not rows:
            emit("(no columns)")
            continue
        for row in rows:
            column_type = row["type"] or "UNKNOWN"
            emit(f"{row['name']} {column_type}")
        emit()


def print_empty_table_status(conn: sqlite3.Connection, table: str) -> None:
    """Print a friendly message when a DB table has no rows."""
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    except sqlite3.Error as exc:
        emit(f"WARNING: failed to count {table}: {exc}")
        return
    count = int(row[0]) if row is not None else 0
    if count != 0:
        return

    emit("=== DB Status ===")
    if table == "opponents":
        emit("opponents table is empty.")
        emit("Run a live session first to accumulate opponent data.")
        emit("This is normal for a fresh or reset database.")
    elif table == "hand_history":
        emit("hand_history table is empty.")
        emit("No hands have been recorded yet.")
    else:
        emit(f"{table} table is empty.")
    emit()


def recent_replay_paths(replay_dir: Path, limit: int) -> list[Path]:
    """Return most recently modified replay JSON paths."""
    if not replay_dir.exists():
        emit(f"WARNING: replay directory not found: {replay_dir}")
        return []
    return sorted(
        replay_dir.rglob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]


def extract_actions(replay: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract all street actions from a replay."""
    actions: list[dict[str, Any]] = []
    streets = replay.get("streets", {})
    if not isinstance(streets, dict):
        return actions
    for street_name in ["preflop", "flop", "turn", "river"]:
        street = streets.get(street_name)
        if not isinstance(street, dict):
            continue
        observed = street.get("actions_observed", [])
        if not isinstance(observed, list):
            continue
        for action in observed:
            if not isinstance(action, dict):
                continue
            action_copy = dict(action)
            action_copy.setdefault("street", street_name)
            actions.append(action_copy)
    return actions


def extract_board(replay: dict[str, Any]) -> list[Any]:
    """Extract the latest board cards from replay streets."""
    board: list[Any] = []
    streets = replay.get("streets", {})
    if not isinstance(streets, dict):
        return board
    for street_name in ["flop", "turn", "river"]:
        street = streets.get(street_name)
        if not isinstance(street, dict):
            continue
        cards = street.get("board")
        if isinstance(cards, list) and len(cards) >= len(board):
            board = cards
    return board


def extract_hero_cards(replay: dict[str, Any]) -> list[Any] | None:
    """Extract hero hole cards from a replay."""
    streets = replay.get("streets", {})
    if not isinstance(streets, dict):
        return None
    preflop = streets.get("preflop")
    if not isinstance(preflop, dict):
        return None
    cards = preflop.get("hole_cards")
    return cards if isinstance(cards, list) else None


def normalize_name(value: Any) -> str | None:
    """Return a usable player name or None."""
    if value is None:
        return None
    name = str(value).strip()
    if name in MISSING_NAMES:
        return None
    return name


def extract_seat_to_name(replay: dict[str, Any]) -> dict[str, str]:
    """Extract seat-to-name mapping from known replay shapes when available."""
    seat_to_name: dict[str, str] = {}
    for key in ["seat_to_name", "player_names", "players", "current_players"]:
        raw = replay.get(key)
        if not isinstance(raw, dict):
            continue
        for seat_key, value in raw.items():
            if isinstance(value, dict):
                name = normalize_name(value.get("name"))
            else:
                name = normalize_name(value)
            if name is not None:
                seat_to_name[str(seat_key)] = name
    return seat_to_name


def parse_replay(path: Path) -> ReplayAudit | None:
    """Parse a replay JSON file into an audit record."""
    replay = load_json(path)
    if replay is None:
        return None

    warnings: list[str] = []
    meta = replay.get("meta", {})
    hand_id = meta.get("hand_id") if isinstance(meta, dict) else None
    hand_id_int = int(hand_id) if isinstance(hand_id, int) else None
    participated_raw = replay.get("participated_seats")
    participated_seats: list[str] | None
    if isinstance(participated_raw, list):
        participated_seats = [str(seat) for seat in participated_raw]
    else:
        participated_seats = None
        warnings.append(f"WARNING: {path.name} has no participated_seats")

    if not isinstance(replay.get("seat_to_name"), dict):
        warnings.append(f"WARNING: {path.name} has no seat_to_name")

    return ReplayAudit(
        path=path,
        hand_id=hand_id_int,
        hero_cards=extract_hero_cards(replay),
        participated_seats=participated_seats,
        seat_to_name=extract_seat_to_name(replay),
        actions=extract_actions(replay),
        board=extract_board(replay),
        warnings=warnings,
    )


def print_replay_audit(audit: ReplayAudit) -> Counter[str]:
    """Print one replay audit and return expected named participation counts."""
    expected: Counter[str] = Counter()
    emit(f"=== replay {audit.path} ===")
    emit(f"hand_id: {audit.hand_id}")
    emit(f"hero_cards: {audit.hero_cards}")
    emit(f"participated_seats: {audit.participated_seats}")
    emit(f"board: {audit.board}")
    emit("seat_to_name:")
    if audit.seat_to_name:
        for seat, name in sorted(audit.seat_to_name.items()):
            emit(f"  {seat} -> {name}")
    else:
        emit("  (not present in replay)")

    for warning in audit.warnings:
        emit(warning)

    emit("actions:")
    for action in audit.actions:
        emit(
            "  "
            f"{action.get('street', '?')} seat={action.get('seat')} "
            f"action={action.get('action')} amount={action.get('amount')}"
        )

    if audit.participated_seats is not None:
        for seat in audit.participated_seats:
            if seat == "1":
                continue
            name = normalize_name(audit.seat_to_name.get(seat))
            if name is None:
                emit(f"seat={seat} skipped: name missing or '-'")
                continue
            expected[name] += 1
    emit()
    return expected


def load_opponents(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Load opponent rows keyed by player_name."""
    columns = ", ".join(OPPONENT_COLUMNS)
    try:
        rows = conn.execute(f"SELECT {columns} FROM opponents").fetchall()
    except sqlite3.Error as exc:
        emit(f"WARNING: failed to read opponents: {exc}")
        return {}
    return {str(row["player_name"]): dict(row) for row in rows}


def print_opponent_rows(
    conn: sqlite3.Connection,
    player_names: list[str],
) -> None:
    """Print current DB rows for player names."""
    emit("=== current opponent rows ===")
    if not player_names:
        emit("(no named participants from replay)")
        emit()
        return

    query = (
        "SELECT player_name, total_hands, vpip, pfr, three_bet_pct, "
        "cbet_flop_pct, fold_to_three_bet, went_to_showdown, last_seen "
        "FROM opponents WHERE player_name = ?"
    )
    for player_name in sorted(set(player_names)):
        try:
            row = conn.execute(query, (player_name,)).fetchone()
        except sqlite3.Error as exc:
            emit(f"{player_name}: WARNING failed to query: {exc}")
            continue
        if row is None:
            emit(f"{player_name}: not found")
            continue
        values = dict(row)
        emit(
            f"{values['player_name']}: total_hands={values['total_hands']} "
            f"vpip={values['vpip']} pfr={values['pfr']} "
            f"three_bet={values['three_bet_pct']} cbet={values['cbet_flop_pct']} "
            f"fold_to_3bet={values['fold_to_three_bet']} "
            f"wtsd={values['went_to_showdown']} last_seen={values['last_seen']}"
        )
    emit()


def validate_stats(opponents: dict[str, dict[str, Any]]) -> list[str]:
    """Return warnings for missing or unnatural opponent stat values."""
    warnings: list[str] = []
    percentage_columns = [
        "vpip",
        "pfr",
        "three_bet_pct",
        "cbet_flop_pct",
        "fold_to_three_bet",
        "went_to_showdown",
    ]
    for player_name, row in opponents.items():
        total_hands = row.get("total_hands")
        if not isinstance(total_hands, int) or total_hands < 0:
            warnings.append(f"WARNING: {player_name} total_hands invalid: {total_hands}")
        for column in percentage_columns:
            value = row.get(column)
            if value is None:
                warnings.append(f"WARNING: {player_name} {column} is NULL")
                continue
            numeric = float(value)
            if numeric < 0.0 or numeric > 100.0:
                warnings.append(f"WARNING: {player_name} {column} out of range: {value}")
    return warnings


def snapshot_opponents(conn: sqlite3.Connection, snapshot_name: str) -> Path:
    """Save current opponent totals to a snapshot JSON file."""
    opponents = load_opponents(conn)
    DEFAULT_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = DEFAULT_SNAPSHOT_DIR / f"{snapshot_name}_opponents.json"
    snapshot = {
        name: {
            "total_hands": row.get("total_hands", 0),
            "last_seen": row.get("last_seen"),
        }
        for name, row in opponents.items()
    }
    path.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_snapshot(snapshot_name: str) -> dict[str, dict[str, Any]]:
    """Load a saved opponent snapshot."""
    path = DEFAULT_SNAPSHOT_DIR / f"{snapshot_name}_opponents.json"
    data = load_json(path)
    if data is None:
        return {}
    return {
        str(name): row
        for name, row in data.items()
        if isinstance(row, dict)
    }


def print_expected_counts(expected_counts: Counter[str]) -> None:
    """Print expected participation counts from recent named replays."""
    emit("=== expected_participation_count from recent replays ===")
    if not expected_counts:
        emit("(none; replay has no usable seat-to-name mapping)")
    for player_name, count in sorted(expected_counts.items()):
        emit(f"{player_name}: {count}")
    emit()


def print_compare(
    conn: sqlite3.Connection,
    snapshot_name: str,
    expected_counts: Counter[str],
) -> list[str]:
    """Print total_hands delta from a named snapshot."""
    before = load_snapshot(snapshot_name)
    after = load_opponents(conn)
    emit("=== total_hands delta ===")
    warnings: list[str] = []
    names = sorted(set(before) | set(after) | set(expected_counts))
    if not names:
        emit("(no opponent rows)")
        emit()
        return warnings

    for player_name in names:
        before_total = int(before.get(player_name, {}).get("total_hands", 0) or 0)
        after_total = int(after.get(player_name, {}).get("total_hands", 0) or 0)
        delta = after_total - before_total
        expected = expected_counts.get(player_name)
        suffix = ""
        if expected is not None and delta < expected:
            suffix = f" WARNING expected +{expected}"
            warnings.append(
                f"NG: {player_name} expected +{expected} but actual +{delta}"
            )
        emit(f"{player_name}: {before_total} -> {after_total} (+{delta}){suffix}")
    emit()
    return warnings


def run_recent_audit(
    conn: sqlite3.Connection | None,
    replay_dir: Path,
    limit: int,
) -> tuple[Counter[str], list[str]]:
    """Print recent replay audits and return expected counts plus warnings."""
    expected_counts: Counter[str] = Counter()
    warnings: list[str] = []
    paths = recent_replay_paths(replay_dir, limit)
    if not paths:
        emit("WARNING: no replay JSON files found")
        return expected_counts, ["WARNING: no replay JSON files found"]

    for path in paths:
        audit = parse_replay(path)
        if audit is None:
            continue
        warnings.extend(audit.warnings)
        expected_counts.update(print_replay_audit(audit))

    print_expected_counts(expected_counts)
    if conn is not None:
        print_opponent_rows(conn, list(expected_counts.keys()))
    return expected_counts, warnings


def print_summary(warnings: list[str], compare_warnings: list[str]) -> None:
    """Print final OK/NG audit summary."""
    emit("=== Audit Summary ===")
    if warnings:
        missing_count = sum("has no participated_seats" in item for item in warnings)
        if missing_count:
            emit(f"WARNING: {missing_count} replay files missing participated_seats")
        missing_names = sum("has no seat_to_name" in item for item in warnings)
        if missing_names:
            emit(f"WARNING: {missing_names} replay files missing seat_to_name")
        for warning in warnings:
            if (
                "has no participated_seats" in warning
                or "has no seat_to_name" in warning
            ):
                continue
            emit(warning)
    else:
        emit("OK: recent replay participated_seats fields are present")

    if compare_warnings:
        for warning in compare_warnings:
            emit(warning)
    else:
        emit("OK: no total_hands delta mismatches detected")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Audit poker assistant DB integrity against replay participation.",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--replay-dir", type=Path, default=DEFAULT_REPLAY_DIR)
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--recent", type=int, default=None)
    parser.add_argument("--snapshot", type=str, default=None)
    parser.add_argument("--compare", type=str, default=None)
    return parser


def main() -> int:
    """Run the audit CLI."""
    args = build_parser().parse_args()
    conn = connect_db(args.db)
    try:
        should_print_schema = args.schema or not any(
            [args.recent, args.snapshot, args.compare]
        )
        if conn is not None and should_print_schema:
            print_schema(conn)
            print_empty_table_status(conn, "opponents")

        expected_counts: Counter[str] = Counter()
        replay_warnings: list[str] = []
        recent_limit = args.recent
        if recent_limit is None and args.compare:
            recent_limit = 10
        if recent_limit is not None:
            if conn is not None:
                print_empty_table_status(conn, "hand_history")
            expected_counts, replay_warnings = run_recent_audit(
                conn,
                args.replay_dir,
                recent_limit,
            )

        if conn is not None and args.snapshot:
            snapshot_path = snapshot_opponents(conn, args.snapshot)
            emit(f"Snapshot saved: {snapshot_path}")

        compare_warnings: list[str] = []
        if conn is not None and args.compare:
            compare_warnings = print_compare(conn, args.compare, expected_counts)

        if conn is not None:
            stat_warnings = validate_stats(load_opponents(conn))
            replay_warnings.extend(stat_warnings)
        if args.recent is not None or args.compare:
            print_summary(replay_warnings, compare_warnings)
    finally:
        if conn is not None:
            conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
