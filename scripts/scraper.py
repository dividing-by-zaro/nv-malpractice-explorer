"""
Scraper for Nevada Medical Board Public Filings (2008-2025)
Downloads PDFs and extracts metadata from public malpractice filings.
"""

import json
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://medboard.nv.gov"
YEARS = range(2008, 2026)  # 2008-2025 inclusive
REQUEST_DELAY = 1.0  # seconds between requests
DATA_DIR = Path("data")
PDF_DIR = Path("pdfs")


def get_filings_page(year: int, client: httpx.Client) -> str:
    """Fetch the public filings page for a given year."""
    url = f"{BASE_URL}/Resources/Public/{year}_Public_Filings/"
    response = client.get(url)
    response.raise_for_status()
    return response.text


def parse_title(title_text: str) -> dict:
    """
    Parse title into type, respondent, and case number.
    Format: "Type - Respondent Name, Credentials - Case No XX-XXXX-X"
    """
    parts = title_text.split(" - ")

    if len(parts) >= 3:
        doc_type = parts[0].strip()
        respondent = parts[1].strip()
        # Case number might have "Case No " prefix
        case_number = parts[2].strip()
        if case_number.lower().startswith("case no "):
            case_number = case_number[8:].strip()
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
        print(f"  Warning: Could not find main_list for year {year}")
        return filings

    for li in main_list.find_all("li"):
        # Extract date
        date_div = li.find("div", class_="main_list_date")
        date = date_div.get_text(strip=True) if date_div else ""

        # Extract title and URL
        title_div = li.find("div", class_="main_list_title")
        if not title_div:
            continue

        link = title_div.find("a")
        if not link:
            continue

        title_text = link.get_text(strip=True)
        href = link.get("href", "")

        # Parse the title components
        parsed = parse_title(title_text)

        filing = {
            "year": year,
            "date": date,
            "title": title_text,
            "type": parsed["type"],
            "respondent": parsed["respondent"],
            "case_number": parsed["case_number"],
            "pdf_url": BASE_URL + href if href.startswith("/") else href,
            "relative_path": href,
        }
        filings.append(filing)

    return filings


def download_pdf(filing: dict, client: httpx.Client) -> bool:
    """Download a PDF for a filing. Returns True if successful."""
    year = filing["year"]
    case_number = filing["case_number"] or "unknown"
    # Sanitize filename
    safe_case = "".join(c if c.isalnum() or c in "-_" else "_" for c in case_number)

    pdf_dir = PDF_DIR / str(year)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    # Create unique filename using case number and part of title
    safe_type = "".join(c if c.isalnum() or c in "-_" else "_" for c in filing["type"][:30])
    filename = f"{safe_case}_{safe_type}.pdf"
    pdf_path = pdf_dir / filename

    if pdf_path.exists():
        print(f"    Skipping (exists): {filename}")
        return True

    try:
        response = client.get(filing["pdf_url"], follow_redirects=True)
        response.raise_for_status()

        pdf_path.write_bytes(response.content)
        print(f"    Downloaded: {filename}")
        return True
    except Exception as e:
        print(f"    Error downloading {filename}: {e}")
        return False


def scrape_all(download_pdfs: bool = True):
    """Main scraping function."""
    DATA_DIR.mkdir(exist_ok=True)
    PDF_DIR.mkdir(exist_ok=True)

    all_filings = []
    errors = []

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for year in YEARS:
            print(f"\nProcessing year {year}...")

            try:
                html = get_filings_page(year, client)
                filings = parse_filings_page(html, year)
                print(f"  Found {len(filings)} filings")

                if download_pdfs:
                    for filing in filings:
                        download_pdf(filing, client)
                        time.sleep(REQUEST_DELAY)

                all_filings.extend(filings)

            except Exception as e:
                error_msg = f"Error processing year {year}: {e}"
                print(f"  {error_msg}")
                errors.append(error_msg)

            time.sleep(REQUEST_DELAY)

    # Save metadata
    output_path = DATA_DIR / "filings.json"
    with open(output_path, "w") as f:
        json.dump({
            "total_filings": len(all_filings),
            "years": list(YEARS),
            "filings": all_filings,
            "errors": errors,
        }, f, indent=2)

    print(f"\n{'='*50}")
    print(f"Scraping complete!")
    print(f"Total filings: {len(all_filings)}")
    print(f"Errors: {len(errors)}")
    print(f"Metadata saved to: {output_path}")


def scrape_metadata_only():
    """Scrape just the metadata without downloading PDFs."""
    scrape_all(download_pdfs=False)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--metadata-only":
        scrape_metadata_only()
    else:
        scrape_all()
