# Docker deployment for sqreader

**Status:** approved design, ready for implementation planning
**Date:** 2026-08-26

Ship sqreader as a container that reads a Squad dedicated server's memory
across a container boundary, and let the operator choose where that server
lives: bundled in the same stack, in a container they already run, or native
on the host.

Nothing in this design changes the reader itself. Every file it adds is new,
and the upstream Squad image (`cm2network/squad`) is used unmodified.

## Why a separate container

sqreader needs four things from the game process, and only four:
`/proc/<pid>/maps`, `/proc/<pid>/mem` (or `process_vm_readv`),
`/proc/<pid>/exe` for the build hash, and the server's `SquadGame.log` for the
kill feed. It never opens the game binary by path and never writes to the game.

A shared PID namespace supplies the first three. The fourth is a read-only
bind mount. That is the whole coupling.

Installing sqreader *into* the Squad image was considered and rejected. The
tempting argument — "same container, so no ptrace privileges needed" — is
false: sqreader would be a *sibling* of the game process, not an ancestor, and
`kernel.yama.ptrace_scope=1` (the default on Debian/Ubuntu) grants
`PTRACE_MODE_ATTACH` to ancestors only. It needs exactly the same capability
either way. What the single-container variant would additionally cost:

- a fork of `cm2network/squad`, rebuilt on every upstream change;
- two lifecycles behind one PID 1, so restarting the reader restarts the game;
- Python dependencies and reader writes inside the game's data volume.

### Measured

Verified on this host (Docker 29.7.2, Compose v5.4.0, AppArmor + seccomp
enabled, `kernel.yama.ptrace_scope=1`) with throwaway containers, target
process running as uid 1000:

| Reader configuration | `maps` | `mem` open | read |
|---|---|---|---|
| **`pid: container:<peer>`**, default caps | ✗ EACCES | — | — |
| `pid: container:<peer>`, `cap_drop ALL` + `SYS_PTRACE` | ✓ | ✗ EACCES | — |
| `pid: container:<peer>`, `cap_drop ALL` + `SYS_PTRACE` + `DAC_READ_SEARCH` | ✓ | ✓ | ✓ |
| **`pid: host`**, same caps, AppArmor `docker-default` | ✗ EACCES | — | — |
| `pid: host`, same caps, AppArmor `unconfined` | ✓ | ✓ | ✓ |

Two separate gates, which is why one capability is not enough:

- `/proc/<pid>/maps` is mode 0444 — blocked only by the ptrace-read check, so
  `SYS_PTRACE` clears it. Under AppArmor's `docker-default` profile the rule
  is `ptrace (trace,read) peer=docker-default`, which covers a peer
  *container* but not an unconfined *host* process — hence the extra
  `apparmor=unconfined` in host mode only.
- `/proc/<pid>/mem` is mode 0600 owned by `steam` — the DAC check applies on
  top, so `DAC_READ_SEARCH` is required as well. An early probe passed without
  it only because Docker's *default* capability set still carried
  `DAC_OVERRIDE`; under `cap_drop: [ALL]` it does not.

