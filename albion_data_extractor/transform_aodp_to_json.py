#!/usr/bin/env python3
"""
Transform AODP SQL market_history exports to price checker JSON format.
Fast append-only approach with in-memory caching.
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
from threading import Thread
import time

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

PATHS = {
    'history': Path(r'C:\Users\Luka\Flutter Projects\albioneconomy\lib\price_checker_and_history_parts\albion data dumps\extracted\history'),
    'output': Path(r'C:\Users\Luka\Flutter Projects\albioneconomy\lib\price_checker_and_history_parts\albion data dumps\formatted'),
    'items': Path(r'C:\Users\Luka\Flutter Projects\albioneconomy\lib\albion data dumps\ao-bin-dumps-master\formatted\items.json'),
}


def load_items_database() -> Dict[str, str]:
    """Load item IDs and their English names from items.json"""
    print("[DEBUG] Loading items database...")
    
    if not PATHS['items'].exists():
        print(f"[WARNING] items.json not found at {PATHS['items']}")
        return {}
    
    try:
        with open(PATHS['items'], 'r', encoding='utf-8') as f:
            items_data = json.load(f)
        
        # Build lookup: item_id -> english_name
        items_lookup = {}
        for item_id, item_info in items_data.items():
            localized_names = item_info.get('LocalizedNames', {})
            english_name = localized_names.get('EN-US', item_id)
            items_lookup[item_id] = english_name
        
        print(f"[DEBUG] Loaded {len(items_lookup)} items")
        return items_lookup
    
    except Exception as e:
        print(f"[ERROR] Failed to load items.json: {e}")
        return {}


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
        'quantity': int(city_id) if city_id else 0,
    }


class TransformerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("AODP → Price Checker JSON Transformer v2")
        self.root.geometry("900x750")
        self.root.resizable(True, True)
        
        # Path variables
        self.input_path = tk.StringVar(value=str(PATHS['history']))
        self.output_path = tk.StringVar(value=str(PATHS['output']))
        self.items_path = tk.StringVar(value=str(PATHS['items']))
        
        self.selected_files = []
        self.processing = False
        self.start_time = None
        self.current_file_idx = 0
        self.total_records = 0
        self.processed_files: Set[str] = set()
        self.skip_processed = tk.BooleanVar(value=True)
        self.items_lookup: Dict[str, str] = {}
        
        self.sql_files = []
        
        self.setup_ui()
    
    def setup_ui(self):
        """Build the UI"""
        # Title
        title = ttk.Label(self.root, text="AODP 6-Year Price History Transformer (Fast)", font=("Arial", 14, "bold"))
        title.pack(pady=10)
        
        # Path selection frame
        frame_paths = ttk.LabelFrame(self.root, text="Select Folders & Files", padding=10)
        frame_paths.pack(fill="x", padx=10, pady=5)
        
        # Input path
        ttk.Label(frame_paths, text="SQL Files Location:", font=("Arial", 9, "bold")).pack(anchor="w", pady=(0, 5))
        input_frame = ttk.Frame(frame_paths)
        input_frame.pack(fill="x", pady=(0, 8))
        ttk.Entry(input_frame, textvariable=self.input_path, state="readonly").pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(input_frame, text="Browse", command=self.browse_input).pack(side="left", padx=2)
        
        # Output path
        ttk.Label(frame_paths, text="Output Location:", font=("Arial", 9, "bold")).pack(anchor="w", pady=(0, 5))
        output_frame = ttk.Frame(frame_paths)
        output_frame.pack(fill="x", pady=(0, 8))
        ttk.Entry(output_frame, textvariable=self.output_path, state="readonly").pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(output_frame, text="Browse", command=self.browse_output).pack(side="left", padx=2)
        
        # Items path
        ttk.Label(frame_paths, text="Items Database (items.json):", font=("Arial", 9, "bold")).pack(anchor="w", pady=(0, 5))
        items_frame = ttk.Frame(frame_paths)
        items_frame.pack(fill="x")
        ttk.Entry(items_frame, textvariable=self.items_path, state="readonly").pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(items_frame, text="Browse", command=self.browse_items).pack(side="left", padx=2)
        
        # Options frame
        frame_options = ttk.LabelFrame(self.root, text="Options", padding=10)
        frame_options.pack(fill="x", padx=10, pady=5)
        
        check_skip = ttk.Checkbutton(
            frame_options,
            text="Skip already processed files",
            variable=self.skip_processed
        )
        check_skip.pack(anchor="w", pady=5)
        
        # File selection frame
        frame_files = ttk.LabelFrame(self.root, text="Select Files to Extract", padding=10)
        frame_files.pack(fill="both", expand=True, padx=10, pady=5)
        
        # Buttons
        btn_frame = ttk.Frame(frame_files)
        btn_frame.pack(fill="x", pady=(0, 10))
        
        ttk.Button(btn_frame, text="Select All", command=self.select_all).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Clear", command=self.clear_selection).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Invert", command=self.invert_selection).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Unprocessed Only", command=self.select_unprocessed).pack(side="left", padx=5)
        
        self.label_file_count = ttk.Label(btn_frame, text="", font=("Arial", 9), foreground="blue")
        self.label_file_count.pack(side="right", padx=10)
        
        # Listbox
        scroll = ttk.Scrollbar(frame_files)
        scroll.pack(side="right", fill="y")
        
        self.listbox = tk.Listbox(frame_files, yscrollcommand=scroll.set, selectmode="multiple", height=10)
        self.listbox.pack(fill="both", expand=True)
        scroll.config(command=self.listbox.yview)
        
        self.reload_files()
        
        # Progress frame
        frame_progress = ttk.LabelFrame(self.root, text="Progress", padding=10)
        frame_progress.pack(fill="x", padx=10, pady=5)
        
        self.label_file = ttk.Label(frame_progress, text="Ready", font=("Arial", 10))
        self.label_file.pack(anchor="w", pady=(0, 5))
        
        self.label_stats = ttk.Label(frame_progress, text="", font=("Arial", 9))
        self.label_stats.pack(anchor="w")
        
        # Buttons
        frame_buttons = ttk.Frame(self.root)
        frame_buttons.pack(fill="x", padx=10, pady=10)
        
        self.btn_start = ttk.Button(frame_buttons, text="▶ START EXTRACTION", command=self.start_extraction)
        self.btn_start.pack(side="left", padx=5, ipady=10)
        
        self.btn_cancel = ttk.Button(frame_buttons, text="⏸ CANCEL", command=self.cancel_extraction, state="disabled")
        self.btn_cancel.pack(side="left", padx=5, ipady=10)
        
        ttk.Button(frame_buttons, text="📁 Open Output", command=self.open_output).pack(side="right", padx=5, ipady=10)
    
    def browse_input(self):
        folder = filedialog.askdirectory(title="Select SQL files folder", initialdir=self.input_path.get())
        if folder:
            self.input_path.set(folder)
            self.reload_files()
    
    def browse_output(self):
        folder = filedialog.askdirectory(title="Select output folder", initialdir=self.output_path.get())
        if folder:
            self.output_path.set(folder)
            self.reload_files()
    
    def browse_items(self):
        file = filedialog.askopenfilename(
            title="Select items.json",
            initialdir=str(Path(self.items_path.get()).parent),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if file:
            self.items_path.set(file)
    
    def reload_files(self):
        """Reload SQL files from selected input path"""
        input_dir = Path(self.input_path.get())
        output_dir = Path(self.output_path.get())
        
        if not input_dir.exists():
            messagebox.showerror("Error", f"Input folder not found:\n{input_dir}")
            return
        
        PATHS['history'] = input_dir
        PATHS['output'] = output_dir
        
        self.processed_files = load_processed_files(output_dir)
        self.sql_files = sorted(input_dir.glob('market_history_*.sql'))
        
        if not self.sql_files:
            messagebox.showwarning("No Files", f"No 'market_history_*.sql' files found in:\n{input_dir}")
            self.listbox.delete(0, "end")
            self.label_file_count.config(text="No files found")
            return
        
        self.listbox.delete(0, "end")
        for sql_file in self.sql_files:
            is_processed = sql_file.stem in self.processed_files
            prefix = "✓ " if is_processed else "  "
            self.listbox.insert("end", prefix + sql_file.stem)
        
        if self.sql_files:
            self.listbox.selection_set(0, "end")
        
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
        self.clear_selection()
        for i, sql_file in enumerate(self.sql_files):
            if sql_file.stem not in self.processed_files:
                self.listbox.selection_set(i)
    
    def get_selected_files(self):
        indices = self.listbox.curselection()
        selected = [self.sql_files[i] for i in indices]
        
        if self.skip_processed.get():
            selected = [f for f in selected if f.stem not in self.processed_files]
        
        return selected
    
    def start_extraction(self):
        selected = self.get_selected_files()
        if not selected:
            messagebox.showwarning("No Selection", "Please select at least one file to extract.")
            return
        
        # Load items database
        self.items_lookup = load_items_database()
        if not self.items_lookup:
            messagebox.showwarning("Warning", "items.json not loaded. Will use item IDs as names.")
        
        # Validate output path
        try:
            output_dir = Path(self.output_path.get())
            output_dir.mkdir(parents=True, exist_ok=True)
            
            test_file = output_dir / '.write_test'
            test_file.touch()
            test_file.unlink()
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
        
        thread = Thread(target=self.process_files, daemon=True)
        thread.start()
    
    def cancel_extraction(self):
        self.processing = False
        self.btn_start.config(state="normal")
        self.btn_cancel.config(state="disabled")
        self.listbox.config(state="normal")
        messagebox.showinfo("Cancelled", "Extraction cancelled.")
    
    def process_files(self):
        """Process selected files - read to RAM, then append to JSON files"""
        try:
            output_dir = Path(self.output_path.get())
            total_items_written = 0
            
            for idx, sql_file in enumerate(self.selected_files):
                if not self.processing:
                    break
                
                print(f"\n[DEBUG] === PROCESSING FILE {idx+1}/{len(self.selected_files)} ===")
                self.current_file_idx = idx
                
                self.root.after(0, self.label_file.config, {"text": f"Reading: {sql_file.stem}"})
                
                # Read SQL file to RAM
                recs = read_sql_file(sql_file)
                
                # Group by item_id in RAM
                print(f"[DEBUG] Grouping {len(recs)} records by item...")
                file_items: Dict[str, List[Dict]] = defaultdict(list)
                
                for i, rec in enumerate(recs):
                    if i % 500000 == 0 and i > 0:
                        print(f"[DEBUG] Processed {i}/{len(recs)} records...")
                    
                    if len(rec) >= 8:
                        item_id = rec[3]
                        transformed = transform_record(rec)
                        file_items[item_id].append(transformed)
                        self.total_records += 1
                
                print(f"[DEBUG] Grouped into {len(file_items)} items, writing to disk...")
                self.root.after(0, self.label_file.config, {"text": f"Writing: {len(file_items)} items"})
                
                # Write items to JSON files (append-only, no reading)
                for item_idx, (item_id, records) in enumerate(file_items.items()):
                    if not self.processing:
                        break
                    
                    if item_idx % 500 == 0 and item_idx > 0:
                        print(f"[DEBUG] Written {item_idx}/{len(file_items)} items...")
                    
                    output_file = output_dir / f"{item_id}.json"
                    
                    # Get item name
                    item_name = self.items_lookup.get(item_id, item_id)
                    
                    try:
                        if output_file.exists():
                            # File exists: append to priceHistory using binary mode
                            with open(output_file, 'r+b') as f:
                                # Seek to 2 bytes before end (before `]}`)
                                f.seek(-2, 2)
                                # Write comma and new records
                                f.write(b',\n')
                                for record in records:
                                    record_json = json.dumps(record)
                                    f.write(b'    ' + record_json.encode('utf-8') + b',\n')
                                # Remove trailing comma and close properly
                                f.seek(-2, 1)  # Go back 2 bytes (comma + newline)
                                f.write(b'\n  ]\n}')
                                f.truncate()  # Ensure file ends here
                        else:
                            # File doesn't exist: create new
                            data = {
                                'itemId': item_id,
                                'itemName': item_name,
                                'priceHistory': records
                            }
                            with open(output_file, 'w', encoding='utf-8') as f:
                                json.dump(data, f, indent=2)
                        
                        total_items_written += 1
                    
                    except Exception as e:
                        print(f"[DEBUG] Error writing {item_id}: {e}")
                
                # Clear memory
                file_items.clear()
                
                # Mark as processed
                save_processed_file(sql_file.stem, output_dir)
                self.processed_files.add(sql_file.stem)
                
                print(f"[DEBUG] File {sql_file.stem} complete! Wrote {len(file_items)} items")
                
                elapsed = time.time() - self.start_time
                self.root.after(0, self.update_status, idx, len(self.selected_files), elapsed)
            
            if not self.processing:
                return
            
            elapsed = time.time() - self.start_time
            hours, remainder = divmod(int(elapsed), 3600)
            minutes, seconds = divmod(remainder, 60)
            time_str = f"{hours}h {minutes}m {seconds}s"
            
            self.root.after(0, messagebox.showinfo, "Success",
                f"Extraction complete!\n\nRecords: {self.total_records:,}\nItems: {total_items_written:,}\nTime: {time_str}")
            self.root.after(0, self.reset_ui)
        
        except Exception as e:
            print(f"[DEBUG] EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            self.root.after(0, messagebox.showerror, "Error", f"Processing failed: {e}")
            self.root.after(0, self.reset_ui)
    
    def update_status(self, current, total, elapsed):
        percent = int((current / total) * 100)
        per_file = elapsed / (current + 1)
        remaining = int(per_file * (total - current - 1))
        
        hours, remainder = divmod(remaining, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if hours > 0:
            time_str = f"{hours}h {minutes}m"
        else:
            time_str = f"{minutes}m {seconds}s"
        
        self.label_stats.config(text=f"{percent}% | Remaining: {time_str} | Total: {self.total_records:,} records")
    
    def reset_ui(self):
        self.processing = False
        self.btn_start.config(state="normal")
        self.btn_cancel.config(state="disabled")
        self.listbox.config(state="normal")
    
    def open_output(self):
        output_path = Path(self.output_path.get())
        if not output_path.exists():
            messagebox.showwarning("Not Found", f"Output folder doesn't exist:\n{output_path}")
            return
        
        if os.name == 'nt':
            os.startfile(output_path)
        else:
            os.system(f"open '{output_path}'" if os.name == 'posix' else f"xdg-open '{output_path}'")


def main_gui():
    root = tk.Tk()
    app = TransformerGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main_gui()
