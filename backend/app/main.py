"""App entry point: `uvicorn backend.app.main:app` (see run.sh)."""
import asyncio
import base64
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db, worker
from .api import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    task = asyncio.create_task(worker.run_forever()) if config.WORKER_ENABLED else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Dr.Tracker Outreach", lifespan=lifespan)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if not config.APP_PASSWORD:
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            _, _, password = base64.b64decode(header[6:]).decode().partition(":")
            if secrets.compare_digest(password, config.APP_PASSWORD):
                return await call_next(request)
        except Exception:
            pass
    return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Dr.Tracker Outreach"'})


app.include_router(router)
app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


@app.get("/healthz")
def healthz():
    return {"ok": True}
