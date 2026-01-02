# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Nevada Medical Malpractice Explorer - Tools to scrape, process, and analyze public medical malpractice filings from the Nevada State Board of Medical Examiners (2008-2025). Includes LLM-powered data extraction and a FastAPI web app for exploring complaints, settlements, and aggregate statistics.

## Workflow Rules

- **Never commit unless explicitly told** - Do not stage or commit changes automatically. Wait for explicit user instruction to commit.

## Data Pipeline

### Automated Processing (Cron Job)

For automated daily processing of new filings (designed for Railway or similar):

```bash
# Check current + previous year, process any new filings
uv run python scripts/process_new_filings.py

# Preview without processing
uv run python scripts/process_new_filings.py --dry-run

# Check all years (for backfill detection)
uv run python scripts/process_new_filings.py --all-years
```

The cron job:
1. Scrapes Nevada Medical Board for filings (current + previous year by default)
2. Compares against MongoDB by `pdf_url` to find new filings
3. Downloads new PDFs to temp directory (ephemeral, no persistence needed)
4. Processes each through the full pipeline (OCR → clean → LLM → MongoDB)
5. Cleans up temp files automatically

### Single File Processing

For processing individual PDFs end-to-end (OCR → clean → LLM → MongoDB):

```bash
# Process a single PDF through the entire pipeline
uv run python scripts/process_single_file.py path/to/file.pdf

# Preview without storing in MongoDB
uv run python scripts/process_single_file.py path/to/file.pdf --dry-run

# Skip OCR if text already exists
uv run python scripts/process_single_file.py path/to/file.pdf --skip-ocr
```

The script automatically:
- Detects document type (complaint, settlement, or ignored)
- Calculates OCR timeout based on page count (30s/page, 2-30 min range)
- Cleans OCR artifacts
- Extracts data via LLM
- Links settlements to complaints
- Handles amended complaints (generates amendment summary)
- Accepts external metadata via `filing_metadata` param (used by cron job for date, respondent, pdf_url)

### Batch Processing (Legacy)

For bulk processing, use the scripts in `scripts/batch/`:

```bash
# Step 1: Scrape new filings metadata and download PDFs
uv run python scripts/scraper.py
# Downloads to: pdfs/{year}/*.pdf
# Creates: data/filings.json

# Step 2: Normalize the scraped data
uv run python scripts/batch/normalize_filings.py
# Cleans formatting, expands multi-case entries
# Creates: data/filings_normalized.json

# Step 3: Validate data quality
uv run python scripts/utils/validate_filings.py
# Checks for missing fields, invalid dates, etc.

# Step 4: OCR the PDFs to extract text
uv run python scripts/batch/ocr_pdfs.py
# Creates: pdfs_ocr/{year}/*.pdf (searchable PDFs)
# Creates: text/{year}/*.txt (plain text)

# Step 5: Clean OCR artifacts from text files
uv run python scripts/batch/clean_text.py --text-dir text/ --apply
# Removes line numbers, page markers, gibberish from margins

# Step 6: Process complaints through LLM
uv run python scripts/batch/process_complaints.py
# Extracts: summary, specialty, drugs, category, patient demographics
# Stores in MongoDB: complaints collection

# Step 7: Process settlements through LLM
uv run python scripts/batch/process_settlements.py
# Extracts: license_action, fines, probation, CME, violations
# Stores in MongoDB: settlements collection
# Links to complaints via complaint_id

# Step 8: Update cases summary table
uv run python scripts/utils/build_cases_summary.py
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
uv run python scripts/utils/migrate_settlements.py

# Apply the migration
uv run python scripts/utils/migrate_settlements.py --apply
```

This consolidates duplicate settlements (same PDF, multiple case numbers) into single documents.

## Quick Commands

```bash
# Run the web app
uv run uvicorn app:app --reload --port 8000

# Process new filings (cron job)
uv run python scripts/process_new_filings.py

# Process a single file end-to-end
uv run python scripts/process_single_file.py path/to/file.pdf

# Check processing status
uv run python scripts/utils/build_cases_summary.py

# Batch process (legacy)
uv run python scripts/batch/process_complaints.py --limit 10
uv run python scripts/batch/process_settlements.py --limit 10
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
- `license_only_filings`: Administrative actions tied to a license number (not a case number)
  - Examples: Order of Summary Suspension, Order Accepting Voluntary Surrender, probation releases
  - Identified by `LICENSE-{number}` pattern (e.g., LICENSE-401)
  - Contains OCR text and metadata, no LLM processing
  - Fields: `license_number`, `type`, `year`, `date`, `respondent`, `pdf_url`, `text_content`
- `cases_summary`: Status tracking for each case (OCR status, extraction status)

### MongoDB Indexes
Run `uv run python scripts/utils/create_indexes.py` to create performance indexes:
- `complaints`: `case_number` (unique), `llm_extracted` (sparse), `category+specialty+year` (compound), `year`, `respondent`
- `settlements`: `pdf_url` (unique), `case_numbers`, `llm_extracted` (sparse), `year`
- `license_only_filings`: `pdf_url` (unique), `license_number`, `type`, `year`, `respondent`

### Web App Features (app.py)
- **Cases Tab**: Browse complaints with custom multi-select filters (category, specialty, settlement status, license action)
  - Filters auto-search on change, support multi-selection with Select All/Clear buttons
  - API accepts comma-separated values for multi-select filters
  - License action filter queries settlements collection and returns matching complaints
  - Sort options: Date (Newest/Oldest), Respondent A-Z/Z-A
  - Content wrapper has max-width 900px, centered
- **Case Cards**: Display all fields with "—" for missing data
  - Row 1: Doctor name (left) + License action tag + category tag (right)
  - Row 2: Specialty with icon (left) + "Case x of y in year" (right)
  - Body: Summary (max-width 82ch for readability)
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

### Scripts Organization
```
scripts/
├── process_new_filings.py   # Cron job: scrape + process new filings (Railway-ready)
├── process_single_file.py   # Core pipeline: single PDF → MongoDB
├── scraper.py               # Download PDFs from Nevada Medical Board
├── prompts/                 # LLM prompts
│   ├── complaint_extraction.md
│   ├── settlement_extraction.md
│   └── amendment_comparison.md
├── batch/                   # Batch processing (legacy)
│   ├── ocr_pdfs.py
│   ├── clean_text.py
│   ├── process_complaints.py
│   ├── process_settlements.py
│   ├── normalize_filings.py
│   └── reprocess_amended_complaints.py
└── utils/                   # Utilities & one-time scripts
    ├── build_cases_summary.py
    ├── create_indexes.py
    ├── migrate_settlements.py
    ├── validate_filings.py
    └── aggregate_cases.py
