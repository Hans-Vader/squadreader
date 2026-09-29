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

    docker compose kill -s HUP sqreader      # Docker
    systemctl kill -s HUP <unit>             # systemd

Every session ends at once and open live maps drop back to the login. If the
file cannot be read or holds no valid password, **nobody** can log in until it
is fixed and SIGHUP is sent again.

SIGHUP only does this while the live map is on. A reader that started without a valid `live_password` has no SIGHUP handler and exits on SIGHUP as before, so turning the live map on, or repairing a value it rejected at startup, takes a restart between two rounds. With the live map on, SIGHUP always means "revoke", never "exit", a terminal hangup included.

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

## Check it

    curl -sN -H 'Cookie: sqr_live=<token>' https://<your site>/api/live/stream

prints `retry: 3000` and then one `data:` line per frame. Without a valid
cookie it answers 401.

## What the log says

| Line | Meaning |
|---|---|
| `live: login ok from <ip> (session <id>)` | a moderator logged in |
| `live: login failed from <ip>` | a wrong password |
| `live: login limit reached for <ip>`, `live: global login limit reached` | 5 failures from one address, or 50 from everyone, within 10 minutes; logins wait, open sessions are unaffected |
| `live: stream opened from <ip> (<n>/10)`, `live: stream closed from <ip> after <t> (<reason>)` | a live map was opened or closed |
| `live: stream refused from <ip> (10/10 in use)` | more than 10 live maps at once |
| `live: SIGHUP: ...` | the password was reloaded and everyone was logged out |

A login or a stream from an address you do not know means the password has
leaked: change it and send SIGHUP.
