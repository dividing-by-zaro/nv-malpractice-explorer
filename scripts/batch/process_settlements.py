#!/usr/bin/env python3
"""
Process settlement documents using OpenAI GPT-4 and store results in MongoDB.

Usage:
    uv run scripts/process_settlements.py                    # Process all unprocessed settlements
    uv run scripts/process_settlements.py --limit 10         # Process only 10 settlements
    uv run scripts/process_settlements.py --reprocess        # Reprocess all settlements
    uv run scripts/process_settlements.py --dry-run          # Preview without processing
"""

import json
import os
import argparse
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from openai import OpenAI
from pymongo import MongoClient

load_dotenv()

# Load the extraction prompt
PROMPT_PATH = Path(__file__).parent / "prompts" / "settlement_extraction.md"


def load_prompt() -> str:
    """Load the extraction prompt from file."""
    with open(PROMPT_PATH, "r") as f:
        return f.read()


def get_mongo_client() -> MongoClient:
    """Create MongoDB client from environment variable."""
    mongo_uri = os.environ.get("MONGODB_URI")
    if not mongo_uri:
        raise ValueError("MONGODB_URI environment variable is required")
    return MongoClient(mongo_uri)


def get_openai_client() -> OpenAI:
    """Create OpenAI client from environment variable."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is required")
    return OpenAI(api_key=api_key)


def load_filings_metadata(data_dir: Path) -> list[dict]:
    """Load filings metadata from JSON file."""
    filings_path = data_dir / "filings_normalized.json"
    with open(filings_path, "r") as f:
        data = json.load(f)
    return data["filings"]


def filter_settlements(filings: list[dict]) -> list[dict]:
    """
    Filter filings to only include settlements, deduplicated by pdf_url.

    Multi-case settlements (e.g., "Case Nos 19-28023-1, 19-28023-2, 19-28023-3")
    are expanded into multiple entries in filings_normalized.json, but they all
    share the same PDF. We consolidate them into a single entry with all case_numbers.
    """
    settlement_types = [
        # Primary settlement types
        "Settlement Agreement and Order",
        "Settlement, Waiver and Consent Agreement",
        "Settlement, Waiver and Consent Agreement and Order",  # +112 documents
        "Settlement Agreement",
        # Amended settlements
        "Amended Settlement Agreement and Order",
        "First Amended Settlement Agreement and Order",
        "Settlement Agreement and Order Lifting Suspension",
        # Combined stipulation + settlement
        "Stipulation and Settlement, Waiver and Consent Agreement and Order",
        # Consent agreements (functionally settlements)
        "Consent Agreement for Revocation of License",
        # Modification orders (update existing settlements)
        "Order Modifying Previously Approved Settlement Agreement",
        "Order Modifying Terms of Previously Approved Settlement Agreement",
        "Order Modifying Conditions of Settlement Agreement",
        "Order Amending Settlement Agreement",
        "Stipulation and Order Amending Terms of Settlement Agreement",
        "Addendum to Previously Adopted Settlement",
        "Order Vacating Remaining Term of Previously Adopted Settlement, Waiver and Consent Agreement",
        # Findings of Fact (contested cases that went to hearing)
        "Findings of Fact, Conclusions of Law and Order",
        "Findings of Fact, Conclusions of Law, and Order",  # Variant with comma
        "Amended Findings of Fact, Conclusions of Law and Order",
        "Findings of Fact, Conclustions of Law and Order",  # Typo in source data
    ]

    # Also match types that have extra text appended (e.g., doctor name in type field)
    def matches_settlement_type(filing_type: str) -> bool:
        if filing_type in settlement_types:
            return True
        # Handle malformed types like "Findings of Fact, Conclusions of Law and Order Elliott Schmerler, MD"
        for st in settlement_types:
            if filing_type.startswith(st):
                return True
        return False

    # Filter to only settlements
    all_settlements = [f for f in filings if matches_settlement_type(f.get("type", ""))]

    # Deduplicate by pdf_url, collecting all case_numbers for each unique PDF
    by_pdf_url: dict[str, dict] = {}
    for filing in all_settlements:
        pdf_url = filing.get("pdf_url", "")
        if not pdf_url:
            # If no pdf_url, use case_number as fallback key
            pdf_url = f"no_url_{filing.get('case_number', 'unknown')}"

        if pdf_url not in by_pdf_url:
            # First time seeing this PDF - create entry with case_numbers array
            entry = filing.copy()
            entry["case_numbers"] = [filing["case_number"]]
            by_pdf_url[pdf_url] = entry
        else:
            # Already seen this PDF - add case_number to array
            existing = by_pdf_url[pdf_url]
            if filing["case_number"] not in existing["case_numbers"]:
                existing["case_numbers"].append(filing["case_number"])

    return list(by_pdf_url.values())


def get_text_file_path(filing: dict, text_dir: Path) -> Path | None:
    """Construct the path to the cleaned text file for a filing.

    For multi-case settlements, the file is named with the first case_number.
    """
    year = filing["year"]
    # Use case_numbers array if available, otherwise fall back to case_number
    case_numbers = filing.get("case_numbers", [filing.get("case_number", "")])
    primary_case_number = case_numbers[0] if case_numbers else filing.get("case_number", "")
    doc_type = filing["type"]

    # Normalize document type for filename
    type_slug = doc_type.replace(" ", "_").replace(",", "")

    year_dir = text_dir / str(year)
    if not year_dir.exists():
        return None

    # Try different filename patterns with primary case number
    patterns = [
        f"{primary_case_number}_{type_slug}.txt",
        f"{primary_case_number}_{type_slug[:30]}.txt",  # Truncated
    ]

    # Search for matching file
    for pattern in patterns:
        candidate = year_dir / pattern
        if candidate.exists():
            return candidate

    # Fallback: search for files starting with any of the case numbers
    for case_number in case_numbers:
        for txt_file in year_dir.glob(f"{case_number}_*.txt"):
            fname_lower = txt_file.name.lower()
            if "settlement" in fname_lower or "findings" in fname_lower:
                return txt_file

    # Second fallback: handle -1 vs -01 suffix variations
    # e.g., case_number "05-9441-1" should match file "05-9441-01_..."
    for case_number in case_numbers:
        # Extract base (e.g., "05-9441") and try with padded suffix
        parts = case_number.rsplit("-", 1)
        if len(parts) == 2:
            base, suffix = parts
            padded_suffix = suffix.zfill(2)  # "1" -> "01"
            alt_case_number = f"{base}-{padded_suffix}"
            for txt_file in year_dir.glob(f"{alt_case_number}_*.txt"):
                fname_lower = txt_file.name.lower()
                if "settlement" in fname_lower or "findings" in fname_lower:
                    return txt_file

    return None


def read_text_file(path: Path) -> str:
    """Read and return the contents of a text file."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def call_openai(client: OpenAI, system_prompt: str, user_content: str, model: str = "gpt-4o") -> dict:
    """Call OpenAI API and parse JSON response."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        temperature=0.1,
        response_format={"type": "json_object"}
    )

    content = response.choices[0].message.content
    return json.loads(content)


def chunk_text(text: str, max_chars: int = 70000, overlap: int = 500) -> list[str]:
    """Split text into chunks that fit within token limits, with overlap for context."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        if end < len(text):
            # Try to break at a paragraph or sentence boundary
            for sep in ["\n\n", "\n", ". ", " "]:
                boundary = text.rfind(sep, start + max_chars - 5000, end)
                if boundary > start:
                    end = boundary + len(sep)
                    break
        chunks.append(text[start:end])
        start = end - overlap if end < len(text) else end
    return chunks


