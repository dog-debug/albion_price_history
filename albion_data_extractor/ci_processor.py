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
from concurrent.futures import ProcessPoolExecutor
import zipfile
import shutil

try:
    import orjson
except ImportError:  # pragma: no cover - optional dependency fallback
    orjson = None

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
DEFAULT_MAX_RUNTIME_SECONDS = int(os.getenv('ALBION_MAX_RUNTIME_SECONDS', '19800'))
DEFAULT_RUNTIME_BUFFER_SECONDS = int(os.getenv('ALBION_RUNTIME_BUFFER_SECONDS', '0'))
DEFAULT_DOWNLOAD_RETRIES = int(os.getenv('ALBION_DOWNLOAD_RETRIES', '5'))
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = int(os.getenv('ALBION_DOWNLOAD_TIMEOUT_SECONDS', '120'))
DEFAULT_MAX_WORKERS = int(os.getenv('ALBION_MAX_WORKERS', '0'))
DEFAULT_MAX_FILES_PER_RUN = int(os.getenv('ALBION_MAX_FILES_PER_RUN', '0'))


def get_max_worker_count() -> int:
    """Return the worker count, or auto-size to the current CPU when left unset."""
    raw_value = os.getenv('ALBION_MAX_WORKERS', str(DEFAULT_MAX_WORKERS))
    try:
        worker_count = int(str(raw_value).strip())
    except ValueError:
        worker_count = DEFAULT_MAX_WORKERS

    if worker_count <= 0:
        return max(1, os.cpu_count() or 4)
    return max(1, worker_count)


def get_max_files_per_run() -> int:
    """Return 0 for unlimited processing; positive values cap a single run."""
    raw_value = os.getenv('ALBION_MAX_FILES_PER_RUN', str(DEFAULT_MAX_FILES_PER_RUN))
    try:
        max_files = int(str(raw_value).strip())
    except ValueError:
        max_files = DEFAULT_MAX_FILES_PER_RUN
    return max(0, max_files)


def get_run_start_time() -> float:
    """Return the full workflow start timestamp, including checkout/setup time."""
    raw = (
        os.getenv('ALBION_JOB_STARTED_AT')
        or os.getenv('GITHUB_RUN_STARTED_AT')
        or os.getenv('RUN_STARTED_AT')
    )
    if raw:
        try:
            value = raw.strip()
            if value.endswith('Z'):
                value = value[:-1] + '+00:00'
            dt = datetime.fromisoformat(value)
            return dt.timestamp()
        except ValueError:
            pass
    return time.time()


def get_max_runtime_seconds() -> int:
    """Read the total runtime budget from the environment; default is 5.5 hours."""
    try:
        max_runtime = int(os.getenv('ALBION_MAX_RUNTIME_SECONDS', str(DEFAULT_MAX_RUNTIME_SECONDS)))
    except ValueError:
        max_runtime = DEFAULT_MAX_RUNTIME_SECONDS

    if max_runtime <= 0:
        return DEFAULT_MAX_RUNTIME_SECONDS
    return max_runtime


def get_runtime_buffer_seconds() -> int:
    """Read the workflow safety buffer from the environment."""
    try:
        buffer_seconds = int(os.getenv('ALBION_RUNTIME_BUFFER_SECONDS', str(DEFAULT_RUNTIME_BUFFER_SECONDS)))
    except ValueError:
        buffer_seconds = DEFAULT_RUNTIME_BUFFER_SECONDS

    if buffer_seconds < 0:
        return 0
    return buffer_seconds


def should_stop_for_deadline(deadline_time: float, reserve_seconds: int | None = None) -> bool:
    """Return True when processing should stop before the next work item starts."""
    if reserve_seconds is None:
        reserve_seconds = get_runtime_buffer_seconds()
    reserve_seconds = max(0, reserve_seconds)
    return time.time() >= (deadline_time - reserve_seconds)


def atomic_write_json(path: Path, data: List[Dict[str, Any]]) -> None:
    """Write JSON to a temporary file and atomically replace the target."""
    payload = dump_json_bytes(data)
    tmp_path = path.parent / f'.{path.name}.tmp'
    with open(tmp_path, 'wb') as f:
        f.write(payload)
    os.replace(tmp_path, path)


