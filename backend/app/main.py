"""App entry point: `uvicorn backend.app.main:app` (see run.sh and the Dockerfile)."""
import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from fastapi.staticfiles import StaticFiles

from . import auth, config, db, settings_store, worker
from .api import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("outreach")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    auth.startup_checks()  # raises (and uvicorn exits) on an unsafe configuration
    for section in ("ai", "email"):
        if fields := settings_store.env_overrides(section):  # raises on a malformed value in .env
            log.info("Using %s from the environment / .env: %s", section, ", ".join(sorted(fields)))
    with db.connect() as conn:
        for w in auth.warnings(conn):
            log.warning(w)
    task = asyncio.create_task(worker.run_forever()) if config.WORKER_ENABLED else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Dr.Tracker Outreach", lifespan=lifespan)

# Reachable without signing in: the page shell and its assets (no data in them), the health check,
# and the endpoints the sign-in screen needs.
PUBLIC_PATHS = {"/", "/healthz", "/api/auth/state", "/api/auth/login", "/api/auth/logout"}
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    return urlsplit(origin).netloc == request.headers.get("host", "")


@app.middleware("http")
async def authenticate(request: Request, call_next):
    path = request.url.path
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_origin(request):
        return JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)
    with db.connect() as conn:
        user = auth.user_for_token(conn, request.cookies.get(auth.COOKIE))
        users_exist = user is not None or auth.user_count(conn) > 0
    if user is None:
        if users_exist:
            return JSONResponse({"detail": "Sign in first."}, status_code=401)
        if (request.client.host if request.client else "") not in LOOPBACK:
            return JSONResponse({"detail": "No user accounts exist yet. Create one with: "
                                           "python -m backend.app.cli create-admin"}, status_code=401)
    request.state.user = user
    token = db.set_actor(user["email"] if user else None)
    try:
        return await call_next(request)
    finally:
        db._actor.reset(token)


app.include_router(router)
app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


@app.get("/healthz")
def healthz():
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    return {"ok": True}
