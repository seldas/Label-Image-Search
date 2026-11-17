import argparse
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.ingestion.pipeline import IngestionPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("worker")


def main() -> None:
    parser = argparse.ArgumentParser(description="DailyMed SPL Sync Worker")
    parser.add_argument(
        "--run-once",
        choices=["full", "weekly"],
        default=None,
        help="Run a single sync immediately and exit.",
    )
    args = parser.parse_args()

    pipeline = IngestionPipeline()

    if args.run_once == "full":
        pipeline.run_full_sync()
        return
    if args.run_once == "weekly":
        pipeline.run_weekly_sync()
        return

    # Automatic initial full sync on first run
    if pipeline.db.is_initial_sync_needed():
        logger.info("No completed full sync found. Running initial full DailyMed sync...")
        pipeline.run_full_sync()

    scheduler = BlockingScheduler(timezone="UTC")
    trigger = CronTrigger(
        day_of_week=settings.sync_cron_day_of_week,
        hour=settings.sync_cron_hour,
        minute=settings.sync_cron_minute,
        timezone="UTC",
    )
    scheduler.add_job(pipeline.run_weekly_sync, trigger=trigger, id="dailymed_weekly_sync", max_instances=1)

    def poll_manual_sync() -> None:
        req = pipeline.db.claim_next_sync_request()
        if not req:
            return
        req_id = req["id"]
        sync_type = req["sync_type"]
        logger.info("Picked up manual sync request id=%d (type=%s)", req_id, sync_type)
        try:
            if sync_type == "full":
                pipeline.run_full_sync()
            else:
                pipeline.run_weekly_sync()
            pipeline.db.complete_sync_request(req_id, "completed", "Sync finished successfully.")
        except Exception as exc:
            logger.exception("Manual sync request id=%d failed: %s", req_id, exc)
            pipeline.db.complete_sync_request(req_id, "failed", str(exc))

    scheduler.add_job(poll_manual_sync, "interval", seconds=5, id="manual_sync_poller", max_instances=1)

    logger.info(
        "Weekly sync scheduler started (UTC cron: day=%s %02d:%02d) + manual sync poller.",
        settings.sync_cron_day_of_week,
        settings.sync_cron_hour,
        settings.sync_cron_minute,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
