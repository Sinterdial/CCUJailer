"""
清理历史因税率/币种异常换算产生的失真价格变动记录。

Revision ID: 0004_cleanup_abnormal_changelogs
Revises: 0003_invite_codes
Create Date: 2026-11-24 23:36:00
"""

from collections import Counter, defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_cleanup_abnormal_changelogs"
down_revision: str | Sequence[str] | None = "0003_invite_codes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRICE_INCREMENT_CENTS = 500
BATCH_TAX_SHIFT_MIN_COUNT = 8
BATCH_TAX_SHIFT_RATIO_SHARE = 0.7
BATCH_NON_STEP_SHARE = 0.3
DELETE_CHUNK_SIZE = 200


def _is_standard_usd_price(price_cents: int | None) -> bool:
    """
    判断价格是否为合法的 $5 USD 整数倍（500 美分增量）。

    @param price_cents: 价格（美分）或 None
    @return: 若为大于 0 且整除 500 的正整数返回 True
    """
    return price_cents is not None and price_cents > 0 and price_cents % PRICE_INCREMENT_CENTS == 0


def upgrade() -> None:
    """
    清理非 $5 倍数以及同批次整体按统一非 1.0 比例（税率纠偏）回落的异常调价记录。
    """
    bind = op.get_bind()
    changelog_table = sa.table(
        "changelog",
        sa.column("id", sa.Integer()),
        sa.column("occurred_at", sa.DateTime(timezone=True)),
        sa.column("event_type", sa.String()),
        sa.column("old_price_cents", sa.Integer()),
        sa.column("new_price_cents", sa.Integer()),
    )
    rows = bind.execute(
        sa.select(
            changelog_table.c.id,
            changelog_table.c.occurred_at,
            changelog_table.c.old_price_cents,
            changelog_table.c.new_price_cents,
        ).where(changelog_table.c.event_type.in_(["store.price_changed", "ccu.price_changed"]))
    ).fetchall()

    ids_to_delete: set[int] = set()
    batches: dict[str, list[tuple[int, int, int]]] = defaultdict(list)

    for row in rows:
        row_id, occurred_at, old_price, new_price = int(row[0]), row[1], row[2], row[3]
        # 1. 单条调价只要任一侧缺失、非正数或不为 $5 整倍数即视为异常脏数据
        if not (_is_standard_usd_price(old_price) and _is_standard_usd_price(new_price)):
            ids_to_delete.add(row_id)
        if old_price is not None and new_price is not None and old_price > 0 and new_price > 0:
            bucket_key = str(occurred_at)[:19] if occurred_at is not None else ""
            batches[bucket_key].append((row_id, int(old_price), int(new_price)))

    # 2. 按秒级批次识别整批税率换算纠偏（如 1.1x/1.2x 回落中包含 $110->$100 等凑巧整倍条目）
    for items in batches.values():
        if len(items) < BATCH_TAX_SHIFT_MIN_COUNT:
            continue
        non_step = sum(
            1
            for _, old_p, new_p in items
            if not (_is_standard_usd_price(old_p) and _is_standard_usd_price(new_p))
        )
        ratio_counts = Counter(round((old_p / new_p) * 100) for _, old_p, new_p in items)
        dominant_ratio, dominant_count = ratio_counts.most_common(1)[0]
        is_non_step_batch = (non_step / len(items)) >= BATCH_NON_STEP_SHARE
        is_uniform_shift_batch = (
            dominant_ratio != 100 and (dominant_count / len(items)) >= BATCH_TAX_SHIFT_RATIO_SHARE
        )
        if is_non_step_batch or is_uniform_shift_batch:
            ids_to_delete.update(row_id for row_id, _, _ in items)

    sorted_ids = sorted(ids_to_delete)
    for start in range(0, len(sorted_ids), DELETE_CHUNK_SIZE):
        chunk = sorted_ids[start : start + DELETE_CHUNK_SIZE]
        bind.execute(sa.delete(changelog_table).where(changelog_table.c.id.in_(chunk)))


def downgrade() -> None:
    """
    异常调价脏数据清理不可逆，回滚不恢复已删除记录。
    """
