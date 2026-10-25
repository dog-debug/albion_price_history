#!/usr/bin/env python3
"""
CI/CD processor for downloading and extracting AODP market history.
Runs headless without GUI, suitable for GitHub Actions.
Processes as many files as possible within a 5.5 hour time window.
"""

import os
import re
import json
import gzip
import urllib.request
import urllib.error
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Tuple, Set
from collections import defaultdict
import zipfile
import shutil

# Location ID to city name mapping
LOCATION_ID_TO_CITY = {
    3003: 'bridgewatch',
    3005: 'caerleon',
    3008: 'lymhurst',
    3002: 'thetford',
    3004: 'forsterling',
    3006: 'brecilien',
    4002: 'blackmarket',
    1002: 'martlock',
    7: 'blackmarket',
}

# Server codes (legacy, kept for reference)
SOURCE_TO_SERVER = {
    6: 'west.albion-online-data.com',
    7: 'europe.albion-online-data.com',
    8: 'east.albion-online-data.com',
}

# Servers to download from (in priority order: europe first, then east, then west)
SERVERS_PRIORITY = ['europe', 'east', 'west']
SERVERS = {
    'europe': 'https://www.albion-online-data.com/database-europe/',
    'east': 'https://www.albion-online-data.com/database-east/',
    'west': 'https://www.albion-online-data.com/database/',
}

# Use relative paths from project root
PROJECT_ROOT = Path(__file__).parent.parent

def get_paths_for_server(server_name: str) -> Dict[str, Path]:
    """Get directory paths for a specific server (each server has its own folder structure)"""
    return {
        'raw': PROJECT_ROOT / 'albion_data_dumps' / server_name / 'raw',
        'history': PROJECT_ROOT / 'albion_data_dumps' / server_name / 'extracted',
        'output': PROJECT_ROOT / 'albion_data_dumps' / server_name / 'formatted',
    }

PATHS = get_paths_for_server('west')  # Default for backward compatibility
PROCESSED_FILE = PATHS['output'] / '.processed.txt'
AODP_DATABASE_PAGE = 'https://www.albion-online-data.com/database/'
AODP_BASE_URL = 'https://www.albion-online-data.com/database/'


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            for name, value in attrs:
                if name == 'href' and value:
                    self.links.append(value)


# Only match exactly market_history_YYYY_MM.sql.gz — ignore db_backup, monthly_db_backup, etc.
_MARKET_HISTORY_RE = re.compile(r'^market_history_\d{4}_\d{2}\.sql\.gz$')


def get_available_downloads(base_url: str) -> List[str]:
    """Scrape AODP database page, return only market_history_YYYY_MM.sql.gz filenames"""
    try:
        req = urllib.request.Request(
            base_url,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode('utf-8', errors='ignore')

        parser = _LinkParser()
        parser.feed(html)

        filenames = []
        for link in parser.links:
            name = link.rstrip('/').split('/')[-1].split('?')[0]
            if _MARKET_HISTORY_RE.match(name):
                filenames.append(name)

        print(f"[INFO] Found {len(filenames)} market_history_YYYY_MM.sql.gz file(s) on {base_url}")
        return sorted(filenames)

    except Exception as e:
        print(f"[ERROR] Failed to scrape {base_url}: {e}")
        return []


def setup_directories():
    """Create necessary directories for all servers"""
    for server_name in SERVERS_PRIORITY:
        paths = get_paths_for_server(server_name)
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)


