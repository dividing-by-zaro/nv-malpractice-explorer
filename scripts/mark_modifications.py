"""
Mark settlement documents that are modifications of previous orders.

This script identifies settlements with types indicating they modify a previous
order (containing "Modifying", "Amended", "Amending", "Addendum", or "Vacating")
and sets is_modification: true on them.

All changes are logged to change_log via TrackedDB.
"""

import os
import sys
from dotenv import load_dotenv
from pymongo import MongoClient

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.tracked_db import TrackedDB

load_dotenv()

# Keywords that indicate a modification
MODIFICATION_KEYWORDS = [
    "modifying",
    "amended",
    "amending",
    "addendum",
    "vacating",
]


def is_modification_type(settlement_type: str) -> bool:
    """Check if a settlement type indicates it's a modification."""
    if not settlement_type:
        return False
    lower_type = settlement_type.lower()
    return any(keyword in lower_type for keyword in MODIFICATION_KEYWORDS)


def main():
    # Connect to MongoDB
    mongo_uri = os.getenv("MONGODB_URI")
    if not mongo_uri:
        print("Error: MONGODB_URI not set")
        sys.exit(1)

    client = MongoClient(mongo_uri)
    db = client.malpractice

    # Initialize TrackedDB for logged updates
    tracked = TrackedDB(db, script="mark_modifications.py", user="system")

    # Find all settlements
    settlements = db.settlements

    # First, let's see what we're working with
    print("Scanning settlements for modification types...\n")

    # Find settlements that should be marked as modifications
    modifications_to_mark = []
    already_marked = []

    for doc in settlements.find():
        settlement_type = doc.get("type", "")
        pdf_url = doc.get("pdf_url", "unknown")
        case_numbers = doc.get("case_numbers", [])

        if is_modification_type(settlement_type):
            if doc.get("is_modification"):
                already_marked.append({
                    "pdf_url": pdf_url,
                    "type": settlement_type,
                    "case_numbers": case_numbers,
                })
            else:
                modifications_to_mark.append({
                    "_id": doc["_id"],
                    "pdf_url": pdf_url,
                    "type": settlement_type,
                    "case_numbers": case_numbers,
                })

    print(f"Found {len(modifications_to_mark)} settlements to mark as modifications")
    print(f"Found {len(already_marked)} settlements already marked as modifications\n")

    if not modifications_to_mark:
        print("No settlements need to be updated.")
        return

    # Show what will be updated
    print("Settlements to be marked as is_modification=true:")
    print("-" * 80)
    for item in modifications_to_mark:
        print(f"  Cases: {', '.join(item['case_numbers']) if item['case_numbers'] else 'N/A'}")
        print(f"  Type: {item['type']}")
        print(f"  URL: {item['pdf_url']}")
        print()

    # Confirm before proceeding
    response = input(f"Proceed with marking {len(modifications_to_mark)} settlements? (y/n): ")
    if response.lower() != 'y':
        print("Aborted.")
        return

    # Update each settlement using TrackedDB
    print("\nUpdating settlements...")
    success_count = 0

    for item in modifications_to_mark:
        result = tracked.update_one(
            collection="settlements",
            filter={"_id": item["_id"]},
            update={"$set": {"is_modification": True}},
            document_key=item["pdf_url"],
            reason=f"Mark as modification based on type: {item['type']}"
        )

        if result.modified_count > 0:
            success_count += 1
            print(f"  ✓ Updated: {', '.join(item['case_numbers']) if item['case_numbers'] else item['pdf_url']}")
        else:
            print(f"  ✗ Failed to update: {item['pdf_url']}")

    print(f"\nCompleted: {success_count}/{len(modifications_to_mark)} settlements updated")
    print("All changes have been logged to the change_log collection.")


if __name__ == "__main__":
    main()
