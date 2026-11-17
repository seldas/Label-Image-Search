import logging
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class DailyMedDownloader:
    """Discovers and downloads DailyMed full & weekly SPL bulk zip archives."""

    def __init__(
        self,
        index_url: str | None = None,
        base_url: str | None = None,
        download_dir: Path | None = None,
    ) -> None:
        self.index_url = index_url or settings.dailymed_index_url
        self.base_url = (base_url or settings.dailymed_base_url).rstrip("/") + "/"
        self.download_dir = download_dir or settings.downloads_dir
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def list_available_zip_urls(self) -> list[str]:
        """Fetches the DailyMed SPL resources page and returns all HTTPS .zip download URLs."""
        logger.info("Fetching DailyMed release index from %s", self.index_url)
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            resp = client.get(self.index_url)
            resp.raise_for_status()

        matches = re.findall(r'https?://[^\s"\'<>]+?/dm_spl_[^\s"\'<>]+\.zip', resp.text, re.I)
        seen: set[str] = set()
        unique: list[str] = []
        for url in matches:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        return unique

    def get_full_release_urls(self, categories: list[str]) -> list[str]:
        """Returns full release zip URLs for requested categories (e.g. human_rx, human_otc)."""
        all_urls = self.list_available_zip_urls()
        selected: list[str] = []

        def _part_sort_key(url: str) -> tuple[str, int]:
            fname = url.rsplit("/", 1)[-1].lower()
            m = re.search(r"_part(\d+)\.zip$", fname)
            return (re.sub(r"_part\d+\.zip$", "", fname), int(m.group(1)) if m else 0)

        for cat in categories:
            pattern = re.compile(rf"/dm_spl_release_{re.escape(cat)}(?:_part\d+)?\.zip$", re.I)
            matched = sorted((u for u in all_urls if pattern.search(u)), key=_part_sort_key)
            selected.extend(matched)
        return selected

    def get_latest_weekly_urls(self) -> list[str]:
        """
        Returns the latest unified weekly update zip URL
        (e.g. dm_spl_weekly_update_110926_111326.zip).
        """
        all_urls = self.list_available_zip_urls()
        weekly_pattern = re.compile(r"/dm_spl_weekly_update_(\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})\.zip$", re.I)

        def _weekly_sort_key(url: str) -> tuple[int, int, int]:
            m = weekly_pattern.search(url)
            if not m:
                return (0, 0, 0)
            # Sort by end date MMDDYY -> (YY, MM, DD)
            mm, dd, yy = int(m.group(4)), int(m.group(5)), int(m.group(6))
            return (yy, mm, dd)

        matched = sorted((u for u in all_urls if weekly_pattern.search(u)), key=_weekly_sort_key)
        return [matched[-1]] if matched else []

    def download_file(self, url_or_filename: str) -> Path:
        """Streams a DailyMed zip file to local disk with .part temp file."""
        if url_or_filename.startswith("http://") or url_or_filename.startswith("https://"):
            url = url_or_filename
            filename = url.rsplit("/", 1)[-1]
        else:
            filename = url_or_filename
            url = urljoin(self.base_url, filename)

        target = self.download_dir / filename
        if target.exists() and target.stat().st_size > 0:
            logger.info("File already exists locally: %s", target)
            return target

        tmp_path = self.download_dir / f"{filename}.part"
        logger.info("Downloading %s -> %s", url, target)

        with httpx.Client(timeout=httpx.Timeout(30.0, read=300.0), follow_redirects=True) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)

        tmp_path.rename(target)
        logger.info("Download completed: %s (%.2f MB)", target, target.stat().st_size / (1024 * 1024))
        return target
