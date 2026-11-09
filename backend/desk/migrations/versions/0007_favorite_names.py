"""favorites: leveraged ETFs by their ticker, ^NDX ("weekend Nasdaq 100") as the Nasdaq 100 future (#238)

Data only. Stars added from the old quote-board bundle kept its descriptive names ("반도체 3배",
"나스닥 3배") and the Nasdaq 100 index (^NDX, which never trades off hours). The tickers read
better for leveraged ETFs, and NQ=F trades nearly around the clock.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENAMED = (("SOXL", "반도체 3배"), ("TQQQ", "나스닥 3배"))


def upgrade() -> None:
    for ticker, old in RENAMED:
        op.execute(sa.text("UPDATE favorites SET name = :ticker WHERE ticker = :ticker AND name = :old")
                   .bindparams(ticker=ticker, old=old))
    # ^NDX becomes NQ=F, unless the account already stars NQ=F (then the ^NDX star goes)
    op.execute(sa.text(
        "DELETE FROM favorites WHERE ticker = '^NDX' AND name = '위켄드 나스닥100' AND EXISTS "
        "(SELECT 1 FROM favorites f WHERE f.user_id = favorites.user_id AND f.ticker = 'NQ=F')"))
    op.execute(sa.text(
        "UPDATE favorites SET ticker = 'NQ=F', name = '나스닥100 선물' "
        "WHERE ticker = '^NDX' AND name = '위켄드 나스닥100'"))


def downgrade() -> None:
    pass    # names only; the old ones are not worth restoring
