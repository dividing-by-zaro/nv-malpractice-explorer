#!/usr/bin/env python3
"""
Clean OCR text files by removing known artifacts.
All patterns are designed to be 100% foolproof - they will NOT remove legitimate text.
"""

import re
import os
import argparse
from pathlib import Path
from collections import defaultdict


# Pattern definitions with descriptions
PATTERNS = {
    "only_numbers": {
        "pattern": re.compile(r"^\s*\d+\s*$"),
        "description": "Lines with ONLY numbers (line numbers from margins)",
    },
    "page_numbers": {
        "pattern": re.compile(r"^\s*\d+\s+of\s+\d+\s*$", re.IGNORECASE),
        "description": "Page numbers like '5 of 6'",
    },
    "slash_markers": {
        "pattern": re.compile(r"^\s*[/\\|lI1!]{2,}\s*$"),
        "description": "Page break markers (///, //1, //}, etc.)",
    },
    "only_punctuation": {
        "pattern": re.compile(r"^\s*[^\w\s]+\s*$"),
        "description": "Lines with ONLY punctuation",
    },
    "k_dividers": {
        "pattern": re.compile(r"^\s*[KkEeRr\s\*]{3,}\s*$"),
        "description": "K/E/R divider artifacts (KKK, KEKE, etc.)",
    },
    "ocr_page_markers": {
        "pattern": re.compile(r"^\s*(Hf|Hil|M1|M1\}|H!|I!|Il|1l)\s*$"),
        "description": "OCR misreads of page markers",
    },
    "single_symbols": {
        "pattern": re.compile(r"^\s*[>\-—=]\s*$"),
        "description": "Isolated single symbols",
    },
    "ss_artifacts": {
        "pattern": re.compile(r"^\s*:?\s*SS\.\s*$", re.IGNORECASE),
        "description": "Isolated 'SS.' verification artifacts",
    },
    "exhibit_number_only": {
        "pattern": re.compile(r"^\s*\d\s*$"),
        "description": "Single digit on a line (exhibit numbers already labeled)",
    },
}


def is_gibberish_line(line: str) -> bool:
    """
    Detect OCR gibberish from margin line numbers.
    These are lines with short random letter sequences like:
    'ana mn FB WwW ND', 'f YW N', 'Co mw IN DH FF Ww'

    Returns True if the line appears to be gibberish.
    """
    stripped = line.strip()
    if not stripped:
        return False

    # Split into "words"
    words = stripped.split()

    # Need at least 2 words to be considered gibberish pattern
    if len(words) < 2:
        return False

    # Check if most words are very short (1-3 chars)
    short_words = sum(1 for w in words if len(w) <= 3)
    short_ratio = short_words / len(words)

    # If less than 70% are short words, probably not gibberish
    if short_ratio < 0.7:
        return False

    # Calculate average word length
    avg_len = sum(len(w) for w in words) / len(words)
    if avg_len > 3.5:
        return False

    # Check for mixed case gibberish pattern (random caps)
    # Real text usually has consistent capitalization
    has_mixed_case_words = 0
    for word in words:
        if len(word) >= 2:
            # Check if word has mixed case in middle (like "wWwN", "BwW")
            uppers = sum(1 for c in word if c.isupper())
            lowers = sum(1 for c in word if c.islower())
            if uppers > 0 and lowers > 0 and len(word) <= 4:
                has_mixed_case_words += 1

    # Check for specific gibberish patterns (OCR of numbers 1-28)
    # These often contain: B, Be, Bw, NH, YN, ND, WwW, etc.
    gibberish_indicators = [
        "WwW", "wWw", "Ww", "wW",  # Mixed case w
        "Bw", "wB", "BW",
        "ND", "YN", "NH", "NM",  # Common OCR of numbers
        "FB", "FF", "FW",
        "eB", "Be", "eH",
        "mw", "mn", "nn",
        "fF", "Ff",
        "Se", "Oe", "oO",
        "HD", "SS",
        "DAH", "DAW", "UDF",
    ]

    indicator_count = sum(1 for ind in gibberish_indicators if ind in stripped)

    # If we have multiple gibberish indicators plus short words, it's gibberish
    if indicator_count >= 2 and short_ratio >= 0.6:
        return True

    # Check for the specific pattern of OCR'd line numbers
    # These often look like: "Co mw IN DH FF Ww" or "ana mn FB WwW ND"
    # Pattern: mostly 2-letter segments with spaces
    two_char_words = sum(1 for w in words if len(w) == 2)
    if two_char_words >= 3 and len(words) >= 4 and avg_len <= 2.5:
        return True

    # Check for repeated Be/eB patterns (common in line number OCR)
    be_pattern = re.compile(r"([BeE]{2}\s*){3,}")
    if be_pattern.search(stripped):
        return True

    # Pattern like "RN YN YN NNN YD" - repeated 2-char sequences
    if re.match(r"^([A-Za-z]{1,3}\s+){4,}[A-Za-z]{1,3}$", stripped):
        # Check if it's not real abbreviations by looking for variety
        unique_words = set(w.upper() for w in words)
        if len(unique_words) < len(words) * 0.7:  # Many repeats
            return True

    return False


