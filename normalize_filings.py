"""
Normalize filings data to fix common issues:
1. Fix missing spaces in titles (e.g., "Complaint- " -> "Complaint - ")
2. Handle License No as case numbers for voluntary surrenders
3. Expand multiple case numbers into separate entries
4. Parse titles with comprehensive credential support
5. Fix typos in "Case No" / "License No"
6. Strip leading zeros from doc numbers
7. Handle comma-separated titles (e.g., "Name, MD, Case No")
8. Fix stuck "pdf" suffix in case numbers
9. Handle "and" in multi-case numbers
10. Assign case numbers to denials
"""

import json
import re
from pathlib import Path
from copy import deepcopy

DATA_DIR = Path("data")

# Comprehensive list of medical credentials
CREDENTIALS = [
    # Physicians
    "MD", "DO", "MBBS", "MBChB",
    # Physician Assistants
    "PA", "PA-C", "PA-S",
    # Nursing
    "RN", "NP", "APRN", "LPN", "CNM", "CRNA", "FNP", "DNP",
    # Therapy/Rehab
    "PT", "DPT", "OT", "OTR", "RT", "RRT", "CRT",
    # Podiatry/Chiropractic/Optometry
    "DPM", "DC", "OD",
    # Dental
    "DDS", "DMD",
    # Pharmacy
    "PharmD", "RPh",
    # Psychology/Counseling
    "PhD", "PsyD", "LCSW", "LPC", "LMFT",
    # Other
    "EMT", "Paramedic", "MA", "CMA", "RMA",
]

# Track denial case numbers for assignment
denial_counter = {}


def fix_title_spacing(title: str) -> str:
    """
    Fix missing spaces around dashes in titles.
    "Complaint- John Doe" -> "Complaint - John Doe"
    "Complaint -John Doe" -> "Complaint - John Doe"

    Only fix dashes followed/preceded by letters, not numbers (to preserve case numbers like -2, -3)
    """
    # Fix "Word- Letter" pattern (missing space before dash, followed by letter)
    title = re.sub(r'([a-zA-Z])- ([a-zA-Z])', r'\1 - \2', title)
    # Fix "Letter -Word" pattern (missing space after dash, preceded by letter)
    title = re.sub(r'([a-zA-Z]) -([a-zA-Z])', r'\1 - \2', title)
    return title


def fix_case_number_typos(case_info: str) -> str:
    """Fix common typos in case/license number prefixes."""
    # Fix "Cae No" -> "Case No"
    case_info = re.sub(r'\bCae No\b', 'Case No', case_info, flags=re.IGNORECASE)
    # Fix "Csae No" -> "Case No"
    case_info = re.sub(r'\bCsae No\b', 'Case No', case_info, flags=re.IGNORECASE)
    # Fix "Licene No" -> "License No"
    case_info = re.sub(r'\bLicene No\b', 'License No', case_info, flags=re.IGNORECASE)
    # Fix "Case No_" or "Case No." -> "Case No " (underscore/period typo)
    case_info = re.sub(r'\bCase No[_\.]\s*', 'Case No ', case_info, flags=re.IGNORECASE)
    # Fix "Case " without "No" -> "Case No "
    case_info = re.sub(r'\bCase (\d)', r'Case No \1', case_info)
    return case_info


def fix_case_number_format(case_number: str) -> str:
    """
    Fix various case number format issues:
    - Strip leading zeros from doc numbers: 05-9441-01 -> 05-9441-1
    - Remove stuck 'pdf' suffix: 08-12069-1pdf -> 08-12069-1
    - Fix missing dash in long numbers: 13-1001401 -> 13-10014-1
    """
    if not case_number:
        return case_number

    # Remove stuck 'pdf' suffix
    case_number = re.sub(r'pdf$', '', case_number, flags=re.IGNORECASE)

    # Strip leading zeros from doc number: XX-XXXXX-01 -> XX-XXXXX-1
    case_number = re.sub(r'^(\d+-\d+)-0+(\d+)$', r'\1-\2', case_number)

    # Fix missing dash in malformed numbers like 13-1001401 -> 13-10014-1
    # Pattern: YY-XXXXXXX where second part is 6+ digits (should be split)
    match = re.match(r'^(\d{2})-(\d{5,})(\d)$', case_number)
    if match:
        year = match.group(1)
        case_id = match.group(2)
        doc_num = match.group(3)
        case_number = f"{year}-{case_id}-{doc_num}"

    return case_number


def handle_comma_separated_title(title: str) -> str:
    """
    Convert comma-separated titles to dash-separated.
    "First Amended Complaint - Paul Ludlow, MD, Case No 11-5171-1"
    -> "First Amended Complaint - Paul Ludlow, MD - Case No 11-5171-1"
    """
    # Pattern: "Name, CREDENTIAL, Case No" -> "Name, CREDENTIAL - Case No"
    title = re.sub(r', (Case Nos?\b)', r' - \1', title, flags=re.IGNORECASE)
    return title


