"""
OCR all downloaded PDFs using ocrmypdf.

Creates:
- pdfs_ocr/{year}/*.pdf - Searchable PDFs for downloading
- text/{year}/*.txt - Extracted plain text for analysis

Uses 10 parallel workers and logs failures to continue processing.

Usage:
    uv run python ocr_pdfs.py
"""

import json
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from dataclasses import dataclass, asdict

PDF_DIR = Path("pdfs")
OCR_PDF_DIR = Path("pdfs_ocr")
TEXT_DIR = Path("text")
DATA_DIR = Path("data")

NUM_WORKERS = 10


@dataclass
class OCRResult:
    input_path: str
    output_pdf: str
    output_text: str
    success: bool
    error: str | None
    duration_seconds: float
    page_count: int | None
    word_count: int | None


def ocr_single_pdf(input_path: Path) -> OCRResult:
    """OCR a single PDF and extract text."""
    start_time = time.time()

    # Determine output paths, preserving year subdirectory
    relative = input_path.relative_to(PDF_DIR)
    output_pdf = OCR_PDF_DIR / relative
    output_text = TEXT_DIR / relative.with_suffix(".txt")

    # Create output directories
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_text.parent.mkdir(parents=True, exist_ok=True)

    # Skip if already processed
    if output_pdf.exists() and output_text.exists():
        # Read existing text to get word count
        text_content = output_text.read_text(errors="ignore")
        word_count = len(text_content.split())
        return OCRResult(
            input_path=str(input_path),
            output_pdf=str(output_pdf),
            output_text=str(output_text),
            success=True,
            error="skipped - already exists",
            duration_seconds=0,
            page_count=None,
            word_count=word_count,
        )

    try:
        # Run ocrmypdf with sidecar text extraction
        cmd = [
            "ocrmypdf",
            "--sidecar", str(output_text),  # Extract text to file
            "--rotate-pages",                # Auto-rotate pages
            "--deskew",                      # Fix skewed scans
            "--clean",                       # Clean up scanned pages
            "--skip-text",                   # Skip pages that already have text
            "-l", "eng",                     # English language
            "--jobs", "2",                   # Use 2 threads per PDF
            str(input_path),
            str(output_pdf),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout per PDF
        )

        duration = time.time() - start_time

        if result.returncode != 0:
            # Check for specific "already has text" case
            if "page already has text" in result.stderr.lower() or result.returncode == 6:
                # Copy original and try to extract text anyway
                import shutil
                shutil.copy(input_path, output_pdf)

                # Try pdftotext for extraction
                try:
                    subprocess.run(
                        ["pdftotext", str(input_path), str(output_text)],
                        capture_output=True,
                        timeout=60,
                    )
                except Exception:
                    output_text.write_text("")

            else:
                return OCRResult(
                    input_path=str(input_path),
                    output_pdf=str(output_pdf),
                    output_text=str(output_text),
                    success=False,
                    error=result.stderr[:500] if result.stderr else f"Return code {result.returncode}",
                    duration_seconds=duration,
                    page_count=None,
                    word_count=None,
                )

        # Get word count from extracted text
        word_count = None
        if output_text.exists():
            text_content = output_text.read_text(errors="ignore")
            word_count = len(text_content.split())

        # Try to get page count
        page_count = None
        try:
            pdfinfo = subprocess.run(
                ["pdfinfo", str(output_pdf)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in pdfinfo.stdout.split("\n"):
                if line.startswith("Pages:"):
                    page_count = int(line.split(":")[1].strip())
                    break
        except Exception:
            pass

        return OCRResult(
            input_path=str(input_path),
            output_pdf=str(output_pdf),
            output_text=str(output_text),
            success=True,
            error=None,
            duration_seconds=duration,
            page_count=page_count,
            word_count=word_count,
        )

    except subprocess.TimeoutExpired:
        return OCRResult(
            input_path=str(input_path),
            output_pdf=str(output_pdf),
            output_text=str(output_text),
            success=False,
            error="Timeout after 5 minutes",
            duration_seconds=time.time() - start_time,
            page_count=None,
            word_count=None,
        )
    except Exception as e:
        return OCRResult(
            input_path=str(input_path),
            output_pdf=str(output_pdf),
            output_text=str(output_text),
            success=False,
            error=str(e)[:500],
            duration_seconds=time.time() - start_time,
            page_count=None,
            word_count=None,
        )


def main():
    # Create output directories
    OCR_PDF_DIR.mkdir(exist_ok=True)
    TEXT_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)

    # Find all PDFs to process
    pdfs = sorted(PDF_DIR.glob("**/*.pdf"))
    print(f"Found {len(pdfs)} PDFs to process")

    if not pdfs:
        print("No PDFs found in pdfs/ directory")
        return

    results = []
    success_count = 0
    skip_count = 0
    fail_count = 0

    print(f"Processing with {NUM_WORKERS} workers...\n")

    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
        # Submit all jobs
        future_to_pdf = {executor.submit(ocr_single_pdf, pdf): pdf for pdf in pdfs}

        # Process as they complete
        for i, future in enumerate(as_completed(future_to_pdf), 1):
            pdf = future_to_pdf[future]
            try:
                result = future.result()
                results.append(asdict(result))

                if result.success:
                    if result.error and "skipped" in result.error:
                        skip_count += 1
                        status = "SKIP"
                    else:
                        success_count += 1
                        status = "OK"
                else:
                    fail_count += 1
                    status = "FAIL"

                # Progress output
                print(f"[{i}/{len(pdfs)}] {status}: {pdf.name}"
                      + (f" ({result.word_count} words)" if result.word_count else "")
                      + (f" - {result.error[:50]}" if result.error and status == "FAIL" else ""))

            except Exception as e:
                fail_count += 1
                print(f"[{i}/{len(pdfs)}] ERROR: {pdf.name} - {e}")
                results.append({
                    "input_path": str(pdf),
                    "success": False,
                    "error": str(e),
                })

    # Save results
    output_path = DATA_DIR / "ocr_results.json"
    with open(output_path, "w") as f:
        json.dump({
            "total": len(pdfs),
            "success": success_count,
            "skipped": skip_count,
            "failed": fail_count,
            "results": results,
        }, f, indent=2)

    # Print summary
    print(f"\n{'='*50}")
    print("OCR COMPLETE")
    print(f"{'='*50}")
    print(f"Total PDFs:  {len(pdfs)}")
    print(f"Success:     {success_count}")
    print(f"Skipped:     {skip_count}")
    print(f"Failed:      {fail_count}")
    print(f"\nResults saved to: {output_path}")

    # Print failed files
    failures = [r for r in results if not r.get("success")]
    if failures:
        print(f"\nFailed files:")
        for f in failures[:10]:
            print(f"  - {f['input_path']}: {f.get('error', 'unknown')[:60]}")
        if len(failures) > 10:
            print(f"  ... and {len(failures) - 10} more")


if __name__ == "__main__":
    main()
