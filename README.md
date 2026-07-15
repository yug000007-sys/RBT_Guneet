# Heitek SPA/Rebate Line Item Extractor

Extracts line-item pricing data from supplier SPA/Rebate PDFs attached to
`.msg` emails and outputs an Excel file matching the 41-column contract
schema.

## What it does

Each `.msg` file contains two PDF attachments:

1. `PriceSheetCreation.pdf` — the P21 price page request form (ignored by
   this app; contains header-level fields only).
2. A supplier PDF (e.g. `SA_309096_for_...pdf`) — contains the line-item
   pricing table.

The app finds the supplier PDF automatically (it's whichever attached PDF
is *not* named `PriceSheetCreation.pdf`), extracts its line-item table, and
maps:

| PDF column   | Output column     |
|--------------|--------------------|
| Model Number | `sku`              |
| List Price   | `list_price`       |
| Dist Multi   | `multiplier`       |
| Dist Net     | `contract_price`   |

All other columns in the 41-column schema are left blank on purpose.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Cloud

1. Push this folder to a GitHub repo.
2. In Streamlit Cloud, create a new app pointing at `app.py` in that repo.
3. **Important:** under Advanced settings, set the Python version to
   **3.12** explicitly. Do not rely on a `runtime.txt` file — Streamlit
   Cloud silently ignores it.
4. Deploy.

## Adding a new supplier

The app currently has a parser for **Banner Engineering Corporation** only.
The dropdown lists all 16 suppliers, but any supplier without a configured
parser will show a "not yet configured" message and its files will come
back as errors.

To add a supplier:
1. Send a sample `.msg` file for that supplier (with its SPA/rebate PDF).
2. A new parser function gets added to `app.py` (following the pattern of
   `extract_line_items_banner`) and registered in `SUPPLIER_PARSERS`.

## Notes / assumptions

- Only 4 fields are populated per your instructions (`sku`, `list_price`,
  `multiplier`, `contract_price`); everything else in the 41-column output
  is left blank.
- If a supplier PDF has multiple line-item tables (e.g. "Added/Updated" and
  "Existing"), the app pulls data rows from all of them — a table with no
  data rows (like an empty "Existing" section) simply contributes nothing.
- If a `.msg` file doesn't contain a recognizable supplier PDF or no line
  items are found, the app reports it as a warning rather than failing the
  whole batch.
