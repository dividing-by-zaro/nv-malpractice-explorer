"""Quick test of the scraper on a single year."""

import json
from pathlib import Path
import httpx
from bs4 import BeautifulSoup
from scraper import get_filings_page, parse_filings_page, BASE_URL

# Test on 2025
year = 2025

with httpx.Client(timeout=30.0, follow_redirects=True) as client:
    print(f"Fetching {year} filings page...")
    html = get_filings_page(year, client)

    print(f"Parsing HTML...")
    filings = parse_filings_page(html, year)

    print(f"\nFound {len(filings)} filings for {year}")
    print(f"\nFirst 3 filings:")
    for f in filings[:3]:
        print(f"\n  Date: {f['date']}")
        print(f"  Type: {f['type']}")
        print(f"  Respondent: {f['respondent']}")
        print(f"  Case Number: {f['case_number']}")
        print(f"  PDF URL: {f['pdf_url']}")

    # Save test output
    Path("data").mkdir(exist_ok=True)
    with open("data/test_2025.json", "w") as f:
        json.dump(filings, f, indent=2)
    print(f"\nSaved all {year} metadata to data/test_2025.json")
