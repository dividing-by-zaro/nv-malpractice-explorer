#!/usr/bin/env python3
"""
FastAPI app for exploring Nevada medical malpractice complaints.

Usage:
    uv run uvicorn app:app --reload
"""

import os
import re
import statistics
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.database import Database

load_dotenv()

# -----------------------------------------------------------------------------
# Pydantic Response Models
# -----------------------------------------------------------------------------


class StatsResponse(BaseModel):
    """Overall statistics."""
    total: int
    with_extraction: int
    resolutions: int
    categories: int
    drugs: int


class FiltersResponse(BaseModel):
    """Available filter options."""
    categories: list[str]
    specialties: list[str]
    years: list[int]
    drugs: list[str]
    license_actions: list[str]


class Complainant(BaseModel):
    """Patient demographic info."""
    age: Optional[int] = None
    sex: Optional[str] = None


class OriginalComplaint(BaseModel):
    """Original complaint data for amended cases."""
    type: str
    date: str
    pdf_url: Optional[str] = None


class LLMExtractedComplaint(BaseModel):
    """LLM-extracted complaint fields."""
    summary: Optional[str] = None
    specialty: Optional[str] = None
    category: Optional[str] = None
    procedure: Optional[str] = None
    num_complainants: Optional[int] = None
    complainants: list[Complainant] = []
    drugs: list[str] = []


class Complaint(BaseModel):
    """Complaint document."""
    id: Optional[str] = None
    case_number: str
    respondent: str
    date: str
    year: int
    type: str
    pdf_url: Optional[str] = None
    llm_extracted: Optional[LLMExtractedComplaint] = None
    is_amended: Optional[bool] = False
    original_complaint: Optional[OriginalComplaint] = None
    amendment_summary: Optional[str] = None

    class Config:
        extra = "allow"  # Allow extra fields from MongoDB


class ComplaintsResponse(BaseModel):
    """Paginated complaints response."""
    complaints: list[dict]  # Using dict for flexibility with MongoDB docs
    total: int


class Violation(BaseModel):
    """NRS violation."""
    nrs_code: Optional[str] = None
    count: Optional[str] = None
    description: Optional[str] = None


class LLMExtractedSettlement(BaseModel):
    """LLM-extracted settlement fields."""
    license_action: Optional[str] = None
    probation_months: Optional[int] = None
    fine_amount: Optional[float] = None
    investigation_costs: Optional[float] = None
    cme_hours: Optional[int] = None
    cme_topic: Optional[str] = None
    public_reprimand: Optional[bool] = None
    npdb_report: Optional[bool] = None
    violations_admitted: list[Violation] = []
    violations_dismissed: list[Violation] = []


class Settlement(BaseModel):
    """Settlement/Resolution document."""
    id: Optional[str] = None
    case_number: Optional[str] = None
    case_numbers: list[str] = []
    complaint_ids: list[str] = []
    respondent: str
    date: str
    year: int
    type: str
    resolution_outcome: Optional[str] = None  # "Settlement", "Hearing", or null
    pdf_url: Optional[str] = None
    llm_extracted: Optional[LLMExtractedSettlement] = None

    class Config:
        extra = "allow"


class CountItem(BaseModel):
    """Generic count item for aggregations."""
    count: int


class LicenseActionCount(CountItem):
    action: str


class SpecialtyCount(CountItem):
    specialty: str


class CategoryCount(CountItem):
    category: str


class YearCount(CountItem):
    year: int


class FinesByYear(BaseModel):
    year: int
    total: float
    count: int


class SettlementSummary(BaseModel):
    total: int
    with_fine: int
    with_probation: int
    with_cme: int
    public_reprimand: int
    npdb_report: int


class Totals(BaseModel):
    total_fines: float
    total_investigation_costs: float
    total_cme_hours: int
    total_probation_months: int
    avg_fine_per_year: int
    total_complaints: int
    year_span: int
    min_year: Optional[int]
    max_year: Optional[int]


class ResolutionTimeStats(BaseModel):
    """Statistics about case resolution times."""
    mean_days: float
    median_days: float
    min_days: int
    max_days: int
    count: int


class AnalyticsResponse(BaseModel):
    """Aggregate analytics data."""
    fine_values: list[float]
    cost_values: list[float]
    cme_values: list[int]
    probation_values: list[int]
    resolution_time_values: list[int]
    resolution_time_stats: Optional[ResolutionTimeStats]
    license_actions: list[LicenseActionCount]
    specialties: list[SpecialtyCount]
    categories: list[CategoryCount]
    by_year: list[YearCount]
    fines_by_year: list[FinesByYear]
    settlement_summary: SettlementSummary
    totals: Totals


# -----------------------------------------------------------------------------
# Database Connection Management
# -----------------------------------------------------------------------------


class DatabaseConnection:
    """Manages MongoDB connection lifecycle."""
    client: Optional[MongoClient] = None

    def connect(self) -> None:
        mongo_uri = os.environ.get("MONGODB_URI")
        if not mongo_uri:
            raise ValueError("MONGODB_URI environment variable is required")
        self.client = MongoClient(mongo_uri)

    def close(self) -> None:
        if self.client:
            self.client.close()
            self.client = None

    def get_db(self) -> Database:
        if not self.client:
            raise RuntimeError("Database not connected")
        return self.client["malpractice"]


db_connection = DatabaseConnection()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle - startup and shutdown."""
    # Startup
    db_connection.connect()
    yield
    # Shutdown
    db_connection.close()


# -----------------------------------------------------------------------------
# Dependency Injection
# -----------------------------------------------------------------------------


def get_db() -> Database:
    """Dependency that provides database access."""
    return db_connection.get_db()


DB = Annotated[Database, Depends(get_db)]


# -----------------------------------------------------------------------------
# FastAPI App
# -----------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"
PDFS_DIR = Path(__file__).parent / "pdfs_ocr"

app = FastAPI(
    title="Nevada Medical Malpractice Explorer",
    lifespan=lifespan,
)

# Mount static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
if PDFS_DIR.exists():
    app.mount("/pdfs", StaticFiles(directory=str(PDFS_DIR)), name="pdfs")


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------


@app.get("/")
def home():
    """Serve the main explorer UI."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/stats", response_model=StatsResponse)