def atomic_write_text(path: Path, content: str) -> None:
    """Write text to a temporary file and atomically replace the target."""
    tmp_path = path.parent / f'.{path.name}.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(tmp_path, path)


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


def download_file_with_retries(url: str, destination: Path, timeout_seconds: int = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS, max_retries: int = DEFAULT_DOWNLOAD_RETRIES) -> bool:
    """Download a file with retries, handling transient network cancels."""
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp, open(destination, 'wb') as out:
                shutil.copyfileobj(resp, out)
            return True
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ConnectionError) as e:
            if attempt == max_retries:
                print(f"[ERROR] Failed to download {url} after {max_retries} attempts: {e}")
                return False
            delay = min(5 * attempt, 30)
            print(f"[WARN] Download retry {attempt}/{max_retries} for {url} after {delay}s ({e})")
            time.sleep(delay)

    return False


def get_available_downloads(base_url: str) -> List[str]:
    """Scrape AODP database page, return only market_history_YYYY_MM.sql.gz filenames"""
    retries = DEFAULT_DOWNLOAD_RETRIES
    last_error = None

    for attempt in range(1, retries + 1):
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
            last_error = e
            if attempt == retries:
                print(f"[ERROR] Failed to scrape {base_url}: {e}")
                return []
            delay = min(5 * attempt, 30)
            print(f"[WARN] Retry {attempt}/{retries} for {base_url} after {delay}s ({e})")
            time.sleep(delay)

    print(f"[ERROR] Failed to scrape {base_url} after retries: {last_error}")
    return []


def setup_directories():
    """Create necessary directories for all servers"""
    for server_name in SERVERS_PRIORITY:
        paths = get_paths_for_server(server_name)
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)


def download_latest_exports(
    server_name: str,
    base_url: str,
    start_time: float = None,
    deadline_time: float = None,
) -> List[Path]:
    """Scrape AODP page, skip already extracted or processed files, download the rest to raw/."""
    if start_time is None:
        start_time = time.time()
    if deadline_time is None:
        deadline_time = start_time + get_max_runtime_seconds()

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
        # Use the workflow-start deadline and the configured safety buffer.
        if should_stop_for_deadline(deadline_time):
            elapsed = time.time() - start_time
            print(f"[INFO] Time limit approaching ({elapsed:.0f}s elapsed), stopping downloads")
            break

        url = base_url + filename
        local_path = paths['raw'] / filename

        try:
            print(f"[DOWNLOAD] {filename} from {server_name}...")
            if download_file_with_retries(url, local_path, timeout_seconds=DEFAULT_DOWNLOAD_TIMEOUT_SECONDS, max_retries=DEFAULT_DOWNLOAD_RETRIES):
                print(f"[SUCCESS] Downloaded {filename} to {server_name}/raw/")
                downloaded_files.append(local_path)
            else:
                print(f"[ERROR] Failed to download {filename} from {server_name} after retries")
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
    """Add file to processed list for a specific server without corrupting the checkpoint file."""
    try:
        processed_file = paths['output'] / '.processed.txt'
        processed = load_processed_files(paths)
        processed.add(filename)
        atomic_write_text(processed_file, ''.join(f'{name}\n' for name in sorted(processed)))
    except Exception as e:
        print(f"[ERROR] Failed to save processed file: {e}")


def restore_file_item_snapshots(paths: Dict[str, Path], item_snapshots: Dict[str, List[Dict[str, Any]]]) -> None:
    """Restore each item JSON file to its pre-file state after a deadline interruption."""
    for item_id, original_records in item_snapshots.items():
        output_path = paths['output'] / f'{item_id}.json'
        if original_records:
            atomic_write_json(output_path, original_records)
        elif output_path.exists():
            output_path.unlink()


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


def iter_sql_records(filepath: Path):
    """Yield market_history records without building the entire file in memory."""
    print(f"[DEBUG] Reading {filepath.name}...")
    try:
        opener = gzip.open if filepath.suffix == '.gz' else open
        with opener(filepath, 'rt', encoding='utf-8', errors='ignore') as f:
            buffer = ''
            in_insert = False
            found_records = 0

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
                                found_records += 1
                                yield tuple(values[:8])
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
                            found_records += 1
                            yield tuple(values[:8])

        print(f"[DEBUG] Extracted {found_records} records from {filepath.name}")
    except Exception as e:
        print(f"[ERROR] Error reading {filepath.name}: {e}")


