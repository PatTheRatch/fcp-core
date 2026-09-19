"""Secrets at rest: sealed with a key that lives only in the VPS `.env`.

What is sealed: a league connection's ESPN cookies (`espn_s2` and `SWID`,
as one JSON object) and a member's own SWID (docs/accounts.md). Sealing is
Fernet from `cryptography`: AES-128-CBC with an HMAC-SHA256 over it, so a
sealed value that was tampered with, or sealed with another key, does not
open at all rather than opening to garbage.

The key is the setting `fcp_secrets_key` (`FCP_SECRETS_KEY` in `.env`), made
by `scripts/new_secrets_key.py`. Without it nothing is sealed and nothing is
opened: `seal` raises `SecretsKeyMissingError`, which names the setting, and
there is no fallback to storing the value as it is. A copy of the database
without the key reads no cookie.

Nothing here logs, and no error carries a secret or the key: the messages
are fixed sentences.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings, get_settings

#: The setting's name as it is written in `.env`, for the messages.
SETTING = "FCP_SECRETS_KEY"


class SecretsKeyMissingError(RuntimeError):
    """`FCP_SECRETS_KEY` is not set, so nothing can be sealed or opened."""

    def __init__(self) -> None:
        super().__init__(
            f"{SETTING} is not set, so secrets cannot be sealed; "
            "make a key with scripts/new_secrets_key.py and put it in the .env"
        )


class SecretsUnreadableError(RuntimeError):
    """A sealed value that does not open with this key: another key sealed it,
    or it was altered."""

    def __init__(self) -> None:
        super().__init__(f"a sealed secret does not open with this {SETTING}")


def new_key() -> str:
    """A fresh key: 32 random bytes, urlsafe base64."""
    return Fernet.generate_key().decode()


def configured(settings: Settings | None = None) -> bool:
    """Whether there is a key to seal with."""
    return (settings or get_settings()).fcp_secrets_key is not None


def _box(settings: Settings | None) -> Fernet:
    key = (settings or get_settings()).fcp_secrets_key
    if not key:
        raise SecretsKeyMissingError()
    return Fernet(key.encode())


def seal(plaintext: str, settings: Settings | None = None) -> str:
    """The value, sealed: an opaque ASCII string safe to store in a text column.

    `settings` defaults to the process's; a route passes its own, so a test's
    override is the one that counts.
    """
    return _box(settings).encrypt(plaintext.encode()).decode()


def open_(sealed: str, settings: Settings | None = None) -> str:
    """The value a `seal` call sealed. Raises `SecretsUnreadableError` if it
    does not open with this key. (`open_` because `open` is the builtin.)"""
    try:
        return _box(settings).decrypt(sealed.encode()).decode()
    except InvalidToken:
        raise SecretsUnreadableError() from None
