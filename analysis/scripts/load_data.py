"""
Load malpractice data from MongoDB into pandas DataFrames.

Usage:
    from analysis.scripts.load_data import load_complaints, load_settlements, load_all

    # Load individual collections
    complaints = load_complaints()
    settlements = load_settlements()

    # Or load everything at once
    data = load_all()
    data['complaints']  # DataFrame
    data['settlements']  # DataFrame
"""

import os
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from pymongo import MongoClient

# Load environment variables
load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/malpractice")


def get_client() -> MongoClient:
    """Get MongoDB client."""
    return MongoClient(MONGODB_URI)


def get_db():
    """Get the malpractice database."""
    client = get_client()
    # Extract database name from URI or use default
    # Handle URIs like mongodb+srv://user:pass@cluster.mongodb.net/dbname?options
    from urllib.parse import urlparse

    parsed = urlparse(MONGODB_URI)
    db_name = parsed.path.lstrip("/").split("?")[0]
    if not db_name:
        db_name = "malpractice"
    return client[db_name]


def load_complaints(flatten_llm: bool = True) -> pd.DataFrame:
    """
    Load complaints collection into a DataFrame.

    Args:
        flatten_llm: If True, flatten llm_extracted fields into top-level columns

    Returns:
        DataFrame with complaint data
    """
    db = get_db()
    cursor = db.complaints.find({})
    df = pd.DataFrame(list(cursor))

    if df.empty:
        return df

    # Convert ObjectId to string
    if "_id" in df.columns:
        df["_id"] = df["_id"].astype(str)

    # Flatten llm_extracted fields
    if flatten_llm and "llm_extracted" in df.columns:
        llm_df = pd.json_normalize(df["llm_extracted"].dropna())
        llm_df.index = df["llm_extracted"].dropna().index

        # Prefix columns to avoid conflicts
        llm_df.columns = [f"llm_{col}" for col in llm_df.columns]

        # Join back
        df = df.join(llm_df)

    # Parse date column
    if "date" in df.columns:
        df["date_parsed"] = pd.to_datetime(df["date"], format="%m/%d/%Y", errors="coerce")

    return df


def load_settlements(flatten_llm: bool = True) -> pd.DataFrame:
    """
    Load settlements collection into a DataFrame.

    Args:
        flatten_llm: If True, flatten llm_extracted fields into top-level columns

    Returns:
        DataFrame with settlement/resolution data
    """
    db = get_db()
    cursor = db.settlements.find({})
    df = pd.DataFrame(list(cursor))

    if df.empty:
        return df

    # Convert ObjectId to string
    if "_id" in df.columns:
        df["_id"] = df["_id"].astype(str)

    # Convert complaint_ids list of ObjectIds to strings
    if "complaint_ids" in df.columns:
        df["complaint_ids"] = df["complaint_ids"].apply(
            lambda x: [str(i) for i in x] if isinstance(x, list) else x
        )

    # Flatten llm_extracted fields
    if flatten_llm and "llm_extracted" in df.columns:
        llm_df = pd.json_normalize(df["llm_extracted"].dropna())
        llm_df.index = df["llm_extracted"].dropna().index
        llm_df.columns = [f"llm_{col}" for col in llm_df.columns]
        df = df.join(llm_df)

    # Parse date column
    if "date" in df.columns:
        df["date_parsed"] = pd.to_datetime(df["date"], format="%m/%d/%Y", errors="coerce")

    return df


def load_license_only() -> pd.DataFrame:
    """
    Load license_only_filings collection into a DataFrame.

    Returns:
        DataFrame with license-only filing data
    """
    db = get_db()
    cursor = db.license_only_filings.find({})
    df = pd.DataFrame(list(cursor))

    if df.empty:
        return df

    if "_id" in df.columns:
        df["_id"] = df["_id"].astype(str)

    if "date" in df.columns:
        df["date_parsed"] = pd.to_datetime(df["date"], format="%m/%d/%Y", errors="coerce")

    return df


def load_all(flatten_llm: bool = True) -> dict[str, pd.DataFrame]:
    """
    Load all collections into DataFrames.

    Returns:
        Dictionary with keys: 'complaints', 'settlements', 'license_only'
    """
    return {
        "complaints": load_complaints(flatten_llm=flatten_llm),
        "settlements": load_settlements(flatten_llm=flatten_llm),
        "license_only": load_license_only(),
    }


def load_merged():
    """
    Load complaints with their settlements merged.

    Returns:
        DataFrame with complaints joined to their resolution data
    """
    complaints = load_complaints(flatten_llm=True)
    settlements = load_settlements(flatten_llm=True)

    if complaints.empty:
        return complaints

    # Create a lookup from case_number to settlement
    # settlements have case_numbers[] array
    settlement_lookup = {}
    for _, row in settlements.iterrows():
        case_numbers = row.get("case_numbers", [])
        if isinstance(case_numbers, list):
            for cn in case_numbers:
                settlement_lookup[cn] = row

    # Merge settlement data onto complaints
    settlement_cols = [
        "llm_license_action",
        "llm_fine_amount",
        "llm_investigation_costs",
        "llm_probation_months",
        "llm_cme_hours",
        "llm_cme_topic",
        "resolution_outcome",
    ]

    for col in settlement_cols:
        complaints[f"settlement_{col.replace('llm_', '')}"] = None

    complaints["settlement_date"] = None
    complaints["has_settlement"] = False

    for idx, row in complaints.iterrows():
        cn = row.get("case_number")
        if cn in settlement_lookup:
            s = settlement_lookup[cn]
            complaints.at[idx, "has_settlement"] = True
            complaints.at[idx, "settlement_date"] = s.get("date")
            for col in settlement_cols:
                if col in s.index:
                    new_col = f"settlement_{col.replace('llm_', '')}"
                    complaints.at[idx, new_col] = s[col]

    # Calculate days to resolution
    complaints["settlement_date_parsed"] = pd.to_datetime(
        complaints["settlement_date"], format="%m/%d/%Y", errors="coerce"
    )
    complaints["days_to_resolution"] = (
        complaints["settlement_date_parsed"] - complaints["date_parsed"]
    ).dt.days

    return complaints


# Quick test when run directly
if __name__ == "__main__":
    print("Loading data from MongoDB...")

    data = load_all()
    for name, df in data.items():
        print(f"\n{name}: {len(df)} records")
        if not df.empty:
            print(f"  Columns: {list(df.columns)[:8]}...")

    print("\n--- Merged view ---")
    merged = load_merged()
    print(f"Merged complaints: {len(merged)} records")
    print(f"With settlement: {merged['has_settlement'].sum()}")