def download_latest_exports(server_name: str, base_url: str, start_time: float = None) -> List[Path]:
    """Scrape AODP page, skip already extracted or processed files, download the rest to raw/"""
    if start_time is None:
        start_time = time.time()
    
    paths = get_paths_for_server(server_name)
    print(f"[INFO] Checking for new market history files on {server_name}...")

    processed = load_processed_files(paths)
    available = get_available_downloads(base_url)

    if not available:
        print(f"[WARN] No market_history files found on {base_url} — skipping download")
        return []

    # Pre-build set of SQL filenames already present in extracted/history/
    existing_sql = {f.name for f in paths['history'].glob('market_history_*.sql')}
    existing_gz  = {f.name for f in paths['history'].glob('market_history_*.sql.gz')}
    existing_raw = {f.name for f in paths['raw'].glob('market_history_*.sql.gz')}

    to_download = []
    for filename in available:
        sql_name = filename[:-3]  # strip .gz → market_history_YYYY_MM.sql
        if filename in processed:
            print(f"[SKIP] {filename} — in .processed.txt")
        elif filename in existing_gz or sql_name in existing_sql:
            print(f"[SKIP] {filename} — already in extracted/history/")
        elif filename in existing_raw:
            print(f"[SKIP] {filename} — already in raw/")
        else:
            to_download.append(filename)

    print(f"[INFO] {len(to_download)} new file(s) available to download on {server_name}")
    
    # Download all unprocessed files within time window
    if not to_download:
        print(f"[INFO] No new files to download on {server_name}")
        return []
    
    to_download.sort()  # Sort chronologically (oldest first)
    
    downloaded_files = []
    for filename in to_download:
        # Check if we're running out of time
        elapsed = time.time() - start_time
        if elapsed > (5.5 * 3600 - 600):  # 10 minute buffer before deadline
            print(f"[INFO] Time limit approaching ({elapsed:.0f}s elapsed), stopping downloads")
            break
        
        url = base_url + filename
        local_path = paths['raw'] / filename

        try:
            print(f"[DOWNLOAD] {filename} from {server_name}...")
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=120) as resp, open(local_path, 'wb') as out:
                shutil.copyfileobj(resp, out)
            print(f"[SUCCESS] Downloaded {filename} to {server_name}/raw/")
            downloaded_files.append(local_path)
        except urllib.error.HTTPError as e:
            print(f"[ERROR] HTTP {e.code} downloading {filename} from {server_name}")
        except Exception as e:
            print(f"[ERROR] Error downloading {filename} from {server_name}: {e}")

    return downloaded_files


