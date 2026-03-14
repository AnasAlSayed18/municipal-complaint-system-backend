from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone
from typing import Optional
from db import get_db

router = APIRouter(prefix="/staff/assignment", tags=["staff-assignment"])

def now_iso():
    return datetime.now(timezone.utc)

def clean_doc(doc):
    if not doc:
        return doc
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

async def inc_agent_open_tasks(db, agent_id: str, delta: int):
    """delta can be +1 or -1. Never allow negative open_tasks."""
    if not agent_id or delta == 0:
        return

    # ensure workload exists
    ag = await db["agent_users"].find_one({"agent_id": agent_id})
    if not ag:
        return

    current = int(((ag.get("workload") or {}).get("open_tasks")) or 0)
    next_val = current + int(delta)
    if next_val < 0:
        next_val = 0

    await db["agent_users"].update_one(
        {"agent_id": agent_id},
        {
            "$set": {
                "workload.open_tasks": next_val,
                "workload.last_assigned_at": now_iso(),
                "updated_at": now_iso(),
            }
        },
    )

@router.get("/teams")
async def list_teams():
    db = get_db()
    teams = await db["departments"].find({}).sort("created_at", -1).to_list(length=500)
    teams = [clean_doc(t) for t in teams]

    dept_ids = [t.get("department_id") for t in teams if t.get("department_id")]
    if dept_ids:
        pipeline = [
            {"$match": {"department_id": {"$in": dept_ids}}},
            {"$group": {"_id": "$department_id", "count": {"$sum": 1}}}
        ]
        counts = await db["agent_users"].aggregate(pipeline).to_list(length=500)
        count_map = {c["_id"]: c["count"] for c in counts}
        for t in teams:
            t["agents_count"] = int(count_map.get(t.get("department_id"), 0))
    else:
        for t in teams:
            t["agents_count"] = 0

    return teams

@router.get("/requests")
async def list_requests_for_assignment(
    department_id: str,
    unassigned: Optional[bool] = False,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    category: Optional[str] = None,
):
    db = get_db()
    if not department_id:
        raise HTTPException(status_code=400, detail="department_id is required")

    query = {}

    if unassigned:
        query["$or"] = [
            {"department_id": {"$exists": False}},
            {"department_id": None},
            {"department_id": ""},
        ]
    else:
        query["department_id"] = department_id

    if status:
        query["status"] = status
    if priority:
        query["priority"] = priority
    if category:
        query["category"] = category

    items = await db["requests"].find(query).sort("created_at", -1).limit(500).to_list(length=500)
    for it in items:
        clean_doc(it)
    return items

