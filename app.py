#!/usr/bin/env python3
"""
FastAPI app for exploring Nevada medical malpractice complaints.

Usage:
    uv run uvicorn app:app --reload
"""

import os
from contextlib import asynccontextmanager
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
    settlements: int
    categories: int
    drugs: int


class FiltersResponse(BaseModel):
    """Available filter options."""
    categories: list[str]
    specialties: list[str]
    years: list[int]
    drugs: list[str]


class Complainant(BaseModel):
    """Patient demographic info."""
    age: Optional[int] = None
    sex: Optional[str] = None


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
    """Settlement document."""
    id: Optional[str] = None
    case_number: Optional[str] = None
    case_numbers: list[str] = []
    complaint_ids: list[str] = []
    respondent: str
    date: str
    year: int
    type: str
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


class AnalyticsResponse(BaseModel):
    """Aggregate analytics data."""
    fine_values: list[float]
    cost_values: list[float]
    cme_values: list[int]
    probation_values: list[int]
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
        settlements=settlements_with_extraction,
        categories=len(categories),
        drugs=drugs_count
    )


@app.get("/api/filters", response_model=FiltersResponse)
def get_filters(db: DB):
    """Get available filter options."""
    complaints = db["complaints"]

    categories = complaints.distinct("llm_extracted.category")
    categories = sorted([c for c in categories if c])

    specialties = complaints.distinct("llm_extracted.specialty")
    specialties = sorted([s for s in specialties if s])

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

    return FiltersResponse(
        categories=categories,
        specialties=specialties,
        years=years,
        drugs=drugs
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
        if len(specialties) == 1:
            query["llm_extracted.specialty"] = specialties[0]
        else:
            query["llm_extracted.specialty"] = {"$in": specialties}

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

    # Filter by settlement existence
    if has_settlement:
        settlement_case_numbers = set()
        for doc in settlements.find({}, {"case_numbers": 1}):
            case_nums = doc.get("case_numbers", [])
            settlement_case_numbers.update(case_nums)

        if has_settlement == "yes":
            query["case_number"] = {"$in": list(settlement_case_numbers)}
        elif has_settlement == "no":
            query["case_number"] = {"$nin": list(settlement_case_numbers)}

    # Sorting
    sort_field = "date"
    sort_dir = -1
    if sort == "date_asc":
        sort_field, sort_dir = "date", 1
    elif sort == "year_desc":
        sort_field, sort_dir = "year", -1
    elif sort == "year_asc":
        sort_field, sort_dir = "year", 1
    elif sort == "respondent":
        sort_field, sort_dir = "respondent", 1

    total = complaints.count_documents(query)

    cursor = complaints.find(
        query,
        {"text_content": 0}
    ).sort(sort_field, sort_dir).skip(skip).limit(limit)

    # Build a lookup of settlement data by case number
    settlement_lookup = {}
    for doc in settlements.find({"llm_extracted": {"$exists": True}}):
        ext = doc.get("llm_extracted", {})
        summary = {
            "license_action": ext.get("license_action"),
            "fine_amount": ext.get("fine_amount"),
            "investigation_costs": ext.get("investigation_costs"),
            "cme_hours": ext.get("cme_hours"),
            "probation_months": ext.get("probation_months"),
        }
        for cn in doc.get("case_numbers", []):
            settlement_lookup[cn] = summary

    results = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        # Attach settlement summary if available
        case_num = doc.get("case_number")
        if case_num in settlement_lookup:
            doc["settlement_summary"] = settlement_lookup[case_num]
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
        # Check for settlement
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
        license_actions=[LicenseActionCount(action=r["_id"], count=r["count"]) for r in actions_result],
        specialties=[SpecialtyCount(specialty=r["_id"], count=r["count"]) for r in specialty_result],
        categories=[CategoryCount(category=r["_id"], count=r["count"]) for r in category_result],
        by_year=[YearCount(year=r["_id"], count=r["count"]) for r in year_result],
        fines_by_year=[FinesByYear(year=r["_id"], total=r["total"], count=r["count"]) for r in fines_by_year],
        settlement_summary=settlement_summary,
        totals=totals,
    )
