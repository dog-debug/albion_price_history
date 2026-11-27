#!/usr/bin/env python3
"""
Transform AODP SQL market_history exports to price checker JSON format.
6+ years of Albion market data from 2020-2026.
GUI with file picker, progress tracking, CPU limiting, and processed file tracking.
"""

import os
import re
import json
import gzip
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Tuple, Set
from collections import defaultdict
from threading import Thread, Semaphore
import time
from concurrent.futures import ThreadPoolExecutor

# Location ID to city name mapping
LOCATION_ID_TO_CITY = {
    3003: 'bridgewatch',    # Bridgewatch
    3005: 'caerleon',       # Caerleon  
    3008: 'lymhurst',       # Lymhurst
    3002: 'thetford',       # Thetford
    3004: 'forsterling',    # Fort Sterling
    3006: 'brecilien',      # Brecilien
    4002: 'blackmarket',    # Black Market
    1002: 'martlock',       # Martlock
    7: 'blackmarket',       # Black Market (alternate code)
}

# Server codes
SOURCE_TO_SERVER = {
    6: 'west.albion-online-data.com',
    7: 'europe.albion-online-data.com', 
    8: 'east.albion-online-data.com',
}

PATHS = {
    'history': Path(r'C:\Users\Luka\Flutter Projects\albioneconomy\lib\price_checker_and_history_parts\albion data dumps\extracted\history'),
    'output': Path(r'C:\Users\Luka\Flutter Projects\albioneconomy\lib\price_checker_and_history_parts\albion data dumps\formatted'),
}

PROCESSED_FILE = PATHS['output'] / '.processed.txt'


def load_processed_files(output_dir: Path = None) -> Set[str]:
    """Load list of already-processed SQL files"""
    if output_dir is None:
        output_dir = PATHS['output']
    
    processed_file = output_dir / '.processed.txt'
    
    if not processed_file.exists():
        return set()
    
    try:
        with open(processed_file, 'r') as f:
            return set(line.strip() for line in f if line.strip())
    except:
        return set()


def save_processed_file(filename: str, output_dir: Path = None):
    """Add file to processed list"""
    if output_dir is None:
        output_dir = PATHS['output']
    
    processed_file = output_dir / '.processed.txt'
    
    try:
        processed = load_processed_files(output_dir)
        processed.add(filename)
        
        with open(processed_file, 'w') as f:
            for name in sorted(processed):
                f.write(name + '\n')
    except:
        pass


def format_price(price: int) -> str:
    """Format price as display string (1.2K, 500, 1.5M)"""
    if price >= 1_000_000:
        return f"{price / 1_000_000:.1f}M".rstrip('0').rstrip('.')
    elif price >= 1_000:
        return f"{price / 1_000:.1f}K".rstrip('0').rstrip('.')
    return str(price)


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
        print(f"\n[DEBUG] Reading file: {filepath.name}")
        if filepath.suffix == '.gz':
            print(f"[DEBUG] File is gzipped, decompressing...")
            with gzip.open(filepath, 'rt', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        else:
            file_size_mb = filepath.stat().st_size / (1024 * 1024)
            print(f"[DEBUG] File size: {file_size_mb:.1f} MB, reading...")
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        
        print(f"[DEBUG] Content loaded ({len(content)} chars), parsing SQL...")
        
        # Find all INSERT VALUES (...), (...), ... patterns
        insert_pattern = r'INSERT INTO\s+`?market_history`?\s+VALUES\s+((?:\([^()]+\)(?:,\s*)?)+)'
        
        insert_count = 0
        for insert_match in re.finditer(insert_pattern, content, re.IGNORECASE):
            insert_count += 1
            values_text = insert_match.group(1)
            
            # Split individual tuples
            for tuple_match in re.finditer(r'\(([^()]+)\)', values_text):
                tuple_str = '(' + tuple_match.group(1) + ')'
                values = parse_sql_insert(tuple_str)
                
                if values and len(values) >= 8:
                    try:
                        records.append(tuple(values[:8]))
                    except:
                        pass
        
        print(f"[DEBUG] Found {insert_count} INSERT statements, extracted {len(records)} records")
        return records
    
    except Exception as e:
        print(f"[DEBUG] ERROR reading {filepath.name}: {e}")
        return records


def transform_record(rec: Tuple) -> Dict[str, Any]:
    """
    Transform SQL record to price checker format.
    SQL: (id, city_id, price, item_id, location_id, quality, timestamp, source)
    """
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
        'sellPriceFormatted': format_price(price_val),
        'buyPriceFormatted': format_price(price_val),
        'quantity': int(city_id) if city_id else 0,  # Transaction quantity
    }