def read_sql_file(filepath: Path) -> List[Tuple]:
    """Compatibility wrapper for callers expecting a list of records."""
    return list(iter_sql_records(filepath))


def dump_json_bytes(data: Any) -> bytes:
    """Dump JSON with the fastest available encoder while keeping a Python fallback."""
    if orjson is not None:
        return orjson.dumps(data, option=orjson.OPT_INDENT_2)
    return (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def get_max_json_file_bytes() -> int | None:
    """Return a file-size limit for individual JSON outputs, or None to disable splitting."""
    raw = os.getenv('ALBION_MAX_JSON_FILE_BYTES', '0')
    if not raw or raw.strip().lower() in {'', '0', 'none', 'off'}:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def load_json_file(path: Path) -> List[Dict[str, Any]]:
    """Load a JSON list safely from disk."""
    if not path.exists():
        return []
    try:
        with open(path, 'rb') as f:
            payload = orjson.loads(f.read()) if orjson is not None else json.load(f)
        if isinstance(payload, list):
            return payload
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return []


def write_json_file(path: Path, records: List[Dict[str, Any]]) -> None:
    """Write JSON records atomically using the fastest available encoder."""
    atomic_write_json(path, records)


def write_item_records(item_id: str, price_records: List[Dict[str, Any]], paths: Dict[str, Path]) -> None:
    """Append transformed records to the per-item JSON file, deduplicated by timestamp/city/quality."""
    base_output = paths['output'] / f'{item_id}.json'
    existing_in_file = load_json_file(base_output)
    file_keys = {(r['timestamp'], r['city'], r['quality']) for r in existing_in_file if isinstance(r, dict)}
    final_records = existing_in_file + [r for r in price_records if (r['timestamp'], r['city'], r['quality']) not in file_keys]
    atomic_write_json(base_output, final_records)


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
    max_files_per_run = get_max_files_per_run()
    if max_files_per_run > 0:
        sql_files = sql_files[:max_files_per_run]
        print(f"[INFO] Limiting run to the first {len(sql_files)} SQL file(s) for {server_name} because ALBION_MAX_FILES_PER_RUN is set.")
    else:
        print(f"[INFO] No per-run file cap configured for {server_name}; processing all available SQL files within the runtime deadline.")
    processed = load_processed_files(paths)
    processed_count = 0
    
    # Process multiple files until the workflow deadline is reached
    for sql_file in sql_files:
        if should_stop_for_deadline(deadline_time, reserve_seconds=0):
            print(f"[INFO] Processing deadline reached; stopping work and leaving {sql_file.name} unmarked as processed.")
            break

        if sql_file.name in processed:
            print(f"[SKIP] {sql_file.name} already processed for {server_name}")
            continue

        try:
            print(f"\n[PROCESS] Processing {sql_file.name} for {server_name}...")
            file_items: Dict[str, List[Dict]] = defaultdict(list)
            record_count = 0

            for rec in iter_sql_records(sql_file):
                record_count += 1
                if len(rec) >= 8:
                    item_id = rec[3]
                    transformed = transform_record(rec, server_name)
                    if transformed:
                        if should_stop_for_deadline(deadline_time, reserve_seconds=0):
                            print(f"[INFO] Deadline reached while reading {sql_file.name}; leaving current SQL file unmarked as processed.")
                            return processed_count
                        file_items[item_id].append(transformed)

                if record_count % 500000 == 0:
                    print(f"[DEBUG] Processed {record_count:,} records...")

            if record_count == 0:
                print(f"[WARN] No records found in {sql_file.name}")
                save_processed_file(sql_file.name, paths)
                processed_count += 1
                continue

            print(f"[DEBUG] Writing {len(file_items):,} items to JSON files for {server_name}...")
            worker_count = get_max_worker_count()
            worker_count = max(1, min(worker_count, len(file_items) if file_items else 1))

            item_snapshots = {
                item_id: load_json_file(paths['output'] / f'{item_id}.json')
                for item_id in file_items
            }

            parallel_buffer = max(0, min(1800, get_runtime_buffer_seconds()))
            use_parallel = len(file_items) > 1 and worker_count > 1 and not should_stop_for_deadline(deadline_time, reserve_seconds=parallel_buffer)

            if use_parallel:
                tasks = [(item_id, price_records, paths) for item_id, price_records in file_items.items()]
                try:
                    with ProcessPoolExecutor(max_workers=worker_count) as executor:
                        for idx, item_id in enumerate(executor.map(_parallel_write_item, tasks), start=1):
                            if idx % 1000 == 0:
                                print(f"[DEBUG] Written {idx:,}/{len(file_items):,} items...")
                            if should_stop_for_deadline(deadline_time, reserve_seconds=0):
                                print(f"[INFO] Deadline reached while writing {item_id} from {sql_file.name}; discarding this file's item writes.")
                                restore_file_item_snapshots(paths, item_snapshots)
                                return processed_count
                    print(f"[DEBUG] Parallel write complete for {server_name}")
                except Exception as exc:
                    print(f"[WARN] Parallel write failed for {server_name}: {exc}; falling back to single-process output")
                    for item_idx, (item_id, price_records) in enumerate(file_items.items()):
                        if item_idx % 1000 == 0 and item_idx > 0:
                            print(f"[DEBUG] Written {item_idx:,}/{len(file_items):,} items...")
                        if should_stop_for_deadline(deadline_time, reserve_seconds=0):
                            print(f"[INFO] Deadline reached while writing {item_id} from {sql_file.name}; discarding this file's item writes.")
                            restore_file_item_snapshots(paths, item_snapshots)
                            return processed_count
                        try:
                            write_item_records(item_id, price_records, paths)
                        except Exception as e:
                            print(f"[ERROR] Failed to write {item_id}: {e}")
            else:
                for item_idx, (item_id, price_records) in enumerate(file_items.items()):
                    if item_idx % 1000 == 0 and item_idx > 0:
                        print(f"[DEBUG] Written {item_idx:,}/{len(file_items):,} items...")

                    if should_stop_for_deadline(deadline_time, reserve_seconds=0):
                        print(f"[INFO] Deadline reached while writing {item_id} from {sql_file.name}; discarding this file's item writes.")
                        restore_file_item_snapshots(paths, item_snapshots)
                        return processed_count

                    try:
                        write_item_records(item_id, price_records, paths)
                    except Exception as e:
                        print(f"[ERROR] Failed to write {item_id}: {e}")

            save_processed_file(sql_file.name, paths)
            processed_count += 1
            print(f"[SUCCESS] Processed {sql_file.name} ({len(file_items):,} items, {record_count:,} records) for {server_name}")

        except Exception as e:
            print(f"[ERROR] Error processing {sql_file.name} for {server_name}: {e}")
            import traceback
            traceback.print_exc()

    return processed_count


def _parallel_write_item(task: Tuple[str, List[Dict[str, Any]], Dict[str, Path]]) -> str:
    """Process one item bucket in a worker process for parallel mode."""
    item_id, price_records, paths = task
    write_item_records(item_id, price_records, paths)
    return item_id


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


def resolve_target_servers() -> List[str]:
    """Return the server list for this run. A single server can be selected explicitly."""
    configured_target = (os.getenv('ALBION_TARGET_SERVER') or '').strip().lower()
    if configured_target in SERVERS_PRIORITY:
        print(f"[INFO] ALBION_TARGET_SERVER override: {configured_target}")
        return [configured_target]
    return list(SERVERS_PRIORITY)



def main():
    """
    Main CI/CD process: download and process as many files as possible within the
    configured runtime window. The workflow start timestamp is used so the
    processor counts the entire job, not just the Python step itself.
    """
    max_runtime_seconds = get_max_runtime_seconds()
    print("[INFO] Starting AODP CI processor...")
    print(f"[INFO] Project root: {PROJECT_ROOT}")
    print(f"[INFO] Time window: {max_runtime_seconds / 60:.0f} minutes ({max_runtime_seconds} seconds)")
    print(f"[INFO] Processing priority: {' -> '.join(SERVERS_PRIORITY)}")

    start_time = get_run_start_time()
    deadline_time = start_time + max_runtime_seconds

    print(f"[INFO] Workflow start time: {datetime.fromtimestamp(start_time)}")
    print(f"[INFO] Deadline: {datetime.fromtimestamp(deadline_time)}")

    setup_directories()

    target_servers = resolve_target_servers()

    # Phase 1: Determine which server is most behind
    print(f"\n{'='*60}")
    print(f"[PHASE 1] Checking which server needs the most work...")
    print(f"{'='*60}")

    if len(target_servers) == 1:
        most_behind = target_servers[0]
        print(f"[INFO] Running server-only mode for {most_behind}")
    else:
        most_behind = find_most_behind_server(SERVERS_PRIORITY)

    # Phase 2: Process that server's files within time window
    print(f"\n{'='*60}")
    print(f"[PHASE 2] Processing server: {most_behind}")
    print(f"{'='*60}")

    max_files_per_run = get_max_files_per_run()

    base_url = SERVERS[most_behind]
    paths = get_paths_for_server(most_behind)

    # Check if this server has unprocessed files
    available = get_available_downloads(base_url)
    if not available:
        print(f"[WARN] No market_history files available on {most_behind}")
    else:
        print(f"[INFO] Found {len(available)} total files on {most_behind}")
        
        # Download and process all available files until deadline.
        processed = load_processed_files(paths)
        unprocessed_files = [f for f in sorted(available) if f not in processed]
        
        if unprocessed_files:
            if max_files_per_run > 0:
                unprocessed_files = unprocessed_files[:max_files_per_run]
                print(f"[INFO] Limiting this run to {len(unprocessed_files)} file(s) because ALBION_MAX_FILES_PER_RUN is set")
            else:
                print(f"[INFO] No per-run file cap configured; downloading all {len(unprocessed_files)} unprocessed file(s) within the workflow deadline")
            print(f"[INFO] {len(unprocessed_files)} unprocessed file(s) selected for download")

            for filename in unprocessed_files:
                # Respect the workflow-start deadline and the configured safety buffer.
                if should_stop_for_deadline(deadline_time):
                    elapsed = time.time() - start_time
                    print(f"[INFO] Time limit approaching ({elapsed:.0f}s elapsed), stopping downloads")
                    break

                print(f"\n[DOWNLOAD] Downloading {filename}...")
                try:
                    url = base_url + filename
                    local_path = paths['raw'] / filename

                    if not download_file_with_retries(url, local_path, timeout_seconds=DEFAULT_DOWNLOAD_TIMEOUT_SECONDS, max_retries=DEFAULT_DOWNLOAD_RETRIES):
                        print(f"[ERROR] Failed to download {filename} after retries")
                        continue

                    print(f"[SUCCESS] Downloaded {filename}")
                    # Extract immediately after download
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
    
    if remaining > 1200 and len(target_servers) != 1:
        other_servers = [s for s in SERVERS_PRIORITY if s != most_behind]
        for server_name in other_servers:
            if should_stop_for_deadline(deadline_time):
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
                    if max_files_per_run > 0:
                        limited_files = unprocessed_files[:max_files_per_run]
                        print(f"[INFO] Limiting secondary server to {len(limited_files)} file(s) because ALBION_MAX_FILES_PER_RUN is set")
                    else:
                        limited_files = unprocessed_files
                        print(f"[INFO] No per-run file cap configured; processing all {len(limited_files)} secondary-server file(s) within the workflow deadline")
                    for filename in limited_files:
                        if should_stop_for_deadline(deadline_time):
                            print(f"[INFO] Time limit approaching, stopping")
                            break

                        try:
                            url = base_url + filename
                            local_path = paths['raw'] / filename

                            if not download_file_with_retries(url, local_path, timeout_seconds=DEFAULT_DOWNLOAD_TIMEOUT_SECONDS, max_retries=DEFAULT_DOWNLOAD_RETRIES):
                                print(f"[ERROR] Failed to process {filename} after retries")
                                continue

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
