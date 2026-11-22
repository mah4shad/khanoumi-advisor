"""Beauty advisor API for a Khanoumi storefront pilot."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from .advisor import DEFAULT_MODEL, AdvisorError, new_session, reply  # noqa: E402
from .khanoumi import CatalogError, showcase  # noqa: E402

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

ROOT = Path(__file__).resolve().parent
SESSION_TTL = 2 * 60 * 60
MAX_SESSIONS = 500
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MIN", "12"))

SESSIONS: dict[str, dict] = {}
_sessions_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()

app = FastAPI(title="مشاور زیبایی خانومی", version="0.2.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

# Only needed when the widget is embedded on another domain (e.g. the store itself).
_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST"], allow_headers=["*"])


class Profile(BaseModel):
    skin_type: str = Field("", max_length=20)
    concerns: list[str] = Field(default_factory=list, max_length=8)
    budget: int | None = Field(None, ge=0, le=1_000_000_000)


class ChatIn(BaseModel):
    session_id: str | None = Field(None, max_length=64)
    message: str = Field(..., min_length=1, max_length=600)
    profile: Profile = Field(default_factory=Profile)
    compare_ids: list[str] = Field(default_factory=list, max_length=4)


def _client_ip(request: Request) -> str:
    if os.getenv("TRUST_PROXY") == "1":
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate(ip: str) -> None:
    """Sliding window per IP. Protects the OpenRouter key from being drained."""
    if RATE_LIMIT <= 0:
        return
    now = time.monotonic()
    with _hits_lock:
        window = _hits[ip]
        while window and window[0] < now - 60:
            window.popleft()
        if len(window) >= RATE_LIMIT:
            raise HTTPException(status_code=429, detail="درخواست‌ها زیاد است. یک دقیقه صبر کن و دوباره بپرس.")
        window.append(now)
        if len(_hits) > 5000:
            for key in [k for k, v in _hits.items() if not v]:
                _hits.pop(key, None)


def _get_session(session_id: str | None) -> dict:
    now = time.time()
    with _sessions_lock:
        for sid in [s for s, v in SESSIONS.items() if now - v.get("touched", now) > SESSION_TTL]:
            SESSIONS.pop(sid, None)
        session = SESSIONS.get(session_id or "")
        if session is None:
            session = new_session()
            SESSIONS[session["id"]] = session
            while len(SESSIONS) > MAX_SESSIONS:
                SESSIONS.pop(next(iter(SESSIONS)))
        session["touched"] = now
        return session


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "model": os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL),
        "has_key": bool(os.getenv("OPENROUTER_API_KEY", "").strip()),
    }


@app.get("/api/showcase")
def get_showcase() -> dict:
    try:
        products = showcase(50)
    except CatalogError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"count": len(products), "products": products}


@app.post("/api/chat")
def chat(body: ChatIn, request: Request) -> dict:
    _check_rate(_client_ip(request))
    session = _get_session(body.session_id)
    try:
        return reply(
            session,
            message=body.message,
            profile=body.profile.model_dump(),
            compare_ids=body.compare_ids,
        )
    except (AdvisorError, CatalogError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception:  # never leak internals to the browser
        logging.getLogger("advisor").exception("unexpected error")
        raise HTTPException(status_code=500, detail="خطای غیرمنتظره. دوباره تلاش کن.") from None
