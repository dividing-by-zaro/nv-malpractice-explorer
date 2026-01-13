"""
Tracked database operations - wraps MongoDB updates with automatic change logging.

IMPORTANT: Always use these functions instead of direct MongoDB updates to ensure
all changes are logged to the change_log collection.

Usage:
    from lib import TrackedDB

    db = get_db()
    tracked = TrackedDB(db, script="my_script.py")

    # Update a single document
    tracked.update_one(
        collection="complaints",
        filter={"case_number": "19-28023-1"},
        update={"$set": {"llm_extracted.specialty": "Neurosurgery"}},
        document_key="19-28023-1",
        reason="Normalize specialty"
    )

    # Update multiple documents
    tracked.update_many(
        collection="complaints",
        filter={"llm_extracted.category": "Old Category"},
        update={"$set": {"llm_extracted.category": "New Category"}},
        reason="Rename category"
    )
"""

import os
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo.database import Database
from pymongo.results import UpdateResult

from .change_logger import ChangeLogger


class TrackedDB:
    """
    Wrapper for MongoDB operations that automatically logs all changes.

    Use this instead of direct db.collection.update_one() calls.
    """

    def __init__(self, db: Database, script: str, user: str = "system"):
        """
        Initialize tracked database wrapper.

        Args:
            db: MongoDB database instance
            script: Name of the script making changes (for audit trail)
            user: User or process making changes (default: "system")
        """
        self.db = db
        self.script = script
        self.user = user
        self.logger = ChangeLogger(db, script=script, user=user)

    def update_one(
        self,
        collection: str,
        filter: dict,
        update: dict,
        document_key: Optional[str] = None,
        reason: str = "",
        upsert: bool = False,
    ) -> UpdateResult:
        """
        Update a single document with change logging.

        Args:
            collection: Collection name
            filter: MongoDB filter to find the document
            update: MongoDB update document (e.g., {"$set": {...}})
            document_key: Human-readable identifier (e.g., case_number).
                         If None, will try to extract from filter or document.
            reason: Explanation of why the change was made
            upsert: If True, insert if document doesn't exist

        Returns:
            pymongo UpdateResult
        """
        coll = self.db[collection]

        # Fetch the document before update to capture old values
        old_doc = coll.find_one(filter)

        # Perform the update
        result = coll.update_one(filter, update, upsert=upsert)

        # If document was modified or inserted, log the change
        if result.modified_count > 0 or result.upserted_id:
            # Fetch the updated document
            if result.upserted_id:
                new_doc = coll.find_one({"_id": result.upserted_id})
                operation = "insert"
            else:
                new_doc = coll.find_one(filter)
                operation = "update"

            # Determine document key
            doc_key = document_key or self._extract_document_key(
                collection, filter, new_doc
            )

            # Compute changes from the update
            changes = self._compute_changes_from_update(old_doc, new_doc, update)

            if changes:
                doc_id = new_doc["_id"] if new_doc else result.upserted_id
                self.logger.log_change(
                    collection=collection,
                    document_id=doc_id,
                    document_key=doc_key,
                    operation=operation,
                    changes=changes,
                    reason=reason,
                )

        return result

    def update_many(
        self,
        collection: str,
        filter: dict,
        update: dict,
        reason: str = "",
    ) -> dict:
        """
        Update multiple documents with change logging.

        Each modified document gets its own change log entry.

        Args:
            collection: Collection name
            filter: MongoDB filter to find documents
            update: MongoDB update document
            reason: Explanation of why the changes were made

        Returns:
            Dict with 'matched_count', 'modified_count', 'logged_count'
        """
        coll = self.db[collection]

        # Fetch all matching documents before update
        old_docs = {doc["_id"]: doc for doc in coll.find(filter)}

        # Perform the update
        result = coll.update_many(filter, update)

        # Log each modified document
        logged_count = 0
        if result.modified_count > 0:
            # Re-fetch to get new values
            new_docs = {doc["_id"]: doc for doc in coll.find({"_id": {"$in": list(old_docs.keys())}})}

            for doc_id, new_doc in new_docs.items():
                old_doc = old_docs.get(doc_id)
                changes = self._compute_changes_from_update(old_doc, new_doc, update)

                if changes:
                    doc_key = self._extract_document_key(collection, {}, new_doc)
                    self.logger.log_change(
                        collection=collection,
                        document_id=doc_id,
                        document_key=doc_key,
                        operation="update",
                        changes=changes,
                        reason=reason,
                    )
                    logged_count += 1

        return {
            "matched_count": result.matched_count,
            "modified_count": result.modified_count,
            "logged_count": logged_count,
        }

    def set_fields(
        self,
        collection: str,
        filter: dict,
        fields: dict,
        document_key: Optional[str] = None,
        reason: str = "",
        upsert: bool = False,
    ) -> UpdateResult:
        """
        Convenience method: set specific fields on a document.

        Args:
            collection: Collection name
            filter: MongoDB filter
            fields: Dict of field paths to values (e.g., {"llm_extracted.specialty": "Neurosurgery"})
            document_key: Human-readable identifier
            reason: Explanation
            upsert: Insert if not exists

        Returns:
            pymongo UpdateResult
        """
        return self.update_one(
            collection=collection,
            filter=filter,
            update={"$set": fields},
            document_key=document_key,
            reason=reason,
            upsert=upsert,
        )

    def _extract_document_key(
        self, collection: str, filter: dict, doc: Optional[dict]
    ) -> str:
        """Extract a human-readable key from filter or document."""
        # Try common key fields based on collection
        key_fields = {
            "complaints": "case_number",
            "settlements": "pdf_url",
            "license_only_filings": "pdf_url",
        }

        key_field = key_fields.get(collection)

        # Try filter first
        if key_field and key_field in filter:
            return str(filter[key_field])

        # Try document
        if doc and key_field and key_field in doc:
            return str(doc[key_field])

        # Fallback to _id
        if doc and "_id" in doc:
            return str(doc["_id"])

        return "unknown"

    def _compute_changes_from_update(
        self, old_doc: Optional[dict], new_doc: Optional[dict], update: dict
    ) -> list[dict]:
        """
        Compute field-level changes based on the update operation.

        Args:
            old_doc: Document before update (None if insert)
            new_doc: Document after update
            update: The MongoDB update document

        Returns:
            List of {"field": str, "old_value": any, "new_value": any}
        """
        changes = []

        if not new_doc:
            return changes

        # Handle $set operations
        if "$set" in update:
            for field_path, new_value in update["$set"].items():
                old_value = _get_nested(old_doc, field_path) if old_doc else None
                actual_new_value = _get_nested(new_doc, field_path)

                if old_value != actual_new_value:
                    changes.append({
                        "field": field_path,
                        "old_value": old_value,
                        "new_value": actual_new_value,
                    })

        # Handle $unset operations
        if "$unset" in update:
            for field_path in update["$unset"]:
                old_value = _get_nested(old_doc, field_path) if old_doc else None
                if old_value is not None:
                    changes.append({
                        "field": field_path,
                        "old_value": old_value,
                        "new_value": None,
                    })

        # Handle $push operations
        if "$push" in update:
            for field_path, pushed_value in update["$push"].items():
                old_value = _get_nested(old_doc, field_path) if old_doc else None
                new_value = _get_nested(new_doc, field_path)
                changes.append({
                    "field": field_path,
                    "old_value": old_value,
                    "new_value": new_value,
                })

        # Handle $inc operations
        if "$inc" in update:
            for field_path, inc_value in update["$inc"].items():
                old_value = _get_nested(old_doc, field_path) if old_doc else 0
                new_value = _get_nested(new_doc, field_path)
                if old_value != new_value:
                    changes.append({
                        "field": field_path,
                        "old_value": old_value,
                        "new_value": new_value,
                    })

        # Handle $pull operations (remove from array)
        if "$pull" in update:
            for field_path, pull_condition in update["$pull"].items():
                old_value = _get_nested(old_doc, field_path) if old_doc else None
                new_value = _get_nested(new_doc, field_path)
                if old_value != new_value:
                    changes.append({
                        "field": field_path,
                        "old_value": old_value,
                        "new_value": new_value,
                    })

        return changes


def _get_nested(doc: Optional[dict], field_path: str) -> Any:
    """Get a nested field value using dot notation."""
    if doc is None:
        return None

    parts = field_path.split(".")
    value = doc

    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None

    return value


# Convenience function for quick one-off updates
def tracked_update_one(
    db: Database,
    collection: str,
    filter: dict,
    update: dict,
    script: str,
    document_key: Optional[str] = None,
    reason: str = "",
    upsert: bool = False,
) -> UpdateResult:
    """
    Standalone function for tracked updates without instantiating TrackedDB.

    Args:
        db: MongoDB database
        collection: Collection name
        filter: MongoDB filter
        update: MongoDB update document
        script: Script name for audit trail
        document_key: Human-readable identifier
        reason: Explanation
        upsert: Insert if not exists

    Returns:
        pymongo UpdateResult
    """
    tracked = TrackedDB(db, script=script)
    return tracked.update_one(
        collection=collection,
        filter=filter,
        update=update,
        document_key=document_key,
        reason=reason,
        upsert=upsert,
    )
