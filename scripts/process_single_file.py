#!/usr/bin/env python3
"""
Unified pipeline script to process a single PDF file end-to-end.

This script:
1. Determines document type (complaint, settlement, or ignored)
2. OCRs the PDF and extracts text
3. Cleans the OCR text
4. Processes through LLM for data extraction
5. Stores in MongoDB with appropriate linking

For complaints:
- If amended, replaces existing complaint and generates amendment summary

For settlements:
- Links to associated complaint(s) via case_numbers

Usage:
    uv run python scripts/process_single_file.py path/to/file.pdf
    uv run python scripts/process_single_file.py path/to/file.pdf --dry-run
    uv run python scripts/process_single_file.py path/to/file.pdf --skip-ocr  # If text already exists
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pymongo import MongoClient

load_dotenv()

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = Path(__file__).parent / "prompts"

# License-only case number pattern (e.g., LICENSE-401, LICENSE-3298)
LICENSE_ONLY_PATTERN = re.compile(r"^LICENSE-\d+$", re.IGNORECASE)

# Case number normalization pattern (strips leading zeros from suffix)
LEADING_ZERO_PATTERN = re.compile(r"^(\d+-\d+)-0+(\d+)$")

# Document type classification
COMPLAINT_TYPES = {
    "Complaint": 1,
    "Complaint and Request for Summary Suspension": 1,
    "Amended Complaint": 2,
    "First Amended Complaint": 2,
    "Second Amended Complaint": 3,
    "Third Amended Complaint": 4,
}

SETTLEMENT_TYPES = [
    # Primary settlement types
    "Settlement Agreement and Order",
    "Settlement, Waiver and Consent Agreement",
    "Settlement, Waiver and Consent Agreement and Order",
    "Settlement Agreement",
    # Amended settlements
    "Amended Settlement Agreement and Order",
    "First Amended Settlement Agreement and Order",
    "Settlement Agreement and Order Lifting Suspension",
    # Combined stipulation + settlement
    "Stipulation and Settlement, Waiver and Consent Agreement and Order",
    # Consent agreements (functionally settlements)
    "Consent Agreement for Revocation of License",
    # Modification orders (update existing settlements)
    "Order Modifying Previously Approved Settlement Agreement",
    "Order Modifying Terms of Previously Approved Settlement Agreement",
    "Order Modifying Conditions of Settlement Agreement",
    "Order Amending Settlement Agreement",
    "Stipulation and Order Amending Terms of Settlement Agreement",
    "Addendum to Previously Adopted Settlement",
    "Order Vacating Remaining Term of Previously Adopted Settlement, Waiver and Consent Agreement",
    # Findings of Fact (contested cases that went to hearing)
    "Findings of Fact, Conclusions of Law and Order",
    "Findings of Fact, Conclusions of Law, and Order",
    "Amended Findings of Fact, Conclusions of Law and Order",
    "Findings of Fact, Conclustions of Law and Order",  # Typo in source data
]

# OCR text cleaning patterns
CLEANING_PATTERNS = {
    "only_numbers": re.compile(r"^\s*\d+\s*$"),
    "page_numbers": re.compile(r"^\s*\d+\s+of\s+\d+\s*$", re.IGNORECASE),
    "slash_markers": re.compile(r"^\s*[/\\|lI1!]{2,}\s*$"),
    "only_punctuation": re.compile(r"^\s*[^\w\s]+\s*$"),
    "k_dividers": re.compile(r"^\s*[KkEeRr\s\*]{3,}\s*$"),
    "ocr_page_markers": re.compile(r"^\s*(Hf|Hil|M1|M1\}|H!|I!|Il|1l)\s*$"),
    "single_symbols": re.compile(r"^\s*[>\-—=]\s*$"),
    "ss_artifacts": re.compile(r"^\s*:?\s*SS\.\s*$", re.IGNORECASE),
    "exhibit_number_only": re.compile(r"^\s*\d\s*$"),
}


# -----------------------------------------------------------------------------
# Document Type Detection
# -----------------------------------------------------------------------------

def is_license_only_case(case_number: str) -> bool:
    """
    Check if a case number is a license-only identifier.

    License-only documents are administrative actions tied to a license number
    rather than a formal complaint case number (e.g., LICENSE-401, LICENSE-3298).
    """
    return bool(LICENSE_ONLY_PATTERN.match(case_number or ""))


def fix_case_number_format(case_number: str) -> str:
    """
    Normalize case number format for consistent matching.

    Fixes:
    - Strip leading zeros from doc suffix: 19-32539-01 -> 19-32539-1
    - Remove stuck 'pdf' suffix: 08-12069-1pdf -> 08-12069-1

    This ensures complaint and settlement case numbers match during linking.
    """
    if not case_number:
        return case_number

    # Remove stuck 'pdf' suffix
    case_number = re.sub(r"pdf$", "", case_number, flags=re.IGNORECASE)

    # Strip leading zeros from doc number: XX-XXXXX-01 -> XX-XXXXX-1
    case_number = LEADING_ZERO_PATTERN.sub(r"\1-\2", case_number)

    return case_number


def classify_document_type(doc_type: str, case_number: str = "") -> str:
    """
    Classify a document as 'complaint', 'settlement', 'license_only', or 'ignored'.

    Args:
        doc_type: The document type string from the filename or metadata
        case_number: The case number (used to detect license-only documents)

    Returns:
        'complaint', 'settlement', 'license_only', or 'ignored'
    """
    # Check if it's a license-only document (tied to license number, not case)
    if is_license_only_case(case_number):
        return "license_only"

    # Check if it's a complaint type
    if doc_type in COMPLAINT_TYPES:
        return "complaint"

    # Check if it's a settlement type (exact match or prefix match)
    for settlement_type in SETTLEMENT_TYPES:
        if doc_type == settlement_type or doc_type.startswith(settlement_type):
            return "settlement"

    return "ignored"


def parse_filename(filepath: Path) -> dict:
    """
    Parse a PDF filename to extract metadata.

    Expected format: {case_number}_{document_type}.pdf
    Example: 24-12345-1_Complaint.pdf
             24-12345-1_Settlement_Agreement_and_Order.pdf

    Returns dict with: case_number, type, year
    """
    stem = filepath.stem  # Filename without extension

    # Split on first underscore to get case_number and type
    parts = stem.split("_", 1)
    if len(parts) < 2:
        return {
            "case_number": fix_case_number_format(stem),
            "type": "Unknown",
            "year": None,
        }

    case_number = fix_case_number_format(parts[0])
    doc_type = parts[1].replace("_", " ")

    # Extract year from case number (format: YY-XXXXX-N)
    year = None
    year_match = re.match(r"^(\d{2})-", case_number)
    if year_match:
        year_prefix = int(year_match.group(1))
        # Convert 2-digit year to 4-digit (00-30 = 2000-2030, 31-99 = 1931-1999)
        year = 2000 + year_prefix if year_prefix <= 30 else 1900 + year_prefix

    return {
        "case_number": case_number,
        "type": doc_type,
        "year": year,
    }


def is_amended_complaint(doc_type: str) -> bool:
    """Check if document type is an amended complaint."""
    return doc_type in COMPLAINT_TYPES and COMPLAINT_TYPES.get(doc_type, 0) > 1


# -----------------------------------------------------------------------------
# OCR Processing
# -----------------------------------------------------------------------------

# OCR timeout constants
OCR_TIMEOUT_BASE = 60        # Base timeout in seconds
OCR_TIMEOUT_PER_PAGE = 30    # Additional seconds per page
OCR_TIMEOUT_MIN = 120        # Minimum timeout (2 minutes)
OCR_TIMEOUT_MAX = 1800       # Maximum timeout (30 minutes)


def check_ocr_dependencies() -> bool:
    """Check that required OCR tools are installed."""
    missing = []
    for cmd in ["ocrmypdf", "pdftotext", "pdfinfo"]:
        result = subprocess.run(["which", cmd], capture_output=True)
        if result.returncode != 0:
            missing.append(cmd)

    if missing:
        print(f"Missing required tools: {', '.join(missing)}")
        print("Install with: brew install ocrmypdf poppler")
        return False
    return True


def get_page_count(pdf_path: Path) -> int:
    """
    Get the page count of a PDF using pdfinfo.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        Number of pages in the PDF

    Raises:
        ValueError: If pdfinfo fails to parse the PDF
    """
    try:
        result = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise ValueError(f"pdfinfo failed: {result.stderr[:200]}")

        for line in result.stdout.split("\n"):
            if line.startswith("Pages:"):
                return int(line.split(":")[1].strip())

        raise ValueError("Could not find page count in pdfinfo output")

    except subprocess.TimeoutExpired:
        raise ValueError("pdfinfo timed out")


def calculate_ocr_timeout(page_count: int) -> int:
    """
    Calculate OCR timeout based on page count.

    Formula: max(MIN, min(MAX, BASE + pages * PER_PAGE))
    - 1 page: 120s (minimum)
    - 5 pages: 210s (3.5 min)
    - 10 pages: 360s (6 min)
    - 20 pages: 660s (11 min)
    - 50 pages: 1560s (26 min)
    - 60+ pages: 1800s (30 min, capped)

    Args:
        page_count: Number of pages in the PDF

    Returns:
        Timeout in seconds
    """
    calculated = OCR_TIMEOUT_BASE + (page_count * OCR_TIMEOUT_PER_PAGE)
    return max(OCR_TIMEOUT_MIN, min(OCR_TIMEOUT_MAX, calculated))


def ocr_pdf(input_path: Path, output_pdf_path: Path, output_text_path: Path, timeout: int) -> dict:
    """
    OCR a single PDF and extract text.

    Args:
        input_path: Path to input PDF
        output_pdf_path: Path for searchable PDF output
        output_text_path: Path for extracted text output
        timeout: Timeout in seconds

    Returns:
        dict with success, error, word_count, duration_seconds, timeout_used
    """
    start_time = time.time()

    # Create output directories
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    output_text_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        cmd = [
            "ocrmypdf",
            "--sidecar", str(output_text_path),
            "--rotate-pages",
            "--deskew",
            "--clean",
            "--force-ocr",
            "-l", "eng",
            "--jobs", "2",
            str(input_path),
            str(output_pdf_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        duration = time.time() - start_time

        if result.returncode != 0:
            # Check for "already has text" case
            if "page already has text" in result.stderr.lower() or result.returncode == 6:
                shutil.copy(input_path, output_pdf_path)
                try:
                    subprocess.run(
                        ["pdftotext", str(input_path), str(output_text_path)],
                        capture_output=True,
                        timeout=60,
                    )
                except Exception:
                    output_text_path.write_text("")
            else:
                return {
                    "success": False,
                    "error": result.stderr[:500] if result.stderr else f"Return code {result.returncode}",
                    "word_count": None,
                    "duration_seconds": duration,
                }

        # Get word count
        word_count = None
        if output_text_path.exists():
            text_content = output_text_path.read_text(errors="ignore")
            word_count = len(text_content.split())

        return {
            "success": True,
            "error": None,
            "word_count": word_count,
            "duration_seconds": duration,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Timeout after {timeout} seconds",
            "word_count": None,
            "duration_seconds": time.time() - start_time,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)[:500],
            "word_count": None,
            "duration_seconds": time.time() - start_time,
        }


# -----------------------------------------------------------------------------
# Text Cleaning
# -----------------------------------------------------------------------------

def is_gibberish_line(line: str) -> bool:
    """Detect OCR gibberish from margin line numbers."""
    stripped = line.strip()
    if not stripped:
        return False

    words = stripped.split()
    if len(words) < 2:
        return False

    short_words = sum(1 for w in words if len(w) <= 3)
    short_ratio = short_words / len(words)

    if short_ratio < 0.7:
        return False

    avg_len = sum(len(w) for w in words) / len(words)
    if avg_len > 3.5:
        return False

    # Check for specific gibberish patterns
    gibberish_indicators = [
        "WwW", "wWw", "Ww", "wW", "Bw", "wB", "BW",
        "ND", "YN", "NH", "NM", "FB", "FF", "FW",
        "eB", "Be", "eH", "mw", "mn", "nn", "fF", "Ff",
        "Se", "Oe", "oO", "HD", "SS", "DAH", "DAW", "UDF",
    ]

    indicator_count = sum(1 for ind in gibberish_indicators if ind in stripped)

    if indicator_count >= 2 and short_ratio >= 0.6:
        return True

    two_char_words = sum(1 for w in words if len(w) == 2)
    if two_char_words >= 3 and len(words) >= 4 and avg_len <= 2.5:
        return True

    return False


def should_remove_line(line: str) -> tuple[bool, str]:
    """Check if a line should be removed. Returns (should_remove, reason)."""
    for name, pattern in CLEANING_PATTERNS.items():
        if pattern.match(line):
            return True, name

    if is_gibberish_line(line):
        return True, "gibberish"

    return False, ""


def clean_text(text: str) -> str:
    """Clean OCR text by removing artifacts."""
    lines = text.split("\n")
    cleaned_lines = []

    for line in lines:
        should_remove, _ = should_remove_line(line)
        if not should_remove:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def clean_text_file(filepath: Path) -> dict:
    """Clean a text file in place. Returns stats."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        original_text = f.read()

    original_lines = original_text.split("\n")
    cleaned_text = clean_text(original_text)
    cleaned_lines = cleaned_text.split("\n")

    removed_count = len(original_lines) - len(cleaned_lines)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(cleaned_text)

    return {
        "original_lines": len(original_lines),
        "cleaned_lines": len(cleaned_lines),
        "removed_lines": removed_count,
    }


