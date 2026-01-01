# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Nevada Medical Malpractice Explorer - Tools to scrape, process, and analyze public medical malpractice filings from the Nevada State Board of Medical Examiners (2008-2025). Includes LLM-powered data extraction and a FastAPI web app for exploring complaints, settlements, and aggregate statistics.

## Data Pipeline (New PDFs → Website)

When new filings are published, run these steps in order:

```bash
# Step 1: Scrape new filings metadata and download PDFs
uv run python scripts/scraper.py
# Downloads to: pdfs/{year}/*.pdf
# Creates: data/filings.json

# Step 2: Normalize the scraped data
uv run python scripts/normalize_filings.py
# Cleans formatting, expands multi-case entries
# Creates: data/filings_normalized.json

# Step 3: Validate data quality
uv run python scripts/validate_filings.py
# Checks for missing fields, invalid dates, etc.

# Step 4: OCR the PDFs to extract text
uv run python scripts/ocr_pdfs.py
# Creates: pdfs_ocr/{year}/*.pdf (searchable PDFs)
# Creates: text/{year}/*.txt (plain text)
# Note: Default 5-min timeout. Large files may need manual OCR with longer timeout.

# Step 5: Clean OCR artifacts from text files
uv run python scripts/clean_text.py --text-dir text/ --apply
# Removes line numbers, page markers, gibberish from margins

# Step 6: Process complaints through LLM
uv run python scripts/process_complaints.py
# Extracts: summary, specialty, drugs, category, patient demographics
# Stores in MongoDB: complaints collection

# Step 7: Process settlements through LLM
uv run python scripts/process_settlements.py
# Extracts: license_action, fines, probation, CME, violations
# Stores in MongoDB: settlements collection
# Links to complaints via complaint_id

# Step 8: Update cases summary table
uv run python scripts/build_cases_summary.py
# Rebuilds cases_summary collection with processing status

# Step 9: Restart web app to see new data
uv run uvicorn app:app --reload --port 8000
```

### Handling OCR Failures

If OCR times out on large/complex PDFs:

```bash
# Check which files failed
uv run python -c "
from pathlib import Path
for f in Path('text').rglob('*.txt'):
    if f.read_text(errors='ignore').strip().count('\n') <= 1:
        print(f)
"

# Manually OCR with longer timeout (no timeout limit)
ocrmypdf --sidecar text/{year}/{case}_Complaint.txt \
    --rotate-pages --deskew --clean --force-ocr -l eng --jobs 2 \
    pdfs/{year}/{case}_Complaint.pdf \
    pdfs_ocr/{year}/{case}_Complaint.pdf

# Then re-run clean_text.py and LLM processing
```

### Handling LLM Rate Limits

OpenAI has 30k TPM limit. If you hit rate limits:
- Run complaints and settlements processing sequentially, not concurrently
- Or add `--limit N` flag to process in batches

### Migrating Settlements (if upgrading from old schema)

If you have existing settlement data with `case_number` (singular) instead of `case_numbers[]` (array):

```bash
# Preview what will change (dry run)
uv run python scripts/migrate_settlements.py

# Apply the migration
uv run python scripts/migrate_settlements.py --apply
```

This consolidates duplicate settlements (same PDF, multiple case numbers) into single documents.

## Quick Commands

```bash
# Run the web app
uv run uvicorn app:app --reload --port 8000

# Check processing status
uv run python scripts/build_cases_summary.py

# Process specific number of documents
uv run python scripts/process_complaints.py --limit 10
uv run python scripts/process_settlements.py --limit 10

# Dry run (preview without changes)
uv run python scripts/process_complaints.py --dry-run
```

## Architecture

