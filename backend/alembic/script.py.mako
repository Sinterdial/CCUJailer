"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """
    执行数据库结构向上迁移。
    """
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """
    执行数据库结构回滚迁移。
    """
    ${downgrades if downgrades else "pass"}
