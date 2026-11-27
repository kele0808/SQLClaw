"""Stable object identities across snapshots (Spec 02 M4, architecture 4.3).

A natural key (`t_order`, `t_order.status`) keeps its `object_uuid` while it
appears in consecutive snapshots. When a key disappears its identity is retired;
an object recreated later under the same name gets a new UUID.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from text2sql.metadata.catalog.models import MetadataObjectIdentity

# (object_type, object_key); object_type is "TABLE" or "COLUMN".
IdentityKey = tuple[str, str]


def column_key(table: str, column: str) -> str:
    return f"{table}.{column}"


def assign_identities(
    session: Session, *, datasource_id: str, snapshot_id: int, keys: Iterable[IdentityKey]
) -> dict[IdentityKey, uuid.UUID]:
    """Reuse, create, or retire identities for one new snapshot; caller owns the transaction."""
    wanted = set(keys)
    active = session.scalars(
        select(MetadataObjectIdentity)
        .where(
            MetadataObjectIdentity.datasource_id == datasource_id,
            MetadataObjectIdentity.retired_at_snapshot_id.is_(None),
        )
        .with_for_update()
    ).all()
    by_key: dict[IdentityKey, MetadataObjectIdentity] = {
        (row.object_type, row.object_key): row for row in active
    }

    result: dict[IdentityKey, uuid.UUID] = {}
    seen_uuids: list[uuid.UUID] = []
    retired_uuids: list[uuid.UUID] = []
    for key, row in by_key.items():
        if key in wanted:
            result[key] = row.object_uuid
            seen_uuids.append(row.object_uuid)
        else:
            retired_uuids.append(row.object_uuid)

    if seen_uuids:
        session.execute(
            update(MetadataObjectIdentity)
            .where(MetadataObjectIdentity.object_uuid.in_(seen_uuids))
            .values(last_seen_snapshot_id=snapshot_id)
        )
    if retired_uuids:
        session.execute(
            update(MetadataObjectIdentity)
            .where(MetadataObjectIdentity.object_uuid.in_(retired_uuids))
            .values(retired_at_snapshot_id=snapshot_id)
        )
    for object_type, object_key in sorted(wanted - by_key.keys()):
        new_uuid = uuid.uuid4()
        session.add(
            MetadataObjectIdentity(
                object_uuid=new_uuid,
                datasource_id=datasource_id,
                object_type=object_type,
                object_key=object_key,
                first_snapshot_id=snapshot_id,
                last_seen_snapshot_id=snapshot_id,
            )
        )
        result[(object_type, object_key)] = new_uuid
    session.flush()
    return result
