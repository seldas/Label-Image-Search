import base64
import io
import sys
import types
import unittest
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api_v1 import create_v1_router, rate_limiter  # noqa: E402
from app.config import settings  # noqa: E402


def _make_png_bytes(color: tuple[int, int, int] = (255, 0, 0), size: tuple[int, int] = (8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


class DummySearchEngine:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_value_error: str | None = None

    def search_stateless(
        self,
        text: str | None = None,
        image_bytes: bytes | None = None,
        use_ai: bool = True,
        top_k: int = 9,
        offset: int = 0,
        base_url: str = "",
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "text": text,
                "image_bytes": image_bytes,
                "use_ai": use_ai,
                "top_k": top_k,
                "offset": offset,
                "base_url": base_url,
            }
        )
        if self.raise_value_error:
            raise ValueError(self.raise_value_error)

        prefix = base_url.rstrip("/")
        return {
            "total": 1,
            "offset": offset,
            "top_k": top_k,
            "returned": 1,
            "ai_used": bool(use_ai and settings.vllm_enabled),
            "ai_reasoning": "Matched white oval tablet." if use_ai else "",
            "rewritten_query": text or "reference image",
            "items": [
                {
                    "image_id": "img_001",
                    "score": 0.032787,
                    "match_sources": ["visual_text", "fts_text"],
                    "thumb_url": f"{prefix}/api/images/img_001/thumb",
                    "orig_url": f"{prefix}/api/images/img_001/orig",
                    "width": 300,
                    "height": 300,
                    "setid": "set-123",
                    "product_name": "Sample Drug",
                    "generic_name": "sample",
                    "manufacturer": "Sample Labs",
                    "category": "human_rx",
                    "section_title": "HOW SUPPLIED",
                    "xml_context": "White oval tablet",
                    "vlm_caption": "",
                }
            ],
        }


class TestPublicApiV1(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_settings = {
            "api_keys": settings.api_keys,
            "api_require_key": settings.api_require_key,
            "api_rate_limit_rpm": settings.api_rate_limit_rpm,
            "api_max_upload_mb": settings.api_max_upload_mb,
            "api_max_top_k": settings.api_max_top_k,
            "public_base_url": settings.public_base_url,
            "vllm_enabled": settings.vllm_enabled,
        }
        settings.api_keys = "key_alpha, key_beta"
        settings.api_require_key = True
        settings.api_rate_limit_rpm = 60
        settings.api_max_upload_mb = 1
        settings.api_max_top_k = 50
        settings.public_base_url = ""
        settings.vllm_enabled = True

        rate_limiter.reset()
        self.engine = DummySearchEngine()
        app = FastAPI()
        app.include_router(create_v1_router(lambda: self.engine))
        self.client = TestClient(app)

    def tearDown(self) -> None:
        for attr, val in self._orig_settings.items():
            setattr(settings, attr, val)
        rate_limiter.reset()

    def test_missing_configured_keys_returns_503(self) -> None:
        settings.api_keys = ""
        resp = self.client.post("/api/v1/search", json={"text": "aspirin"})
        self.assertEqual(resp.status_code, 503)

    def test_missing_or_wrong_api_key_returns_401(self) -> None:
        r1 = self.client.post("/api/v1/search", json={"text": "aspirin"})
        self.assertEqual(r1.status_code, 401)

        r2 = self.client.post(
            "/api/v1/search",
            headers={"X-API-Key": "wrong_key"},
            json={"text": "aspirin"},
        )
        self.assertEqual(r2.status_code, 401)

    def test_auth_with_x_api_key_and_bearer(self) -> None:
        r1 = self.client.post(
            "/api/v1/search",
            headers={"X-API-Key": "key_alpha"},
            json={"text": "aspirin"},
        )
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.headers.get("X-Request-ID"))

        r2 = self.client.post(
            "/api/v1/search",
            headers={"Authorization": "Bearer key_beta"},
            json={"text": "ibuprofen"},
        )
        self.assertEqual(r2.status_code, 200)

    def test_require_key_false_allows_unauthenticated(self) -> None:
        settings.api_require_key = False
        settings.api_keys = ""
        resp = self.client.post("/api/v1/search", json={"text": "acetaminophen"})
        self.assertEqual(resp.status_code, 200)

    def test_json_base64_and_data_uri_image_search(self) -> None:
        png_bytes = _make_png_bytes()
        b64 = base64.b64encode(png_bytes).decode("ascii")

        r1 = self.client.post(
            "/api/v1/search",
            headers={"X-API-Key": "key_alpha"},
            json={"image_base64": b64, "top_k": 5, "offset": 2, "use_ai": False},
        )
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(self.engine.calls[-1]["image_bytes"], png_bytes)
        self.assertFalse(self.engine.calls[-1]["use_ai"])
        self.assertEqual(self.engine.calls[-1]["top_k"], 5)
        self.assertEqual(self.engine.calls[-1]["offset"], 2)

        data_uri = f"data:image/png;base64,{b64}"
        r2 = self.client.post(
            "/api/v1/search",
            headers={"X-API-Key": "key_alpha"},
            json={"text": "blue pill", "image_base64": data_uri},
        )
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(self.engine.calls[-1]["image_bytes"], png_bytes)
        self.assertEqual(self.engine.calls[-1]["text"], "blue pill")

    def test_json_validation_errors(self) -> None:
        r_empty = self.client.post(
            "/api/v1/search",
            headers={"X-API-Key": "key_alpha"},
            json={"text": "   "},
        )
        self.assertEqual(r_empty.status_code, 400)

        r_bad_b64 = self.client.post(
            "/api/v1/search",
            headers={"X-API-Key": "key_alpha"},
            json={"image_base64": "not-valid-base64!!!"},
        )
        self.assertEqual(r_bad_b64.status_code, 400)

        r_topk = self.client.post(
            "/api/v1/search",
            headers={"X-API-Key": "key_alpha"},
            json={"text": "pill", "top_k": 51},
        )
        self.assertEqual(r_topk.status_code, 400)

        oversized_b64 = base64.b64encode(b"x" * (1024 * 1024 + 100)).decode("ascii")
        r_big = self.client.post(
            "/api/v1/search",
            headers={"X-API-Key": "key_alpha"},
            json={"image_base64": oversized_b64},
        )
        self.assertEqual(r_big.status_code, 413)

    def test_multipart_upload_search_and_size_limit(self) -> None:
        png_bytes = _make_png_bytes()
        resp = self.client.post(
            "/api/v1/search/upload",
            headers={"X-API-Key": "key_alpha"},
            data={"text": "oval tablet", "top_k": "3", "offset": "1", "use_ai": "true"},
            files={"image": ("pill.png", png_bytes, "image/png")},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.engine.calls[-1]["image_bytes"], png_bytes)
        self.assertEqual(self.engine.calls[-1]["top_k"], 3)
        self.assertEqual(self.engine.calls[-1]["offset"], 1)

        too_large = b"0" * (1024 * 1024 + 16)
        r_big = self.client.post(
            "/api/v1/search/upload",
            headers={"X-API-Key": "key_alpha"},
            files={"image": ("huge.png", too_large, "image/png")},
        )
        self.assertEqual(r_big.status_code, 413)

    def test_rate_limit_returns_429_with_retry_after(self) -> None:
        settings.api_rate_limit_rpm = 2
        headers = {"X-API-Key": "key_alpha"}

        self.assertEqual(self.client.post("/api/v1/search", headers=headers, json={"text": "a"}).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/search", headers=headers, json={"text": "b"}).status_code, 200)
        r3 = self.client.post("/api/v1/search", headers=headers, json={"text": "c"})
        self.assertEqual(r3.status_code, 429)
        self.assertGreaterEqual(int(r3.headers["Retry-After"]), 1)

        # Separate key has its own bucket
        r_other = self.client.post(
            "/api/v1/search",
            headers={"X-API-Key": "key_beta"},
            json={"text": "d"},
        )
        self.assertEqual(r_other.status_code, 200)

    def test_base_url_resolution_public_and_forwarded_headers(self) -> None:
        r_fwd = self.client.post(
            "/api/v1/search",
            headers={
                "X-API-Key": "key_alpha",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "labels.example.org:8845",
            },
            json={"text": "pill"},
        )
        self.assertEqual(r_fwd.status_code, 200)
        item = r_fwd.json()["items"][0]
        self.assertEqual(item["thumb_url"], "https://labels.example.org:8845/api/images/img_001/thumb")
        self.assertEqual(item["orig_url"], "https://labels.example.org:8845/api/images/img_001/orig")

        settings.public_base_url = "http://10.0.0.5:8845/"
        r_pub = self.client.post(
            "/api/v1/search",
            headers={"X-API-Key": "key_alpha", "Host": "ignored.local"},
            json={"text": "pill"},
        )
        self.assertEqual(r_pub.status_code, 200)
        item_pub = r_pub.json()["items"][0]
        self.assertEqual(item_pub["thumb_url"], "http://10.0.0.5:8845/api/images/img_001/thumb")
        self.assertEqual(item_pub["orig_url"], "http://10.0.0.5:8845/api/images/img_001/orig")

    def test_engine_value_error_maps_to_400(self) -> None:
        self.engine.raise_value_error = "Invalid or unsupported image file."
        resp = self.client.post(
            "/api/v1/search",
            headers={"X-API-Key": "key_alpha"},
            json={"text": "pill"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid or unsupported image file.", resp.json()["detail"])


class TestHybridSearchEngineStateless(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_modules = {
            name: sys.modules.get(name)
            for name in (
                "app.db.postgres_store",
                "app.ml.embedder",
                "app.ml.vlm_client",
                "app.search.engine",
            )
        }

        class FakePostgresStore:
            def sync_db(self) -> None:
                pass

            def search_similar(self, vec: list[float], limit: int = 50) -> list[tuple[str, float]]:
                return [("img_a", 0.95), ("img_b", 0.85), ("img_c", 0.70)]

            def fts_search(self, query: str, limit: int = 50) -> list[tuple[str, float]]:
                return [("img_b", 0.90), ("img_c", 0.60)]

            def get_images_by_ids(self, image_ids: list[str]) -> dict[str, dict[str, Any]]:
                return {
                    img_id: {
                        "width": 200,
                        "height": 200,
                        "setid": f"set-{img_id}",
                        "product_name": f"Product {img_id}",
                        "generic_name": "generic",
                        "manufacturer": "Pharma Inc",
                        "category": "human_rx",
                        "section_title": "HOW SUPPLIED",
                        "xml_context": "Context",
                        "vlm_caption": "",
                    }
                    for img_id in image_ids
                }

        class FakeEmbedder:
            def embed_images(self, images: list[Any]) -> list[list[float]]:
                return [[0.1] * 4 for _ in images]

            def embed_text(self, text: str) -> list[float]:
                return [0.2] * 4

        class FakeVLMClient:
            def rewrite_query(self, text: str | None = None, image_bytes: bytes | None = None) -> dict[str, Any]:
                return {
                    "english_visual_query": text or "tablet",
                    "keywords": text or "tablet",
                    "reasoning": "Parsed query",
                }

        pg_mod = types.ModuleType("app.db.postgres_store")
        pg_mod.PostgresStore = FakePostgresStore  # type: ignore[attr-defined]
        emb_mod = types.ModuleType("app.ml.embedder")
        emb_mod.embedder = FakeEmbedder()  # type: ignore[attr-defined]
        vlm_mod = types.ModuleType("app.ml.vlm_client")
        vlm_mod.vlm_client = FakeVLMClient()  # type: ignore[attr-defined]

        sys.modules["app.db.postgres_store"] = pg_mod
        sys.modules["app.ml.embedder"] = emb_mod
        sys.modules["app.ml.vlm_client"] = vlm_mod
        sys.modules.pop("app.search.engine", None)

        from app.search.engine import HybridSearchEngine  # noqa: E402

        self.engine = HybridSearchEngine()

    def tearDown(self) -> None:
        for name, mod in self._saved_modules.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    def test_stateless_search_rrf_slicing_and_no_cache(self) -> None:
        png_bytes = _make_png_bytes()
        res = self.engine.search_stateless(
            text="white tablet",
            image_bytes=png_bytes,
            use_ai=True,
            top_k=2,
            offset=1,
            base_url="http://127.0.0.1:8845",
        )
        # No cache entries created
        self.assertEqual(len(self.engine._cache), 0)
        # RRF scores: img_b = 2/62 + 1/61 = 0.048651 (#1),
        #             img_c = 2/63 + 1/62 = 0.047875 (#2),
        #             img_a = 2/61        = 0.032787 (#3)
        # offset=1, top_k=2 -> [img_c, img_a]
        self.assertEqual(res["total"], 3)
        self.assertEqual(res["returned"], 2)
        self.assertEqual([item["image_id"] for item in res["items"]], ["img_c", "img_a"])
        self.assertEqual(
            res["items"][0]["thumb_url"],
            "http://127.0.0.1:8845/api/images/img_c/thumb",
        )
        self.assertEqual(
            res["items"][0]["orig_url"],
            "http://127.0.0.1:8845/api/images/img_c/orig",
        )

    def test_stateless_search_rejects_corrupt_image(self) -> None:
        with self.assertRaises(ValueError):
            self.engine.search_stateless(image_bytes=b"not-an-image", use_ai=False)


if __name__ == "__main__":
    unittest.main()