### Data Flow
```
scraper.py → pdfs/{year}/*.pdf + data/filings.json
     ↓
normalize_filings.py → data/filings_normalized.json
     ↓
ocr_pdfs.py → pdfs_ocr/{year}/*.pdf + text/{year}/*.txt
     ↓
clean_text.py → text/{year}/*.txt (cleaned)
     ↓
process_complaints.py → MongoDB: complaints collection
process_settlements.py → MongoDB: settlements collection
     ↓
build_cases_summary.py → MongoDB: cases_summary collection
     ↓
app.py → Web UI at http://localhost:8000
```

### MongoDB Collections
- `complaints`: Extracted complaint data with `llm_extracted` field
  - **Amended complaints**: If `is_amended: true`, includes `original_complaint` (type, date, pdf_url) and `amendment_summary` (LLM-generated description of changes)
- `settlements`: Extracted settlement data with `llm_extracted` field, linked via `complaint_ids[]` array
  - **Important**: Settlements use `case_numbers[]` array (not singular `case_number`) because one settlement can resolve multiple complaints
  - Unique index is on `pdf_url`, not `case_number`
- `cases_summary`: Status tracking for each case (OCR status, extraction status)

### Web App Features (app.py)
- **Cases Tab**: Browse complaints with custom multi-select filters (category, specialty, year, settlement status)
  - Filters auto-search on change, support multi-selection with Select All/Clear buttons
  - API accepts comma-separated values for multi-select filters
  - Content wrapper has max-width 900px, centered
- **Case Cards**: Display all fields with "—" for missing data
  - Row 1: Doctor name (left) + License action tag + category tag (right)
  - Row 2: Specialty with icon (left) + "Case x of y in year" (right)
  - Body: Summary (max-width 65ch for readability)
  - Footer: Procedure, fine, investigative costs (text labels with colored icons)
  - Case numbering derived from case_number suffix (e.g., "19-28023-1" → Case 1, counted by prefix)
- **Modal View**: Click any case to see details + embedded PDF viewer with tabs for complaint/settlement
  - **Timeline section** at top: Complaint date, Settlement date, Time to Resolution
  - Amended complaints show "Amended Complaint" tab label and "Original Complaint" tab for viewing both PDFs
  - "Changes from Original" section displays LLM-generated amendment summary
- **Statistics Tab**: Aggregate analytics with Chart.js
  - Stats cards: Total complaints, processed, settlements, categories, unique drugs
  - Totals cards: Fines collected, investigation costs, CME hours, probation time
  - Charts: Cases by year, category breakdown, top specialties, license actions
  - Histograms: Fine/cost distributions (capped at 90th percentile)

### Key Files
- `app.py`: FastAPI app (~580 lines) with Pydantic models and dependency injection
- `static/index.html`: Frontend HTML + JavaScript
- `static/css/styles.css`: Frontend styles with design system
- `scripts/process_complaints.py`: LLM extraction for complaints (GPT-4o), handles amended complaint pairs
- `scripts/process_settlements.py`: LLM extraction for settlements (GPT-4o)
- `scripts/reprocess_amended_complaints.py`: Migration script for adding amendment data to existing complaints
- `scripts/prompts/complaint_extraction.md`: LLM prompt for complaints
- `scripts/prompts/settlement_extraction.md`: LLM prompt for settlements
- `scripts/prompts/amendment_comparison.md`: LLM prompt for comparing original vs amended complaints

### Frontend Design System

**Aesthetic**: Archival Brutalism - sharp corners, border-based separation, utilitarian feel inspired by legal documents and filing systems.

**Color Palette** (CSS variables in `:root`):
- `--black`: #14110F (near-black, headers)
- `--charcoal`: #34312D (dark charcoal, secondary backgrounds)
- `--gray`: #7E7F83 (muted text, borders)
- `--tan`: #D9C5B2 (warm accent, highlights)
- `--off-white`: #F3F3F4 (backgrounds)
- `--warm-white`: #faf9f7 (card backgrounds)

