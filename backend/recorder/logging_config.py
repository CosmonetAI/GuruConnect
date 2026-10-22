"""Safe, consistent application logging for the API and durable worker."""

import logging
import os


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=False,
    )
    # Provider clients can emit request headers or response bodies. Keep those
    # libraries quiet; application events below remain available in Render logs.
    for name in ("httpx", "httpcore", "psycopg.pool"):
        logging.getLogger(name).setLevel(logging.WARNING)
