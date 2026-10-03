#!/usr/bin/env python3
"""
Build or refresh a server-scoped highs/lows summary for all item JSON files.

Behavior:
- Accepts one server argument: eu, asia, us, or their full names.
- Reads every item JSON in the target server's formatted directory.
- Creates a summary file named highs_and_lows.json.
- Each item includes its highest and lowest recorded values.
- Only one server is processed per run.
- If a summary already exists for the selected server and was created within the
  last 30 days, it skips that server and moves to the next one in priority order.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

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
    if requested_server:
        first_server = requested_server
        remaining = [server for server in SERVER_PRIORITY if server != first_server]
        candidates = [first_server] + remaining
    else:
        candidates = SERVER_PRIORITY

    for server in candidates:
        server_dir = get_server_dir(server)
        if not server_dir.exists():
            print(f"[SKIP] {server}: data directory not found")
            continue

        summary_file = get_summary_path(server)
        if not is_fresh(summary_file):
            return server
        print(f"[SKIP] {server}: summary was updated within the last {MAX_AGE_DAYS} days")

    return None


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def clean_price(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_item_extremes(records: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    highest_value: Optional[float] = None
    lowest_value: Optional[float] = None
    highest_record: Optional[Dict[str, Any]] = None
    lowest_record: Optional[Dict[str, Any]] = None

    for record in records:
        if not isinstance(record, dict):
            continue

        sell_price = clean_price(record.get("sellPrice"))
        buy_price = clean_price(record.get("buyPrice"))
        for price in (sell_price, buy_price):
            if price is None:
                continue

            if highest_value is None or price > highest_value:
                highest_value = price
                highest_record = {
                    "value": round(price, 2),
                    "timestamp": record.get("timestamp"),
                    "city": record.get("city"),
                    "quality": record.get("quality"),
                    "server": record.get("server"),
                }

            if lowest_value is None or price < lowest_value:
                lowest_value = price
                lowest_record = {
                    "value": round(price, 2),
                    "timestamp": record.get("timestamp"),
                    "city": record.get("city"),
                    "quality": record.get("quality"),
                    "server": record.get("server"),
                }

    if highest_value is None or lowest_value is None:
        return None

    return {
        "highest": highest_record,
        "lowest": lowest_record,
    }


def merge_with_previous(current_item: Dict[str, Any], previous_item: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if previous_item is None:
        return current_item

    previous_high = previous_item.get("highest")
    previous_low = previous_item.get("lowest")

    if previous_high:
        previous_high_value = previous_high.get("value")
        current_high_value = current_item.get("highest", {}).get("value")
        if previous_high_value is not None and current_high_value is not None:
            if previous_high_value > current_high_value:
                current_item["highest"] = previous_high

    if previous_low:
        previous_low_value = previous_low.get("value")
        current_low_value = current_item.get("lowest", {}).get("value")
        if previous_low_value is not None and current_low_value is not None:
            if previous_low_value < current_low_value:
                current_item["lowest"] = previous_low

    return current_item


def build_server_summary(server_name: str) -> Dict[str, Any]:
    server_dir = get_server_dir(server_name)
    if not server_dir.exists():
        return {
            "server": server_name,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "items": {},
        }

    summary_path = get_summary_path(server_name)
    previous_summary: Dict[str, Any] = {}

    if summary_path.exists():
        try:
            previous_summary = load_json(summary_path)
        except (json.JSONDecodeError, OSError):
            previous_summary = {}

    previous_items = previous_summary.get("items", {}) if isinstance(previous_summary, dict) else {}
    item_map: Dict[str, Dict[str, Any]] = {}

    files = sorted(server_dir.glob("*.json"))
    for item_file in files:
        if item_file.name == SUMMARY_FILENAME:
            continue

        try:
            data = load_json(item_file)
        except (json.JSONDecodeError, OSError):
            print(f"[WARN] Skipping unreadable file: {item_file.name}")
            continue

        if not isinstance(data, list):
            continue

        item_extremes = read_item_extremes(data)
        if item_extremes is None:
            continue

        item_id = item_file.stem
        previous_item = previous_items.get(item_id)
        item_map[item_id] = merge_with_previous(item_extremes, previous_item)

    return {
        "server": server_name,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "items": item_map,
    }


def write_server_summary(server_name: str, summary: Dict[str, Any]) -> Path:
    summary_path = get_summary_path(server_name)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return summary_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build per-server highs and lows summaries from JSON market history files.")
    parser.add_argument(
        "server",
        nargs="?",
        help="One server: eu, asia, us, europe, east, or west. If omitted, the first stale server in priority order is used.",
    )
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
