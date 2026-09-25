#!/usr/bin/env python3
"""Is this server ready to be answered for on the public internet?

Usage:
    python scripts/preflight_public.py          # check the live settings
    python scripts/preflight_public.py --json   # the same, as one JSON object

Run on the VPS before step (b) of docs/cutover.md, and again after editing
`.env`, from the repository directory so the same `.env` the API reads is the
one checked. It reads settings and the code's own shape; it opens no socket,
sends no mail, touches no database and changes nothing.

**It never prints a secret.** A token, a key or a password is reported as set
or not set and by its length, never by its value, because this is run over
SSH and scrolls into a terminal's history.

Each line is PASS, FAIL or a NOTE. FAIL means something a reader could be
hurt by -- a page open to a stranger, a cookie a script could read, a link
built on the caller's own Host header. A NOTE is a thing worth knowing that
is not a reason to stop.

Exit codes:
    0   every check passed
    1   at least one check failed
"""

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Run by path, so `scripts/` is on sys.path and the repo root is not; the
# other CLIs do the same, for the same reason.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import oauth
from app.api import access, auth, leagues_admin
from app.brand import BRAND, BRAND_DOMAIN
from app.config import Settings, get_settings

PASS, FAIL, NOTE = "PASS", "FAIL", "NOTE"


@dataclass(frozen=True)
class Check:
    """One line of the report: its name, how it went, and why in one sentence."""

    name: str
    state: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.state != FAIL


def _held(value: str | None) -> str:
    """A secret, described and never shown."""
    return f"set, {len(value)} characters" if value else "not set"


# ---------------------------------------------------------------------------
# the settings the cutover needs
# ---------------------------------------------------------------------------


def check_auth_mode(settings: Settings) -> Check:
    mode = settings.fcp_auth_mode
    if mode == "accounts":
        return Check("FCP_AUTH_MODE", PASS, "accounts: every route is enforced")
    return Check(
        "FCP_AUTH_MODE",
        FAIL,
        f"{mode}: single mode enforces nothing and is only safe on the tailnet",
    )


def check_public_url(settings: Settings) -> Check:
    url = settings.fcp_public_url
    if not url:
        return Check("FCP_PUBLIC_URL", FAIL, "not set: no sign-in link can be mailed")
    if not url.startswith("https://"):
        return Check("FCP_PUBLIC_URL", FAIL, f"{url}: must be https, or the cookie is not Secure")
    if BRAND_DOMAIN not in url:
        return Check("FCP_PUBLIC_URL", NOTE, f"{url}: https, but not the {BRAND} domain")
    return Check("FCP_PUBLIC_URL", PASS, url)


def check_mcp_public_url(settings: Settings) -> Check:
    """Where the co-manager answers, for the apps that have to discover it.

    A NOTE rather than a FAIL: the site is public without it, and the remote
    co-manager simply is not served. What would be a failure is serving it
    without this, and `scripts/mcp_server.py --http` refuses to start in that
    case rather than running open (`app.mcp.server.auth_settings`).
    """
    url = settings.fcp_mcp_public_url
    if not url:
        return Check(
            "FCP_MCP_PUBLIC_URL",
            NOTE,
            "not set: no remote co-manager, and --http refuses to start with auth on",
        )
    if not url.startswith("https://"):
        return Check("FCP_MCP_PUBLIC_URL", FAIL, f"{url}: must be https; a bearer travels on it")
    if BRAND_DOMAIN not in url:
        return Check("FCP_MCP_PUBLIC_URL", NOTE, f"{url}: https, but not the {BRAND} domain")
    return Check(
        "FCP_MCP_PUBLIC_URL", PASS, f"{url}/mcp, signed in through {settings.fcp_public_url}"
    )


def check_the_oauth_front_door(settings: Settings) -> Check:
    """The endpoints an app discovers, and the two things that must be true.

    An authorization server with no public address cannot name itself, and
    one in single mode would hand the owner's token to whoever asked -- both
    are already covered above, so this line says what is served and where.
    """
    base = (settings.fcp_public_url or "").rstrip("/")
    if not base:
        return Check("OAuth front door", FAIL, "no FCP_PUBLIC_URL: no metadata can be served")
    if settings.fcp_auth_mode != "accounts":
        return Check("OAuth front door", FAIL, "single mode: /oauth/authorize is refused outright")
    return Check(
        "OAuth front door",
        PASS,
        f"{base}/.well-known/oauth-authorization-server; PKCE S256 only, "
        f"codes {int(oauth.CODE_TTL.total_seconds() // 60)} minutes and single-use, "
        "no refresh token, tokens revocable on /account/connections",
    )