`README.md` already states this pair correctly ("run as root, or grant
`CAP_SYS_PTRACE` (and `CAP_DAC_READ_SEARCH`)"). Also confirmed working across
the boundary: `open("/proc/<pid>/exe")` for `squad_build.build_sha256`, and
`process_vm_readv` for `mem.read_many`'s fast path.

## Topology and mode selection

One `docker-compose.yml` at the repo root. The bundled game server sits behind
a Compose profile; the reader interpolates the two settings that differ between
modes from `.env`.

```yaml
services:
  squad:
    image: ${SQUAD_IMAGE:-cm2network/squad}
    profiles: ["bundled-squad"]
    restart: unless-stopped
    network_mode: host            # upstream's recommendation
    volumes:
      - ${SQUAD_DATA:-./squad-data}:/home/steam/squad-dedicated
    environment:
      PORT: ${PORT:-7787}
      QUERYPORT: ${QUERYPORT:-27165}
      BEACONPORT: ${BEACONPORT:-15000}
      RCONPORT: ${RCONPORT:-21114}
      FIXEDMAXPLAYERS: ${FIXEDMAXPLAYERS:-80}
      SERVER_NAME: ${SERVER_NAME:-Squad Dedicated Server}

  sqreader:
    build: { context: ., dockerfile: docker/Dockerfile }
    image: sqreader:local
    restart: unless-stopped
    pid: "${SQUAD_PID_MODE:-service:squad}"
    cap_drop: ["ALL"]
    cap_add: ["SYS_PTRACE", "DAC_READ_SEARCH"]
    security_opt: ["apparmor=${SQUAD_APPARMOR:-docker-default}"]
    stop_grace_period: 30s
    ports:
      - "${SQREADER_BIND:-127.0.0.1}:${SQREADER_PORT:-8080}:8080"
    volumes:
      - ${SQUAD_DATA:-./squad-data}:/squad:ro
      - sqreader-data:/data
    environment: [ ... see "Runtime knobs" ... ]

volumes:
  sqreader-data:
```

Modes, each one contiguous block in `.env.example`:

| Mode | `COMPOSE_PROFILES` | `SQUAD_PID_MODE` | `SQUAD_APPARMOR` |
|---|---|---|---|
| 1 · reference stack (game bundled) | `bundled-squad` | `service:squad` | `docker-default` |
| 2 · drop-in (existing Squad container) | *(empty)* | `container:<name>` | `docker-default` |
| 3 · host (LinuxGSM, bare metal) | *(empty)* | `host` | `unconfined` |

Mode 1 is the default, so a fresh clone runs with `docker compose up -d` and no
`.env` at all.

`apparmor=docker-default` is passed explicitly rather than omitted, so the list
element is never empty and the value is a plain interpolation. Verified
accepted by the daemon.

An inconsistent pair — `SQUAD_PID_MODE=service:squad` with the profile inactive
— fails at project-load time, before any container starts, with
`service "sqreader" depends on undefined service "squad": invalid compose
project`. That is a good enough guard; no extra validation is written.

`cap_add` and `cap_drop` are identical in all three modes. Only `pid` and
`security_opt` vary, and both are single interpolated strings — which is what
makes one file serve all three.

## The reader image (`docker/Dockerfile`)

`python:3.11-slim-bookworm`, plus:

- `procps`, so `find_squad_server_pid`'s first and most reliable branch
  (`pidof -s SquadGameServer`) works. Without it resolution silently degrades
  to the generic `/proc` scan, which happens to work only because
  `SquadGameServer` is exactly 15 characters and so survives `comm`
  truncation. Not a coincidence worth depending on.
- `pip install .[fast]` — the `fast` extra pulls numpy, which vectorizes the
  incremental `GUObjectArray` diff. Wheels exist for both numpy and
  `zstandard`, so no compiler is needed in the final image.
- `WORKDIR /app`, with `COPY icons/ sqmaps/ frontend/dist/` landing at
  `/app/icons`, `/app/sqmaps` and `/app/frontend/dist` — the paths the
  entrypoint passes. `frontend/dist` is committed, so there is no Node stage.

Not Nuitka. The compiled artifact in `packaging/` exists to spare operators a
Python toolchain; an image ships one regardless. Building from source has a
second, larger benefit: `updater.running_binary(compiled=_is_compiled())`
returns `None` for a source install, so remote self-update — which stages a
binary and expects systemd's `ExecStartPre` to swap it — is inert by
construction rather than by a flag someone can flip in an immutable image.

`.dockerignore` excludes `.git`, `recordings/`, `stats/`, `frontend/node_modules`,
`frontend/src`, `tests/`, `scripts/`, `__pycache__`.

## Container process: `serve` plus a retention loop

`docker/entrypoint.sh` runs the pruner immediately, then on an interval in the
background, then hands PID 1 to the reader:

```sh
#!/bin/sh
set -eu

# `serve` creates these itself (cli.py:642, stats.py:592), but the pruner runs
# first and `cmd_retention` returns 1 on a missing directory — so on a fresh
# volume the very first log line would be a spurious "recordings dir not found".
mkdir -p /data/recordings /data/stats

# An operator typo here would otherwise make `-gt` fail under `set -e` and kill
# the container at boot with nothing but a shell error to go on.
RETENTION_INTERVAL="${RETENTION_INTERVAL:-86400}"
case "$RETENTION_INTERVAL" in
  ''|*[!0-9]*)
    echo "entrypoint: RETENTION_INTERVAL must be a whole number of seconds" \
         "(got '$RETENTION_INTERVAL'); use 0 to disable pruning" >&2
    exit 1 ;;
esac

if [ "$RETENTION_INTERVAL" -gt 0 ]; then
  (
    while :; do
      sqreader retention \
        --recordings-dir /data/recordings \
        --max-age-days   "${RETENTION_MAX_AGE_DAYS:-90}" \
        --max-total-gb   "${RETENTION_MAX_TOTAL_GB:-150}" \
        --min-free-gb    "${RETENTION_MIN_FREE_GB:-50}" \
        --min-keep       "${RETENTION_MIN_KEEP:-3}" || true
      sleep "$RETENTION_INTERVAL"
    done
  ) &
fi

# Optional two-tier recording, assembled positionally rather than with
# `${RECORD_HZ:+--record-hz "$RECORD_HZ"}` — that expansion's quoting behaviour
# is subtle enough to be a liability in a file nobody reads twice.
set --
if [ -n "${RECORD_HZ:-}" ]; then
  set -- --record-hz "$RECORD_HZ"
fi

exec sqreader serve \
  --host 0.0.0.0 --port 8080 \
  --hz "${SQREADER_HZ:-0.5}" \
  --server-id "${SERVER_ID:-squad}" \
  --recordings-dir /data/recordings \
  --stats-db /data/stats/player_stats.db \
  --icons-dir /app/icons \
  --sqmaps-dir /app/sqmaps \
  --frontend-dir /app/frontend/dist \
  --squad-log /squad/SquadGame/Saved/Logs/SquadGame.log \
  "$@"
```

Design points:

- **No `--pid`.** `_open_pipeline_or_wait` (cli.py:174) re-resolves the PID on
  every retry and stays alive when the game is absent, so the first boot simply
  waits out SteamCMD's download and a game restart is survived without a
  wrapper script or a `depends_on` healthcheck.
- **`exec`**, so `serve` is PID 1 and receives Docker's SIGTERM directly. It
  installs a handler (cli.py:1022) and its `finally` block writes the `.sqrx`
  footer for an in-flight recording while deliberately leaving the match row
  open (cli.py:1285ff). `stop_grace_period: 30s` gives that room; the 10 s
  default risks a SIGKILL mid-footer.
- **Retention runs first, then sleeps.** A container that restarts onto an
  already-full disk must not wait a day for the safety valve. Repeated runs are
  harmless — the policies are idempotent and a file younger than
  `--in-progress-min` is never touched.
- The background loop is a single long-lived shell reparented to PID 1; it
  reaps its own children and exits with the container. No supervisor, no
  second container.

### Runtime knobs

| Env | Default | Effect |
|---|---|---|
| `RETENTION_INTERVAL` | `86400` | seconds between passes; `0` disables the loop entirely |
| `RETENTION_MAX_AGE_DAYS` | `90` | `0` disables this policy |
| `RETENTION_MAX_TOTAL_GB` | `150` | `0` disables this policy |
| `RETENTION_MIN_FREE_GB` | `50` | `0` disables this policy |
| `RETENTION_MIN_KEEP` | `3` | newest N recordings are never pruned |
| `SQREADER_HZ` | `0.5` | full-snapshot rate |
| `RECORD_HZ` | *(unset)* | position-frame rate; unset keeps the single-tier loop |
| `SERVER_ID` | `squad` | snapshot label and stats-DB partition key |
| `SQREADER_CONFIG` | *(unset)* | path to a mounted `sqreader.config.json` |
| `SQREADER_LOG_LEVEL` | `INFO` | honoured by `cmd_serve`'s `logging.basicConfig` |

The retention defaults reproduce `deploy/sqreader-retention.service` exactly.
Each of the three policies is truthiness-guarded in `cmd_retention`
(cli.py:2257–2278), so `0` is a real off switch and all three at `0` make the
pass a no-op — the loop can be neutered without being removed.

`--min-free-gb` calls `shutil.disk_usage` on the recordings directory, which
inside the container reports the *host* filesystem backing the volume. That is
the intended meaning.

`RECORD_HZ` deserves a note: `README.md` describes two-tier recording (≈1 Hz
full snapshots plus 4 Hz position frames) as how a match is recorded, but
`deploy/sqreader-prod.service` passes no `--record-hz` and therefore runs
single-tier. The container reproduces the *unit's* behaviour by default and
exposes the knob rather than silently picking a side. Reconciling the two is
out of scope here.

## Volumes, ports, log discovery

The kill-feed log does **not** require mounting the game at a matching path.
`cmd_serve` resolves it as `args.squad_log or find_squad_log(pid)` (cli.py:570),
so an explicit `--squad-log` wins over `log_from_pid`'s
`realpath("/proc/<pid>/exe")` derivation — which would otherwise yield a path
valid only inside the game's mount namespace. Squad's layout is fixed relative
to its install root, so `<root>/SquadGame/Saved/Logs/SquadGame.log` is a
constant. Mounting the install root read-only at a fixed `/squad` therefore
works identically in all three modes and needs one variable, `SQUAD_DATA`.

| Mount | Purpose |
|---|---|
| `${SQUAD_DATA}:/squad:ro` | the kill-feed log, and nothing else |
| `sqreader-data:/data` | recordings and `stats/player_stats.db` |
| `./sqreader.config.json:/app/sqreader.config.json:ro` (optional) | with `SQREADER_CONFIG` |

Losing the log degrades quietly and expensively — the reader keeps running,
falls back to memory-sampled hits, and produces stats that look fine while
undercounting kills. `cmd_serve` already prints a loud warning in that case;
the README section must say that seeing it means the `/squad` mount is wrong.

The reader binds `0.0.0.0:8080` inside the container — it has to, or the
published port would never reach it — but the host side is published to
`127.0.0.1` by default, matching `deploy/nginx_reverse-proxy.example.conf`.
`SQREADER_BIND=0.0.0.0` opens it deliberately.

Plugins need no new plumbing. `config.py`'s `plugins_config` key exists for
"deployments whose start command is not ours to edit — a container entrypoint
baked into an image": mount a config file and set `SQREADER_CONFIG`.

Central push stays off. It requires `sqreader enroll` and the `push` extra
(`cryptography`); the README documents adding both, and nothing leaves the box
by default.

## Files

All new, all in this repo. `/home/hans/PhpstormProjects/Squad` is an unmodified
clone of `CM2Walki/Squad` and is not touched.

```
docker/Dockerfile
docker/entrypoint.sh
docker-compose.yml
.env.example
.dockerignore
tests/test_docker_entrypoint.py
README.md                          (+ "Run in Docker" section)
```

## Testing

The only non-trivial logic added is the entrypoint, so that is what gets a
test. `tests/test_docker_entrypoint.py`:

- runs `docker/entrypoint.sh` with a stub `sqreader` on `PATH` that records its
  argv and exits, and asserts the assembled `serve` command — bind address,
  the four fixed paths, `--squad-log`, and that no `--pid` is passed;
- asserts `RETENTION_INTERVAL=0` starts no pruner, and that a non-zero interval
  invokes `retention` exactly once. Note it must NOT assert ordering against
  `serve`: the pruner is a background subshell racing an `exec`, so which line
  lands first is genuinely undefined — a dry run of this entrypoint showed the
  `serve` call printing first. Asserting the order would buy a flaky test;
- asserts the retention policy values are threaded through from the environment,
  including `0`, and that `RECORD_HZ` appends `--record-hz` only when set;
- asserts a non-numeric `RETENTION_INTERVAL` exits non-zero with a message
  naming the variable, rather than dying inside `[ -gt ]`;
- and, skipped when `shutil.which("docker")` is None, runs
  `docker compose config` for all three `.env` modes, asserting the resolved
  `pid` and `security_opt` and that mode 1 alone includes the `squad` service.
  Both mode 1 and mode 3 broke during design probes; this is the regression that
  is actually worth pinning.

No Docker build runs in the test suite.

## Deliberately excluded

- **RCON.** Not wired into the agent at all — `grep -rn rcon sqreader/` returns
  nothing; only the standalone `scripts/rcon_diff.py` speaks it. Nothing to
  containerise.
- **supervisord / s6.** Two processes, one of them a `sleep` loop.
- **A separate retention container.** Explicitly chosen against: the pruner
  belongs in the reader container, logrotate-style.
- **A Compose `healthcheck` on `/health`.** `restart: unless-stopped` plus the
  endless retry in `_open_pipeline_or_wait` already cover the failure modes an
  unhealthy mark would report.
- **Changes to the reader.** If the design needed one, it would be a signal the
  topology is wrong.
