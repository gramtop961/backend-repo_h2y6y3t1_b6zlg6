import os
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from bson import ObjectId

from database import db, create_document, get_documents

app = FastAPI(title="Rate the World API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------- Pydantic models ---------
class ReviewCreate(BaseModel):
    country_iso: str = Field(..., description="ISO code, e.g., FR")
    rating: float = Field(..., ge=1, le=5)
    title: Optional[str] = None
    comment: Optional[str] = None
    contexts: List[str] = []  # Tourist, Local, Expat, Digital Nomad
    anonymous: bool = True
    photos: List[str] = []

class Review(BaseModel):
    id: str
    country_iso: str
    rating: float
    title: Optional[str] = None
    comment: Optional[str] = None
    contexts: List[str] = []
    anonymous: bool = True
    photos: List[str] = []
    votes: int = 0
    created_at: datetime

class VoteRequest(BaseModel):
    direction: str = Field(..., pattern="^(up|down)$")

class FlagRequest(BaseModel):
    reason: str
    note: Optional[str] = None

class GlobeCountry(BaseModel):
    iso: str
    avgRating: float
    reviewCount: int
    centroid: List[float]
    trend: float

# --------- Helpers ---------

def _oid_to_str(doc):
    if not doc:
        return doc
    doc["id"] = str(doc.pop("_id"))
    return doc

# --------- Routes ---------

@app.get("/")
def root():
    return {"message": "Rate the World API running"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            try:
                cols = db.list_collection_names()
                response["collections"] = cols
                response["database"] = "✅ Connected & Working"
                response["connection_status"] = "Connected"
            except Exception as e:
                response["database"] = f"⚠️  Error: {str(e)[:80]}"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response

@app.get("/api/globe", response_model=List[GlobeCountry])
def globe_data(limit: int = 250):
    # Try to aggregate from reviews; fallback to sample
    try:
        if db is None:
            raise Exception("DB not configured")
        pipeline = [
            {"$group": {"_id": "$country_iso", "avg": {"$avg": "$rating"}, "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": limit}
        ]
        rows = list(db["review"].aggregate(pipeline))
        out = []
        for r in rows:
            iso = r["_id"]
            # naive centroid placeholder; should be real lookup table
            out.append({
                "iso": iso,
                "avgRating": round(float(r.get("avg", 0)), 2),
                "reviewCount": int(r.get("count", 0)),
                "centroid": [0.0, 0.0],
                "trend": 0.0
            })
        if out:
            return out
    except Exception:
        pass
    # fallback sample
    return [
        {"iso": "JP", "avgRating": 4.6, "reviewCount": 3210, "centroid": [138.2529, 36.2048], "trend": 0.12},
        {"iso": "PT", "avgRating": 4.5, "reviewCount": 1452, "centroid": [-8.2245, 39.3999], "trend": 0.08},
        {"iso": "MX", "avgRating": 4.0, "reviewCount": 2890, "centroid": [-102.5528, 23.6345], "trend": 0.04}
    ]

@app.post("/api/reviews", response_model=Review)
def create_review(payload: ReviewCreate):
    doc = payload.model_dump()
    doc["votes"] = 0
    inserted_id = create_document("review", doc)
    saved = db["review"].find_one({"_id": ObjectId(inserted_id)})
    return _oid_to_str(saved)

@app.get("/api/reviews", response_model=List[Review])
def list_reviews(country: Optional[str] = Query(None, description="ISO code filter"), limit: int = 20):
    filt = {"country_iso": country} if country else {}
    items = get_documents("review", filt, limit)
    items = [
        _oid_to_str({**i, "created_at": i.get("created_at", datetime.now(timezone.utc))}) for i in items
    ]
    return items

@app.post("/api/reviews/{review_id}/vote")
def vote_review(review_id: str, payload: VoteRequest):
    if db is None:
        raise HTTPException(500, "Database not configured")
    try:
        delta = 1 if payload.direction == "up" else -1
        res = db["review"].update_one({"_id": ObjectId(review_id)}, {"$inc": {"votes": delta}})
        if res.matched_count == 0:
            raise HTTPException(404, "Review not found")
        doc = db["review"].find_one({"_id": ObjectId(review_id)})
        return {"ok": True, "votes": doc.get("votes", 0)}
    except Exception:
        raise HTTPException(400, "Invalid ID")

@app.post("/api/reviews/{review_id}/flag")
def flag_review(review_id: str, payload: FlagRequest):
    if db is None:
        raise HTTPException(500, "Database not configured")
    try:
        db["flag"].insert_one({
            "review_id": review_id,
            "reason": payload.reason,
            "note": payload.note,
            "created_at": datetime.now(timezone.utc)
        })
        return {"ok": True}
    except Exception:
        raise HTTPException(400, "Invalid flag request")

@app.get("/api/og")
def og_image(country: str):
    # Placeholder: return a CDN style placeholder URL
    base = os.getenv("PLACEHOLDER_OG", "https://placehold.co/1200x630/png?text=")
    return {"image": f"{base}{country}+%7C+Rate+the+World"}

@app.get("/affiliate/booking")
def affiliate_booking(place: str, utm: Optional[str] = None):
    target = f"https://www.example.com/booking?place={place}&utm={utm or 'rtw'}"
    return RedirectResponse(url=target, status_code=302)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
