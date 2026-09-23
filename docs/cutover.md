# The cutover: boxoutfantasy.com answers, and sign-in is enforced

**Written:** 2026-09-22, to be followed by hand on the VPS. **Status:** not
done. Nothing in this file has been run; it is written from the VPS as it
stands (STATUS.md, "Deployment: sharing a host with the live stack") and from
the settings the code reads.

This is the whole of step 5 of docs/product.md: the domain, the certificate
and the switch from single mode to accounts mode. It is a runbook, not a
script: read a step, run it, read what it printed, then the next one. Every
command is meant to be pasted as it is written, from the VPS over Tailscale
SSH, in `/opt/fcp-core` unless it says otherwise.

## What is already there, and is not touched

| | |
|---|---|
| **The old stack** | `fcp.patrickmcdowell.dev` and `patrickmcdowell.dev`, served by the Docker container `fullcourtpress-caddy-1` from `/srv/fullcourtpress/docker-compose.yml`. It stays up and stays serving throughout. |
| **Its Caddyfile** | `/srv/fullcourtpress/Caddyfile`, mounted into the container read-only at `/etc/caddy/Caddyfile`. It gains one block at the end. **The two existing blocks are not edited, reordered or reindented.** |
| **fcp-core's API** | `fcp-core-api.service` on the host, `scripts/serve_api.sh`, bound to the tailnet address `100.105.64.94:8001`. The container can already reach it there: that is the route the new block uses. The bind does not change. |
| **The public IP** | `178.105.181.43`. |
| **The scheduled units** | `fcp-core-ingest`, `-status`, `-enqueue`, `-bbm`, `-backup`, `-watchdog`, and `fcp-core-worker.service`. Their timers keep running. |

**The scheduled scripts keep calling the tailnet address.** `FCP_API_URL` in
`/opt/fcp-core/.env` is the tailnet one and **must stay that way**:
`scripts/warm_pages.py` and the watchdog call the API directly, not through
Caddy. Pointing `FCP_API_URL` at `https://boxoutfantasy.com` would send every
warm-up out to the internet and back for nothing, and would break the morning
pass the first time DNS or a certificate renewal hiccuped.
`scripts/preflight_public.py` fails if it finds the public name there.

## (a) The preflight, green

```
cd /opt/fcp-core
./.venv/bin/python scripts/preflight_public.py
```

It reads settings and the app's own shape. It opens no socket, sends no mail,
touches no database, changes nothing, and prints no secret: a token is
reported by its length. Every line must be `PASS` or `NOTE` before anything
below is run. At this point `FCP_AUTH_MODE` is still `single`, so that line
will say `FAIL` — it is the next step's. Everything else must be green now.

## (b) The settings, and the restart

`FCP_PUBLIC_URL=https://boxoutfantasy.com` is already set. Two things are
not.

**The service token.** It acts as the owner for the scheduled scripts, so it
is generated on the VPS and written straight into the file without ever being
displayed. First make sure there is not one already, because a second line
would be confusing rather than an error:

```
grep -c '^FCP_SERVICE_TOKEN=' /opt/fcp-core/.env
```

If that prints `0`:

```
cd /opt/fcp-core
umask 077
./.venv/bin/python -c "import secrets; print('FCP_SERVICE_TOKEN=' + secrets.token_urlsafe(32))" >> /opt/fcp-core/.env
grep -c '^FCP_SERVICE_TOKEN=' /opt/fcp-core/.env   # must now print 1
```

If it printed `1`, a token is already there and nothing needs generating.
**Do not `cat` the file, and do not echo the token to read it back.** The
preflight confirms it is set and long enough without showing it.

**The mode.** Edit `/opt/fcp-core/.env` and set:

```
FCP_AUTH_MODE=accounts
```

Then restart the two services that read it, and check the preflight again:

```
sudo systemctl restart fcp-core-api.service fcp-core-worker.service
systemctl is-active fcp-core-api fcp-core-worker
./.venv/bin/python scripts/preflight_public.py     # every line PASS or NOTE
curl -sS http://100.105.64.94:8001/health          # {"status":"ok"}
curl -sS -o /dev/null -w '%{http_code}\n' http://100.105.64.94:8001/leagues   # 401
```

That 401 is the point of the whole exercise: over the tailnet, with no
cookie, the API now refuses. `/health` still answers, because it says nothing.

Prove the service token still works, which is how the morning pass reaches
the API from now on:

```
set -a; . /opt/fcp-core/.env; set +a
curl -sS -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $FCP_SERVICE_TOKEN" \
  http://100.105.64.94:8001/leagues                 # 200
unset FCP_SERVICE_TOKEN
```

## (c) The order, and why it is this order

