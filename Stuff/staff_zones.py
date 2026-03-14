from fastapi import APIRouter
from db import get_db

router = APIRouter(prefix="/staff/zones", tags=["staff-zones"])

@router.get("")
async def list_zones(active: str | None = None):
    db = get_db()
    q = {}
    if active is not None:
        q["active"] = (active.lower() == "true")

    items = await db["zones"].find(q).sort("_id", 1).to_list(length=2000)
    return items