# -----------------------------------------------------------------------------
# LLM Processing
# -----------------------------------------------------------------------------

def get_mongo_client() -> MongoClient:
    """Create MongoDB client from environment variable."""
    mongo_uri = os.environ.get("MONGODB_URI")
    if not mongo_uri:
        raise ValueError("MONGODB_URI environment variable is required")
    return MongoClient(mongo_uri)


def get_openai_client() -> OpenAI:
    """Create OpenAI client from environment variable."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is required")
    return OpenAI(api_key=api_key)


def load_prompt(prompt_name: str) -> str:
    """Load a prompt file from the prompts directory."""
    prompt_path = PROMPTS_DIR / f"{prompt_name}.md"
    with open(prompt_path, "r") as f:
        return f.read()


def call_openai(client: OpenAI, system_prompt: str, user_content: str, model: str = "gpt-4o") -> dict:
    """Call OpenAI API and parse JSON response."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        temperature=0.1,
        response_format={"type": "json_object"}
    )

    content = response.choices[0].message.content
    return json.loads(content)


def chunk_text(text: str, max_chars: int = 70000, overlap: int = 500) -> list[str]:
    """Split text into chunks for large documents."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        if end < len(text):
            for sep in ["\n\n", "\n", ". ", " "]:
                boundary = text.rfind(sep, start + max_chars - 5000, end)
                if boundary > start:
                    end = boundary + len(sep)
                    break
        chunks.append(text[start:end])
        start = end - overlap if end < len(text) else end
    return chunks


def merge_extraction_results(results: list[dict]) -> dict:
    """Merge multiple extraction results from chunked processing."""
    if len(results) == 1:
        return results[0]

    merged = {
        "summary": results[0].get("summary", ""),
        "license_action": None,
        "probation_months": None,
        "ineligible_to_reapply_months": None,
        "fine_amount": None,
        "investigation_costs": None,
        "charity_donation": None,
        "costs_payment_deadline_days": None,
        "costs_stayed": False,
        "cme_hours": None,
        "cme_topic": None,
        "cme_deadline_months": None,
        "public_reprimand": False,
        "npdb_report": False,
        "practice_restrictions": [],
        "monitoring_requirements": [],
        "violations_admitted": [],
        "_chunked": True,
        "_chunk_count": len(results),
    }

    for r in results:
        if not merged["license_action"] and r.get("license_action"):
            merged["license_action"] = r["license_action"]
        if not merged["probation_months"] and r.get("probation_months"):
            merged["probation_months"] = r["probation_months"]
        if not merged["ineligible_to_reapply_months"] and r.get("ineligible_to_reapply_months"):
            merged["ineligible_to_reapply_months"] = r["ineligible_to_reapply_months"]
        if not merged["fine_amount"] and r.get("fine_amount"):
            merged["fine_amount"] = r["fine_amount"]
        if not merged["investigation_costs"] and r.get("investigation_costs"):
            merged["investigation_costs"] = r["investigation_costs"]
        if not merged["charity_donation"] and r.get("charity_donation"):
            merged["charity_donation"] = r["charity_donation"]
        if not merged["costs_payment_deadline_days"] and r.get("costs_payment_deadline_days"):
            merged["costs_payment_deadline_days"] = r["costs_payment_deadline_days"]
        if r.get("costs_stayed"):
            merged["costs_stayed"] = True
        if not merged["cme_hours"] and r.get("cme_hours"):
            merged["cme_hours"] = r["cme_hours"]
        if not merged["cme_topic"] and r.get("cme_topic"):
            merged["cme_topic"] = r["cme_topic"]
        if not merged["cme_deadline_months"] and r.get("cme_deadline_months"):
            merged["cme_deadline_months"] = r["cme_deadline_months"]
        if r.get("public_reprimand"):
            merged["public_reprimand"] = True
        if r.get("npdb_report"):
            merged["npdb_report"] = True

        for restriction in r.get("practice_restrictions", []):
            if restriction not in merged["practice_restrictions"]:
                merged["practice_restrictions"].append(restriction)
        for req in r.get("monitoring_requirements", []):
            if req not in merged["monitoring_requirements"]:
                merged["monitoring_requirements"].append(req)
        for v in r.get("violations_admitted", []):
            if not any(existing.get("nrs_code") == v.get("nrs_code") for existing in merged["violations_admitted"]):
                merged["violations_admitted"].append(v)

    return merged


# -----------------------------------------------------------------------------
# Complaint Processing
# -----------------------------------------------------------------------------

def process_complaint(
    metadata: dict,
    text_content: str,
    openai_client: OpenAI,
    db,
    dry_run: bool = False,
    model: str = "gpt-4o"
) -> dict:
    """
    Process a complaint document through LLM and store in MongoDB.

    Handles amended complaints by:
    1. Finding the original complaint in the database
    2. Generating an amendment summary
    3. Storing reference to original complaint
    """
    case_number = metadata["case_number"]
    doc_type = metadata["type"]
    is_amended = is_amended_complaint(doc_type)

    complaints_collection = db["complaints"]

    # Check for existing complaint
    existing_complaint = complaints_collection.find_one({"case_number": case_number})

    # Load prompts
    extraction_prompt = load_prompt("complaint_extraction")

    # Build user content for LLM
    user_content = f"""## Metadata

