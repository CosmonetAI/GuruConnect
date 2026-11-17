"""Safe, consistent application logging for the API and durable worker."""

import logging
import os

from .error_alerts import ErrorAlertEmailHandler


def configure_logging(settings=None) -> None:
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
    if settings is None:
        try:
            from .config import Settings
            settings = Settings()
        except Exception:
            settings = None
    root = logging.getLogger()
    if settings and settings.error_alert_emails and not any(isinstance(item, ErrorAlertEmailHandler) for item in root.handlers):
        root.addHandler(ErrorAlertEmailHandler(settings))