def build_item_file(item_id: str, records: List[Dict]) -> Dict:
    """Build item JSON structure"""
    # Sort by timestamp descending (newest first)
    sorted_recs = sorted(records, key=lambda r: r['timestamp'], reverse=True)
    
    return {
        'itemId': item_id,
        'priceHistory': sorted_recs,
        'latest': {
            rec['city']: {
                rec['quality']: {
                    'timestamp': rec['timestamp'],
                    'sellPrice': rec['sellPrice'],
                    'buyPrice': rec['buyPrice'],
                    'sellPriceFormatted': rec['sellPriceFormatted'],
                    'buyPriceFormatted': rec['buyPriceFormatted'],
                }
                for rec in sorted_recs
                if rec['city'] != 'unknown'
            }
            for rec in sorted_recs
        }
    }


class TransformerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("AODP → Price Checker JSON Transformer")
        self.root.geometry("900x950")
        self.root.resizable(True, True)
        
        # Path variables
        self.input_path = tk.StringVar(value=str(PATHS['history']))
        self.output_path = tk.StringVar(value=str(PATHS['output']))
        
        self.selected_files = []
        self.processing = False
        self.start_time = None
        self.current_file_idx = 0
        self.total_records = 0
        self.processed_files: Set[str] = set()
        self.skip_processed = tk.BooleanVar(value=True)
        self.cpu_workers = tk.IntVar(value=2)
        
        self.sql_files = []
        
        self.setup_ui()
    
    def setup_ui(self):
        """Build the UI"""
        # Title
        title = ttk.Label(self.root, text="AODP 6-Year Price History Transformer", font=("Arial", 14, "bold"))
        title.pack(pady=10)
        
        # Path selection frame
        frame_paths = ttk.LabelFrame(self.root, text="Select Folders", padding=10)
        frame_paths.pack(fill="x", padx=10, pady=5)
        
        # Input path
        ttk.Label(frame_paths, text="SQL Files Location:", font=("Arial", 9, "bold")).pack(anchor="w", pady=(0, 5))
        input_frame = ttk.Frame(frame_paths)
        input_frame.pack(fill="x", pady=(0, 10))
        
        ttk.Entry(input_frame, textvariable=self.input_path, state="readonly").pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(input_frame, text="Browse", command=self.browse_input).pack(side="left", padx=2)
        ttk.Button(input_frame, text="Reload", command=self.reload_files).pack(side="left", padx=2)
        
        # Output path
        ttk.Label(frame_paths, text="Output Location:", font=("Arial", 9, "bold")).pack(anchor="w", pady=(0, 5))
        output_frame = ttk.Frame(frame_paths)
        output_frame.pack(fill="x")
        
        ttk.Entry(output_frame, textvariable=self.output_path, state="readonly").pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(output_frame, text="Browse", command=self.browse_output).pack(side="left", padx=2)
        frame_options = ttk.LabelFrame(self.root, text="Options", padding=10)
        frame_options.pack(fill="x", padx=10, pady=5)
        
        # Skip processed checkbox
        check_skip = ttk.Checkbutton(
            frame_options, 
            text="Skip already processed files", 
            variable=self.skip_processed
        )
        check_skip.pack(anchor="w", pady=5)
        
        # CPU limit frame
        cpu_frame = ttk.Frame(frame_options)
        cpu_frame.pack(anchor="w", pady=5)
        
        ttk.Label(cpu_frame, text="CPU Workers (1-8):").pack(side="left", padx=5)
        spinbox = ttk.Spinbox(
            cpu_frame,
            from_=1,
            to=8,
            textvariable=self.cpu_workers,
            width=5
        )
        spinbox.pack(side="left")
        ttk.Label(cpu_frame, text="(fewer = less CPU usage)", font=("Arial", 8)).pack(side="left", padx=10)
        
        # File selection frame
        frame_files = ttk.LabelFrame(self.root, text="Select Files to Extract", padding=10)
        frame_files.pack(fill="both", expand=True, padx=10, pady=5)
        
        # Buttons
        btn_frame = ttk.Frame(frame_files)
        btn_frame.pack(fill="x", pady=(0, 10))
        
        ttk.Button(btn_frame, text="Select All", command=self.select_all).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Clear Selection", command=self.clear_selection).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Invert Selection", command=self.invert_selection).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Unprocessed Only", command=self.select_unprocessed).pack(side="left", padx=5)
        
        # File count label
        self.label_file_count = ttk.Label(btn_frame, text="", font=("Arial", 9), foreground="blue")
        self.label_file_count.pack(side="right", padx=10)
        
        # Listbox with scrollbar
        scroll = ttk.Scrollbar(frame_files)
        scroll.pack(side="right", fill="y")
        
        self.listbox = tk.Listbox(frame_files, yscrollcommand=scroll.set, selectmode="multiple", height=12)
        self.listbox.pack(fill="both", expand=True)
        scroll.config(command=self.listbox.yview)
        
        # Load initial files
        self.reload_files()
        
        # Progress frame
        frame_progress = ttk.LabelFrame(self.root, text="Progress", padding=10)
        frame_progress.pack(fill="both", padx=10, pady=5)
        
        # File label
        self.label_file = ttk.Label(frame_progress, text="Ready to process", font=("Arial", 10))
        self.label_file.pack(anchor="w", pady=(0, 5))
        
        # Progress bar
        self.progress_bar = ttk.Progressbar(frame_progress, length=400, mode="determinate")
        self.progress_bar.pack(fill="x", pady=5)
        
        # Stats frame
        frame_stats = ttk.Frame(frame_progress)
        frame_stats.pack(fill="x", pady=5)
        
        self.label_percent = ttk.Label(frame_stats, text="0%", font=("Arial", 9))
        self.label_percent.pack(side="left", padx=10)
        
        self.label_time = ttk.Label(frame_stats, text="--:--", font=("Arial", 9))
        self.label_time.pack(side="right", padx=10)
        
        # Recent files frame
        frame_recent = ttk.LabelFrame(self.root, text="Recently Extracted", padding=10)
        frame_recent.pack(fill="both", expand=True, padx=10, pady=5)
        
        scroll_recent = ttk.Scrollbar(frame_recent)
        scroll_recent.pack(side="right", fill="y")
        
        self.listbox_recent = tk.Listbox(frame_recent, yscrollcommand=scroll_recent.set, height=6)
        self.listbox_recent.pack(fill="both", expand=True)
        scroll_recent.config(command=self.listbox_recent.yview)
        
        # Stats
        self.label_stats = ttk.Label(self.root, text="", font=("Arial", 9))
        self.label_stats.pack(pady=5)
        
        # Buttons
        frame_buttons = ttk.Frame(self.root)
        frame_buttons.pack(fill="both", padx=10, pady=15)
        
        # Big START button
        style = ttk.Style()
        style.configure('Start.TButton', font=("Arial", 12, "bold"))
        
        self.btn_start = ttk.Button(frame_buttons, text="▶ START EXTRACTION", command=self.start_extraction, style='Start.TButton')
        self.btn_start.pack(side="left", padx=5, pady=10, ipady=10)
        
        self.btn_cancel = ttk.Button(frame_buttons, text="⏸ CANCEL", command=self.cancel_extraction, state="disabled")
        self.btn_cancel.pack(side="left", padx=5, pady=10, ipady=10)
        
        ttk.Button(frame_buttons, text="📁 Open Output Folder", command=self.open_output).pack(side="right", padx=5, pady=10, ipady=10)
    
    def select_all(self):
        self.listbox.selection_set(0, "end")
    
    def clear_selection(self):
        self.listbox.selection_clear(0, "end")
    
    def invert_selection(self):
        for i in range(len(self.sql_files)):
            if self.listbox.selection_includes(i):
                self.listbox.selection_clear(i)
            else:
                self.listbox.selection_set(i)
    
    def select_unprocessed(self):
        """Select only files that haven't been processed yet"""
        self.clear_selection()
        for i, sql_file in enumerate(self.sql_files):
            if sql_file.stem not in self.processed_files:
                self.listbox.selection_set(i)
    
    def browse_input(self):
        """Browse for input SQL files folder"""
        folder = filedialog.askdirectory(
            title="Select folder with SQL files",
            initialdir=self.input_path.get()
        )
        if folder:
            self.input_path.set(folder)
            self.reload_files()
    
    def browse_output(self):
        """Browse for output JSON files folder"""
        folder = filedialog.askdirectory(
            title="Select output folder for JSON files",
            initialdir=self.output_path.get()
        )
        if folder:
            self.output_path.set(folder)
            self.reload_files()
    
    def reload_files(self):
        """Reload SQL files from selected input path"""
        input_dir = Path(self.input_path.get())
        output_dir = Path(self.output_path.get())
        
        if not input_dir.exists():
            messagebox.showerror("Error", f"Input folder not found:\n{input_dir}")
            return
        
        # Update PATHS
        PATHS['history'] = input_dir
        PATHS['output'] = output_dir
        
        # Reload processed files
        self.processed_files = load_processed_files(output_dir)
        
        # Load SQL files
        self.sql_files = sorted(input_dir.glob('market_history_*.sql'))
        
        if not self.sql_files:
            messagebox.showwarning("No Files", f"No 'market_history_*.sql' files found in:\n{input_dir}")
            self.listbox.delete(0, "end")
            self.label_file_count.config(text="No files found")
            return
        
        # Update listbox
        self.listbox.delete(0, "end")
        for sql_file in self.sql_files:
            is_processed = sql_file.stem in self.processed_files
            prefix = "✓ " if is_processed else "  "
            self.listbox.insert("end", prefix + sql_file.stem)
        
        # Select all by default
        if self.sql_files:
            self.listbox.selection_set(0, "end")
        
        # Update file count label
        processed_count = len(self.processed_files)
        total_count = len(self.sql_files)
        remaining = total_count - processed_count
        
        if processed_count > 0:
            self.label_file_count.config(
                text=f"✓ {processed_count}/{total_count} done ({remaining} left)",
                foreground="green"
            )
        else:
            self.label_file_count.config(text=f"{total_count} files", foreground="blue")
    
    def get_selected_files(self):
        indices = self.listbox.curselection()
        selected = [self.sql_files[i] for i in indices]
        
        # Filter out processed files if checkbox is enabled
        if self.skip_processed.get():
            selected = [f for f in selected if f.stem not in self.processed_files]
        
        return selected
    
    def start_extraction(self):
        selected = self.get_selected_files()
        if not selected:
            messagebox.showwarning("No Selection", "Please select at least one file to extract.")
            return
        
        # Validate and create output path FIRST (on main thread, not background)
        try:
            output_dir = Path(self.output_path.get())
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # Test write access
            test_file = output_dir / '.write_test'
            test_file.touch()
            test_file.unlink()
        except PermissionError:
            messagebox.showerror("Error", f"No permission to write to:\n{self.output_path.get()}")
            return
        except Exception as e:
            messagebox.showerror("Error", f"Cannot create output folder:\n{e}")
            return
        
        self.selected_files = selected
        self.processing = True
        self.start_time = time.time()
        self.current_file_idx = 0
        self.total_records = 0
        
        self.btn_start.config(state="disabled")
        self.btn_cancel.config(state="normal")
        self.listbox.config(state="disabled")
        
        # Start processing in background thread
        thread = Thread(target=self.process_files, daemon=True)
        thread.start()
    
    def cancel_extraction(self):
        self.processing = False
        self.btn_start.config(state="normal")
        self.btn_cancel.config(state="disabled")
        self.listbox.config(state="normal")
        messagebox.showinfo("Cancelled", "Extraction cancelled.")
    
    def process_files(self):
        """Process selected files one at a time, writing to disk immediately (low memory)"""
        try:
            # Get paths from GUI (already validated in start_extraction)
            output_dir = Path(self.output_path.get())
            num_workers = self.cpu_workers.get()
            total_items_written = 0
            
            # Cache of already-read JSON files (keep only what's needed)
            file_cache: Dict[str, List[Dict]] = {}
            
            for idx, sql_file in enumerate(self.selected_files):
                if not self.processing:
                    break
                
                print(f"\n[DEBUG] === PROCESSING FILE {idx+1}/{len(self.selected_files)} ===")
                self.current_file_idx = idx
                self.update_progress_display(sql_file.stem, idx, len(self.selected_files))
                
                # Read this file's records
                print(f"[DEBUG] Reading SQL file...")
                recs = read_sql_file(sql_file)
                print(f"[DEBUG] Read {len(recs)} records, transforming...")
                
                # Process records for this file only
                file_items: Dict[str, List[Dict]] = defaultdict(list)
                
                for i, rec in enumerate(recs):
                    if i % 100000 == 0 and i > 0:
                        print(f"[DEBUG] Transforming record {i}/{len(recs)}...")
                    
                    if len(rec) >= 8:
                        item_id = rec[3]
                        transformed = transform_record(rec)
                        file_items[item_id].append(transformed)
                        self.total_records += 1
                
                print(f"[DEBUG] Transformation done, {len(file_items)} unique items to write")
                print(f"[DEBUG] Starting direct writes with cache...")
                
                # Write JSON files directly (use cache to avoid re-reading)
                written_count = 0
                for item_id in file_items.keys():
                    if not self.processing:
                        break
                    
                    new_records = file_items[item_id]
                    
                    # Get existing records from cache or disk
                    if item_id in file_cache:
                        existing_history = file_cache[item_id]
                    else:
                        output_file = output_dir / f"{item_id}.json"
                        if output_file.exists():
                            try:
                                with open(output_file, 'r', encoding='utf-8') as f:
                                    existing_data = json.load(f)
                                    existing_history = existing_data.get('priceHistory', [])
                                    file_cache[item_id] = existing_history  # Cache it
                            except:
                                existing_history = []
                                file_cache[item_id] = []
                        else:
                            existing_history = []
                            file_cache[item_id] = []
                    
                    # Merge and write
                    all_records = existing_history + new_records
                    item_data = build_item_file(item_id, all_records)
                    output_file = output_dir / f"{item_id}.json"
                    
                    try:
                        with open(output_file, 'w', encoding='utf-8') as f:
                            json.dump(item_data, f, indent=2)
                        
                        # Update cache with new data
                        file_cache[item_id] = item_data.get('priceHistory', [])
                        written_count += 1
                        total_items_written += 1
                        
                        if written_count % 500 == 0:
                            print(f"[DEBUG] Written {written_count}/{len(file_items)}...")
                    except Exception as e:
                        print(f"[DEBUG] Error writing {item_id}: {e}")
                
                print(f"[DEBUG] Wrote {written_count} items")
                
                # Clear file data from memory
                file_items.clear()
                
                # Mark file as processed
                print(f"[DEBUG] Saving processed file marker...")
                save_processed_file(sql_file.stem, output_dir)
                self.processed_files.add(sql_file.stem)
                
                # Update recently extracted
                self.root.after(0, self.add_recent_file, sql_file.stem, len(recs))
                print(f"[DEBUG] File {sql_file.stem} complete!\n")
            
            if not self.processing:
                return
            
            # Complete
            elapsed = time.time() - self.start_time
            self.root.after(0, self.on_complete, total_items_written, elapsed)
        
        except Exception as e:
            print(f"[DEBUG] EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            self.root.after(0, lambda: messagebox.showerror("Error", f"Processing failed: {e}"))
            self.root.after(0, self.reset_ui)
    
    def _merge_and_write_json(self, item_id: str, new_records: List[Dict], output_dir: Path) -> bool:
        """Merge new records with existing JSON file and write back (called from thread pool)"""
        try:
            output_file = output_dir / f"{item_id}.json"
            
            # Load existing data if file exists
            if output_file.exists():
                with open(output_file, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                    existing_history = existing_data.get('priceHistory', [])
            else:
                existing_history = []
            
            # Combine old and new records
            all_records = existing_history + new_records
            
            # Build final item structure
            item_data = build_item_file(item_id, all_records)
            
            # Write back to file
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(item_data, f, indent=2)
            
            return True
        except:
            return False
    
    def update_progress_display(self, filename, current, total):
        """Update progress display (called from background thread)"""
        def update():
            percent = int((current / total) * 100)
            self.progress_bar['value'] = percent
            self.label_percent.config(text=f"{percent}%")
            self.label_file.config(text=f"Processing: {filename}")
            
            # Calculate ETA
            if current > 0:
                elapsed = time.time() - self.start_time
                per_file = elapsed / current
                remaining_files = total - current - 1
                eta_seconds = int(per_file * remaining_files)
                
                hours, remainder = divmod(eta_seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                
                if hours > 0:
                    time_str = f"{hours}h {minutes}m"
                else:
                    time_str = f"{minutes}m {seconds}s"
                
                self.label_time.config(text=time_str)
        
        self.root.after(0, update)
    
    def add_recent_file(self, filename, record_count):
        """Add file to recent list"""
        self.listbox_recent.insert(0, f"{filename}: {record_count:,} records")
        
        # Keep only last 10
        if self.listbox_recent.size() > 10:
            self.listbox_recent.delete(10, "end")
        
        # Update stats
        total_files = len(self.selected_files)
        self.label_stats.config(
            text=f"Total Records: {self.total_records:,} | Selected Files: {total_files}"
        )
    
    def on_complete(self, written, elapsed):
        """Called when processing complete"""
        hours, remainder = divmod(int(elapsed), 3600)
        minutes, seconds = divmod(remainder, 60)
        time_str = f"{hours}h {minutes}m {seconds}s"
        
        self.label_file.config(text=f"✓ Complete! Written {written:,} JSON files in {time_str}")
        self.progress_bar['value'] = 100
        self.label_percent.config(text="100%")
        
        self.reset_ui()
        
        messagebox.showinfo(
            "Success",
            f"Extraction complete!\n\n"
            f"Records processed: {self.total_records:,}\n"
            f"Files written: {written:,}\n"
            f"Time elapsed: {time_str}\n\n"
            f"Output: {PATHS['output']}"
        )
    
    def reset_ui(self):
        """Reset UI after processing"""
        self.processing = False
        self.btn_start.config(state="normal")
        self.btn_cancel.config(state="disabled")
        self.listbox.config(state="normal")
    
    def open_output(self):
        """Open output folder in explorer"""
        import subprocess
        import platform
        
        output_path = Path(self.output_path.get())
        
        if not output_path.exists():
            messagebox.showwarning("Folder Not Found", f"Output folder doesn't exist yet:\n{output_path}")
            return
        
        if platform.system() == "Windows":
            os.startfile(output_path)
        elif platform.system() == "Darwin":
            os.system(f"open '{output_path}'")
        else:
            os.system(f"xdg-open '{output_path}'")


def main_gui():
    root = tk.Tk()
    app = TransformerGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main_gui()
