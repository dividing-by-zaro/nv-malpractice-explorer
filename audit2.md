## Data Validation Plan

This document outlines the validation strategy for the Nevada Medical Malpractice Explorer before public release.

---

## Part 1: Manual Audit (Gold Standard Sample)

### Objective
Create a benchmark dataset of 50-100 manually verified documents to measure extraction accuracy and identify systematic errors.

### Sample Selection

**Target: 80 documents total**
- 40 complaints (diverse years, types, specialties)
- 40 settlements (mix of outcomes and penalty types)

**Stratified sampling criteria:**
- Year distribution: 10% from 2008-2012, 30% from 2013-2018, 60% from 2019-2025 (weighted toward recent)
- Include at least 5 amended complaints
- Include at least 5 cases with multiple complainants
- Include settlements with: fines only, probation only, revocation/surrender, CME requirements

### Fields to Validate

**Complaints (critical fields):**
| Field | Priority | Why it matters |
|-------|----------|----------------|
| `respondent` | High | Identity accuracy |
| `case_number` | High | Linkage to settlements |
| `specialty` | Medium | Analysis grouping |
| `category` | Medium | Analysis grouping |
| `date` | Medium | Timeline accuracy |
| `num_complainants` | Low | Statistical accuracy |

**Settlements (critical fields):**
| Field | Priority | Why it matters |
|-------|----------|----------------|
| `case_numbers` | High | Links to correct complaints |
| `license_action` | High | Most consequential field |
| `fine_amount` | High | Quantitative accuracy |
| `probation_months` | Medium | Penalty severity |
| `cme_hours` | Low | Penalty details |
| `violations_admitted` | Low | Qualitative summary |

### Audit Process

**Step 1: Generate sample list**
```bash
uv run python scripts/audit/generate_sample.py --count 80
```
Script should output a CSV with `_id`, `case_number`, `pdf_url`, `type` (complaint/settlement).

**Step 2: Manual review**
For each document:
1. Open the source PDF
2. Read and extract ground truth values for each field
3. Compare to database values
4. Record: match (✓), mismatch (✗), or ambiguous (?)

**Step 3: Record results**
Use a spreadsheet or JSON file with structure:
```json
{
  "document_id": "...",
  "case_number": "19-28023-1",
  "type": "complaint",
  "fields": {
    "respondent": { "extracted": "John Smith, MD", "actual": "John Smith, MD", "match": true },
    "specialty": { "extracted": "Internal Medicine", "actual": "Cardiology", "match": false, "note": "Subspecialty mapping issue" }
  }
}
```

**Step 4: Calculate metrics**
- Per-field accuracy rate
- Overall document accuracy (all critical fields correct)
- Error categorization (OCR error, LLM hallucination, ambiguous source, mapping issue)

### Time Estimate
- Sample generation: 30 min (scripting)
- Manual review: 5-10 min per document × 80 = 7-13 hours
- Analysis and writeup: 2 hours
- **Total: 10-16 hours**

### Acceptance Criteria
Before publishing:
- `license_action`: ≥95% accuracy
- `fine_amount`: ≥95% accuracy
- `respondent`: ≥98% accuracy
- `case_number` linkage: ≥98% accuracy

If below threshold: investigate errors, fix extraction prompts, re-run affected documents.

---

## Part 2: Automated Validation Checks

### Implementation Plan

Create a validation script that runs against the full database and generates a report.

**Location:** `scripts/validation/run_checks.py`

### Check Categories

#### 2.1 Format Validation

| Check | Field | Rule | Severity |
|-------|-------|------|----------|
| Case number format | `case_number` | Matches `^\d{2}-\d{4,5}-\d+$` | Error |
| Date format | `date` | Valid M/D/YYYY, year 2005-2026 | Error |
| Fine format | `fine_amount` | Number ≥ 0 or null | Error |
| License action vocab | `license_action` | In allowed set: revoked, suspended, surrendered, probation, reprimand, none, null | Error |
| Specialty vocab | `specialty` | In 24 NPDB categories | Warning |
| Category vocab | `category` | In defined categories | Warning |

#### 2.2 Range Validation

| Check | Field | Rule | Severity |
|-------|-------|------|----------|
| Probation range | `probation_months` | 0-120 (0-10 years) | Warning |
| CME hours range | `cme_hours` | 0-100 | Warning |
| Fine range | `fine_amount` | 0-500,000 | Warning |
| Year range | `year` | 2005-2026 | Error |
| Date not future | `date` | ≤ today | Error |

#### 2.3 Consistency Checks (Settlements)

