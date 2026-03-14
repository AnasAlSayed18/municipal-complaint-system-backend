from fastapi import APIRouter
from datetime import datetime, timezone
from db import get_db
from fastapi import APIRouter, HTTPException, Request
from bson import ObjectId
from pymongo import ReturnDocument
from fastapi import HTTPException
from pydantic import BaseModel
import uuid
from typing import Optional, Any, Dict, Tuple
from datetime import datetime, timezone, timedelta


router = APIRouter(prefix="/requests", tags=["requests"])

@router.get("")
async def list_requests(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    zone_id: Optional[str] = None,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None
):
    db = get_db()
    query = {}

    if status:
        query["status"] = status
    if priority:
        query["priority"] = priority

    # ✅ صح: zone داخل location
    if zone_id:
        query["location.zone_id"] = zone_id

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

    items = await db["requests"].find(query).sort("created_at", -1).limit(200).to_list(length=200)

    # ✅ اجمع zone_ids
    zone_ids = []
    for it in items:
        z = (it.get("location") or {}).get("zone_id")
        if z:
            zone_ids.append(z)

    zone_map = {}
    if zone_ids:
        zones = await db["zones"].find({"_id": {"$in": list(set(zone_ids))}}).to_list(length=1000)
        zone_map = {z["_id"]: (z.get("name_ar") or z.get("name") or z["_id"]) for z in zones}

    # ✅ حط zone_name داخل location + حوّل _id لسترينج
    for it in items:
        it["_id"] = str(it["_id"])
        loc = it.get("location") or {}
        zid = loc.get("zone_id")
        if zid:
            loc["zone_name"] = zone_map.get(zid, zid)  # fallback = id
        it["location"] = loc

    return items






ALLOWED_PRIORITY = {"P1", "P2", "P3"}

