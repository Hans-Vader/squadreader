# Moderator live map

A real-time map of the running round, for moderators only. It is **off**
unless a live password is set; then it lives under `/api/live/` and nowhere
else.

A live map shows **both teams in real time**. Anyone who watches it while
playing can ghost, which is why the public build has no live view at all.
Give the password to moderators and to nobody else.

The live map is the replay viewer, pointed at the round being recorded right
now. Its timeline runs from the start of the round to now, and **● LIVE**
plays a few seconds behind the newest frame. Click anywhere on the timeline to
watch a moment again, and **● LIVE** to come back. So it needs the recording
(`--recordings-dir`, which the Docker image always sets). Between two rounds,
while the scoreboard shows and the next map loads, nothing is recorded and
there is nothing to watch; the map waits and opens the next round by itself.

## Enable

1. Pick a password and turn it into a hash:

       python3 -m sqreader.live hash                                                          # native
       docker compose run --rm --no-deps --entrypoint python3 sqreader -m sqreader.live hash   # Docker

   It asks twice and prints one line that starts with `scrypt:`. A long random
   password is still the better choice: logins are throttled, but whoever gets
   hold of the hash can try passwords offline as fast as scrypt allows. One way
   to make one: `python3 -c 'import secrets; print(secrets.token_urlsafe(24))'`
2. Put the hash where the reader finds it, either
   - **Docker:** `SQREADER_LIVE_PASSWORD_HASH=scrypt:…` in `.env`, or
   - **any install:** `"live_password": "scrypt:…"` in `sqreader.config.json`,
     and `chmod 600` the file. A plain-text password works there as well.
     Under Docker, uncomment the `./sqreader.config.json:/app/sqreader.config.json:ro`
     line in `docker-compose.yml`.

   The environment wins when both are set, and it takes only a hash: anyone
   who can run `docker inspect` reads it.
3. Restart the reader between two rounds (`docker compose up -d` after editing
   `.env`). The log says `live map enabled for moderators (password from …)`.

Moderators open the start page and click **Moderator-Login** at the top, or
go to `https://<your site>/?mode=live`. HTTPS is required; plain `http://`
only works on `localhost`. After a login the button reads **Live-Karte**. A
session lasts 12 hours, and every reader restart, a Squad server restart
included, logs everyone out.

## Watching

- The timeline covers the round so far and grows with it. Click or drag to any
  point; −10 s, +10 s, frame steps (`,` and `.`) and the speeds work as in any
  replay. Kills, markers and the ticket view (`G`) cover what has been loaded.
- **● LIVE** is red while you are live, about 8 s behind the newest frame. Grey
  means you are behind; click it to jump to the live edge. Catching up at 2× or
  faster drops to 1× at the edge on its own.
- If the reader stalls, the map pauses at the edge and goes on once 8 s are
  held again.
- When the round ends, the recording plays to its end, and then the map opens
  the next round, showing "Warte auf die nächste Runde…" until it starts.

## Change the password, or kick everyone out

SIGHUP always ends every session at once, and open live maps drop back to the
login. What it does to the password depends on where the password lives.

**`live_password` in the config file:** edit it, then send SIGHUP. No restart
is needed, and the running recording stays intact:

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

If the file cannot be read or holds no valid password, **nobody** can log in
until it is fixed and SIGHUP is sent again. The log line names the reason.

**`SQREADER_LIVE_PASSWORD_HASH` in the environment:** a running process never
sees a changed environment. SIGHUP still logs everyone out and says the
password is `UNCHANGED (environment …)`; a new hash takes a restart between
two rounds: edit `.env`, then `docker compose up -d`.

SIGHUP only does this while the live map is on. A reader that started without
a valid password has no SIGHUP handler. Under systemd or in a terminal it
exits on SIGHUP as before; as a container's PID 1 it does not, because the
kernel ignores a signal that has no handler there. Either way, turning the
live map on, or repairing a value it rejected at startup, takes a restart
between two rounds. With the live map on, SIGHUP always means "revoke", never
"exit", a terminal hangup included.

## Turn it off

Remove the password, from `.env` and from the config file, and restart the
reader **between two rounds**. A restart in the middle of a round leaves that
round's recording so far unplayable.

## Proxies

The round is streamed as NDJSON in one long response, gzip-compressed for
browsers, and must not be buffered:

- **Traefik**: no `buffering` middleware on this router, and keep the
  entrypoint's `respondingTimeouts.writeTimeout` at 0 (the default).
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

Log in with curl and let it keep the cookie in a jar (that leaves the password
in your shell history), or copy the `sqr_live` cookie of a logged-in browser
from the dev tools (Application in Chrome, Storage in Firefox) and pass it with
`-H 'Cookie: sqr_live=<token>'` instead of `-b jar`:

    curl -c jar -H 'Content-Type: application/json' -d '{"password":"…"}' https://<your site>/api/live/login
    curl -s --compressed -b jar https://<your site>/api/live/round

prints the round being recorded, `{"id": "…", "startedAtUtc": "…", …}`: 401
without a valid cookie, 404 between rounds. Then

    curl -sN --compressed -b jar https://<your site>/api/live/round/<id>

prints its frames, one JSON object per line, and keeps printing new ones as
they are recorded.

## What the log says

| Line | Meaning |
|---|---|
| `live map enabled for moderators (password from SQREADER_LIVE_PASSWORD_HASH)`, `… (password from live_password)` | at startup: the live map is on, and where its password came from |
| `live map disabled: <source> <rule>` | at startup: the password was rejected and there is no live map; the rule is named, the value never |
| `live map disabled: it needs --recordings-dir` | at startup: there is a password, but nothing is recorded, so there is no round to show |
| `live: login ok from <ip> (session <id>)` | a moderator logged in |
| `live: login failed from <ip>` | a wrong password |
| `live: logout from <ip>` | a moderator logged out |
| `live: login limit reached for <ip>`, `live: global login limit reached` | 5 failures from one address, or 50 from everyone, within 10 minutes; logins wait, open sessions are unaffected; see "If logins are being blocked" |
| `live: round stream opened from <ip> (<n>/10)`, `live: round stream closed from <ip> after <t> (<reason>)` | a live map started or stopped streaming the round; the reason is `round ended`, `session ended`, `client gone`, `write timeout` or `bad data` |
| `live: stream refused from <ip> (10/10 in use)` | more than 10 live maps at once |
| `live: SIGHUP: all sessions revoked, password reloaded` | the file held a different password; everyone was logged out |
| `live: SIGHUP: all sessions revoked; live_password in <path> is UNCHANGED` | everyone was logged out, but the file still holds the old password; expected for a plain revoke, a problem after an edit (see the Docker bind mount above) |
| `live: SIGHUP: all sessions revoked; password from SQREADER_LIVE_PASSWORD_HASH is UNCHANGED (environment: change it with a restart between rounds)` | everyone was logged out; the password comes from the environment, which a running process cannot reload |
| `live: SIGHUP: all sessions revoked; NO valid live_password in <path> (<why>), logins disabled until fixed` | the file cannot be read or holds no valid password, and nobody can log in until it is fixed and SIGHUP is sent again; `<why>` is an error type such as `FileNotFoundError` or `JSONDecodeError`, or the rule the value broke; `<path>` is `None` if the path itself could not be found |

A login or a stream from an address you do not know means the password has
leaked: change it and send SIGHUP (or restart, for the environment's hash).