def check_owner_email(settings: Settings) -> Check:
    email = settings.fcp_owner_email
    if not email:
        return Check("FCP_OWNER_EMAIL", FAIL, "not set: nobody signs in as the owner")
    return Check("FCP_OWNER_EMAIL", PASS, email)


def check_service_token(settings: Settings) -> Check:
    token = settings.fcp_service_token
    if not token:
        return Check(
            "FCP_SERVICE_TOKEN",
            FAIL,
            "not set: the scheduled scripts would be refused in accounts mode",
        )
    return Check("FCP_SERVICE_TOKEN", PASS, _held(token))


def check_secrets_key(settings: Settings) -> Check:
    key = settings.fcp_secrets_key
    if not key:
        return Check(
            "FCP_SECRETS_KEY", FAIL, "not set: no league can be connected and no SWID kept"
        )
    return Check("FCP_SECRETS_KEY", PASS, _held(key))


def check_smtp(settings: Settings) -> Check:
    if not settings.smtp_configured:
        return Check("SMTP", FAIL, "no host or no sender: no sign-in link is mailed, only logged")
    detail = f"{settings.fcp_smtp_host}:{settings.fcp_smtp_port} as {settings.fcp_email_from}"
    password = "with a password" if settings.fcp_smtp_password else "with no password"
    return Check("SMTP", PASS, f"{detail}, {password}")


def check_api_url_stays_on_the_tailnet(settings: Settings) -> Check:
    """The scheduled scripts must keep calling the API where it binds.

    `FCP_API_URL` is what `scripts/warm_pages.py` and the watchdog ask; it is
    the tailnet address, not the public one. Pointing it at the public name
    sends every warm-up out through Caddy and back for no reason, and breaks
    the moment DNS or the certificate does.
    """
    url = settings.fcp_api_url
    if not url:
        return Check("FCP_API_URL", NOTE, "not set: the morning pass warms nothing")
    if BRAND_DOMAIN in url:
        return Check(
            "FCP_API_URL",
            FAIL,
            f"{url}: the scheduled scripts must call the tailnet address, not the public name",
        )
    return Check("FCP_API_URL", PASS, url)


# ---------------------------------------------------------------------------
# what the code itself guarantees
# ---------------------------------------------------------------------------

#: What may be answered to somebody signed out. Anything else is a 401 or a
#: redirect to sign in. Kept beside `tests/test_public_ready.py`, which fails
#: if a route is added outside it without a check.
OPEN_ROUTES = (
    "GET /",
    "GET /sign-in",
    "GET /health",
    "GET /auth/callback",
    "POST /auth/sign-in",
    "POST /auth/sign-out",
    "GET /pages/static/{name}",
    # A browser's own guess at an icon, before it has read a page: a redirect
    # to the icon under `/pages/static/`. Nothing behind it but the mark.
    "GET /favicon.ico",
    # The design language (docs/design_system.md): a static file with no data
    # in it. Its specimens read league routes, each behind its own check.
    "GET /design",
    # The OAuth front door (docs/mcp.md). Each of these is open because the
    # spec says so and because none of them gives anything away: the metadata
    # describes the server, registering stores an app's name and gets no
    # secret, and the token and revocation endpoints are held to a one-time
    # code with PKCE, or to holding the token already. The one endpoint that
    # hands something over -- `/oauth/authorize` -- is NOT here: it sends a
    # signed-out browser to sign in, like every other page.
    "GET /.well-known/oauth-authorization-server",
    "POST /oauth/register",
    "POST /oauth/token",
    "POST /oauth/revoke",
)


def _routes() -> list[tuple[str, object]]:
    """Every (method path, route) the app serves.

    The app is built here rather than imported as a global, so this costs a
    moment and touches no database: `create_app` wires routers and nothing
    else. An included router is one entry wrapping the router, so its own
    routes are walked out of it; FastAPI's `/docs`, `/redoc` and
    `/openapi.json` are not `APIRoute`s and are left out, and they carry no
    data.
    """
    from collections.abc import Iterable, Iterator
    from typing import Any

    from fastapi.routing import APIRoute

    from app.main import create_app

    def walk(routes: Iterable[Any]) -> Iterator[APIRoute]:
        for route in routes:
            if isinstance(route, APIRoute):
                yield route
            elif hasattr(route, "original_router"):
                yield from walk(route.original_router.routes)
            elif hasattr(route, "routes"):
                yield from walk(route.routes)

    return [
        (f"{method} {route.path}", route)
        for route in walk(create_app().routes)
        for method in sorted(route.methods or ())
    ]


