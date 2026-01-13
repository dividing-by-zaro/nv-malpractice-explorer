#!/usr/bin/env python3
"""
Add related links to cases.

Usage:
    # Add a link to a case
    uv run python scripts/add_link.py 19-28023-1 "https://example.com/article" --title "News Coverage"

    # Add a link without a title (URL will be displayed)
    uv run python scripts/add_link.py 19-28023-1 "https://example.com/article"

    # View existing links for a case
    uv run python scripts/add_link.py 19-28023-1 --list

    # Remove a link by URL
    uv run python scripts/add_link.py 19-28023-1 --remove "https://example.com/article"
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from pymongo import MongoClient

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lib import TrackedDB

load_dotenv()


def get_db():
    """Get MongoDB database connection."""
    mongo_uri = os.environ.get("MONGODB_URI")
    if not mongo_uri:
        print("Error: MONGODB_URI environment variable not set")
        sys.exit(1)
    client = MongoClient(mongo_uri)
    return client["malpractice"]


def list_links(db, case_number: str) -> None:
    """List all links for a case."""
    doc = db["complaints"].find_one({"case_number": case_number})

    if not doc:
        print(f"Error: Case {case_number} not found")
        sys.exit(1)

    links = doc.get("related_links", [])

    if not links:
        print(f"No links for case {case_number}")
        return

    print(f"\nLinks for case {case_number}:")
    print("-" * 50)
    for i, link in enumerate(links, 1):
        title = link.get("title") or "(no title)"
        print(f"{i}. {title}")
        print(f"   {link['url']}")
    print()


def add_link(db, case_number: str, url: str, title: str | None, reason: str) -> None:
    """Add a link to a case."""
    # Check case exists
    doc = db["complaints"].find_one({"case_number": case_number})
    if not doc:
        print(f"Error: Case {case_number} not found")
        sys.exit(1)

    # Check if URL already exists
    existing_links = doc.get("related_links", [])
    for link in existing_links:
        if link["url"] == url:
            print(f"Error: URL already exists for case {case_number}")
            sys.exit(1)

    # Build the new link
    new_link = {"url": url}
    if title:
        new_link["title"] = title

    # Use TrackedDB to add the link
    tracked = TrackedDB(db, script="add_link.py")

    if existing_links:
        # Append to existing array
        tracked.update_one(
            collection="complaints",
            filter={"case_number": case_number},
            update={"$push": {"related_links": new_link}},
            document_key=case_number,
            reason=reason or f"Add link: {title or url}",
        )
    else:
        # Create the array
        tracked.update_one(
            collection="complaints",
            filter={"case_number": case_number},
            update={"$set": {"related_links": [new_link]}},
            document_key=case_number,
            reason=reason or f"Add link: {title or url}",
        )

    print(f"Added link to case {case_number}")
    if title:
        print(f"  Title: {title}")
    print(f"  URL: {url}")


def remove_link(db, case_number: str, url: str) -> None:
    """Remove a link from a case by URL."""
    # Check case exists
    doc = db["complaints"].find_one({"case_number": case_number})
    if not doc:
        print(f"Error: Case {case_number} not found")
        sys.exit(1)

    existing_links = doc.get("related_links", [])
    if not existing_links:
        print(f"Error: No links exist for case {case_number}")
        sys.exit(1)

    # Find the link to remove
    link_to_remove = None
    for link in existing_links:
        if link["url"] == url:
            link_to_remove = link
            break

    if not link_to_remove:
        print(f"Error: URL not found in case {case_number}")
        sys.exit(1)

    # Use TrackedDB to remove the link
    tracked = TrackedDB(db, script="add_link.py")
    tracked.update_one(
        collection="complaints",
        filter={"case_number": case_number},
        update={"$pull": {"related_links": {"url": url}}},
        document_key=case_number,
        reason=f"Remove link: {link_to_remove.get('title') or url}",
    )

    print(f"Removed link from case {case_number}")
    print(f"  URL: {url}")


def main():
    parser = argparse.ArgumentParser(
        description="Add related links to cases",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("case_number", help="Case number (e.g., 19-28023-1)")
    parser.add_argument("url", nargs="?", help="URL to add")
    parser.add_argument("--title", "-t", help="Title for the link")
    parser.add_argument("--reason", "-r", help="Reason for adding (for change log)")
    parser.add_argument("--list", "-l", action="store_true", help="List existing links")
    parser.add_argument("--remove", metavar="URL", help="Remove a link by URL")

    args = parser.parse_args()

    db = get_db()

    if args.list:
        list_links(db, args.case_number)
    elif args.remove:
        remove_link(db, args.case_number, args.remove)
    elif args.url:
        add_link(db, args.case_number, args.url, args.title, args.reason)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