def parse_title_improved(title: str) -> dict:
    """
    Parse title into type, respondent, and case number with improved logic.
    Handles various credential formats.
    """
    # First fix spacing and comma separators
    title = fix_title_spacing(title)
    title = handle_comma_separated_title(title)

    parts = title.split(" - ")

    if len(parts) >= 3:
        doc_type = parts[0].strip()
        respondent = parts[1].strip()
        # Everything after second " - " is case info
        case_info = " - ".join(parts[2:]).strip()

        # Fix typos in case info
        case_info = fix_case_number_typos(case_info)

        # Extract case number, handling various formats
        case_number = extract_case_number(case_info)

        return {
            "type": doc_type,
            "respondent": respondent,
            "case_number": case_number,
            "case_info_raw": case_info,
        }
    elif len(parts) == 2:
        doc_type = parts[0].strip()
        # Second part could be respondent or case info
        second = parts[1].strip()

        # Fix typos
        second_fixed = fix_case_number_typos(second)

        # Check if it looks like a case number
        if "Case No" in second_fixed or "License No" in second_fixed:
            return {
                "type": doc_type,
                "respondent": "",
                "case_number": extract_case_number(second_fixed),
                "case_info_raw": second_fixed,
            }
        else:
            return {
                "type": doc_type,
                "respondent": second,
                "case_number": "",
                "case_info_raw": "",
            }
    else:
        return {
            "type": title.strip(),
            "respondent": "",
            "case_number": "",
            "case_info_raw": "",
        }


def extract_case_number(case_info: str) -> str:
    """
    Extract case number from case info string.
    Handles:
    - "Case No 25-8654-1"
    - "License No 21350"
    - "License No RC36" (with letter prefix)
    - "Case Nos 24-22461-1, -2, -3, -4" (returns as-is for later expansion)
    - "12-6816-1 and 13-6816-1" (returns as-is for later expansion)
    """
    case_info = case_info.strip()

    # Handle License No format - convert to case number format
    # Support both numeric and alphanumeric (RC36, RC1428)
    license_match = re.match(r'License No\.?\s*([A-Za-z]*\d+)', case_info, re.IGNORECASE)
    if license_match:
        return f"LICENSE-{license_match.group(1)}"

    # Handle Case Nos (multiple) - check this BEFORE singular Case No
    cases_match = re.match(r'Case Nos\.?\s*(.+)', case_info, re.IGNORECASE)
    if cases_match:
        return cases_match.group(1).strip()

    # Handle standard Case No format (singular)
    case_match = re.match(r'Case No\.?\s*(.+)', case_info, re.IGNORECASE)
    if case_match:
        return case_match.group(1).strip()

    return case_info


def expand_multiple_case_numbers(case_number: str, base_case_id: str = None) -> list[str]:
    """
    Expand condensed case numbers into individual entries.

    "24-22461-1, -2, -3, -4" -> ["24-22461-1", "24-22461-2", "24-22461-3", "24-22461-4"]
    "24-11896-1, 25-11896-1, -2, -3" -> ["24-11896-1", "25-11896-1", "25-11896-2", "25-11896-3"]
    "12-6816-1 and 13-6816-1" -> ["12-6816-1", "13-6816-1"]
    """
    if not case_number:
        return []

    # Handle "and" separator (convert to comma for unified processing)
    case_number = case_number.replace(" and ", ", ")

    if "," not in case_number:
        return [case_number]

    parts = [p.strip() for p in case_number.split(",")]
    expanded = []
    current_base = None

    for part in parts:
        if re.match(r'^\d+-\d+-\d+$', part):
            # Full case number like "24-22461-1"
            expanded.append(part)
            # Extract base for subsequent short references
            match = re.match(r'^(\d+-\d+)-\d+$', part)
            if match:
                current_base = match.group(1)
        elif re.match(r'^-\d+$', part) and current_base:
            # Short reference like "-2"
            doc_num = part[1:]  # Remove leading dash
            expanded.append(f"{current_base}-{doc_num}")
        elif re.match(r'^\d+$', part) and current_base:
            # Just a number like "2"
            expanded.append(f"{current_base}-{part}")
        else:
            # Unknown format, keep as-is
            expanded.append(part)

    return expanded


def assign_denial_case_number(filing: dict) -> str:
    """
    Assign a case number to denial filings that don't have one.
    Format: YY-00000-N where YY is year and N is sequence number.
    """
    year = filing.get("year", 2021)
    year_short = str(year)[2:]  # "2021" -> "21"

    if year not in denial_counter:
        denial_counter[year] = 0

    denial_counter[year] += 1
    return f"{year_short}-00000-{denial_counter[year]}"


