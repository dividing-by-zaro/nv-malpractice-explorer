"""
Normalize specialty values in complaints collection to match NPDB categories.

This script:
1. Maps name variations to standard names (e.g., "Neurological Surgery" → "Neurosurgery")
2. Maps subspecialties to parent specialties (e.g., "Nephrology" → "Internal Medicine")
3. Preserves original values in specialty_original field
4. Logs all changes to change_log collection

Usage:
    # Preview changes (dry-run)
    uv run python scripts/utils/normalize_specialties.py

    # Apply changes
    uv run python scripts/utils/normalize_specialties.py --apply
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from pymongo import MongoClient

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lib import ChangeLogger

load_dotenv()

# Mapping from our specialty names to NPDB standard names
# These are direct renames (same specialty, different name)
NAME_MAPPINGS = {
    "Neurological Surgery": "Neurosurgery",
    "Orthopedic Surgery": "Orthopedics",
    "Cardiothoracic Surgery": "Thoracic Surgery",
}

# Subspecialties that should map to a parent specialty
# Original is preserved in specialty_original, specialty becomes the parent
SUBSPECIALTY_MAPPINGS = {
    "Dermatopathology": "Pathology",
    "Infectious Disease": "Internal Medicine",
    "Interventional Radiology": "Radiology",
    "Nephrology": "Internal Medicine",
    "Pediatric Cardiology": "Pediatrics",
    "Pediatric Critical Care Medicine": "Pediatrics",
    "Pediatric Emergency Medicine": "Pediatrics",
    "Radiation Oncology": "Radiology",
    "Rheumatology": "Internal Medicine",
}

SCRIPT_NAME = "normalize_specialties.py"
REASON = "Normalize specialty to match NPDB categories for cross-dataset analysis"


def get_db():
    """Get MongoDB database connection."""
    mongo_uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017/malpractice")
    client = MongoClient(mongo_uri)

    from urllib.parse import urlparse
    parsed = urlparse(mongo_uri)
    db_name = parsed.path.lstrip("/").split("?")[0] or "malpractice"

    return client[db_name]


def normalize_specialty(specialty: str) -> tuple[str, str | None, bool]:
    """
    Normalize a specialty value.

    Args:
        specialty: Original specialty string

    Returns:
        Tuple of (normalized_specialty, original_if_changed, is_subspecialty)
    """
    if not specialty:
        return specialty, None, False

    # Check for direct name mapping
    if specialty in NAME_MAPPINGS:
        return NAME_MAPPINGS[specialty], specialty, False

    # Check for subspecialty mapping
    if specialty in SUBSPECIALTY_MAPPINGS:
        return SUBSPECIALTY_MAPPINGS[specialty], specialty, True

    # No change needed
    return specialty, None, False


def run_normalization(dry_run: bool = True) -> dict:
    """
    Run the specialty normalization.

    Args:
        dry_run: If True, preview changes without applying

    Returns:
        Summary dict with counts
    """
    db = get_db()
    complaints = db.complaints

    # Initialize change logger (only creates indexes if not dry_run)
    logger = None
    if not dry_run:
        logger = ChangeLogger(db, script=SCRIPT_NAME)

    # Find all complaints with llm_extracted.specialty
    cursor = complaints.find(
        {"llm_extracted.specialty": {"$exists": True, "$ne": None}},
        {"_id": 1, "case_number": 1, "llm_extracted.specialty": 1}
    )

    stats = {
        "total_with_specialty": 0,
        "name_mappings": 0,
        "subspecialty_mappings": 0,
        "already_normalized": 0,
        "changes": [],
    }

    for doc in cursor:
        stats["total_with_specialty"] += 1

        current_specialty = doc.get("llm_extracted", {}).get("specialty")
        normalized, original, is_subspecialty = normalize_specialty(current_specialty)

        # No change needed
        if original is None:
            stats["already_normalized"] += 1
            continue

        # Record the change
        change_record = {
            "case_number": doc["case_number"],
            "old_specialty": current_specialty,
            "new_specialty": normalized,
            "is_subspecialty": is_subspecialty,
        }
        stats["changes"].append(change_record)

        if is_subspecialty:
            stats["subspecialty_mappings"] += 1
        else:
            stats["name_mappings"] += 1

        if not dry_run:
            # Build the update - only update specialty field
            # Original value is preserved in change_log, not in the document
            update_fields = {
                "llm_extracted.specialty": normalized,
            }

            # Apply the update
            complaints.update_one(
                {"_id": doc["_id"]},
                {"$set": update_fields}
            )

            # Log the change (includes old value for audit trail)
            logger.log_change(
                collection="complaints",
                document_id=doc["_id"],
                document_key=doc["case_number"],
                operation="update",
                changes=[
                    {"field": "llm_extracted.specialty",
                     "old_value": current_specialty, "new_value": normalized},
                ],
                reason=REASON,
            )

    return stats


def print_report(stats: dict, dry_run: bool) -> None:
    """Print a summary report."""
    mode = "DRY RUN" if dry_run else "APPLIED"
    print(f"\n{'=' * 60}")
    print(f"SPECIALTY NORMALIZATION - {mode}")
    print(f"{'=' * 60}\n")

    print(f"Total complaints with specialty: {stats['total_with_specialty']}")
    print(f"Already normalized (no change): {stats['already_normalized']}")
    print(f"Name mappings: {stats['name_mappings']}")
    print(f"Subspecialty mappings: {stats['subspecialty_mappings']}")
    print(f"Total changes: {len(stats['changes'])}")

    if stats["changes"]:
        print(f"\n{'─' * 60}")
        print("CHANGES:")
        print(f"{'─' * 60}")

        # Group by type
        name_changes = [c for c in stats["changes"] if not c["is_subspecialty"]]
        sub_changes = [c for c in stats["changes"] if c["is_subspecialty"]]

        if name_changes:
            print("\nName Mappings:")
            for c in name_changes[:10]:  # Show first 10
                print(f"  {c['case_number']}: {c['old_specialty']} → {c['new_specialty']}")
            if len(name_changes) > 10:
                print(f"  ... and {len(name_changes) - 10} more")

        if sub_changes:
            print("\nSubspecialty Mappings:")
            for c in sub_changes[:10]:  # Show first 10
                print(f"  {c['case_number']}: {c['old_specialty']} → {c['new_specialty']} (subspecialty)")
            if len(sub_changes) > 10:
                print(f"  ... and {len(sub_changes) - 10} more")

    if dry_run and stats["changes"]:
        print(f"\n{'─' * 60}")
        print("To apply these changes, run:")
        print("  uv run python scripts/utils/normalize_specialties.py --apply")
        print(f"{'─' * 60}")


def main():
    parser = argparse.ArgumentParser(
        description="Normalize specialty values in complaints collection"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry-run)"
    )
    args = parser.parse_args()

    dry_run = not args.apply

    stats = run_normalization(dry_run=dry_run)
    print_report(stats, dry_run)


if __name__ == "__main__":
    main()
