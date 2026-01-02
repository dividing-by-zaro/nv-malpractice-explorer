#!/usr/bin/env python3
"""
Cron job wrapper to scrape and process new filings.

This script:
1. Scrapes the Nevada Medical Board for filings (current + previous year)
2. Checks MongoDB for existing filings by pdf_url
3. Downloads new PDFs to a temp directory
4. Processes each new filing through the pipeline
5. Cleans up temp files

Designed to run on Railway or any ephemeral environment.

Usage:
    uv run python scripts/process_new_filings.py              # Check current + previous year
    uv run python scripts/process_new_filings.py --dry-run    # Preview without processing
    uv run python scripts/process_new_filings.py --all-years  # Check all years (2008-present)
"""

import argparse
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pymongo import MongoClient

# Add scripts directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from process_single_file import (
    process_single_file,
    classify_document_type,
)

load_dotenv()

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

BASE_URL = "https://medboard.nv.gov"
REQUEST_DELAY = 1.0  # seconds between requests


# -----------------------------------------------------------------------------
# Scraping Functions (from scraper.py)
# -----------------------------------------------------------------------------

def get_filings_page(year: int, client: httpx.Client) -> str | None:
    """Fetch the public filings page for a given year. Returns None if page doesn't exist."""
    url = f"{BASE_URL}/Resources/Public/{year}_Public_Filings/"
    try:
        response = client.get(url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


def normalize_case_number(case_info: str) -> str:
    """
    Normalize case number, converting License No format to LICENSE-XXXX.

    Examples:
        "Case No 24-12345-1" -> "24-12345-1"
        "License No 10534" -> "LICENSE-10534"
        "License No RC36" -> "LICENSE-RC36"
    """
    case_info = case_info.strip()

    # Handle "Case No" prefix
    if case_info.lower().startswith("case no "):
        return case_info[8:].strip()

    # Handle "License No" format - convert to LICENSE-XXXX
    license_match = re.match(r'License No\.?\s*([A-Za-z]*\d+)', case_info, re.IGNORECASE)
    if license_match:
        return f"LICENSE-{license_match.group(1)}"

    return case_info


def parse_title(title_text: str) -> dict:
    """Parse title into type, respondent, and case number."""
    parts = title_text.split(" - ")

    if len(parts) >= 3:
        doc_type = parts[0].strip()
        respondent = parts[1].strip()
        case_number = normalize_case_number(parts[2].strip())
    elif len(parts) == 2:
        doc_type = parts[0].strip()
        respondent = parts[1].strip()
        case_number = ""
    else:
        doc_type = title_text.strip()
        respondent = ""
        case_number = ""

    return {
        "type": doc_type,
        "respondent": respondent,
        "case_number": case_number,
    }


def parse_filings_page(html: str, year: int) -> list[dict]:
    """Parse the HTML page and extract filing metadata."""
    soup = BeautifulSoup(html, "lxml")
    filings = []

    main_list = soup.find("ul", class_="main_list")
    if not main_list:
        return filings

    for li in main_list.find_all("li"):
        date_div = li.find("div", class_="main_list_date")
        date = date_div.get_text(strip=True) if date_div else ""

        title_div = li.find("div", class_="main_list_title")
        if not title_div:
            continue

        link = title_div.find("a")
        if not link:
            continue

        title_text = link.get_text(strip=True)
        href = link.get("href", "")

        parsed = parse_title(title_text)

        filing = {
            "year": year,
            "date": date,
            "title": title_text,
            "type": parsed["type"],
            "respondent": parsed["respondent"],
            "case_number": parsed["case_number"],
            "pdf_url": BASE_URL + href if href.startswith("/") else href,
        }
        filings.append(filing)

    return filings


def scrape_years(years: list[int], client: httpx.Client) -> list[dict]:
    """Scrape filings for the given years."""
    all_filings = []

    for year in years:
        print(f"  Scraping {year}...", end=" ")

        html = get_filings_page(year, client)
        if html is None:
            print("page not found")
            continue

        filings = parse_filings_page(html, year)
        print(f"found {len(filings)} filings")
        all_filings.extend(filings)

        time.sleep(REQUEST_DELAY)

    return all_filings


# -----------------------------------------------------------------------------
# MongoDB Functions
# -----------------------------------------------------------------------------

def get_mongo_client() -> MongoClient:
    """Create MongoDB client from environment variable."""
    mongo_uri = os.environ.get("MONGODB_URI")
    if not mongo_uri:
        raise ValueError("MONGODB_URI environment variable is required")
    return MongoClient(mongo_uri)


def get_existing_pdf_urls(db) -> set[str]:
    """Get all pdf_urls that already exist in the database."""
    existing = set()

    # Get from complaints collection
    for doc in db["complaints"].find({}, {"pdf_url": 1}):
        if doc.get("pdf_url"):
            existing.add(doc["pdf_url"])

    # Get from settlements collection
    for doc in db["settlements"].find({}, {"pdf_url": 1}):
        if doc.get("pdf_url"):
            existing.add(doc["pdf_url"])

    # Get from license_only_filings collection
    for doc in db["license_only_filings"].find({}, {"pdf_url": 1}):
        if doc.get("pdf_url"):
            existing.add(doc["pdf_url"])

    return existing


# -----------------------------------------------------------------------------
# Download Functions
# -----------------------------------------------------------------------------

def download_pdf_to_temp(filing: dict, client: httpx.Client, temp_dir: Path) -> Path | None:
    """Download a PDF to a temp directory. Returns the path or None on failure."""
    case_number = filing["case_number"] or "unknown"
    safe_case = "".join(c if c.isalnum() or c in "-_" else "_" for c in case_number)
    safe_type = "".join(c if c.isalnum() or c in "-_" else "_" for c in filing["type"][:30])
    filename = f"{safe_case}_{safe_type}.pdf"

    pdf_path = temp_dir / filename

    try:
        response = client.get(filing["pdf_url"], follow_redirects=True)
        response.raise_for_status()
        pdf_path.write_bytes(response.content)
        return pdf_path
    except Exception as e:
        print(f"    Error downloading: {e}")
        return None


# -----------------------------------------------------------------------------
# Main Function
# -----------------------------------------------------------------------------

def process_new_filings(
    years: list[int] | None = None,
    dry_run: bool = False,
    model: str = "gpt-4o",
) -> dict:
    """
    Main function to scrape and process new filings.

    Args:
        years: List of years to check. Defaults to current + previous year.
        dry_run: If True, don't download or process, just show what would be done.
        model: OpenAI model to use for extraction.

    Returns:
        dict with summary of results.
    """
    # Default to current year + previous year
    if years is None:
        current_year = datetime.now().year
        years = [current_year, current_year - 1]

    print(f"\n{'='*60}")
    print(f"Processing New Filings")
    print(f"{'='*60}")
    print(f"Years to check: {years}")
    print(f"Dry run: {dry_run}")
    print(f"Time: {datetime.now().isoformat()}")

    # Step 1: Scrape filings from website
    print(f"\nStep 1: Scraping filings...")
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        all_filings = scrape_years(years, client)

    print(f"  Total filings found: {len(all_filings)}")

    if not all_filings:
        print("\nNo filings found. Exiting.")
        return {"status": "no_filings", "total_found": 0}

    # Step 2: Check MongoDB for existing filings
    print(f"\nStep 2: Checking MongoDB for existing filings...")
    mongo_client = get_mongo_client()
    db = mongo_client["malpractice"]
    existing_urls = get_existing_pdf_urls(db)
    print(f"  Existing filings in DB: {len(existing_urls)}")

    # Step 3: Filter to new filings only
    new_filings = [f for f in all_filings if f["pdf_url"] not in existing_urls]

    # Also filter out ignored document types
    processable_filings = []
    ignored_filings = []
    for filing in new_filings:
        doc_class = classify_document_type(filing["type"], filing.get("case_number", ""))
        if doc_class == "ignored":
            ignored_filings.append(filing)
        else:
            filing["_classification"] = doc_class
            processable_filings.append(filing)

    print(f"\nStep 3: Filtering...")
    print(f"  New filings: {len(new_filings)}")
    print(f"  Processable (complaint/settlement/license_only): {len(processable_filings)}")
    print(f"  Ignored (other orders, etc.): {len(ignored_filings)}")

    if not processable_filings:
        print("\nNo new processable filings. Exiting.")
        return {
            "status": "no_new_filings",
            "total_found": len(all_filings),
            "already_processed": len(all_filings) - len(new_filings),
            "ignored": len(ignored_filings),
        }

    # Show what we'll process
    print(f"\nNew filings to process:")
    for f in processable_filings[:10]:
        print(f"  - [{f['_classification']}] {f['case_number']}: {f['type'][:50]}")
    if len(processable_filings) > 10:
        print(f"  ... and {len(processable_filings) - 10} more")

    if dry_run:
        print(f"\n[DRY RUN] Would process {len(processable_filings)} filings")
        return {
            "status": "dry_run",
            "total_found": len(all_filings),
            "new_filings": len(processable_filings),
            "ignored": len(ignored_filings),
        }

    # Step 4: Process each new filing
    print(f"\nStep 4: Processing {len(processable_filings)} new filings...")

    results = {
        "success": [],
        "failed": [],
        "errors": [],
    }

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for i, filing in enumerate(processable_filings, 1):
                case_number = filing["case_number"]
                doc_type = filing["type"]

                print(f"\n[{i}/{len(processable_filings)}] {case_number}: {doc_type[:40]}...")

                # Download PDF
                print(f"  Downloading...")
                pdf_path = download_pdf_to_temp(filing, client, temp_path)

                if not pdf_path:
                    print(f"  Failed to download")
                    results["failed"].append({
                        "case_number": case_number,
                        "error": "Download failed",
                    })
                    continue

                print(f"  Downloaded: {pdf_path.name}")

                # Process through pipeline (pass scraped metadata for date, respondent, etc.)
                try:
                    result = process_single_file(
                        pdf_path=pdf_path,
                        output_dir=temp_path,
                        dry_run=False,
                        model=model,
                        filing_metadata=filing,
                    )

                    if result.get("status") == "success":
                        results["success"].append({
                            "case_number": case_number,
                            "type": doc_type,
                            "classification": result.get("classification"),
                        })
                    else:
                        results["failed"].append({
                            "case_number": case_number,
                            "error": result.get("error", "Unknown error"),
                        })

                except Exception as e:
                    print(f"  Error: {e}")
                    results["errors"].append({
                        "case_number": case_number,
                        "error": str(e),
                    })

                # Brief delay between filings
                time.sleep(REQUEST_DELAY)

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total filings found: {len(all_filings)}")
    print(f"Already in database: {len(all_filings) - len(new_filings)}")
    print(f"New filings processed: {len(processable_filings)}")
    print(f"  - Success: {len(results['success'])}")
    print(f"  - Failed: {len(results['failed'])}")
    print(f"  - Errors: {len(results['errors'])}")
    print(f"Ignored (non-processable types): {len(ignored_filings)}")

    if results["failed"]:
        print(f"\nFailed filings:")
        for f in results["failed"][:5]:
            print(f"  - {f['case_number']}: {f['error']}")

    if results["errors"]:
        print(f"\nErrors:")
        for e in results["errors"][:5]:
            print(f"  - {e['case_number']}: {e['error']}")

    return {
        "status": "completed",
        "total_found": len(all_filings),
        "already_processed": len(all_filings) - len(new_filings),
        "new_processed": len(processable_filings),
        "success": len(results["success"]),
        "failed": len(results["failed"]),
        "errors": len(results["errors"]),
        "ignored": len(ignored_filings),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Scrape and process new filings from Nevada Medical Board",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    uv run python scripts/process_new_filings.py              # Current + previous year
    uv run python scripts/process_new_filings.py --dry-run    # Preview only
    uv run python scripts/process_new_filings.py --all-years  # All years (2008-present)
    uv run python scripts/process_new_filings.py --years 2024 2025
        """
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be processed without actually processing"
    )
    parser.add_argument(
        "--all-years",
        action="store_true",
        help="Check all years (2008-present) instead of just current + previous"
    )
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        help="Specific years to check (e.g., --years 2024 2025)"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o",
        help="OpenAI model to use (default: gpt-4o)"
    )

    args = parser.parse_args()

    # Determine years to check
    if args.years:
        years = args.years
    elif args.all_years:
        current_year = datetime.now().year
        years = list(range(2008, current_year + 1))
    else:
        years = None  # Will default to current + previous

    result = process_new_filings(
        years=years,
        dry_run=args.dry_run,
        model=args.model,
    )

    # Exit with appropriate code
    if result.get("status") in ["completed", "dry_run", "no_filings", "no_new_filings"]:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
