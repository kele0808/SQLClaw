"""Read-only MySQL access with the t2s_ro account.

Only for connectivity checks and metadata collection (Spec 02). User queries
never use this engine; they go through the gateway with tenant accounts
(Spec 07).
"""

import logging
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from text2sql.common.config import get_settings

logger = logging.getLogger(__name__)

READ_ONLY_INIT_COMMAND = "SET SESSION TRANSACTION READ ONLY"


def business_connect_args(timeout_s: int) -> dict[str, Any]:
    return {
        "connect_timeout": timeout_s,
        "read_timeout": timeout_s,
        "init_command": READ_ONLY_INIT_COMMAND,
    }


@lru_cache(maxsize=1)
def get_business_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.business_db_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args=business_connect_args(settings.business_db_timeout_s),
    )


def ping() -> bool:
    try:
        with get_business_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("business_db_ping_failed", exc_info=True)
        return False
    return True


def server_version() -> str:
    with get_business_engine().connect() as conn:
        return str(conn.execute(text("SELECT VERSION()")).scalar_one())
