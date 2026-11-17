import logging
from threading import Lock
from typing import Sequence

import numpy as np
import torch
from PIL import Image
from transformers import AutoModel, AutoProcessor

from app.config import settings

logger = logging.getLogger(__name__)


class SigLIPEmbedder:
    """CPU-friendly lazy-loaded SigLIP embedder for multimodal image/text retrieval."""

    def __init__(self, model_name_or_path: str | None = None) -> None:
        self.model_name = model_name_or_path or settings.embedding_model
        self._lock = Lock()
        self._model = None
        self._processor = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            logger.info("Loading SigLIP model on CPU: %s", self.model_name)
            self._processor = AutoProcessor.from_pretrained(self.model_name)
            self._model = AutoModel.from_pretrained(self.model_name).to("cpu")
            self._model.eval()
            logger.info("SigLIP model loaded successfully.")

    @staticmethod
    def _l2_normalize(vecs: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(vecs, axis=-1, keepdims=True)
        norms = np.maximum(norms, 1e-12)
        return vecs / norms

    @torch.inference_mode()
    def embed_images(self, images: Sequence[Image.Image]) -> list[list[float]]:
        if not images:
            return []
        self._ensure_loaded()
        rgb_images = [img.convert("RGB") for img in images]
        inputs = self._processor(images=rgb_images, return_tensors="pt", padding=True)
        features = self._model.get_image_features(**inputs)
        vecs = self._l2_normalize(features.cpu().numpy().astype(np.float32))
        return vecs.tolist()

    @torch.inference_mode()
    def embed_text(self, text: str) -> list[float]:
        self._ensure_loaded()
        # SigLIP uses padding="max_length" per official recommendation
        inputs = self._processor(
            text=[text],
            return_tensors="pt",
            padding="max_length",
            truncation=True,
        )
        features = self._model.get_text_features(**inputs)
        vecs = self._l2_normalize(features.cpu().numpy().astype(np.float32))
        return vecs[0].tolist()


embedder = SigLIPEmbedder()