The obvious thing to do next is to prove sign-in works before the world can
reach the site — point `FCP_PUBLIC_URL` at the tailnet address, sign in, then
change it back. **That does not work.** The link in the sign-in email is
built on `FCP_PUBLIC_URL` and nothing else (that is the defence against a
link being built on a `Host` header the caller wrote), so a link built on a
tailnet address is a link that only opens from a tailnet machine, and the
address in `FCP_PUBLIC_URL` has to be the one that will still be right
tomorrow. Changing it for a test and changing it back is two more chances to
leave it wrong.

So the honest order is: **the Caddy block first, then DNS for your own
resolver only, then test end to end, then the public A record.** On the
laptop, before the A record exists, add:

```
178.105.181.43  boxoutfantasy.com www.boxoutfantasy.com
```

to `/etc/hosts`. That machine then reaches the new site by its real name
while nobody else on the internet can resolve it at all, which is what lets
you click a real sign-in link at the real address before it is anybody's.

**What the hosts entry cannot do, and be ready for it.** A certificate
cannot be issued until the name resolves publicly: Caddy asks Let's Encrypt,
and Let's Encrypt validates from the internet, not from your laptop. So
between (d) and (e) Caddy will serve the site and keep failing to get a
certificate, and a browser will warn. In that window, test over plain HTTP
and with the warning accepted:

```
curl -sS -o /dev/null -w '%{http_code}\n' --resolve boxoutfantasy.com:80:178.105.181.43 \
  http://boxoutfantasy.com/health
```

Everything that needs a valid certificate — a real sign-in link clicked in a
real mail client — waits for (e). The A records in (e) are DNS only and
announced to nobody, so the window between them and telling the league is
yours as well.

## (d) The Caddy block

**Back the file up first**, named by date like the existing backups:

```
sudo cp /srv/fullcourtpress/Caddyfile /srv/fullcourtpress/Caddyfile.$(date +%Y-%m-%d)
ls -l /srv/fullcourtpress/Caddyfile*
```

Then append this to the end of `/srv/fullcourtpress/Caddyfile`, leaving the
`fcp.patrickmcdowell.dev` and `patrickmcdowell.dev` blocks exactly as they
are:

```
www.boxoutfantasy.com {
  redir https://boxoutfantasy.com{uri} permanent
}
boxoutfantasy.com {
  reverse_proxy 100.105.64.94:8001
  encode gzip
  header {
    X-Content-Type-Options nosniff
    X-Frame-Options DENY
    Referrer-Policy strict-origin-when-cross-origin
  }
  log {
    output file /var/log/caddy/boxout-access.log
  }
}
```

The file is mounted read-only into the container, so it is edited on the host
and validated and reloaded **inside** it:

```
docker exec fullcourtpress-caddy-1 caddy validate --config /etc/caddy/Caddyfile
```

Only if that says the file is valid:

```
docker exec fullcourtpress-caddy-1 caddy reload --config /etc/caddy/Caddyfile
```

**Never `docker restart` or `docker compose restart` that container.** It
serves two other sites; a reload swaps the configuration with no connection
dropped, and a restart takes them down with it. If `validate` refuses the
file, fix it and validate again — nothing has been loaded, and the running
Caddy is still serving the old configuration.

Check the other two sites are still themselves before going on:

```
curl -sS -o /dev/null -w '%{http_code}\n' https://fcp.patrickmcdowell.dev/
curl -sS -o /dev/null -w '%{http_code}\n' https://patrickmcdowell.dev/
```

**The access log.** Caddy logs the request URI, and a sign-in link, an invite
link and an alert-confirmation link all carry their token in the URI
(docs/accounts.md, "Security notes"). The token is spent by the time it is
logged, but it should not be written down; `/var/log/caddy/boxout-access.log`
is on the VPS's disk and in its backups. Either leave the `log` block out, or
treat that file as holding secrets.

## (e) DNS, at Cloudflare

Two records, both **DNS only — the grey cloud, not the orange one**:

| Type | Name | Content | Proxy |
|---|---|---|---|
| A | `boxoutfantasy.com` | `178.105.181.43` | DNS only |
| A | `www` | `178.105.181.43` | DNS only |

Grey cloud because Caddy obtains and renews its own certificate, exactly as
it does for the other two sites on this host, and it can only do that if
Let's Encrypt reaches this machine rather than Cloudflare's. Proxying also
hides the real client address from the rate limits.

Then wait for the certificate and watch it happen:

```
docker logs --tail 50 -f fullcourtpress-caddy-1
```

It should say it obtained a certificate for `boxoutfantasy.com` and
`www.boxoutfantasy.com` within a minute or two of the records resolving. Take
the `/etc/hosts` line off the laptop once they do: the name resolves properly
now, and a stale hosts entry is a thing that bites six months later.

## (f) The smoke test

Every one of these, in order, from the laptop:

1. `https://boxoutfantasy.com/` shows the landing page: the name, the
   tagline, the three figures, **Sign in with your email**. No certificate
   warning.