def normalize_filing(filing: dict) -> list[dict]:
    """
    Normalize a single filing, potentially returning multiple entries
    if the filing has multiple case numbers.
    """
    # Fix the title first
    original_title = filing.get("title", "")
    fixed_title = fix_title_spacing(original_title)
    fixed_title = handle_comma_separated_title(fixed_title)

    # Re-parse the title
    parsed = parse_title_improved(fixed_title)

    # Get case numbers (may be multiple)
    case_number_raw = parsed["case_number"]

    # Fix case number format issues
    case_number_raw = fix_case_number_format(case_number_raw)

    # Expand multiple case numbers
    case_numbers = expand_multiple_case_numbers(case_number_raw)

    # Handle empty case numbers
    if not case_numbers or case_numbers == [""]:
        # Check if this is a denial (no case number expected)
        if "Denying Application" in original_title:
            case_numbers = [assign_denial_case_number(filing)]
        else:
            case_numbers = [""]

    # Fix format for each case number
    case_numbers = [fix_case_number_format(cn) for cn in case_numbers]

    # Create an entry for each case number
    results = []
    for case_num in case_numbers:
        normalized = deepcopy(filing)
        normalized["title"] = fixed_title
        normalized["title_original"] = original_title
        normalized["type"] = parsed["type"]
        normalized["respondent"] = parsed["respondent"]
        normalized["case_number"] = case_num
        normalized["case_info_raw"] = parsed["case_info_raw"]

        # Flag if this was expanded from multiple
        if len(case_numbers) > 1:
            normalized["expanded_from"] = case_number_raw
            normalized["sibling_case_numbers"] = case_numbers

        results.append(normalized)

    return results


def normalize_all_filings(filings: list[dict]) -> list[dict]:
    """Normalize all filings."""
    normalized = []

    # Reset denial counter
    global denial_counter
    denial_counter = {}

    for filing in filings:
        normalized.extend(normalize_filing(filing))

    return normalized


def print_normalization_summary(original: list[dict], normalized: list[dict]):
    """Print summary of normalization changes."""
    print(f"\n{'='*60}")
    print("NORMALIZATION SUMMARY")
    print(f"{'='*60}")
    print(f"Original filings: {len(original)}")
    print(f"Normalized entries: {len(normalized)}")
    print(f"Entries added (from multi-case expansion): {len(normalized) - len(original)}")

    # Count fixes
    title_fixes = sum(1 for n in normalized if n.get("title") != n.get("title_original"))
    expanded = sum(1 for n in normalized if n.get("expanded_from"))
    license_nos = sum(1 for n in normalized if n.get("case_number", "").startswith("LICENSE-"))
    denials = sum(1 for n in normalized if "-00000-" in n.get("case_number", ""))

    print(f"\nFixes applied:")
    print(f"  Title spacing/comma fixes: {title_fixes}")
    print(f"  Expanded multi-case entries: {expanded}")
    print(f"  License No conversions: {license_nos}")
    print(f"  Denial case numbers assigned: {denials}")

    # Show examples of each fix type
    print(f"\n{'-'*50}")
    print("Examples of fixes:")

    # Title fixes
    title_fixed = [n for n in normalized if n.get("title") != n.get("title_original")][:3]
    if title_fixed:
        print(f"\nTitle fixes:")
        for f in title_fixed:
            print(f"  BEFORE: {f['title_original'][:70]}...")
            print(f"  AFTER:  {f['title'][:70]}...")

    # Expanded entries
    expanded_examples = [n for n in normalized if n.get("expanded_from")][:3]
    if expanded_examples:
        print(f"\nMulti-case expansions:")
        for f in expanded_examples:
            print(f"  Original: {f['expanded_from']}")
            print(f"  Expanded to: {f['case_number']}")

    # License numbers
    license_examples = [n for n in normalized if n.get("case_number", "").startswith("LICENSE-")][:3]
    if license_examples:
        print(f"\nLicense No conversions:")
        for f in license_examples:
            print(f"  {f['respondent']} -> {f['case_number']}")

    # Denials
    denial_examples = [n for n in normalized if "-00000-" in n.get("case_number", "")][:3]
    if denial_examples:
        print(f"\nDenial case numbers:")
        for f in denial_examples:
            print(f"  {f['respondent']} -> {f['case_number']}")


def main():
    # Load filings
    filings_path = DATA_DIR / "filings.json"
    test_path = DATA_DIR / "test_2025.json"

    if filings_path.exists():
        with open(filings_path) as f:
            data = json.load(f)
        filings = data.get("filings", [])
        source = "filings.json"
    elif test_path.exists():
        with open(test_path) as f:
            filings = json.load(f)
        data = {"filings": filings}
        source = "test_2025.json"
    else:
        print("Error: No filings data found. Run scraper.py first.")
        return

    print(f"Loaded {len(filings)} filings from {source}")

    # Normalize
    normalized = normalize_all_filings(filings)

    # Print summary
    print_normalization_summary(filings, normalized)

    # Save normalized data
    output = {
        "total_filings": len(normalized),
        "original_count": len(filings),
        "years": data.get("years", []),
        "filings": normalized,
        "errors": data.get("errors", []),
    }

    output_path = DATA_DIR / "filings_normalized.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n\nNormalized data saved to: {output_path}")


if __name__ == "__main__":
    main()
