"""Store versioned requirements and link analyses to their input version."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conversation = op.create_table(
        "conversation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.bulk_insert(conversation, [{"id": 1, "revision": 0}])
    op.create_table(
        "requirements_versions",
        sa.Column("version", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("next_requirement_number", sa.Integer(), nullable=False),
    )
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("requirements_version", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_messages_requirements_version", "requirements_versions",
            ["requirements_version"], ["version"],
        )


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.drop_constraint("fk_messages_requirements_version", type_="foreignkey")
        batch.drop_column("requirements_version")
    op.drop_table("requirements_versions")
    op.drop_table("conversation")
