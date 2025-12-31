# Nevada Medical Malpractice Explorer

Tools to scrape, process, and analyze public medical malpractice filings from the Nevada State Board of Medical Examiners (2008-2025).

## Setup

```bash
uv sync
brew install ocrmypdf  # for PDF text extraction
```

## Data Pipeline

```bash
# 1. Scrape filings metadata and PDFs
uv run python scraper.py

# 2. Normalize data (fix formatting issues, expand multi-case entries)
uv run python normalize_filings.py

# 3. Validate data quality
uv run python validate_filings.py

# 4. Aggregate into cases (group related documents)
uv run python aggregate_cases.py

# 5. OCR PDFs to extract text
uv run python ocr_pdfs.py
```

## Directory Structure

```
data/
├── filings.json              # Raw scraped metadata
├── filings_normalized.json   # Cleaned metadata (1,594 filings)
├── cases.json                # Grouped by case
pdfs/{year}/                  # Original scanned PDFs
pdfs_ocr/{year}/              # Searchable PDFs (after OCR)
text/{year}/                  # Extracted plain text
```

## Data Schema

Each filing contains:
- `case_number`: e.g., "25-8654-1" (year-case-document)
- `type`: Complaint, Settlement Agreement and Order, etc.
- `respondent`: Provider name and credentials
- `date`: Filing date
- `year`: Filing year
- `pdf_url`: Source URL
