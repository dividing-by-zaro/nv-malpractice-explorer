"""Test aggregation on 2025 data."""

import json
from aggregate_cases import parse_case_number, aggregate_cases, generate_summary

# Load test data
with open("data/test_2025.json") as f:
    filings = json.load(f)

# Wrap in expected format
print(f"Testing with {len(filings)} filings from 2025\n")

# Test case number parsing
print("Case number parsing examples:")
test_cases = ["25-8654-1", "25-20296-1", "08-1234-2", "", "invalid"]
for tc in test_cases:
    case_id, doc_num = parse_case_number(tc)
    print(f"  '{tc}' -> case_id='{case_id}', doc_number={doc_num}")

# Run aggregation
print("\nRunning aggregation...")
result = aggregate_cases(filings)
cases = result["cases"]
unmatched = result["unmatched"]

print(f"  Cases: {len(cases)}")
print(f"  Unmatched: {len(unmatched)}")

# Show some cases with multiple documents
multi_doc_cases = [c for c in cases if c["document_count"] > 1]
print(f"\nCases with multiple documents: {len(multi_doc_cases)}")

if multi_doc_cases:
    print("\nExample case with multiple documents:")
    example = multi_doc_cases[0]
    print(f"  Case ID: {example['case_id']}")
    print(f"  Respondent: {example['respondent']}")
    print(f"  Documents:")
    for doc in example["documents"]:
        print(f"    - [{doc['doc_number']}] {doc['type']} ({doc['date']})")

# Summary
summary = generate_summary(cases, unmatched)
print(f"\nDocument types found:")
for doc_type, count in list(summary["document_types"].items())[:5]:
    print(f"  {doc_type}: {count}")
