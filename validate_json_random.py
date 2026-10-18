#!/usr/bin/env python3
"""Validate local Albion JSON payloads and optionally repair invalid files."""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

try:
    from fix_json_format import fix_json_string as repair_json_string
except ImportError:  # pragma: no cover - fallback for direct script execution in some envs
    repair_json_string = None


def get_all_json_files_from_github(owner: str, repo: str, branch: str = "main", server: str | None = None) -> list[str]:
    """Fetch a list of JSON filenames from the GitHub repository."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/albion_data_dumps"
    if server:
        url += f"/{server}/formatted"

    try:
        with urlopen(url) as response:
            data = json.loads(response.read().decode("utf-8"))
        return [item["name"] for item in data if item.get("name", "").endswith(".json")]
    except (URLError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"[ERROR] Failed to fetch file list: {exc}")
        return []


def fetch_json_from_github(filename: str, owner: str, repo: str, branch: str = "main", server: str = "europe") -> tuple[str, bool]:
    """Fetch a specific JSON file from GitHub raw content."""
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/albion_data_dumps/{server}/formatted/{filename}"
    try:
        with urlopen(url) as response:
            return response.read().decode("utf-8"), True
    except URLError as exc:
        return f"Failed to fetch file: {exc}", False


def validate_json_content(content: str) -> tuple[bool, list[str]]:
    """Return whether the content is valid JSON and list any issues."""
    try:
        json.loads(content)
        return True, []
    except json.JSONDecodeError as exc:
        errors = [f"Line {exc.lineno}, Col {exc.colno}: {exc.msg}"]
        lines = content.splitlines()
        for index, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("//") or "//" in stripped:
                errors.append(f"Line {index}: Comments are not valid in JSON")
            if "'" in stripped:
                errors.append(f"Line {index}: Single quotes are not valid in JSON")
            if "NaN" in stripped or "Infinity" in stripped or "undefined" in stripped:
                errors.append(f"Line {index}: JavaScript literals are not valid in JSON")
            if re.search(r"[{\[,]\s*[A-Za-z_][A-Za-z0-9_]*\s*:", stripped):
                errors.append(f"Line {index}: Unquoted object keys are not valid in JSON")
        return False, list(dict.fromkeys(errors))
    except Exception as exc:  # pragma: no cover - defensive guard
        return False, [f"Error parsing JSON: {exc}"]


def parse_repo_input(repo_input: str) -> tuple[str | None, str | None]:
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


def find_local_json_files(root: Path) -> list[Path]:
    """Return all JSON files under the repository's formatted data directories."""
    if root.is_file():
        return [root] if root.suffix.lower() == ".json" else []

    candidates = sorted(root.glob("albion_data_dumps/*/formatted/*.json"))
    if candidates:
        return candidates
    return sorted(root.glob("**/formatted/*.json"))


def attempt_json_repair(content: str) -> tuple[str, bool, list[str]]:
    """Return repaired JSON content if the built-in fixer can recover it."""
    if repair_json_string is None:
        return content, False, ["No repair engine available"]
    return repair_json_string(content)


def validate_local_files(root: Path, fix_invalid: bool = False) -> int:
    """Validate every JSON file under the repo's formatted output directories."""
    files = find_local_json_files(root)
    if not files:
        print("[INFO] No JSON files found under albion_data_dumps/*/formatted")
        return 0

    valid_count = 0
    invalid_files: list[Path] = []
    for file_path in files:
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"[ERROR] {file_path}: {exc}")
            invalid_files.append(file_path)
            continue

        is_valid, errors = validate_json_content(content)
        if is_valid:
            valid_count += 1
            print(f"[OK] {file_path}")
            continue

        print(f"[ERROR] {file_path}")
        if fix_invalid:
            fixed_content, repaired_valid, fixes = attempt_json_repair(content)
            if repaired_valid:
                try:
                    file_path.write_text(fixed_content, encoding="utf-8")
                    print(f"[FIXED] {file_path}")
                    for fix in fixes[:5]:
                        print(f"  - {fix}")
                    valid_count += 1
                    continue
                except Exception as exc:
                    print(f"[ERROR] Could not save repaired file {file_path}: {exc}")
            else:
                print("  - Repair attempt failed")

        for error in errors[:5]:
            print(f"  - {error}")
        if len(errors) > 5:
            print(f"  - ... and {len(errors) - 5} more error(s)")
        invalid_files.append(file_path)

    total_files = len(files)
    print(f"[SUMMARY] Valid: {valid_count}/{total_files}; Invalid: {len(invalid_files)}")
    return 1 if invalid_files else 0


