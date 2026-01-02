#!/usr/bin/env python3
"""
Create MongoDB indexes for better query performance.

Usage:
    uv run python scripts/create_indexes.py
"""

import os
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING, DESCENDING

load_dotenv()


def create_indexes():
    """Create indexes for complaints and settlements collections."""
    mongo_uri = os.environ.get("MONGODB_URI")
    if not mongo_uri:
        raise ValueError("MONGODB_URI environment variable is required")

    client = MongoClient(mongo_uri)
    db = client["malpractice"]
    complaints = db["complaints"]
    settlements = db["settlements"]

    print("Creating indexes for complaints collection...")

    # Index for llm_extracted existence check (sparse)
    complaints.create_index(
        "llm_extracted",
        sparse=True,
        name="llm_extracted_sparse"
    )
    print("  - llm_extracted (sparse)")

    # Compound index for common filter combinations
    complaints.create_index(
        [
            ("llm_extracted.category", ASCENDING),
            ("llm_extracted.specialty", ASCENDING),
            ("year", DESCENDING),
        ],
        name="category_specialty_year"
    )
    print("  - category + specialty + year (compound)")

    # Index for year filtering and sorting
    complaints.create_index(
        "year",
        name="year_idx"
    )
    print("  - year")

    # Index for respondent sorting
    complaints.create_index(
        "respondent",
        name="respondent_idx"
    )
    print("  - respondent")

    print("\nCreating indexes for settlements collection...")

    # Index for llm_extracted existence check
    settlements.create_index(
        "llm_extracted",
        sparse=True,
        name="llm_extracted_sparse"
    )
    print("  - llm_extracted (sparse)")

    # Index for year in analytics queries
    settlements.create_index(
        "year",
        name="year_idx"
    )
    print("  - year")

    print("\nListing all indexes:")
    print("\ncomplaints:")
    for idx in complaints.list_indexes():
        print(f"  - {idx['name']}: {idx['key']}")

    print("\nsettlements:")
    for idx in settlements.list_indexes():
        print(f"  - {idx['name']}: {idx['key']}")

    client.close()
    print("\nDone!")


if __name__ == "__main__":
    create_indexes()
