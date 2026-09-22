"""every channel is an email address now

Everything this server says goes by email (2026-09-22): a member's digest and
alerts, and the operator's own notices. The Telegram and ntfy kinds are gone
from the code (`app/channels.py`, `app/notify.py`).

**The rows are disabled, not deleted.** A member who confirmed a Telegram
chat or an ntfy topic keeps his row, so the Alerts page can tell him it has
stopped and ask him for an address, and so nothing about his account is
silently rewritten. Disabling here means exactly what disabling on the page
means: `disabled_at` is set and the sealed target, the verify hash and its
expiry are wiped, so nothing can be sent to it and the address or chat id it
held is gone.

The CHECK becomes "an email address, or disabled": the old rows still read,
and a new row of a retired kind cannot be written even by hand.

Downgrade puts the old CHECK back and leaves the rows disabled. It cannot
un-disable them: the targets were wiped, and a channel with no target is one
nothing can be sent to whatever its flags say.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_notification_channels_kind"
_WAS = "kind IN ('email', 'telegram', 'ntfy')"
_NOW = "kind = 'email' OR disabled_at IS NOT NULL"


def upgrade() -> None:
    op.execute(
        """
        UPDATE notification_channels
           SET disabled_at = now(),
               sealed_target = NULL,
               verify_hash = NULL,
               verify_expires_at = NULL
         WHERE kind <> 'email'
           AND disabled_at IS NULL
        """
    )
    op.drop_constraint(_NAME, "notification_channels", type_="check")
    op.create_check_constraint(_NAME, "notification_channels", _NOW)


def downgrade() -> None:
    op.drop_constraint(_NAME, "notification_channels", type_="check")
    op.create_check_constraint(_NAME, "notification_channels", _WAS)
