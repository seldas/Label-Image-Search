import io
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from app.config import settings
from app.db.postgres_store import PostgresStore
from app.ml.embedder import embedder
from app.ml.vlm_client import vlm_client


@dataclass
class RankedHit:
    image_id: str
    score: float
    sources: list[str] = field(default_factory=list)


@dataclass
class CachedSearch:
    search_id: str
    created_at: float
    ai_reasoning: str
    rewritten_query: str
    hits: list[RankedHit]


class HybridSearchEngine:
    """
    Offline-from-internet hybrid search:
    - Optional intranet vLLM query understanding (gemma-4-31b)
    - Local SigLIP image-to-image & text-to-image vector search (LanceDB)
    - Local BM25 full-text search (SQLite FTS5)
    - RRF fusion + in-memory session cache for instant 3x3 pagination
    """

    def __init__(self) -> None:
        settings.ensure_dirs()
        self.db = PostgresStore()
        self.db.sync_db()
        self._cache: dict[str, CachedSearch] = {}

    def _prune_cache(self) -> None:
        now = time.time()
        expired = [
            sid
            for sid, entry in self._cache.items()
            if now - entry.created_at > settings.search_cache_ttl_seconds
        ]
        for sid in expired:
            self._cache.pop(sid, None)

    @staticmethod
    def _rrf_fuse(ranked_lists: dict[str, list[tuple[str, float]]], k: int = 60) -> list[RankedHit]:
        scores: dict[str, float] = {}
        sources_map: dict[str, list[str]] = {}

        for source_name, items in ranked_lists.items():
            for rank_idx, (image_id, _) in enumerate(items, start=1):
                scores[image_id] = scores.get(image_id, 0.0) + 1.0 / (k + rank_idx)
                sources_map.setdefault(image_id, []).append(source_name)

        ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [
            RankedHit(image_id=img_id, score=round(score, 6), sources=sources_map[img_id])
            for img_id, score in ordered[: settings.search_max_results]
        ]

    def search(
        self,
        text: str | None = None,
        image_bytes: bytes | None = None,
        use_ai: bool = True,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        self._prune_cache()
        p_size = page_size or settings.page_size

        ai_info = (
            vlm_client.rewrite_query(text=text, image_bytes=image_bytes)
            if use_ai
            else {
                "english_visual_query": (text or "").strip(),
                "keywords": (text or "").strip(),
                "reasoning": "",
            }
        )

        ranked_lists: dict[str, list[tuple[str, float]]] = {}

        # 1. Image-to-Image vector search
        if image_bytes:
            pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            img_vec = embedder.embed_images([pil_img])[0]
            ranked_lists["visual_image"] = self.db.search_similar(img_vec, limit=60)

        # 2. Text-to-Image cross-modal vector search
        visual_query = ai_info["english_visual_query"]
        if visual_query:
            txt_vec = embedder.embed_text(visual_query)
            ranked_lists["visual_text"] = self.db.search_similar(txt_vec, limit=60)

        # 3. Full-text search (SPL metadata + XML context + VLM caption)
        kw_query = ai_info["keywords"] or (text or "")
        if kw_query:
            ranked_lists["fts_text"] = self.db.fts_search(kw_query, limit=60)

        fused_hits = self._rrf_fuse(ranked_lists)
        search_id = uuid.uuid4().hex[:12]
        self._cache[search_id] = CachedSearch(
            search_id=search_id,
            created_at=time.time(),
            ai_reasoning=ai_info.get("reasoning", ""),
            rewritten_query=visual_query,
            hits=fused_hits,
        )

        return self.get_page(search_id=search_id, page=1, page_size=p_size)

    def get_page(self, search_id: str, page: int = 1, page_size: int | None = None) -> dict[str, Any]:
        self._prune_cache()
        entry = self._cache.get(search_id)
        if not entry:
            raise KeyError("Search session expired or not found.")

        p_size = page_size or settings.page_size
        total = len(entry.hits)
        total_pages = max(1, (total + p_size - 1) // p_size)
        page = max(1, min(page, total_pages))

        start = (page - 1) * p_size
        end = start + p_size
        page_hits = entry.hits[start:end]

        meta_map = self.db.get_images_by_ids([h.image_id for h in page_hits])
        items: list[dict[str, Any]] = []
        for hit in page_hits:
            meta = meta_map.get(hit.image_id, {})
            items.append(
                {
                    "image_id": hit.image_id,
                    "score": hit.score,
                    "match_sources": hit.sources,
                    "thumb_url": f"/api/images/{hit.image_id}/thumb",
                    "orig_url": f"/api/images/{hit.image_id}/orig",
                    "width": meta.get("width", 0),
                    "height": meta.get("height", 0),
                    "setid": meta.get("setid", ""),
                    "product_name": meta.get("product_name", ""),
                    "generic_name": meta.get("generic_name", ""),
                    "manufacturer": meta.get("manufacturer", ""),
                    "category": meta.get("category", ""),
                    "section_title": meta.get("section_title", ""),
                    "xml_context": meta.get("xml_context", ""),
                    "vlm_caption": meta.get("vlm_caption", ""),
                }
            )

        return {
            "search_id": search_id,
            "page": page,
            "page_size": p_size,
            "total": total,
            "total_pages": total_pages,
            "ai_reasoning": entry.ai_reasoning,
            "rewritten_query": entry.rewritten_query,
            "items": items,
        }
