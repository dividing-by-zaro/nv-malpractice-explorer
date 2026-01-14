#!/usr/bin/env python3
"""
Backfill amended complaints that are missing from the database.

For cases where we have:
- Original complaint in DB (type='Complaint')
- Amended complaint PDF exists but not in DB

This script:
1. Finds amended complaint filings not yet in DB
2. Runs LLM extraction on the amended complaint text
3. Loads original complaint text from DB
4. Runs LLM comparison to generate amendment_summary
5. Creates new document with is_amended=True and original_complaint reference
6. All changes tracked via TrackedDB

Usage:
    uv run python scripts/backfill_amended_complaints.py
    uv run python scripts/backfill_amended_complaints.py --dry-run
    uv run python scripts/backfill_amended_complaints.py --limit 5
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pymongo import MongoClient

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.tracked_db import TrackedDB

load_dotenv()

PROMPTS_DIR = Path(__file__).parent / "prompts"
TEXT_DIR = Path(__file__).parent.parent / "text"
DATA_DIR = Path(__file__).parent.parent / "data"


def load_prompt(name: str) -> str:
    """Load a prompt file from the prompts directory."""
    prompt_path = PROMPTS_DIR / f"{name}.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text()


def call_openai(client: OpenAI, system_prompt: str, user_content: str, model: str = "gpt-4o") -> dict:
    """Call OpenAI API and return parsed JSON response."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        temperature=0.1,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)


def find_text_file(case_number: str, doc_type: str) -> Path | None:
    """Find the text file for an amended complaint."""
    # Normalize case number for filename matching
    case_num_normalized = case_number.replace("-01", "-1")

    # Build expected filename patterns
    type_slug = doc_type.replace(" ", "_").replace(",", "")

    # Search all year directories
    for year_dir in TEXT_DIR.iterdir():
        if not year_dir.is_dir():
            continue

        # Try exact match first
        for pattern in [
            f"{case_num_normalized}_{type_slug}.txt",
            f"{case_number}_{type_slug}.txt",
            f"{case_num_normalized}_First_Amended_Complaint.txt",
            f"{case_number}_First_Amended_Complaint.txt",
            f"{case_num_normalized}_Amended_Complaint.txt",
            f"{case_number}_Amended_Complaint.txt",
            f"{case_num_normalized}_Second_Amended_Complaint.txt",
            f"{case_number}_Second_Amended_Complaint.txt",
        ]:
            text_file = year_dir / pattern
            if text_file.exists():
                return text_file

        # Try glob match
        for txt_file in year_dir.glob(f"{case_num_normalized}*Amended*Complaint*.txt"):
            return txt_file
        for txt_file in year_dir.glob(f"{case_number}*Amended*Complaint*.txt"):
            return txt_file

    return None


def get_original_complaint(db, case_number: str) -> dict | None:
    """Get the original (non-amended) complaint from DB."""
    # Normalize case number
    case_num_normalized = case_number.replace("-01", "-1")

    # Find original complaint (is_amended is False or not set, type is 'Complaint')
    doc = db.complaints.find_one({
        "case_number": {"$in": [case_number, case_num_normalized]},
        "$or": [
            {"is_amended": {"$exists": False}},
            {"is_amended": False},
            {"is_amended": None}
        ]
    })

    return doc


