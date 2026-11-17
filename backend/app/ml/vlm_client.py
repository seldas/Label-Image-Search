import base64
import json
import logging
from typing import Any

import httpx
from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)


def _encode_image_b64(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


class VLMClient:
    """Client for intranet vLLM OpenAI-compatible API (e.g., gemma-4-31b)."""

    def __init__(self) -> None:
        self.enabled = settings.vllm_enabled
        self.model = settings.vllm_model
        self._client = (
            OpenAI(
                base_url=settings.vllm_base_url,
                api_key=settings.vllm_api_key,
                timeout=settings.vllm_timeout,
                max_retries=settings.vllm_max_retries,
                http_client=httpx.Client(verify=settings.vllm_verify_ssl, timeout=settings.vllm_timeout),
            )
            if self.enabled
            else None
        )

    def rewrite_query(
        self,
        text: str | None = None,
        image_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        """
        Translates/expands user query (text and/or image) into:
        - english_visual_query: concise English description for SigLIP text-to-image search
        - keywords: pharmaceutical/imprint/drug name terms for BM25 FTS5
        """
        fallback = {
            "english_visual_query": (text or "").strip(),
            "keywords": (text or "").strip(),
            "reasoning": "VLM disabled or unavailable; using raw query.",
        }
        if not self._client or (not text and not image_bytes):
            return fallback

        system_prompt = (
            "You are a pharmaceutical labeling image search assistant. "
            "Given a user's text description and/or reference image (pill, tablet, capsule, "
            "drug packaging, or usage diagram), extract:\n"
            "1. 'english_visual_query': a concise English visual description suitable for CLIP/SigLIP "
            "(include color, shape, dosage form, packaging style, diagram type).\n"
            "2. 'keywords': space-separated English keywords for BM25 search (drug names, imprint codes, "
            "manufacturer, dosage form).\n"
            "3. 'reasoning': brief 1-sentence summary of what the user is looking for.\n"
            "Respond ONLY with valid JSON."
        )

        content: list[dict[str, Any]] = []
        if text:
            content.append({"type": "text", "text": f"User query: {text}"})
        if image_bytes:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _encode_image_b64(image_bytes)},
                }
            )

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                temperature=settings.vllm_temperature,
                max_tokens=settings.vllm_query_max_tokens,
            )
            raw = (resp.choices[0].message.content or "").strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.startswith("json"):
                    raw = raw[4:].strip()
            parsed = json.loads(raw)
            return {
                "english_visual_query": parsed.get("english_visual_query") or fallback["english_visual_query"],
                "keywords": parsed.get("keywords") or fallback["keywords"],
                "reasoning": parsed.get("reasoning", ""),
            }
        except Exception as exc:
            logger.warning("vLLM rewrite_query failed, using fallback: %s", exc)
            return fallback

    def caption_image(self, image_bytes: bytes, drug_context: str = "") -> str:
        """Generates a structured English visual caption for background ingestion."""
        if not self._client:
            return ""
        prompt = (
            "Describe this pharmaceutical labeling image in 1-2 concise English sentences. "
            "Mention image type (tablet/capsule/bottle/box/diagram/table), colors, shape, "
            "visible imprint codes, or drug name if legible."
        )
        if drug_context:
            prompt += f"\nContext from SPL label: {drug_context[:300]}"

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": _encode_image_b64(image_bytes)}},
                        ],
                    }
                ],
                temperature=settings.vllm_temperature,
                max_tokens=settings.vllm_caption_max_tokens,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.warning("vLLM caption_image failed: %s", exc)
            return ""


vlm_client = VLMClient()