- **Title:** {metadata.get('title', doc_type)}
- **Respondent:** {metadata.get('respondent', 'Unknown')}
- **Case Number:** {case_number}
- **Date:** {metadata.get('date', 'Unknown')}
- **Type:** {doc_type}

## Document Text

{text_content}
"""

    # Check for OCR failure
    line_count = len([l for l in text_content.strip().split('\n') if l.strip()])
    ocr_failed = line_count <= 1

    result = {
        "case_number": case_number,
        "is_amended": is_amended,
        "ocr_failed": ocr_failed,
        "llm_extracted": None,
        "amendment_summary": None,
    }

    if dry_run:
        result["dry_run"] = True
        return result

    # Build document for MongoDB
    document = {
        "case_number": case_number,
        "year": metadata.get("year"),
        "date": metadata.get("date"),
        "title": metadata.get("title", doc_type),
        "type": doc_type,
        "respondent": metadata.get("respondent", ""),
        "pdf_url": metadata.get("pdf_url"),
        "text_content": text_content,
        "text_file": metadata.get("text_file"),
        "ocr_failed": ocr_failed,
        "processed_at": datetime.now(timezone.utc),
        "is_amended": is_amended,
    }

    # Process with LLM if OCR succeeded
    if not ocr_failed:
        print("  Calling OpenAI for complaint extraction...")
        llm_result = call_openai(openai_client, extraction_prompt, user_content, model)
        document["llm_extracted"] = llm_result
        document["llm_model"] = model
        result["llm_extracted"] = llm_result
        print(f"  Extracted: {llm_result.get('category', 'Unknown')} - {llm_result.get('summary', '')[:60]}...")

    # Handle amended complaint
    if is_amended and existing_complaint:
        # Store reference to original
        document["original_complaint"] = {
            "type": existing_complaint.get("type"),
            "date": existing_complaint.get("date"),
            "pdf_url": existing_complaint.get("pdf_url"),
            "text_file": existing_complaint.get("text_file"),
        }

        # Generate amendment summary if we have both texts
        original_text = existing_complaint.get("text_content", "")
        if original_text and not ocr_failed:
            print("  Comparing with original complaint...")
            comparison_prompt = load_prompt("amendment_comparison")

            # Truncate texts for comparison
            max_chars = 6000
            comparison_content = f"""## Original Complaint Text

