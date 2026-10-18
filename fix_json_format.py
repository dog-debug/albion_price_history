#!/usr/bin/env python3
"""Repair common JSON formatting issues for Albion market-history exports."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def parse_repo_input(repo_input: str) -> tuple[str | None, str | None]:
    """Parse owner/repo values and GitHub URLs."""
    repo_input = repo_input.strip()

    if "raw.githubusercontent.com" in repo_input:
        parts = repo_input.split("/")
        if len(parts) >= 5:
            return parts[3], parts[4]

    if "github.com" in repo_input:
        parts = repo_input.split("/")
        if len(parts) >= 5:
            return parts[3], parts[4].rstrip("/")

    if "/" in repo_input:
        parts = repo_input.split("/")
        if len(parts) == 2:
            return parts[0], parts[1]

    return None, None


def strip_comments(content: str) -> str:
    content = re.sub(r"//.*?$", "", content, flags=re.MULTILINE)
    return re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)


def normalize_trailing_commas(content: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", content)


def normalize_unquoted_keys(content: str) -> str:
    return re.sub(r"([{,\[]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)", r'\1"\2"\3', content)


def normalize_single_quotes(content: str) -> str:
    pattern = r"'([^'\\]*(?:\\.[^'\\]*)*)'"
    return re.sub(pattern, lambda match: '"' + match.group(1).replace('"', '\\"') + '"', content)


def normalize_special_literals(content: str) -> str:
    for token, replacement in {"NaN": "null", "Infinity": "null", "undefined": "null"}.items():
        content = re.sub(rf"\b{token}\b", replacement, content)
    return content


def ensure_array_root(content: str) -> str:
    stripped = content.strip()
    if not stripped:
        return "[]"
    if stripped.startswith("["):
        return stripped
    if stripped.startswith("{"):
        return f"[{stripped}]"
    return stripped


def fix_json_string(content: str) -> tuple[str, bool, list[str]]:
    """Attempt to repair common JSON issues and return validated, normalized JSON."""
    original = content.lstrip("\ufeff").strip()
    if not original:
        return "", False, ["File is empty"]

    candidate = original
    fixes_applied: list[str] = []

    if "//" in candidate or "/*" in candidate:
        candidate = strip_comments(candidate)
        fixes_applied.append("Removed inline and block comments")

    previous = candidate
    candidate = normalize_trailing_commas(candidate)
    if candidate != previous:
        fixes_applied.append("Removed trailing commas")

    previous = candidate
    candidate = normalize_unquoted_keys(candidate)
    if candidate != previous:
        fixes_applied.append("Quoted object keys")

    previous = candidate
    candidate = normalize_single_quotes(candidate)
    if candidate != previous:
        fixes_applied.append("Normalized string quoting")

    previous = candidate
    candidate = normalize_special_literals(candidate)
    if candidate != previous:
        fixes_applied.append("Converted NaN/Infinity/undefined to null")

    previous = candidate
    candidate = ensure_array_root(candidate)
    if candidate != previous:
        fixes_applied.append("Normalized the JSON root to an array")

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        # Try a common recovery for adjacent objects: }{ -> },{
        repaired = re.sub(r"}\s*{", "},{", candidate)
        if repaired != candidate:
            candidate = repaired
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                return original, False, fixes_applied + [f"Could not parse JSON: {exc.msg} at line {exc.lineno}"]
        else:
            return original, False, fixes_applied + [f"Could not parse JSON: {exc.msg} at line {exc.lineno}"]

    if isinstance(data, dict):
        data = [data]

    pretty = json.dumps(data, ensure_ascii=False, indent=2)
    return pretty, True, fixes_applied


def fetch_json_from_github(filename: str, owner: str, repo: str, branch: str = "main", server: str = "europe") -> tuple[str, bool]:
    """Fetch a specific JSON file from GitHub raw content."""
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/albion_data_dumps/{server}/formatted/{filename}"

    try:
        print(f"Fetching from: {url}")
        with urlopen(url) as response:
            content = response.read().decode("utf-8")
        return content, True
    except URLError as exc:
        return f"Failed to fetch file: {exc}", False


def main() -> int:
    if len(sys.argv) > 1:
        user_input = sys.argv[1]
    else:
        print("Enter GitHub repo (format: owner/repo) OR local file path")
        print("Examples:")
        print("  dog-debug/albion_price_history --filename T4_BAG")
        print("  ./albion_data_dumps/europe/formatted/T4_BAG.json")
        print(r"  C:\path\to\file.json")
        user_input = input("Repo/File: ").strip()

    auto_fix = "--auto-fix" in sys.argv

    if user_input.startswith(".") or user_input.startswith("/") or user_input.startswith("\\") or ":\\" in user_input or "albion_data_dumps" in user_input:
        file_path = Path(user_input)
        if not file_path.exists():
            print(f"[ERROR] File not found: {file_path}")
            return 1

        content = file_path.read_text(encoding="utf-8")
        fixed_content, is_valid, fixes = fix_json_string(content)
        if fixes:
            for fix in fixes:
                print(f"  • {fix}")

        if not is_valid:
            print("\n[ERROR] Could not automatically fix JSON")
            return 1

        if auto_fix:
            file_path.write_text(fixed_content, encoding="utf-8")
            print(f"[INFO] File auto-saved: {file_path}")
        else:
            save_choice = input("\nSave fixed JSON? (y/n): ").strip().lower()
            if save_choice == "y":
                file_path.write_text(fixed_content, encoding="utf-8")
                print(f"[INFO] File saved: {file_path}")

        print("\n[OK] JSON successfully fixed and validated!")
        return 0

    owner, repo = parse_repo_input(user_input)
    if not owner or not repo:
        print(f"[ERROR] Invalid input: {user_input}")
        print("  Expected format: owner/repo or valid GitHub URL")
        return 1

    branch = "main"
    server = "europe"
    filename = None
    args = sys.argv[2:]
    index = 0
    while index < len(args):
        if args[index] == "--filename" and index + 1 < len(args):
            filename = args[index + 1]
            if not filename.endswith(".json"):
                filename += ".json"
            index += 2
        elif args[index] == "--branch" and index + 1 < len(args):
            branch = args[index + 1]
            index += 2
        elif args[index] == "--server" and index + 1 < len(args):
            server = args[index + 1]
            index += 2
        else:
            index += 1

    if not filename:
        print("Error: --filename is required")
        return 1

    print("\nFetching file...")
    content, success = fetch_json_from_github(filename, owner, repo, branch, server)
    if not success:
        print(f"[ERROR] {content}")
        return 1

    fixed_content, is_valid, fixes = fix_json_string(content)
    if fixes:
        for fix in fixes:
            print(f"  • {fix}")

    if not is_valid:
        print("\n[ERROR] Could not automatically fix JSON")
        return 1

    print("\n[OK] JSON successfully fixed and validated!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
