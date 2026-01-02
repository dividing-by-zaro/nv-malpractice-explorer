#!/usr/bin/env python3
"""
Add resolution_outcome field to existing settlement documents.

Resolution outcomes:
- "Hearing": Contested case that went to formal hearing (Findings of Fact)
- "Settlement": Negotiated agreement (everything else)

For complaints without a resolution document, the frontend will show "Open".
"""

import os
import sys
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()


def get_resolution_outcome(doc_type: str) -> str:
    """Determine resolution_outcome based on document type."""
    if not doc_type:
        return "Settlement"

    doc_type_lower = doc_type.lower()

    # Hearing: Findings of Fact documents (contested cases)
    if "findings of fact" in doc_type_lower:
        return "Hearing"

    # Everything else is a Settlement
    return "Settlement"


def migrate_settlements(dry_run: bool = True):
    """Add resolution_outcome field to all settlement documents."""
    uri = os.getenv("MONGODB_URI")
    if not uri:
        print("Error: MONGODB_URI not set")
        sys.exit(1)

    client = MongoClient(uri)
    db = client["malpractice"]

    # Get all settlements
    settlements = list(db.settlements.find({}, {"_id": 1, "type": 1, "case_numbers": 1}))
    print(f"Found {len(settlements)} settlements to update")

    # Count by outcome
    outcome_counts = {"Settlement": 0, "Hearing": 0}
    updates = []

    for s in settlements:
        doc_type = s.get("type", "")
        outcome = get_resolution_outcome(doc_type)
        outcome_counts[outcome] += 1
        updates.append({
            "_id": s["_id"],
            "case_numbers": s.get("case_numbers", []),
            "type": doc_type,
            "resolution_outcome": outcome
        })

    print(f"\nResolution outcome breakdown:")
    for outcome, count in sorted(outcome_counts.items(), key=lambda x: -x[1]):
        print(f"  {outcome}: {count}")

    if dry_run:
        print("\n[DRY RUN] No changes made. Run with --apply to update.")
        print("\nSample updates:")
        for u in updates[:5]:
            print(f"  {u['case_numbers']}: {u['type'][:50]}... -> {u['resolution_outcome']}")

        # Show some Hearing examples
        print("\nHearing examples:")
        for u in updates:
            if u["resolution_outcome"] == "Hearing":
                print(f"  {u['case_numbers']}: {u['type']}")
                if sum(1 for x in updates if x["resolution_outcome"] == "Hearing" and updates.index(x) <= updates.index(u)) >= 5:
                    break
    else:
        print("\nApplying updates...")
        updated = 0
        for u in updates:
            result = db.settlements.update_one(
                {"_id": u["_id"]},
                {"$set": {"resolution_outcome": u["resolution_outcome"]}}
            )
            if result.modified_count > 0:
                updated += 1

        print(f"Updated {updated} documents")

    client.close()


if __name__ == "__main__":
    dry_run = "--apply" not in sys.argv
    migrate_settlements(dry_run=dry_run)
