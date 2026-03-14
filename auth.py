# routes/auth.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone
import hashlib
import binascii

from db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    user_name: str
    password: str


def _verify_password(stored: str | None, provided: str) -> bool:
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


@router.post("/login")
async def login(body: LoginBody):
    db = get_db()

    # 1) Staff
    staff = await db["staff_users"].find_one({
        "user_name": body.user_name,
        "active": True
    })

    if staff and _verify_password(staff.get("password"), body.password):
        token = f"stub-token-{staff.get('staff_id')}"
        await db["staff_users"].update_one(
            {"_id": staff["_id"]},
            {"$set": {"last_login_at": datetime.now(timezone.utc)}}
        )
        return {
            "token": token,
            "account_type": "staff",
            "role": staff.get("role", "staff"),
            "staff_id": staff.get("staff_id"),
            "full_name": staff.get("full_name"),
        }

    # 2) Agent 
    agent = await db["agent_users"].find_one({
        "user_name": body.user_name,
        "active": True
    })

    if agent and _verify_password(agent.get("password"), body.password):
        token = f"stub-token-{agent.get('agent_id')}"
        return {
            "token": token,
            "account_type": "agent",
            "role": agent.get("role", "agent"),
            "agent_id": agent.get("agent_id"),
            "full_name": agent.get("full_name"),
        }

    raise HTTPException(status_code=401, detail="Invalid credentials")
