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
- `settlements`: Extracted settlement data with `llm_extracted` field, linked via `complaint_id`
- `cases_summary`: Status tracking for each case (OCR status, extraction status)

### Web App Features (app.py)
- **Cases Tab**: Browse complaints with filters (category, specialty, year, drug, patient sex, has settlement)
- **Modal View**: Click any case to see details + embedded PDF viewer with tabs for complaint/settlement
- **Statistics Tab**: Aggregate analytics with Chart.js
  - Totals cards: Fines collected, investigation costs, CME hours, probation time
  - Charts: Cases by year, category breakdown, top specialties, license actions
  - Histograms: Fine/cost distributions (capped at 90th percentile)

### Key Files
- `app.py`: FastAPI app with embedded HTML/JS frontend
- `scripts/process_complaints.py`: LLM extraction for complaints (GPT-4o)
- `scripts/process_settlements.py`: LLM extraction for settlements (GPT-4o)
- `scripts/prompts/complaint_extraction.md`: LLM prompt for complaints
- `scripts/prompts/settlement_extraction.md`: LLM prompt for settlements

### Environment Variables (.env)
```
OPENAI_API_KEY=sk-...
MONGODB_URI=mongodb://...
```

## Data Schema

### Complaint Extraction
- `summary`: One-sentence description
- `specialty`: ABMS-recognized specialty (e.g., "Internal Medicine")
- `num_complainants`: Number of patients
- `complainants[]`: Array of {age, sex}
- `procedure`: Medical procedure involved
- `drugs[]`: Medications mentioned
- `category`: Standard of Care, Controlled Substances, Sexual Misconduct, etc.

### Settlement Extraction
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
