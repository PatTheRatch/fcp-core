"""Sending one short message to a phone.

One POST, no client library, no account: the manager picks a service and
gives it a URL. Two shapes cover the two services docs/pickups.md section
10 left open, and which one is used is decided by whether a chat id is set:

* **ntfy** (the least setup, and the default shape): the text is the body,
  the title is a header. A topic URL like `https://ntfy.sh/fcp-core-abc123`
  is all it needs. The topic name is the only secret, so make it long.
* **Telegram** (the nicer phone experience): set the chat id as well, and
  the same text goes as JSON to `https://api.telegram.org/bot<token>/sendMessage`.

Nothing here retries. A missed digest is tomorrow's digest, and a failed
send leaves the events unmarked so the next one repeats them.
"""

from typing import Any

import requests

#: Long enough for a phone push to be accepted, short enough that a hung
#: notification service cannot hold up a scheduled pass.
TIMEOUT_SECONDS = (5, 15)


def send(url: str, text: str, *, title: str | None = None, chat_id: str | None = None) -> None:
    """POST one message. Raises `requests.HTTPError` on a refusal."""
    if chat_id:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if title:
            payload["text"] = f"{title}\n\n{text}"
        response = requests.post(url, json=payload, timeout=TIMEOUT_SECONDS)
    else:
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        if title:
            # ntfy reads these; anything else ignores them.
            headers["Title"] = title
        response = requests.post(
            url, data=text.encode("utf-8"), headers=headers, timeout=TIMEOUT_SECONDS
        )
    response.raise_for_status()
