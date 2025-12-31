"""
Validate filings and surface malformed titles/case numbers for manual review.

Checks for:
- Case numbers with leading zeros (e.g., -01 instead of -1)
- Titles starting with "unknown"
- Case numbers that don't match expected pattern
- Empty/missing case numbers
- Unusual document types
- Parsing issues (title didn't split into 3 parts)
"""

import json
import re
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path("data")


def check_leading_zeros(case_number: str) -> str | None:
    """Check if document number has leading zeros."""
    match = re.match(r'^(\d+-\d+)-0(\d+)$', case_number)
    if match:
        return f"Leading zero in doc number: '{case_number}' -> should be '{match.group(1)}-{match.group(2)}'"
    return None


def check_unknown_prefix(title: str) -> str | None:
    """Check if title starts with 'unknown' or similar."""
    lower = title.lower()
    if lower.startswith("unknown") or lower.startswith("unkonwn"):
        return f"Unknown prefix in title"
    return None


def check_case_number_format(case_number: str) -> str | None:
    """Check if case number matches expected format."""
    if not case_number:
        return "Empty case number"

    # Expected formats:
    # - XX-XXXXX-X (standard case number)
    # - LICENSE-XXXXX (voluntary surrender, numeric)
    # - LICENSE-RCXXXX or LICENSE-PAXXXX (alphanumeric license)
    # - XX-R-X (remediation cases)
    # - XX-00000-X (denials)
    if re.match(r'^\d+-\d+-\d+$', case_number):
        return None  # Standard format OK
    if re.match(r'^LICENSE-[A-Za-z]*\d+$', case_number):
        return None  # License format OK (including RC, PA prefixes)
    if re.match(r'^\d+-R-\d+$', case_number):
        return None  # Remediation format OK
    if re.match(r'^\d+-00000-\d+$', case_number):
        return None  # Denial format OK

    return f"Unexpected format: '{case_number}'"


def check_title_parsing(filing: dict) -> str | None:
    """Check if title was parsed correctly into 3 parts."""
    title = filing.get("title", "")
    doc_type = filing.get("type", "")
    respondent = filing.get("respondent", "")
    case_number = filing.get("case_number", "")

    # If any field is empty but title exists, parsing may have failed
    if title and (not doc_type or not respondent or not case_number):
        missing = []
        if not doc_type:
            missing.append("type")
        if not respondent:
            missing.append("respondent")
        if not case_number:
            missing.append("case_number")
        return f"Incomplete parsing - missing: {', '.join(missing)}"

    return None


def check_unusual_characters(title: str) -> str | None:
    """Check for unusual characters that might indicate encoding issues."""
    if "â€" in title or "Ã" in title or "â€™" in title:
        return "Possible encoding issue"
    return None


def validate_filings(filings: list[dict]) -> dict:
    """Run all validations and return issues grouped by type."""
    issues = defaultdict(list)

    for filing in filings:
        title = filing.get("title", "")
        case_number = filing.get("case_number", "")
        year = filing.get("year", "")

        # Run checks
        checks = [
            ("leading_zeros", check_leading_zeros(case_number)),
            ("unknown_prefix", check_unknown_prefix(title)),
            ("case_format", check_case_number_format(case_number)),
            ("title_parsing", check_title_parsing(filing)),
            ("encoding", check_unusual_characters(title)),
        ]

        for check_name, result in checks:
            if result:
                issues[check_name].append({
                    "year": year,
                    "title": title,
                    "case_number": case_number,
                    "type": filing.get("type", ""),
                    "respondent": filing.get("respondent", ""),
                    "issue": result,
                    "pdf_url": filing.get("pdf_url", ""),
                })

    return dict(issues)


def print_issues(issues: dict):
    """Print issues in a readable format."""
    total = sum(len(v) for v in issues.values())
    print(f"\n{'='*60}")
    print(f"VALIDATION REPORT - {total} issues found")
    print(f"{'='*60}")

    if total == 0:
        print("\nNo issues found!")
        return

    # Priority order for display
    priority = ["leading_zeros", "unknown_prefix", "encoding", "case_format", "title_parsing"]

    for check_name in priority:
        if check_name not in issues:
            continue

        items = issues[check_name]
        print(f"\n\n## {check_name.upper().replace('_', ' ')} ({len(items)} issues)")
        print("-" * 50)

        for item in items[:20]:  # Show first 20
            print(f"\n  Year: {item['year']}")
            print(f"  Title: {item['title'][:80]}{'...' if len(item['title']) > 80 else ''}")
            print(f"  Case#: {item['case_number']}")
            print(f"  Issue: {item['issue']}")

        if len(items) > 20:
            print(f"\n  ... and {len(items) - 20} more")


def main():
    # Try normalized data first, then raw filings, then test data
    normalized_path = DATA_DIR / "filings_normalized.json"
    filings_path = DATA_DIR / "filings.json"
    test_path = DATA_DIR / "test_2025.json"

    if normalized_path.exists():
        with open(normalized_path) as f:
            data = json.load(f)
        filings = data.get("filings", [])
        print(f"Loaded {len(filings)} filings from filings_normalized.json")
    elif filings_path.exists():
        with open(filings_path) as f:
            data = json.load(f)
        filings = data.get("filings", [])
        print(f"Loaded {len(filings)} filings from filings.json")
    elif test_path.exists():
        with open(test_path) as f:
            filings = json.load(f)
        print(f"Loaded {len(filings)} filings from test_2025.json")
    else:
        print("Error: No filings data found. Run scraper.py first.")
        return

    # Validate
    issues = validate_filings(filings)

    # Print report
    print_issues(issues)

    # Save detailed report
    report_path = DATA_DIR / "validation_report.json"
    with open(report_path, "w") as f:
        json.dump(issues, f, indent=2)
    print(f"\n\nDetailed report saved to: {report_path}")


if __name__ == "__main__":
    main()
