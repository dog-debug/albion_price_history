#!/usr/bin/env python3
"""
JSON Format Fixer for Albion Price History

Automatically fixes common JSON format issues and validates the output.

Usage:
    python fix_json_format.py <owner/repo> [--filename FILENAME] [--server SERVER]
    
Examples:
    python fix_json_format.py dog-debug/albion_price_history --random
    python fix_json_format.py dog-debug/albion_price_history --filename T4_BAG
"""

import json
import re
import sys
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError
from io import StringIO


def parse_repo_input(repo_input: str) -> tuple[str, str]:
    """Parse repo input - handles both 'owner/repo' and GitHub URLs."""
    repo_input = repo_input.strip()
    
    # Handle raw GitHub URLs
    if "raw.githubusercontent.com" in repo_input:
        parts = repo_input.split("/")
        if len(parts) >= 5:
            owner = parts[3]
            repo = parts[4]
            return owner, repo
    
    # Handle github.com URLs
    if "github.com" in repo_input:
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


def fix_json_string(content: str) -> tuple[str, bool, list[str]]:
    """Attempt to fix common JSON formatting issues."""
    fixes_applied = []
    original_content = content
    
    # Fix 1: Remove comments (// style)
    if "//" in content:
        content = re.sub(r'//.*$', '', content, flags=re.MULTILINE)
        fixes_applied.append("Removed // comments")
    
    # Fix 2: Remove block comments (/* */)
    if "/*" in content:
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        fixes_applied.append("Removed /* */ comments")
    
    # Fix 3: Replace single quotes with double quotes (carefully)
    pattern = r"'([^']*?)'"
    if re.search(pattern, content):
        if "'" in content and '"' not in content:
            content = re.sub(r"'([^']*?)'", r'"\1"', content)
            fixes_applied.append("Converted single quotes to double quotes")
    
    # Fix 4: Remove trailing commas before } and ]
    content = re.sub(r',(\s*[}\]])', r'\1', content)
    if re.search(r',\s*[}\]]', original_content):
        fixes_applied.append("Removed trailing commas")
    
    # Fix 5: Try to fix unquoted keys
    original_before_fix5 = content
    content = re.sub(r'([{\[,]\s*)([a-zA-Z_]\w*)(\s*:)', r'\1"\2"\3', content)
    if content != original_before_fix5:
        fixes_applied.append("Quoted unquoted keys")
    
    # Fix 6: Replace undefined/Infinity/NaN with null
    replacements = {
        'undefined': 'null',
        'Infinity': 'null',
        'NaN': 'null',
    }
    for old, new in replacements.items():
        if re.search(r'\b' + old + r'\b', content):
            content = re.sub(r'\b' + old + r'\b', new, content)
            fixes_applied.append(f"Replaced {old} with {new}")
    
def fix_json_string(content: str) -> tuple[str, bool, list[str]]:
    """Attempt to fix common JSON formatting issues."""
    fixes_applied = []
    original_content = content
    
    # Fix 1: Remove comments (// style)
    if "//" in content:
        content = re.sub(r'//.*$', '', content, flags=re.MULTILINE)
        fixes_applied.append("Removed // comments")
    
    # Fix 2: Remove block comments (/* */)
    if "/*" in content:
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        fixes_applied.append("Removed /* */ comments")
    
    # Fix 3: Replace single quotes with double quotes
    pattern = r"'([^']*?)'"
    if re.search(pattern, content):
        if "'" in content and '"' not in content:
            content = re.sub(r"'([^']*?)'", r'"\1"', content)
            fixes_applied.append("Converted single quotes to double quotes")
    
    # Fix 4: Remove trailing commas before } and ]
    content = re.sub(r',(\s*[}\]])', r'\1', content)
    if re.search(r',\s*[}\]]', original_content):
        fixes_applied.append("Removed trailing commas")
    
    # Fix 5: Try to fix unquoted keys
    original_before_fix5 = content
    content = re.sub(r'([{\[,]\s*)([a-zA-Z_]\w*)(\s*:)', r'\1"\2"\3', content)
    if content != original_before_fix5:
        fixes_applied.append("Quoted unquoted keys")
    
    # Fix 6: Replace undefined/Infinity/NaN with null
    replacements = {
        'undefined': 'null',
        'Infinity': 'null',
        'NaN': 'null',
    }
    for old, new in replacements.items():
        if re.search(r'\b' + old + r'\b', content):
            content = re.sub(r'\b' + old + r'\b', new, content)
            fixes_applied.append(f"Replaced {old} with {new}")
    
    # Fix 7: Handle premature array closure: ], followed by more objects
    # Replace ], with just , to continue the array
    if re.search(r'\]\s*,', content):
        content = re.sub(r'\]\s*,', ',', content)
        fixes_applied.append("Fixed premature array closure (], pattern)")
    
    # Also handle ]\n  { pattern
    if re.search(r'\]\s*\n\s*\{', content):
        content = re.sub(r'\]\s*\n(\s*\{)', r',\n\1', content)
        fixes_applied.append("Fixed premature array closure (newline pattern)")
    
    # Fix 8: Ensure file ends with ]
    content = content.rstrip()
    if not content.endswith(']'):
        content += '\n]'
        fixes_applied.append("Added closing ]")
    
    # Fix 9: Try to parse and format as one object per line
    try:
        parsed = json.loads(content)
        
        # Format as array with one object per line
        if isinstance(parsed, list):
            lines = ["["]
            for i, obj in enumerate(parsed):
                obj_str = json.dumps(obj, separators=(',', ':'), ensure_ascii=False)
                if i < len(parsed) - 1:
                    lines.append(f"  {obj_str},")
                else:
                    lines.append(f"  {obj_str}")
            lines.append("]")
            content = "\n".join(lines)
        else:
            # Single object, wrap in array
            obj_str = json.dumps(parsed, separators=(',', ':'), ensure_ascii=False)
            content = f"[\n  {obj_str}\n]"
        
        fixes_applied.append("Reformatted to one object per line")
        return content, True, fixes_applied
    except json.JSONDecodeError as e:
        return original_content, False, fixes_applied + [f"Could not parse JSON: {e.msg} at line {e.lineno}"]


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


