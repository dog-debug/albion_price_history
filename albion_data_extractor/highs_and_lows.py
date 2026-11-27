#!/usr/bin/env python3
"""Build per-server highs/lows and distribution statistics for Albion market history."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = PROJECT_ROOT / "albion_data_dumps"
SUMMARY_FILENAME = "highs_and_lows.json"
MAX_AGE_DAYS = 30
SERVER_PRIORITY = ["europe", "east", "west"]
SERVER_ALIASES = {
    "eu": "europe",
    "europe": "europe",
    "european": "europe",
    "asia": "east",
    "east": "east",
    "us": "west",
    "west": "west",
}


def normalize_server(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = value.strip().lower().replace(" ", "").replace("_", "")
    return SERVER_ALIASES.get(raw)


def get_server_dir(server_name: str) -> Path:
    return DATA_ROOT / server_name / "formatted"


def get_summary_path(server_name: str) -> Path:
    return get_server_dir(server_name) / SUMMARY_FILENAME


def is_fresh(path: Path, max_age_days: int = MAX_AGE_DAYS) -> bool:
    if not path.exists():
        return False
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return age < timedelta(days=max_age_days)


def choose_server(requested_server: Optional[str]) -> Optional[str]:
    candidates = [requested_server] + [server for server in SERVER_PRIORITY if server != requested_server] if requested_server else SERVER_PRIORITY
    for server in candidates:
        server_dir = get_server_dir(server)
        if not server_dir.exists():
            print(f"[SKIP] {server}: data directory not found")
            continue
        if not is_fresh(get_summary_path(server)):
            return server
        print(f"[SKIP] {server}: summary was updated within the last {MAX_AGE_DAYS} days")
    return None


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def record_snapshot(record: dict[str, Any], value: float, price_key: str) -> dict[str, Any]:
    return {
        "value": round(value, 2),
        "timestamp": record.get("timestamp"),
        "city": record.get("city"),
        "quality": record.get("quality"),
        "server": record.get("server"),
        "price_field": price_key,
    }


def read_item_statistics(records: Iterable[dict[str, Any]]) -> Optional[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        for price_key in ("sellPrice", "buyPrice"):
            value = normalize_number(record.get(price_key))
            if value is None:
                continue
            samples.append({"value": value, "record": record_snapshot(record, value, price_key)})

    if not samples:
        return None

    values = [sample["value"] for sample in samples]
    highest = max(samples, key=lambda sample: sample["value"])
    lowest = min(samples, key=lambda sample: sample["value"])

    return {
        "count": len(values),
        "average": round(sum(values) / len(values), 2),
        "median": round(median(values), 2),
        "stddev": round(stddev(values), 2),
        "highest": highest["record"],
        "lowest": lowest["record"],
        "max": highest["record"],
        "min": lowest["record"],
    }


def merge_with_previous(current_item: dict[str, Any], previous_item: Optional[dict[str, Any]]) -> dict[str, Any]:
    if previous_item is None:
        return current_item

    previous_high = previous_item.get("highest")
    previous_low = previous_item.get("lowest")

    if previous_high:
        previous_high_value = previous_high.get("value")
        current_high_value = current_item.get("highest", {}).get("value")
        if previous_high_value is not None and current_high_value is not None and previous_high_value > current_high_value:
            current_item["highest"] = previous_high
            current_item["max"] = previous_high

    if previous_low:
        previous_low_value = previous_low.get("value")
        current_low_value = current_item.get("lowest", {}).get("value")
        if previous_low_value is not None and current_low_value is not None and previous_low_value < current_low_value:
            current_item["lowest"] = previous_low
            current_item["min"] = previous_low

    current_item.setdefault("count", 0)
    current_item.setdefault("average", 0)
    current_item.setdefault("median", 0)
    current_item.setdefault("stddev", 0)
    return current_item


def build_server_summary(server_name: str) -> dict[str, Any]:
    server_dir = get_server_dir(server_name)
    if not server_dir.exists():
        return {
            "server": server_name,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "items": {},
        }

    summary_path = get_summary_path(server_name)
    previous_summary: dict[str, Any] = {}
    if summary_path.exists():
        try:
            previous_summary = load_json(summary_path)
        except (json.JSONDecodeError, OSError):
            previous_summary = {}

    previous_items = previous_summary.get("items", {}) if isinstance(previous_summary, dict) else {}
    item_map: dict[str, dict[str, Any]] = {}

    for item_file in sorted(server_dir.glob("*.json")):
        if item_file.name == SUMMARY_FILENAME:
            continue
        try:
            payload = load_json(item_file)
        except (json.JSONDecodeError, OSError):
            print(f"[WARN] Skipping unreadable file: {item_file.name}")
            continue
        if not isinstance(payload, list):
            continue

        stats = read_item_statistics(payload)
        if stats is None:
            continue

        item_id = item_file.stem
        item_map[item_id] = merge_with_previous(stats, previous_items.get(item_id))

    return {
        "server": server_name,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "items": item_map,
    }


def write_server_summary(server_name: str, summary: dict[str, Any]) -> Path:
    summary_path = get_summary_path(server_name)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return summary_path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build per-server highs and lows summaries from JSON market history files.")
    parser.add_argument("server", nargs="?", help="One server: eu, asia, us, europe, east, or west. If omitted, first stale server is used.")
    args = parser.parse_args(argv)

    requested_server = normalize_server(args.server)
    if args.server and requested_server is None:
        print(f"[ERROR] Unknown server: {args.server}. Use one of: eu, asia, us, europe, east, west")
        return 2

    chosen = choose_server(requested_server)
    if chosen is None:
        print("[INFO] No stale server needs a highs/lows refresh in this run.")
        return 0

    summary_path = write_server_summary(chosen, build_server_summary(chosen))
    print(f"[SUCCESS] Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
