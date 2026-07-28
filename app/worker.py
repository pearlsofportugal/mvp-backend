"""Cloud Run Job entrypoint for long-running scrape work."""
import argparse
import asyncio

from app.services.scraper_service import run_scrape_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one persisted scraper job")
    parser.add_argument("--scrape-job-id", required=True)
    args = parser.parse_args()
    asyncio.run(run_scrape_job(args.scrape_job_id))


if __name__ == "__main__":
    main()
