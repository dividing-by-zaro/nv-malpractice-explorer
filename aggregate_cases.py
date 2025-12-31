"""
Aggregate filings into cases by grouping documents with the same case_id.

Case number format: XX-XXXXX-N
- XX-XXXXX = case_id (shared across related documents)
- N = document number within the case (1=complaint, 2=settlement, etc.)
"""

import json
import re
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path("data")


def parse_case_number(case_number: str) -> tuple[str | None, int | None]:
    """
    Parse a case number into case_id and document number.

    Examples:
        "25-8654-1" -> ("25-8654", 1)
        "25-20296-1" -> ("25-20296", 1)
        "08-1234-2" -> ("08-1234", 2)
        "" -> (None, None)
    """
    if not case_number:
        return None, None

    # Match pattern: digits-digits-digits
    match = re.match(r'^(\d+-\d+)-(\d+)$', case_number)
    if match:
        case_id = match.group(1)
        doc_number = int(match.group(2))
        return case_id, doc_number

    # If it doesn't match the expected pattern, return the whole thing as case_id
    return case_number, None


def aggregate_cases(filings: list[dict]) -> dict:
    """
    Group filings by case_id and create case objects.

    Returns dict with:
    - cases: list of case objects with their documents
    - unmatched: filings that couldn't be parsed into the case structure
    """
    cases_map = defaultdict(lambda: {"documents": [], "respondents": set()})
    unmatched = []

    for filing in filings:
        case_number = filing.get("case_number", "")
        case_id, doc_number = parse_case_number(case_number)

        if case_id is None:
            unmatched.append(filing)
            continue

        # Build document entry
        doc = {
            "doc_number": doc_number,
            "type": filing.get("type", ""),
            "date": filing.get("date", ""),
            "filing_year": filing.get("year"),
            "respondent": filing.get("respondent", ""),
            "title": filing.get("title", ""),
            "pdf_url": filing.get("pdf_url", ""),
            "relative_path": filing.get("relative_path", ""),
        }

        cases_map[case_id]["documents"].append(doc)
        if filing.get("respondent"):
            cases_map[case_id]["respondents"].add(filing["respondent"])

    # Convert to list format
    cases = []
    for case_id, data in sorted(cases_map.items()):
        # Sort documents by doc_number
        docs = sorted(
            data["documents"],
            key=lambda d: (d["doc_number"] or 0, d["date"])
        )

        # Use the most common respondent (should all be the same)
        respondents = list(data["respondents"])
        primary_respondent = respondents[0] if respondents else ""

        case = {
            "case_id": case_id,
            "respondent": primary_respondent,
            "document_count": len(docs),
            "documents": docs,
        }
        cases.append(case)

    return {
        "cases": cases,
        "unmatched": unmatched,
    }


def generate_summary(cases: list[dict], unmatched: list[dict]) -> dict:
    """Generate summary statistics."""
    total_docs = sum(c["document_count"] for c in cases)

    # Count cases by number of documents
    doc_count_distribution = defaultdict(int)
    for case in cases:
        doc_count_distribution[case["document_count"]] += 1

    # Count document types
    type_counts = defaultdict(int)
    for case in cases:
        for doc in case["documents"]:
            type_counts[doc["type"]] += 1

    return {
        "total_cases": len(cases),
        "total_documents": total_docs,
        "unmatched_filings": len(unmatched),
        "cases_by_document_count": dict(sorted(doc_count_distribution.items())),
        "document_types": dict(sorted(type_counts.items(), key=lambda x: -x[1])),
    }


def main():
    # Load filings
    filings_path = DATA_DIR / "filings.json"
    if not filings_path.exists():
        print(f"Error: {filings_path} not found. Run scraper.py first.")
        return

    with open(filings_path) as f:
        data = json.load(f)

    filings = data.get("filings", [])
    print(f"Loaded {len(filings)} filings")

    # Aggregate into cases
    result = aggregate_cases(filings)
    cases = result["cases"]
    unmatched = result["unmatched"]

    print(f"Grouped into {len(cases)} cases")
    print(f"Unmatched filings: {len(unmatched)}")

    # Generate summary
    summary = generate_summary(cases, unmatched)

    # Save cases
    output = {
        "summary": summary,
        "cases": cases,
        "unmatched": unmatched,
    }

    output_path = DATA_DIR / "cases.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nSaved to {output_path}")

    # Print summary
    print(f"\n{'='*50}")
    print("Summary:")
    print(f"  Total cases: {summary['total_cases']}")
    print(f"  Total documents: {summary['total_documents']}")
    print(f"  Unmatched: {summary['unmatched_filings']}")

    print(f"\nCases by document count:")
    for count, num_cases in sorted(summary["cases_by_document_count"].items()):
        label = "document" if count == 1 else "documents"
        print(f"  {count} {label}: {num_cases} cases")

    print(f"\nTop document types:")
    for doc_type, count in list(summary["document_types"].items())[:10]:
        print(f"  {doc_type}: {count}")


if __name__ == "__main__":
    main()
