import shutil
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import settings
from app.search.engine import HybridSearchEngine

app = FastAPI(title="Labeling Image Query API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = HybridSearchEngine()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
def status() -> dict:
    return {
        "vllm_enabled": settings.vllm_enabled,
        "vllm_model": settings.vllm_model,
        "embedding_model": settings.embedding_model,
        "page_size": settings.page_size,
        **engine.db.get_stats(),
    }


@app.post("/api/search")
async def search(
    text: str | None = Form(default=None),
    use_ai: bool = Form(default=True),
    image: UploadFile | None = File(default=None),
) -> dict:
    clean_text = text.strip() if text else None
    image_bytes = await image.read() if image else None

    if not clean_text and not image_bytes:
        raise HTTPException(status_code=400, detail="Provide at least text or an image.")

    return engine.search(text=clean_text, image_bytes=image_bytes, use_ai=use_ai)


@app.get("/api/search/{search_id}")
def get_search_page(
    search_id: str,
    page: int = Query(default=1, ge=1),
) -> dict:
    try:
        return engine.get_page(search_id=search_id, page=page)
    except KeyError:
        raise HTTPException(status_code=404, detail="Search session expired or not found.")


@app.get("/api/images/{image_id}/{variant}")
def get_image_file(image_id: str, variant: str) -> FileResponse:
    if variant not in {"thumb", "orig"}:
        raise HTTPException(status_code=400, detail="Variant must be 'thumb' or 'orig'.")

    meta = engine.db.get_images_by_ids([image_id]).get(image_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Image not found.")

    rel_path = meta["thumb_rel_path"] if variant == "thumb" else meta["orig_rel_path"]
    abs_path: Path = settings.images_dir / rel_path
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="Image file missing on disk.")

    return FileResponse(abs_path, media_type="image/webp")


def _require_admin(x_admin_token: str = Header(default="")) -> None:
    if settings.admin_token and x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Invalid or missing Admin Token.")


def _get_disk_usage_gb(path: Path) -> dict[str, float]:
    """Instant statvfs syscall (~0.01ms) — zero directory traversal."""
    usage = shutil.disk_usage(path if path.exists() else Path("/"))
    gb = 1024.0 ** 3
    return {
        "disk_used_gb": round(usage.used / gb, 2),
        "disk_free_gb": round(usage.free / gb, 2),
        "disk_total_gb": round(usage.total / gb, 2),
    }


class SyncTriggerRequest(BaseModel):
    sync_type: str = "weekly"


@app.get("/api/admin/overview", dependencies=[Depends(_require_admin)])
def admin_overview() -> dict:
    return {
        "stats": engine.db.get_stats(),
        "storage": {
            "db_mb": engine.db.get_db_size_mb(),
            **_get_disk_usage_gb(settings.data_dir),
        },
        "active_sync": engine.db.get_active_sync_status(),
        "sync_logs": engine.db.get_sync_logs(limit=20),
        "config": {
            "categories": settings.categories_list,
            "cron_schedule": f"{settings.sync_cron_day_of_week} {settings.sync_cron_hour:02d}:{settings.sync_cron_minute:02d} UTC",
            "vllm_enabled": settings.vllm_enabled,
            "vllm_base_url": settings.vllm_base_url,
            "vllm_model": settings.vllm_model,
            "embedding_model": settings.embedding_model,
            "auth_required": bool(settings.admin_token),
        },
    }


@app.post("/api/admin/sync", dependencies=[Depends(_require_admin)])
def admin_trigger_sync(body: SyncTriggerRequest) -> dict:
    if body.sync_type not in {"weekly", "full"}:
        raise HTTPException(status_code=400, detail="sync_type must be 'weekly' or 'full'.")
    try:
        req_id = engine.db.enqueue_sync_request(body.sync_type)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"request_id": req_id, "sync_type": body.sync_type, "status": "pending"}