2. `https://www.boxoutfantasy.com/` lands on the same page at the bare name
   (a 301).
3. `/sign-in`, own address, **Send the link**. The page says a link is on its
   way whether or not the address has an account.
4. The mail arrives from `Box Out <hello@mail.boxoutfantasy.com>`, looks like
   the site, and its link starts `https://boxoutfantasy.com/auth/callback`.
   Clicking it signs you in and lands on your league.
5. A league page loads: **This week**, with the matchups and the nine
   categories.
6. In a private window, with no cookie, a team page —
   `https://boxoutfantasy.com/l/<league>/<season>/team/<team>/week` — sends
   you to `/sign-in` and does not show the plan.
7. `curl -sS https://boxoutfantasy.com/health` answers `{"status":"ok"}`.
8. On the VPS, `./.venv/bin/python scripts/warm_pages.py` prints HTTP 200 and
   not 401: the service token is reaching the API over the tailnet, which is
   what the morning pass depends on.

Only after all eight: invite the league (`/account/connections`, the invite
links), and tell them.

## (g) Rollback

In this order, because taking the domain away first means nobody is mid-way
through anything when the mode changes:

1. **Remove the block.** Delete the two blocks appended in (d) from
   `/srv/fullcourtpress/Caddyfile` (or copy the dated backup back over it),
   then validate and reload inside the container, the same two commands.
   Never a restart.
2. **Back to single mode.** In `/opt/fcp-core/.env` set
   `FCP_AUTH_MODE=single` and
   `sudo systemctl restart fcp-core-api.service fcp-core-worker.service`.

Nothing is lost either way: sessions stay in the table and simply stop being
asked for, and the accounts, memberships and claims are untouched. Leave
`FCP_SERVICE_TOKEN` and `FCP_PUBLIC_URL` where they are; neither does any
harm in single mode, and both are needed the next time.

The DNS records can stay or go. Left in place with the Caddy block removed,
the name resolves to a host that has no site for it, which is a plain 404
from Caddy and tells nobody anything.

## Security, as checked on 2026-09-23

What the host exposes to the internet, read from the machine itself
(`ss -tlnp`, `sshd -T`, `docker ps`) the day the domain went live:

| | state |
|---|---|
| SSH | keys only, no passwords, no root login, fail2ban active |
| Updates | unattended security upgrades on; Ubuntu 24.04 LTS |
| 80 / 443 | Caddy only; the API listens on the tailnet address alone |
| The API | `100.105.64.94:8001` — reachable over Tailscale, not from the internet |
| `.env` | `0600`, the one user |
| The database | **was on the public internet**, see below; now loopback only |

**The hole.** `docker-compose.yml` published the database as `"5433:5432"`.
A bare `port:port` in Compose binds every interface, and Docker writes its
own firewall rules ahead of anything `ufw` would say, so `fcp-core-db-1`
answered on `178.105.181.43:5433` to anyone — behind a three-character
password on a superuser role. The container's log held 891 failed logins,
every one against the user `postgres`; a bot that tried `fcp` would have
been in. Found by asking the question rather than assuming the answer.

**Fixed the same hour, in this order:** the port bound to
`127.0.0.1:${FCP_DB_PORT}:5432` and the container recreated (the API stayed
up); the password rotated to forty random characters generated on the VPS
and never shown anywhere, `DATABASE_URL` and `TEST_DATABASE_URL` rewritten
in `.env` (backup beside it), services restarted, the old password confirmed
refused on the host path; the Compose file in the repository carries the
loopback binding so a redeploy cannot reopen it.

**What is not known.** Postgres was not logging connections and recreating
the container dropped its log, so a successful login while the port was open
would have left no trace. What was checked instead: one role, one schema, no
table, function or extension the migrations did not create, one user, no
tokens. No sign of anyone having been in; not proof of it either.

**Still open, in order of worth:** `log_connections=on` so the next question
has an answer; the `fcp` role does not need to be a superuser; a host
firewall that Docker cannot bypass (`DOCKER-USER` chain) as belt and braces;
the sign-in link is rate-limited per address (`app/api/auth.py`) and that is
the one public form.

## What this does not cover

- **The old site is not retired here.** `fcp.patrickmcdowell.dev` keeps
  serving whatever it serves. Deciding what happens to it is a separate day's
  work.
- **Billing** (`BILLING_ENABLED`, docs/product.md step 7). The team pages are
  open to whoever manages the team until then.
- **A bare-SWID claim** is still not proof (docs/accounts.md, "A known
  weakness"). `TRUST_BARE_SWID` is `False`, so every member's claim waits for
  a league owner to approve it, which is the right setting for a league of
  strangers and stays that way.
- **The draft room** is still a tool run on a laptop, not a hosted page.