def main():
    parser = argparse.ArgumentParser(description="Backfill amended complaints")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of complaints to process (0 = no limit)")
    parser.add_argument("--model", default="gpt-4o", help="OpenAI model to use (default: gpt-4o)")
    args = parser.parse_args()

    # Connect to MongoDB
    mongo_uri = os.getenv("MONGODB_URI")
    if not mongo_uri:
        print("Error: MONGODB_URI not set")
        sys.exit(1)

    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        print("Error: OPENAI_API_KEY not set")
        sys.exit(1)

    client = MongoClient(mongo_uri)
    db = client.malpractice
    openai_client = OpenAI(api_key=openai_api_key)

    # Initialize TrackedDB for logged updates
    tracked = TrackedDB(db, script="backfill_amended_complaints.py", user="system")

    # Load filings metadata
    filings_path = DATA_DIR / "filings_normalized.json"
    with open(filings_path) as f:
        data = json.load(f)
    filings = data.get("filings", [])

    # Find amended complaints
    amended_filings = [
        f for f in filings
        if "Amended" in f.get("type", "") and "Complaint" in f.get("type", "")
    ]
    print(f"Found {len(amended_filings)} amended complaint filings in metadata")

    # Load prompts
    extraction_prompt = load_prompt("complaint_extraction")
    comparison_prompt = load_prompt("amendment_comparison")

    # Track results
    success_count = 0
    skip_count = 0
    error_count = 0
    already_exists_count = 0

    for i, filing in enumerate(amended_filings):
        if args.limit and success_count >= args.limit:
            print(f"\nReached limit of {args.limit} complaints")
            break

        case_number = filing.get("case_number")
        doc_type = filing.get("type")
        pdf_url = filing.get("pdf_url")

        print(f"\n[{i+1}/{len(amended_filings)}] Processing {case_number}: {doc_type}")

        # Check if this amended complaint already exists in DB
        existing = db.complaints.find_one({"pdf_url": pdf_url})
        if existing:
            print(f"  Already exists in DB (is_amended={existing.get('is_amended')})")
            already_exists_count += 1
            continue

        # Find the text file
        text_file = find_text_file(case_number, doc_type)
        if not text_file:
            print(f"  Skipping: Text file not found")
            skip_count += 1
            continue

        # Load amended complaint text
        amended_text = text_file.read_text()
        if len(amended_text.strip()) < 100:
            print(f"  Skipping: Text file too short ({len(amended_text)} chars)")
            skip_count += 1
            continue

        print(f"  Found text file: {text_file.name} ({len(amended_text)} chars)")

        # Get original complaint from DB
        original = get_original_complaint(db, case_number)
        if not original:
            print(f"  Skipping: Original complaint not found in DB")
            skip_count += 1
            continue

        original_text = original.get("text_content", "")
        if not original_text:
            print(f"  Skipping: Original complaint has no text content")
            skip_count += 1
            continue

        print(f"  Found original: {original.get('type')} ({original.get('date')})")

        if args.dry_run:
            print(f"  [DRY RUN] Would process amended complaint and generate summary")
            success_count += 1
            continue

        try:
            # Step 1: Extract data from amended complaint via LLM
            print(f"  Extracting data from amended complaint...")
            extraction_content = f"""## Metadata

- Title: {filing.get('title', '')}
- Respondent: {filing.get('respondent', '')}
- Case Number: {case_number}
- Date: {filing.get('date', '')}
- Type: {doc_type}

## Document Text

{amended_text[:12000]}
"""
            extracted = call_openai(openai_client, extraction_prompt, extraction_content, args.model)
            print(f"  Extracted: category={extracted.get('category')}, specialty={extracted.get('specialty')}")

            # Step 2: Compare original vs amended via LLM
            print(f"  Generating amendment summary...")
            max_chars = 6000
            comparison_content = f"""## Original Complaint Text

{original_text[:max_chars]}

## Amended Complaint Text

{amended_text[:max_chars]}
"""
            comparison_result = call_openai(openai_client, comparison_prompt, comparison_content, args.model)
            amendment_summary = comparison_result.get("amendment_summary", "")
            print(f"  Summary: {amendment_summary[:80]}...")

            # Step 3: Build the new document
            # Extract year from date
            date_str = filing.get("date", "")
            try:
                year = int(date_str.split("/")[-1]) if date_str else None
            except (ValueError, IndexError):
                year = None

            new_doc = {
                "case_number": case_number,
                "respondent": filing.get("respondent"),
                "date": date_str,
                "year": year,
                "type": doc_type,
                "pdf_url": pdf_url,
                "title": filing.get("title"),
                "text_content": amended_text,
                "text_file": str(text_file),
                "is_amended": True,
                "original_complaint": {
                    "type": original.get("type"),
                    "date": original.get("date"),
                    "pdf_url": original.get("pdf_url"),
                },
                "amendment_summary": amendment_summary,
                "llm_extracted": extracted,
                "llm_model": args.model,
                "processed_at": datetime.utcnow(),
            }

            # Step 4: Insert using TrackedDB (upsert with pdf_url as unique key)
            tracked.update_one(
                collection="complaints",
                filter={"pdf_url": pdf_url},
                update={"$set": new_doc},
                document_key=f"{case_number} (amended)",
                reason=f"Backfill amended complaint: {amendment_summary[:100]}",
                upsert=True
            )

            print(f"  Successfully inserted amended complaint")
            success_count += 1

        except Exception as e:
            print(f"  Error: {e}")
            error_count += 1

    # Summary
    print(f"\n{'='*60}")
    print(f"Results:")
    print(f"  Successfully processed: {success_count}")
    print(f"  Already in DB: {already_exists_count}")
    print(f"  Skipped (no text/original): {skip_count}")
    print(f"  Errors: {error_count}")

    if not args.dry_run and success_count > 0:
        print("\nAll changes have been logged to the change_log collection.")


if __name__ == "__main__":
    main()
