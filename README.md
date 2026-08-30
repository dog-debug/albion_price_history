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

Each item gets its own JSON file named `{ITEM_ID}.json`:

```json
{
  "itemId": "ITEM_NAME",
  "priceHistory": [
    {
      "timestamp": "2026-08-30T10:30:45Z",
      "city": "caerleon",
      "quality": 1,
      "server": "europe.albion-online-data.com",
      "sellPrice": 5000,
      "buyPrice": 5000,
      "quantity": 100
    }
  ]
}
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

1. Download latest AODP exports
2. Extract ZIP files
3. Process market history SQL files
4. Commit results back to the repo

To manually trigger this workflow, go to the GitHub Actions tab and click "Run workflow".

## File Structure

```
albion_price_history/
├── albion_data_dumps/
│   ├── extracted/
│   │   └── history/          # SQL files extracted from AODP
│   └── formatted/
│       ├── {ITEM_ID}.json    # Output price history files
│       └── .processed.txt    # Tracks which SQL files have been processed
├── albion_data_extractor/
│   ├── transform_aodp_to_json_v2.py  # GUI tool
│   └── ci_processor.py               # Automated CI processor
└── .github/workflows/
    └── monthly-update.yml    # GitHub Actions workflow
```

## Data Sources

Market history data is sourced from the **Albion Online Data Project (AODP)**:
- Website: https://www.albion-online-data.com/
- Database: https://www.albion-online-data.com/database/

Data includes historical prices from multiple servers and cities dating back to 2020.
