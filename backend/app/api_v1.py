import base64
import binascii
import hashlib
import math
import secrets
import threading
import time
import uuid
from collections import deque
from typing import Any, Callable

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


class SlidingWindowRateLimiter:
    """Thread-safe 60-second sliding-window rate limiter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = {}

    def check(self, identifier: str, limit_rpm: int) -> tuple[bool, int]:
        if limit_rpm <= 0:
            return True, 0

        now = time.monotonic()
        cutoff = now - 60.0

        with self._lock:
            if len(self._buckets) > 1024:
                stale_keys = [
                    k for k, dq in self._buckets.items() if not dq or dq[-1] <= cutoff
                ]
                for k in stale_keys:
                    self._buckets.pop(k, None)

            dq = self._buckets.setdefault(identifier, deque())
            while dq and dq[0] <= cutoff:
                dq.popleft()

            if len(dq) >= limit_rpm:
                retry_after = max(1, math.ceil(60.0 - (now - dq[0])))
                return False, retry_after

            dq.append(now)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


rate_limiter = SlidingWindowRateLimiter()


def verify_api_access(
    request: Request,
    x_api_key: str | None = Depends(api_key_header),
    bearer: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    candidate = (x_api_key or (bearer.credentials if bearer else "")).strip()

    if settings.api_require_key:
        configured_keys = settings.api_keys_set
        if not configured_keys:
            raise HTTPException(
                status_code=503,
                detail="Public API keys are not configured on the server.",
            )
        if not candidate or not any(
            secrets.compare_digest(candidate, k) for k in configured_keys
        ):
            raise HTTPException(status_code=401, detail="Invalid or missing API key.")
        bucket_key = f"key:{hashlib.sha256(candidate.encode()).hexdigest()[:16]}"
    else:
        host = request.client.host if request.client else "unknown"
        bucket_key = f"ip:{host}"

    allowed, retry_after = rate_limiter.check(bucket_key, settings.api_rate_limit_rpm)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )


def _resolve_base_url(request: Request) -> str:
    if settings.public_base_url.strip():
        return settings.public_base_url.strip().rstrip("/")
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    ).split(",")[0].strip()
    return f"{proto}://{host}".rstrip("/")


def _decode_base64_image(raw: str, max_bytes: int) -> bytes:
    payload = raw.strip()
    if payload.startswith("data:") and ";base64," in payload[:64]:
        payload = payload.split(";base64,", 1)[1]
    payload = "".join(payload.split())

    if len(payload) > int(max_bytes * 1.4) + 128:
        raise HTTPException(
            status_code=413,
            detail=f"Image exceeds maximum allowed size of {settings.api_max_upload_mb} MB.",
        )

    try:
        decoded = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="Invalid base64 image payload.") from exc

    if len(decoded) == 0:
        raise HTTPException(status_code=400, detail="Empty image payload.")
    if len(decoded) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Image exceeds maximum allowed size of {settings.api_max_upload_mb} MB.",
        )
    return decoded


async def _read_upload_limited(upload: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(min(1024 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Image exceeds maximum allowed size of {settings.api_max_upload_mb} MB.",
            )
    return b"".join(chunks)


class SearchV1JSONRequest(BaseModel):
    text: str | None = Field(
        default=None,
        max_length=2000,
        description="Text description, drug name, or imprint query.",
        json_schema_extra={"example": "white oval tablet scored L484"},
    )
    image_base64: str | None = Field(
        default=None,
        description="Optional raw base64 or data-URI encoded reference image.",
    )
    top_k: int = Field(default=9, ge=1, le=200, description="Number of results to return.")
    offset: int = Field(default=0, ge=0, le=500, description="Pagination offset into ranked hits.")
    use_ai: bool = Field(
        default=True,
        description="Whether to use intranet Gemma VLM for query rewriting/feature extraction.",
    )


class ImageHitV1(BaseModel):
    image_id: str
    score: float
    match_sources: list[str]
    thumb_url: str
    orig_url: str
    width: int
    height: int
    setid: str
    product_name: str
    generic_name: str
    manufacturer: str
    category: str
    section_title: str
    xml_context: str
    vlm_caption: str


class SearchV1Response(BaseModel):
    request_id: str
    total: int
    offset: int
    top_k: int
    returned: int
    ai_used: bool
    ai_reasoning: str
    rewritten_query: str
    items: list[ImageHitV1]


def create_v1_router(get_engine: Callable[[], Any]) -> APIRouter:
    """Builds the versioned public RESTful API router (/api/v1)."""
    router = APIRouter(prefix="/api/v1", tags=["Public API v1"])

    @router.post(
        "/search",
        response_model=SearchV1Response,
        dependencies=[Depends(verify_api_access)],
        summary="Search labeling images using JSON (text and/or base64 image)",
    )
    async def search_v1_json(
        body: SearchV1JSONRequest,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        if body.top_k > settings.api_max_top_k:
            raise HTTPException(
                status_code=400,
                detail=f"top_k must be <= {settings.api_max_top_k}.",
            )

        clean_text = body.text.strip() if body.text else None
        image_bytes = (
            _decode_base64_image(body.image_base64, settings.api_max_upload_bytes)
            if body.image_base64
            else None
        )

        if not clean_text and not image_bytes:
            raise HTTPException(status_code=400, detail="Provide at least text or image_base64.")

        try:
            result = await run_in_threadpool(
                get_engine().search_stateless,
                text=clean_text,
                image_bytes=image_bytes,
                use_ai=body.use_ai,
                top_k=body.top_k,
                offset=body.offset,
                base_url=_resolve_base_url(request),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        request_id = uuid.uuid4().hex[:12]
        response.headers["X-Request-ID"] = request_id
        return {"request_id": request_id, **result}

    @router.post(
        "/search/upload",
        response_model=SearchV1Response,
        dependencies=[Depends(verify_api_access)],
        summary="Search labeling images using multipart file upload and/or text",
    )
    async def search_v1_upload(
        request: Request,
        response: Response,
        text: str | None = Form(default=None),
        top_k: int = Form(default=9, ge=1, le=200),
        offset: int = Form(default=0, ge=0, le=500),
        use_ai: bool = Form(default=True),
        image: UploadFile | None = File(default=None),
    ) -> dict[str, Any]:
        if top_k > settings.api_max_top_k:
            raise HTTPException(
                status_code=400,
                detail=f"top_k must be <= {settings.api_max_top_k}.",
            )

        clean_text = text.strip() if text else None
        image_bytes: bytes | None = None
        if image is not None:
            raw = await _read_upload_limited(image, settings.api_max_upload_bytes)
            if raw:
                image_bytes = raw

        if not clean_text and not image_bytes:
            raise HTTPException(status_code=400, detail="Provide at least text or a non-empty image.")

        try:
            result = await run_in_threadpool(
                get_engine().search_stateless,
                text=clean_text,
                image_bytes=image_bytes,
                use_ai=use_ai,
                top_k=top_k,
                offset=offset,
                base_url=_resolve_base_url(request),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        request_id = uuid.uuid4().hex[:12]
        response.headers["X-Request-ID"] = request_id
        return {"request_id": request_id, **result}

    return router