def merge_extraction_results(results: list[dict]) -> dict:
    """Merge multiple extraction results from chunked processing."""
    if len(results) == 1:
        return results[0]

    merged = {
        "summary": results[0].get("summary", ""),
        "license_action": None,
        "probation_months": None,
        "ineligible_to_reapply_months": None,
        "fine_amount": None,
        "investigation_costs": None,
        "charity_donation": None,
        "costs_payment_deadline_days": None,
        "costs_stayed": False,
        "cme_hours": None,
        "cme_topic": None,
        "cme_deadline_months": None,
        "public_reprimand": False,
        "npdb_report": False,
        "practice_restrictions": [],
        "monitoring_requirements": [],
        "violations_admitted": [],
        "_chunked": True,
        "_chunk_count": len(results),
    }

    # Merge by taking first non-null value for scalars, union for arrays
    for r in results:
        if not merged["license_action"] and r.get("license_action"):
            merged["license_action"] = r["license_action"]
        if not merged["probation_months"] and r.get("probation_months"):
            merged["probation_months"] = r["probation_months"]
        if not merged["ineligible_to_reapply_months"] and r.get("ineligible_to_reapply_months"):
            merged["ineligible_to_reapply_months"] = r["ineligible_to_reapply_months"]
        if not merged["fine_amount"] and r.get("fine_amount"):
            merged["fine_amount"] = r["fine_amount"]
        if not merged["investigation_costs"] and r.get("investigation_costs"):
            merged["investigation_costs"] = r["investigation_costs"]
        if not merged["charity_donation"] and r.get("charity_donation"):
            merged["charity_donation"] = r["charity_donation"]
        if not merged["costs_payment_deadline_days"] and r.get("costs_payment_deadline_days"):
            merged["costs_payment_deadline_days"] = r["costs_payment_deadline_days"]
        if r.get("costs_stayed"):
            merged["costs_stayed"] = True
        if not merged["cme_hours"] and r.get("cme_hours"):
            merged["cme_hours"] = r["cme_hours"]
        if not merged["cme_topic"] and r.get("cme_topic"):
            merged["cme_topic"] = r["cme_topic"]
        if not merged["cme_deadline_months"] and r.get("cme_deadline_months"):
            merged["cme_deadline_months"] = r["cme_deadline_months"]
        if r.get("public_reprimand"):
            merged["public_reprimand"] = True
        if r.get("npdb_report"):
            merged["npdb_report"] = True

        # Merge arrays (deduplicate by converting to string for comparison)
        for restriction in r.get("practice_restrictions", []):
            if restriction not in merged["practice_restrictions"]:
                merged["practice_restrictions"].append(restriction)
        for req in r.get("monitoring_requirements", []):
            if req not in merged["monitoring_requirements"]:
                merged["monitoring_requirements"].append(req)
        for v in r.get("violations_admitted", []):
            # Dedupe by nrs_code
            if not any(existing.get("nrs_code") == v.get("nrs_code") for existing in merged["violations_admitted"]):
                merged["violations_admitted"].append(v)

    return merged


