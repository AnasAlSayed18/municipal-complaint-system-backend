from fastapi import APIRouter
from typing import Optional, Any, Dict, List
from datetime import datetime, timezone, timedelta
from db import get_db

router = APIRouter(prefix="/analytics", tags=["analytics"])

OPEN_STATUSES = {"new", "triaged", "assigned", "in_progress"}


def _to_utc(dt: Any) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if isinstance(dt, str):
        try:
            s = dt.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            d = datetime.fromisoformat(s)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _safe_float(x):
    try:
        return float(x)
    except Exception:
        return None


from datetime import datetime, timezone

def sla_status_from_request(req: dict) -> str:
    sla = req.get("sla") or {}
    target = sla.get("target_hours")
    breach = sla.get("breach_hours")

    created = req.get("created_at")
    if not target or not breach or not created:
        return "no_policy"

    if isinstance(created, str):
        try:
            s = created
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            created = datetime.fromisoformat(s)
        except Exception:
            return "no_policy"

    if isinstance(created, datetime) and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    age_hours = (now - created).total_seconds() / 3600.0

    try:
        target = float(target)
        breach = float(breach)
    except Exception:
        return "no_policy"

    if age_hours <= target:
        return "on_time"
    if age_hours <= breach:
        return "at_risk"
    return "overdue"



@router.get("/map-markers")
async def get_map_markers(
    days: int = 14,
    status: str = "open",              
    category: Optional[str] = None,
    zone_id: Optional[str] = None,
    limit: int = 3000
):
    """
    Markers for clustering:
    items: [{
      request_id, lat, lng, status, category, category_name_ar,
      zone_id, zone_name, priority, created_at, sla_status
    }]
    """
    db = get_db()
    now = datetime.now(timezone.utc)

    query: Dict[str, Any] = {}

    if days and days > 0:
        since = now - timedelta(days=int(days))
        query["created_at"] = {"$gte": since}

    st = (status or "open").strip().lower()
    if st != "all":
        if st == "open":
            query["status"] = {"$in": list(OPEN_STATUSES)}
        else:
            query["status"] = st

    if category:
        query["category"] = category

    if zone_id:
        query["location.zone_id"] = zone_id

    cursor = db["requests"].find(query).sort("created_at", -1).limit(int(limit))
    docs = await cursor.to_list(length=int(limit))

    cat_ids = {d.get("category") for d in docs if d.get("category")}
    cat_map: Dict[str, str] = {}
    if cat_ids:
        clist = await db["issue_categories"].find({"_id": {"$in": list(cat_ids)}}).to_list(length=2000)
        for c in clist:
            cat_map[c["_id"]] = c.get("name_ar") or c.get("name") or c["_id"]

    items: List[Dict[str, Any]] = []
    for d in docs:
        loc = d.get("location") or {}
        coords = loc.get("coordinates") or []
        if len(coords) < 2:
            continue

        lng = _safe_float(coords[0])
        lat = _safe_float(coords[1])
        if lat is None or lng is None:
            continue

        rid = d.get("request_id") or d.get("requestId") or "-"
        status_v = (d.get("status") or "").lower() or "-"
        cat = d.get("category") or "-"
        pr = d.get("priority")

        created = _to_utc(d.get("created_at"))
        created_iso = created.isoformat() if created else None

        sla_status = None
        ck = d.get("computed_kpis") or {}
        sla_status = ck.get("sla_state") or d.get("sla_state") or d.get("slaStatus") or "no_policy"
        sla_status = sla_status_from_request(d)
        

        items.append({
            "request_id": rid,
            "lat": float(lat),
            "lng": float(lng),
            "status": status_v,
            "category": cat,
            "category_name_ar": cat_map.get(cat, cat),
            "zone_id": loc.get("zone_id"),
            "zone_name": loc.get("zone_name"),
            "priority": pr,
            "created_at": created_iso,
            "sla_status": sla_status, 
        })

    return {
        "items": items,
        "meta": {
            "count": len(items),
            "days": days,
            "status": status,
            "category": category,
            "zone_id": zone_id,
            "limit": limit
        }
    }


@router.get("/zones/summary")
async def zones_summary(
    days: int = 14,
    status: str = "open",              
    category: Optional[str] = None,
    include_geometry: bool = False     
):
    """
    Zone-based summaries (choropleth-like):
    returns list of zones with counts for current filter.
    If include_geometry=true and zones collection exists, attach zone geometry.
    """
    db = get_db()
    now = datetime.now(timezone.utc)

    match: Dict[str, Any] = {}

    if days and days > 0:
        since = now - timedelta(days=int(days))
        match["created_at"] = {"$gte": since}

    st = (status or "open").strip().lower()
    if st != "all":
        if st == "open":
            match["status"] = {"$in": list(OPEN_STATUSES)}
        else:
            match["status"] = st

    if category:
        match["category"] = category

    # group by zone_id (inside location)
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": "$location.zone_id",
            "count": {"$sum": 1},
            "zone_name": {"$first": "$location.zone_name"},
        }},
        {"$sort": {"count": -1}}
    ]

    groups = await db["requests"].aggregate(pipeline).to_list(length=5000)

    zone_geo_map: Dict[str, Any] = {}
    if include_geometry:
        zone_ids = [g["_id"] for g in groups if g.get("_id")]
        if zone_ids:
            zdocs = await db["zones"].find({"_id": {"$in": zone_ids}}).to_list(length=5000)
            for z in zdocs:
                zone_geo_map[z["_id"]] = {
                    "name": z.get("name") or z.get("name_ar") or z["_id"],
                    "geometry": z.get("geometry") or z.get("geojson")  # حسب تخزينك
                }

    items = []
    total = 0
    for g in groups:
        zid = g.get("_id") or "UNKNOWN"
        c = int(g.get("count") or 0)
        total += c

        item = {
            "zone_id": zid,
            "zone_name": g.get("zone_name") or zid,
            "count": c,
        }

        if include_geometry and zid in zone_geo_map:
            item["zone_name"] = zone_geo_map[zid].get("name", item["zone_name"])
            item["geometry"] = zone_geo_map[zid].get("geometry")

        items.append(item)

    return {
        "items": items,
        "meta": {
            "total": total,
            "days": days,
            "status": status,
            "category": category,
            "include_geometry": include_geometry
        }
    }


@router.get("/map/health")
async def map_health():
    return {"ok": True, "router": "agent.map_live", "paths": ["/analytics/map-markers", "/analytics/zones/summary"]}