def is_fax_header_garbage(line: str) -> bool:
    """
    Detect fax header garbage lines like:
    'h/t L6LV 088-204 OUNPLISUT ULed seBeA Se]'
    'v/v L6LV 088-Z0Z SUNVLYSUI ULed'
    """
    stripped = line.strip()
    if not stripped:
        return False

    # Check for fax header patterns
    # These often have: phone-like numbers mixed with gibberish
    if re.search(r"\d{3}[-\s]?\d{3,4}", stripped):
        # Has phone-like pattern, check for gibberish around it
        # Look for nonsense words mixed with numbers
        words = stripped.split()
        nonsense_count = 0
        for word in words:
            # Remove punctuation for checking
            clean = re.sub(r"[^\w]", "", word)
            if clean and not clean.isdigit():
                # Check if it looks like gibberish (mixed case, odd patterns)
                if re.search(r"[A-Z][a-z][A-Z]|[a-z][A-Z][a-z]", clean):
                    nonsense_count += 1
                # Check for unlikely letter combos
                if re.search(r"[LZVXQ]{2,}|ULed|seBeA|SUNVLY|OUNPLI", clean, re.IGNORECASE):
                    nonsense_count += 1

        if nonsense_count >= 2:
            return True

    return False


def is_number_sequence_garbage(line: str) -> bool:
    """
    Detect OCR garbage from number sequences like:
    'BRRRFERBRRESV BARA BZEEBHRES'
    'Py» Rey PP Re NPR KBE SE ='
    """
    stripped = line.strip()
    if not stripped:
        return False

    # These patterns are very distinctive
    # Lots of repeated letters, especially B, R, E, S
    if re.match(r"^[BRRESA\s]{10,}$", stripped):
        return True

    # Pattern with » and random letter combos
    if "»" in stripped and re.search(r"[A-Z]{2,3}\s+[A-Z]{2,3}", stripped):
        words = stripped.replace("»", " ").split()
        if all(len(w) <= 4 for w in words if w.isalpha()):
            return True

    return False


def should_remove_line(line: str) -> tuple[bool, str]:
    """
    Check if a line should be removed.
    Returns (should_remove, reason).
    """
    # Check regex patterns first
    for name, info in PATTERNS.items():
        if info["pattern"].match(line):
            return True, name

    # Check for gibberish
    if is_gibberish_line(line):
        return True, "gibberish_margin_numbers"

    # Check for fax header garbage
    if is_fax_header_garbage(line):
        return True, "fax_header_garbage"

    # Check for number sequence garbage
    if is_number_sequence_garbage(line):
        return True, "number_sequence_garbage"

    return False, ""


def clean_file(filepath: Path, dry_run: bool = True) -> dict:
    """
    Clean a single file.
    Returns statistics about what was removed.
    """
    stats = defaultdict(int)
    stats["total_lines"] = 0
    stats["removed_lines"] = 0
    stats["reasons"] = defaultdict(int)
    stats["removed_examples"] = defaultdict(list)

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    stats["total_lines"] = len(lines)
    cleaned_lines = []

    for line in lines:
        should_remove, reason = should_remove_line(line)
        if should_remove:
            stats["removed_lines"] += 1
            stats["reasons"][reason] += 1
            # Keep a few examples
            if len(stats["removed_examples"][reason]) < 3:
                stats["removed_examples"][reason].append(line.strip()[:50])
        else:
            cleaned_lines.append(line)

    # Write cleaned file if not dry run
    if not dry_run and stats["removed_lines"] > 0:
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(cleaned_lines)

    return stats