{original_text[:max_chars]}

## Amended Complaint Text

{text_content[:max_chars]}
"""
            try:
                comparison_result = call_openai(openai_client, comparison_prompt, comparison_content, model)
                amendment_summary = comparison_result.get("amendment_summary")
                if amendment_summary:
                    document["amendment_summary"] = amendment_summary
                    result["amendment_summary"] = amendment_summary
                    print(f"  Amendment summary: {amendment_summary[:80]}...")
            except Exception as e:
                print(f"  Warning: Amendment comparison failed: {e}")

    # Upsert to MongoDB
    complaints_collection.update_one(
        {"case_number": case_number},
        {"$set": document},
        upsert=True
    )
    print("  Stored in MongoDB (complaints collection)")

    return result


# -----------------------------------------------------------------------------
# Settlement Processing
# -----------------------------------------------------------------------------

def process_settlement(
    metadata: dict,
    text_content: str,
    openai_client: OpenAI,
    db,
    dry_run: bool = False,
    model: str = "gpt-4o"
) -> dict:
    """
    Process a settlement document through LLM and store in MongoDB.

    Links settlement to associated complaint(s) via case_numbers.
    """
    case_number = metadata["case_number"]
    case_numbers = metadata.get("case_numbers", [case_number])
    doc_type = metadata["type"]
    pdf_url = metadata.get("pdf_url", f"local://{metadata.get('pdf_path', case_number)}")

    settlements_collection = db["settlements"]
    complaints_collection = db["complaints"]

    # Load prompt
    extraction_prompt = load_prompt("settlement_extraction")

    # Check for OCR failure
    line_count = len([l for l in text_content.strip().split('\n') if l.strip()])
    ocr_failed = line_count <= 1

    result = {
        "case_numbers": case_numbers,
        "ocr_failed": ocr_failed,
        "llm_extracted": None,
        "linked_complaints": 0,
    }

    if dry_run:
        result["dry_run"] = True
        return result

    # Look up linked complaints
    complaint_ids = []
    for cn in case_numbers:
        complaint = complaints_collection.find_one({"case_number": cn}, {"_id": 1})
        if complaint:
            complaint_ids.append(complaint["_id"])

    result["linked_complaints"] = len(complaint_ids)
    if complaint_ids:
        print(f"  Linked to {len(complaint_ids)} complaint(s)")

    # Build document for MongoDB
    document = {
        "case_numbers": case_numbers,
        "complaint_ids": complaint_ids,
        "year": metadata.get("year"),
        "date": metadata.get("date"),
        "title": metadata.get("title", doc_type),
        "type": doc_type,
        "respondent": metadata.get("respondent", ""),
        "pdf_url": pdf_url,
        "text_content": text_content,
        "text_file": metadata.get("text_file"),
        "ocr_failed": ocr_failed,
        "processed_at": datetime.now(timezone.utc),
    }

    # Process with LLM if OCR succeeded
    if not ocr_failed:
        print("  Calling OpenAI for settlement extraction...")

        # Handle large documents with chunking
        chunks = chunk_text(text_content)
        if len(chunks) > 1:
            print(f"  Document split into {len(chunks)} chunks")

        chunk_results = []
        for i, chunk in enumerate(chunks):
            chunk_note = f"\n\n[This is part {i+1} of {len(chunks)} of the document]" if len(chunks) > 1 else ""

            user_content = f"""## Metadata