def get_stats(db: DB):
    """Get overall statistics."""
    complaints = db["complaints"]
    settlements = db["settlements"]

    total = complaints.count_documents({})
    with_extraction = complaints.count_documents({"llm_extracted": {"$exists": True}})
    settlements_with_extraction = settlements.count_documents({"llm_extracted": {"$exists": True}})

    categories = complaints.distinct("llm_extracted.category")
    categories = [c for c in categories if c]

    drugs_pipeline = [
        {"$unwind": "$llm_extracted.drugs"},
        {"$group": {"_id": "$llm_extracted.drugs"}},
        {"$count": "count"}
    ]
    drugs_result = list(complaints.aggregate(drugs_pipeline))
    drugs_count = drugs_result[0]["count"] if drugs_result else 0

    return StatsResponse(
        total=total,
        with_extraction=with_extraction,
        resolutions=settlements_with_extraction,
        categories=len(categories),
        drugs=drugs_count
    )


@app.get("/api/filters", response_model=FiltersResponse)
def get_filters(db: DB):
    """Get available filter options."""
    complaints = db["complaints"]
    settlements = db["settlements"]

    categories = complaints.distinct("llm_extracted.category")
    categories = sorted([c for c in categories if c])

    specialties = complaints.distinct("llm_extracted.specialty")
    specialties = sorted([s for s in specialties if s])
    specialties.append("Missing")  # Add option to filter for cases without specialty

    years = complaints.distinct("year")
    years = sorted([y for y in years if y], reverse=True)

    drugs_pipeline = [
        {"$unwind": "$llm_extracted.drugs"},
        {"$group": {"_id": {"$toLower": "$llm_extracted.drugs"}, "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 100}
    ]
    drugs_result = list(complaints.aggregate(drugs_pipeline))
    drugs = [d["_id"] for d in drugs_result]

    # Get distinct license actions from settlements
    license_actions = settlements.distinct("llm_extracted.license_action")
    license_actions = sorted([a for a in license_actions if a])

    return FiltersResponse(
        categories=categories,
        specialties=specialties,
        years=years,
        drugs=drugs,
        license_actions=license_actions
    )


@app.get("/api/complaints", response_model=ComplaintsResponse)
def get_complaints(
    db: DB,
    category: Optional[str] = None,
    specialty: Optional[str] = None,
    year: Optional[str] = None,
    drug: Optional[str] = None,
    sex: Optional[str] = None,
    has_settlement: Optional[str] = None,
    license_action: Optional[str] = None,
    sort: str = "date_desc",
    skip: int = 0,
    limit: int = Query(default=20, le=100)
):
    """Get complaints with filtering and sorting. Multi-value filters accept comma-separated values."""
    complaints = db["complaints"]
    settlements = db["settlements"]

    query = {"llm_extracted": {"$exists": True}}

    # Multi-value filters (comma-separated)
    if category:
        categories = [c.strip() for c in category.split(",")]
        if len(categories) == 1:
            query["llm_extracted.category"] = categories[0]
        else:
            query["llm_extracted.category"] = {"$in": categories}

    if specialty:
        specialties = [s.strip() for s in specialty.split(",")]
        has_missing = "Missing" in specialties
        real_specialties = [s for s in specialties if s != "Missing"]

        if has_missing and not real_specialties:
            # Only "Missing" selected - match null, empty, or non-existent
            query["$or"] = [
                {"llm_extracted.specialty": {"$exists": False}},
                {"llm_extracted.specialty": None},
                {"llm_extracted.specialty": ""}
            ]
        elif has_missing and real_specialties:
            # "Missing" + real specialties - use $or
            query["$or"] = [
                {"llm_extracted.specialty": {"$in": real_specialties}},
                {"llm_extracted.specialty": {"$exists": False}},
                {"llm_extracted.specialty": None},
                {"llm_extracted.specialty": ""}
            ]
        elif len(real_specialties) == 1:
            query["llm_extracted.specialty"] = real_specialties[0]
        else:
            query["llm_extracted.specialty"] = {"$in": real_specialties}

    if year:
        years = [int(y.strip()) for y in year.split(",")]
        if len(years) == 1:
            query["year"] = years[0]
        else:
            query["year"] = {"$in": years}

    if drug:
        drugs = [d.strip() for d in drug.split(",")]
        if len(drugs) == 1:
            query["llm_extracted.drugs"] = {"$regex": drugs[0], "$options": "i"}
        else:
            # Match any of the drugs using $or
            query["$or"] = [{"llm_extracted.drugs": {"$regex": d, "$options": "i"}} for d in drugs]

    if sex:
        sexes = [s.strip() for s in sex.split(",")]
        if len(sexes) == 1:
            query["llm_extracted.complainants.sex"] = sexes[0]
        else:
            query["llm_extracted.complainants.sex"] = {"$in": sexes}

    # Filter by settlement existence - use aggregation to get all case numbers in one query
    if has_settlement:
        # Single aggregation to unwind and collect all case numbers with settlements
        settlement_case_numbers_pipeline = [
            {"$unwind": "$case_numbers"},
            {"$group": {"_id": None, "case_nums": {"$addToSet": "$case_numbers"}}}
        ]
        result = list(settlements.aggregate(settlement_case_numbers_pipeline))
        settlement_case_numbers = result[0]["case_nums"] if result else []

        if has_settlement == "yes":
            query["case_number"] = {"$in": settlement_case_numbers}
        elif has_settlement == "no":
            query["case_number"] = {"$nin": settlement_case_numbers}

    # Filter by license action - get case numbers from settlements with matching action
    if license_action:
        actions = [a.strip() for a in license_action.split(",")]
        if len(actions) == 1:
            action_match = {"llm_extracted.license_action": actions[0]}
        else:
            action_match = {"llm_extracted.license_action": {"$in": actions}}

        action_case_numbers_pipeline = [
            {"$match": action_match},
            {"$unwind": "$case_numbers"},
            {"$group": {"_id": None, "case_nums": {"$addToSet": "$case_numbers"}}}
        ]
        result = list(settlements.aggregate(action_case_numbers_pipeline))
        action_case_numbers = result[0]["case_nums"] if result else []

        # Combine with existing case_number filter if present
        if "case_number" in query:
            # Intersect with existing filter
            existing = query["case_number"]
            if "$in" in existing:
                query["case_number"] = {"$in": list(set(existing["$in"]) & set(action_case_numbers))}
            elif "$nin" in existing:
                query["case_number"] = {"$in": [cn for cn in action_case_numbers if cn not in existing["$nin"]]}
        else:
            query["case_number"] = {"$in": action_case_numbers}

    # Sorting - need aggregation pipeline for date sorting since dates are stored as M/D/YYYY strings
    total = complaints.count_documents(query)

    # Build aggregation pipeline for proper date sorting
    pipeline = [
        {"$match": query},
        {"$project": {"text_content": 0}},
    ]

    if sort in ("date_desc", "date_asc"):
        # Parse M/D/YYYY date string to proper date for sorting
        pipeline.append({
            "$addFields": {
                "_parsed_date": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%m/%d/%Y",
                        "onError": None,
                        "onNull": None
                    }
                }
            }
        })
        sort_dir = -1 if sort == "date_desc" else 1
        pipeline.append({"$sort": {"_parsed_date": sort_dir, "case_number": sort_dir}})
        pipeline.append({"$project": {"_parsed_date": 0}})
    elif sort == "respondent_asc":
        pipeline.append({"$sort": {"respondent": 1, "case_number": 1}})
    elif sort == "respondent_desc":
        pipeline.append({"$sort": {"respondent": -1, "case_number": -1}})
    else:
        # Default to date descending
        pipeline.append({
            "$addFields": {
                "_parsed_date": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%m/%d/%Y",
                        "onError": None,
                        "onNull": None
                    }
                }
            }
        })
        pipeline.append({"$sort": {"_parsed_date": -1, "case_number": -1}})
        pipeline.append({"$project": {"_parsed_date": 0}})

    pipeline.append({"$skip": skip})
    pipeline.append({"$limit": limit})

    results_list = list(complaints.aggregate(pipeline))

    # Helper functions for case number parsing
    def get_case_prefix(case_num: str) -> str:
        """Extract prefix from case number (e.g., '19-28023' from '19-28023-1')"""
        parts = case_num.rsplit("-", 1)
        return parts[0] if len(parts) > 1 else case_num

    def get_case_suffix(case_num: str) -> int:
        """Extract suffix from case number (e.g., 1 from '19-28023-1')"""
        parts = case_num.rsplit("-", 1)
        try:
            return int(parts[1]) if len(parts) > 1 else 1
        except ValueError:
            return 1

    # Get case numbers from results for targeted settlement lookup
    case_numbers_in_results = [doc.get("case_number", "") for doc in results_list]

    # Fetch ONLY settlements for case numbers in current page (not all settlements)
    settlement_lookup = {}
    if case_numbers_in_results:
        settlement_query = {
            "case_numbers": {"$in": case_numbers_in_results},
            "llm_extracted": {"$exists": True}
        }
        for doc in settlements.find(settlement_query):
            ext = doc.get("llm_extracted", {})
            summary = {
                "license_action": ext.get("license_action"),
                "fine_amount": ext.get("fine_amount"),
                "investigation_costs": ext.get("investigation_costs"),
                "cme_hours": ext.get("cme_hours"),
                "probation_months": ext.get("probation_months"),
                "date": doc.get("date"),
                "resolution_outcome": doc.get("resolution_outcome"),
            }
            for cn in doc.get("case_numbers", []):
                if cn in case_numbers_in_results:
                    settlement_lookup[cn] = summary

    # Get unique prefixes and count cases in a single aggregation query
    prefixes_in_results = list(set(
        get_case_prefix(doc.get("case_number", ""))
        for doc in results_list
        if doc.get("case_number")
    ))

    prefix_counts = {}
    if prefixes_in_results:
        # Build regex pattern to match all prefixes at once, then group in Python
        prefix_regex = "^(" + "|".join(re.escape(p) for p in prefixes_in_results) + ")-"
        matching_cases = complaints.find(
            {
                "case_number": {"$regex": prefix_regex},
                "llm_extracted": {"$exists": True}
            },
            {"case_number": 1}
        )
        # Count by prefix in Python (simpler than complex aggregation)
        for doc in matching_cases:
            prefix = get_case_prefix(doc.get("case_number", ""))
            if prefix:
                prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1

    results = []
    for doc in results_list:
        doc["_id"] = str(doc["_id"])
        case_num = doc.get("case_number", "")
        # Attach settlement summary if available
        if case_num in settlement_lookup:
            doc["settlement_summary"] = settlement_lookup[case_num]
        # Attach case index and total based on case number prefix
        # Use max of suffix and actual count to handle cases where earlier numbers are missing
        prefix = get_case_prefix(case_num)
        case_suffix = get_case_suffix(case_num)
        doc["case_index"] = case_suffix
        doc["total_cases"] = max(case_suffix, prefix_counts.get(prefix, 1))
        results.append(doc)

    return ComplaintsResponse(complaints=results, total=total)


