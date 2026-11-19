import hashlib
import io
import logging
from pathlib import Path

import imagehash
from PIL import Image

from app.config import settings
from app.db.postgres_store import PostgresStore
from app.ingestion.downloader import DailyMedDownloader
from app.ingestion.spl_parser import ParsedSPL, iter_spl_archive
from app.ml.embedder import embedder
from app.ml.vlm_client import vlm_client

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """Orchestrates DailyMed full & weekly syncs into local SQLite, LanceDB, and image disk storage."""

    def __init__(self) -> None:
        settings.ensure_dirs()
        self.db = PostgresStore()
        self.db.sync_db()
        self.downloader = DailyMedDownloader()

    def _save_image_files(self, image_id: str, pil_img: Image.Image) -> tuple[str, str]:
        """Saves original (WebP/JPEG) and 300x300 WebP thumbnail sharded by image_id prefix."""
        shard = Path(image_id[:2]) / image_id[2:4]
        orig_rel = shard / f"{image_id}_orig.webp"
        thumb_rel = shard / f"{image_id}_thumb.webp"

        orig_abs = settings.images_dir / orig_rel
        thumb_abs = settings.images_dir / thumb_rel
        orig_abs.parent.mkdir(parents=True, exist_ok=True)

        rgb = pil_img.convert("RGB")
        if not orig_abs.exists():
            rgb.save(orig_abs, format="WEBP", quality=90)

        if not thumb_abs.exists():
            thumb = rgb.copy()
            thumb.thumbnail((300, 300), Image.Resampling.LANCZOS)
            thumb.save(thumb_abs, format="WEBP", quality=82)

        return orig_rel.as_posix(), thumb_rel.as_posix()

    def _process_zip_file(self, zip_path: Path, sync_type: str, category: str) -> None:
        filename = zip_path.name
        if self.db.is_filename_processed(filename):
            logger.info("Skipping already-processed archive: %s", filename)
            return

        log_id = self.db.start_sync_log(sync_type, filename)
        setids_count = 0
        images_added = 0
        pending_vectors: list[tuple[str, Image.Image]] = []

        try:
            for spl in iter_spl_archive(zip_path):
                setids_count += 1
                self.db.upsert_spl_document(
                    setid=spl.setid,
                    version_number=spl.version_number,
                    effective_time=spl.effective_time,
                    product_name=spl.product_name,
                    generic_name=spl.generic_name,
                    manufacturer=spl.manufacturer,
                    category=category,
                )

                for img in spl.images:
                    md5_hash = hashlib.md5(img.data).hexdigest()
                    existing_id = self.db.find_image_by_md5(md5_hash)

                    if existing_id:
                        self.db.insert_image_and_link(
                            image_id=existing_id,
                            md5_hash=md5_hash,
                            phash="",
                            width=0,
                            height=0,
                            orig_rel_path="",
                            thumb_rel_path="",
                            setid=spl.setid,
                            media_id=img.media_id,
                            section_title=img.section_title,
                            xml_context=img.xml_context,
                            product_name=spl.product_name,
                            generic_name=spl.generic_name,
                            manufacturer=spl.manufacturer,
                        )
                        continue

                    try:
                        pil_img = Image.open(io.BytesIO(img.data))
                        pil_img.load()
                    except Exception:
                        continue

                    w, h = pil_img.size
                    if w < settings.min_image_size or h < settings.min_image_size:
                        continue

                    phash_str = str(imagehash.phash(pil_img))
                    image_id = md5_hash[:20]
                    orig_rel, thumb_rel = self._save_image_files(image_id, pil_img)

                    vlm_caption = ""
                    if settings.ingest_vlm_caption:
                        ctx = f"{spl.product_name} ({spl.generic_name}) - {img.section_title}"
                        vlm_caption = vlm_client.caption_image(img.data, ctx)

                    is_new = self.db.insert_image_and_link(
                        image_id=image_id,
                        md5_hash=md5_hash,
                        phash=phash_str,
                        width=w,
                        height=h,
                        orig_rel_path=orig_rel,
                        thumb_rel_path=thumb_rel,
                        setid=spl.setid,
                        media_id=img.media_id,
                        section_title=img.section_title,
                        xml_context=img.xml_context,
                        product_name=spl.product_name,
                        generic_name=spl.generic_name,
                        manufacturer=spl.manufacturer,
                        vlm_caption=vlm_caption,
                    )
                    if is_new:
                        images_added += 1
                        pending_vectors.append((image_id, pil_img.copy()))

                    if len(pending_vectors) >= settings.embedding_batch_size:
                        self._flush_vectors(pending_vectors)
                        pending_vectors.clear()

            if pending_vectors:
                self._flush_vectors(pending_vectors)
                pending_vectors.clear()

            self.db.finish_sync_log(
                log_id,
                status="completed",
                setids_processed=setids_count,
                images_added=images_added,
            )
            logger.info(
                "Finished %s (%s): %d SetIDs, %d new images.",
                filename,
                sync_type,
                setids_count,
                images_added,
            )
        except Exception as exc:
            logger.exception("Failed processing %s: %s", filename, exc)
            self.db.finish_sync_log(
                log_id,
                status="failed",
                setids_processed=setids_count,
                images_added=images_added,
                error_message=str(exc),
            )
            raise
        finally:
            if settings.cleanup_downloads and zip_path.exists():
                zip_path.unlink(missing_ok=True)

    def _flush_vectors(self, batch: list[tuple[str, Image.Image]]) -> None:
        ids = [item[0] for item in batch]
        imgs = [item[1] for item in batch]
        vecs = embedder.embed_images(imgs)
        records = [{"image_id": img_id, "vector": vec} for img_id, vec in zip(ids, vecs)]
        self.db.upsert_vectors(records)

    def run_full_sync(self) -> None:
        logger.info("Starting FULL DailyMed sync for categories: %s", settings.categories_list)
        urls = self.downloader.get_full_release_urls(settings.categories_list)
        if not urls:
            raise RuntimeError("No full release DailyMed zip URLs found.")
        for url in urls:
            filename = url.rsplit("/", 1)[-1]
            if self.db.is_filename_processed(filename):
                logger.info("Skipping already-completed archive before download: %s", filename)
                continue
            cat = "human_rx" if "human_rx" in url.lower() else "human_otc"
            zip_path = self.downloader.download_file(url)
            self._process_zip_file(zip_path, sync_type="full", category=cat)

    def run_weekly_sync(self) -> None:
        logger.info("Starting WEEKLY DailyMed sync...")
        urls = self.downloader.get_latest_weekly_urls()
        if not urls:
            raise RuntimeError("No weekly update DailyMed zip URLs found.")
        for url in urls:
            filename = url.rsplit("/", 1)[-1]
            if self.db.is_filename_processed(filename):
                logger.info("Skipping already-completed weekly archive before download: %s", filename)
                continue
            zip_path = self.downloader.download_file(url)
            self._process_zip_file(zip_path, sync_type="weekly", category="weekly_update")