def extract_zip_files(zip_paths: List[Path], server_name: str):
    """Extract downloaded archives to history directory, mark archive as processed"""
    paths = get_paths_for_server(server_name)
    
    for zip_path in zip_paths:
        if not zip_path.exists():
            continue

        try:
            print(f"[EXTRACT] Extracting {zip_path.name} for {server_name}...")

            if zip_path.suffix == '.gz' and zip_path.stem.endswith('.sql'):
                # Single gzipped SQL file — decompress directly
                dest = paths['history'] / zip_path.stem
                with gzip.open(zip_path, 'rb') as f_in, open(dest, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            else:
                # ZIP archive — extract all contents
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(paths['history'])

            print(f"[SUCCESS] Extracted {zip_path.name} to {server_name}/extracted/history/")
            
            # Mark archive itself as processed so it won't be re-downloaded
            save_processed_file(zip_path.name, paths)

        except Exception as e:
            print(f"[ERROR] Failed to extract {zip_path.name} for {server_name}: {e}")


def reset_processed_files(paths: Dict[str, Path]) -> None:
    """Clear the per-server processed cache so the job can re-seed from scratch."""
    processed_file = paths['output'] / '.processed.txt'
    if processed_file.exists():
        try:
            processed_file.unlink()
            print(f"[INFO] Reset processed state for {paths['output']}")
        except OSError as exc:
            print(f"[WARN] Failed to reset processed state for {paths['output']}: {exc}")


def load_processed_files(paths: Dict[str, Path]) -> Set[str]:
    """Load list of already-processed SQL files for a specific server."""
    processed_file = paths['output'] / '.processed.txt'
    if str(os.getenv('ALBION_RESET_PROCESSED', '0')).lower() in {'1', 'true', 'yes'}:
        reset_processed_files(paths)
        return set()

    if not processed_file.exists():
        return set()

    try:
        with open(processed_file, 'r') as f:
            processed = {line.strip() for line in f if line.strip()}
    except Exception:
        return set()

    # If the repo was freshly checked out or the formatted outputs were cleaned up,
    # a stale .processed.txt can make the job think every remote file is already done.
    if processed and not any(paths['output'].glob('*.json')):
        print(f"[WARN] No JSON output found for {paths['output']}; resetting stale processed state.")
        reset_processed_files(paths)
        return set()

    return processed


def save_processed_file(filename: str, paths: Dict[str, Path]):
    """Add file to processed list for a specific server"""
    try:
        processed_file = paths['output'] / '.processed.txt'
        processed = load_processed_files(paths)
        processed.add(filename)
        
        with open(processed_file, 'w') as f:
            for name in sorted(processed):
                f.write(name + '\n')
    except Exception as e:
        print(f"[ERROR] Failed to save processed file: {e}")


def parse_sql_insert(line: str) -> List[Any]:
    """Parse VALUES tuple from SQL INSERT"""
    match = re.search(r'\((.*)\)(?:,|\s|;|$)', line)
    if not match:
        return None
    
    content = match.group(1)
    values = []
    current = ""
    in_quotes = False
    
    for char in content:
        if char == "'" and (not current or current[-1] != '\\'):
            in_quotes = not in_quotes
            current += char
        elif char == ',' and not in_quotes:
            values.append(current.strip())
            current = ""
        else:
            current += char
    
    if current:
        values.append(current.strip())
    
    result = []
    for val in values:
        val = val.strip()
        if val.startswith("'") and val.endswith("'"):
            result.append(val[1:-1])
        elif val.upper() in ('NULL', 'NONE'):
            result.append(None)
        else:
            try:
                result.append(int(val) if '.' not in val else float(val))
            except:
                result.append(val)
    
    return result if len(result) >= 8 else None


def read_sql_file(filepath: Path) -> List[Tuple]:
    """Extract all market_history records from SQL file without loading the whole file into memory."""
    records = []

    try:
        print(f"[DEBUG] Reading {filepath.name}...")

        opener = gzip.open if filepath.suffix == '.gz' else open
        mode = 'rt'

        with opener(filepath, mode, encoding='utf-8', errors='ignore') as f:
            buffer = ''
            in_insert = False

            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue

                if not in_insert:
                    if re.match(r'INSERT\s+INTO\s+`?market_history`?\s+VALUES\s*', line, re.IGNORECASE):
                        buffer = line
                        in_insert = True
                    continue

                buffer += ' ' + line
                if line.endswith(';'):
                    insert_pattern = r'INSERT INTO\s+`?market_history`?\s+VALUES\s+((?:\([^()]+\)(?:,\s*)?)+)'
                    insert_match = re.search(insert_pattern, buffer, re.IGNORECASE)
                    if insert_match:
                        values_text = insert_match.group(1)
                        for tuple_match in re.finditer(r'\(([^()]+)\)', values_text):
                            tuple_str = '(' + tuple_match.group(1) + ')'
                            values = parse_sql_insert(tuple_str)
                            if values and len(values) >= 8:
                                records.append(tuple(values[:8]))
                    in_insert = False
                    buffer = ''

            if in_insert and buffer:
                insert_pattern = r'INSERT INTO\s+`?market_history`?\s+VALUES\s+((?:\([^()]+\)(?:,\s*)?)+)'
                insert_match = re.search(insert_pattern, buffer, re.IGNORECASE)
                if insert_match:
                    values_text = insert_match.group(1)
                    for tuple_match in re.finditer(r'\(([^()]+)\)', values_text):
                        tuple_str = '(' + tuple_match.group(1) + ')'
                        values = parse_sql_insert(tuple_str)
                        if values and len(values) >= 8:
                            records.append(tuple(values[:8]))

        print(f"[DEBUG] Extracted {len(records)} records from {filepath.name}")
        return records

    except Exception as e:
        print(f"[ERROR] Error reading {filepath.name}: {e}")
        return records


def transform_record(rec: Tuple, server_name: str) -> Dict[str, Any]:
    """
    Transform SQL record to price checker format.
    SQL record format: (id, city_id, price, item_id, location_id, quality, timestamp, source)
    server_name: The server this record came from (europe, east, or west)
    """
    try:
        id_, city_id, price, item_id, location_id, quality, ts_str, source = rec
        
        # Map location to city name
        city = LOCATION_ID_TO_CITY.get(location_id, 'unknown').lower()
        
        # Use just the server name (not the full domain)
        server = server_name
        
        # Parse timestamp
        try:
            if isinstance(ts_str, str):
                dt = datetime.strptime(ts_str.split('.')[0], '%Y-%m-%d %H:%M:%S')
                timestamp = dt.isoformat() + 'Z'
            else:
                timestamp = str(ts_str)
        except:
            timestamp = str(ts_str)
        
        # Price fields
        price_val = int(price) if price else 0
        
        return {
            'timestamp': timestamp,
            'city': city,
            'quality': int(quality) if quality else 1,
            'server': server,
            'sellPrice': price_val,
            'buyPrice': price_val,
            'quantity': int(city_id) if city_id else 0,
        }
    except Exception as e:
        print(f"[ERROR] Failed to transform record {rec}: {e}")
        return None


def process_market_history_files(server_name: str, deadline_time: float) -> int:
    """
    Process unprocessed SQL files for a specific server until deadline is reached.
    
    Args:
        server_name: Name of the server to process
        deadline_time: Unix timestamp when processing must stop
        
    Returns:
        Number of files processed
    """
    paths = get_paths_for_server(server_name)
    print(f"[INFO] Processing market history files for {server_name}...")
    
    if not paths['history'].exists():
        print(f"[WARN] History directory not found for {server_name}")
        return 0
    
    sql_files = sorted(paths['history'].glob('market_history*.sql')) + sorted(paths['history'].glob('market_history*.sql.gz'))
    processed = load_processed_files(paths)
    processed_count = 0
    
    # Process multiple files until deadline
    for sql_file in sql_files:
        # Check if we're running out of time (leave 5 minute buffer for cleanup)
        if time.time() > deadline_time - 300:
            print(f"[INFO] Approaching deadline, stopping processing to allow time for commit/push")
            break
        
        if sql_file.name in processed:
            print(f"[SKIP] {sql_file.name} already processed for {server_name}")
            continue
        
        try:
            print(f"\n[PROCESS] Processing {sql_file.name} for {server_name}...")
            records = read_sql_file(sql_file)
            
            if not records:
                print(f"[WARN] No records found in {sql_file.name}")
                save_processed_file(sql_file.name, paths)
                processed_count += 1
                continue
            
            # Group records by item_id in memory
            file_items: Dict[str, List[Dict]] = defaultdict(list)
            
            for i, rec in enumerate(records):
                if i % 500000 == 0 and i > 0:
                    print(f"[DEBUG] Processed {i:,}/{len(records):,} records...")
                
                if len(rec) >= 8:
                    item_id = rec[3]  # item_id is at index 3
                    transformed = transform_record(rec, server_name)
                    
                    if transformed:
                        file_items[item_id].append(transformed)
            
            # Write items to individual JSON files with splitting at 90MB
            MAX_FILE_SIZE = 90 * 1024 * 1024  # 90MB safe limit
            print(f"[DEBUG] Writing {len(file_items):,} items to JSON files for {server_name}...")
            
            for item_idx, (item_id, price_records) in enumerate(file_items.items()):
                if item_idx % 1000 == 0 and item_idx > 0:
                    print(f"[DEBUG] Written {item_idx:,}/{len(file_items):,} items...")
                
                try:
                    # ALWAYS append to item.json - never skip or create new numbered files
                    base_output = paths['output'] / f"{item_id}.json"
                    
                    # Load existing records from base file (or empty if doesn't exist)
                    existing_records = []
                    if base_output.exists():
                        try:
                            with open(base_output, 'r', encoding='utf-8') as f:
                                existing_records = json.load(f)
                                if not isinstance(existing_records, list):
                                    existing_records = []
                        except (json.JSONDecodeError, OSError) as e:
                            print(f"[WARN] Failed to read existing {base_output.name}, starting fresh: {e}")
                            existing_records = []
                    
                    # Merge new records with existing ones (no duplicates by timestamp+city+quality)
                    existing_keys = {(r['timestamp'], r['city'], r['quality']) for r in existing_records if isinstance(r, dict)}
                    unique_new_records = [r for r in price_records if (r['timestamp'], r['city'], r['quality']) not in existing_keys]
                    
                    # Combine all records
                    all_records = existing_records + unique_new_records
                    
                    # Write to base file (may split if over 90MB)
                    current_file_num = 1
                    current_records = []
                    
                    for record in all_records:
                        current_records.append(record)
                        
                        # Check actual JSON size every 100 records
                        if len(current_records) % 100 == 0:
                            temp_json = json.dumps(current_records, indent=2, ensure_ascii=False)
                            current_size = len(temp_json.encode('utf-8'))
                            
                            # If would exceed 90MB, write current file and start new numbered file
                            if current_size > MAX_FILE_SIZE and len(current_records) > 1:
                                previous_records = current_records[:-1]
                                
                                # Determine output filename
                                if current_file_num == 1:
                                    current_output = paths['output'] / f"{item_id}.json"
                                else:
                                    current_output = paths['output'] / f"{item_id}_{current_file_num}.json"
                                
                                # APPEND to file if it exists, don't overwrite
                                existing_in_file = []
                                if current_output.exists():
                                    try:
                                        with open(current_output, 'r', encoding='utf-8') as f:
                                            existing_in_file = json.load(f)
                                            if not isinstance(existing_in_file, list):
                                                existing_in_file = []
                                    except (json.JSONDecodeError, OSError):
                                        existing_in_file = []
                                
                                # Only write new records not already in file
                                file_keys = {(r['timestamp'], r['city'], r['quality']) for r in existing_in_file if isinstance(r, dict)}
                                final_records = existing_in_file + [r for r in previous_records if (r['timestamp'], r['city'], r['quality']) not in file_keys]
                                
                                with open(current_output, 'w', encoding='utf-8') as f:
                                    json.dump(final_records, f, indent=2, ensure_ascii=False)
                                    f.write('\n')
                                
                                actual_size_bytes = current_output.stat().st_size
                                actual_size_mb = actual_size_bytes / (1024 * 1024)
                                print(f"[DEBUG] Split {item_id}: wrote {len(final_records)} records ({actual_size_mb:.2f}MB) to {current_output.name}")
                                
                                # Move last record to next file
                                current_file_num += 1
                                current_records = [current_records[-1]]
                    
                    # Write final batch
                    if current_records:
                        if current_file_num == 1:
                            current_output = paths['output'] / f"{item_id}.json"
                        else:
                            current_output = paths['output'] / f"{item_id}_{current_file_num}.json"
                        
                        # APPEND to file if it exists
                        existing_in_file = []
                        if current_output.exists():
                            try:
                                with open(current_output, 'r', encoding='utf-8') as f:
                                    existing_in_file = json.load(f)
                                    if not isinstance(existing_in_file, list):
                                        existing_in_file = []
                            except (json.JSONDecodeError, OSError):
                                existing_in_file = []
                        
                        # Only write new records not already in file
                        file_keys = {(r['timestamp'], r['city'], r['quality']) for r in existing_in_file if isinstance(r, dict)}
                        final_records = existing_in_file + [r for r in current_records if (r['timestamp'], r['city'], r['quality']) not in file_keys]
                        
                        with open(current_output, 'w', encoding='utf-8') as f:
                            json.dump(final_records, f, indent=2, ensure_ascii=False)
                            f.write('\n')
                        
                        actual_size_bytes = current_output.stat().st_size
                        actual_size_mb = actual_size_bytes / (1024 * 1024)
                        
                        if actual_size_mb > 0.5:
                            print(f"[DEBUG] File {item_id}: wrote {len(final_records)} records ({actual_size_mb:.2f}MB) to {current_output.name}")


                
                except Exception as e:
                    print(f"[ERROR] Failed to write {item_id}: {e}")
            
            # Mark as processed
            save_processed_file(sql_file.name, paths)
            processed_count += 1
            
            print(f"[SUCCESS] Processed {sql_file.name} ({len(file_items):,} items, {len(records):,} records) for {server_name}")
        
        except Exception as e:
            print(f"[ERROR] Error processing {sql_file.name} for {server_name}: {e}")
            import traceback
            traceback.print_exc()
    
    return processed_count


def count_unprocessed_available_files(server_name: str) -> int:
    """
    Count how many unprocessed files are available to download from a server.
    This checks the remote server's available files minus what's in .processed.txt
    """
    paths = get_paths_for_server(server_name)
    base_url = SERVERS[server_name]
    processed = load_processed_files(paths)
    
    try:
        available = get_available_downloads(base_url)
        if not available:
            return 0
        
        # Count files that haven't been processed yet
        unprocessed = [f for f in available if f not in processed]
        return len(unprocessed)
    except Exception as e:
        print(f"[WARN] Error checking available files for {server_name}: {e}")
        return 0


def find_most_behind_server(servers: List[str]) -> str:
    """Find which server has the most unprocessed files available for download."""
    print(f"[INFO] Analyzing available files on each server...")
    server_counts = {}
    
    for server in servers:
        count = count_unprocessed_available_files(server)
        server_counts[server] = count
        print(f"[INFO] Server {server}: {count} unprocessed files available for download")
    
    # Return server with most unprocessed files
    most_behind = max(servers, key=lambda s: server_counts[s])
    print(f"[INFO] Most behind server: {most_behind} with {server_counts[most_behind]} unprocessed files")
    
    # If all servers are at 0, default to first server
    if server_counts[most_behind] == 0:
        print(f"[WARN] All servers up to date, defaulting to: {servers[0]}")
        return servers[0]
    
    return most_behind



def main():
    """
    Main CI/CD process: Download and process as many files as possible within 5.5 hours.
    
    Logic:
    1. Check all servers' processed.txt to find which needs the most work
    2. Download and process that server's entire database within time window
    3. If time remains, move to next behind server
    """
    print("[INFO] Starting AODP CI processor...")
    print(f"[INFO] Project root: {PROJECT_ROOT}")
    print(f"[INFO] Time window: 5.5 hours (19,800 seconds)")
    print(f"[INFO] Processing priority: {' -> '.join(SERVERS_PRIORITY)}")
    
    # Set deadline: 5.5 hours from now (minus small buffer for cleanup)
    start_time = time.time()
    deadline_time = start_time + (5.5 * 3600)
    
    print(f"[INFO] Start time: {datetime.fromtimestamp(start_time)}")
    print(f"[INFO] Deadline: {datetime.fromtimestamp(deadline_time)}")

    setup_directories()

    # Phase 1: Determine which server is most behind
    print(f"\n{'='*60}")
    print(f"[PHASE 1] Checking which server needs the most work...")
    print(f"{'='*60}")
    
    most_behind = find_most_behind_server(SERVERS_PRIORITY)
    
    # Phase 2: Process that server's files within time window
    print(f"\n{'='*60}")
    print(f"[PHASE 2] Processing server: {most_behind}")
    print(f"{'='*60}")
    
    base_url = SERVERS[most_behind]
    paths = get_paths_for_server(most_behind)
    
    # Check if this server has unprocessed files
    available = get_available_downloads(base_url)
    if not available:
        print(f"[WARN] No market_history files available on {most_behind}")
    else:
        print(f"[INFO] Found {len(available)} total files on {most_behind}")
        
        # Download and process all available files until deadline
        processed = load_processed_files(paths)
        unprocessed_files = [f for f in sorted(available) if f not in processed]
        
        if unprocessed_files:
            print(f"[INFO] {len(unprocessed_files)} unprocessed files to download")
            
            for filename in unprocessed_files:
                # Check if we're running out of time
                elapsed = time.time() - start_time
                if elapsed > (5.5 * 3600 - 600):  # 10 minute buffer
                    print(f"[INFO] Time limit approaching ({elapsed:.0f}s elapsed), stopping downloads")
                    break
                
                # Download file
                print(f"\n[DOWNLOAD] Downloading {filename}...")
                try:
                    url = base_url + filename
                    local_path = paths['raw'] / filename
                    
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=120) as resp, open(local_path, 'wb') as out:
                        shutil.copyfileobj(resp, out)
                    print(f"[SUCCESS] Downloaded {filename}")
                    # Extract immediately after download
                    print(f"[EXTRACT] Extracting {filename}...")
                    print(f"[EXTRACT] Extracting {filename}...")
                    extracted_path = paths['history'] / filename[:-3]  # Remove .gz
                    try:
                        if filename.endswith('.gz'):
                            with gzip.open(local_path, 'rt', encoding='utf-8', errors='ignore') as f_in:
                                with open(extracted_path, 'w', encoding='utf-8') as f_out:
                                    f_out.write(f_in.read())
                        else:
                            with open(local_path, 'r', encoding='utf-8', errors='ignore') as f_in:
                                with open(extracted_path, 'w', encoding='utf-8') as f_out:
                                    f_out.write(f_in.read())
                        print(f"[SUCCESS] Extracted to {extracted_path.name}")
                    except Exception as e:
                        print(f"[ERROR] Failed to extract {filename}: {e}")
                        continue
                
                except Exception as e:
                    print(f"[ERROR] Failed to download {filename}: {e}")
                    continue
        else:
            print(f"[INFO] All files already processed on {most_behind}")
    
    # Phase 3: Process extracted files
    print(f"\n{'='*60}")
    print(f"[PHASE 3] Processing extracted files...")
    print(f"{'='*60}")
    
    processed_count = process_market_history_files(most_behind, deadline_time)
    print(f"[SUCCESS] {most_behind}: {processed_count} files processed")
    
    # Phase 4: Check time and process other servers if time permits
    elapsed = time.time() - start_time
    remaining = deadline_time - time.time()
    print(f"\n{'='*60}")
    print(f"[INFO] Elapsed time: {elapsed:.0f}s ({elapsed/3600:.1f}h)")
    print(f"[INFO] Remaining time: {remaining:.0f}s ({remaining/3600:.1f}h)")
    print(f"{'='*60}")
    
    if remaining > 1200:  # More than 20 minutes remaining
        other_servers = [s for s in SERVERS_PRIORITY if s != most_behind]
        for server_name in other_servers:
            if time.time() > deadline_time - 600:  # 10 minute buffer
                print(f"[INFO] Insufficient time to process {server_name}, stopping")
                break
            
            print(f"\n[PHASE 4] Processing secondary server: {server_name}")
            base_url = SERVERS[server_name]
            paths = get_paths_for_server(server_name)
            
            available = get_available_downloads(base_url)
            if available:
                processed = load_processed_files(paths)
                unprocessed_files = [f for f in sorted(available) if f not in processed]
                
                if unprocessed_files:
                    # Download a few files
                    for filename in unprocessed_files[:3]:  # Limit to 3 files per secondary server
                        if time.time() > deadline_time - 600:
                            print(f"[INFO] Time limit approaching, stopping")
                            break
                        
                        try:
                            url = base_url + filename
                            local_path = paths['raw'] / filename
                            
                            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                            with urllib.request.urlopen(req, timeout=120) as resp, open(local_path, 'wb') as out:
                                shutil.copyfileobj(resp, out)
                            
                            # Extract
                            extracted_path = paths['history'] / filename[:-3]
                            
                            if filename.endswith('.gz'):
                                with gzip.open(local_path, 'rt', encoding='utf-8', errors='ignore') as f_in:
                                    with open(extracted_path, 'w', encoding='utf-8') as f_out:
                                        f_out.write(f_in.read())
                            else:
                                with open(local_path, 'r', encoding='utf-8', errors='ignore') as f_in:
                                    with open(extracted_path, 'w', encoding='utf-8') as f_out:
                                        f_out.write(f_in.read())
                            
                            print(f"[SUCCESS] Downloaded and extracted {filename}")
                        except Exception as e:
                            print(f"[ERROR] Failed to process {filename}: {e}")
                            continue
                    
                    # Process this server's files
                    processed_count = process_market_history_files(server_name, deadline_time)
                    print(f"[SUCCESS] {server_name}: {processed_count} files processed")
    
    print(f"\n{'='*60}")
    print(f"[INFO] Total elapsed time: {time.time() - start_time:.0f}s")
    print(f"[INFO] Processing complete. Formatted JSON files available in: albion_data_dumps/{{europe,east,west}}/formatted/")
    print(f"{'='*60}")
    
    # COMMIT AND PUSH TO GITHUB
    print(f"\n{'='*60}")
    print(f"[COMMIT] Committing changes to repository...")
    print(f"{'='*60}")
    
    try:
        import subprocess
        
        # Stage all changes
        subprocess.run(['git', 'add', '-A'], cwd=PROJECT_ROOT, check=True, capture_output=True)
        
        # Check if there are changes to commit
        result = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=PROJECT_ROOT)
        if result.returncode != 0:  # There are changes
            # Commit
            commit_msg = f"Market history update - {len(SERVERS_PRIORITY)} servers processed at {datetime.now().isoformat()}"
            subprocess.run(['git', 'commit', '-m', commit_msg], cwd=PROJECT_ROOT, check=True, capture_output=True)
            print(f"[SUCCESS] Committed changes: {commit_msg}")
            
            # Push to GitHub
            print(f"[PUSH] Pushing to GitHub...")
            subprocess.run(['git', 'push', 'origin', 'main'], cwd=PROJECT_ROOT, check=True, capture_output=True)
            print(f"[SUCCESS] Pushed to GitHub successfully!")
        else:
            print(f"[INFO] No changes to commit (data already current)")
    
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Git operation failed: {e}")
        print(f"[WARN] Data was processed but not committed/pushed to GitHub")
    except Exception as e:
        print(f"[ERROR] Unexpected error during commit/push: {e}")
        print(f"[WARN] Data was processed but not committed/pushed to GitHub")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
