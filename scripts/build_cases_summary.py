#!/usr/bin/env python3
"""
Build a cases summary collection that shows status of each case.

Usage:
    uv run python scripts/build_cases_summary.py
"""

import json
import os
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()


def main():
    # Connect to MongoDB
    mongo_uri = os.environ.get("MONGODB_URI")
    if not mongo_uri:
        raise ValueError("MONGODB_URI environment variable is required")

    client = MongoClient(mongo_uri)
    db = client["malpractice"]

    complaints = db["complaints"]
    settlements = db["settlements"]
    cases_summary = db["cases_summary"]

    # Load filings metadata
    data_dir = Path("data")
    with open(data_dir / "filings_normalized.json", "r") as f:
        filings_data = json.load(f)

    # Group filings by case number
    cases = {}
    for filing in filings_data["filings"]:
        case_num = filing["case_number"]
        if case_num not in cases:
            cases[case_num] = {
                "case_number": case_num,
                "respondent": filing["respondent"],
                "year": filing["year"],
                "filings": []
            }
        cases[case_num]["filings"].append({
            "type": filing["type"],
            "date": filing["date"],
            "title": filing["title"]
        })

    print(f"Found {len(cases)} unique cases in filings")

    # Build complaint lookup
    complaint_docs = {}
    for doc in complaints.find({}, {"case_number": 1, "ocr_failed": 1, "llm_extracted": 1}):
        complaint_docs[doc["case_number"]] = {
            "has_text": not doc.get("ocr_failed", False),
            "has_extraction": "llm_extracted" in doc and doc["llm_extracted"] is not None,
            "ocr_failed": doc.get("ocr_failed", False)
        }

    print(f"Found {len(complaint_docs)} complaints in DB")

    # Build settlement lookup
    settlement_docs = {}
    for doc in settlements.find({}, {"case_number": 1, "ocr_failed": 1, "llm_extracted": 1}):
        settlement_docs[doc["case_number"]] = {
            "has_text": not doc.get("ocr_failed", False),
            "has_extraction": "llm_extracted" in doc and doc["llm_extracted"] is not None,
            "ocr_failed": doc.get("ocr_failed", False)
        }

    print(f"Found {len(settlement_docs)} settlements in DB")

    # Build summary for each case
    summaries = []
    for case_num, case_data in cases.items():
        complaint_status = complaint_docs.get(case_num, {})
        settlement_status = settlement_docs.get(case_num, {})

        # Determine filing types present
        filing_types = [f["type"] for f in case_data["filings"]]
        has_complaint_filing = any("Complaint" in t for t in filing_types)
        has_settlement_filing = any("Settlement" in t for t in filing_types)

        summary = {
            "case_number": case_num,
            "respondent": case_data["respondent"],
            "year": case_data["year"],
            "num_filings": len(case_data["filings"]),
            "filing_types": filing_types,

            # Complaint status
            "has_complaint_filing": has_complaint_filing,
            "complaint_in_db": case_num in complaint_docs,
            "complaint_has_text": complaint_status.get("has_text", False),
            "complaint_ocr_failed": complaint_status.get("ocr_failed", False),
            "complaint_has_extraction": complaint_status.get("has_extraction", False),

            # Settlement status
            "has_settlement_filing": has_settlement_filing,
            "settlement_in_db": case_num in settlement_docs,
            "settlement_has_text": settlement_status.get("has_text", False),
            "settlement_ocr_failed": settlement_status.get("ocr_failed", False),
            "settlement_has_extraction": settlement_status.get("has_extraction", False),

            "updated_at": datetime.now(timezone.utc)
        }
        summaries.append(summary)

    # Clear and rebuild collection
    cases_summary.drop()
    cases_summary.create_index("case_number", unique=True)
    cases_summary.create_index("year")
    cases_summary.create_index("complaint_has_extraction")
    cases_summary.create_index("settlement_has_extraction")

    if summaries:
        cases_summary.insert_many(summaries)

    print(f"\nInserted {len(summaries)} case summaries")

    # Print stats
    total = len(summaries)

    has_complaint_filing = sum(1 for s in summaries if s["has_complaint_filing"])
    complaint_in_db = sum(1 for s in summaries if s["complaint_in_db"])
    complaint_has_text = sum(1 for s in summaries if s["complaint_has_text"])
    complaint_ocr_failed = sum(1 for s in summaries if s["complaint_ocr_failed"])
    complaint_extracted = sum(1 for s in summaries if s["complaint_has_extraction"])

    has_settlement_filing = sum(1 for s in summaries if s["has_settlement_filing"])
    settlement_in_db = sum(1 for s in summaries if s["settlement_in_db"])
    settlement_has_text = sum(1 for s in summaries if s["settlement_has_text"])
    settlement_ocr_failed = sum(1 for s in summaries if s["settlement_ocr_failed"])
    settlement_extracted = sum(1 for s in summaries if s["settlement_has_extraction"])

    both_extracted = sum(1 for s in summaries if s["complaint_has_extraction"] and s["settlement_has_extraction"])

    print("\n" + "=" * 60)
    print("CASES SUMMARY")
    print("=" * 60)
    print(f"Total unique cases: {total}")
    print()
    print("COMPLAINTS:")
    print(f"  Has complaint filing:    {has_complaint_filing:>4} ({100*has_complaint_filing/total:.1f}%)")
    print(f"  In database:             {complaint_in_db:>4} ({100*complaint_in_db/total:.1f}%)")
    print(f"  Has text (OCR success):  {complaint_has_text:>4} ({100*complaint_has_text/total:.1f}%)")
    print(f"  OCR failed:              {complaint_ocr_failed:>4} ({100*complaint_ocr_failed/total:.1f}%)")
    print(f"  Has LLM extraction:      {complaint_extracted:>4} ({100*complaint_extracted/total:.1f}%)")
    print()
    print("SETTLEMENTS:")
    print(f"  Has settlement filing:   {has_settlement_filing:>4} ({100*has_settlement_filing/total:.1f}%)")
    print(f"  In database:             {settlement_in_db:>4} ({100*settlement_in_db/total:.1f}%)")
    print(f"  Has text (OCR success):  {settlement_has_text:>4} ({100*settlement_has_text/total:.1f}%)")
    print(f"  OCR failed:              {settlement_ocr_failed:>4} ({100*settlement_ocr_failed/total:.1f}%)")
    print(f"  Has LLM extraction:      {settlement_extracted:>4} ({100*settlement_extracted/total:.1f}%)")
    print()
    print(f"Both complaint & settlement extracted: {both_extracted} ({100*both_extracted/total:.1f}%)")


if __name__ == "__main__":
    main()
