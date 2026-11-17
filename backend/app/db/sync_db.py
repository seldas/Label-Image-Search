import logging

from app.db.postgres_store import PostgresStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sync_db")


def main() -> None:
    logger.info("Running PostgreSQL schema & column migration (sync_db)...")
    store = PostgresStore()
    store.sync_db()
    logger.info("sync_db completed.")


if __name__ == "__main__":
    main()