def main() -> int:
    if len(sys.argv) > 1:
        user_input = sys.argv[1]
    else:
        print("Enter GitHub repo (format: owner/repo) OR local file path")
        print("Examples:")
        print("  dog-debug/albion_price_history")
        print("  ./albion_data_dumps/europe/formatted/T4_BAG.json")
        print(r"  C:\path\to\file.json")
        user_input = input("Repo/File: ").strip()

    all_mode = "--all" in sys.argv
    fix_mode = "--fix" in sys.argv

    if user_input.startswith(".") or user_input.startswith("/") or user_input.startswith("\\") or ":\\" in user_input or "albion_data_dumps" in user_input or user_input == ".":
        root_path = Path(user_input)
        if not root_path.exists():
            print(f"[ERROR] Path not found: {root_path}")
            return 1

        if root_path.is_dir():
            if all_mode or user_input in {".", "./", ".\\"}:
                print(f"[INFO] Validating all JSON files under {root_path}")
                if fix_mode:
                    print("[INFO] Repairing invalid JSON files in place")
                return validate_local_files(root_path, fix_invalid=fix_mode)
            print(f"[ERROR] Directory mode requires --all: {root_path}")
            return 1

        if root_path.is_file():
            content = root_path.read_text(encoding="utf-8")
            is_valid, errors = validate_json_content(content)
            if is_valid:
                print("[OK] Valid JSON - File is properly formatted!")
                return 0
            print(f"[ERROR] Invalid JSON file: {root_path}")
            if fix_mode:
                fixed_content, repaired_valid, fixes = attempt_json_repair(content)
                if repaired_valid:
                    root_path.write_text(fixed_content, encoding="utf-8")
                    print(f"[FIXED] {root_path}")
                    return 0
            for error in errors[:5]:
                print(f"  - {error}")
            return 1

    owner, repo = parse_repo_input(user_input)
    if not owner or not repo:
        print(f"[ERROR] Invalid input: {user_input}")
        print("  Expected: owner/repo, GitHub URL, or local file path")
        return 1

    branch = "main"
    server = "europe"
    filename = None
    random_mode = False
    args = sys.argv[2:]
    index = 0
    while index < len(args):
        if args[index] == "--random":
            random_mode = True
            index += 1
        elif args[index] == "--filename" and index + 1 < len(args):
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

    if not filename and not random_mode:
        random_mode = True

    if random_mode:
        json_files = get_all_json_files_from_github(owner, repo, branch, server)
        if not json_files:
            print("[ERROR] No JSON files found in repository")
            return 1
        filename = random.choice(json_files)
        print(f"[INFO] Randomly selected: {filename}")
    else:
        print(f"[INFO] Using filename: {filename}")

    content, success = fetch_json_from_github(filename, owner, repo, branch, server)
    if not success:
        print(f"[ERROR] {content}")
        return 1

    is_valid, errors = validate_json_content(content)
    if is_valid:
        print("[OK] Valid JSON - File is properly formatted!")
        return 0

    print("\nValidating JSON format...")
    if fix_mode:
        fixed_content, repaired_valid, fixes = attempt_json_repair(content)
        if repaired_valid:
            print("[OK] File repaired and is now valid JSON.")
            for fix in fixes[:5]:
                print(f"  - {fix}")
            return 0
    for error in errors[:5]:
        print(f"  - {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