@router.patch("/requests/{request_id}/assign_team")
async def assign_request_to_team(request_id: str, payload: dict):
    """
    payload: { department_id }
    """
    db = get_db()

    department_id = payload.get("department_id")
    if not department_id:
        raise HTTPException(status_code=400, detail="department_id is required")

    dep = await db["departments"].find_one({"department_id": department_id})
    if not dep:
        raise HTTPException(status_code=400, detail="department_id not found")

    req = await db["requests"].find_one({"request_id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    update = {"department_id": department_id, "updated_at": now_iso()}

    prev_agent = req.get("agent_id")
    prev_dept = req.get("department_id")
    if prev_agent and str(prev_dept or "") != str(department_id):
        update["agent_id"] = None
        # decrement old agent open_tasks
        await inc_agent_open_tasks(db, str(prev_agent), -1)

    evt = {"type": "ASSIGN_TEAM", "at": now_iso(), "department_id": department_id}

    await db["requests"].update_one(
        {"request_id": request_id},
        {"$set": update, "$push": {"timeline": evt}},
    )

    updated = await db["requests"].find_one({"request_id": request_id})
    clean_doc(updated)
    return updated

@router.patch("/requests/{request_id}/assign_agent")
async def assign_request_to_agent(request_id: str, payload: dict):
    """
    payload: { department_id, agent_id }
    - Handles load changes automatically:
      - None -> A : A +1
      - A -> B : A -1, B +1
      - A -> None : A -1  (use unassign_agent endpoint below)
    """
    db = get_db()

    department_id = payload.get("department_id")
    agent_id = payload.get("agent_id")

    if not department_id or not agent_id:
        raise HTTPException(status_code=400, detail="department_id and agent_id are required")

    dep = await db["departments"].find_one({"department_id": department_id})
    if not dep:
        raise HTTPException(status_code=400, detail="department_id not found")

    ag = await db["agent_users"].find_one({"agent_id": agent_id})
    if not ag:
        raise HTTPException(status_code=400, detail="agent_id not found")

    if str(ag.get("department_id")) != str(department_id):
        raise HTTPException(status_code=400, detail="Agent is not in this department")

    req = await db["requests"].find_one({"request_id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    if str(req.get("department_id") or "") != str(department_id):
        raise HTTPException(status_code=400, detail="Assign request to team first")

    prev_agent = req.get("agent_id")

    update = {"agent_id": agent_id, "updated_at": now_iso()}

    if str(req.get("status") or "").upper() in ["NEW", "TRIAGED", ""]:
        update["status"] = "ASSIGNED"

    evt = {
        "type": "ASSIGN_AGENT",
        "at": now_iso(),
        "department_id": department_id,
        "agent_id": agent_id,
        "prev_agent_id": prev_agent or None,
    }

    await db["requests"].update_one(
        {"request_id": request_id},
        {"$set": update, "$push": {"timeline": evt}},
    )

    # ===== workload logic =====
    # prev None -> new: +1
    # prev A -> new B: A -1, B +1
    if prev_agent and str(prev_agent) != str(agent_id):
        await inc_agent_open_tasks(db, str(prev_agent), -1)
        await inc_agent_open_tasks(db, str(agent_id), +1)
    elif (not prev_agent) and agent_id:
        await inc_agent_open_tasks(db, str(agent_id), +1)
    # if same agent, no change

    updated = await db["requests"].find_one({"request_id": request_id})
    clean_doc(updated)
    return updated

@router.patch("/requests/{request_id}/unassign_agent")
async def unassign_agent(request_id: str):
    """
    Removes agent_id from request and decrements old agent open_tasks by 1.
    """
    db = get_db()

    req = await db["requests"].find_one({"request_id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    prev_agent = req.get("agent_id")
    if not prev_agent:
        # nothing to do
        updated = req
        clean_doc(updated)
        return updated

    await db["requests"].update_one(
        {"request_id": request_id},
        {
            "$set": {"agent_id": None, "updated_at": now_iso()},
            "$push": {"timeline": {"type": "UNASSIGN_AGENT", "at": now_iso(), "prev_agent_id": prev_agent}},
        },
    )

    await inc_agent_open_tasks(db, str(prev_agent), -1)

    updated = await db["requests"].find_one({"request_id": request_id})
    clean_doc(updated)
    return updated

@router.patch("/requests/{request_id}/unassign_team")
async def unassign_team(request_id: str):
    """
    Cancels department assignment:
    - removes department_id
    - removes agent_id if exists
    - if agent removed => decrement open_tasks
    """
    db = get_db()

    req = await db["requests"].find_one({"request_id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    prev_agent = req.get("agent_id")
    prev_dept = req.get("department_id")

    update_set = {
        "department_id": None,
        "agent_id": None,   # important
        "updated_at": now_iso(),
    }

    await db["requests"].update_one(
        {"request_id": request_id},
        {
            "$set": update_set,
            "$push": {"timeline": {"type": "UNASSIGN_TEAM", "at": now_iso(), "prev_department_id": prev_dept, "prev_agent_id": prev_agent}},
        },
    )

    if prev_agent:
        await inc_agent_open_tasks(db, str(prev_agent), -1)

    updated = await db["requests"].find_one({"request_id": request_id})
    clean_doc(updated)
    return updated
