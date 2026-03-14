from fastapi import APIRouter, HTTPException, UploadFile, File
from typing import Optional
from datetime import datetime, timezone
from bson import ObjectId
from pydantic import BaseModel, EmailStr
import secrets
import hashlib
import binascii
from db import get_db
import os
import time
import hashlib
import requests

router = APIRouter(
    prefix="/agent/requests",
    tags=["agent_requests"]
)

@router.get("/")
async def list_agent_requests(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    zone_id: Optional[str] = None,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    db = get_db()
    query = {}

    if status:
        query["status"] = {"$regex": f"^{status}$", "$options": "i"}

    if priority:
        query["priority"] = priority

    if zone_id:
        query["zone_id"] = zone_id

    if q:
        query["$or"] = [
            {"request_id": {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}},
        ]

    if date_from or date_to:
        query["created_at"] = {}
        if date_from:
            query["created_at"]["$gte"] = datetime.fromisoformat(date_from)
        if date_to:
            query["created_at"]["$lte"] = datetime.fromisoformat(date_to)

    cursor = (
        db["requests"]
        .find(query)
        .sort("created_at", -1)
        .limit(200)
    )

    items = await cursor.to_list(length=200)
    for item in items:
        item["_id"] = str(item["_id"])

    return items



@router.get("/by-agent/{agent_id}")
async def list_requests_by_agent(
    agent_id: str,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    zone_id: Optional[str] = None,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    db = get_db()
    query = {"agent_id": agent_id}

    if status:
        query["status"] = {"$regex": f"^{status}$", "$options": "i"}

    if priority:
        query["priority"] = priority

    if zone_id:
        query["zone_id"] = zone_id

    if q:
        query["$or"] = [
            {"request_id": {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}},
        ]

    if date_from or date_to:
        query["created_at"] = {}
        if date_from:
            query["created_at"]["$gte"] = datetime.fromisoformat(date_from)
        if date_to:
            query["created_at"]["$lte"] = datetime.fromisoformat(date_to)

    cursor = (
        db["requests"]
        .find(query)
        .sort("created_at", -1)
        .limit(200)
    )

    items = await cursor.to_list(length=200)
    for item in items:
        item["_id"] = str(item["_id"])

    return items
@router.get("/performance/{agent_id}")
async def get_agent_performance(agent_id: str):
    db = get_db()

    cursor = db["requests"].find({
        "agent_id": agent_id,
        "status": {"$regex": "^resolved$", "$options": "i"},
    })

    tasks = await cursor.to_list(length=1000)

    completed_tasks = len(tasks)

    if completed_tasks == 0:
        return {
            "completed_tasks": 0,
            "average_resolution_time_minutes": 0,
            "commitment_rate": 0,
        }

    total_minutes = 0
    on_time = 0
    SLA_MINUTES = 120
    processed = 0

    def _ensure_dt(val):
        if isinstance(val, datetime):
            dt = val
            if dt.tzinfo is None:
                # assume stored as UTC naive
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        if isinstance(val, str):
            try:
                s = val
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                return None
        return None

    for task in tasks:
        created = _ensure_dt(task.get("created_at"))
        resolved_at = _ensure_dt(task.get("updated_at"))

        if not created or not resolved_at:
            continue

        diff_minutes = (resolved_at - created).total_seconds() / 60
        total_minutes += diff_minutes
        processed += 1

        if diff_minutes <= SLA_MINUTES:
            on_time += 1

    if processed == 0:
        return {
            "completed_tasks": completed_tasks,
            "total_completed": 0,
            "average_resolution_time_minutes": 0,
            "avg_time_minutes": 0,
            "commitment_rate": 0,
            "adherence_percent": 0,
        }

    average_resolution_time = round(total_minutes / processed)
    commitment_rate = round((on_time / processed) * 100)

    return {
        "completed_tasks": completed_tasks,
        "total_completed": processed,
        "average_resolution_time_minutes": average_resolution_time,
        "avg_time_minutes": average_resolution_time,
        "commitment_rate": commitment_rate,
        "adherence_percent": commitment_rate,
    }


@router.get("/{id}")
async def get_agent_request(id: str):
    db = get_db()

    try:
        query = {"_id": ObjectId(id)}
    except Exception:
        query = {"request_id": id}

    doc = await db["requests"].find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail="Agent request not found")

    doc["_id"] = str(doc["_id"])
    return doc


@router.get("/agent/{agent_id}")
async def get_agent_user(agent_id: str):
    db = get_db()

    doc = await db["agent_users"].find_one({"agent_id": agent_id})
    if not doc:
        doc = await db["agent_users"].find_one({"user_name": agent_id})

    if not doc:
        raise HTTPException(status_code=404, detail="Agent user not found")

    doc["_id"] = str(doc["_id"])
    if "password" in doc:
        doc["password"] = None
    return doc


class ContactsPayload(BaseModel):
    email: EmailStr | None = None
    phone: Optional[str] = None


class UpdateAgentProfile(BaseModel):
    name: Optional[str] = None
    contacts: Optional[ContactsPayload] = None


class ChangePasswordPayload(BaseModel):
    current_password: str
    new_password: str
    confirm_password: Optional[str] = None


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 100_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2${iterations}${salt}${binascii.hexlify(dk).decode()}"


def _verify_password(stored: str, provided: str) -> bool:
    if not stored:
        return False
    if stored.startswith("pbkdf2$"):
        try:
            _prefix, iter_s, salt, hash_hex = stored.split("$", 3)
            iterations = int(iter_s)
            dk = hashlib.pbkdf2_hmac("sha256", provided.encode("utf-8"), salt.encode("utf-8"), iterations)
            return binascii.hexlify(dk).decode() == hash_hex
        except Exception:
            return False
    return stored == provided


@router.patch("/agent/{agent_id}")
async def patch_agent_profile(agent_id: str, payload: UpdateAgentProfile):
    db = get_db()

    update = {}

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        update["name"] = name

    if payload.contacts:
        if payload.contacts.email is not None:
            update["contacts.email"] = payload.contacts.email
        if payload.contacts.phone is not None:
            update["contacts.phone"] = payload.contacts.phone

    if not update:
        raise HTTPException(status_code=400, detail="no editable fields provided")

    update["updated_at"] = datetime.now(timezone.utc)

    res = await db["agent_users"].find_one_and_update(
        {"agent_id": agent_id},
        {"$set": update},
        return_document=True,
    )

    if not res:
        raise HTTPException(status_code=404, detail="Agent not found")

    res["_id"] = str(res["_id"])
    if "password" in res:
        res["password"] = None
    return res


@router.patch("/agent/{agent_id}/password")
async def change_agent_password(agent_id: str, payload: ChangePasswordPayload):
    db = get_db()

    user = await db["agent_users"].find_one({"agent_id": agent_id})
    if not user:
        raise HTTPException(status_code=404, detail="Agent not found")

    stored = user.get("password")
    if not _verify_password(stored or "", payload.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    if payload.confirm_password is not None and payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirm password do not match")

    if not payload.new_password or len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    hashed = _hash_password(payload.new_password)
    now = datetime.now(timezone.utc)

    await db["agent_users"].update_one(
        {"agent_id": agent_id},
        {"$set": {"password": hashed, "updated_at": now, "password_changed_at": now}}
    )

    updated = await db["agent_users"].find_one({"agent_id": agent_id})
    updated["_id"] = str(updated["_id"])
    if "password" in updated:
        updated["password"] = None
    return {"detail": "password updated successfully", "user": updated}

class UpdateStatusPayload(BaseModel):
    status: str


@router.patch("/{id}/status")
async def update_request_status(id: str, payload: UpdateStatusPayload):
    db = get_db()

    transitions = {
        "assigned": ["in_progress"],
        "in_progress": ["resolved"],
        "resolved": ["in_progress"],
    }

    try:
        query = {"_id": ObjectId(id)}
    except Exception:
        query = {"request_id": id}

    doc = await db["requests"].find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")

    # 👇 normalize ONLY for comparison
    current_raw = doc.get("status")
    new_raw = payload.status

    if not current_raw or not new_raw:
        raise HTTPException(status_code=400, detail="Status is required")

    current = current_raw.lower()
    new_status_norm = new_raw.lower()

    if current not in transitions:
        raise HTTPException(
            status_code=400,
            detail=f"Status '{current_raw}' cannot be updated via this endpoint"
        )

    if new_status_norm not in transitions[current]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid transition from '{current_raw}' to '{new_raw}'"
        )

    now = datetime.utcnow()

    await db["requests"].update_one(
        query,
        {
            "$set": {
                "status": new_raw,          
                "updated_at": now,
                "status_updated_at": now,
            },
            "$push": {
                "status_history": {
                    "from": current_raw,     
                    "to": new_raw,
                    "at": now,
                }
            },
        }
    )

    updated = await db["requests"].find_one(query)
    updated["_id"] = str(updated["_id"])
    return updated


@router.post("/{id}/evidence")
async def upload_request_evidence(id: str, files: list[UploadFile] = File(...)):
    db = get_db()

    try:
        query = {"_id": ObjectId(id)}
    except Exception:
        query = {"request_id": id}

    doc = await db["requests"].find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")

    CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "dprx3dawc")
    API_KEY = os.getenv("CLOUDINARY_API_KEY", "715985161534258")
    API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "1_IMol12rxlyFlEcPa8L72qzls8")

    uploaded_entries = []

    for upload in files:
        content = await upload.read()
        timestamp = int(time.time())
        to_sign = f"timestamp={timestamp}{API_SECRET}"
        signature = hashlib.sha1(to_sign.encode("utf-8")).hexdigest()

        data = {
            "timestamp": timestamp,
            "api_key": API_KEY,
            "signature": signature,
        }

        files_payload = {"file": (upload.filename or "file", content, upload.content_type or "application/octet-stream")}

        url = f"https://api.cloudinary.com/v1_1/{CLOUD_NAME}/image/upload"
        try:
            resp = requests.post(url, data=data, files=files_payload, timeout=30)
            resp.raise_for_status()
            result = resp.json()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Cloudinary upload failed: {str(e)}")

        secure_url = result.get("secure_url") or result.get("url")
        public_id = result.get("public_id")

        entry = {
            "type": "photo",
            "url": secure_url,
            "public_id": public_id,
            "uploaded_at": datetime.utcnow(),
        }
        uploaded_entries.append(entry)

    if uploaded_entries:
        await db["requests"].update_one(
            query,
            {
                "$push": {
                    "agent_evidence": {"$each": uploaded_entries},
                }
            },
        )

    updated = await db["requests"].find_one(query)
    updated["_id"] = str(updated["_id"])
    return updated

@router.delete("/{id}/evidence/{public_id}")
async def delete_agent_evidence(id: str, public_id: str):
    db = get_db()

    try:
        query = {"_id": ObjectId(id)}
    except Exception:
        query = {"request_id": id}

    doc = await db["requests"].find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")

    res = await db["requests"].update_one(
        query,
        {"$pull": {"agent_evidence": {"public_id": public_id}}}
    )

    if res.modified_count == 0:
        raise HTTPException(status_code=404, detail="Agent evidence not found")

    updated = await db["requests"].find_one(query)
    updated["_id"] = str(updated["_id"])
    return updated
