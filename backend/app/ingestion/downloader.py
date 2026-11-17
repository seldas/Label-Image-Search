import logging
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class DailyMedDownloader:
    """Discovers and downloads DailyMed full & weekly SPL bulk zip archives."""

    def __init__(self, base_url: str | None = None, download_dir: Path | None = None) -> None:
        self.base_url = (base_url or settings.dailymed_base_url).rstrip("/") + "/"
        self.download_dir = download_dir or settings.downloads_dir
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def list_available_zips(self) -> list[str]:
        """Fetches the DailyMed public-release-files index and returns all .zip filenames."""
        logger.info("Fetching DailyMed release index from %s", self.base_url)
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            resp = client.get(self.base_url)
            resp.raise_for_status()
        # Match href="dm_spl_release_....zip"
        matches = re.findall(r'href=["\']?(dm_spl_release_[^"\'>\s]+\.zip)["\']?', resp.text, re.I)
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for m in matches:
            if m not in seen:
                seen.add(m)
                unique.append(m)
        return unique

    def get_full_release_files(self, categories: list[str]) -> list[str]:
        """Returns full release zip filenames for requested categories (e.g. human_rx, human_otc)."""
        all_zips = self.list_available_zips()
        selected: list[str] = []
        for cat in categories:
            # Full release files look like dm_spl_release_human_rx_part1.zip or dm_spl_release_human_otc.zip
            pattern = re.compile(rf"^dm_spl_release_{re.escape(cat)}(?:_part\d+)?\.zip$", re.I)
            matched = sorted(f for f in all_zips if pattern.match(f))
            selected.extend(matched)
        return selected

    def get_latest_weekly_files(self, categories: list[str]) -> list[str]:
        """Returns the latest weekly update zip filename for each requested category."""
        all_zips = self.list_available_zips()
        selected: list[str] = []
        for cat in categories:
            # Weekly files look like dm_spl_release_human_rx_weekly_20261110.zip or similar
            pattern = re.compile(rf"^dm_spl_release_{re.escape(cat)}.*weekly.*\.zip$", re.I)
            matched = sorted(f for f in all_zips if pattern.match(f))
            if matched:
                selected.append(matched[-1])
        return selected

    def download_file(self, filename: str) -> Path:
        """Streams a DailyMed zip file to local disk with .part temp file."""
        target = self.download_dir / filename
        if target.exists() and target.stat().st_size > 0:
            logger.info("File already exists locally: %s", target)
            return target

        url = urljoin(self.base_url, filename)
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
