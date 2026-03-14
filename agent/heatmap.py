from fastapi import APIRouter, HTTPException
from typing import Optional, Any, Dict, List, Tuple
from datetime import datetime, timezone, timedelta
import math

from db import get_db

router = APIRouter(prefix="/analytics", tags=["analytics"])

OPEN_STATUSES = {"new", "triaged", "assigned", "in_progress"}

PRIORITY_WEIGHT = {
    "P1": 3.0,
    "P2": 2.0,
    "P3": 1.0,
}

FEED_NAME_DEFAULT = "open_requests_heatmap"


def _to_utc(dt: Any) -> Optional[datetime]:
    """Accept datetime or ISO string and return timezone-aware UTC datetime."""
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


def _age_hours(created_at_utc: datetime) -> float:
    now = datetime.now(timezone.utc)
    return max(0.0, (now - created_at_utc).total_seconds() / 3600.0)


def _weight(priority: Optional[str], age_hours: float) -> float:
    pw = PRIORITY_WEIGHT.get((priority or "P3").upper(), 1.0)
    return pw * math.log1p(age_hours)  

def _extract_points_from_geojson(geojson: Dict[str, Any]) -> Tuple[List[List[float]], float]:
    feats = (geojson or {}).get("features") or []
    points: List[List[float]] = []
    max_w = 1.0

    for f in feats:
        geom = (f or {}).get("geometry") or {}
        props = (f or {}).get("properties") or {}
        if geom.get("type") != "Point":
            continue

        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue

        lng, lat = coords[0], coords[1]
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue

        w = props.get("weight", 1.0)
        try:
            w = float(w)
        except Exception:
            w = 1.0

        if w > max_w:
            max_w = w

        points.append([float(lat), float(lng), float(w)])

    return points, max_w


async def rebuild_open_requests_heatmap(
    feed_name: str = FEED_NAME_DEFAULT,
    zone_id: Optional[str] = None,
    category: Optional[str] = None,
    days: int = 14
) -> Dict[str, Any]:
    """
    Build/refresh geo_feeds doc from requests:
      - filters open statuses
      - computes age_hours + weight
      - stores GeoJSON FeatureCollection (Point)
    """
    db = get_db()
    now = datetime.now(timezone.utc)

    query: Dict[str, Any] = {
        "status": {"$in": list(OPEN_STATUSES)},
        "location.type": "Point",
    }

    if days and days > 0:
        since = now - timedelta(days=int(days))
        query["created_at"] = {"$gte": since}

    if zone_id:
        query["location.zone_id"] = zone_id
    if category:
        query["category"] = category

    cursor = db["requests"].find(query).sort("created_at", -1)

    features: List[Dict[str, Any]] = []
    async for r in cursor:
        loc = r.get("location") or {}
        coords = loc.get("coordinates") or []
        if len(coords) < 2:
            continue

        lng, lat = coords[0], coords[1]
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue

        created_at_utc = _to_utc(r.get("created_at"))
        if not created_at_utc:
            continue

        age_h = _age_hours(created_at_utc)
        w = _weight(r.get("priority"), age_h)

        props = {
            "request_id": r.get("request_id") or r.get("requestId"),
            "age_hours": round(age_h, 2),
            "weight": round(w, 4),
            "category": r.get("category"),
            "zone_id": (loc.get("zone_id") or loc.get("zoneId")),
        }

        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "Point", "coordinates": [float(lng), float(lat)]}
        })

    doc = {
        "feed_name": feed_name,
        "generated_at": now,
        "filters": {
            "status_in": list(OPEN_STATUSES),
            "zone_id": zone_id,
            "category_in": [category] if category else []
        },
        "geojson": {"type": "FeatureCollection", "features": features},
        "aggregation": {
            "method": "weighted_heatmap",
            "weight_formula": "priority_weight * log1p(age_hours)",
            "tile_hint": "z=12",
        }
    }

    await db["geo_feeds"].update_one(
        {"feed_name": feed_name, "filters.zone_id": zone_id, "filters.category_in": doc["filters"]["category_in"]},
        {"$set": doc},
        upsert=True
    )

    return {"ok": True, "feed_name": feed_name, "count": len(features), "generated_at": now.isoformat()}


@router.post("/geofeeds/heatmap/rebuild")
async def rebuild_heatmap_endpoint(
    feed_name: str = FEED_NAME_DEFAULT,
    zone_id: Optional[str] = None,
    category: Optional[str] = None,
    days: int = 14
):
    return await rebuild_open_requests_heatmap(
        feed_name=feed_name,
        zone_id=zone_id,
        category=category,
        days=days
    )
@router.get("/geofeeds/heatmap/rebuild")
async def rebuild_heatmap_endpoint_get(
    feed_name: str = FEED_NAME_DEFAULT,
    zone_id: Optional[str] = None,
    category: Optional[str] = None,
    days: int = 14
):
    return await rebuild_open_requests_heatmap(
        feed_name=feed_name,
        zone_id=zone_id,
        category=category,
        days=days
    )


@router.get("/geofeeds/heatmap")
async def get_geofeed_heatmap(
    feed_name: str = FEED_NAME_DEFAULT,
    zone_id: Optional[str] = None,
    category: Optional[str] = None
):
    """
    Returns:
      - geojson (FeatureCollection)
      - points [[lat,lng,weight],...]
      - meta {count,max_weight}
    """
    db = get_db()

    doc = await db["geo_feeds"].find_one({
        "feed_name": feed_name,
        "filters.zone_id": zone_id,
        "filters.category_in": ([category] if category else [])
    })

    if not doc:
        return {
            "feed_name": feed_name,
            "generated_at": None,
            "filters": {"status_in": list(OPEN_STATUSES), "zone_id": zone_id, "category_in": ([category] if category else [])},
            "aggregation": None,
            "geojson": {"type": "FeatureCollection", "features": []},
            "points": [],
            "meta": {"count": 0, "max_weight": 1.0},
        }

    geojson = doc.get("geojson") or {"type": "FeatureCollection", "features": []}

    feats = geojson.get("features") or []
    if zone_id or category:
        filtered = []
        for f in feats:
            props = (f or {}).get("properties") or {}
            if zone_id and props.get("zone_id") != zone_id:
                continue
            if category and props.get("category") != category:
                continue
            filtered.append(f)
        geojson = {"type": "FeatureCollection", "features": filtered}

    points, max_w = _extract_points_from_geojson(geojson)

    generated_at = doc.get("generated_at")
    if isinstance(generated_at, datetime):
        generated_at = generated_at.isoformat()

    return {
        "feed_name": doc.get("feed_name"),
        "generated_at": generated_at,
        "filters": doc.get("filters"),
        "aggregation": doc.get("aggregation"),
        "geojson": geojson,
        "points": points,
        "meta": {"count": len(points), "max_weight": max_w},
    }


@router.get("/geofeeds/heatmap/ping")
async def heatmap_ping():
    return {"ok": True, "router": "agent.heatmap", "path": "/analytics/geofeeds/heatmap"}
