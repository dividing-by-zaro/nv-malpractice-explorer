#!/usr/bin/env python3
"""
Reprocess existing amended complaints to add original_complaint data and amendment_summary.

This migration script finds all amended complaints in MongoDB, loads their original
complaint text, and uses the LLM to generate a summary of what changed.

Usage:
    uv run python scripts/reprocess_amended_complaints.py              # Dry run (preview)
    uv run python scripts/reprocess_amended_complaints.py --apply      # Apply changes
    uv run python scripts/reprocess_amended_complaints.py --limit 5    # Process only 5
"""

import json
import os
import argparse
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from openai import OpenAI
from pymongo import MongoClient

# Load environment variables from .env file
load_dotenv()

# Load the comparison prompt
COMPARISON_PROMPT_PATH = Path(__file__).parent / "prompts" / "amendment_comparison.md"

# Priority mapping for complaint types
COMPLAINT_PRIORITY = {
    "Complaint": 1,
    "Complaint and Request for Summary Suspension": 1,
    "Amended Complaint": 2,
    "First Amended Complaint": 2,
    "Second Amended Complaint": 3,
    "Third Amended Complaint": 4,
}


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


def find_original_for_case(filings: list[dict], case_number: str) -> dict | None:
    """Find the original complaint for a given case number."""
    candidates = []
    for filing in filings:
        if filing.get("case_number") != case_number:
            continue
        ftype = filing.get("type", "")
        if ftype in COMPLAINT_PRIORITY:
            priority = COMPLAINT_PRIORITY[ftype]
            candidates.append((filing, priority))

    if not candidates:
        return None

    # Sort by priority ascending, return lowest (original)
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]


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
        f"{case_number}_{type_slug[:30]}.txt",
    ]

    year_dir = text_dir / str(year)
    if not year_dir.exists():
        return None

    for pattern in patterns:
        candidate = year_dir / pattern
        if candidate.exists():
            return candidate

    # Fallback: search for files starting with case number
    for txt_file in year_dir.glob(f"{case_number}_*.txt"):
        fname_lower = txt_file.name.lower()
        if "complaint" in fname_lower and "amended" not in fname_lower:
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


def compare_amendments(
    original_text: str,
    amended_text: str,
    openai_client: OpenAI,
    comparison_prompt: str,
    model: str = "gpt-4o"
) -> str | None:
    """Compare original and amended complaint texts to summarize changes."""
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
    parser = argparse.ArgumentParser(description="Reprocess amended complaints")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry run)")
    parser.add_argument("--limit", type=int, help="Limit number of complaints to process")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Data directory")
    parser.add_argument("--text-dir", type=Path, default=Path("text"), help="Text files directory")
    parser.add_argument("--model", type=str, default="gpt-4o", help="OpenAI model to use")
    args = parser.parse_args()

    dry_run = not args.apply
    if dry_run:
        print("=" * 50)
        print("DRY RUN - No changes will be made")
        print("Use --apply to actually update MongoDB")
        print("=" * 50)

    # Load comparison prompt
    print("\nLoading comparison prompt...")
    comparison_prompt = load_comparison_prompt()

    # Connect to MongoDB
    print("Connecting to MongoDB...")
    mongo_client = get_mongo_client()
    db = mongo_client["malpractice"]
    complaints_collection = db["complaints"]

    # Load filings for finding originals
    print("Loading filings metadata...")
    filings = load_filings_metadata(args.data_dir)

    # Find amended complaints that need processing
    # Look for complaints with "Amended" in type that don't have amendment_summary
    amended_query = {
        "type": {"$regex": "Amended", "$options": "i"},
        "llm_extracted": {"$exists": True},
    }

    if not dry_run:
        # Only process those without amendment_summary
        amended_query["amendment_summary"] = {"$exists": False}

    amended_complaints = list(complaints_collection.find(amended_query))
    print(f"\nFound {len(amended_complaints)} amended complaints to process")

    # Connect to OpenAI
    if not dry_run:
        print("Connecting to OpenAI...")
        openai_client = get_openai_client()

    # Apply limit
    if args.limit:
        amended_complaints = amended_complaints[:args.limit]
        print(f"Limited to {len(amended_complaints)} complaints")

    # Process each amended complaint
    processed = 0
    errors = 0
    skipped = 0

    for i, complaint in enumerate(amended_complaints, 1):
        case_number = complaint["case_number"]
        complaint_type = complaint["type"]
        print(f"\n[{i}/{len(amended_complaints)}] {case_number} ({complaint_type})")

        # Find original complaint in filings
        original_filing = find_original_for_case(filings, case_number)
        if not original_filing:
            print(f"  ⚠ No original complaint found in filings")
            skipped += 1
            continue

        if original_filing["type"] == complaint_type:
            print(f"  ⚠ Original has same type as amended, skipping")
            skipped += 1
            continue

        print(f"  📋 Original: {original_filing['type']}")

        # Get text files
        amended_text_path = complaint.get("text_file")
        if amended_text_path:
            amended_text_path = Path(amended_text_path)
            if not amended_text_path.exists():
                amended_text_path = None

        if not amended_text_path:
            print(f"  ⚠ Amended text file not found")
            skipped += 1
            continue

        original_text_path = get_text_file_path(original_filing, args.text_dir)
        if not original_text_path:
            print(f"  ⚠ Original text file not found")
            skipped += 1
            continue

        # Read texts
        amended_text = read_text_file(amended_text_path)
        original_text = read_text_file(original_text_path)
        print(f"  📄 Amended: {len(amended_text):,} chars | Original: {len(original_text):,} chars")

        if dry_run:
            print(f"  [DRY RUN] Would compare and update")
            processed += 1
            continue

        try:
            # Compare amendments
            print(f"  🔄 Comparing amendments...")
            amendment_summary = compare_amendments(
                original_text, amended_text, openai_client, comparison_prompt, args.model
            )

            if amendment_summary:
                print(f"  ✓ Changes: {amendment_summary[:80]}...")
            else:
                print(f"  ⚠ No summary generated")

            # Build update document
            update_doc = {
                "is_amended": True,
                "original_complaint": {
                    "type": original_filing["type"],
                    "date": original_filing["date"],
                    "pdf_url": original_filing.get("pdf_url"),
                    "text_file": str(original_text_path),
                },
                "reprocessed_at": datetime.now(timezone.utc),
            }

            if amendment_summary:
                update_doc["amendment_summary"] = amendment_summary

            # Update MongoDB
            complaints_collection.update_one(
                {"_id": complaint["_id"]},
                {"$set": update_doc}
            )
            print(f"  💾 Updated in MongoDB")
            processed += 1

        except Exception as e:
            print(f"  ✗ Error: {e}")
            errors += 1

    # Summary
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Processed: {processed}")
    print(f"Skipped: {skipped}")
    print(f"Errors: {errors}")

    if not dry_run:
        amended_in_db = complaints_collection.count_documents({"is_amended": True})
        with_summary = complaints_collection.count_documents({"amendment_summary": {"$exists": True}})
        print(f"\nMongoDB stats:")
        print(f"  Amended complaints: {amended_in_db}")
        print(f"  With amendment summary: {with_summary}")


if __name__ == "__main__":
    main()