def _to_utc(dt: Any) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if isinstance(dt, str):
        try:
            d = datetime.fromisoformat(dt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None

def _normalize_priority(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    up = str(p).strip().upper()
    if up in {"NORMAL", "MEDIUM"}:
        return "P2"
    if up in {"LOW"}:
        return "P3"
    if up in {"HIGH", "URGENT", "CRITICAL"}:
        return "P1"
    if up in ALLOWED_PRIORITY:
        return up
    return None

def _normalize_status(s: Optional[str]) -> str:
    return (s or "").strip().lower() or "new"

def _classify_sla(elapsed_h: float, target_h: float, breach_h: float) -> str:
    if elapsed_h < target_h:
        return "on_time"
    if elapsed_h < breach_h:
        return "at_risk"
    return "overdue"









# ✅ Dashboard endpoint (لازم يكون قبل /{request_id})
@router.get("/dashboard")
async def staff_dashboard():
    db = get_db()
    now = datetime.now(timezone.utc)

    closed_statuses = {"resolved", "closed"}

    items = await db["requests"].find({}).sort("created_at", -1).limit(300).to_list(length=300)

    # categories map
    cat_ids = {it.get("category") for it in items if it.get("category")}
    cat_map: Dict[str, dict] = {}
    if cat_ids:
        clist = await db["issue_categories"].find({"_id": {"$in": list(cat_ids)}}).to_list(length=2000)
        for c in clist:
            cat_map[c["_id"]] = {
                "name_ar": c.get("name_ar") or c.get("name") or c["_id"],
                "default_priority": (c.get("default_priority") or "").upper()
            }

    # SLA policies active
    pol_map: Dict[Tuple[str, str], dict] = {}
    if cat_ids:
        plist = await db["sla_policies"].find({
            "issue_category_id": {"$in": list(cat_ids)},
            "active": True
        }).to_list(length=5000)
        for p in plist:
            key = (p.get("issue_category_id"), str(p.get("priority")).upper())
            pol_map[key] = p

    open_requests_count = 0
    sla_summary = {"on_time": 0, "at_risk": 0, "overdue": 0, "no_policy": 0}

    # by_category counts (للـ OPEN فقط مثل ما مكتوب بالـ UI "Top 12 من الطلبات المفتوحة")
    by_cat_counts: Dict[str, int] = {}

    for it in items:
        status = _normalize_status(it.get("status"))
        is_open = status not in closed_statuses
        if not is_open:
            continue

        open_requests_count += 1

        cat_id = it.get("category") or "-"
        cat_info = cat_map.get(cat_id, {"name_ar": cat_id, "default_priority": ""})

        pr = _normalize_priority(it.get("priority"))
        if not pr:
            dp = (cat_info.get("default_priority") or "").upper()
            pr = dp if dp in ALLOWED_PRIORITY else None

        created = _to_utc(it.get("created_at")) or now
        elapsed_h = (now - created).total_seconds() / 3600.0

        policy = pol_map.get((cat_id, pr)) if (cat_id and pr) else None
        sla_status = "no_policy"
        if policy:
            target_h = float(policy.get("target_hours") or 0)
            breach_h = float(policy.get("breach_hours") or 0)
            if target_h > 0 and breach_h > 0:
                sla_status = _classify_sla(elapsed_h, target_h, breach_h)

        sla_summary[sla_status] = sla_summary.get(sla_status, 0) + 1
        by_cat_counts[cat_id] = by_cat_counts.get(cat_id, 0) + 1

    by_category = []
    for cat_id, count in by_cat_counts.items():
        cat_info = cat_map.get(cat_id, {"name_ar": cat_id})
        by_category.append({
            "category_id": cat_id,
            "category_name_ar": cat_info.get("name_ar") or cat_id,
            "count": count
        })

    by_category.sort(key=lambda x: x["count"], reverse=True)
    by_category = by_category[:12]

    return {
        "open_requests_count": open_requests_count,
        "sla_summary": sla_summary,
        "by_category": by_category,
        "generated_at": now.isoformat()
    }




# تعريف الثوابت
#----------------

ALLOWED_PRIORITY = {"P1", "P2", "P3"}
STATUS_FLOW = ["new", "triaged", "assigned", "in_progress", "resolved", "closed"]
STATUS_INDEX = {s: i for i, s in enumerate(STATUS_FLOW)}


class UpdateRequestBody(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None








# ---------- Helpers ----------
ALLOWED_PRIORITY = {"P1", "P2", "P3"}

def _to_utc(dt: Any) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if isinstance(dt, str):
        try:
            # supports "2026-01-11T20:09:50.307000"
            d = datetime.fromisoformat(dt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None

def _normalize_priority(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    up = str(p).strip().upper()
    # your data has "Normal" sometimes
    if up in {"NORMAL", "MEDIUM"}:
        return "P2"
    if up in {"LOW"}:
        return "P3"
    if up in {"HIGH", "URGENT", "CRITICAL"}:
        return "P1"
    if up in ALLOWED_PRIORITY:
        return up
    return None

# ---------- SLA Monitoring ----------
@router.get("/sla/monitoring")
async def sla_monitoring(
    sla_status: Optional[str] = None,   # on_time | at_risk | overdue | no_policy
    status: Optional[str] = None,       # request.status filter
    category: Optional[str] = None,     # request.category filter
    zone_id: Optional[str] = None,      # request.location.zone_id filter
    q: Optional[str] = None             # search in request_id / description
):
    db = get_db()
    now = datetime.now(timezone.utc)

    query = {}

    if status:
        query["status"] = status
    if category:
        query["category"] = category
    if zone_id:
        query["location.zone_id"] = zone_id

    if q:
        query["$or"] = [
            {"request_id": {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}},
        ]

    cursor = db["requests"].find(query).sort("created_at", -1).limit(300)
    items = await cursor.to_list(length=300)

    # collect categories for name/default_priority
    cat_ids = set()
    for it in items:
        cid = it.get("category")
        if cid:
            cat_ids.add(cid)

    cat_map = {}
    if cat_ids:
        ccur = db["issue_categories"].find({"_id": {"$in": list(cat_ids)}})
        clist = await ccur.to_list(length=1000)
        for c in clist:
            cat_map[c["_id"]] = {
                "name_ar": c.get("name_ar") or c.get("name") or c["_id"],
                "default_priority": (c.get("default_priority") or "").upper()
            }

    # fetch sla policies for these categories (active only)
    pol_map: Dict[Tuple[str, str], dict] = {}
    if cat_ids:
        pcur = db["sla_policies"].find({"issue_category_id": {"$in": list(cat_ids)}, "active": True})
        plist = await pcur.to_list(length=5000)
        for p in plist:
            key = (p.get("issue_category_id"), str(p.get("priority")).upper())
            pol_map[key] = p

    def classify(elapsed_h: float, target_h: float, breach_h: float) -> str:
        if elapsed_h < target_h:
            return "on_time"
        if elapsed_h < breach_h:
            return "at_risk"
        return "overdue"

    out = []
    summary = {"on_time": 0, "at_risk": 0, "overdue": 0, "no_policy": 0}

    for it in items:
        it["_id"] = str(it["_id"])
        created = _to_utc(it.get("created_at"))
        if not created:
            created = now

        elapsed_h = (now - created).total_seconds() / 3600.0

        cat_id = it.get("category")
        cat_info = cat_map.get(cat_id, {"name_ar": cat_id or "-", "default_priority": ""})

        # effective priority: request priority -> normalized -> fallback to issue_categories.default_priority
        pr = _normalize_priority(it.get("priority"))
        if not pr:
            dp = (cat_info.get("default_priority") or "").upper()
            pr = dp if dp in ALLOWED_PRIORITY else None

        policy = pol_map.get((cat_id, pr)) if (cat_id and pr) else None

        sla_obj = {
            "status": "no_policy",
            "target_hours": None,
            "breach_hours": None,
            "elapsed_hours": round(elapsed_h, 1),
            "target_due_at": None,
            "breach_due_at": None,
        }

        if policy:
            target_h = float(policy.get("target_hours") or 0)
            breach_h = float(policy.get("breach_hours") or 0)

            if target_h > 0 and breach_h > 0:
                st = classify(elapsed_h, target_h, breach_h)
                sla_obj["status"] = st
                sla_obj["target_hours"] = target_h
                sla_obj["breach_hours"] = breach_h
                sla_obj["target_due_at"] = (created + timedelta(hours=target_h)).isoformat()
                sla_obj["breach_due_at"] = (created + timedelta(hours=breach_h)).isoformat()
            else:
                sla_obj["status"] = "no_policy"

        summary[sla_obj["status"]] = summary.get(sla_obj["status"], 0) + 1

        it["category_name_ar"] = cat_info.get("name_ar")
        it["effective_priority"] = pr or it.get("priority") or "-"
        it["sla"] = sla_obj

        out.append(it)

    # filter by sla_status if requested
    if sla_status:
        wanted = sla_status.strip().lower()
        out = [x for x in out if (x.get("sla") or {}).get("status") == wanted]
        # recompute summary for filtered list (optional)
        summary2 = {"on_time": 0, "at_risk": 0, "overdue": 0, "no_policy": 0}
        for x in out:
            s = (x.get("sla") or {}).get("status") or "no_policy"
            summary2[s] = summary2.get(s, 0) + 1
        summary = summary2

    return {"summary": summary, "items": out}



# ---------- Get Feedback by Request ID ----------

@router.get("/{request_id}/feedback")
async def get_feedback_by_request_id(request_id: str):
    db = get_db()
    try:
        items = await db["ratings_feedback"] \
            .find({"request_id": request_id}) \
            .limit(100) \
            .to_list(length=100)

        for it in items:
            it["_id"] = str(it["_id"])

        return items

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving feedback: {str(e)}"
        )


# ---------- Add Internal Note ----------

class InternalNoteIn(BaseModel):
    text: str

@router.post("/{request_id}/internal-notes")
async def add_internal_note(request_id: str, body: InternalNoteIn, request: Request):
    db = get_db()

    txt = (body.text or "").strip()
    if len(txt) < 3:
        raise HTTPException(status_code=400, detail="Note is too short")

    # try to get staff identity from Authorization header (stub-token-<staff_id>)
    staff_id = "staff_demo"
    staff_name = "Staff User"
    try:
        auth_header = None
        # request is FastAPI Request injected into handler
        # note: some frameworks pass Authorization as 'authorization'
        auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth_header:
            parts = auth_header.split()
            token = parts[-1]
            # expected token format: stub-token-<staff_id>
            if token.startswith("stub-token-"):
                maybe_id = token.replace("stub-token-", "")
                # lookup staff user by staff_id
                staff = await db["staff_users"].find_one({"staff_id": maybe_id})
                if staff:
                    staff_id = staff.get("staff_id")
                    staff_name = staff.get("full_name") or staff.get("user_name") or staff_id
    except Exception:
        # fallback to demo values
        staff_id = staff_id
        staff_name = staff_name

    note = {
        "note_id": str(uuid.uuid4()),
        "text": txt,
        "staff_id": staff_id,
        "staff_name": staff_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    updated = await db["requests"].find_one_and_update(
        {"request_id": request_id},
        {"$push": {"internal_notes": note}, "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}},
        return_document=ReturnDocument.AFTER
    )

    if not updated:
        raise HTTPException(status_code=404, detail="Request not found")

    updated["_id"] = str(updated["_id"])
    return updated





# ---------- Manual Escalate ----------
class EscalateIn(BaseModel):
    reason: Optional[str] = None
    bump_priority: Optional[bool] = False  # if true -> set priority to P1

@router.post("/{request_id}/escalate")
async def manual_escalate(request_id: str, body: EscalateIn):
    db = get_db()

    doc = await db["requests"].find_one({"request_id": request_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")

    reason = (body.reason or "").strip() or "Manual escalation"

    updates = {
        "$inc": {"escalation_count": 1},
        "$set": {"updated_at": datetime.now(timezone.utc).isoformat(), "breach_reason": reason},
        "$push": {"timeline": {
            "type": "MANUAL_ESCALATE",
            "at": datetime.now(timezone.utc).isoformat(),
            "reason": reason
        }}
    }

    if body.bump_priority:
        updates["$set"]["priority"] = "P1"

    updated = await db["requests"].find_one_and_update(
        {"request_id": request_id},
        updates,
        return_document=ReturnDocument.AFTER
    )

    updated["_id"] = str(updated["_id"])
    return updated




# ---------- Get Request Details ----------

@router.get("/{request_id}")
async def get_request_details(request_id: str):
    db = get_db()
    request = await db["requests"].find_one({"request_id": request_id})
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    request["_id"] = str(request["_id"])

    zid = (request.get("location") or {}).get("zone_id")
    if zid:
        z = await db["zones"].find_one({"_id": zid})
        request["location"]["zone_name"] = (z.get("name_ar") or z.get("name") or zid) if z else zid

    return request



# ---------- Update Request ----------

ALLOWED_PRIORITY = {"P1", "P2", "P3"}
STATUS_FLOW = ["new", "triaged", "assigned", "in_progress", "resolved", "closed"]
STATUS_INDEX = {s: i for i, s in enumerate(STATUS_FLOW)}


class UpdateRequestBody(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None


@router.patch("/{id}")
async def update_request(id: str, body: UpdateRequestBody):
    db = get_db()

    doc = await db["requests"].find_one({"_id": ObjectId(id)})
    if not doc:
        raise HTTPException(status_code=404, detail="NOT_FOUND")

    updates = {}

    # --- validate priority
    if body.priority is not None:
        if body.priority not in ALLOWED_PRIORITY:
            raise HTTPException(status_code=400, detail="INVALID_PRIORITY")
        updates["priority"] = body.priority

    # --- validate status transition
    if body.status is not None:
        new_status = body.status
        current_status = (doc.get("status") or "").lower()

        if new_status not in STATUS_INDEX:
            raise HTTPException(status_code=400, detail="INVALID_STATUS")

        # إذا الحالي فاضي، اعتبره new
        if current_status not in STATUS_INDEX:
            current_status = "new"

        # السماح فقط بالانتقال للـ next مباشرة
        curr_i = STATUS_INDEX[current_status]
        next_allowed = STATUS_FLOW[min(curr_i + 1, len(STATUS_FLOW) - 1)]

        if new_status != current_status and new_status != next_allowed:
            raise HTTPException(
                status_code=400,
                detail=f"INVALID_STATUS_TRANSITION:{current_status}->{new_status}"
            )

        updates["status"] = new_status

    if not updates:
        return {"ok": True, "updated": False}

    # updated_at (اختياري)
    updates["updated_at"] = datetime.utcnow()

    await db["requests"].update_one(
        {"_id": ObjectId(id)},
        {"$set": updates}
    )

    # رجّع آخر نسخة
    new_doc = await db["requests"].find_one({"_id": ObjectId(id)})
    new_doc["_id"] = str(new_doc["_id"])
    if "request_id" not in new_doc and "requestId" in new_doc:
        new_doc["request_id"] = new_doc["requestId"]

    if isinstance(new_doc.get("created_at"), datetime):
        new_doc["created_at"] = new_doc["created_at"].isoformat()
    if isinstance(new_doc.get("updated_at"), datetime):
        new_doc["updated_at"] = new_doc["updated_at"].isoformat()

    return new_doc