**Accent Colors** (for category tags and status indicators):
- `--accent-blue`: #2563eb (treatment/procedures)
- `--accent-green`: #059669 (positive outcomes, fine icon)
- `--accent-amber`: #d97706 (warnings, probation)
- `--accent-red`: #dc2626 (severe actions like revocation)
- `--accent-purple`: #7c3aed (controlled substances, costs icon)
- `--accent-pink`: #db2777 (sexual misconduct)
- `--accent-teal`: #0891b2 (license issues)

**License Action Severity Colors** (yellow to red gradient):
- Reprimand: #eab308 (yellow)
- Probation: #f59e0b (amber)
- Suspended: #f97316 (orange)
- Surrendered: #ef4444 (red-orange)
- Revoked: #dc2626 (red)

**Typography**:
- Display: Libre Baskerville (serif, headers)
- UI/Data: IBM Plex Mono (monospace, case numbers, stats)
- Body: Source Sans 3 (sans-serif, readable text)
- Minimum font size: 11px (no smaller fonts allowed)

**Icons**: Lucide Icons library (CDN). Call `lucide.createIcons()` after dynamic content renders.

**Component Naming**: BEM convention for case cards (`.case-card`, `.case-card__header`, `.case-card__body`, etc.)

**Custom Multi-Select Dropdowns**: JavaScript `CustomSelect` class manages filter dropdowns with:
- Multi-select with checkboxes (category, specialty, year)
- Single-select with radio style (sort, settlement)
- Select All / Clear buttons
- Auto-search on selection change

**API Enhancement**: `/api/complaints` accepts comma-separated values for multi-select filters and includes:
- `settlement_summary`: license_action, fine_amount, investigation_costs, cme_hours, probation_months, date
- `case_index`: Position of this case in the series (from case_number suffix, e.g., -1, -2)
- `total_cases`: Total cases with same prefix (e.g., "19-28023-*")

### FastAPI Architecture
- **Lifespan**: Uses `@asynccontextmanager` lifespan for startup/shutdown (not deprecated `@app.on_event`)
- **Dependency Injection**: `DB = Annotated[Database, Depends(get_db)]` for testable DB access
- **Response Models**: Pydantic models for all API responses (see `/docs` for OpenAPI schema)
- **DatabaseConnection**: Class managing MongoDB connection lifecycle

### Environment Variables (.env)
```
OPENAI_API_KEY=sk-...
MONGODB_URI=mongodb://...
```

## Data Schema

### Complaint Document (MongoDB)
- `case_number`: Single case identifier (e.g., "19-28023-1")
- `is_amended`: Boolean, true if this is an amended complaint
- `original_complaint`: Object with original complaint metadata (type, date, pdf_url) - only present if amended
- `amendment_summary`: LLM-generated one-sentence description of changes - only present if amended
- `llm_extracted`: LLM-extracted fields:
  - `summary`: One-sentence description
  - `specialty`: ABMS-recognized specialty (e.g., "Internal Medicine")
  - `num_complainants`: Number of patients
  - `complainants[]`: Array of {age, sex}
  - `procedure`: Medical procedure involved
  - `drugs[]`: Medications mentioned
  - `category`: Standard of Care, Controlled Substances, Sexual Misconduct, etc.

### Settlement Document (MongoDB)
- `case_numbers[]`: Array of case identifiers this settlement resolves (one-to-many relationship)
- `complaint_ids[]`: Array of ObjectIds linking to complaints
- `pdf_url`: Unique identifier for the settlement PDF
- `llm_extracted`: LLM-extracted fields:
  - `license_action`: revoked, suspended, surrendered, probation, reprimand, none
  - `probation_months`: Duration of probation
  - `fine_amount`: Dollar amount
  - `investigation_costs`: Costs recovered
  - `cme_hours`, `cme_topic`: Continuing education requirements
  - `violations_admitted[]`, `violations_dismissed[]`: NRS codes and descriptions

## Current Stats

- 674 complaints with LLM extraction (86.3%)
- 463 settlements with LLM extraction (59.3%)
- 436 cases with both complaint and settlement extracted (55.8%)
- $1,049,700 in total fines collected
- $2,144,887 in investigation costs recovered
