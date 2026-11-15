# Albion Price History

Python tool for processing and transforming Albion Online Data Project (AODP) SQL market history exports into JSON format for price checking.

Handles 6+ years of Albion market data (2020-2026) with support for multiple cities and servers.

## Features

- Transform AODP SQL market_history exports to JSON
- Multi-city support (Bridgewatch, Caerleon, Lymhurst, Thetford, Fort Sterling, Brecilien, Martlock, Black Market)
- Multi-server support (West, Europe, East)
- Fast append-only JSON writes (memory efficient)
- Support for gzipped and uncompressed SQL files
- Processed file tracking to avoid re-processing
- **Automated monthly updates via GitHub Actions**

## Output Format

Data is organized by server, with each server having its own complete folder structure:

**File Structure:**
```
albion_data_dumps/
├── europe/
│   ├── raw/              (temporary, ignored in git)
│   ├── extracted/        (temporary, ignored in git)
│   └── formatted/        (tracked in git)
│       ├── ITEM_ID.json
│       ├── .processed.txt
│       └── ...
├── east/
│   ├── raw/              (temporary, ignored in git)
│   ├── extracted/        (temporary, ignored in git)
│   └── formatted/        (tracked in git)
│       ├── ITEM_ID.json
│       ├── .processed.txt
│       └── ...
└── west/
    ├── raw/              (temporary, ignored in git)
    ├── extracted/        (temporary, ignored in git)
    └── formatted/        (tracked in git)
        ├── ITEM_ID.json
        ├── .processed.txt
        └── ...
```

**Example:** `albion_data_dumps/europe/formatted/T4_2H_SWORD.json`
```json
[
  {
    "timestamp": "2026-08-30T10:30:45Z",
    "city": "bridgewatch",
    "quality": 1,
    "server": "europe",
    "sellPrice": 5000,
    "buyPrice": 5000,
    "quantity": 100
  },
  {
    "timestamp": "2026-08-30T11:00:00Z",
    "city": "caerleon",
    "quality": 1,
    "server": "europe",
    "sellPrice": 5050,
    "buyPrice": 5025,
    "quantity": 150
  }
]
```

## Usage

### Manual Processing

Run the original GUI tool to manually process AODP SQL files:

```bash
python albion_data_extractor/transform_aodp_to_json_v2.py
```

This opens an interactive GUI where you can:
- Select SQL files from your AODP extracted data
- View processing progress
- Track which files have been processed

### CI/CD Processing

The project uses a single combined GitHub Actions workflow that runs every **6 hours** (`0 */6 * * *`) and also supports manual triggering from the Actions tab. Each scheduled run processes **Europe → East → West** sequentially, with up to **10 files per server** per execution.

The workflow does the following for each server:
1. Uses its own isolated `data/<server>/` folder
2. Downloads the oldest unprocessed archive for that server
3. Extracts and formats the data into JSON
4. Keeps only `formatted/` output and the server `.processed.txt` in the repository commit
5. Removes raw and extracted SQL dumps before pushing to avoid GitHub's 100 MB file limit

Each server has its own `.processed.txt` file tracking which files have been completed. If a stale `.processed.txt` blocks downloading, rerun the workflow with the `reset_processed` option enabled to clear the cached state and re-seed the archive list.

## File Structure

```
albion_price_history/
├── data/
│   ├── europe/                        # Europe server data
│   │   ├── raw/                       # Downloaded .gz files (ignored in git)
│   │   ├── extracted/                 # SQL files extracted (ignored in git)
│   │   └── formatted/                 # JSON output (tracked in git)
│   │       ├── {ITEM_ID}.json
│   │       ├── .processed.txt
│   │       └── ...
│   ├── east/                          # East server data
│   │   ├── raw/                       # Downloaded .gz files (ignored in git)
│   │   ├── extracted/                 # SQL files extracted (ignored in git)
│   │   └── formatted/                 # JSON output (tracked in git)
│   │       ├── {ITEM_ID}.json
│   │       ├── .processed.txt
│   │       └── ...
│   └── west/                          # West server data
│       ├── raw/                       # Downloaded .gz files (ignored in git)
│       ├── extracted/                 # SQL files extracted (ignored in git)
│       └── formatted/                 # JSON output (tracked in git)
│           ├── {ITEM_ID}.json
│           ├── .processed.txt
│           └── ...
├── albion_data_extractor/
│   ├── transform_aodp_to_json_v2.py  # GUI tool (legacy)
│   └── ci_processor.py               # Automated CI processor
└── .github/workflows/
    └── market-history/*.yml          # Server-specific GitHub Actions workflows
```

## Data Sources

Market history data is sourced from the **Albion Online Data Project (AODP)**:
- Website: https://www.albion-online-data.com/
- Database: https://www.albion-online-data.com/database/

Data includes historical prices from multiple servers and cities dating back to 2020.
