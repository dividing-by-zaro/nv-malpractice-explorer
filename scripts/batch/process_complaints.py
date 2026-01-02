#!/usr/bin/env python3
"""
Process complaint documents using OpenAI GPT-4 and store results in MongoDB.

Usage:
    uv run scripts/process_complaints.py                    # Process all unprocessed complaints
    uv run scripts/process_complaints.py --limit 10         # Process only 10 complaints
    uv run scripts/process_complaints.py --reprocess        # Reprocess all complaints
    uv run scripts/process_complaints.py --dry-run          # Preview without processing
"""

import json
import os
import argparse
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from openai import OpenAI
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

# Load environment variables from .env file
load_dotenv()


# Load the extraction prompts
PROMPT_PATH = Path(__file__).parent / "prompts" / "complaint_extraction.md"
COMPARISON_PROMPT_PATH = Path(__file__).parent / "prompts" / "amendment_comparison.md"


def load_prompt() -> str:
    """Load the extraction prompt from file."""
    with open(PROMPT_PATH, "r") as f:
        return f.read()


def load_comparison_prompt() -> str:
    """Load the amendment comparison prompt from file."""
    with open(COMPARISON_PROMPT_PATH, "r") as f:
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


# Priority order: higher number = preferred
COMPLAINT_PRIORITY = {
    "Complaint": 1,
    "Complaint and Request for Summary Suspension": 1,
    "Amended Complaint": 2,
    "First Amended Complaint": 2,
    "Second Amended Complaint": 3,
    "Third Amended Complaint": 4,
}


def filter_complaints(filings: list[dict]) -> list[dict]:
    """
    Filter filings to only include complaints.
    If a case has an amended complaint, use that instead of the original.
    """
    # Filter to only complaints
    complaints = [f for f in filings if f.get("type") in COMPLAINT_PRIORITY]

    # Group by case_number and keep only the highest priority version
    by_case: dict[str, dict] = {}
    for filing in complaints:
        case_num = filing["case_number"]
        priority = COMPLAINT_PRIORITY.get(filing["type"], 0)

        if case_num not in by_case:
            by_case[case_num] = (filing, priority)
        else:
            _, existing_priority = by_case[case_num]
            if priority > existing_priority:
                by_case[case_num] = (filing, priority)

    return [filing for filing, _ in by_case.values()]


def find_complaint_pairs(filings: list[dict]) -> list[tuple[dict, dict | None]]:
    """
    Find complaint pairs: (primary_complaint, original_complaint_or_none).

    For each case, returns:
    - primary_complaint: The highest priority version (amended if exists)
    - original_complaint: The original complaint if primary is amended, else None
    """
    # Filter to only complaints
    complaints = [f for f in filings if f.get("type") in COMPLAINT_PRIORITY]

    # Group all versions by case_number
    by_case: dict[str, list[tuple[dict, int]]] = {}
    for filing in complaints:
        case_num = filing["case_number"]
        priority = COMPLAINT_PRIORITY.get(filing["type"], 0)
        if case_num not in by_case:
            by_case[case_num] = []
        by_case[case_num].append((filing, priority))

    # For each case, identify primary (highest priority) and original (if different)
    result = []
    for case_num, entries in by_case.items():
        entries.sort(key=lambda x: x[1])  # Sort by priority ascending
        primary = entries[-1][0]  # Highest priority

        # Find original if it exists and is different from primary
        original = None
        if len(entries) > 1 and entries[0][1] < entries[-1][1]:
            # There's a lower priority version (original)
            original = entries[0][0]

        result.append((primary, original))

    return result


def get_text_file_path(filing: dict, text_dir: Path) -> Path | None:
    """Construct the path to the cleaned text file for a filing."""
    year = filing["year"]
    case_number = filing["case_number"]
    doc_type = filing["type"]

    # Normalize document type for filename
    type_slug = doc_type.replace(" ", "_").replace(",", "")

    # Try different filename patterns
    patterns = [
        f"{case_number}_{type_slug}.txt",
        f"{case_number}_{type_slug[:30]}.txt",  # Truncated
    ]

    year_dir = text_dir / str(year)
    if not year_dir.exists():
        return None

    # Search for matching file
    for pattern in patterns:
        candidate = year_dir / pattern
        if candidate.exists():
            return candidate

    # Fallback: search for files starting with case number
    for txt_file in year_dir.glob(f"{case_number}_*.txt"):
        # Check if it's a complaint type
        fname_lower = txt_file.name.lower()
        if "complaint" in fname_lower:
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


