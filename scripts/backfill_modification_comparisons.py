#!/usr/bin/env python3
"""
Backfill modification comparisons for existing settlement modifications.

This script:
1. Finds settlements with is_modification=True but no modification_summary
2. Locates the original settlement for each
3. Generates modification_summary via LLM comparison
4. Updates the settlement using TrackedDB for audit logging

Usage:
    uv run python scripts/backfill_modification_comparisons.py
    uv run python scripts/backfill_modification_comparisons.py --dry-run
"""

import argparse
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


def load_prompt(name: str) -> str:
    """Load a prompt file from the prompts directory."""
    prompt_path = PROMPTS_DIR / f"{name}.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text()


def call_openai(client: OpenAI, system_prompt: str, user_content: str, model: str = "gpt-4o") -> dict:
    """Call OpenAI API and return parsed JSON response."""
    import json

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


def find_original_settlement(settlements_collection, case_numbers: list[str], current_pdf_url: str):
    """Find the original (non-modification) settlement for the same case number(s)."""
    if not case_numbers:
        return None

    candidates = list(settlements_collection.find({
        "case_numbers": {"$in": case_numbers},
        "pdf_url": {"$ne": current_pdf_url},
        "$or": [
            {"is_modification": {"$exists": False}},
            {"is_modification": False}
        ]
    }))

    if not candidates:
        return None

    def parse_date(s):
        try:
            return datetime.strptime(s.get("date", "1/1/1900"), "%m/%d/%Y")
        except (ValueError, TypeError):
            return datetime(1900, 1, 1)

    return min(candidates, key=parse_date)


def main():
    parser = argparse.ArgumentParser(description="Backfill modification comparisons")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
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
    tracked = TrackedDB(db, script="backfill_modification_comparisons.py", user="system")

    settlements = db.settlements

    # Find modifications without modification_summary
    modifications = list(settlements.find({
        "is_modification": True,
        "$or": [
            {"modification_summary": {"$exists": False}},
            {"modification_summary": None}
        ]
    }))

    print(f"Found {len(modifications)} modifications needing comparison\n")

    if not modifications:
        print("No modifications need backfilling.")
        return

    # Load the comparison prompt
    comparison_prompt = load_prompt("settlement_modification_comparison")

    success_count = 0
    skip_count = 0
    error_count = 0

    for mod in modifications:
        case_numbers = mod.get("case_numbers", [])
        pdf_url = mod.get("pdf_url", "unknown")
        mod_type = mod.get("type", "Unknown")

        print(f"Processing: {', '.join(case_numbers)} - {mod_type}")

        # Find original settlement
        original = find_original_settlement(settlements, case_numbers, pdf_url)

        if not original:
            print("  Skipping: No original settlement found")
            skip_count += 1
            continue

        original_text = original.get("text_content", "")
        mod_text = mod.get("text_content", "")

        if not original_text:
            print("  Skipping: Original has no text content")
            skip_count += 1
            continue

        if not mod_text:
            print("  Skipping: Modification has no text content")
            skip_count += 1
            continue

        print(f"  Found original: {original.get('type')} ({original.get('date')})")

        if args.dry_run:
            print("  [DRY RUN] Would generate comparison")
            continue

        # Generate comparison via LLM
        max_chars = 6000
        comparison_content = f"""## Original Settlement Text

{original_text[:max_chars]}

## Modification Order Text

{mod_text[:max_chars]}
"""

        try:
            print("  Calling OpenAI for comparison...")
            result = call_openai(openai_client, comparison_prompt, comparison_content, args.model)

            modification_summary = result.get("modification_summary")
            modification_changes = result.get("changes", [])

            if not modification_summary:
                print("  Warning: No summary returned from LLM")
                error_count += 1
                continue

            # Update using TrackedDB
            update_fields = {
                "modification_summary": modification_summary,
                "modification_changes": modification_changes,
                "original_settlement": {
                    "pdf_url": original.get("pdf_url"),
                    "type": original.get("type"),
                    "date": original.get("date"),
                }
            }

            tracked.update_one(
                collection="settlements",
                filter={"_id": mod["_id"]},
                update={"$set": update_fields},
                document_key=pdf_url,
                reason=f"Backfill modification comparison: {modification_summary[:100]}"
            )

            print(f"  Summary: {modification_summary[:80]}...")
            success_count += 1

        except Exception as e:
            print(f"  Error: {e}")
            error_count += 1

    print(f"\n{'='*60}")
    print(f"Results:")
    print(f"  Successfully processed: {success_count}")
    print(f"  Skipped (no original/text): {skip_count}")
    print(f"  Errors: {error_count}")

    if not args.dry_run and success_count > 0:
        print("\nAll changes have been logged to the change_log collection.")


if __name__ == "__main__":
    main()
