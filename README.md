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

The project includes automated GitHub Actions that run **on the 1st of every month** to:

1. Check servers in priority order: **Europe → East → West**
2. Process **ONE server per run** (whichever has unprocessed files first)
3. Download the oldest unprocessed file from that server
4. Extract and format the data
5. Clean up temporary files and commit results to the repo

**Processing Order:**
- Month 1: Europe file 1 processed
- Month 2: Europe file 2 processed (if available) or East file 1 (if EU done)
- Month 3: Continue down the priority list
- Eventually cycles through all servers processing all files chronologically

Each server has its own `.processed.txt` file tracking which files have been completed.

To manually trigger this workflow, go to the GitHub Actions tab and click "Run workflow".

## File Structure

```
albion_price_history/
├── albion_data_dumps/
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
    └── monthly-update.yml            # GitHub Actions workflow
```

## Data Sources

Market history data is sourced from the **Albion Online Data Project (AODP)**:
- Website: https://www.albion-online-data.com/
- Database: https://www.albion-online-data.com/database/

Data includes historical prices from multiple servers and cities dating back to 2020.
