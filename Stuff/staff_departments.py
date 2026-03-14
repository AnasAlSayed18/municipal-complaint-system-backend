from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone
from db import get_db

router = APIRouter(prefix="/staff/departments", tags=["staff-departments"])

def now_iso():
    return datetime.now(timezone.utc)

@router.get("")
async def list_departments():
    db = get_db()
    deps = await db["departments"].find({}).sort("created_at", -1).to_list(length=500)

    for d in deps:
        d_id = d.get("department_id")
        d_name = d.get("name")
        query = {"$or": []}
        if d_id:
            query["$or"].append({"department_id": d_id})
            query["$or"].append({"department": d_id})
        if d_name:
            query["$or"].append({"department": d_name})

        if not query["$or"]:
            count = 0
        else:
            count = await db["agent_users"].count_documents(query)

        d["_id"] = str(d["_id"])
        d["members_count"] = count

    return deps


@router.post("")
async def create_department(payload: dict):
    """
    payload:
      - name (required)
      - category_ids (array)
      - active (bool)
    """
    db = get_db()
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    last = await db["departments"].find({}).sort("department_id", -1).limit(1).to_list(length=1)
    if last and last[0].get("department_id", "").startswith("DEP-"):
        try:
            n = int(last[0]["department_id"].split("-")[1])
        except:
            n = 0
    else:
        n = 0
    department_id = f"DEP-{n+1:04d}"

    doc = {
        "department_id": department_id,
        "name": name,
        "category_ids": payload.get("category_ids") or [],
        "active": bool(payload.get("active", True)),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    await db["departments"].insert_one(doc)
    doc["_id"] = str(doc["_id"]) if "_id" in doc else None
    return doc


@router.patch("/{department_id}")
async def update_department(department_id: str, payload: dict):
    db = get_db()
    update = {}

    if "name" in payload:
        update["name"] = payload["name"]
    if "category_ids" in payload:
        update["category_ids"] = payload["category_ids"] or []
    if "active" in payload:
        update["active"] = bool(payload["active"])

    if not update:
        raise HTTPException(status_code=400, detail="no fields to update")

    update["updated_at"] = now_iso()

    res = await db["departments"].find_one_and_update(
        {"department_id": department_id},
        {"$set": update},
        return_document=True
    )

    if not res:
        raise HTTPException(status_code=404, detail="Department not found")

    res["_id"] = str(res["_id"])
    return res


@router.patch("/{department_id}/active")
async def toggle_department_active(department_id: str, payload: dict):
    db = get_db()
    if "active" not in payload:
        raise HTTPException(status_code=400, detail="active is required")

    res = await db["departments"].find_one_and_update(
        {"department_id": department_id},
        {"$set": {"active": bool(payload["active"]), "updated_at": now_iso()}},
        return_document=True
    )

    if not res:
        raise HTTPException(status_code=404, detail="Department not found")

    res["_id"] = str(res["_id"])
    return res
