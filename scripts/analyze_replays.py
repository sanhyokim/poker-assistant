"""Replay JSON aggregation script for Phase 22-2 baseline measurement.

Loads replay JSON files under hand_replays/ and prints recommendation save rate,
latency statistics, followed-action distribution, and inferred source counts.

Usage:
    python scripts/analyze_replays.py [directory_path]

If directory_path is omitted, the latest dated directory under hand_replays/ is used.
"""

import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


STREET_NAMES = ["preflop", "flop", "turn", "river"]
LATENCY_KEYS = [
    "capture_ms",
    "ocr_ms",
    "preflop_chart_ms",
    "multiway_ms",
    "llm_ms",
    "solver_ms",
    "hud_ms",
    "total_ms",
]


def find_latest_replay_dir(base_dir: str = "hand_replays") -> Path | None:
    """Return the latest dated replay directory under hand_replays/.

    Args:
        base_dir: Base replay directory path.

    Returns:
        Latest child directory, or None if no directory exists.
    """
    base_path = Path(base_dir)
    if not base_path.exists():
        return None

    subdirs = [path for path in base_path.iterdir() if path.is_dir()]
    if not subdirs:
        return None

    subdirs.sort(key=lambda path: path.name, reverse=True)
    return subdirs[0]


def load_replays(replay_dir: Path) -> list[dict[str, Any]]:
    """Load all replay JSON files in a directory.

    Args:
        replay_dir: Directory containing replay JSON files.

    Returns:
        Loaded replay dictionaries. Invalid files are skipped with a warning.
    """
    replays: list[dict[str, Any]] = []
    for json_path in sorted(replay_dir.glob("*.json")):
        try:
            with open(json_path, "r", encoding="utf-8") as replay_file:
                data = json.load(replay_file)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: Failed to load {json_path.name}: {exc}")
            continue

        if isinstance(data, dict):
            data["_filename"] = json_path.name
            replays.append(data)
        else:
            print(f"WARNING: Skipping non-object JSON: {json_path.name}")

    return replays


def infer_source(
    recommendation: str | None,
    breakdown: dict[str, Any] | None,
) -> str | None:
    """Infer strategy source from replay fields.

    Args:
        recommendation: Saved recommendation text.
        breakdown: Saved latency breakdown.

    Returns:
        Inferred source name, or None when the source cannot be inferred.
    """
    if recommendation is None:
        return None
    if not isinstance(breakdown, dict):
        return None

    solver_ms = numeric_value(breakdown.get("solver_ms"))
    llm_ms = numeric_value(breakdown.get("llm_ms"))
    preflop_chart_ms = numeric_value(breakdown.get("preflop_chart_ms"))
    multiway_ms = numeric_value(breakdown.get("multiway_ms"))

    if solver_ms is not None and solver_ms > 0:
        return "solver"
    if llm_ms is not None and llm_ms > 0:
        return "llm"
    if preflop_chart_ms is not None and preflop_chart_ms > 0:
        return "chart"
    if multiway_ms is not None and multiway_ms > 0:
        return "multiway"
    if breakdown:
        return "fallback_or_cached"
    return None