def check_every_route_is_checked(_: Settings) -> Check:
    """Every route the app serves declares an access check, or is open on purpose."""
    found = _routes()
    naked = sorted(
        where
        for where, route in found
        if where not in OPEN_ROUTES
        and not any(dep.call in access.CHECKS for dep in route.dependant.dependencies)  # type: ignore[attr-defined]
    )
    if naked:
        return Check("every route checked", FAIL, f"open to anyone: {', '.join(naked)}")
    return Check(
        "every route checked",
        PASS,
        f"{len(found)} routes; {len(OPEN_ROUTES)} open on purpose ({', '.join(OPEN_ROUTES)})",
    )


def check_the_cookie(settings: Settings) -> Check:
    """HttpOnly and SameSite=Lax always; Secure follows the scheme.

    The flags are set in one place (`app/api/auth.py`), so what is checked
    here is that place's answer for this configuration rather than a promise.
    """
    secure = (settings.fcp_public_url or "").startswith("https://")
    if not secure:
        return Check(
            f"cookie {access.COOKIE}",
            FAIL,
            "HttpOnly and SameSite=Lax, but not Secure: FCP_PUBLIC_URL is not https",
        )
    return Check(f"cookie {access.COOKIE}", PASS, "Secure, HttpOnly, SameSite=Lax, path /, 30 days")


def check_links_are_built_on_the_public_url(settings: Settings) -> Check:
    """A mailed link must never be built on the request's Host header.

    `app.api.auth._link` prefers `FCP_PUBLIC_URL`, and sign-in answers 503
    rather than mailing a link built on the request when SMTP is configured
    and it is not. Both are settings-dependent, so this says which branch
    this configuration lands in.
    """
    if not settings.fcp_public_url:
        return Check("sign-in links", FAIL, "no FCP_PUBLIC_URL: a link would be built on Host")
    base = settings.fcp_public_url.rstrip("/")
    return Check("sign-in links", PASS, f"built on {base}/auth/callback, never on Host")


def check_rate_limits(_: Settings) -> Check:
    signin = auth.SignInLimits()
    admin = leagues_admin.AdminLimits()
    return Check(
        "rate limits",
        PASS,
        "sign-in "
        f"{signin.by_email.capacity:.0f} per address / {signin.by_ip.capacity:.0f} per client; "
        f"connect {admin.connect.capacity:.0f}; swid {admin.identity.capacity:.0f}; "
        f"invite {admin.invite.capacity:.0f} per user",
    )


def check_the_service_token_path(settings: Settings) -> Check:
    """`Authorization: Bearer <FCP_SERVICE_TOKEN>` is the owner, and nothing else is.

    Checked against the resolver's own rule rather than a second copy of it:
    a bearer is compared to the configured token by hash, and a bearer that
    does not match is refused outright instead of falling back to a cookie.
    """
    from app import accounts

    token = settings.fcp_service_token
    if not token:
        return Check("service token path", FAIL, "no token: the scheduled scripts get 401")
    if not accounts.same_secret(token, token):
        # Unreachable; here so a change to `same_secret` is caught on the VPS.
        return Check("service token path", FAIL, "the constant-time compare refuses its own token")
    if accounts.same_secret("not-the-token", token):
        return Check("service token path", FAIL, "a wrong bearer would be accepted")
    return Check("service token path", PASS, "warm_pages.py and the watchdog act as the owner")


def check_billing_is_off(_: Settings) -> Check:
    state = "off" if not access.BILLING_ENABLED else "on"
    return Check("paywall", NOTE, f"BILLING_ENABLED is {state}; team pages open to their managers")


CHECKS: tuple[Callable[[Settings], Check], ...] = (
    check_auth_mode,
    check_public_url,
    check_mcp_public_url,
    check_the_oauth_front_door,
    check_owner_email,
    check_service_token,
    check_secrets_key,
    check_smtp,
    check_api_url_stays_on_the_tailnet,
    check_every_route_is_checked,
    check_the_cookie,
    check_links_are_built_on_the_public_url,
    check_rate_limits,
    check_the_service_token_path,
    check_billing_is_off,
)


def run(settings: Settings) -> list[Check]:
    return [check(settings) for check in CHECKS]


def report(checks: list[Check]) -> str:
    width = max(len(check.name) for check in checks)
    lines = [f"{BRAND}: is this server ready to answer on the public internet?", ""]
    lines += [f"{check.state}  {check.name.ljust(width)}  {check.detail}" for check in checks]
    failed = [check for check in checks if not check.ok]
    lines += ["", "Ready. Follow docs/cutover.md from step (b)." if not failed else ""]
    if failed:
        lines[-1] = f"NOT ready: {len(failed)} of {len(checks)} failed. Fix them and run again."
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="one JSON object instead of a report")
    args = parser.parse_args()

    checks = run(get_settings())
    if args.json:
        print(json.dumps([check.__dict__ for check in checks], indent=1))
    else:
        print(report(checks))
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
