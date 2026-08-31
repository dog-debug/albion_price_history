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

# Use relative paths from project root
PROJECT_ROOT = Path(__file__).parent.parent
PATHS = {
    'raw': PROJECT_ROOT / 'albion_data_dumps' / 'raw',
    'history': PROJECT_ROOT / 'albion_data_dumps' / 'extracted' / 'history',
    'output': PROJECT_ROOT / 'albion_data_dumps' / 'formatted',
}

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


def get_available_downloads() -> List[str]:
    """Scrape AODP database page, return only market_history_YYYY_MM.sql.gz filenames"""
    try:
        req = urllib.request.Request(
            AODP_DATABASE_PAGE,
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

        print(f"[INFO] Found {len(filenames)} market_history_YYYY_MM.sql.gz file(s) on AODP page")
        return sorted(filenames)

    except Exception as e:
        print(f"[ERROR] Failed to scrape AODP page: {e}")
        return []


def setup_directories():
    """Create necessary directories"""
    for path in PATHS.values():
        path.mkdir(parents=True, exist_ok=True)


def download_latest_exports() -> List[Path]:
    """Scrape AODP page, skip already extracted or processed files, download the rest to raw/"""
    print("[INFO] Checking for new market history files...")

    processed = load_processed_files()
    available = get_available_downloads()

    if not available:
        print("[WARN] No market_history files found on AODP page — skipping download")
        return []

    # Pre-build set of SQL filenames already present in extracted/history/
    existing_sql = {f.name for f in PATHS['history'].glob('market_history_*.sql')}
    existing_gz  = {f.name for f in PATHS['history'].glob('market_history_*.sql.gz')}
    existing_raw = {f.name for f in PATHS['raw'].glob('market_history_*.sql.gz')}

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

    print(f"[INFO] {len(to_download)} new file(s) available to download")
    
    # Download only the OLDEST unprocessed file
    if not to_download:
        print("[INFO] No new files to download")
        return []
    
    to_download.sort()  # Sort chronologically (oldest first)
    filename = to_download[0]  # Get the oldest one
    
    downloaded_files = []
    url = AODP_BASE_URL + filename
    local_path = PATHS['raw'] / filename

    try:
        print(f"[DOWNLOAD] {filename} (oldest unprocessed)...")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=120) as resp, open(local_path, 'wb') as out:
            shutil.copyfileobj(resp, out)
        print(f"[SUCCESS] Downloaded {filename} to raw/")
        downloaded_files.append(local_path)
    except urllib.error.HTTPError as e:
        print(f"[ERROR] HTTP {e.code} downloading {filename}")
    except Exception as e:
        print(f"[ERROR] Error downloading {filename}: {e}")

    return downloaded_files


def extract_zip_files(zip_paths: List[Path]):
    """Extract downloaded archives to history directory, mark archive as processed"""
    for zip_path in zip_paths:
        if not zip_path.exists():
            continue

        try:
            print(f"[EXTRACT] Extracting {zip_path.name}...")

            if zip_path.suffix == '.gz' and zip_path.stem.endswith('.sql'):
                # Single gzipped SQL file — decompress directly
                dest = PATHS['history'] / zip_path.stem
                with gzip.open(zip_path, 'rb') as f_in, open(dest, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            else:
                # ZIP archive — extract all contents
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(PATHS['history'])

            print(f"[SUCCESS] Extracted {zip_path.name} to extracted/history/")
            
            # Mark archive itself as processed so it won't be re-downloaded
            save_processed_file(zip_path.name)

        except Exception as e:
            print(f"[ERROR] Failed to extract {zip_path.name}: {e}")


def load_processed_files() -> Set[str]:
    """Load list of already-processed SQL files"""
    if not PROCESSED_FILE.exists():
        return set()
    
    try:
        with open(PROCESSED_FILE, 'r') as f:
            return set(line.strip() for line in f if line.strip())
    except:
        return set()


def save_processed_file(filename: str):
    """Add file to processed list"""
    try:
        processed = load_processed_files()
        processed.add(filename)
        
        with open(PROCESSED_FILE, 'w') as f:
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


def process_market_history_files():
    """Process ONE unprocessed SQL file per run"""
    print("[INFO] Processing market history files...")
    
    if not PATHS['history'].exists():
        print("[WARN] History directory not found")
        return 0
    
    sql_files = sorted(PATHS['history'].glob('market_history*.sql')) + sorted(PATHS['history'].glob('market_history*.sql.gz'))
    processed = load_processed_files()
    processed_count = 0
    
    # Process only the FIRST unprocessed file
    for sql_file in sql_files:
        if sql_file.name in processed:
            print(f"[SKIP] {sql_file.name} already processed")
            continue
        
        try:
            print(f"\n[PROCESS] Processing {sql_file.name}...")
            records = read_sql_file(sql_file)
            
            if not records:
                print(f"[WARN] No records found in {sql_file.name}")
                save_processed_file(sql_file.name)
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
            print(f"[DEBUG] Writing {len(file_items):,} items to JSON files...")
            
            for item_idx, (item_id, price_records) in enumerate(file_items.items()):
                if item_idx % 1000 == 0 and item_idx > 0:
                    print(f"[DEBUG] Written {item_idx:,}/{len(file_items):,} items...")
                
                output_file = PATHS['output'] / f"{item_id}.json"
                
                try:
                    if output_file.exists():
                        # Append to existing file (binary mode, don't re-parse entire file)
                        with open(output_file, 'r+b') as f:
                            # Seek to 2 bytes before end (before `]}`)
                            f.seek(-2, 2)
                            # Write comma and new records
                            f.write(b',\n')
                            for record in price_records:
                                record_json = json.dumps(record, separators=(',', ':'))
                                f.write(b'    ' + record_json.encode('utf-8') + b',\n')
                            # Remove trailing comma and close properly
                            f.seek(-2, 1)  # Go back 2 bytes (comma + newline)
                            f.write(b'\n  ]\n}')
                            f.truncate()
                    else:
                        # Create new file
                        data = {
                            'itemId': item_id,
                            'priceHistory': price_records
                        }
                        with open(output_file, 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=2)
                
                except Exception as e:
                    print(f"[ERROR] Failed to write {item_id}: {e}")
            
            # Mark as processed
            save_processed_file(sql_file.name)
            processed_count += 1
            
            print(f"[SUCCESS] Processed {sql_file.name} ({len(file_items):,} items, {len(records):,} records)")
            
            # Exit after processing one file per run
            break
        
        except Exception as e:
            print(f"[ERROR] Error processing {sql_file.name}: {e}")
            import traceback
            traceback.print_exc()
    
    return processed_count


def main():
    """Main CI/CD process: download → extract → format"""
    print("[INFO] Starting AODP CI processor...")
    print(f"[INFO] Project root: {PROJECT_ROOT}")

    setup_directories()

    # Step 1: Download all new files to raw/
    downloaded = download_latest_exports()

    # Step 2: Extract each downloaded file from raw/ to extracted/history/, one at a time
    if downloaded:
        extract_zip_files(downloaded)

    # Step 3: Process all unprocessed history files, format to JSON in formatted/
    processed = process_market_history_files()

    print(f"[INFO] Processing complete. {processed} files processed.")
    print(f"[INFO] Formatted JSON files available in: albion_data_dumps/formatted/")
    return 0 if processed >= 0 else 1


if __name__ == '__main__':
    sys.exit(main())