def numeric_value(value: Any) -> float | None:
    """Return a float when value is numeric, otherwise None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def percentile(values: list[float], pct: float) -> float:
    """Return the nearest-rank percentile value for a non-empty list."""
    sorted_values = sorted(values)
    index = min(int(len(sorted_values) * pct), len(sorted_values) - 1)
    return sorted_values[index]


def analyze(replays: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate replay data.

    Args:
        replays: Replay dictionaries.

    Returns:
        Report statistics dictionary.
    """
    streets_with_hero_turn = 0
    streets_with_recommendation = 0
    followed_true = 0
    followed_false = 0
    followed_null = 0
    source_counts: dict[str, int] = {}
    recommend_times: list[float] = []
    latency_components: dict[str, list[float]] = {
        key: [] for key in LATENCY_KEYS
    }
    fallback_count = 0
    hand_details: list[dict[str, Any]] = []

    for replay in replays:
        hand_id = replay.get("meta", {}).get("hand_id", "?")
        streets = replay.get("streets", {})
        hand_info: dict[str, Any] = {
            "hand_id": hand_id,
            "filename": replay.get("_filename", ""),
            "streets_analyzed": [],
        }

        if not isinstance(streets, dict):
            hand_details.append(hand_info)
            continue

        for street_name in STREET_NAMES:
            street = streets.get(street_name)
            if not isinstance(street, dict):
                continue
            if street.get("spectate_only", False):
                continue

            recommendation = street.get("recommendation")
            human_action = street.get("human_action")
            followed = street.get("followed_recommendation")
            time_ms = numeric_value(street.get("time_to_recommend_ms"))
            breakdown = street.get("latency_breakdown")

            has_hero_turn = recommendation is not None or human_action is not None
            if has_hero_turn:
                streets_with_hero_turn += 1

            if recommendation is not None:
                streets_with_recommendation += 1
                source = infer_source(str(recommendation), breakdown)
                if source is not None:
                    source_counts[source] = source_counts.get(source, 0) + 1
                    if source == "fallback_or_cached":
                        fallback_count += 1

            if followed is True:
                followed_true += 1
            elif followed is False:
                followed_false += 1
            elif has_hero_turn:
                followed_null += 1

            if time_ms is not None:
                recommend_times.append(time_ms)

            if isinstance(breakdown, dict):
                for key in LATENCY_KEYS:
                    value = numeric_value(breakdown.get(key))
                    if value is not None:
                        latency_components[key].append(value)

            hand_info["streets_analyzed"].append(
                {
                    "street": street_name,
                    "recommendation": recommendation,
                    "human_action": human_action,
                    "followed": followed,
                    "time_ms": time_ms,
                }
            )

        hand_details.append(hand_info)

    recommendation_rate = 0.0
    if streets_with_hero_turn > 0:
        recommendation_rate = (
            streets_with_recommendation / streets_with_hero_turn * 100.0
        )

    stats: dict[str, Any] = {
        "total_hands": len(replays),
        "streets_with_hero_turn": streets_with_hero_turn,
        "streets_with_recommendation": streets_with_recommendation,
        "recommendation_rate": recommendation_rate,
        "followed": {
            "true": followed_true,
            "false": followed_false,
            "null": followed_null,
        },
        "source_counts": source_counts,
        "latency": {},
        "latency_components": {},
        "fallback_count": fallback_count,
        "hand_details": hand_details,
    }

    if recommend_times:
        stats["latency"] = {
            "count": len(recommend_times),
            "min_ms": round(min(recommend_times), 1),
            "max_ms": round(max(recommend_times), 1),
            "median_ms": round(statistics.median(recommend_times), 1),
            "mean_ms": round(statistics.mean(recommend_times), 1),
            "p95_ms": round(percentile(recommend_times, 0.95), 1),
        }

    for key, values in latency_components.items():
        if values:
            stats["latency_components"][key] = {
                "mean_ms": round(statistics.mean(values), 1),
                "max_ms": round(max(values), 1),
            }

    return stats