@app.get("/api/random")
def get_random(db: DB):
    """Get a random complaint."""
    complaints = db["complaints"]
    settlements = db["settlements"]

    pipeline = [
        {"$match": {"llm_extracted": {"$exists": True}}},
        {"$sample": {"size": 1}},
        {"$project": {"text_content": 0}}
    ]
    result = list(complaints.aggregate(pipeline))
    if result:
        doc = result[0]
        doc["_id"] = str(doc["_id"])
        # Check for resolution
        case_num = doc.get("case_number")
        settlement = settlements.find_one({"case_numbers": case_num, "llm_extracted": {"$exists": True}})
        if settlement:
            ext = settlement.get("llm_extracted", {})
            doc["settlement_summary"] = {
                "license_action": ext.get("license_action"),
                "fine_amount": ext.get("fine_amount"),
                "investigation_costs": ext.get("investigation_costs"),
                "cme_hours": ext.get("cme_hours"),
                "probation_months": ext.get("probation_months"),
                "resolution_outcome": settlement.get("resolution_outcome"),
            }
        return doc
    return {"error": "No complaints found"}


@app.get("/api/complaint/{case_number}")
def get_complaint(case_number: str, db: DB):
    """Get a specific complaint by case number."""
    complaints = db["complaints"]

    doc = complaints.find_one({"case_number": case_number})
    if doc:
        doc["_id"] = str(doc["_id"])
        return doc
    return {"error": "Complaint not found"}


@app.get("/api/settlement/{case_number}")
def get_settlement(case_number: str, db: DB):
    """Get a settlement by case number."""
    settlements = db["settlements"]

    doc = settlements.find_one({"case_numbers": case_number})
    if doc:
        doc["_id"] = str(doc["_id"])
        if doc.get("complaint_ids"):
            doc["complaint_ids"] = [str(cid) for cid in doc["complaint_ids"]]
        if doc.get("case_numbers"):
            doc["case_number"] = doc["case_numbers"][0]
        return doc
    return None


