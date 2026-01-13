"""
Change logging utility for tracking field-level modifications to MongoDB documents.

Usage:
    from analysis.scripts.change_logger import ChangeLogger

    logger = ChangeLogger(db, script="normalize_specialties.py")

    # Log a single field update
    logger.log_field_update(
        collection="complaints",
        document_id=doc["_id"],
        document_key=doc["case_number"],
        field="llm_extracted.specialty",
        old_value="Neurological Surgery",
        new_value="Neurosurgery",
        reason="Normalize specialty to match NPDB categories"
    )

    # Log multiple field changes at once
    logger.log_change(
        collection="complaints",
        document_id=doc["_id"],
        document_key=doc["case_number"],
        operation="update",
        changes=[
            {"field": "llm_extracted.specialty", "old_value": "Neurological Surgery", "new_value": "Neurosurgery"},
            {"field": "llm_extracted.specialty_original", "old_value": None, "new_value": "Neurological Surgery"},
        ],
        reason="Normalize specialty to match NPDB categories"
    )
"""

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo.database import Database


class ChangeLogger:
    """Logs field-level changes to a change_log collection."""

    COLLECTION_NAME = "change_log"

    def __init__(self, db: Database, script: str, user: str = "system"):
        """
        Initialize the change logger.

        Args:
            db: MongoDB database instance
            script: Name of the script making changes (e.g., "normalize_specialties.py")
            user: User or process making the change (default: "system")
        """
        self.db = db
        self.script = script
        self.user = user
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        """Create indexes on change_log collection if they don't exist."""
        collection = self.db[self.COLLECTION_NAME]

        # Get existing index keys (not just names) to avoid conflicts
        existing_indexes = {}
        for idx in collection.list_indexes():
            # Convert key dict to tuple for comparison
            key_tuple = tuple(idx["key"].items())
            existing_indexes[key_tuple] = idx["name"]

        # Compound index on collection + document_id
        compound_key = (("collection", 1), ("document_id", 1))
        if compound_key not in existing_indexes:
            collection.create_index(
                [("collection", 1), ("document_id", 1)],
                name="collection_document_id",
            )

        # Single field indexes
        timestamp_key = (("timestamp", 1),)
        if timestamp_key not in existing_indexes:
            collection.create_index("timestamp", name="timestamp")

        script_key = (("script", 1),)
        if script_key not in existing_indexes:
            collection.create_index("script", name="script")

    def log_change(
        self,
        collection: str,
        document_id: ObjectId,
        document_key: str,
        operation: str,
        changes: list[dict],
        reason: str,
    ) -> ObjectId:
        """
        Log a change with multiple field modifications.

        Args:
            collection: Name of the collection (e.g., "complaints")
            document_id: _id of the document being changed
            document_key: Human-readable identifier (e.g., case_number)
            operation: Type of operation ("update", "insert", "delete")
            changes: List of {"field": str, "old_value": any, "new_value": any}
            reason: Explanation of why the change was made

        Returns:
            ObjectId of the inserted log entry
        """
        log_entry = {
            "timestamp": datetime.now(timezone.utc),
            "collection": collection,
            "document_id": document_id,
            "document_key": document_key,
            "operation": operation,
            "script": self.script,
            "reason": reason,
            "changes": changes,
            "user": self.user,
        }

        result = self.db[self.COLLECTION_NAME].insert_one(log_entry)
        return result.inserted_id

    def log_field_update(
        self,
        collection: str,
        document_id: ObjectId,
        document_key: str,
        field: str,
        old_value: Any,
        new_value: Any,
        reason: str,
    ) -> ObjectId:
        """
        Convenience method for logging a single field update.

        Args:
            collection: Name of the collection
            document_id: _id of the document
            document_key: Human-readable identifier
            field: Field path (e.g., "llm_extracted.specialty")
            old_value: Previous value
            new_value: New value
            reason: Explanation of why

        Returns:
            ObjectId of the inserted log entry
        """
        return self.log_change(
            collection=collection,
            document_id=document_id,
            document_key=document_key,
            operation="update",
            changes=[{"field": field, "old_value": old_value, "new_value": new_value}],
            reason=reason,
        )

    @staticmethod
    def compute_diff(
        old_doc: dict, new_doc: dict, fields: list[str]
    ) -> list[dict]:
        """
        Compute field-level differences between two documents.

        Args:
            old_doc: Original document
            new_doc: Modified document
            fields: List of field paths to compare (supports dot notation)

        Returns:
            List of {"field": str, "old_value": any, "new_value": any} for changed fields
        """
        changes = []

        for field in fields:
            old_value = _get_nested(old_doc, field)
            new_value = _get_nested(new_doc, field)

            if old_value != new_value:
                changes.append({
                    "field": field,
                    "old_value": old_value,
                    "new_value": new_value,
                })

        return changes


def _get_nested(doc: dict, field_path: str) -> Any:
    """
    Get a nested field value using dot notation.

    Args:
        doc: Document to extract from
        field_path: Dot-separated path (e.g., "llm_extracted.specialty")

    Returns:
        Value at path, or None if not found
    """
    parts = field_path.split(".")
    value = doc

    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None

    return value


def _set_nested(doc: dict, field_path: str, value: Any) -> None:
    """
    Set a nested field value using dot notation.

    Args:
        doc: Document to modify
        field_path: Dot-separated path (e.g., "llm_extracted.specialty")
        value: Value to set
    """
    parts = field_path.split(".")

    for part in parts[:-1]:
        if part not in doc:
            doc[part] = {}
        doc = doc[part]

    doc[parts[-1]] = value


# Convenience function for scripts that don't need the full class
def get_document_history(db: Database, collection: str, document_id: ObjectId) -> list[dict]:
    """
    Get all change log entries for a specific document.

    Args:
        db: MongoDB database instance
        collection: Collection name
        document_id: Document _id

    Returns:
        List of change log entries, sorted by timestamp descending
    """
    return list(
        db[ChangeLogger.COLLECTION_NAME]
        .find({"collection": collection, "document_id": document_id})
        .sort("timestamp", -1)
    )


def get_changes_by_script(db: Database, script: str) -> list[dict]:
    """
    Get all changes made by a specific script.

    Args:
        db: MongoDB database instance
        script: Script name

    Returns:
        List of change log entries
    """
    return list(
        db[ChangeLogger.COLLECTION_NAME]
        .find({"script": script})
        .sort("timestamp", -1)
    )
