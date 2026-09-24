#!/usr/bin/env python3
"""
JSON File Validator for Albion Price History

Fetches and validates JSON files from the GitHub repo online.
Can fetch a specific file or a random one.

Usage:
    python validate_json_random.py <owner/repo> [OPTIONS]
    
Examples:
    python validate_json_random.py YourUsername/albion_price_history --random
    python validate_json_random.py YourUsername/albion_price_history --filename T4_BAG
    python validate_json_random.py YourUsername/albion_price_history --filename T4_BAG --server europe
"""

import json
import random
import sys
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError


def get_all_json_files_from_github(owner: str, repo: str, branch: str = "main", server: str = None) -> list[str]:
    """Fetch list of all JSON files from GitHub repo."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/albion_data_dumps"
    
    if server:
        url += f"/{server}/formatted"
    
    try:
        print(f"Fetching file list from GitHub...")
        with urlopen(url) as response:
            data = json.loads(response.read().decode())
        
        # Extract JSON filenames
        json_files = [item["name"] for item in data if item["name"].endswith(".json")]
        return json_files
    except URLError as e:
        print(f"✗ Failed to fetch file list: {e}")
        return []
    except (json.JSONDecodeError, KeyError) as e:
        print(f"✗ Error parsing GitHub API response: {e}")
        return []


def fetch_json_from_github(filename: str, owner: str, repo: str, branch: str = "main", server: str = "europe") -> tuple[str, bool]:
    """Fetch a specific JSON file from GitHub raw content."""
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/albion_data_dumps/{server}/formatted/{filename}"
    
    try:
        print(f"Fetching from: {url}")
        with urlopen(url) as response:
            content = response.read().decode("utf-8")
        return content, True
    except URLError as e:
        return f"Failed to fetch file: {e}", False


def validate_json_content(content: str) -> tuple[bool, list[str]]:
    """Validate that content is proper JSON format and return ALL errors found."""
    errors = []
    
    # First, try full parse to get primary error
    try:
        json.loads(content)
        return True, []
    except json.JSONDecodeError as e:
        errors.append(f"Line {e.lineno}, Col {e.colno}: {e.msg}")
    except Exception as e:
        errors.append(f"Error parsing JSON: {str(e)}")
    
    # Now do comprehensive checks for all potential errors
    lines = content.split('\n')
    
    # Check for common structural issues
    open_braces = content.count('{')
    close_braces = content.count('}')
    if open_braces != close_braces:
        errors.append(f"Mismatched braces: {open_braces} {{ but {close_braces} }}}}")
    
    open_brackets = content.count('[')
    close_brackets = content.count(']')
    if open_brackets != close_brackets:
        errors.append(f"Mismatched brackets: {open_brackets} [ but {close_brackets} ]")
    
    open_parens = content.count('(')
    close_parens = content.count(')')
    if open_parens != close_parens:
        errors.append(f"Found parentheses: {open_parens} ( but {close_parens} ) - JSON doesn't support parentheses")
    
    # Check for trailing commas
    import re
    trailing_comma_matches = list(re.finditer(r',\s*[}\]]', content))
    if trailing_comma_matches:
        for match in trailing_comma_matches:
            line_num = content[:match.start()].count('\n') + 1
            errors.append(f"Line {line_num}: Trailing comma before {match.group()[-1]}")
    
    # Check for unclosed strings and invalid escape sequences
    in_string = False
    escape_next = False
    for i, line in enumerate(lines, 1):
        for j, char in enumerate(line):
            if escape_next:
                # Check for valid escape sequences
                if char not in ['"', '\\', '/', 'b', 'f', 'n', 'r', 't', 'u']:
                    errors.append(f"Line {i}, Col {j}: Invalid escape sequence '\\{char}'")
                escape_next = False
                continue
            
            if char == '\\' and in_string:
                escape_next = True
                continue
            
            if char == '"':
                in_string = not in_string
    
    if in_string:
        errors.append("Unclosed string: missing closing quote")
    
    # Check for single quotes (not valid JSON)
    single_quote_lines = []
    for i, line in enumerate(lines, 1):
        if "'" in line and not line.strip().startswith('//'):
            single_quote_lines.append(i)
    if single_quote_lines:
        errors.append(f"Single quotes found on lines {single_quote_lines} - JSON requires double quotes")
    
    # Check for comments (not valid JSON)
    for i, line in enumerate(lines, 1):
        if '//' in line:
            errors.append(f"Line {i}: Comments not allowed in JSON")
        if '/*' in line or '*/' in line:
            errors.append(f"Line {i}: Block comments not allowed in JSON")
    
    # Check for NaN, Infinity, undefined (not valid JSON)
    invalid_values = ['NaN', 'Infinity', 'undefined']
    for i, line in enumerate(lines, 1):
        for val in invalid_values:
            if re.search(r'\b' + val + r'\b', line):
                errors.append(f"Line {i}: '{val}' is not valid JSON (use null instead)")
    
    # Check for unquoted keys or invalid key formats
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # Look for patterns like: word: value (unquoted key)
        if re.search(r'[a-zA-Z_]\w*\s*:', stripped) and '"' not in stripped[:stripped.find(':')]:
            errors.append(f"Line {i}: Unquoted key found - all keys must be in double quotes")
    
    # Check for missing commas between array/object elements
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped and not stripped.startswith('{') and not stripped.startswith('[') and not stripped.endswith(',') and not stripped.endswith('{') and not stripped.endswith('['):
            # Check if next line has a new property/element without comma
            if i < len(lines):
                next_stripped = lines[i].strip()
                if next_stripped and next_stripped[0] in ['"', '{', '[', '-'] and not stripped.endswith(','):
                    if '}' not in stripped and ']' not in stripped:
                        errors.append(f"Line {i}: Possible missing comma before next element")
    
    # Remove duplicates and sort by line number
    errors = list(dict.fromkeys(errors))  # Remove duplicates while preserving order
    
    return False, errors


def parse_repo_input(repo_input: str) -> tuple[str, str]:
    """Parse repo input - handles both 'owner/repo' and GitHub URLs."""
    repo_input = repo_input.strip()
    
    # Handle raw GitHub URLs
    if "raw.githubusercontent.com" in repo_input:
        # Extract from: https://raw.githubusercontent.com/owner/repo/branch/...
        parts = repo_input.split("/")
        if len(parts) >= 5:
            owner = parts[3]
            repo = parts[4]
            return owner, repo
    
    # Handle github.com URLs
    if "github.com" in repo_input:
        # Extract from: https://github.com/owner/repo
        parts = repo_input.split("/")
        if len(parts) >= 5:
            owner = parts[3]
            repo = parts[4].rstrip("/")
            return owner, repo
    
    # Handle owner/repo format
    if "/" in repo_input:
        parts = repo_input.split("/")
        if len(parts) == 2:
            return parts[0], parts[1]
    
    return None, None


def main():
    # Ask user for input if not provided as argument
    if len(sys.argv) > 1:
        user_input = sys.argv[1]
    else:
        print("Enter GitHub repo (format: owner/repo) OR local file path")
        print("Examples:")
        print("  dog-debug/albion_price_history")
        print("  ./albion_data_dumps/europe/formatted/T4_BAG.json")
        print("  C:\\path\\to\\file.json\n")
        user_input = input("Repo/File: ").strip()
    
    # Check if it's a local file path
    if user_input.startswith(".") or user_input.startswith("/") or user_input.startswith("\\") or ":\\" in user_input or "albion_data_dumps" in user_input:
        # Local file mode
        file_path = Path(user_input)
        if not file_path.exists():
            print(f"✗ File not found: {file_path}")
            sys.exit(1)
        
        print(f"✓ Using local file: {file_path}")
        print(f"File size: {file_path.stat().st_size} bytes")
        
        # Read and validate
        print("\nValidating JSON format...")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"✗ Error reading file: {e}")
            sys.exit(1)
    else:
        # GitHub mode
        owner, repo = parse_repo_input(user_input)
        
        if not owner or not repo:
            print(f"✗ Invalid input: {user_input}")
            print("  Expected: owner/repo, GitHub URL, or local file path")
            sys.exit(1)
        
        branch = "main"
        server = "europe"
        filename = None
        random_mode = False
        
        # Parse options
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--random":
                random_mode = True
                i += 1
            elif args[i] == "--filename" and i + 1 < len(args):
                filename = args[i + 1]
                if not filename.endswith(".json"):
                    filename += ".json"
                i += 2
            elif args[i] == "--branch" and i + 1 < len(args):
                branch = args[i + 1]
                i += 2
            elif args[i] == "--server" and i + 1 < len(args):
                server = args[i + 1]
                i += 2
            else:
                i += 1
        
        # If no filename and not random, use random mode by default
        if not filename and not random_mode:
            random_mode = True
        
        # Get filename if random mode
        if random_mode:
            json_files = get_all_json_files_from_github(owner, repo, branch, server)
            if not json_files:
                print("✗ No JSON files found in repository")
                sys.exit(1)
            filename = random.choice(json_files)
            print(f"✓ Randomly selected: {filename}")
        else:
            print(f"✓ Using filename: {filename}")
        
        # Fetch the file
        print(f"\nFetching file...")
        content, success = fetch_json_from_github(filename, owner, repo, branch, server)
        
        if not success:
            print(f"✗ {content}")
            sys.exit(1)
        
        print(f"✓ File downloaded ({len(content)} bytes)")
        
        # Validate JSON format
        print("\nValidating JSON format...")
    
    is_valid, errors = validate_json_content(content)
    
    if is_valid:
        print(f"✓ Valid JSON - File is properly formatted!")
        
        # Show a preview of the JSON structure
        try:
            data = json.loads(content)
            
            if isinstance(data, list):
                print(f"✓ JSON structure: List with {len(data)} item(s)")
                if data and isinstance(data[0], dict):
                    keys = data[0].keys()
                    print(f"  First item keys: {', '.join(keys)}")
            elif isinstance(data, dict):
                print(f"✓ JSON structure: Dictionary with keys: {', '.join(data.keys())}")
            else:
                print(f"✓ JSON structure: {type(data).__name__}")
        except Exception as e:
            print(f"  Could not preview structure: {str(e)}")
        
        sys.exit(0)
    else:
        print(f"✗ Found {len(errors)} error(s):")
        for i, error in enumerate(errors, 1):
            print(f"  {i}. {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
