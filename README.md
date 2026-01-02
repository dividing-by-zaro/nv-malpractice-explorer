# Nevada Medical Malpractice Explorer

Tools to scrape, process, and analyze public medical malpractice filings from the Nevada State Board of Medical Examiners (2008-2025). Features LLM-powered data extraction and an interactive web app for exploring cases.

## Current Stats

- **1,594 filings** scraped (2008-2025)
- **679 complaints** in MongoDB (674 with LLM extraction)
- **660 settlements** in MongoDB (all with LLM extraction)
  - Includes 56 "Findings of Fact" documents (contested cases that went to hearing)
- **615 cases** with both complaint and settlement linked

### Pipeline Status

| Stage | Complaints | Settlements |
|-------|------------|-------------|
| 1. Source filings | 770 | 664 |
| 2. PDFs downloaded | 770 | 762 |
| 3. OCR'd text files | 763 | 762 |
| 4. MongoDB (LLM extracted) | 679 (674) | 660 (660) |
| 5. Linked (settlement → complaint) | — | 615 |

### Known Gaps (TODO)

**Complaints needing processing** (have text, not in MongoDB):
- `08-12069-1` - Complaint type not recognized by filter
- `13-10054-1` - Has both Complaint and First Amended Complaint
- `21-12891-1`, `21-12891-3` - "Complaint and Errata" type

**Complaints needing OCR** (PDF exists, no text):
- `14-38887-1`, `21-41427-1`, `21-12423-1`

**Complaints needing PDF download**:
- `24-43198-1`, `18-19369-1`, `18-9800-1`, `19-38390-1`

## Quick Start

```bash
# Install dependencies
uv sync
brew install ocrmypdf poppler  # OCR tools (macOS)

# Configure environment
cp .env.example .env
# Add OPENAI_API_KEY and MONGODB_URI

# Run the web app
uv run uvicorn app:app --reload --port 8000
# Open http://localhost:8000
```

## Web App Features

- **Cases Tab**: Browse complaints with custom multi-select filters
  - Filters: Category, specialty, settlement status (with Select All/Clear buttons)
  - Sort by: Date (Newest/Oldest), Respondent A-Z/Z-A
  - Auto-search on filter change, no manual submit needed
  - Narrower layout (900px max-width) for improved readability
  - Case cards show:
    - Row 1: Doctor name + license action tag (yellow→red severity) + category tag (blue/purple)
    - Row 2: Specialty + "Case x of y in [year]" (based on case number series)
    - Summary text with comfortable reading width
    - Footer: Procedure, fine, investigative costs (with colored icons)
- **Case Details**: Click any case to view extracted data + embedded PDF viewer (tabs for complaint/settlement)
  - Timeline section shows complaint date, settlement date, and time to resolution
  - Amended complaints display both original and amended PDFs in separate tabs
  - LLM-generated summary explains what changed between versions
- **Statistics Tab**: Aggregate analytics dashboard
  - Stats cards: Total complaints, processed count, settlements, categories
  - Totals: Fines collected, investigation costs, CME hours, probation time
  - Charts: Cases by year, category breakdown, top specialties, license actions
  - Histograms: Fine/cost distributions (capped at 90th percentile for readability)
- **API Documentation**: Interactive OpenAPI docs at `/docs` with typed response schemas
- **Optimized API**: Targeted settlement lookups, batched prefix counting, indexed queries (~180ms response time)

### Design System

The frontend uses an "Archival Brutalism" aesthetic with sharp corners, border-based separation, and a utilitarian feel:

