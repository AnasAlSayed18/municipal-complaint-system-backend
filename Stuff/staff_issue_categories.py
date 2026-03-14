from fastapi import APIRouter
from db import get_db

router = APIRouter()


@router.get("/issue-categories")
async def list_issue_categories(active: str | None = None):
    db = get_db()
    query = {}
    if active is not None:
        query["active"] = (active.lower() == "true")
    cats = await db["issue_categories"].find(query).sort("created_at", -1).to_list(length=500)
    for c in cats:
        c["_id"] = str(c.get("_id"))
    return cats


@router.get("/staff/issue-categories")
async def list_issue_categories_staff(active: str | None = None):
    return await list_issue_categories(active)
