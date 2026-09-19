"""Print a fresh FCP_SECRETS_KEY, and nothing else.

    python scripts/new_secrets_key.py

Patrick runs this on the VPS and puts the line in `/opt/fcp-core/.env` as
`FCP_SECRETS_KEY=<the key>` himself (docs/accounts.md, "The secrets key").
The key is printed once and stored nowhere by this script. Run it once: a
second key does not open what the first one sealed.
"""

from cryptography.fernet import Fernet


def main() -> None:
    print(Fernet.generate_key().decode())


if __name__ == "__main__":
    main()