def print_report(stats: dict[str, Any], replay_dir: Path) -> None:
    """Print an aggregate report.

    Args:
        stats: Statistics returned by analyze().
        replay_dir: Directory that was analyzed.
    """
    print("=" * 60)
    print("  Phase 22-2 Baseline Measurement Report")
    print("=" * 60)
    print(f"  Replay directory: {replay_dir}")
    print(f"  Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    print(f"1. Total hands: {stats['total_hands']}")
    print()

    hero_turns = stats["streets_with_hero_turn"]
    recommendation_count = stats["streets_with_recommendation"]
    recommendation_rate = stats["recommendation_rate"]
    print("2. Recommendation save rate:")
    print(f"   Hero-turn streets: {hero_turns}")
    print(f"   Streets with recommendation: {recommendation_count}")
    print(f"   Save rate: {recommendation_rate:.1f}%", end="")
    print("  PASS (target: >80%)" if recommendation_rate >= 80 else "  FAIL (target: >80%)")
    print()

    followed = stats["followed"]
    print("3. followed_recommendation:")
    print(f"   true:  {followed['true']}")
    print(f"   false: {followed['false']}")
    print(f"   null:  {followed['null']}")
    print()

    print("4. strategy_source distribution (inferred from latency_breakdown):")
    if stats["source_counts"]:
        for source, count in sorted(stats["source_counts"].items()):
            print(f"   {source}: {count}")
    else:
        print("   (no data - latency_breakdown may be missing)")
    print()

    latency = stats.get("latency", {})
    if latency:
        print("5. time_to_recommend_ms:")
        print(f"   Samples: {latency['count']}")
        print(f"   Min:    {latency['min_ms']} ms")
        print(f"   Max:    {latency['max_ms']} ms")
        print(f"   Median: {latency['median_ms']} ms")
        print(f"   Mean:   {latency['mean_ms']} ms")
        p95 = latency["p95_ms"]
        print(f"   P95:    {p95} ms", end="")
        print("  PASS (target: <=7000ms)" if p95 <= 7000 else "  FAIL (target: <=7000ms)")
    else:
        print("5. time_to_recommend_ms: no data")
    print()

    components = stats.get("latency_components", {})
    if components:
        print("6. Latency breakdown (mean / max):")
        for key in LATENCY_KEYS:
            component = components.get(key)
            if component:
                print(f"   {key}: {component['mean_ms']} / {component['max_ms']} ms")
    else:
        print("6. Latency breakdown: no data")
    print()

    print(f"7. Fallback/cached source count: {stats['fallback_count']}")
    print()

    print("=" * 60)
    print("  Acceptance Criteria Check")
    print("=" * 60)
    rate_pass = recommendation_rate >= 80
    latency_pass = bool(latency) and latency.get("p95_ms", 99999) <= 7000
    rate_mark = "PASS" if rate_pass else "FAIL"
    latency_mark = "PASS" if latency_pass else "FAIL"
    print(f"  [{rate_mark}] Recommendation save rate > 80%: {recommendation_rate:.1f}%")
    print(f"  [{latency_mark}] E2E P95 <= 7000ms: {latency.get('p95_ms', 'N/A')} ms")
    print("  [MANUAL] Recognition errors: check logs/poker_assistant.log")
    print()

    print("=" * 60)
    print("  First 5 Hands")
    print("=" * 60)
    for hand in stats["hand_details"][:5]:
        print(f"  Hand #{hand['hand_id']} ({hand['filename']}):")
        for street in hand["streets_analyzed"]:
            recommendation = str(street["recommendation"] or "-")[:30]
            human_action = str(street["human_action"] or "-")[:20]
            time_ms = street["time_ms"]
            time_text = f"{time_ms:.0f}ms" if time_ms is not None else "-"
            print(
                f"    {street['street']:8s} | rec: {recommendation:30s} | "
                f"human: {human_action:20s} | {time_text}"
            )
        print()


def main() -> None:
    """Run replay analysis from the command line."""
    if len(sys.argv) > 1:
        replay_dir = Path(sys.argv[1])
    else:
        replay_dir = find_latest_replay_dir()

    if replay_dir is None or not replay_dir.exists():
        print(f"ERROR: Replay directory not found: {replay_dir}")
        print("Usage: python scripts/analyze_replays.py [directory_path]")
        sys.exit(1)

    print(f"Loading replays from: {replay_dir}")
    replays = load_replays(replay_dir)
    if not replays:
        print(f"ERROR: No replay JSON files found in {replay_dir}")
        sys.exit(1)

    print(f"Loaded {len(replays)} replay files.")
    print()

    stats = analyze(replays)
    print_report(stats, replay_dir)


if __name__ == "__main__":
    main()
