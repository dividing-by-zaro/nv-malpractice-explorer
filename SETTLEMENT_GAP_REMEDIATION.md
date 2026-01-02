# Settlement Gap Remediation Plan

## COMPLETED - 2024-01-01

### Results Summary

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Settlements in MongoDB | 442 | 604 | +162 |
| Complaints without settlement | 224 | 89 | -135 |
| Settlement coverage | ~67% | 86.9% | +20% |

The remaining 89 complaints are **truly unsettled** - no settlement PDF exists in source data (mostly 2023-2025 pending cases).

---

## Problem Summary (Original)

224 complaints in MongoDB lacked settlements. Root causes:

| Cause | Count | Status |
|-------|-------|--------|
| Missing settlement types in code | ~125 | FIXED |
| Truly unsettled (no PDF exists) | 89 | N/A (legitimate) |
| Have text, not processed | 33 | FIXED |
| OCR failures (no text file) | 8-11 | FIXED (10 OCR'd) |

---

## Step 1: Fix OCR Failures

Settlement PDFs missing text files (OCR timed out on large documents):

```
pdfs/2008/07-7759-1_Settlement__Waiver_and_Consent.pdf
pdfs/2019/19-28023-1_Settlement_Agreement_and_Order.pdf
pdfs/2019/18-10032-1_Settlement_Agreement_and_Order.pdf
pdfs/2019/19-35720-1_Settlement_Agreement_and_Order.pdf
pdfs/2019/19-28023-3_Settlement_Agreement_and_Order.pdf
pdfs/2019/19-46451-1_Settlement_Agreement_and_Order.pdf
pdfs/2019/19-28023-2_Settlement_Agreement_and_Order.pdf
pdfs/2018/18-11308-1_Settlement_Agreement_and_Order.pdf
pdfs/2018/18-350-1_Settlement_Agreement_and_Order.pdf
pdfs/2018/18-8756-1_Settlement_Agreement_and_Order.pdf
```

### 1.1 Check file sizes to confirm timeout hypothesis

```bash
# List settlement PDFs without text files and their sizes
```

### 1.2 Run OCR manually with no timeout

For each file:
```bash
ocrmypdf --sidecar text/{year}/{case}_Settlement_Agreement_and_Order.txt \
    --rotate-pages --deskew --clean --force-ocr -l eng --jobs 2 \
    pdfs/{year}/{case}_Settlement_Agreement_and_Order.pdf \
    pdfs_ocr/{year}/{case}_Settlement_Agreement_and_Order.pdf
```

### 1.3 Clean the new text files

```bash
uv run python scripts/clean_text.py --text-dir text/ --apply
```

---

## Step 2: Fix Settlement Type Filter

Update `scripts/process_settlements.py` line 66-74 to include all settlement types.

### Current (missing 125 documents):
```python
settlement_types = [
    "Settlement Agreement and Order",
    "Settlement, Waiver and Consent Agreement",
    "Settlement Agreement",
    "Amended Settlement Agreement and Order",
]
```

### Updated (captures all):
```python
settlement_types = [
    "Settlement Agreement and Order",
    "Settlement, Waiver and Consent Agreement",
    "Settlement, Waiver and Consent Agreement and Order",  # +112
    "Settlement Agreement",
    "Amended Settlement Agreement and Order",
    "First Amended Settlement Agreement and Order",
    "Settlement Agreement and Order Lifting Suspension",
    "Stipulation and Settlement, Waiver and Consent Agreement and Order",
    "Consent Agreement for Revocation of License",
    # Modification orders (link to existing settlements)
    "Order Modifying Previously Approved Settlement Agreement",
    "Order Modifying Terms of Previously Approved Settlement Agreement",
    "Order Modifying Conditions of Settlement Agreement",
    "Order Amending Settlement Agreement",
    "Stipulation and Order Amending Terms of Settlement Agreement",
    "Addendum to Previously Adopted Settlement",
    "Order Vacating Remaining Term of Previously Adopted Settlement, Waiver and Consent Agreement",
]
```

---

## Step 3: Re-run Settlement Processing

```bash
# Dry run first to see what will be processed
uv run python scripts/process_settlements.py --dry-run

# Process all unprocessed settlements
uv run python scripts/process_settlements.py
```

---

## Step 4: Verify Results

```bash
# Check final counts
uv run python -c "
from pymongo import MongoClient
from dotenv import load_dotenv
import os

load_dotenv()
client = MongoClient(os.getenv('MONGODB_URI'))
db = client.malpractice

complaints = db.complaints.count_documents({})
settlements = db.settlements.count_documents({})
with_llm = db.settlements.count_documents({'llm_extracted': {'$exists': True}})

print(f'Complaints: {complaints}')
print(f'Settlements: {settlements}')
print(f'With LLM extraction: {with_llm}')
"
```

---

## Expected Outcome

After remediation:
- ~567 settlements in MongoDB (up from 442)
- ~89 complaints legitimately without settlement (recent/dismissed cases)
- ~112 complaints with newly matched settlements from "Waiver and Consent" type
