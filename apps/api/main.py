"""API entry point: `uv run uvicorn apps.api.main:app --no-access-log`."""

from text2sql.api.app import create_app
from text2sql.common.config import get_settings
from text2sql.common.logging import configure_logging

configure_logging(get_settings().log_level)
app = create_app()
