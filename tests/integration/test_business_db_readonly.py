import pymysql
import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError

from text2sql.db import business

pytestmark = pytest.mark.integration


def test_ping_and_server_version(mysql_url: str, mysql_target: str) -> None:
    assert business.ping() is True
    expected_prefix = {"mysql57": "5.7.", "mysql80": "8.0."}[mysql_target]
    assert business.server_version().startswith(expected_prefix)


def test_session_is_read_only(mysql_url: str) -> None:
    with business.get_business_engine().connect() as conn:
        flag = conn.execute(text("SELECT @@SESSION.transaction_read_only")).scalar_one()
    assert int(flag) == 1


def test_write_through_engine_fails(mysql_url: str) -> None:
    with pytest.raises(DBAPIError), business.get_business_engine().begin() as conn:
        conn.execute(text("INSERT INTO _t2s_probe (id, note) VALUES (2, 'x')"))


def test_write_without_read_only_session_is_denied_by_grants(mysql_url: str) -> None:
    url = make_url(mysql_url)
    conn = pymysql.connect(
        host=url.host or "127.0.0.1",
        port=url.port or 3306,
        user=url.username or "",
        password=url.password or "",
        database=url.database,
    )
    try:
        with pytest.raises(pymysql.err.MySQLError) as excinfo, conn.cursor() as cursor:
            cursor.execute("INSERT INTO _t2s_probe (id, note) VALUES (3, 'y')")
        assert excinfo.value.args[0] == 1142  # INSERT command denied
    finally:
        conn.close()
