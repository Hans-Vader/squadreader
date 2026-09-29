# Moderator live map

A real-time map of the running round, for moderators only. It is **off**
unless `live_password` is set in `sqreader.config.json`; then it lives under
`/api/live/` and nowhere else.

A live map shows **both teams in real time**. Anyone who watches it while
playing can ghost, which is why the public build has no live view at all.
Give the password to moderators and to nobody else.

## Enable

1. Generate a password (at least 20 characters):
   `python3 -c 'import secrets; print(secrets.token_urlsafe(24))'`
2. Put it into `sqreader.config.json` as `"live_password": "<password>"` and
   `chmod 600` the file. It is a credential.
3. Docker: uncomment the `./sqreader.config.json:/app/sqreader.config.json:ro`
   line in `docker-compose.yml`.
4. Restart the reader. The log says `live map enabled for moderators`.

Moderators open `https://<your site>/?mode=live` and log in. HTTPS is
required; plain `http://` only works on `localhost`. After a login the start
page shows a **Live-Karte** button. A session lasts 12 hours, and every reader
restart, a Squad server restart included, logs everyone out.

## Change the password, or kick everyone out

Edit `live_password`, then send SIGHUP. No restart is needed, and the running
recording stays intact:

    docker compose kill -s HUP sqreader                # Docker
    systemctl kill -s HUP --kill-whom=main <unit>      # systemd

`--kill-whom=main` signals the reader alone. Without it systemd also signals
the build worker process of a two-tier recording, which has no SIGHUP handler,
dies, and has to be respawned.

