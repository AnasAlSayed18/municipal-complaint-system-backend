from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone
from db import get_db

router = APIRouter(prefix="/staff/agents", tags=["staff-agents"])

def now_iso():
    return datetime.now(timezone.utc)

def dep_exists_query(department_id: str):
    return {"$or": [{"department_id": department_id}, {"_id": department_id}]}

@router.get("")
async def list_agents(department_id: str | None = None, active: str | None = None):
    db = get_db()
    query = {}

    if department_id:
        query["department_id"] = department_id
    if active is not None:
        query["active"] = (active.lower() == "true")

    agents = await db["agent_users"].find(query).sort("created_at", -1).to_list(length=500)
    for a in agents:
        a["_id"] = str(a["_id"])
        if "password" in a:
            a["password"] = None
    return agents


@router.post("")
async def create_agent(payload: dict):
    db = get_db()

    user_name = payload.get("user_name")
    password = payload.get("password")
    name = payload.get("name")
    department_id = payload.get("department_id")

    if not user_name or not password or not name or not department_id:
        raise HTTPException(status_code=400, detail="Missing required fields")

    # validate department exists (department_id OR _id)
    dep = await db["departments"].find_one(dep_exists_query(department_id))
    if not dep:
        raise HTTPException(status_code=400, detail="department_id not found")

    # unique username
    exists = await db["agent_users"].find_one({"user_name": user_name})
    if exists:
        raise HTTPException(status_code=400, detail="user_name already exists")

    # generate agent_id (string)
    last = await db["agent_users"].find({}).sort("agent_id", -1).limit(1).to_list(length=1)
    n = 0
    if last and isinstance(last[0].get("agent_id"), str) and last[0]["agent_id"].startswith("AG-"):
        try:
            n = int(last[0]["agent_id"].split("-")[1])
        except:
            n = 0
    agent_id = f"AG-{n+1:04d}"

    # zones support
    coverage = payload.get("coverage") or {}
    zone_ids = coverage.get("zone_ids") or []
    if not isinstance(zone_ids, list):
        raise HTTPException(status_code=400, detail="coverage.zone_ids must be a list")

    doc = {
        "agent_id": agent_id,
        "agent_code": payload.get("agent_code", agent_id),
        "user_name": user_name,
        "password": password,  # later: hash
        "name": name,
        "department_id": department_id,
        "account_type": payload.get("account_type", "agent"),
        "active": bool(payload.get("active", True)),
        "roles": payload.get("roles", ["agent"]),
        "skills": payload.get("skills", []),

        "coverage": {
            "zone_ids": zone_ids,
            "geo_fence": coverage.get("geo_fence", None),
        },

        "schedule": payload.get("schedule", {"shifts": [], "on_call": False}),
        "contacts": payload.get("contacts", {"phone": "", "email": ""}),
        "workload": payload.get("workload", {"open_tasks": 0, "last_assigned_at": None}),

        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    res = await db["agent_users"].insert_one(doc)
    saved = await db["agent_users"].find_one({"_id": res.inserted_id})

    saved["_id"] = str(saved["_id"])
    if "password" in saved:
        saved["password"] = None
    return saved


@router.patch("/{agent_id}")
async def update_agent(agent_id: str, payload: dict):
    db = get_db()
    update = {}

    # required-ish fields you allow updating
    if "name" in payload:
        update["name"] = payload["name"]

    if "department_id" in payload:
        dep = await db["departments"].find_one(dep_exists_query(payload["department_id"]))
        if not dep:
            raise HTTPException(status_code=400, detail="department_id not found")
        update["department_id"] = payload["department_id"]

    if "user_name" in payload:
        exists = await db["agent_users"].find_one({"user_name": payload["user_name"], "agent_id": {"$ne": agent_id}})
        if exists:
            raise HTTPException(status_code=400, detail="user_name already exists")
        update["user_name"] = payload["user_name"]

    if "password" in payload and payload["password"]:
        update["password"] = payload["password"]

    if "active" in payload:
        update["active"] = bool(payload["active"])

    # optional blocks
    for f in ["agent_code", "skills", "schedule", "contacts", "roles", "workload"]:
        if f in payload:
            update[f] = payload[f]

    # coverage.zone_ids
    if "coverage" in payload and isinstance(payload["coverage"], dict):
        cov = payload["coverage"]
        if "zone_ids" in cov:
            if not isinstance(cov["zone_ids"], list):
                raise HTTPException(status_code=400, detail="coverage.zone_ids must be a list")
            update["coverage.zone_ids"] = cov["zone_ids"]
        if "geo_fence" in cov:
            update["coverage.geo_fence"] = cov["geo_fence"]

    if not update:
        raise HTTPException(status_code=400, detail="no fields to update")

    update["updated_at"] = now_iso()

    res = await db["agent_users"].find_one_and_update(
        {"agent_id": agent_id},
        {"$set": update},
        return_document=True
    )
    if not res:
        raise HTTPException(status_code=404, detail="Agent not found")

    res["_id"] = str(res["_id"])
    if "password" in res:
        res["password"] = None
    return res
