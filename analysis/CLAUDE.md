# Analysis Module

## Project Context

The Nevada Medical Malpractice Explorer is a tool for exploring public records from the Nevada State Board of Medical Examiners (2008–2025). We scrape, OCR, and use LLMs to extract structured data from complaint and resolution PDFs, then present this data through a web application.

The **Analysis tab** in the frontend provides in-depth explorations of this data—going beyond simple statistics to tell stories about healthcare risk, physician accountability, and patient safety in Nevada. This `analysis/` directory contains the Python code and datasets that power those analyses.

## Database Updates - IMPORTANT

**ALWAYS use `TrackedDB` for any database modifications.** Never use direct `db.collection.update_one()` calls. This ensures all changes are logged to the `change_log` collection for audit purposes.

```python
from lib import TrackedDB
from analysis.scripts.load_data import get_db

db = get_db()
tracked = TrackedDB(db, script="my_script.py")

# Update a document (automatically logged)
tracked.update_one(
    collection="complaints",
    filter={"case_number": "19-28023-1"},
    update={"$set": {"llm_extracted.specialty": "Neurosurgery"}},
    document_key="19-28023-1",
    reason="Normalize specialty to NPDB standard"
)

# Convenience method for $set operations
tracked.set_fields(
    collection="complaints",
    filter={"case_number": "19-28023-1"},
    fields={"llm_extracted.category": "Malpractice - Treatment"},
    document_key="19-28023-1",
    reason="Fix category typo"
)

# Bulk updates (each doc logged separately)
tracked.update_many(
    collection="complaints",
    filter={"llm_extracted.specialty": "Old Name"},
    update={"$set": {"llm_extracted.specialty": "New Name"}},
    reason="Rename specialty"
)
```

### Querying Change History

```python
from lib import get_document_history, get_changes_by_script

# Get all changes to a specific document
history = get_document_history(db, "complaints", doc_id)

# Get all changes made by a specific script
changes = get_changes_by_script(db, "normalize_specialties.py")
```

## Quick Start

```python
from analysis.scripts import load_complaints, load_settlements, load_merged

# Load data into pandas DataFrames
complaints = load_complaints()      # ~690 records, llm_* fields flattened
settlements = load_settlements()    # ~663 records
df = load_merged()                  # Complaints joined with resolution data
```

## Directory Structure

```
analysis/
├── datasets/       # External datasets (CSV, JSON) for enriching analysis
├── output/         # Generated outputs (gitignored)
└── scripts/        # Python analysis modules
    └── load_data.py      # MongoDB → pandas loader

# Tracking utilities are in lib/ at project root:
lib/
├── tracked_db.py     # Tracked database operations (ALWAYS USE THIS)
└── change_logger.py  # Change log utilities
```

## Data Loading

### `load_complaints(flatten_llm=True)`
Returns DataFrame with complaint data. Key columns:
- `case_number`, `date`, `year`, `respondent`, `type`
- `date_parsed` - datetime version of date
- `llm_summary`, `llm_category`, `llm_specialty`, `llm_procedure`
- `llm_drugs` - list of medications
- `llm_complainants` - list of {age, sex} dicts
- `is_amended`, `amendment_summary`, `original_complaint`

### `load_settlements(flatten_llm=True)`
Returns DataFrame with resolution data. Key columns:
- `case_numbers` - array of case numbers this resolves
- `resolution_outcome` - "Settlement" or "Hearing"
- `llm_license_action` - revoked, suspended, surrendered, probation, reprimand, none
- `llm_fine_amount`, `llm_investigation_costs`
- `llm_probation_months`, `llm_cme_hours`, `llm_cme_topic`
- `llm_violations_admitted` - list of {nrs_code, description}

### `load_merged()`
Complaints joined with their settlement data. Adds columns:
- `has_settlement` - boolean
- `settlement_date`, `settlement_date_parsed`
- `settlement_license_action`, `settlement_fine_amount`, etc.
- `days_to_resolution` - days between complaint and resolution

### `load_license_only()`
License-only filings (suspensions, surrenders not tied to a case number).

## Categories

Values in `llm_category`:
- Controlled Substances
- Impairment
- License Violation
- Malpractice - Diagnosis
- Malpractice - Medication
- Malpractice - Surgical Error
- Malpractice - Treatment
- Other
- Sexual Misconduct
- Unprofessional Conduct

## Adding New Analyses

1. Create a script in `scripts/` that uses `load_data.py`
2. Store any generated data in `output/`
3. External datasets go in `datasets/`

## Environment

Requires `MONGODB_URI` in `.env` file (loaded automatically via python-dotenv).
