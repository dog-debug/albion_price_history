#!/usr/bin/env python3
"""
Wipe all price history data from the repository.
This allows the workflow to start completely fresh on next run.
"""

import os
import shutil
from pathlib import Path

def wipe_price_history():
    """Remove all generated price history data"""
    repo_root = Path(__file__).parent
    
    # Directories to wipe
    dirs_to_wipe = [
        repo_root / 'albion_data_dumps',
    ]
    
    # Files to wipe
    files_to_wipe = [
        repo_root / 'albion_data_extractor' / '.processed_europe.txt',
        repo_root / 'albion_data_extractor' / '.processed_east.txt',
        repo_root / 'albion_data_extractor' / '.processed_west.txt',
    ]
    
    print("[WIPE] Wiping price history data...\n")
    
    # Remove directories
    for dir_path in dirs_to_wipe:
        if dir_path.exists():
            print(f"[REMOVE] Directory: {dir_path.name}/")
            shutil.rmtree(dir_path)
        else:
            print(f"[SKIP] Directory not found (already clean): {dir_path.name}/")
    
    # Remove files
    for file_path in files_to_wipe:
        if file_path.exists():
            print(f"[REMOVE] File: {file_path.name}")
            file_path.unlink()
        else:
            print(f"[SKIP] File not found (already clean): {file_path.name}")
    
    print("\n[SUCCESS] Price history data wiped successfully!")
    print("[INFO] The workflow will start completely fresh on next run.")

if __name__ == '__main__':
    wipe_price_history()
