#!/usr/bin/env python3
"""
FastAPI app for exploring Nevada medical malpractice complaints.

Usage:
    uv run uvicorn app:app --reload
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient

load_dotenv()

app = FastAPI(title="Nevada Medical Malpractice Explorer")

# Serve static files (CSS, JS)
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Serve PDFs from pdfs_ocr folder
PDFS_DIR = Path(__file__).parent / "pdfs_ocr"
if PDFS_DIR.exists():
    app.mount("/pdfs", StaticFiles(directory=str(PDFS_DIR)), name="pdfs")

# MongoDB connection
mongo_client: MongoClient = None
db = None
complaints = None


@app.on_event("startup")
def startup():
    global mongo_client, db, complaints
    mongo_uri = os.environ.get("MONGODB_URI")
    if not mongo_uri:
        raise ValueError("MONGODB_URI environment variable is required")
    mongo_client = MongoClient(mongo_uri)
    db = mongo_client["malpractice"]
    complaints = db["complaints"]


@app.on_event("shutdown")
def shutdown():
    if mongo_client:
        mongo_client.close()


@app.get("/")
def home():
    """Serve the main explorer UI."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/stats")
def get_stats():
    """Get overall statistics."""
    settlements_coll = db["settlements"]

    total = complaints.count_documents({})
    with_extraction = complaints.count_documents({"llm_extracted": {"$exists": True}})

    # Count settlements
    total_settlements = settlements_coll.count_documents({})
    settlements_with_extraction = settlements_coll.count_documents({"llm_extracted": {"$exists": True}})

    # Count unique categories
    categories = complaints.distinct("llm_extracted.category")
    categories = [c for c in categories if c]

    # Count unique drugs
    drugs_pipeline = [
        {"$unwind": "$llm_extracted.drugs"},
        {"$group": {"_id": "$llm_extracted.drugs"}},
        {"$count": "count"}
    ]
    drugs_result = list(complaints.aggregate(drugs_pipeline))
    drugs_count = drugs_result[0]["count"] if drugs_result else 0

    return {
        "total": total,
        "with_extraction": with_extraction,
        "settlements": settlements_with_extraction,
        "categories": len(categories),
        "drugs": drugs_count
    }