```

### Key Files
- `app.py`: FastAPI app (~580 lines) with Pydantic models and dependency injection
- `static/index.html`: Frontend HTML + JavaScript
- `static/css/styles.css`: Frontend styles with design system
- `scripts/process_single_file.py`: Unified pipeline for single-file processing
  - Supports 21 settlement types and complaint types
  - Automatic page-based OCR timeout (30s/page, 2-30 min range)
  - Handles amended complaints with amendment summary generation
- `scripts/batch/process_complaints.py`: Batch LLM extraction for complaints (GPT-4o)
- `scripts/batch/process_settlements.py`: Batch LLM extraction for settlements (GPT-4o)
  - Automatic chunking for documents >70k chars to handle TPM limits
- `scripts/utils/create_indexes.py`: Creates MongoDB indexes for query performance

### Frontend Design System

**Aesthetic**: Archival Brutalism - sharp corners, border-based separation, utilitarian feel inspired by legal documents and filing systems.

**Color Palette** (CSS variables in `:root`):
- `--black`: #14110F (near-black, headers)
- `--charcoal`: #34312D (dark charcoal, secondary backgrounds)
- `--gray`: #7E7F83 (muted text, borders)
- `--tan`: #D9C5B2 (warm accent, highlights)
- `--off-white`: #F3F3F4 (backgrounds)
- `--warm-white`: #faf9f7 (card backgrounds)

**Category Colors** (blue/purple palette to distinguish from license actions):
- Treatment: #2563eb (blue)
- Diagnosis: #4f46e5 (indigo)
- Medication: #0284c7 (sky blue)
- Surgical: #6366f1 (slate)
- Controlled Substances: #9333ea (purple)
- License Violation: #7c3aed (violet)
- Sexual Misconduct: #c026d3 (fuchsia)
- Impairment: #1e3a8a (deep blue)
- Unprofessional Conduct: #a855f7 (light purple)
- Other: #8b5cf6 (medium purple)

**License Action Severity Colors** (yellow to red gradient, uses partial matching for variations like "SUSPENSION (STAYED)"):
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
- Multi-select with checkboxes (category, specialty, license action)
- Single-select with radio style (sort, settlement status)
- Select All / Clear buttons
- Auto-search on selection change
- **None selected behavior**: Returns `null` (not empty array), triggers "no cases matched" message
- **"Missing" filter option**: Specialty filter includes "Missing" to find cases without specialty data (queries null/empty/non-existent)

**Date Sorting**: Uses MongoDB aggregation pipeline with `$dateFromString` to parse M/D/YYYY date strings for correct chronological sorting.

**API Enhancement**: `/api/complaints` accepts comma-separated values for multi-select filters and includes:
- `settlement_summary`: license_action, fine_amount, investigation_costs, cme_hours, probation_months, date
- `case_index`: Position of this case in the series (from case_number suffix, e.g., -1, -2)
- `total_cases`: `max(suffix, actual_count)` - handles cases where earlier numbers are missing (e.g., only "-2" exists → shows "Case 2 of 2")

**API Performance Optimizations** (in `get_complaints`):
- Settlement lookup fetches only settlements for case numbers in current page (not all 604)
- `has_settlement` filter uses single aggregation with `$unwind` + `$addToSet` instead of Python loop
- Case prefix counting uses single regex query matching all prefixes at once

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
  - `charity_donation`: Required charitable donation (rare, ~5 cases)
  - `cme_hours`, `cme_topic`: Continuing education requirements
  - `violations_admitted[]`: NRS codes and descriptions

## Current Stats

- 679 complaints in MongoDB, 674 with LLM extraction (99.3%)
- 660 settlements in MongoDB, all with LLM extraction (100%)
- 615 cases with both complaint and settlement
- Includes 56 "Findings of Fact" documents (contested cases that went to hearing)