Under Docker the config file is a single-file bind mount, and a bind mount
follows the file's inode. Edit it in place, for example with `cat new.json >
sqreader.config.json` or `tee`. `sed -i`, vim's default save and most editors
replace the file instead; the container then keeps reading the old copy, and
SIGHUP logs `live_password in <path> is UNCHANGED`. After every change, check
that the log says `password reloaded` and that the old password is refused.

Every session ends at once and open live maps drop back to the login. If the
file cannot be read or holds no valid password, **nobody** can log in until it
is fixed and SIGHUP is sent again. The log line names the reason.

SIGHUP only does this while the live map is on. A reader that started without
a valid `live_password` has no SIGHUP handler. Under systemd or in a terminal
it exits on SIGHUP as before; as a container's PID 1 it does not, because the
kernel ignores a signal that has no handler there. Either way, turning the
live map on, or repairing a value it rejected at startup, takes a restart
between two rounds. With the live map on, SIGHUP always means "revoke", never
"exit", a terminal hangup included.

## Turn it off

Remove `live_password` and restart the reader **between two rounds**. A
restart in the middle of a round leaves that round's recording so far
unplayable.

## Proxies

The stream is Server-Sent Events (`text/event-stream`) and must not be
buffered:

- **Traefik**: no `buffering` middleware on this router, and keep the
  entrypoint's `respondingTimeouts.writeTimeout` at 0 (the default). On
  Traefik v3.0 to v3.3.4 add `text/event-stream` to the compress middleware's
  `excludedContentTypes`.
- **Caddy**: keep `/api/live/*` out of `encode`, for example
  `@compress not path /api/live/*` and `encode @compress zstd gzip`.
- **nginx**: nothing to do; the stream sends `X-Accel-Buffering: no`.

Serve the map on its own hostname. Cookies do not tell ports apart, so two
instances on one hostname share a login cookie, and an app that shares the
origin (a path prefix next to other sites) could read the stream with a
moderator's session.

## If logins are being blocked

After five wrong passwords from one address, or fifty from everyone, within
10 minutes, new logins wait. The log says which limit was reached:

    live: login limit reached for <ip>     that address waits
    live: global login limit reached       every address waits

Open live maps keep streaming; only new logins wait, at most 10 minutes after
the last failed one. SIGHUP does not clear the counters. A restart does, and
logs everyone out, but a restart in the middle of a round leaves that round's
recording so far unplayable.

The real fix is to keep strangers off `/api/live/` at the proxy, so that only
moderators can spend failed attempts: an IP allowlist for their addresses, or
basic auth, on a router or location that matches `/api/live/` and nothing
else.

**Traefik (v3)**: a second router for the same service. Traefik ranks routers
by the length of their rule, so the longer one takes `/api/live/` and the
existing router keeps everything else:

    labels:
      - traefik.http.middlewares.sqreader-mods.ipallowlist.sourcerange=203.0.113.7,198.51.100.0/24
      - "traefik.http.routers.sqreader-live.rule=Host(`live.example.com`) && PathPrefix(`/api/live/`)"
      - traefik.http.routers.sqreader-live.middlewares=sqreader-mods
      - traefik.http.routers.sqreader-live.service=sqreader

Use the `Host` of your existing router, and copy its `entrypoints` and `tls`
labels onto the new one if it has any: a router that differs there does not
match the same requests, and the allowlist would silently not apply. Then
check from both sides. A moderator's address still logs in, and
`curl -i https://<your site>/api/live/session` from any other address answers
Traefik's 403.

**Caddy**: put this in the site block; `respond` runs before `reverse_proxy`.

    @strangers {
        path /api/live/*
        not remote_ip 203.0.113.7 198.51.100.0/24
    }
    respond @strangers 403

**nginx**: give `/api/live/` a location of its own, with your usual
`proxy_pass` lines in it.

    location /api/live/ {
        allow 203.0.113.7;
        allow 198.51.100.0/24;
        deny all;
        # proxy_pass and proxy_set_header as in the location that serves the map
    }

The throttle keys on the rightmost `X-Forwarded-For` entry, which is the
address your own proxy saw. With a CDN in front that is the CDN's edge, so
everyone behind the same edge shares one per-address limit; rely on the
allowlist there.

## Check it

    curl -sN -H 'Cookie: sqr_live=<token>' https://<your site>/api/live/stream

prints `retry: 3000` and then one `data:` line per frame. Without a valid
cookie it answers 401.

`<token>` is the value of the `sqr_live` cookie of a logged-in browser, which
the dev tools show under Application (Chrome) or Storage (Firefox). Or log in
with curl and let it keep the cookie in a jar; that leaves the password in
your shell history:

    curl -c jar -H 'Content-Type: application/json' -d '{"password":"…"}' https://<your site>/api/live/login
    curl -sN -b jar https://<your site>/api/live/stream

## What the log says

| Line | Meaning |
|---|---|
| `live map enabled for moderators (login via ?mode=live)` | at startup: the live map is on |
| `live map disabled: <rule>` | at startup: `live_password` was rejected and there is no live map; the rule is named, the value never |
| `live: login ok from <ip> (session <id>)` | a moderator logged in |
| `live: login failed from <ip>` | a wrong password |
| `live: logout from <ip>` | a moderator logged out |
| `live: login limit reached for <ip>`, `live: global login limit reached` | 5 failures from one address, or 50 from everyone, within 10 minutes; logins wait, open sessions are unaffected; see "If logins are being blocked" |
| `live: stream opened from <ip> (<n>/10)`, `live: stream closed from <ip> after <t> (<reason>)` | a live map was opened or closed |
| `live: stream refused from <ip> (10/10 in use)` | more than 10 live maps at once |
| `live: SIGHUP: all sessions revoked, password reloaded` | the file held a different password; everyone was logged out |
| `live: SIGHUP: all sessions revoked; live_password in <path> is UNCHANGED` | everyone was logged out, but the file still holds the old password; expected for a plain revoke, a problem after an edit (see the Docker bind mount above) |
| `live: SIGHUP: all sessions revoked; NO valid live_password in <path> (<why>), logins disabled until fixed` | the file cannot be read or holds no valid password, and nobody can log in until it is fixed and SIGHUP is sent again; `<why>` is an error type such as `FileNotFoundError` or `JSONDecodeError`, or the rule the value broke; `<path>` is `None` if the path itself could not be found |

A login or a stream from an address you do not know means the password has
leaked: change it and send SIGHUP.