- **Color Palette**: Near-black (#14110F), charcoal (#34312D), gray (#7E7F83), tan (#D9C5B2), off-white (#F3F3F4)
- **Category Colors**: Blue/purple palette (treatment, diagnosis, medication, surgical, controlled substances, etc.)
- **License Action Colors**: Yellow→red severity gradient (reprimand, probation, suspended, surrendered, revoked)
- **Typography**: Libre Baskerville (headers), IBM Plex Mono (data), Source Sans 3 (body)
- **Icons**: Lucide Icons library

## Data Pipeline

### Processing New Filings (Complete Pipeline)

When new filings are published on the Nevada Board website, run these steps in order:

```bash
# Step 1: Scrape - Download new filings metadata and PDFs
uv run python scripts/scraper.py
# Output: pdfs/{year}/*.pdf, data/filings.json

# Step 2: Normalize - Clean and standardize the data
uv run python scripts/normalize_filings.py
# Output: data/filings_normalized.json

# Step 3: Validate - Check data quality
uv run python scripts/validate_filings.py
# Reports any issues with the data

# Step 4: OCR - Extract text from scanned PDFs
uv run python scripts/ocr_pdfs.py
# Output: pdfs_ocr/{year}/*.pdf (searchable), text/{year}/*.txt
# Note: Uses 5-min timeout per file. Large files may need manual processing.

# Step 5: Clean Text - Remove OCR artifacts
uv run python scripts/clean_text.py --text-dir text/ --apply
# Removes line numbers, page markers, margin gibberish

# Step 6: LLM Extract Complaints - Process through GPT-4o
uv run python scripts/process_complaints.py
# Extracts: summary, specialty, drugs, category, patient info
# Stores in MongoDB: complaints collection

# Step 7: LLM Extract Settlements - Process through GPT-4o
uv run python scripts/process_settlements.py
# Extracts: license action, fines, probation, CME, violations
# Stores in MongoDB: settlements collection

# Step 8: Update Status - Rebuild cases summary
uv run python scripts/build_cases_summary.py
# Updates MongoDB: cases_summary collection

# Step 9: View Results - Start web app
uv run uvicorn app:app --reload --port 8000
```

### Pipeline Diagram

```
Nevada Board Website
        ↓
   scraper.py ──────→ pdfs/{year}/*.pdf + data/filings.json
        ↓
normalize_filings.py → data/filings_normalized.json
        ↓
   ocr_pdfs.py ─────→ pdfs_ocr/{year}/*.pdf + text/{year}/*.txt
        ↓
  clean_text.py ────→ text/{year}/*.txt (cleaned)
        ↓
process_complaints.py ──┬──→ MongoDB: complaints
process_settlements.py ─┘    MongoDB: settlements
        ↓
build_cases_summary.py → MongoDB: cases_summary
        ↓
     app.py ────────→ Web UI (http://localhost:8000)
```

### Troubleshooting

**OCR Timeout on Large Files**

Some PDFs (especially tilted scans >10MB) may timeout. Process manually:

```bash
# Find failed files (1 line or less = failed)
find text -name "*.txt" -exec sh -c 'lines=$(wc -l < "$1"); [ "$lines" -le 1 ] && echo "$1"' _ {} \;

# Manual OCR with no timeout
ocrmypdf --sidecar text/2019/19-8552-1_Complaint.txt \
    --rotate-pages --deskew --clean --force-ocr -l eng --jobs 2 \
    pdfs/2019/19-8552-1_Complaint.pdf \
    pdfs_ocr/2019/19-8552-1_Complaint.pdf

# Then clean and reprocess
uv run python scripts/clean_text.py --text-dir text/ --apply
uv run python scripts/process_complaints.py
```

**LLM Rate Limits**

OpenAI has 30k tokens/minute limit. If you hit 429 errors:
- Run complaints and settlements sequentially (not at the same time)
- Use `--limit N` to process in smaller batches

**Check Processing Status**

```bash
uv run python scripts/build_cases_summary.py
# Shows: total cases, OCR success/fail, LLM extraction status
```

## Directory Structure

```
app.py                        # FastAPI API (~580 lines, Pydantic models + DI)
static/
├── index.html                # Frontend HTML + JavaScript
└── css/
    └── styles.css            # Frontend styles
scripts/
├── scraper.py                # Download filings from Nevada Board
├── normalize_filings.py      # Clean/standardize metadata
├── validate_filings.py       # Data quality checks
├── ocr_pdfs.py               # OCR with parallel workers
├── clean_text.py             # Remove OCR artifacts
├── process_complaints.py     # LLM extraction for complaints
├── process_settlements.py    # LLM extraction for settlements
├── build_cases_summary.py    # Update status tracking
├── migrate_settlements.py    # Migrate to deduplicated schema
├── reprocess_amended_complaints.py  # Add amendment data to existing complaints
├── create_indexes.py         # Create MongoDB indexes for performance
└── prompts/
    ├── complaint_extraction.md   # GPT-4o prompt for complaints
    ├── settlement_extraction.md  # GPT-4o prompt for settlements
    └── amendment_comparison.md   # GPT-4o prompt for comparing original vs amended
data/
├── filings.json              # Raw scraped metadata
├── filings_normalized.json   # Cleaned metadata
└── ocr_results.json          # OCR processing log
pdfs/{year}/                  # Original scanned PDFs
pdfs_ocr/{year}/              # Searchable PDFs (after OCR)
text/{year}/                  # Extracted plain text
```

## Data Schema

### Filings Metadata
- `case_number`: e.g., "25-8654-1"
- `type`: Complaint, Settlement Agreement and Order, etc.
- `respondent`: Provider name and credentials
- `date`: Filing date
- `year`: Filing year
- `pdf_url`: Source URL

### Complaint Extraction (LLM)
- `summary`: One-sentence description
- `specialty`: ABMS-recognized specialty (Internal Medicine, Dermatology, etc.)
- `num_complainants`: Number of patients involved
- `complainants[]`: Array of {age, sex}
- `procedure`: Medical procedure if applicable
- `drugs[]`: Medications mentioned
- `category`: Standard of Care, Controlled Substances, Sexual Misconduct, etc.
- `is_amended`: Boolean indicating if this is an amended complaint
- `original_complaint`: Original complaint metadata (type, date, pdf_url) - if amended
- `amendment_summary`: LLM-generated description of changes - if amended

### Settlement Extraction (LLM)
- `case_numbers[]`: Array of case IDs this settlement resolves (one-to-many)
- `license_action`: revoked, suspended, surrendered, probation, reprimand, none
- `probation_months`: Duration of probation
- `fine_amount`: Dollar amount of fine
- `investigation_costs`: Costs recovered from respondent
- `charity_donation`: Required charitable donation (rare)
- `cme_hours`, `cme_topic`: Continuing education requirements
- `public_reprimand`, `npdb_report`: Boolean flags
- `violations_admitted[]`: NRS codes and descriptions admitted

## Environment Variables

Create `.env` file:
```
OPENAI_API_KEY=sk-...      # For LLM processing (GPT-4o)
MONGODB_URI=mongodb://...   # MongoDB connection string
```

## License

This project processes publicly available government records from the Nevada State Board of Medical Examiners.