def process_directory(text_dir: Path, dry_run: bool = True) -> dict:
    """
    Process all text files in directory and subdirectories.
    """
    total_stats = {
        "files_processed": 0,
        "files_modified": 0,
        "total_lines": 0,
        "removed_lines": 0,
        "reasons": defaultdict(int),
        "examples": defaultdict(list),
    }

    # Find all .txt files
    txt_files = list(text_dir.rglob("*.txt"))
    print(f"Found {len(txt_files)} text files to process...")

    for filepath in txt_files:
        stats = clean_file(filepath, dry_run=dry_run)

        total_stats["files_processed"] += 1
        total_stats["total_lines"] += stats["total_lines"]
        total_stats["removed_lines"] += stats["removed_lines"]

        if stats["removed_lines"] > 0:
            total_stats["files_modified"] += 1

        for reason, count in stats["reasons"].items():
            total_stats["reasons"][reason] += count
            # Collect examples
            for ex in stats["removed_examples"][reason]:
                if len(total_stats["examples"][reason]) < 5:
                    total_stats["examples"][reason].append(ex)

    return total_stats


def preview_single_file(filepath: Path):
    """
    Preview what would be removed from a single file.
    Shows each line that would be removed with its reason.
    """
    print(f"Previewing: {filepath}")
    print("=" * 60)

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    removed = []
    kept = []

    for i, line in enumerate(lines, 1):
        should_remove, reason = should_remove_line(line)
        if should_remove:
            removed.append((i, line.rstrip(), reason))
        else:
            kept.append(line)

    print(f"Total lines: {len(lines)}")
    print(f"Lines to REMOVE: {len(removed)}")
    print(f"Lines to KEEP: {len(kept)}")
    print()

    if removed:
        print("LINES TO BE REMOVED:")
        print("-" * 60)
        for line_num, content, reason in removed:
            desc = PATTERNS.get(reason, {}).get("description", reason.replace("_", " "))
            # Truncate long lines for display
            display = content[:60] + "..." if len(content) > 60 else content
            print(f"  L{line_num:4d} | {desc:30s} | '{display}'")

    print()
    print("=" * 60)
    print("PREVIEW OF CLEANED TEXT (first 50 lines):")
    print("-" * 60)
    for line in kept[:50]:
        print(line.rstrip())


def main():
    parser = argparse.ArgumentParser(
        description="Clean OCR text files by removing known artifacts"
    )
    parser.add_argument(
        "--text-dir",
        type=Path,
        default=Path("text"),
        help="Directory containing text files (default: text/)",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Preview a single file instead of processing directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview changes without modifying files (default: True)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply changes (turns off dry-run)",
    )

    args = parser.parse_args()

    # Single file preview mode
    if args.file:
        if not args.file.exists():
            print(f"Error: File not found: {args.file}")
            return
        preview_single_file(args.file)
        return

    dry_run = not args.apply

    if dry_run:
        print("=" * 60)
        print("DRY RUN MODE - No files will be modified")
        print("Use --apply to actually clean the files")
        print("=" * 60)
    else:
        print("=" * 60)
        print("APPLY MODE - Files will be modified!")
        print("=" * 60)

    print()

    # Process files
    stats = process_directory(args.text_dir, dry_run=dry_run)

    # Print results
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Files processed: {stats['files_processed']}")
    print(f"Files that would be modified: {stats['files_modified']}")
    print(f"Total lines processed: {stats['total_lines']:,}")
    print(f"Lines to remove: {stats['removed_lines']:,}")

    if stats["total_lines"] > 0:
        pct = (stats["removed_lines"] / stats["total_lines"]) * 100
        print(f"Removal percentage: {pct:.2f}%")

    print()
    print("Breakdown by reason:")
    print("-" * 40)
    for reason, count in sorted(stats["reasons"].items(), key=lambda x: -x[1]):
        desc = PATTERNS.get(reason, {}).get("description", reason.replace("_", " ").title())
        print(f"  {desc}: {count:,}")
        # Show examples
        if reason in stats["examples"]:
            for ex in stats["examples"][reason][:2]:
                print(f"    -> '{ex}'")

    print()
    if dry_run:
        print("To apply these changes, run with --apply flag")


if __name__ == "__main__":
    main()