- **Title:** {metadata.get('title', doc_type)}
- **Respondent:** {metadata.get('respondent', 'Unknown')}
- **Case Number:** {case_number}
- **Date:** {metadata.get('date', 'Unknown')}
- **Type:** {doc_type}{chunk_note}

## Document Text

{chunk}
"""
            chunk_result = call_openai(openai_client, extraction_prompt, user_content, model)
            chunk_results.append(chunk_result)

        llm_result = merge_extraction_results(chunk_results)
        document["llm_extracted"] = llm_result
        document["llm_model"] = model
        result["llm_extracted"] = llm_result
        print(f"  Extracted: {llm_result.get('license_action', 'Unknown')} - Fine: ${llm_result.get('fine_amount', 0) or 0:,.0f}")

    # Upsert to MongoDB by pdf_url
    settlements_collection.update_one(
        {"pdf_url": pdf_url},
        {"$set": document},
        upsert=True
    )
    print("  Stored in MongoDB (settlements collection)")

    return result


# -----------------------------------------------------------------------------
# License-Only Filing Processing
# -----------------------------------------------------------------------------

def process_license_only_filing(
    metadata: dict,
    text_content: str,
    db,
    dry_run: bool = False,
) -> dict:
    """
    Process a license-only filing document and store in MongoDB.

    License-only filings are administrative actions tied to a license number
    (e.g., LICENSE-401) rather than a formal complaint case. These include
    summary suspensions, voluntary surrenders, probation releases, etc.

    No LLM processing is performed - just stores metadata and OCR text.
    """
    license_number = metadata["case_number"]  # e.g., "LICENSE-401"
    doc_type = metadata["type"]
    pdf_url = metadata.get("pdf_url", f"local://{metadata.get('pdf_path', license_number)}")

    collection = db["license_only_filings"]

    # Check for OCR failure
    line_count = len([l for l in text_content.strip().split('\n') if l.strip()])
    ocr_failed = line_count <= 1

    result = {
        "license_number": license_number,
        "type": doc_type,
        "ocr_failed": ocr_failed,
    }

    if dry_run:
        result["dry_run"] = True
        return result

    # Build document for MongoDB
    document = {
        "license_number": license_number,
        "year": metadata.get("year"),
        "date": metadata.get("date"),
        "title": metadata.get("title", doc_type),
        "type": doc_type,
        "respondent": metadata.get("respondent", ""),
        "pdf_url": pdf_url,
        "text_content": text_content,
        "text_file": metadata.get("text_file"),
        "ocr_failed": ocr_failed,
        "processed_at": datetime.now(timezone.utc),
    }

    # Upsert to MongoDB by pdf_url (unique identifier)
    collection.update_one(
        {"pdf_url": pdf_url},
        {"$set": document},
        upsert=True
    )
    print("  Stored in MongoDB (license_only_filings collection)")

    return result


# -----------------------------------------------------------------------------
# Main Pipeline
# -----------------------------------------------------------------------------

def process_single_file(
    pdf_path: Path,
    output_dir: Path | None = None,
    dry_run: bool = False,
    skip_ocr: bool = False,
    model: str = "gpt-4o",
    filing_metadata: dict | None = None,
) -> dict:
    """
    Process a single PDF file through the entire pipeline.

    Args:
        pdf_path: Path to the PDF file
        output_dir: Directory for OCR outputs (default: creates temp dir or uses standard structure)
        dry_run: If True, don't store in MongoDB
        skip_ocr: If True, assume text file already exists
        model: OpenAI model to use
        filing_metadata: Optional metadata from scraper (date, respondent, pdf_url, etc.)
                        Supplements/overrides filename-parsed metadata.

    Returns:
        dict with processing results
    """
    print(f"\n{'='*60}")
    print(f"Processing: {pdf_path.name}")
    print(f"{'='*60}")

    # Parse filename for metadata
    metadata = parse_filename(pdf_path)
    metadata["pdf_path"] = str(pdf_path)

    # Merge in externally-provided metadata (from scraper)
    if filing_metadata:
        for key in ["date", "respondent", "pdf_url", "title", "year"]:
            if filing_metadata.get(key):
                metadata[key] = filing_metadata[key]

    print(f"  Case Number: {metadata['case_number']}")
    print(f"  Document Type: {metadata['type']}")
    print(f"  Year: {metadata['year']}")

    # Classify document type
    doc_class = classify_document_type(metadata["type"], metadata["case_number"])
    print(f"  Classification: {doc_class.upper()}")

    if doc_class == "ignored":
        print("\n  This document type is not processed. Skipping.")
        return {
            "status": "ignored",
            "case_number": metadata["case_number"],
            "type": metadata["type"],
        }

    # Determine output paths
    if output_dir:
        ocr_pdf_path = output_dir / "pdfs_ocr" / pdf_path.name
        text_path = output_dir / "text" / pdf_path.with_suffix(".txt").name
    else:
        # Use standard project structure
        year_str = str(metadata["year"]) if metadata["year"] else "unknown"
        ocr_pdf_path = PROJECT_ROOT / "pdfs_ocr" / year_str / pdf_path.name
        text_path = PROJECT_ROOT / "text" / year_str / pdf_path.with_suffix(".txt").name

    metadata["text_file"] = str(text_path)

    # Step 1: OCR
    if skip_ocr:
        if not text_path.exists():
            print(f"\n  Error: --skip-ocr specified but text file not found: {text_path}")
            return {"status": "error", "error": "Text file not found"}
        print(f"\n  Skipping OCR (using existing text file)")
    else:
        print(f"\n  Step 1: OCR Processing")

        if not check_ocr_dependencies():
            return {"status": "error", "error": "Missing OCR dependencies"}

        # Get page count and calculate timeout
        try:
            page_count = get_page_count(pdf_path)
            ocr_timeout = calculate_ocr_timeout(page_count)
            print(f"  Pages: {page_count}, Timeout: {ocr_timeout}s ({ocr_timeout // 60}m {ocr_timeout % 60}s)")
        except ValueError as e:
            print(f"  Error reading PDF: {e}")
            return {"status": "error", "error": f"Invalid PDF: {e}"}

        ocr_result = ocr_pdf(pdf_path, ocr_pdf_path, text_path, timeout=ocr_timeout)

        if not ocr_result["success"]:
            print(f"  OCR failed: {ocr_result['error']}")
            return {"status": "error", "error": f"OCR failed: {ocr_result['error']}"}

        print(f"  OCR complete: {ocr_result['word_count']} words, {ocr_result['duration_seconds']:.1f}s")

    # Step 2: Clean text
    print(f"\n  Step 2: Text Cleaning")
    clean_stats = clean_text_file(text_path)
    print(f"  Removed {clean_stats['removed_lines']} artifact lines ({clean_stats['original_lines']} -> {clean_stats['cleaned_lines']})")

    # Read cleaned text
    text_content = text_path.read_text(encoding="utf-8", errors="replace")

    # Step 3: Store in MongoDB (with LLM processing for complaints/settlements)
    if doc_class == "license_only":
        print(f"\n  Step 3: Store in MongoDB (no LLM processing)")
    else:
        print(f"\n  Step 3: LLM Processing")

    if dry_run:
        msg = "Would store in MongoDB" if doc_class == "license_only" else "Would process with LLM and store in MongoDB"
        print(f"  [DRY RUN] {msg}")
        return {
            "status": "dry_run",
            "case_number": metadata["case_number"],
            "type": metadata["type"],
            "classification": doc_class,
            "text_length": len(text_content),
        }

    # Connect to services
    mongo_client = get_mongo_client()
    db = mongo_client["malpractice"]

    # Process based on document type
    if doc_class == "license_only":
        result = process_license_only_filing(metadata, text_content, db, dry_run)
    elif doc_class == "complaint":
        openai_client = get_openai_client()
        result = process_complaint(metadata, text_content, openai_client, db, dry_run, model)
    else:  # settlement
        openai_client = get_openai_client()
        result = process_settlement(metadata, text_content, openai_client, db, dry_run, model)

    result["status"] = "success"
    result["classification"] = doc_class

    print(f"\n  Processing complete!")
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Process a single PDF file through the entire pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    uv run python scripts/process_single_file.py pdfs/2024/24-12345-1_Complaint.pdf
    uv run python scripts/process_single_file.py my_file.pdf --dry-run
    uv run python scripts/process_single_file.py my_file.pdf --skip-ocr
    uv run python scripts/process_single_file.py my_file.pdf --output-dir ./output
        """
    )
    parser.add_argument("pdf_path", type=Path, help="Path to the PDF file to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview without storing in MongoDB")
    parser.add_argument("--skip-ocr", action="store_true", help="Skip OCR (text file must already exist)")
    parser.add_argument("--output-dir", type=Path, help="Custom output directory for OCR files")
    parser.add_argument("--model", type=str, default="gpt-4o", help="OpenAI model to use (default: gpt-4o)")

    args = parser.parse_args()

    if not args.pdf_path.exists():
        print(f"Error: File not found: {args.pdf_path}")
        sys.exit(1)

    if not args.pdf_path.suffix.lower() == ".pdf":
        print(f"Error: File must be a PDF: {args.pdf_path}")
        sys.exit(1)

    result = process_single_file(
        pdf_path=args.pdf_path,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
        skip_ocr=args.skip_ocr,
        model=args.model,
    )

    print(f"\n{'='*60}")
    print("RESULT")
    print(f"{'='*60}")
    print(json.dumps(result, indent=2, default=str))

    sys.exit(0 if result.get("status") in ["success", "dry_run", "ignored"] else 1)


if __name__ == "__main__":
    main()