def main():
    # Ask user for input if not provided as argument
    if len(sys.argv) > 1:
        user_input = sys.argv[1]
    else:
        print("Enter GitHub repo (format: owner/repo) OR local file path")
        print("Examples:")
        print("  dog-debug/albion_price_history --filename T4_BAG")
        print("  ./albion_data_dumps/europe/formatted/T4_BAG.json")
        print("  C:\\path\\to\\file.json\n")
        user_input = input("Repo/File: ").strip()
    
    # Check for --auto-fix flag
    auto_fix = "--auto-fix" in sys.argv
    
    # Check if it's a local file path
    if user_input.startswith(".") or user_input.startswith("/") or user_input.startswith("\\") or ":\\" in user_input or "albion_data_dumps" in user_input:
        # Local file mode
        file_path = user_input
        if not Path(file_path).exists():
            print(f"✗ File not found: {file_path}")
            sys.exit(1)
        
        print(f"✓ Using local file: {file_path}")
        
        # Read file
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"✗ Error reading file: {e}")
            sys.exit(1)
        
        print(f"File size: {len(content)} bytes")
        
        # Try to fix it
        print("\nAttempting to fix JSON format...")
        fixed_content, is_valid, fixes = fix_json_string(content)
        
        if fixes:
            for fix in fixes:
                print(f"  • {fix}")
        
        # Count items in original vs fixed
        original_items = 0
        fixed_items = 0
        try:
            original_data = json.loads(content)
            if isinstance(original_data, list):
                original_items = len(original_data)
        except:
            pass
        
        try:
            fixed_data = json.loads(fixed_content)
            if isinstance(fixed_data, list):
                fixed_items = len(fixed_data)
        except:
            pass
        
        if original_items > 0:
            print(f"\nItems count: {original_items} → {fixed_items}")
            if fixed_items != original_items:
                print(f"  ⚠️  {original_items - fixed_items} items were lost!")
        
        if is_valid:
            print(f"\n✓ JSON successfully fixed and validated!")
            
            # Save file if auto-fix is enabled or ask user
            if auto_fix:
                try:
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(fixed_content)
                    print(f"✓ File auto-saved: {file_path}")
                except Exception as e:
                    print(f"✗ Error saving file: {e}")
                    sys.exit(1)
            else:
                save_choice = input("\nSave fixed JSON? (y/n): ").strip().lower()
                if save_choice == 'y':
                    try:
                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write(fixed_content)
                        print(f"✓ File saved: {file_path}")
                    except Exception as e:
                        print(f"✗ Error saving file: {e}")
                        sys.exit(1)
            
            print(f"\nFixed file ({len(fixed_content)} bytes):")
            print("=" * 50)
            print(fixed_content[:500] + ("..." if len(fixed_content) > 500 else ""))
            print("=" * 50)
            sys.exit(0)
        else:
            print(f"\n✗ Could not automatically fix JSON")
            if fixes:
                print(f"  Attempted: {', '.join(fixes)}")
            sys.exit(1)
    else:
        # GitHub mode
        owner, repo = parse_repo_input(user_input)
        
        if not owner or not repo:
            print(f"✗ Invalid input: {user_input}")
            print("  Expected format: owner/repo or valid GitHub URL")
            sys.exit(1)
    
    branch = "main"
    server = "europe"
    filename = None
    
    # Parse options
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--filename" and i + 1 < len(args):
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
    
    # Need filename
    if not filename:
        print("Error: --filename is required")
        sys.exit(1)
    
    # Fetch the file
    print(f"\nFetching file...")
    content, success = fetch_json_from_github(filename, owner, repo, branch, server)
    
    if not success:
        print(f"✗ {content}")
        sys.exit(1)
    
    print(f"✓ File downloaded ({len(content)} bytes)")
    
    # Try to fix it
    print("\nAttempting to fix JSON format...")
    fixed_content, is_valid, fixes = fix_json_string(content)
    
    if fixes:
        for fix in fixes:
            print(f"  • {fix}")
    
    if is_valid:
        print(f"\n✓ JSON successfully fixed and validated!")
        print(f"\nFixed file ({len(fixed_content)} bytes):")
        print("=" * 50)
        print(fixed_content[:500] + ("..." if len(fixed_content) > 500 else ""))
        print("=" * 50)
        sys.exit(0)
    else:
        print(f"\n✗ Could not automatically fix JSON")
        if fixes:
            print(f"  Attempted: {', '.join(fixes)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