@app.get("/api/analytics", response_model=AnalyticsResponse)
def get_analytics(db: DB):
    """Get aggregate analytics data for charts."""
    complaints = db["complaints"]
    settlements = db["settlements"]

    # Fine amounts distribution
    fines_pipeline = [
        {"$match": {"llm_extracted.fine_amount": {"$exists": True, "$ne": None, "$gt": 0}}},
        {"$group": {"_id": None, "values": {"$push": "$llm_extracted.fine_amount"}}}
    ]
    fines_result = list(settlements.aggregate(fines_pipeline))
    fine_values = fines_result[0]["values"] if fines_result else []

    # Investigation costs distribution
    costs_pipeline = [
        {"$match": {"llm_extracted.investigation_costs": {"$exists": True, "$ne": None, "$gt": 0}}},
        {"$group": {"_id": None, "values": {"$push": "$llm_extracted.investigation_costs"}}}
    ]
    costs_result = list(settlements.aggregate(costs_pipeline))
    cost_values = costs_result[0]["values"] if costs_result else []

    # CME hours distribution
    cme_pipeline = [
        {"$match": {"llm_extracted.cme_hours": {"$exists": True, "$ne": None, "$gt": 0}}},
        {"$group": {"_id": None, "values": {"$push": "$llm_extracted.cme_hours"}}}
    ]
    cme_result = list(settlements.aggregate(cme_pipeline))
    cme_values = cme_result[0]["values"] if cme_result else []

    # Probation months distribution
    probation_pipeline = [
        {"$match": {"llm_extracted.probation_months": {"$exists": True, "$ne": None, "$gt": 0}}},
        {"$group": {"_id": None, "values": {"$push": "$llm_extracted.probation_months"}}}
    ]
    probation_result = list(settlements.aggregate(probation_pipeline))
    probation_values = probation_result[0]["values"] if probation_result else []

    # License actions breakdown
    actions_pipeline = [
        {"$match": {"llm_extracted.license_action": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$llm_extracted.license_action", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 15}
    ]
    actions_result = list(settlements.aggregate(actions_pipeline))

    # Specialty breakdown
    specialty_pipeline = [
        {"$match": {"llm_extracted.specialty": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$llm_extracted.specialty", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 15}
    ]
    specialty_result = list(complaints.aggregate(specialty_pipeline))

    # Category breakdown
    category_pipeline = [
        {"$match": {"llm_extracted.category": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$llm_extracted.category", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    category_result = list(complaints.aggregate(category_pipeline))

    # Cases by year
    year_pipeline = [
        {"$match": {"year": {"$exists": True}}},
        {"$group": {"_id": "$year", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}}
    ]
    year_result = list(complaints.aggregate(year_pipeline))

    # Settlement outcomes summary
    settlement_summary = SettlementSummary(
        total=settlements.count_documents({}),
        with_fine=settlements.count_documents({"llm_extracted.fine_amount": {"$gt": 0}}),
        with_probation=settlements.count_documents({"llm_extracted.probation_months": {"$gt": 0}}),
        with_cme=settlements.count_documents({"llm_extracted.cme_hours": {"$gt": 0}}),
        public_reprimand=settlements.count_documents({"llm_extracted.public_reprimand": True}),
        npdb_report=settlements.count_documents({"llm_extracted.npdb_report": True}),
    )

    # Calculate totals
    totals_pipeline = [
        {"$group": {
            "_id": None,
            "total_fines": {"$sum": {"$ifNull": ["$llm_extracted.fine_amount", 0]}},
            "total_investigation_costs": {"$sum": {"$ifNull": ["$llm_extracted.investigation_costs", 0]}},
            "total_cme_hours": {"$sum": {"$ifNull": ["$llm_extracted.cme_hours", 0]}},
            "total_probation_months": {"$sum": {"$ifNull": ["$llm_extracted.probation_months", 0]}},
        }}
    ]
    totals_result = list(settlements.aggregate(totals_pipeline))
    totals_data = totals_result[0] if totals_result else {}

    # Fines by year
    fines_by_year_pipeline = [
        {"$match": {"llm_extracted.fine_amount": {"$gt": 0}}},
        {"$group": {
            "_id": "$year",
            "total": {"$sum": "$llm_extracted.fine_amount"},
            "count": {"$sum": 1}
        }},
        {"$sort": {"_id": 1}}
    ]
    fines_by_year = list(settlements.aggregate(fines_by_year_pipeline))

    # Calculate resolution times (days from complaint to resolution)
    resolution_times = []
    # Build a lookup of case_number -> complaint_date
    complaint_dates = {}
    for doc in complaints.find({"date": {"$exists": True}}, {"case_number": 1, "date": 1}):
        try:
            complaint_date = datetime.strptime(doc["date"], "%m/%d/%Y")
            complaint_dates[doc["case_number"]] = complaint_date
        except (ValueError, KeyError):
            continue

    # For each settlement, find the earliest complaint date and calculate resolution time
    for settlement in settlements.find({"date": {"$exists": True, "$ne": None}, "case_numbers": {"$exists": True}}):
        try:
            date_str = settlement.get("date")
            if not date_str:
                continue
            resolution_date = datetime.strptime(date_str, "%m/%d/%Y")
            case_numbers = settlement.get("case_numbers", [])
            # Find earliest complaint date for this settlement
            earliest_complaint = None
            for cn in case_numbers:
                if cn in complaint_dates:
                    if earliest_complaint is None or complaint_dates[cn] < earliest_complaint:
                        earliest_complaint = complaint_dates[cn]
            if earliest_complaint:
                days = (resolution_date - earliest_complaint).days
                if days >= 0:  # Only include valid (non-negative) resolution times
                    resolution_times.append(days)
        except (ValueError, KeyError, TypeError):
            continue

    # Calculate resolution time statistics
    resolution_time_stats = None
    if resolution_times:
        sorted_times = sorted(resolution_times)
        resolution_time_stats = ResolutionTimeStats(
            mean_days=round(statistics.mean(resolution_times), 1),
            median_days=float(statistics.median(resolution_times)),
            min_days=min(resolution_times),
            max_days=max(resolution_times),
            count=len(resolution_times)
        )

    # Calculate years span
    years = [r["_id"] for r in year_result if r["_id"]]
    year_span = max(years) - min(years) + 1 if years else 1

    totals = Totals(
        total_fines=totals_data.get("total_fines", 0),
        total_investigation_costs=totals_data.get("total_investigation_costs", 0),
        total_cme_hours=int(totals_data.get("total_cme_hours", 0)),
        total_probation_months=int(totals_data.get("total_probation_months", 0)),
        avg_fine_per_year=round(totals_data.get("total_fines", 0) / year_span) if year_span else 0,
        total_complaints=complaints.count_documents({}),
        year_span=year_span,
        min_year=min(years) if years else None,
        max_year=max(years) if years else None,
    )

    return AnalyticsResponse(
        fine_values=fine_values,
        cost_values=cost_values,
        cme_values=cme_values,
        probation_values=probation_values,
        resolution_time_values=resolution_times,
        resolution_time_stats=resolution_time_stats,
        license_actions=[LicenseActionCount(action=r["_id"], count=r["count"]) for r in actions_result],
        specialties=[SpecialtyCount(specialty=r["_id"], count=r["count"]) for r in specialty_result],
        categories=[CategoryCount(category=r["_id"], count=r["count"]) for r in category_result],
        by_year=[YearCount(year=r["_id"], count=r["count"]) for r in year_result],
        fines_by_year=[FinesByYear(year=r["_id"], total=r["total"], count=r["count"]) for r in fines_by_year],
        settlement_summary=settlement_summary,
        totals=totals,
    )


@app.get("/api/debug")
def get_debug_data(db: DB):
    """Get collection schemas and field statistics for debugging."""
    collections_info = []

    # Field descriptions for documentation
    field_descriptions = {
        "complaints": {
            "_id": "MongoDB document ID",
            "case_number": "Unique case identifier (e.g., '19-28023-1')",
            "respondent": "Name of the doctor/practitioner",
            "date": "Date complaint was filed (M/D/YYYY)",
            "year": "Year extracted from date",
            "type": "Document type (e.g., 'Formal Complaint')",
            "pdf_url": "URL to original PDF on Nevada Medical Board site",
            "is_amended": "Whether this is an amended complaint",
            "original_complaint": "Reference to original complaint if amended",
            "amendment_summary": "LLM summary of changes from original",
            "llm_extracted.summary": "One-sentence case summary",
            "llm_extracted.specialty": "ABMS-recognized medical specialty",
            "llm_extracted.category": "Case category (Treatment, Diagnosis, etc.)",
            "llm_extracted.procedure": "Medical procedure involved",
            "llm_extracted.num_complainants": "Number of patients",
            "llm_extracted.complainants": "Array of {age, sex} for each patient",
            "llm_extracted.drugs": "Medications mentioned in complaint",
        },
        "settlements": {
            "_id": "MongoDB document ID",
            "case_numbers": "Array of case numbers this resolution covers",
            "complaint_ids": "Array of ObjectIds linking to complaints",
            "respondent": "Name of the doctor/practitioner",
            "date": "Date of resolution (M/D/YYYY)",
            "year": "Year extracted from date",
            "type": "Document type (e.g., 'Stipulation for Settlement')",
            "resolution_outcome": "'Settlement' (negotiated) or 'Hearing' (contested)",
            "pdf_url": "URL to original PDF",
            "llm_extracted.license_action": "Action taken (revoked, suspended, probation, etc.)",
            "llm_extracted.probation_months": "Duration of probation in months",
            "llm_extracted.fine_amount": "Dollar amount of fine",
            "llm_extracted.investigation_costs": "Costs recovered from respondent",
            "llm_extracted.cme_hours": "Required continuing education hours",
            "llm_extracted.cme_topic": "Topic area for CME",
            "llm_extracted.public_reprimand": "Whether public reprimand was issued",
            "llm_extracted.npdb_report": "Whether reported to NPDB",
            "llm_extracted.violations_admitted": "Array of NRS violations admitted",
        },
        "license_only_filings": {
            "_id": "MongoDB document ID",
            "license_number": "License number (LICENSE-XXX format)",
            "type": "Document type (e.g., 'Order of Summary Suspension')",
            "year": "Year of filing",
            "date": "Date of filing",
            "respondent": "Name of the practitioner",
            "pdf_url": "URL to original PDF",
            "text_content": "OCR extracted text content",
        },
    }

    def get_field_stats(collection, field_path, is_numeric=False):
        """Get statistics for a field."""
        stats = {}

        # Count non-null values
        is_array = field_path.endswith("[]")
        non_null_query = {field_path: {"$exists": True, "$ne": None}}
        if is_array:
            # Array field - check for non-empty arrays
            base_field = field_path.rstrip("[]")
            non_null_query = {base_field: {"$exists": True, "$not": {"$size": 0}}}
            field_path = base_field

        non_null_count = collection.count_documents(non_null_query)
        total_count = collection.count_documents({})
        stats["count"] = non_null_count
        stats["null_count"] = total_count - non_null_count
        stats["null_pct"] = round((total_count - non_null_count) / total_count * 100, 1) if total_count > 0 else 0

        if non_null_count == 0:
            return stats

        # For array fields, compute length statistics and unique values
        if is_array:
            length_pipeline = [
                {"$match": {field_path: {"$exists": True, "$type": "array"}}},
                {"$project": {"_len": {"$size": f"${field_path}"}}},
                {"$group": {
                    "_id": None,
                    "min_len": {"$min": "$_len"},
                    "max_len": {"$max": "$_len"},
                    "avg_len": {"$avg": "$_len"},
                }}
            ]
            len_result = list(collection.aggregate(length_pipeline))
            if len_result:
                stats["min_length"] = len_result[0].get("min_len")
                stats["max_length"] = len_result[0].get("max_len")
                stats["avg_length"] = round(len_result[0].get("avg_len", 0), 2)

            # Get unique values and top examples for arrays
            unique_pipeline = [
                {"$match": {field_path: {"$exists": True, "$type": "array", "$ne": []}}},
                {"$unwind": f"${field_path}"},
                {"$group": {"_id": f"${field_path}", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 100}  # Get top 100 for unique count
            ]
            unique_result = list(collection.aggregate(unique_pipeline))
            stats["unique"] = len(unique_result)
            # Top 3 examples by frequency
            stats["examples"] = [
                {"value": str(r["_id"]) if r["_id"] is not None else None, "count": r["count"]}
                for r in unique_result[:3]
            ]

        if is_numeric:
            # Get min, max, mean for numeric fields
            pipeline = [
                {"$match": {field_path: {"$exists": True, "$ne": None, "$type": "number"}}},
                {"$group": {
                    "_id": None,
                    "min": {"$min": f"${field_path}"},
                    "max": {"$max": f"${field_path}"},
                    "avg": {"$avg": f"${field_path}"},
                    "sum": {"$sum": f"${field_path}"},
                }}
            ]
            result = list(collection.aggregate(pipeline))
            if result:
                stats["min"] = result[0].get("min")
                stats["max"] = result[0].get("max")
                stats["mean"] = round(result[0].get("avg", 0), 2)
                stats["sum"] = result[0].get("sum")
        else:
            # Get unique count and top values for string fields
            unique_values = collection.distinct(field_path)
            unique_values = [v for v in unique_values if v is not None and v != ""]
            stats["unique"] = len(unique_values)

            # Get top 5 values by frequency
            top_pipeline = [
                {"$match": {field_path: {"$exists": True, "$ne": None, "$ne": ""}}},
                {"$group": {"_id": f"${field_path}", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 5}
            ]
            top_result = list(collection.aggregate(top_pipeline))
            # Convert ObjectId and other non-JSON types to strings
            stats["top_values"] = [
                {"value": str(r["_id"]) if r["_id"] is not None else None, "count": r["count"]}
                for r in top_result
            ]

        return stats

    # Process each collection
    for coll_name in ["complaints", "settlements", "license_only_filings"]:
        coll = db[coll_name]
        total = coll.count_documents({})

        if total == 0:
            collections_info.append({
                "name": coll_name,
                "total_documents": 0,
                "fields": []
            })
            continue

        # Sample a document to get field structure
        sample = coll.find_one()
        fields_data = []
        descriptions = field_descriptions.get(coll_name, {})

        # Define which fields are numeric
        numeric_fields = {
            "year", "llm_extracted.fine_amount", "llm_extracted.investigation_costs",
            "llm_extracted.probation_months", "llm_extracted.cme_hours",
            "llm_extracted.num_complainants", "llm_extracted.charity_donation"
        }

        def process_fields(doc, prefix=""):
            """Recursively process document fields."""
            results = []
            for key, value in doc.items():
                if key == "_id":
                    continue
                field_path = f"{prefix}{key}" if prefix else key

                if isinstance(value, dict) and key != "original_complaint":
                    # Nested document - recurse
                    results.extend(process_fields(value, f"{field_path}."))
                elif isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
                    # Array of objects - note as array
                    results.append({
                        "field": field_path,
                        "type": "array<object>",
                        "description": descriptions.get(field_path, ""),
                        "stats": get_field_stats(coll, field_path + "[]")
                    })
                elif isinstance(value, list):
                    # Array of primitives
                    results.append({
                        "field": field_path,
                        "type": "array",
                        "description": descriptions.get(field_path, ""),
                        "stats": get_field_stats(coll, field_path + "[]")
                    })
                else:
                    # Primitive field
                    field_type = type(value).__name__ if value is not None else "unknown"
                    is_numeric = field_path in numeric_fields or field_type in ("int", "float")
                    results.append({
                        "field": field_path,
                        "type": field_type,
                        "description": descriptions.get(field_path, ""),
                        "stats": get_field_stats(coll, field_path, is_numeric)
                    })
            return results

        fields_data = process_fields(sample)

        # Sort fields alphabetically
        fields_data.sort(key=lambda x: x["field"])

        collections_info.append({
            "name": coll_name,
            "total_documents": total,
            "fields": fields_data
        })

    return {"collections": collections_info}


@app.get("/api/debug/{collection_name}")
def get_debug_collection(collection_name: str, db: DB):
    """Get schema and field statistics for a single collection."""
    valid_collections = ["complaints", "settlements", "license_only_filings"]
    if collection_name not in valid_collections:
        return {"error": f"Invalid collection. Must be one of: {valid_collections}"}

    # Field descriptions for documentation
    field_descriptions = {
        "complaints": {
            "_id": "MongoDB document ID",
            "case_number": "Unique case identifier (e.g., '19-28023-1')",
            "respondent": "Name of the doctor/practitioner",
            "date": "Date complaint was filed (M/D/YYYY)",
            "year": "Year extracted from date",
            "type": "Document type (e.g., 'Formal Complaint')",
            "pdf_url": "URL to original PDF on Nevada Medical Board site",
            "is_amended": "Whether this is an amended complaint",
            "original_complaint": "Reference to original complaint if amended",
            "amendment_summary": "LLM summary of changes from original",
            "llm_extracted.summary": "One-sentence case summary",
            "llm_extracted.specialty": "ABMS-recognized medical specialty",
            "llm_extracted.category": "Case category (Treatment, Diagnosis, etc.)",
            "llm_extracted.procedure": "Medical procedure involved",
            "llm_extracted.num_complainants": "Number of patients",
            "llm_extracted.complainants": "Array of {age, sex} for each patient",
            "llm_extracted.drugs": "Medications mentioned in complaint",
        },
        "settlements": {
            "_id": "MongoDB document ID",
            "case_numbers": "Array of case numbers this resolution covers",
            "complaint_ids": "Array of ObjectIds linking to complaints",
            "respondent": "Name of the doctor/practitioner",
            "date": "Date of resolution (M/D/YYYY)",
            "year": "Year extracted from date",
            "type": "Document type (e.g., 'Stipulation for Settlement')",
            "resolution_outcome": "'Settlement' (negotiated) or 'Hearing' (contested)",
            "pdf_url": "URL to original PDF",
            "llm_extracted.license_action": "Action taken (revoked, suspended, probation, etc.)",
            "llm_extracted.probation_months": "Duration of probation in months",
            "llm_extracted.fine_amount": "Dollar amount of fine",
            "llm_extracted.investigation_costs": "Costs recovered from respondent",
            "llm_extracted.cme_hours": "Required continuing education hours",
            "llm_extracted.cme_topic": "Topic area for CME",
            "llm_extracted.public_reprimand": "Whether public reprimand was issued",
            "llm_extracted.npdb_report": "Whether reported to NPDB",
            "llm_extracted.violations_admitted": "Array of NRS violations admitted",
        },
        "license_only_filings": {
            "_id": "MongoDB document ID",
            "license_number": "License number (LICENSE-XXX format)",
            "type": "Document type (e.g., 'Order of Summary Suspension')",
            "year": "Year of filing",
            "date": "Date of filing",
            "respondent": "Name of the practitioner",
            "pdf_url": "URL to original PDF",
            "text_content": "OCR extracted text content",
        },
    }

    def get_field_stats(collection, field_path, is_numeric=False):
        """Get statistics for a field."""
        stats = {}
        is_array = field_path.endswith("[]")
        non_null_query = {field_path: {"$exists": True, "$ne": None}}
        if is_array:
            base_field = field_path.rstrip("[]")
            non_null_query = {base_field: {"$exists": True, "$not": {"$size": 0}}}
            field_path = base_field

        non_null_count = collection.count_documents(non_null_query)
        total_count = collection.count_documents({})
        stats["count"] = non_null_count
        stats["null_count"] = total_count - non_null_count
        stats["null_pct"] = round((total_count - non_null_count) / total_count * 100, 1) if total_count > 0 else 0

        if non_null_count == 0:
            return stats

        if is_array:
            length_pipeline = [
                {"$match": {field_path: {"$exists": True, "$type": "array"}}},
                {"$project": {"_len": {"$size": f"${field_path}"}}},
                {"$group": {"_id": None, "min_len": {"$min": "$_len"}, "max_len": {"$max": "$_len"}, "avg_len": {"$avg": "$_len"}}}
            ]
            len_result = list(collection.aggregate(length_pipeline))
            if len_result:
                stats["min_length"] = len_result[0].get("min_len")
                stats["max_length"] = len_result[0].get("max_len")
                stats["avg_length"] = round(len_result[0].get("avg_len", 0), 2)

            unique_pipeline = [
                {"$match": {field_path: {"$exists": True, "$type": "array", "$ne": []}}},
                {"$unwind": f"${field_path}"},
                {"$group": {"_id": f"${field_path}", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 100}
            ]
            unique_result = list(collection.aggregate(unique_pipeline))
            stats["unique"] = len(unique_result)
            stats["examples"] = [{"value": str(r["_id"]) if r["_id"] is not None else None, "count": r["count"]} for r in unique_result[:3]]

        if is_numeric:
            pipeline = [
                {"$match": {field_path: {"$exists": True, "$ne": None, "$type": "number"}}},
                {"$group": {"_id": None, "min": {"$min": f"${field_path}"}, "max": {"$max": f"${field_path}"}, "avg": {"$avg": f"${field_path}"}, "sum": {"$sum": f"${field_path}"}}}
            ]
            result = list(collection.aggregate(pipeline))
            if result:
                stats["min"] = result[0].get("min")
                stats["max"] = result[0].get("max")
                stats["mean"] = round(result[0].get("avg", 0), 2)
                stats["sum"] = result[0].get("sum")
        else:
            unique_values = collection.distinct(field_path)
            unique_values = [v for v in unique_values if v is not None and v != ""]
            stats["unique"] = len(unique_values)
            top_pipeline = [
                {"$match": {field_path: {"$exists": True, "$ne": None, "$ne": ""}}},
                {"$group": {"_id": f"${field_path}", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 5}
            ]
            top_result = list(collection.aggregate(top_pipeline))
            stats["top_values"] = [{"value": str(r["_id"]) if r["_id"] is not None else None, "count": r["count"]} for r in top_result]

        return stats

    coll = db[collection_name]
    total = coll.count_documents({})

    if total == 0:
        return {"name": collection_name, "total_documents": 0, "fields": []}

    sample = coll.find_one()
    descriptions = field_descriptions.get(collection_name, {})
    numeric_fields = {"year", "llm_extracted.fine_amount", "llm_extracted.investigation_costs", "llm_extracted.probation_months", "llm_extracted.cme_hours", "llm_extracted.num_complainants", "llm_extracted.charity_donation"}

    def process_fields(doc, prefix=""):
        results = []
        for key, value in doc.items():
            if key == "_id":
                continue
            field_path = f"{prefix}{key}" if prefix else key
            if isinstance(value, dict) and key != "original_complaint":
                results.extend(process_fields(value, f"{field_path}."))
            elif isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
                results.append({"field": field_path, "type": "array<object>", "description": descriptions.get(field_path, ""), "stats": get_field_stats(coll, field_path + "[]")})
            elif isinstance(value, list):
                results.append({"field": field_path, "type": "array", "description": descriptions.get(field_path, ""), "stats": get_field_stats(coll, field_path + "[]")})
            else:
                field_type = type(value).__name__ if value is not None else "unknown"
                is_numeric = field_path in numeric_fields or field_type in ("int", "float")
                results.append({"field": field_path, "type": field_type, "description": descriptions.get(field_path, ""), "stats": get_field_stats(coll, field_path, is_numeric)})
        return results

    fields_data = process_fields(sample)
    fields_data.sort(key=lambda x: x["field"])

    return {"name": collection_name, "total_documents": total, "fields": fields_data}


@app.get("/api/extended-analytics")
def get_extended_analytics(db: DB):
    """Get extended analytics for deep-dive statistics."""
    complaints = db["complaints"]
    settlements = db["settlements"]

    # Build lookups
    complaint_data = {}
    for doc in complaints.find({"date": {"$exists": True}, "llm_extracted": {"$exists": True}}):
        try:
            complaint_date = datetime.strptime(doc["date"], "%m/%d/%Y")
            ext = doc.get("llm_extracted", {})
            complaint_data[doc["case_number"]] = {
                "date": complaint_date,
                "year": doc.get("year"),
                "category": ext.get("category"),
                "specialty": ext.get("specialty"),
            }
        except (ValueError, KeyError):
            continue

    # === RESOLUTION TIME BY CATEGORY ===
    resolution_by_category = {}
    resolution_by_action = {}

    for s in settlements.find({"date": {"$exists": True, "$ne": None}, "case_numbers": {"$exists": True}}):
        try:
            res_date = datetime.strptime(s["date"], "%m/%d/%Y")
            case_numbers = s.get("case_numbers", [])
            earliest_cn = None
            earliest_date = None
            for cn in case_numbers:
                if cn in complaint_data:
                    cd = complaint_data[cn]["date"]
                    if earliest_date is None or cd < earliest_date:
                        earliest_date = cd
                        earliest_cn = cn

            if earliest_cn and earliest_date:
                days = (res_date - earliest_date).days
                if days >= 0:
                    cat = complaint_data[earliest_cn].get("category")
                    if cat:
                        if cat not in resolution_by_category:
                            resolution_by_category[cat] = []
                        resolution_by_category[cat].append(days)

                    ext = s.get("llm_extracted", {})
                    action = (ext.get("license_action") or "").lower()
                    if action:
                        # Normalize action
                        if "revok" in action or "revoc" in action:
                            action_key = "revoked"
                        elif "surrender" in action:
                            action_key = "surrendered"
                        elif "suspen" in action:
                            action_key = "suspended"
                        elif "probation" in action:
                            action_key = "probation"
                        elif "reprimand" in action:
                            action_key = "reprimand"
                        elif "none" in action or "no action" in action:
                            action_key = "no action"
                        else:
                            action_key = None

                        if action_key:
                            if action_key not in resolution_by_action:
                                resolution_by_action[action_key] = []
                            resolution_by_action[action_key].append(days)
        except (ValueError, KeyError, TypeError):
            continue

    def calc_stats(values):
        if not values or len(values) < 3:
            return None
        sorted_vals = sorted(values)
        return {
            "n": len(values),
            "median": float(sorted_vals[len(sorted_vals) // 2]),
            "mean": round(sum(values) / len(values), 1),
            "min": min(values),
            "max": max(values),
        }

    resolution_time_by_category = [
        {"category": cat, **calc_stats(days)}
        for cat, days in resolution_by_category.items()
        if calc_stats(days)
    ]
    resolution_time_by_category.sort(key=lambda x: x["median"])

    resolution_time_by_action = [
        {"action": action, **calc_stats(days)}
        for action, days in resolution_by_action.items()
        if calc_stats(days)
    ]

    # === FINES BY CATEGORY ===
    fines_by_category = {}
    fines_by_action = {}
    fines_by_year = {}

    for s in settlements.find({"llm_extracted.fine_amount": {"$exists": True, "$gt": 0}}):
        fine = s["llm_extracted"]["fine_amount"]
        year = s.get("year")

        # By year
        if year:
            if year not in fines_by_year:
                fines_by_year[year] = []
            fines_by_year[year].append(fine)

        # By category (from linked complaint)
        case_numbers = s.get("case_numbers", [])
        for cn in case_numbers:
            if cn in complaint_data and complaint_data[cn].get("category"):
                cat = complaint_data[cn]["category"]
                if cat not in fines_by_category:
                    fines_by_category[cat] = []
                fines_by_category[cat].append(fine)
                break

        # By action
        ext = s.get("llm_extracted", {})
        action = (ext.get("license_action") or "").lower()
        if "revok" in action:
            action_key = "revoked"
        elif "surrender" in action:
            action_key = "surrendered"
        elif "suspen" in action:
            action_key = "suspended"
        elif "probation" in action:
            action_key = "probation"
        elif "reprimand" in action:
            action_key = "reprimand"
        elif "none" in action or "no action" in action:
            action_key = "no action"
        else:
            action_key = None

        if action_key:
            if action_key not in fines_by_action:
                fines_by_action[action_key] = []
            fines_by_action[action_key].append(fine)

    def calc_fine_stats(values):
        if not values or len(values) < 3:
            return None
        sorted_vals = sorted(values)
        return {
            "n": len(values),
            "median": float(sorted_vals[len(sorted_vals) // 2]),
            "mean": round(sum(values) / len(values), 0),
            "total": sum(values),
            "max": max(values),
        }

    fines_category_stats = [
        {"category": cat, **calc_fine_stats(fines)}
        for cat, fines in fines_by_category.items()
        if calc_fine_stats(fines)
    ]
    fines_category_stats.sort(key=lambda x: -x["median"])

    fines_action_stats = [
        {"action": action, **calc_fine_stats(fines)}
        for action, fines in fines_by_action.items()
        if calc_fine_stats(fines)
    ]

    fines_year_stats = [
        {"year": year, **calc_fine_stats(fines)}
        for year, fines in fines_by_year.items()
        if calc_fine_stats(fines)
    ]
    fines_year_stats.sort(key=lambda x: x["year"])

    # Fine brackets
    all_fines = []
    for s in settlements.find({"llm_extracted.fine_amount": {"$exists": True, "$gt": 0}}):
        all_fines.append(s["llm_extracted"]["fine_amount"])

    fine_brackets = [
        {"label": "Under $1,000", "count": sum(1 for f in all_fines if f < 1000)},
        {"label": "$1,000 - $2,500", "count": sum(1 for f in all_fines if 1000 <= f <= 2500)},
        {"label": "$2,501 - $5,000", "count": sum(1 for f in all_fines if 2501 <= f <= 5000)},
        {"label": "$5,001 - $10,000", "count": sum(1 for f in all_fines if 5001 <= f <= 10000)},
        {"label": "Over $10,000", "count": sum(1 for f in all_fines if f > 10000)},
    ]

    # === CME ANALYSIS ===
    cme_by_category = {}
    cme_by_action = {}
    cme_topics = {}

    for s in settlements.find({"llm_extracted.cme_hours": {"$exists": True, "$gt": 0}}):
        ext = s.get("llm_extracted", {})
        hours = ext.get("cme_hours", 0)
        topic = ext.get("cme_topic", "")

        # Count topics
        if topic:
            topic_lower = topic.lower().strip()
            # Categorize topic
            if "record" in topic_lower or "documentation" in topic_lower:
                topic_cat = "Records/Documentation"
            elif "prescri" in topic_lower or "controlled" in topic_lower or "opioid" in topic_lower:
                topic_cat = "Prescribing/Opioids"
            elif "ethic" in topic_lower or "professionalism" in topic_lower:
                topic_cat = "Ethics"
            elif "boundar" in topic_lower or "sexual" in topic_lower:
                topic_cat = "Boundaries"
            elif "impair" in topic_lower or "substance" in topic_lower:
                topic_cat = "Impairment"
            else:
                topic_cat = "Other"

            cme_topics[topic_cat] = cme_topics.get(topic_cat, 0) + 1

        # By category
        case_numbers = s.get("case_numbers", [])
        for cn in case_numbers:
            if cn in complaint_data and complaint_data[cn].get("category"):
                cat = complaint_data[cn]["category"]
                if cat not in cme_by_category:
                    cme_by_category[cat] = []
                cme_by_category[cat].append(hours)
                break

        # By action
        action = (ext.get("license_action") or "").lower()
        if "revok" in action:
            action_key = "revoked"
        elif "surrender" in action:
            action_key = "surrendered"
        elif "suspen" in action:
            action_key = "suspended"
        elif "probation" in action:
            action_key = "probation"
        elif "reprimand" in action:
            action_key = "reprimand"
        else:
            action_key = None

        if action_key:
            if action_key not in cme_by_action:
                cme_by_action[action_key] = []
            cme_by_action[action_key].append(hours)

    def calc_cme_stats(values):
        if not values or len(values) < 3:
            return None
        sorted_vals = sorted(values)
        return {
            "n": len(values),
            "median": float(sorted_vals[len(sorted_vals) // 2]),
            "mean": round(sum(values) / len(values), 1),
        }

    cme_category_stats = [
        {"category": cat, **calc_cme_stats(hours)}
        for cat, hours in cme_by_category.items()
        if calc_cme_stats(hours)
    ]
    cme_category_stats.sort(key=lambda x: -x["mean"])

    cme_action_stats = [
        {"action": action, **calc_cme_stats(hours)}
        for action, hours in cme_by_action.items()
        if calc_cme_stats(hours)
    ]

    cme_topic_breakdown = [
        {"topic": topic, "count": count}
        for topic, count in sorted(cme_topics.items(), key=lambda x: -x[1])
    ]

    return {
        "resolution_time_by_category": resolution_time_by_category,
        "resolution_time_by_action": resolution_time_by_action,
        "fines_by_category": fines_category_stats,
        "fines_by_action": fines_action_stats,
        "fines_by_year": fines_year_stats,
        "fine_brackets": fine_brackets,
        "total_fines": sum(all_fines),
        "cme_by_category": cme_category_stats,
        "cme_by_action": cme_action_stats,
        "cme_topic_breakdown": cme_topic_breakdown,
    }
