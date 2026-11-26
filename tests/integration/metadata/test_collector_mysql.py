import pytest

from tests.integration.metadata.conftest import MysqlFixture
from text2sql.db.business import get_business_engine
from text2sql.metadata.collector.mysql import collect_snapshot

pytestmark = pytest.mark.integration


def test_collects_fixture_schema(mysql_fixture: MysqlFixture) -> None:
    data = collect_snapshot(get_business_engine(), mysql_fixture.datasource_id)

    expected_major_minor = {"mysql57": (5, 7), "mysql80": (8, 0)}[mysql_fixture.target]
    assert data.server.version[:2] == expected_major_minor
    assert data.database_name == "t2s_meta_fixture"
    assert data.server.sql_mode
    assert data.server.time_zone
    assert data.server.character_set == "utf8mb4"
    assert data.server.lower_case_table_names in (0, 1, 2)
    assert [t.name for t in data.tables] == [
        "t_customer",
        "t_work_order",
        "t_work_process",
        "v_open_orders",
    ]
    tables = {t.name: t for t in data.tables}

    customer = tables["t_customer"]
    assert customer.comment == "客户"
    assert [c.name for c in customer.columns] == ["id", "tenant_id", "code", "name"]
    assert {i.name: i.columns for i in customer.indexes} == {
        "PRIMARY": ("id",),
        "uk_tenant_code": ("tenant_id", "code"),
    }
    assert customer.columns[0].is_primary and customer.columns[0].is_unique
    assert not customer.columns[1].is_unique  # only part of uk_tenant_code

    order = tables["t_work_order"]
    columns = {c.name: c for c in order.columns}
    assert columns["order_no"].is_unique and not columns["order_no"].is_primary
    assert columns["status"].default_value == "open"
    assert columns["status"].comment_flags == ("INSTRUCTION_OVERRIDE",)
    assert columns["id"].extra == "auto_increment"
    assert columns["order_no"].data_type == "varchar"
    assert columns["order_no"].column_type == "varchar(32)"
    assert {i.name: i.is_unique for i in order.indexes} == {
        "PRIMARY": True,
        "ix_customer": False,
        "uk_order_no": True,
    }
    assert [(fk.name, fk.columns, fk.ref_table, fk.ref_columns) for fk in order.foreign_keys] == [
        ("fk_work_order_customer", ("customer_id",), "t_customer", ("id",))
    ]

    process = tables["t_work_process"]
    assert process.foreign_keys[0].on_delete == "CASCADE"
    assert {i.name: i.columns for i in process.indexes}["uk_order_step"] == (
        "work_order_id",
        "step_no",
    )

    view = tables["v_open_orders"]
    assert view.table_type == "VIEW"
    assert [c.name for c in view.columns] == ["id", "order_no"]
    assert view.view is not None
    # t2s_ro has SELECT only, no SHOW VIEW (Spec 02 4.2 step 7).
    assert view.view.definition_available is False
    assert view.view.definition == ""