| Check | Rule | Severity |
|-------|------|----------|
| Non-negative fine | `fine_amount >= 0` | Error |
| Probation implies action | If `probation_months > 0`, `license_action` should be "probation" | Warning |
| CME typically standard | `cme_hours` in {5, 10, 15, 20, 40} or null | Info |
| Has case numbers | `case_numbers` array not empty | Error |

#### 2.4 Inter-Document Validation

| Check | Rule | Severity |
|-------|------|----------|
| Settlement links exist | All `complaint_ids` in settlement exist in complaints collection | Error |
| Case number match | Settlement `case_numbers` match linked complaint `case_number` | Error |
| Respondent consistency | Same `case_number` → same `respondent` across documents | Warning |
| Amended references valid | `original_complaint` exists and has same respondent | Warning |
| Resolution after complaint | Settlement date ≥ complaint date (when both available) | Warning |

### Output Format

Generate both JSON (for programmatic use) and HTML (for review).

```json
{
  "run_date": "2025-01-13T10:00:00Z",
  "summary": {
    "total_documents": 1593,
    "documents_with_errors": 23,
    "documents_with_warnings": 87,
    "error_rate": "1.4%",
    "warning_rate": "5.5%"
  },
  "by_check": {
    "case_number_format": { "passed": 1590, "failed": 3, "severity": "error" },
    "fine_non_negative": { "passed": 658, "failed": 2, "severity": "error" }
  },
  "failures": [
    {
      "document_id": "...",
      "case_number": "19-28023-1",
      "check": "case_number_format",
      "message": "Invalid format: '19-28023'",
      "severity": "error"
    }
  ]
}
```

### Implementation Steps

1. **Create validation framework** (`lib/validation.py`)
   - Base `Check` class with `run(document) -> Result`
   - `ValidationRunner` that executes all checks
   - Result aggregation and reporting

2. **Implement individual checks** (`scripts/validation/checks/`)
   - `format_checks.py` - regex and type validation
   - `range_checks.py` - numerical bounds
   - `consistency_checks.py` - intra-document logic
   - `cross_document_checks.py` - inter-document relationships

3. **Create report generator** (`scripts/validation/report.py`)
   - JSON output for CI/automation
   - HTML output for human review
   - Summary statistics

4. **Add to app** (`app.py`, `static/index.html`)
   - New "Data Quality" section
   - Show pass/fail rates by check category
   - Link to full report

### Time Estimate
- Validation framework: 2-3 hours
- Individual checks: 3-4 hours
- Report generator: 2 hours
- App integration: 1-2 hours
- **Total: 8-11 hours**

---

## Part 3: Reporting & Transparency

### Public-Facing Data Quality Section

Add to the app's UI:

```
Data Quality

This dataset uses LLM extraction from scanned PDFs.
Accuracy validated against 80 manually reviewed documents.

Field Accuracy:
- License action: 96% (38/40 settlements correct)
- Fine amount: 95% (38/40 settlements correct)
- Respondent name: 98% (78/80 documents correct)
- Case linkage: 100% (40/40 settlements linked correctly)

Automated Checks:
- 1,593 documents scanned
- 23 documents flagged with errors (1.4%)
- 87 documents flagged with warnings (5.5%)

[View full validation report] [View methodology]

⚠️ Always verify critical information against the source PDF.
```

### Source Linking
Every record in the app should have a visible "View Source PDF" link. Users should never have to trust extracted data alone.

---

## Execution Order

| Phase | Task | Hours | Dependency |
|-------|------|-------|------------|
| 1 | Build sample generation script | 1 | - |
| 2 | Manual audit of 80 documents | 10-13 | Phase 1 |
| 3 | Calculate accuracy metrics | 1 | Phase 2 |
| 4 | Build validation framework | 3 | - |
| 5 | Implement automated checks | 4 | Phase 4 |
| 6 | Generate validation report | 2 | Phase 5 |
| 7 | Add Data Quality section to app | 2 | Phase 3, 6 |
| 8 | Fix critical errors found | 2-4 | Phase 2, 5 |

**Total estimate: 25-30 hours**

Can be parallelized: Phase 1-3 (manual audit) and Phase 4-6 (automated checks) can run concurrently.

---

## Definition of Done

Ready to publish when:
- [ ] 80 documents manually audited
- [ ] Critical field accuracy ≥95%
- [ ] Automated validation runs with <2% error rate
- [ ] All "Error" severity issues resolved or documented
- [ ] Data Quality section visible in app
- [ ] Every record links to source PDF
- [ ] Methodology documented and accessible