def process_single_settlement(
    filing: dict,
    text_content: str,
    openai_client: OpenAI,
    system_prompt: str
) -> dict:
    """Process a single settlement through the LLM, chunking if necessary."""
    MAX_CHARS = 70000  # ~17.5k tokens, leaving room for prompt and response

    chunks = chunk_text(text_content, max_chars=MAX_CHARS)

    results = []
    for i, chunk in enumerate(chunks):
        chunk_note = f"\n\n[This is part {i+1} of {len(chunks)} of the document]" if len(chunks) > 1 else ""

        user_content = f"""## Metadata

- **Title:** {filing.get('title', 'Unknown')}
- **Respondent:** {filing.get('respondent', 'Unknown')}
- **Case Number:** {filing.get('case_number', 'Unknown')}
- **Date:** {filing.get('date', 'Unknown')}
- **Type:** {filing.get('type', 'Unknown')}{chunk_note}

## Document Text

{chunk}
"""
        result = call_openai(openai_client, system_prompt, user_content)
        results.append(result)

    return merge_extraction_results(results)


def main():
    parser = argparse.ArgumentParser(description="Process settlement documents with LLM")
    parser.add_argument("--limit", type=int, help="Limit number of settlements to process")
    parser.add_argument("--reprocess", action="store_true", help="Reprocess already processed settlements")
    parser.add_argument("--dry-run", action="store_true", help="Preview without processing or storing")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Data directory")
    parser.add_argument("--text-dir", type=Path, default=Path("text"), help="Text files directory")
    parser.add_argument("--model", type=str, default="gpt-4o", help="OpenAI model to use")
    args = parser.parse_args()

    # Load prompt
    print("Loading extraction prompt...")
    system_prompt = load_prompt()

    # Connect to services (skip in dry-run)
    if not args.dry_run:
        print("Connecting to MongoDB...")
        mongo_client = get_mongo_client()
        db = mongo_client["malpractice"]
        settlements_collection = db["settlements"]
        complaints_collection = db["complaints"]

        # Create unique index on pdf_url (one settlement per PDF)
        settlements_collection.create_index("pdf_url", unique=True)
        # Create index on case_numbers for lookups
        settlements_collection.create_index("case_numbers")

        print("Connecting to OpenAI...")
        openai_client = get_openai_client()
    else:
        print("[DRY RUN] Skipping service connections")
        complaints_collection = None

    # Load and filter filings
    print("Loading filings metadata...")
    filings = load_filings_metadata(args.data_dir)
    settlements = filter_settlements(filings)
    print(f"Found {len(settlements)} settlement documents")

    # Filter out already processed (unless reprocessing)
    if not args.dry_run and not args.reprocess:
        processed_pdf_urls = set(
            doc["pdf_url"]
            for doc in settlements_collection.find(
                {"llm_extracted": {"$exists": True}},
                {"pdf_url": 1}
            )
            if doc.get("pdf_url")
        )
        settlements = [s for s in settlements if s.get("pdf_url") not in processed_pdf_urls]
        print(f"{len(settlements)} settlements remaining to process")

    # Apply limit
    if args.limit:
        settlements = settlements[:args.limit]
        print(f"Limited to {len(settlements)} settlements")

    # Process each settlement
    processed = 0
    errors = 0
    skipped = 0

    for i, filing in enumerate(settlements, 1):
        case_numbers = filing.get("case_numbers", [filing.get("case_number", "")])
        primary_case_number = case_numbers[0] if case_numbers else "unknown"
        pdf_url = filing.get("pdf_url", "")

        if len(case_numbers) > 1:
            print(f"\n[{i}/{len(settlements)}] Processing {primary_case_number} (+{len(case_numbers)-1} sibling cases)...")
        else:
            print(f"\n[{i}/{len(settlements)}] Processing {primary_case_number}...")

        # Find text file
        text_path = get_text_file_path(filing, args.text_dir)
        if not text_path:
            print(f"  ⚠ Text file not found, skipping")
            skipped += 1
            continue

        # Read text content
        text_content = read_text_file(text_path)
        line_count = len([l for l in text_content.strip().split('\n') if l.strip()])
        print(f"  📄 Loaded {len(text_content):,} characters, {line_count} lines from {text_path.name}")

        # Check if OCR failed (only 1 line)
        ocr_failed = line_count <= 1

        if ocr_failed:
            print(f"  ⚠ OCR failed (only {line_count} line), skipping LLM but storing metadata")

        if args.dry_run:
            if ocr_failed:
                print(f"  [DRY RUN] Would store metadata only (no LLM)")
            else:
                print(f"  [DRY RUN] Would process and store this settlement")
            processed += 1
            continue

        try:
            # Look up linked complaints for ALL case_numbers
            complaint_ids = []
            if complaints_collection is not None:
                for cn in case_numbers:
                    complaint = complaints_collection.find_one(
                        {"case_number": cn},
                        {"_id": 1}
                    )
                    if complaint:
                        complaint_ids.append(complaint["_id"])

                if complaint_ids:
                    print(f"  🔗 Linked to {len(complaint_ids)} complaint(s)")

            # Build base document for MongoDB
            document = {
                "case_numbers": case_numbers,  # Array of all case numbers
                "complaint_ids": complaint_ids,  # Array of complaint ObjectIds
                "year": filing["year"],
                "date": filing["date"],
                "title": filing["title"],
                "type": filing["type"],
                "respondent": filing["respondent"],
                "pdf_url": pdf_url,
                "text_content": text_content,
                "text_file": str(text_path),
                "ocr_failed": ocr_failed,
                "processed_at": datetime.now(timezone.utc),
            }

            # Only call LLM if OCR succeeded
            if not ocr_failed:
                print(f"  🤖 Calling OpenAI {args.model}...")
                llm_result = process_single_settlement(
                    filing, text_content, openai_client, system_prompt
                )
                print(f"  ✓ Extracted: {llm_result.get('license_action', 'Unknown')} - Fine: ${llm_result.get('fine_amount', 0) or 0:,.0f}")
                document["llm_extracted"] = llm_result
                document["llm_model"] = args.model

            # Upsert to MongoDB by pdf_url (one settlement per PDF)
            settlements_collection.update_one(
                {"pdf_url": pdf_url},
                {"$set": document},
                upsert=True
            )
            print(f"  💾 Stored in MongoDB")
            processed += 1

        except Exception as e:
            print(f"  ✗ Error: {e}")
            errors += 1

    # Summary
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Processed: {processed}")
    print(f"Skipped (no text file): {skipped}")
    print(f"Errors: {errors}")

    if not args.dry_run:
        total_in_db = settlements_collection.count_documents({})
        with_extraction = settlements_collection.count_documents({"llm_extracted": {"$exists": True}})
        ocr_failures = settlements_collection.count_documents({"ocr_failed": True})
        linked = settlements_collection.count_documents({"complaint_ids.0": {"$exists": True}})
        multi_case = settlements_collection.count_documents({"case_numbers.1": {"$exists": True}})
        print(f"\nMongoDB stats:")
        print(f"  Total settlements: {total_in_db}")
        print(f"  With LLM extraction: {with_extraction}")
        print(f"  OCR failures (no LLM): {ocr_failures}")
        print(f"  Linked to complaints: {linked}")
        print(f"  Multi-case settlements: {multi_case}")


if __name__ == "__main__":
    main()
