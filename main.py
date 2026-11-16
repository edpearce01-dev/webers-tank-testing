"""
Weber's Tank Testing (DOT RIN B386) — Modular FastAPI Entrypoint (v14.0)
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from database import init_db
from services.core_ops import (
    STATIC_DIR,
    background_sheets_sync_loop,
    get_current_session_user,
    is_tv_authorized,
)
from routes import office_admin, ops_floor, pages_auth, public_portal, schedule_day


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(background_sheets_sync_loop())
    yield
    task.cancel()


app = FastAPI(title="Weber's Tank Testing - DOT RIN B386", lifespan=lifespan)


@app.middleware("http")
async def auth_gate_middleware(request: Request, call_next):
    if request.url.path in ("/merch-go", "/api/public/hours", "/api/public/iphone-sms-bridge", "/api/public/iphone-sms-bridge/ack"):
        return await call_next(request)
    host = (request.headers.get("host") or "").split(":")[0].lower()
    path = request.url.path

    if path == "/" and host in ("weberstanktesting.com", "www.weberstanktesting.com"):
        return FileResponse(STATIC_DIR / "landing.html")

    public_paths = {
        "/", "/ops", "/schedule", "/office", "/tv", "/manifest.json", "/merch", "/sw.js", "/icon.svg", "/pwa-icon.png", "/apple-touch-icon.png",
        "/api/auth/login", "/api/auth/status", "/api/auth/logout",
        "/api/public/dropoff", "/api/public/track", "/api/public/turnaround", "/api/public/theme",
    }
    if path in public_paths or path.startswith("/static") or path.startswith("/api/public/receipt/"):
        return await call_next(request)

    if path == "/api/tv" and is_tv_authorized(request):
        return await call_next(request)

    user = get_current_session_user(request)
    if not user:
        return JSONResponse({"detail": "Daily session expired at midnight — please enter your PIN."}, status_code=401)
    request.state.user = user
    return await call_next(request)


app.include_router(pages_auth.router)
app.include_router(public_portal.router)
app.include_router(ops_floor.router)
app.include_router(schedule_day.router)
app.include_router(office_admin.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
