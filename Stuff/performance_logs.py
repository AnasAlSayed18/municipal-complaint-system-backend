from fastapi import APIRouter, HTTPException
from typing import Optional, Any, Dict
from datetime import datetime
from db import get_db
from bson import ObjectId

router = APIRouter(prefix="/performance-logs", tags=["performance-logs"])


def _safe_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # supports: 2026-01-25T22:49:35.670+00:00  OR  2026-01-25T22:49:35.670000
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc["_id"] = str(doc.get("_id")) if doc.get("_id") else None

    # timestamp -> ISO string
    ts = doc.get("timestamp")
    if isinstance(ts, datetime):
        doc["timestamp"] = ts.isoformat()

    return doc


@router.get("")
async def list_performance_logs(
    q: Optional[str] = None,
    event_name: Optional[str] = None,
    user_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    limit: int = 25,
):
    try:
        db = get_db()

        # guardrails
        page = max(page, 1)
        limit = min(max(limit, 1), 200)
        skip = (page - 1) * limit

        query: Dict[str, Any] = {}

        if event_name:
            query["event_name"] = event_name

        if user_id:
            query["user_id"] = user_id

        df = _safe_dt(date_from)
        dt = _safe_dt(date_to)
        if df or dt:
            query["timestamp"] = {}
            if df:
                query["timestamp"]["$gte"] = df
            if dt:
                query["timestamp"]["$lte"] = dt

        # search
        if q:
            query["$or"] = [
                {"event_name": {"$regex": q, "$options": "i"}},
                {"user_id": {"$regex": q, "$options": "i"}},
                {"metadata": {"$regex": q, "$options": "i"}},  # إذا metadata string
            ]

        total = await db["performance_logs"].count_documents(query)

        cursor = (
            db["performance_logs"]
            .find(query)
            .sort("timestamp", -1)
            .skip(skip)
            .limit(limit)
        )

        items = await cursor.to_list(length=limit)
        items = [_serialize(x) for x in items]

        return {
            "items": items,
            "page": page,
            "limit": limit,
            "total": total,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading performance logs: {str(e)}")
