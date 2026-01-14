# CLAUDE.md

Nevada Medical Malpractice Explorer - Scrape, process, and analyze public medical malpractice filings from the Nevada State Board of Medical Examiners (2008-2025).

## Workflow Rules

- **Never commit unless explicitly told**
- **Always use TrackedDB for database updates** - Never use direct `db.collection.update_one()`. Use `TrackedDB` from `lib/tracked_db.py` to log all changes. See `analysis/CLAUDE.md` for examples.

## Quick Commands

```bash
uv run uvicorn app:app --reload --port 8000    # Run web app
uv run python scripts/process_new_filings.py   # Process new filings (cron job)
uv run python scripts/process_single_file.py path/to/file.pdf  # Process one PDF

# Manage related links on cases
uv run python scripts/add_link.py 19-28023-1 "https://..." --title "Title"  # Add link
uv run python scripts/add_link.py 19-28023-1 --list                          # List links
uv run python scripts/add_link.py 19-28023-1 --remove "https://..."          # Remove link
```

## MongoDB Collections

### complaints
- `case_number`: Unique identifier (e.g., "19-28023-1")
- `respondent`, `date`, `year`, `type`, `pdf_url`
- `is_amended`, `original_complaint`, `amendment_summary` (for amended complaints)
- `related_links[]`: Array of `{url, title}` for external links (news articles, etc.)
- `llm_extracted`:
  - `summary`: One-sentence description
  - `specialty`: NPDB-normalized (24 categories: Internal Medicine, Neurosurgery, Orthopedics, etc.)
  - `category`: Malpractice - Treatment/Diagnosis/Surgical Error/Medication, Controlled Substances, Sexual Misconduct, Impairment, Unprofessional Conduct, License Violation, Other
  - `procedure`, `drugs[]`, `num_complainants`, `complainants[]`

### settlements
- `case_numbers[]`: Array (one resolution can cover multiple complaints)
- `complaint_ids[]`: ObjectId references to complaints
- `resolution_outcome`: "Settlement" or "Hearing"
- `pdf_url`: Unique identifier
- `llm_extracted`:
  - `license_action`: revoked, suspended, surrendered, probation, reprimand, none
  - `fine_amount`, `investigation_costs`, `probation_months`
  - `cme_hours`, `cme_topic`, `violations_admitted[]`

### change_log
Auto-populated by TrackedDB. Fields: `timestamp`, `collection`, `document_id`, `document_key`, `operation`, `script`, `reason`, `changes[]`

### Other collections
- `license_only_filings`: Administrative actions by license number (no case number)
- `cases_summary`: Processing status tracking

## Project Structure

```
app.py                      # FastAPI app with Pydantic models
static/index.html           # Frontend (vanilla JS, Chart.js)
static/css/styles.css       # Archival Brutalism design system

lib/                        # Shared utilities
├── tracked_db.py           # ALWAYS use for DB updates
└── change_logger.py        # Change tracking utilities

scripts/
├── process_new_filings.py  # Cron job: scrape + process new filings
├── process_single_file.py  # Core pipeline: PDF → OCR → LLM → MongoDB
├── add_link.py             # CLI to add/remove related links on cases
├── prompts/                # LLM extraction prompts
└── utils/                  # Migrations, indexes, normalization scripts

analysis/                   # See analysis/CLAUDE.md
├── scripts/
│   ├── load_data.py        # MongoDB → pandas DataFrames
│   └── specialty_comparison.py  # Nevada vs national claims by specialty
├── datasets/               # External datasets (NPDB claims data)
└── output/                 # Generated charts (gitignored)
```

## Key Patterns

- **Case number format**: `YY-NNNNN-N` (e.g., "19-28023-1"). Suffix indicates case in series.
- **Settlement linking**: Settlements reference complaints via `case_numbers[]` array and `complaint_ids[]` ObjectIds
- **Specialty normalization**: 24 NPDB-standard categories. Subspecialties map to parents (e.g., Nephrology → Internal Medicine)
- **Date format**: M/D/YYYY strings in MongoDB, parsed with `$dateFromString` for sorting

## Data Validation

See `audit2.md` for the validation plan before public release:
- Manual audit of 80 documents (gold standard sample)
- Automated validation checks (format, range, consistency, inter-document)
- Acceptance criteria: ≥95% accuracy on critical fields

## Environment

```
OPENAI_API_KEY=sk-...
MONGODB_URI=mongodb://...
```
