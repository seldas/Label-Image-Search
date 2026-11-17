from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Storage paths & PostgreSQL
    data_dir: Path = Path("/app/data")
    images_dir: Path = Path("/app/data/images")
    downloads_dir: Path = Path("/app/data/downloads")
    database_url: str = "postgresql://labeling:labeling_pass@postgres:5432/labeling_db"

    # Intranet vLLM (gemma-4-31b)
    vllm_base_url: str = "http://192.168.1.100:8000/v1"
    vllm_api_key: str = "EMPTY"
    vllm_model: str = "gemma-4-31b"
    vllm_timeout: int = 60
    vllm_max_retries: int = 2
    vllm_temperature: float = 0.1
    vllm_query_max_tokens: int = 300
    vllm_caption_max_tokens: int = 150
    vllm_verify_ssl: bool = True
    vllm_enabled: bool = True

    # Local CPU embedding model
    embedding_model: str = "google/siglip-base-patch16-224"
    embedding_dim: int = 768
    embedding_batch_size: int = 16

    # DailyMed sync schedule
    dailymed_index_url: str = "https://dailymed.nlm.nih.gov/dailymed/spl-resources-all-drug-labels.cfm"
    dailymed_base_url: str = "https://dailymed-data.nlm.nih.gov/public-release-files"
    sync_cron_day_of_week: str = "sun"
    sync_cron_hour: int = 2
    sync_cron_minute: int = 0
    dailymed_categories: str = "human_rx,human_otc"
    cleanup_downloads: bool = True
    min_image_size: int = 64
    ingest_vlm_caption: bool = False

    # Search & pagination
    search_cache_ttl_seconds: int = 3600
    search_max_results: int = 90
    page_size: int = 9

    # Admin Dashboard (leave empty to disable token auth in trusted intranet)
    admin_token: str = ""

    @property
    def categories_list(self) -> list[str]:
        return [c.strip().lower() for c in self.dailymed_categories.split(",") if c.strip()]

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
