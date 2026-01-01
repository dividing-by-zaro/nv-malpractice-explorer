#!/usr/bin/env python3
"""
Migration script to consolidate duplicate settlements.

Previously, multi-case settlements (e.g., "Case Nos 19-28023-1, 19-28023-2, 19-28023-3")
were stored as separate documents with the same pdf_url but different case_numbers.

This migration consolidates them into single documents with:
- case_numbers: array of all case numbers the settlement covers
- complaint_ids: array of all linked complaint ObjectIds

Usage:
    uv run python scripts/migrate_settlements.py           # Dry run (preview changes)
    uv run python scripts/migrate_settlements.py --apply   # Apply changes
"""

import os
import argparse
from collections import defaultdict
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()


def get_mongo_client() -> MongoClient:
    """Create MongoDB client from environment variable."""
    mongo_uri = os.environ.get("MONGODB_URI")
    if not mongo_uri:
        raise ValueError("MONGODB_URI environment variable is required")
    return MongoClient(mongo_uri)


def migrate_settlements(apply: bool = False):
    """Migrate settlements to new schema with case_numbers array."""
    print("Connecting to MongoDB...")
    client = get_mongo_client()
    db = client["malpractice"]
    settlements = db["settlements"]

    # Get all settlements
    all_docs = list(settlements.find({}))
    print(f"Found {len(all_docs)} total settlement documents")

    # Check if migration is needed
    # If documents already have case_numbers array, they're already migrated
    already_migrated = sum(1 for doc in all_docs if "case_numbers" in doc)
    if already_migrated == len(all_docs) and already_migrated > 0:
        print(f"All {already_migrated} documents already have case_numbers array.")
        print("Migration may have already been run. Checking for duplicates...")

    # Group documents by pdf_url
    by_pdf_url = defaultdict(list)
    for doc in all_docs:
        pdf_url = doc.get("pdf_url", "")
        if pdf_url:
            by_pdf_url[pdf_url].append(doc)
        else:
            # Documents without pdf_url - use case_number as fallback
            case_num = doc.get("case_number", doc.get("case_numbers", ["unknown"])[0] if doc.get("case_numbers") else "unknown")
            by_pdf_url[f"no_url_{case_num}"].append(doc)

    # Find duplicates (same pdf_url, multiple documents)
    duplicates = {url: docs for url, docs in by_pdf_url.items() if len(docs) > 1}
    unique = {url: docs[0] for url, docs in by_pdf_url.items() if len(docs) == 1}

    print(f"\nUnique settlements (no duplicates): {len(unique)}")
    print(f"Duplicate groups to consolidate: {len(duplicates)}")

    if not duplicates:
        print("\nNo duplicates found. Checking if schema update is needed...")
        # Even if no duplicates, we may need to convert case_number to case_numbers
        need_schema_update = []
        for doc in all_docs:
            if "case_number" in doc and "case_numbers" not in doc:
                need_schema_update.append(doc)

        if need_schema_update:
            print(f"Found {len(need_schema_update)} documents needing schema update (case_number -> case_numbers)")
            if apply:
                for doc in need_schema_update:
                    case_number = doc.get("case_number", "")
                    complaint_id = doc.get("complaint_id")
                    settlements.update_one(
                        {"_id": doc["_id"]},
                        {
                            "$set": {
                                "case_numbers": [case_number] if case_number else [],
                                "complaint_ids": [complaint_id] if complaint_id else [],
                            },
                            "$unset": {
                                "case_number": "",
                                "complaint_id": "",
                            }
                        }
                    )
                print(f"Updated {len(need_schema_update)} documents to new schema")
            else:
                print("[DRY RUN] Would update these documents to new schema")
        else:
            print("All documents already in new schema format.")
        return

    # Show details of duplicates
    print(f"\n{'='*60}")
    print("DUPLICATE GROUPS TO CONSOLIDATE")
    print(f"{'='*60}")

    total_docs_to_delete = 0
    consolidation_plan = []

    for pdf_url, docs in sorted(duplicates.items()):
        # Collect all case_numbers and complaint_ids
        all_case_numbers = []
        all_complaint_ids = []

        for doc in docs:
            # Handle both old schema (case_number) and new schema (case_numbers)
            if "case_numbers" in doc:
                all_case_numbers.extend(doc["case_numbers"])
            elif "case_number" in doc:
                all_case_numbers.append(doc["case_number"])

            if "complaint_ids" in doc:
                all_complaint_ids.extend(doc["complaint_ids"])
            elif "complaint_id" in doc and doc["complaint_id"]:
                all_complaint_ids.append(doc["complaint_id"])

        # Deduplicate while preserving order
        seen_cn = set()
        unique_case_numbers = []
        for cn in all_case_numbers:
            if cn and cn not in seen_cn:
                seen_cn.add(cn)
                unique_case_numbers.append(cn)

        seen_cid = set()
        unique_complaint_ids = []
        for cid in all_complaint_ids:
            if cid and str(cid) not in seen_cid:
                seen_cid.add(str(cid))
                unique_complaint_ids.append(cid)

        # Use the first document as the base (keep its _id)
        base_doc = docs[0]
        docs_to_delete = docs[1:]

        print(f"\nPDF: {pdf_url[:80]}...")
        print(f"  Documents: {len(docs)} -> 1")
        print(f"  Case numbers: {unique_case_numbers}")
        print(f"  Complaint IDs: {len(unique_complaint_ids)}")

        consolidation_plan.append({
            "pdf_url": pdf_url,
            "base_doc_id": base_doc["_id"],
            "docs_to_delete": [d["_id"] for d in docs_to_delete],
            "case_numbers": unique_case_numbers,
            "complaint_ids": unique_complaint_ids,
        })

        total_docs_to_delete += len(docs_to_delete)

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Duplicate groups: {len(duplicates)}")
    print(f"Documents to consolidate: {sum(len(d) for d in duplicates.values())}")
    print(f"Documents to delete: {total_docs_to_delete}")
    print(f"Final document count: {len(all_docs) - total_docs_to_delete}")

    if not apply:
        print(f"\n[DRY RUN] No changes made. Run with --apply to consolidate.")
        return

    # Apply consolidation
    print(f"\nApplying consolidation...")

    for plan in consolidation_plan:
        # Update base document with consolidated data
        settlements.update_one(
            {"_id": plan["base_doc_id"]},
            {
                "$set": {
                    "case_numbers": plan["case_numbers"],
                    "complaint_ids": plan["complaint_ids"],
                    "migrated_at": datetime.now(timezone.utc),
                },
                "$unset": {
                    "case_number": "",
                    "complaint_id": "",
                }
            }
        )

        # Delete duplicate documents
        if plan["docs_to_delete"]:
            settlements.delete_many({"_id": {"$in": plan["docs_to_delete"]}})

    print(f"Consolidated {len(consolidation_plan)} duplicate groups")
    print(f"Deleted {total_docs_to_delete} duplicate documents")

    # Update any remaining documents that haven't been migrated
    remaining = settlements.count_documents({"case_number": {"$exists": True}})
    if remaining > 0:
        print(f"\nUpdating {remaining} remaining documents to new schema...")
        for doc in settlements.find({"case_number": {"$exists": True}}):
            case_number = doc.get("case_number", "")
            complaint_id = doc.get("complaint_id")
            settlements.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "case_numbers": [case_number] if case_number else [],
                        "complaint_ids": [complaint_id] if complaint_id else [],
                        "migrated_at": datetime.now(timezone.utc),
                    },
                    "$unset": {
                        "case_number": "",
                        "complaint_id": "",
                    }
                }
            )

    # Drop old index and create new one
    print("\nUpdating indexes...")
    try:
        settlements.drop_index("case_number_1")
        print("  Dropped old case_number index")
    except Exception:
        print("  No old case_number index to drop")

    settlements.create_index("pdf_url", unique=True)
    print("  Created unique index on pdf_url")

    settlements.create_index("case_numbers")
    print("  Created index on case_numbers")

    # Final stats
    final_count = settlements.count_documents({})
    print(f"\nMigration complete!")
    print(f"Final settlement count: {final_count}")


def main():
    parser = argparse.ArgumentParser(description="Migrate settlements to consolidated schema")
    parser.add_argument("--apply", action="store_true", help="Apply the migration (default is dry run)")
    args = parser.parse_args()

    migrate_settlements(apply=args.apply)


if __name__ == "__main__":
    main()
