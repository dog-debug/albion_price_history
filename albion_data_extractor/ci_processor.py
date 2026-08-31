#!/usr/bin/env python3
"""
CI/CD processor for downloading and extracting AODP market history.
Runs headless without GUI, suitable for GitHub Actions.
"""

import os
import re
import json
import gzip
import urllib.request
import urllib.error
import sys
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

# Server codes
SOURCE_TO_SERVER = {
    6: 'west.albion-online-data.com',
    7: 'europe.albion-online-data.com',
    8: 'east.albion-online-data.com',
}

# Servers to download from
SERVERS = {
    'west': 'https://www.albion-online-data.com/database/',
    'europe': 'https://www.albion-online-data.com/database-europe/',
    'east': 'https://www.albion-online-data.com/database-east/',
}

# Use relative paths from project root
PROJECT_ROOT = Path(__file__).parent.parent

def get_paths_for_server(server_name: str) -> Dict[str, Path]:
    """Get directory paths for a specific server"""
    return {
        'raw': PROJECT_ROOT / 'albion_data_dumps' / 'raw' / server_name,
        'history': PROJECT_ROOT / 'albion_data_dumps' / 'extracted' / 'history' / server_name,
        'output': PROJECT_ROOT / 'albion_data_dumps' / 'formatted' / server_name,
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
    for server_name in SERVERS.keys():
        paths = get_paths_for_server(server_name)
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)


def download_latest_exports(server_name: str, base_url: str) -> List[Path]:
    """Scrape AODP page, skip already extracted or processed files, download the rest to raw/"""
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
    
    # Download only the OLDEST unprocessed file
    if not to_download:
        print(f"[INFO] No new files to download on {server_name}")
        return []
    
    to_download.sort()  # Sort chronologically (oldest first)
    filename = to_download[0]  # Get the oldest one
    
    downloaded_files = []
    url = base_url + filename
    local_path = paths['raw'] / filename

    try:
        print(f"[DOWNLOAD] {filename} from {server_name} (oldest unprocessed)...")
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


def load_processed_files(paths: Dict[str, Path]) -> Set[str]:
    """Load list of already-processed SQL files for a specific server"""
    processed_file = paths['output'] / '.processed.txt'
    if not processed_file.exists():
        return set()
    
    try:
        with open(processed_file, 'r') as f:
            return set(line.strip() for line in f if line.strip())
    except:
        return set()


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
    """Extract all market_history records from SQL file"""
    records = []
    
    try:
        print(f"[DEBUG] Reading {filepath.name}...")
        
        if filepath.suffix == '.gz':
            with gzip.open(filepath, 'rt', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        else:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        
        # Find all INSERT VALUES (...), (...), ... patterns
        insert_pattern = r'INSERT INTO\s+`?market_history`?\s+VALUES\s+((?:\([^()]+\)(?:,\s*)?)+)'
        
        for insert_match in re.finditer(insert_pattern, content, re.IGNORECASE):
            values_text = insert_match.group(1)
            
            # Split individual tuples
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


def transform_record(rec: Tuple) -> Dict[str, Any]:
    """
    Transform SQL record to price checker format.
    SQL record format: (id, city_id, price, item_id, location_id, quality, timestamp, source)
    """
    try:
        id_, city_id, price, item_id, location_id, quality, ts_str, source = rec
        
        # Map location to city name
        city = LOCATION_ID_TO_CITY.get(location_id, 'unknown').lower()
        
        # Map server
        server = SOURCE_TO_SERVER.get(source, 'unknown.albion-online-data.com')
        
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


def process_market_history_files(server_name: str):
    """Process ONE unprocessed SQL file per run for a specific server"""
    paths = get_paths_for_server(server_name)
    print(f"[INFO] Processing market history files for {server_name}...")
    
    if not paths['history'].exists():
        print(f"[WARN] History directory not found for {server_name}")
        return 0
    
    sql_files = sorted(paths['history'].glob('market_history*.sql')) + sorted(paths['history'].glob('market_history*.sql.gz'))
    processed = load_processed_files(paths)
    processed_count = 0
    
    # Process only the FIRST unprocessed file
    for sql_file in sql_files:
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
                    transformed = transform_record(rec)
                    
                    if transformed:
                        file_items[item_id].append(transformed)
            
            # Write items to individual JSON files (append-only)
            print(f"[DEBUG] Writing {len(file_items):,} items to JSON files for {server_name}...")
            
            for item_idx, (item_id, price_records) in enumerate(file_items.items()):
                if item_idx % 1000 == 0 and item_idx > 0:
                    print(f"[DEBUG] Written {item_idx:,}/{len(file_items):,} items...")
                
                output_file = paths['output'] / f"{item_id}.json"
                
                try:
                    if output_file.exists():
                        # Append to existing file (binary mode)
                        with open(output_file, 'r+b') as f:
                            # Seek to 1 byte before end (before `]`)
                            f.seek(-1, 2)
                            # Write comma and new records
                            f.write(b',\n')
                            for record in price_records:
                                record_json = json.dumps(record, separators=(',', ':'))
                                f.write(b'  ' + record_json.encode('utf-8') + b',\n')
                            # Remove trailing comma and close array
                            f.seek(-2, 1)  # Go back 2 bytes (comma + newline)
                            f.write(b'\n]\n')
                            f.truncate()
                    else:
                        # Create new file with simple array format
                        with open(output_file, 'w', encoding='utf-8') as f:
                            f.write('[\n')
                            for i, record in enumerate(price_records):
                                record_json = json.dumps(record, separators=(',', ':'))
                                if i < len(price_records) - 1:
                                    f.write('  ' + record_json + ',\n')
                                else:
                                    f.write('  ' + record_json + '\n')
                            f.write(']\n')
                
                except Exception as e:
                    print(f"[ERROR] Failed to write {item_id}: {e}")
            
            # Mark as processed
            save_processed_file(sql_file.name, paths)
            processed_count += 1
            
            print(f"[SUCCESS] Processed {sql_file.name} ({len(file_items):,} items, {len(records):,} records) for {server_name}")
            
            # Exit after processing one file per run
            break
        
        except Exception as e:
            print(f"[ERROR] Error processing {sql_file.name} for {server_name}: {e}")
            import traceback
            traceback.print_exc()
    
    return processed_count


def main():
    """Main CI/CD process: download → extract → format for all servers"""
    print("[INFO] Starting AODP CI processor...")
    print(f"[INFO] Project root: {PROJECT_ROOT}")

    setup_directories()

    # Process each server
    for server_name, base_url in SERVERS.items():
        print(f"\n{'='*60}")
        print(f"[INFO] Processing server: {server_name}")
        print(f"{'='*60}")
        
        # Step 1: Download oldest unprocessed file for this server
        downloaded = download_latest_exports(server_name, base_url)

        # Step 2: Extract each downloaded file, one at a time
        if downloaded:
            extract_zip_files(downloaded, server_name)

        # Step 3: Process all unprocessed history files for this server
        processed = process_market_history_files(server_name)
        print(f"[INFO] {server_name}: {processed} files processed")

    print(f"\n[INFO] Processing complete for all servers")
    print(f"[INFO] Formatted JSON files available in: albion_data_dumps/formatted/west/, /europe/, /east/")
    return 0


if __name__ == '__main__':
    sys.exit(main())