@app.get("/api/filters")
def get_filters():
    """Get available filter options."""
    categories = complaints.distinct("llm_extracted.category")
    categories = sorted([c for c in categories if c])

    specialties = complaints.distinct("llm_extracted.specialty")
    specialties = sorted([s for s in specialties if s])

    years = complaints.distinct("year")
    years = sorted([y for y in years if y], reverse=True)

    # Get all drugs mentioned
    drugs_pipeline = [
        {"$unwind": "$llm_extracted.drugs"},
        {"$group": {"_id": {"$toLower": "$llm_extracted.drugs"}, "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 100}
    ]
    drugs_result = list(complaints.aggregate(drugs_pipeline))
    drugs = [d["_id"] for d in drugs_result]

    return {
        "categories": categories,
        "specialties": specialties,
        "years": years,
        "drugs": drugs
    }


@app.get("/api/complaints")
def get_complaints(
    category: Optional[str] = None,
    specialty: Optional[str] = None,
    year: Optional[int] = None,
    drug: Optional[str] = None,
    sex: Optional[str] = None,
    has_settlement: Optional[str] = None,
    sort: str = "date_desc",
    skip: int = 0,
    limit: int = Query(default=20, le=100)
):
    """Get complaints with filtering and sorting."""
    settlements_coll = db["settlements"]

    query = {"llm_extracted": {"$exists": True}}

    if category:
        query["llm_extracted.category"] = category
    if specialty:
        query["llm_extracted.specialty"] = specialty
    if year:
        query["year"] = year
    if drug:
        query["llm_extracted.drugs"] = {"$regex": drug, "$options": "i"}
    if sex:
        query["llm_extracted.complainants.sex"] = sex

    # Filter by settlement existence
    if has_settlement:
        # Get all case numbers that have settlements (flatten case_numbers arrays)
        settlement_case_numbers = set()
        for doc in settlements_coll.find({}, {"case_numbers": 1}):
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
        sort_field = "date"
        sort_dir = 1
    elif sort == "year_desc":
        sort_field = "year"
        sort_dir = -1
    elif sort == "year_asc":
        sort_field = "year"
        sort_dir = 1
    elif sort == "respondent":
        sort_field = "respondent"
        sort_dir = 1

    total = complaints.count_documents(query)

    cursor = complaints.find(
        query,
        {"text_content": 0}  # Exclude large text field
    ).sort(sort_field, sort_dir).skip(skip).limit(limit)

    results = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)

    return {"complaints": results, "total": total}


@app.get("/api/random")
def get_random():
    """Get a random complaint."""
    pipeline = [
        {"$match": {"llm_extracted": {"$exists": True}}},
        {"$sample": {"size": 1}},
        {"$project": {"text_content": 0}}
    ]
    result = list(complaints.aggregate(pipeline))
    if result:
        result[0]["_id"] = str(result[0]["_id"])
        return result[0]
    return {"error": "No complaints found"}


@app.get("/api/complaint/{case_number}")
def get_complaint(case_number: str):
    """Get a specific complaint by case number."""
    doc = complaints.find_one({"case_number": case_number})
    if doc:
        doc["_id"] = str(doc["_id"])
        return doc
    return {"error": "Complaint not found"}


@app.get("/api/settlement/{case_number}")
def get_settlement(case_number: str):
    """Get a settlement by case number.

    Settlements now use case_numbers array, so we check if the requested
    case_number is in the array.
    """
    settlements = db["settlements"]
    # Query where case_number is in the case_numbers array
    doc = settlements.find_one({"case_numbers": case_number})
    if doc:
        doc["_id"] = str(doc["_id"])
        # Convert ObjectIds in complaint_ids array to strings
        if doc.get("complaint_ids"):
            doc["complaint_ids"] = [str(cid) for cid in doc["complaint_ids"]]
        # Keep backward compatibility - also set singular fields for UI
        if doc.get("case_numbers"):
            doc["case_number"] = doc["case_numbers"][0]
        return doc
    return None


@app.get("/api/analytics")
def get_analytics():
    """Get aggregate analytics data for charts."""
    settlements_coll = db["settlements"]

    # Fine amounts distribution
    fines_pipeline = [
        {"$match": {"llm_extracted.fine_amount": {"$exists": True, "$ne": None, "$gt": 0}}},
        {"$group": {
            "_id": None,
            "values": {"$push": "$llm_extracted.fine_amount"}
        }}
    ]
    fines_result = list(settlements_coll.aggregate(fines_pipeline))
    fine_values = fines_result[0]["values"] if fines_result else []

    # Investigation costs distribution
    costs_pipeline = [
        {"$match": {"llm_extracted.investigation_costs": {"$exists": True, "$ne": None, "$gt": 0}}},
        {"$group": {
            "_id": None,
            "values": {"$push": "$llm_extracted.investigation_costs"}
        }}
    ]
    costs_result = list(settlements_coll.aggregate(costs_pipeline))
    cost_values = costs_result[0]["values"] if costs_result else []

    # CME hours distribution
    cme_pipeline = [
        {"$match": {"llm_extracted.cme_hours": {"$exists": True, "$ne": None, "$gt": 0}}},
        {"$group": {
            "_id": None,
            "values": {"$push": "$llm_extracted.cme_hours"}
        }}
    ]
    cme_result = list(settlements_coll.aggregate(cme_pipeline))
    cme_values = cme_result[0]["values"] if cme_result else []

    # Probation months distribution
    probation_pipeline = [
        {"$match": {"llm_extracted.probation_months": {"$exists": True, "$ne": None, "$gt": 0}}},
        {"$group": {
            "_id": None,
            "values": {"$push": "$llm_extracted.probation_months"}
        }}
    ]
    probation_result = list(settlements_coll.aggregate(probation_pipeline))
    probation_values = probation_result[0]["values"] if probation_result else []

    # License actions breakdown
    actions_pipeline = [
        {"$match": {"llm_extracted.license_action": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$llm_extracted.license_action", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 15}
    ]
    actions_result = list(settlements_coll.aggregate(actions_pipeline))

    # Specialty breakdown (from complaints)
    specialty_pipeline = [
        {"$match": {"llm_extracted.specialty": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$llm_extracted.specialty", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 15}
    ]
    specialty_result = list(complaints.aggregate(specialty_pipeline))

    # Category breakdown (from complaints)
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
    settlement_summary = {
        "total": settlements_coll.count_documents({}),
        "with_fine": settlements_coll.count_documents({"llm_extracted.fine_amount": {"$gt": 0}}),
        "with_probation": settlements_coll.count_documents({"llm_extracted.probation_months": {"$gt": 0}}),
        "with_cme": settlements_coll.count_documents({"llm_extracted.cme_hours": {"$gt": 0}}),
        "public_reprimand": settlements_coll.count_documents({"llm_extracted.public_reprimand": True}),
        "npdb_report": settlements_coll.count_documents({"llm_extracted.npdb_report": True}),
    }

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
    totals_result = list(settlements_coll.aggregate(totals_pipeline))
    totals = totals_result[0] if totals_result else {}

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
    fines_by_year = list(settlements_coll.aggregate(fines_by_year_pipeline))

    # Calculate years span
    years = [r["_id"] for r in year_result if r["_id"]]
    year_span = max(years) - min(years) + 1 if years else 1

    return {
        "fine_values": fine_values,
        "cost_values": cost_values,
        "cme_values": cme_values,
        "probation_values": probation_values,
        "license_actions": [{"action": r["_id"], "count": r["count"]} for r in actions_result],
        "specialties": [{"specialty": r["_id"], "count": r["count"]} for r in specialty_result],
        "categories": [{"category": r["_id"], "count": r["count"]} for r in category_result],
        "by_year": [{"year": r["_id"], "count": r["count"]} for r in year_result],
        "fines_by_year": [{"year": r["_id"], "total": r["total"], "count": r["count"]} for r in fines_by_year],
        "settlement_summary": settlement_summary,
        "totals": {
            "total_fines": totals.get("total_fines", 0),
            "total_investigation_costs": totals.get("total_investigation_costs", 0),
            "total_cme_hours": totals.get("total_cme_hours", 0),
            "total_probation_months": totals.get("total_probation_months", 0),
            "avg_fine_per_year": round(totals.get("total_fines", 0) / year_span) if year_span else 0,
            "total_complaints": complaints.count_documents({}),
            "year_span": year_span,
            "min_year": min(years) if years else None,
            "max_year": max(years) if years else None,
        }
    }