def process_single_complaint(
    filing: dict,
    text_content: str,
    openai_client: OpenAI,
    system_prompt: str
) -> dict:
    """Process a single complaint through the LLM."""
    # Build the user message with metadata and text
    user_content = f"""## Metadata

- **Title:** {filing.get('title', 'Unknown')}
- **Respondent:** {filing.get('respondent', 'Unknown')}
- **Case Number:** {filing.get('case_number', 'Unknown')}
- **Date:** {filing.get('date', 'Unknown')}
- **Type:** {filing.get('type', 'Unknown')}

## Document Text

{text_content}
"""

    return call_openai(openai_client, system_prompt, user_content)


def compare_amendments(
    original_text: str,
    amended_text: str,
    openai_client: OpenAI,
    comparison_prompt: str,
    model: str = "gpt-4o"
) -> str | None:
    """Compare original and amended complaint texts to summarize changes."""
    # Truncate texts to fit in context (keep first 6000 chars of each)
    max_chars = 6000
    original_truncated = original_text[:max_chars]
    amended_truncated = amended_text[:max_chars]

    user_content = f"""## Original Complaint Text

{original_truncated}

## Amended Complaint Text

{amended_truncated}
"""

    try:
        result = call_openai(openai_client, comparison_prompt, user_content, model)
        return result.get("amendment_summary")
    except Exception as e:
        print(f"  ⚠ Amendment comparison failed: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Process complaint documents with LLM")
    parser.add_argument("--limit", type=int, help="Limit number of complaints to process")
    parser.add_argument("--reprocess", action="store_true", help="Reprocess already processed complaints")
    parser.add_argument("--dry-run", action="store_true", help="Preview without processing or storing")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Data directory")
    parser.add_argument("--text-dir", type=Path, default=Path("text"), help="Text files directory")
    parser.add_argument("--model", type=str, default="gpt-4o", help="OpenAI model to use")
    args = parser.parse_args()

    # Load prompts
    print("Loading extraction prompt...")
    system_prompt = load_prompt()
    comparison_prompt = load_comparison_prompt()

    # Connect to services (skip in dry-run)
    if not args.dry_run:
        print("Connecting to MongoDB...")
        mongo_client = get_mongo_client()
        db = mongo_client["malpractice"]
        complaints_collection = db["complaints"]

        # Create unique index on case_number (one complaint per case)
        complaints_collection.create_index("case_number", unique=True)

        print("Connecting to OpenAI...")
        openai_client = get_openai_client()
    else:
        print("[DRY RUN] Skipping service connections")

    # Load and filter filings
    print("Loading filings metadata...")
    filings = load_filings_metadata(args.data_dir)
    complaint_pairs = find_complaint_pairs(filings)
    print(f"Found {len(complaint_pairs)} complaint cases")
    amended_count = sum(1 for _, orig in complaint_pairs if orig is not None)
    print(f"  - {amended_count} cases with amended complaints")

    # Filter out already processed (unless reprocessing)
    if not args.dry_run and not args.reprocess:
        processed_cases = set(
            doc["case_number"]
            for doc in complaints_collection.find(
                {"llm_extracted": {"$exists": True}},
                {"case_number": 1}
            )
        )
        complaint_pairs = [(p, o) for p, o in complaint_pairs if p["case_number"] not in processed_cases]
        print(f"{len(complaint_pairs)} complaints remaining to process")

    # Apply limit
    if args.limit:
        complaint_pairs = complaint_pairs[:args.limit]
        print(f"Limited to {len(complaint_pairs)} complaints")

    # Process each complaint
    processed = 0
    errors = 0
    skipped = 0

    for i, (filing, original_filing) in enumerate(complaint_pairs, 1):
        case_number = filing["case_number"]
        is_amended = original_filing is not None
        print(f"\n[{i}/{len(complaint_pairs)}] Processing {case_number}...")
        if is_amended:
            print(f"  📋 Type: {filing['type']} (original: {original_filing['type']})")

        # Find text file for primary complaint
        text_path = get_text_file_path(filing, args.text_dir)
        if not text_path:
            print(f"  ⚠ Text file not found, skipping")
            skipped += 1
            continue

        # Read text content
        text_content = read_text_file(text_path)
        line_count = len([l for l in text_content.strip().split('\n') if l.strip()])
        print(f"  📄 Loaded {len(text_content):,} characters, {line_count} lines from {text_path.name}")

        # Load original text if this is an amended complaint
        original_text = None
        original_text_path = None
        if is_amended:
            original_text_path = get_text_file_path(original_filing, args.text_dir)
            if original_text_path:
                original_text = read_text_file(original_text_path)
                orig_line_count = len([l for l in original_text.strip().split('\n') if l.strip()])
                print(f"  📄 Loaded original: {len(original_text):,} chars, {orig_line_count} lines")
            else:
                print(f"  ⚠ Original text file not found")

        # Check if OCR failed (only 1 line)
        ocr_failed = line_count <= 1

        if ocr_failed:
            print(f"  ⚠ OCR failed (only {line_count} line), skipping LLM but storing metadata")

        if args.dry_run:
            if ocr_failed:
                print(f"  [DRY RUN] Would store metadata only (no LLM)")
            else:
                msg = "[DRY RUN] Would process and store this complaint"
                if is_amended and original_text:
                    msg += " with amendment comparison"
                print(f"  {msg}")
            processed += 1
            continue

        try:
            # Build base document for MongoDB
            document = {
                "case_number": case_number,
                "year": filing["year"],
                "date": filing["date"],
                "title": filing["title"],
                "type": filing["type"],
                "respondent": filing["respondent"],
                "pdf_url": filing.get("pdf_url"),
                "text_content": text_content,
                "text_file": str(text_path),
                "ocr_failed": ocr_failed,
                "processed_at": datetime.now(timezone.utc),
                "is_amended": is_amended,
            }

            # Add original complaint data if this is amended
            if is_amended:
                document["original_complaint"] = {
                    "type": original_filing["type"],
                    "date": original_filing["date"],
                    "pdf_url": original_filing.get("pdf_url"),
                    "text_file": str(original_text_path) if original_text_path else None,
                }

            # Only call LLM if OCR succeeded
            if not ocr_failed:
                print(f"  🤖 Calling OpenAI {args.model} for extraction...")
                llm_result = process_single_complaint(
                    filing, text_content, openai_client, system_prompt
                )
                print(f"  ✓ Extracted: {llm_result.get('category', 'Unknown')} - {llm_result.get('summary', '')[:60]}...")
                document["llm_extracted"] = llm_result
                document["llm_model"] = args.model

                # Compare with original if this is amended and we have both texts
                if is_amended and original_text:
                    print(f"  🔄 Comparing amendments...")
                    amendment_summary = compare_amendments(
                        original_text, text_content, openai_client, comparison_prompt, args.model
                    )
                    if amendment_summary:
                        document["amendment_summary"] = amendment_summary
                        print(f"  ✓ Changes: {amendment_summary[:80]}...")

            # Upsert to MongoDB
            complaints_collection.update_one(
                {"case_number": case_number},
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
        total_in_db = complaints_collection.count_documents({})
        with_extraction = complaints_collection.count_documents({"llm_extracted": {"$exists": True}})
        ocr_failures = complaints_collection.count_documents({"ocr_failed": True})
        amended_in_db = complaints_collection.count_documents({"is_amended": True})
        with_summary = complaints_collection.count_documents({"amendment_summary": {"$exists": True}})
        print(f"\nMongoDB stats:")
        print(f"  Total complaints: {total_in_db}")
        print(f"  With LLM extraction: {with_extraction}")
        print(f"  OCR failures (no LLM): {ocr_failures}")
        print(f"  Amended complaints: {amended_in_db}")
        print(f"  With amendment summary: {with_summary}")


if __name__ == "__main__":
    main()
