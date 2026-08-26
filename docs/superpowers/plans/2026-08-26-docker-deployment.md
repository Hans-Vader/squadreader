# Docker Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship sqreader as a container that reads a Squad dedicated server's memory across a container boundary, with the operator choosing where that server lives.

**Architecture:** One `docker-compose.yml` defines a reader service plus an optional bundled game service behind a Compose profile. The reader joins the game's PID namespace and is granted exactly two capabilities. A shell entrypoint runs a logrotate-style pruner in the background and hands PID 1 to `sqreader serve`. No reader source code changes.

**Tech Stack:** Docker Compose v2+ (verified on v5.4.0), `python:3.11-slim-bookworm`, POSIX `sh`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-26-docker-deployment-design.md`

## Global Constraints

- **No changes to `sqreader/`.** If a task seems to need one, stop and report — it means the topology is wrong.
- **`/home/hans/PhpstormProjects/Squad` is never modified.** It is an unmodified clone of `CM2Walki/Squad`; the upstream image is consumed as published.
- **Capabilities are identical in all three modes:** `cap_drop: ["ALL"]`, `cap_add: ["SYS_PTRACE", "DAC_READ_SEARCH"]`. Both are required — `SYS_PTRACE` clears the ptrace gate on `/proc/<pid>/maps` (0444), `DAC_READ_SEARCH` clears the DAC gate on `/proc/<pid>/mem` (0600, owned by the game's user). Never "simplify" to one.
- **The reader container runs as root.** Do not add a `USER` line; the capabilities above are only effective for uid 0.
- **Retention defaults, copied from `deploy/sqreader-retention.service`:** 90 days, 150 GB, 50 GB free, min-keep 3, once per 86400 s.
- **`SQREADER_HZ` defaults to `0.5`**, matching `deploy/sqreader-prod.service` — not argparse's `3.0`.
- **Python ≥ 3.10** (`pyproject.toml`); the image pins 3.11.
- **ruff:** line length 100, `select = ["E", "F", "W", "B", "UP"]`. `tests/*.py` already has `E501` ignored.
- **Commit messages carry no `Co-Authored-By` or other AI-attribution trailer.**
- Test style follows the repo: a module docstring saying *why* the file exists, `from __future__ import annotations`, and sentence-shaped test names (see `tests/test_retention.py`, `tests/test_notify.py`).

---

### Task 1: The container entrypoint

The only non-trivial logic in this plan. It assembles the `serve` command line and runs the retention pruner beside it.

**Files:**
- Create: `docker/entrypoint.sh`
- Test: `tests/test_docker_entrypoint.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: an executable `docker/entrypoint.sh` that reads these environment
  variables — `SQREADER_DATA_DIR` (default `/data`), `RETENTION_INTERVAL`
  (default `86400`), `RETENTION_MAX_AGE_DAYS` (`90`), `RETENTION_MAX_TOTAL_GB`
  (`150`), `RETENTION_MIN_FREE_GB` (`50`), `RETENTION_MIN_KEEP` (`3`),
  `SQREADER_HZ` (`0.5`), `SERVER_ID` (`squad`), `RECORD_HZ` (unset) — and
  `exec`s `sqreader serve`. Task 2 sets it as the image `ENTRYPOINT`; Task 3
  supplies these variables from `.env`.

**Two traps this task exists to avoid.** Both were hit while designing it:

1. The pruner is a background subshell racing an `exec`, so **whether `retention` or `serve` logs first is undefined.** A dry run showed `serve` first. Never assert ordering between them.
2. Because that subshell inherits the parent's stdout, **capturing output through a pipe can hang** until the background loop ends. The tests below write to a file and use a stub `sleep` that exits non-zero, which under `set -e` terminates the loop after exactly one pass.

- [ ] **Step 1: Write the failing test**

Create `tests/test_docker_entrypoint.py`:

```python
"""What the container actually runs.

The reader itself is unchanged by the Docker work; this file is the whole of
the new logic. It pins three things that are quiet when they break: that the
reader is started WITHOUT --pid (so it re-resolves the game on every retry
instead of freezing a stale one), that the retention pruner can be switched
off and configured rather than being a hardcoded policy, and that an operator
typo in an interval produces a sentence instead of a shell error.

The stubs deserve a word. `sleep` is stubbed to exit non-zero so `set -e` ends
the pruner's `while` loop after one pass — otherwise the loop would run
forever and the assertions would race it. Output goes to a FILE, never a pipe:
the pruner is a background subshell holding the parent's stdout, and a pipe
would not close until it exits.

Nothing here asserts an ordering between the `retention` and `serve` calls.
The pruner is backgrounded and `serve` is `exec`ed, so which one reaches the
log first is genuinely undefined — a dry run showed `serve` winning.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO / "docker" / "entrypoint.sh"

_SQREADER_STUB = """#!/bin/sh
printf 'sqreader %s\\n' "$*" >> "$STUB_LOG"
"""

# Exits 1 on purpose: under `set -e` that ends the pruner's `while` loop after
# a single pass, so the test never races an endless background job.
_SLEEP_STUB = """#!/bin/sh
printf 'sleep %s\\n' "$*" >> "$STUB_LOG"
exit 1
"""


def run_entrypoint(tmp_path: Path, **env: str) -> list[str]:
    """Run the entrypoint with stubbed `sqreader`/`sleep`; return logged calls."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in (("sqreader", _SQREADER_STUB), ("sleep", _SLEEP_STUB)):
        p = bindir / name
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)

    # Truncate: several tests call this twice with the same tmp_path, and a log
    # that accumulated across runs would show two `serve` calls for one run.
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    stdout = tmp_path / "stdout.txt"
    environ = dict(os.environ)
    environ.update({
        "PATH": f"{bindir}:{environ['PATH']}",
        "STUB_LOG": str(log),
        "SQREADER_DATA_DIR": str(tmp_path / "data"),
    })
    environ.update(env)

    # A FILE, not a pipe — see the module docstring.
    with stdout.open("w", encoding="utf-8") as fh:
        proc = subprocess.run(["sh", str(ENTRYPOINT)], env=environ,
                              stdout=fh, stderr=subprocess.STDOUT, timeout=30)
    run_entrypoint.last_returncode = proc.returncode
    run_entrypoint.last_output = stdout.read_text(encoding="utf-8")
    return log.read_text(encoding="utf-8").splitlines()


def serve_call(calls: list[str]) -> str:
    matches = [c for c in calls if c.startswith("sqreader serve")]
    assert len(matches) == 1, f"expected exactly one serve call, got {calls}"
    return matches[0]


def retention_calls(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("sqreader retention")]


# --- the reader ------------------------------------------------------------

def test_the_reader_is_started_without_a_pid(tmp_path):
    """--pid would freeze whichever process existed at boot. Leaving it off is
    what lets _open_pipeline_or_wait sit through SteamCMD's first download and
    survive a game restart, so this is the assertion that matters most."""
    call = serve_call(run_entrypoint(tmp_path))
    assert "--pid" not in call


def test_the_reader_binds_all_interfaces_inside_the_container(tmp_path):
    """It has to: a published port cannot reach a 127.0.0.1 bind. The host-side
    narrowing is the compose file's job, not this one's."""
    call = serve_call(run_entrypoint(tmp_path))
    assert "--host 0.0.0.0 --port 8080" in call


def test_the_reader_is_pointed_at_the_mounted_log_and_assets(tmp_path):
    call = serve_call(run_entrypoint(tmp_path))
    assert "--squad-log /squad/SquadGame/Saved/Logs/SquadGame.log" in call
    assert "--icons-dir /app/icons" in call
    assert "--sqmaps-dir /app/sqmaps" in call
    assert "--frontend-dir /app/frontend/dist" in call


def test_the_tick_rate_follows_the_production_unit_not_argparse(tmp_path):
    """deploy/sqreader-prod.service runs 0.5 Hz; argparse defaults to 3.0."""
    assert "--hz 0.5" in serve_call(run_entrypoint(tmp_path))
    assert "--hz 2" in serve_call(run_entrypoint(tmp_path, SQREADER_HZ="2"))


def test_two_tier_recording_is_off_unless_asked_for(tmp_path):
    assert "--record-hz" not in serve_call(run_entrypoint(tmp_path))
    assert "--record-hz 4" in serve_call(run_entrypoint(tmp_path, RECORD_HZ="4"))


def test_the_server_id_is_settable(tmp_path):
    assert "--server-id squad" in serve_call(run_entrypoint(tmp_path))
    assert "--server-id eu-1" in serve_call(run_entrypoint(tmp_path, SERVER_ID="eu-1"))


# --- the pruner ------------------------------------------------------------

def test_the_pruner_runs_once_with_the_units_policy(tmp_path):
    """Defaults are deploy/sqreader-retention.service, verbatim."""
    calls = retention_calls(run_entrypoint(tmp_path))
    assert len(calls) == 1
    assert "--max-age-days 90" in calls[0]
    assert "--max-total-gb 150" in calls[0]
    assert "--min-free-gb 50" in calls[0]
    assert "--min-keep 3" in calls[0]


def test_the_pruner_can_be_switched_off_entirely(tmp_path):
    calls = run_entrypoint(tmp_path, RETENTION_INTERVAL="0")
    assert retention_calls(calls) == []
    assert serve_call(calls)          # the reader still starts


def test_each_policy_can_be_disabled_on_its_own(tmp_path):
    """cmd_retention guards every policy on truthiness, so 0 is a real off
    switch — the loop can be neutered without being removed."""
    calls = retention_calls(run_entrypoint(
        tmp_path, RETENTION_MAX_AGE_DAYS="0", RETENTION_MAX_TOTAL_GB="0",
        RETENTION_MIN_FREE_GB="0"))
    assert "--max-age-days 0" in calls[0]
    assert "--max-total-gb 0" in calls[0]
    assert "--min-free-gb 0" in calls[0]


def test_the_pruner_sleeps_for_the_configured_interval(tmp_path):
    calls = run_entrypoint(tmp_path, RETENTION_INTERVAL="900")
    assert "sleep 900" in calls


def test_the_data_directories_exist_before_the_pruner_looks(tmp_path):
    """serve creates them itself, but the pruner runs first and cmd_retention
    returns 1 on a missing directory — a fresh volume would open with a false
    alarm in the log."""
    run_entrypoint(tmp_path)
    assert (tmp_path / "data" / "recordings").is_dir()
    assert (tmp_path / "data" / "stats").is_dir()


def test_a_mistyped_interval_is_a_sentence_not_a_shell_error(tmp_path):
    """`[ -gt ]` on a non-number fails under `set -eu` and kills the container
    at boot with nothing an operator can act on."""
    run_entrypoint(tmp_path, RETENTION_INTERVAL="abends")
    assert run_entrypoint.last_returncode == 1
    assert "RETENTION_INTERVAL" in run_entrypoint.last_output
    assert "abends" in run_entrypoint.last_output


# --- shell hygiene ---------------------------------------------------------

@pytest.mark.skipif(shutil.which("dash") is None, reason="dash not installed")
def test_the_entrypoint_is_portable_posix_shell(tmp_path):
    """The image has no bash guarantee, and the shebang says /bin/sh."""
    assert subprocess.run(["dash", "-n", str(ENTRYPOINT)]).returncode == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_docker_entrypoint.py -v`
Expected: every test FAILS — `docker/entrypoint.sh` does not exist yet, so `sh` exits 127.

- [ ] **Step 3: Write the entrypoint**

Create `docker/entrypoint.sh`:

```sh
#!/bin/sh
#
# Two things run in this container: the reader, and a logrotate-style pruner
# for the recordings it writes. The pruner is a background loop rather than a
# second container because a full disk takes the GAME down, not just us — the
# valve belongs next to the thing that opens it.
set -eu

# SQREADER_DATA_DIR exists so this file is runnable outside a container (the
# test harness cannot write /data). In the image it is always the default.
DATA_DIR="${SQREADER_DATA_DIR:-/data}"

# `serve` creates these itself (cli.py:642, stats.py:592), but the pruner runs
# first and `cmd_retention` returns 1 on a missing directory — so on a fresh
# volume the very first log line would be a spurious "recordings dir not found".
mkdir -p "$DATA_DIR/recordings" "$DATA_DIR/stats"

# An operator typo here would otherwise make `-gt` fail under `set -e` and kill
# the container at boot with nothing but a shell error to go on.
RETENTION_INTERVAL="${RETENTION_INTERVAL:-86400}"
case "$RETENTION_INTERVAL" in
  ''|*[!0-9]*)
    echo "entrypoint: RETENTION_INTERVAL must be a whole number of seconds" \
         "(got '$RETENTION_INTERVAL'); use 0 to disable pruning" >&2
    exit 1 ;;
esac

# Prune immediately, then on the interval: a container restarting onto an
# already-full disk must not wait a day for the safety valve. Repeat passes are
# harmless — the policies are idempotent and cmd_retention never touches a file
# younger than --in-progress-min.
if [ "$RETENTION_INTERVAL" -gt 0 ]; then
  (
    while :; do
      sqreader retention \
        --recordings-dir "$DATA_DIR/recordings" \
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

# `exec`, so the reader is PID 1 and gets Docker's SIGTERM directly: it installs
# a handler (cli.py:1022) and its `finally` writes the .sqrx footer for an
# in-flight recording (cli.py:1285ff). Behind a shell it would be SIGKILLed.
#
# No --pid on purpose. _open_pipeline_or_wait (cli.py:174) re-resolves the game
# on every retry and stays alive when it is absent, so the first boot simply
# waits out SteamCMD's download and a game restart needs no wrapper.
exec sqreader serve \
  --host 0.0.0.0 --port 8080 \
  --hz "${SQREADER_HZ:-0.5}" \
  --server-id "${SERVER_ID:-squad}" \
  --recordings-dir "$DATA_DIR/recordings" \
  --stats-db "$DATA_DIR/stats/player_stats.db" \
  --icons-dir /app/icons \
  --sqmaps-dir /app/sqmaps \
  --frontend-dir /app/frontend/dist \
  --squad-log /squad/SquadGame/Saved/Logs/SquadGame.log \
  "$@"
```

Then: `chmod +x docker/entrypoint.sh`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_docker_entrypoint.py -v`
Expected: PASS (the `dash` test skips if dash is absent).

- [ ] **Step 5: Check the whole suite still passes and lint is clean**

Run: `python3 -m pytest -q`
Expected: no new failures.

Then lint. `ruff` is declared in `[project.optional-dependencies] dev` but is
NOT installed on this machine — install it first or the command fails with
`No module named ruff`, which is not a lint finding:

```bash
python3 -m pip install -e '.[dev]'      # once
python3 -m ruff check tests/test_docker_entrypoint.py
```

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add docker/entrypoint.sh tests/test_docker_entrypoint.py
git commit -m "Run the reader and a recordings pruner from one entrypoint"
```

---

### Task 2: The reader image

**Files:**
- Create: `docker/Dockerfile`
- Create: `.dockerignore`

**Interfaces:**
- Consumes: `docker/entrypoint.sh` from Task 1, as the image `ENTRYPOINT`.
- Produces: an image tagged `sqreader:local` with `sqreader` on `PATH`, `pidof` available, and the served assets at `/app/icons`, `/app/sqmaps`, `/app/frontend/dist`. Task 3's compose file builds it with `context: .` and `dockerfile: docker/Dockerfile`.

- [ ] **Step 1: Write the `.dockerignore`**

Create `.dockerignore`:

```
.git
.github
.idea
.claude
docs
deploy
packaging
scripts
tests

# The prebuilt SPA is committed and is the only part of frontend/ the image
# serves; the sources and node_modules are build-time only.
frontend
!frontend/dist

# Runtime output — never bake a previous run's data into an image.
recordings
stats
*.sqrx
*.log
sqreader.config.json
.env

__pycache__
*.pyc
.venv
venv
.pytest_cache
.mypy_cache
.ruff_cache
*.egg-info
```

- [ ] **Step 2: Write the Dockerfile**

Create `docker/Dockerfile`:

```dockerfile
# The reader, for running beside a Squad server rather than on it.
#
# Built from source rather than from packaging/build.sh's Nuitka artifact. That
# artifact exists to spare operators a Python toolchain, which an image supplies
# anyway — and building from source has a second, larger benefit: for a source
# install `updater.running_binary(compiled=False)` returns None, so remote
# self-update (which stages a binary and expects systemd's ExecStartPre to swap
# it) is inert BY CONSTRUCTION rather than by a flag someone can flip in an
# image that cannot rewrite itself.
FROM python:3.11-slim-bookworm

# procps supplies pidof, which is find_squad_server_pid's first and most
# reliable branch (config.py). Without it, resolution silently degrades to the
# generic /proc scan — which happens to work only because "SquadGameServer" is
# exactly 15 characters and so survives comm truncation. Not a coincidence
# worth depending on.
RUN apt-get update \
 && apt-get install -y --no-install-recommends procps \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# .[fast] pulls numpy, which vectorises the incremental GUObjectArray diff.
# numpy and zstandard both ship manylinux wheels, so no compiler is needed.
RUN pip install --no-cache-dir ".[fast]" \
 && chmod +x docker/entrypoint.sh

# No USER line, deliberately. CAP_SYS_PTRACE and CAP_DAC_READ_SEARCH are only
# effective for uid 0, and both are required to read the game's memory.
ENTRYPOINT ["/app/docker/entrypoint.sh"]
```

- [ ] **Step 3: Build the image**

Run: `docker build -f docker/Dockerfile -t sqreader:local .`
Expected: build succeeds.

- [ ] **Step 4: Verify the image contents**

Run:

```bash
docker run --rm --entrypoint sh sqreader:local -c \
  'sqreader version && command -v pidof && python3 -c "import numpy, zstandard; print(\"deps ok\")" \
   && ls -d /app/icons /app/sqmaps /app/frontend/dist /app/docker/entrypoint.sh \
   && test -x /app/docker/entrypoint.sh && echo "entrypoint executable"'
```

Expected: a version string, a `pidof` path, `deps ok`, all four paths listed, and `entrypoint executable`.

- [ ] **Step 5: Verify the entrypoint refuses a bad interval inside the real image**

This proves Task 1's guard survives packaging — the image's `/bin/sh` is not the host's.

Run: `docker run --rm -e RETENTION_INTERVAL=abends sqreader:local; echo "exit=$?"`
Expected: the `RETENTION_INTERVAL must be a whole number of seconds` message and `exit=1`.

- [ ] **Step 6: Commit**

```bash
git add docker/Dockerfile .dockerignore
git commit -m "Package the reader as an image that cannot update itself"
```

---

### Task 3: The compose stack and its three modes

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`
- Modify: `.gitignore` (add a negation — see Step 1)
- Modify: `tests/test_docker_entrypoint.py` (append a new section)

**Interfaces:**
- Consumes: the `sqreader:local` image from Task 2 and the environment variables Task 1's entrypoint reads.
- Produces: a compose project whose `sqreader` service resolves `pid` and `security_opt` from `.env`, and a `squad` service gated behind the `bundled-squad` profile.

**The `.gitignore` trap:** `.gitignore:4` is `.env.*`, which matches `.env.example` — confirmed with `git check-ignore -v .env.example`. Without the negation in Step 1, the file silently never gets committed and every operator lands on a missing-variable error with no example to copy.

- [ ] **Step 1: Un-ignore the example env file**

In `.gitignore`, immediately after the `.env.*` line, add:

```
# ...but the mode-selection template MUST be committed; .env.* above would eat it.
!.env.example
```

Verify: `git check-ignore -v .env.example; echo "exit=$?"`
Expected: `exit=1` (no longer ignored).

- [ ] **Step 2: Write the compose file**

Create `docker-compose.yml`:

```yaml
# sqreader beside a Squad dedicated server.
#
# WHICH Squad server it reads is chosen entirely in .env — see .env.example and
# start with `cp .env.example .env`. Only `pid` and `security_opt` differ
# between the three modes; the capability set is identical, because the kernel
# gates are the same in all of them.

services:
  # Only created when COMPOSE_PROFILES=bundled-squad. Operators who already run
  # a Squad server leave the profile off and point SQUAD_PID_MODE at theirs.
  squad:
    image: ${SQUAD_IMAGE:-cm2network/squad}
    profiles: ["bundled-squad"]
    restart: unless-stopped
    network_mode: host          # upstream's recommendation for this image
    volumes:
      - ${SQUAD_DATA:-./squad-data}:/home/steam/squad-dedicated
    environment:
      PORT: ${PORT:-7787}
      QUERYPORT: ${QUERYPORT:-27165}
      BEACONPORT: ${BEACONPORT:-15000}
      RCONPORT: ${RCONPORT:-21114}
      FIXEDMAXPLAYERS: ${FIXEDMAXPLAYERS:-80}
      FIXEDMAXTICKRATE: ${FIXEDMAXTICKRATE:-50}
      SERVER_NAME: ${SERVER_NAME:-Squad Dedicated Server}

  sqreader:
    build:
      context: .
      dockerfile: docker/Dockerfile
    image: sqreader:local
    restart: unless-stopped

    # Required, not defaulted. SQUAD_PID_MODE could carry a default but
    # COMPOSE_PROFILES cannot, so a defaulted `service:squad` would point at a
    # service the inactive profile never creates. Better a sentence than a
    # dangling reference.
    pid: "${SQUAD_PID_MODE:?no .env found — run: cp .env.example .env, then pick a mode in it}"

    # SYS_PTRACE clears the ptrace gate on /proc/<pid>/maps (0444).
    # DAC_READ_SEARCH clears the DAC gate on /proc/<pid>/mem (0600, owned by the
    # game's user). BOTH are required; Docker's default set only ever hid the
    # second one behind DAC_OVERRIDE.
    cap_drop: ["ALL"]
    cap_add: ["SYS_PTRACE", "DAC_READ_SEARCH"]

    # docker-default's `ptrace peer=docker-default` covers a peer container but
    # not an unconfined host process, so host mode needs `unconfined` here and
    # the other two modes do not.
    security_opt: ["apparmor=${SQUAD_APPARMOR:-docker-default}"]

    # serve installs a SIGTERM handler and writes the .sqrx footer on the way
    # out; the 10s default risks a SIGKILL mid-footer.
    stop_grace_period: 30s

    ports:
      # Host side is loopback by default, matching deploy/nginx_reverse-proxy.example.conf.
      # Set SQREADER_BIND=0.0.0.0 to expose it deliberately.
      - "${SQREADER_BIND:-127.0.0.1}:${SQREADER_PORT:-8080}:8080"

    volumes:
      # Read-only, and only for the kill-feed log. Nothing else is read from here.
      - ${SQUAD_DATA:-./squad-data}:/squad:ro
      - sqreader-data:/data
      # Optional: plugins, alert webhook, central URL. Uncomment to use one —
      # config.py picks it up from the working directory with no env var needed.
      # - ./sqreader.config.json:/app/sqreader.config.json:ro

    environment:
      SERVER_ID: ${SERVER_ID:-squad}
      SQREADER_HZ: ${SQREADER_HZ:-0.5}
      RECORD_HZ: ${RECORD_HZ:-}
      SQREADER_LOG_LEVEL: ${SQREADER_LOG_LEVEL:-INFO}
      # Only needed for a config file mounted somewhere OTHER than /app: WORKDIR
      # is /app and config.py falls back to `Path.cwd() / sqreader.config.json`,
      # so the mount below is self-sufficient on its own.
      SQREADER_CONFIG: ${SQREADER_CONFIG:-}
      RETENTION_INTERVAL: ${RETENTION_INTERVAL:-86400}
      RETENTION_MAX_AGE_DAYS: ${RETENTION_MAX_AGE_DAYS:-90}
      RETENTION_MAX_TOTAL_GB: ${RETENTION_MAX_TOTAL_GB:-150}
      RETENTION_MIN_FREE_GB: ${RETENTION_MIN_FREE_GB:-50}
      RETENTION_MIN_KEEP: ${RETENTION_MIN_KEEP:-3}

volumes:
  sqreader-data:
```

- [ ] **Step 3: Write the env template**

Create `.env.example`:

```bash
# Copy this file to .env and uncomment exactly ONE mode block.
#
#     cp .env.example .env
#
# All three modes use the same capabilities. Only the PID namespace and the
# AppArmor profile differ, because those are the only two things the kernel
# decides differently depending on where the game process lives.

# ── Mode 1 · this stack runs the game too ────────────────────────────────────
COMPOSE_PROFILES=bundled-squad
SQUAD_PID_MODE=service:squad
SQUAD_APPARMOR=docker-default

# ── Mode 2 · attach to a Squad container you already run ─────────────────────
# Use the container's NAME (docker ps), not the compose service name.
# COMPOSE_PROFILES=
# SQUAD_PID_MODE=container:my-squad
# SQUAD_APPARMOR=docker-default

# ── Mode 3 · Squad runs natively on the host (LinuxGSM, bare metal) ──────────
# apparmor=unconfined is not optional here: docker-default allows ptrace only
# toward peers under the same profile, so an unconfined host process is refused
# at /proc/<pid>/maps before the reader ever reaches the memory.
# COMPOSE_PROFILES=
# SQUAD_PID_MODE=host
# SQUAD_APPARMOR=unconfined

# ─────────────────────────────────────────────────────────────────────────────

# Where the game is installed, as the DOCKER HOST sees it. Mounted read-only at
# /squad in the reader, which reads exactly one file under it:
#   <SQUAD_DATA>/SquadGame/Saved/Logs/SquadGame.log
# In mode 1 this is also the game's own data volume, so it must be writable by
# the image's unprivileged user: `mkdir -p squad-data && chmod 777 squad-data`.
SQUAD_DATA=./squad-data

# Replay UI. Loopback by default; put it behind the nginx config in deploy/
# rather than setting SQREADER_BIND=0.0.0.0 on a public box.
SQREADER_BIND=127.0.0.1
SQREADER_PORT=8080

# Optional sqreader.config.json (plugins, alert webhook, central URL). Mount it
# by uncommenting the matching volume line in docker-compose.yml; it is picked
# up from the working directory automatically. Set this only if you mount it
# somewhere other than /app/sqreader.config.json.
# SQREADER_CONFIG=/app/sqreader.config.json

# Reader
SERVER_ID=squad
SQREADER_HZ=0.5
# RECORD_HZ=4          # two-tier recording: 4 Hz position frames between snapshots
# SQREADER_LOG_LEVEL=DEBUG

# Recording retention — a logrotate for replays. RETENTION_INTERVAL=0 turns the
# pruner off entirely; each policy below is disabled on its own with 0.
RETENTION_INTERVAL=86400
RETENTION_MAX_AGE_DAYS=90
RETENTION_MAX_TOTAL_GB=150
RETENTION_MIN_FREE_GB=50
RETENTION_MIN_KEEP=3

# Bundled game server (mode 1 only) — see the cm2network/squad README.
# PORT=7787
# QUERYPORT=27165
# BEACONPORT=15000
# RCONPORT=21114
# FIXEDMAXPLAYERS=80
# FIXEDMAXTICKRATE=50
# SERVER_NAME=Squad Dedicated Server
# SQUAD_IMAGE=cm2network/squad
```

- [ ] **Step 4: Write the failing mode tests**

Append to `tests/test_docker_entrypoint.py`:

```python
# --- the compose modes -----------------------------------------------------
#
# Both mode 1 and mode 3 broke while this was being designed — mode 1 because a
# defaulted `pid: service:squad` referenced a service the inactive profile never
# created, mode 3 because AppArmor refuses a peer it does not confine. Those are
# the regressions worth pinning.
#
# `json` is imported at the TOP of this file, with the others — ruff selects the
# full `E` set, so a mid-file import here would trip E402.

COMPOSE = REPO / "docker-compose.yml"

MODES = {
    "bundled": "COMPOSE_PROFILES=bundled-squad\nSQUAD_PID_MODE=service:squad\nSQUAD_APPARMOR=docker-default\n",
    "attach":  "COMPOSE_PROFILES=\nSQUAD_PID_MODE=container:my-squad\nSQUAD_APPARMOR=docker-default\n",
    "host":    "COMPOSE_PROFILES=\nSQUAD_PID_MODE=host\nSQUAD_APPARMOR=unconfined\n",
}

needs_docker = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker not installed")


def compose_config(tmp_path: Path, mode: str) -> dict:
    env_file = tmp_path / f"{mode}.env"
    env_file.write_text(MODES[mode], encoding="utf-8")
    proc = subprocess.run(
        ["docker", "compose", "--env-file", str(env_file),
         "-f", str(COMPOSE), "config", "--format", "json"],
        capture_output=True, text=True, timeout=120, cwd=REPO)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@needs_docker
def test_the_bundled_mode_brings_the_game_service_with_it(tmp_path):
    cfg = compose_config(tmp_path, "bundled")
    assert sorted(cfg["services"]) == ["sqreader", "squad"]
    assert cfg["services"]["sqreader"]["pid"] == "service:squad"


@needs_docker
@pytest.mark.parametrize("mode,pid", [
    ("attach", "container:my-squad"),
    ("host", "host"),
])
def test_the_other_modes_leave_the_game_service_out(tmp_path, mode, pid):
    """Starting a second Squad server for someone who already has one would be
    a very expensive surprise."""
    cfg = compose_config(tmp_path, mode)
    assert list(cfg["services"]) == ["sqreader"]
    assert cfg["services"]["sqreader"]["pid"] == pid


@needs_docker
def test_host_mode_is_the_only_one_that_unconfines_apparmor(tmp_path):
    for mode, want in (("bundled", "apparmor=docker-default"),
                       ("attach", "apparmor=docker-default"),
                       ("host", "apparmor=unconfined")):
        opts = compose_config(tmp_path, mode)["services"]["sqreader"]["security_opt"]
        assert opts == [want], f"{mode}: {opts}"


@needs_docker
@pytest.mark.parametrize("mode", list(MODES))
def test_every_mode_grants_both_capabilities_and_no_others(tmp_path, mode):
    """SYS_PTRACE alone opens /proc/<pid>/maps but not /proc/<pid>/mem."""
    svc = compose_config(tmp_path, mode)["services"]["sqreader"]
    assert svc["cap_drop"] == ["ALL"]
    assert sorted(svc["cap_add"]) == ["DAC_READ_SEARCH", "SYS_PTRACE"]


@needs_docker
def test_the_replay_ui_is_not_published_to_the_world_by_default(tmp_path):
    port = compose_config(tmp_path, "bundled")["services"]["sqreader"]["ports"][0]
    assert port["host_ip"] == "127.0.0.1"


@needs_docker
def test_a_missing_env_file_names_the_fix(tmp_path):
    """The fresh-clone case. Compose cannot default COMPOSE_PROFILES, so the
    mode has to be stated rather than guessed — say so instead of dangling."""
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    proc = subprocess.run(
        ["docker", "compose", "--env-file", str(empty), "-f", str(COMPOSE), "config"],
        capture_output=True, text=True, timeout=120, cwd=REPO)
    assert proc.returncode != 0
    assert "SQUAD_PID_MODE" in proc.stderr
    assert "cp .env.example .env" in proc.stderr


@needs_docker
def test_a_mode_that_contradicts_its_profile_is_refused_before_anything_starts(tmp_path):
    bad = tmp_path / "bad.env"
    bad.write_text("COMPOSE_PROFILES=\nSQUAD_PID_MODE=service:squad\n", encoding="utf-8")
    proc = subprocess.run(
        ["docker", "compose", "--env-file", str(bad), "-f", str(COMPOSE), "config"],
        capture_output=True, text=True, timeout=120, cwd=REPO)
    assert proc.returncode != 0
    assert "undefined service" in proc.stderr
```

- [ ] **Step 5: Run the mode tests to verify they fail**

Run: `python3 -m pytest tests/test_docker_entrypoint.py -v -k "mode or capabilit or apparmor or env_file or publish"`
Expected: FAIL before Steps 2–3 are in place; run this after writing them and it should PASS. If you are following the steps in order, run it now and confirm PASS.

- [ ] **Step 6: Prove the privileges actually work, not merely that they resolve**

`docker compose config` proves the YAML says the right thing. It cannot prove the kernel agrees. This step attaches the real reader image to a stub target that impersonates the game process, and asserts the reader can read its memory. It is the check that would have caught the missing `DAC_READ_SEARCH`.

Run:

```bash
docker rm -f sqstub >/dev/null 2>&1
docker run -d --name sqstub --user 1000:1000 debian:bookworm-slim \
  sh -c 'MARKER=SQREADERCANARY exec sleep 300' >/dev/null

docker run --rm --pid=container:sqstub \
  --cap-drop=ALL --cap-add=SYS_PTRACE --cap-add=DAC_READ_SEARCH \
  --entrypoint python3 sqreader:local -c '
import os, re
pid = 1
maps = open(f"/proc/{pid}/maps").read()
fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
regions = [(int(a,16), int(b,16)) for a,b in
           re.findall(r"^([0-9a-f]+)-([0-9a-f]+) r.*\[stack\]$", maps, re.M)]
found = any(b"SQREADERCANARY" in os.pread(fd, e-s, s) for s, e in regions)
print("cross-container memory read:", "OK" if found else "FAIL")
assert found
'
docker rm -f sqstub >/dev/null
```

Expected: `cross-container memory read: OK`.

Then confirm the negative case — drop `--cap-add=DAC_READ_SEARCH` from the second command and re-run. Expected: `PermissionError` on `/proc/1/mem`. If it succeeds without the capability, something in the environment is granting it and the compose file's minimality claim is wrong — report that rather than proceeding.

- [ ] **Step 7: Bring mode 1 up with stub images and confirm the namespace is shared**

Verifies `pid: service:squad` against a `network_mode: host` service — the combination is unusual enough to be worth one real run, and it needs no Squad download.

Run:

```bash
cat > /tmp/sqr-mode1.env <<'EOF'
COMPOSE_PROFILES=bundled-squad
SQUAD_PID_MODE=service:squad
SQUAD_APPARMOR=docker-default
SQUAD_IMAGE=nginx:alpine
SQUAD_DATA=/tmp/sqr-stub-data
EOF
mkdir -p /tmp/sqr-stub-data
# `up -d` with no service name, NOT `up -d squad`: naming a service starts that
# service and its dependencies, not its dependents, so `up -d squad` would leave
# the reader down and the next command with nothing to exec into.
docker compose --env-file /tmp/sqr-mode1.env up -d
docker compose --env-file /tmp/sqr-mode1.env exec -T sqreader \
  sh -c 'cat /proc/1/cmdline | tr "\0" " "; echo'
docker compose --env-file /tmp/sqr-mode1.env down -t 5
```

The stub image must stay up on its own. `debian:bookworm-slim` will not do: its
default command is a shell, which exits immediately with no TTY, so the PID
namespace the reader is supposed to join dies before it can join it.
`nginx:alpine` runs a real foreground daemon and needs no `command:` override —
which matters, because the compose file deliberately does not set one for the
game service.

Expected: the reader's PID 1 is the `squad` service's process, not its own.
Note the stub `squad` image has no Squad in it, so the reader will log
`[degraded] cannot read the game (no SquadGameServer process running)` and keep
retrying — that is the correct behaviour, not a failure.

- [ ] **Step 8: Run the whole suite and lint**

Run: `python3 -m pytest -q && python3 -m ruff check tests/`
Expected: no failures, no lint findings. (`ruff` needs
`python3 -m pip install -e '.[dev]'` first — see Task 1, Step 5.)

- [ ] **Step 9: Commit**

```bash
git add docker-compose.yml .env.example .gitignore tests/test_docker_entrypoint.py
git commit -m "Let an operator pick where the game process lives"
```

---

### Task 4: Document it

**Files:**
- Modify: `README.md` (new section after "Install")

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: no code.

- [ ] **Step 1: Add the section**

Insert into `README.md` directly after the `## Install` section and before `## Configuration`:

````markdown
## Run in Docker

The reader can run in its own container beside a containerised Squad server,
reading the game's memory across the container boundary. It needs two
capabilities and a shared PID namespace — nothing else, and no changes to the
game's image.

```bash
cp .env.example .env      # then uncomment ONE mode block in it
docker compose up -d
```

`.env` is required: Compose can default the PID mode but not the profile that
creates the game service, so the mode is stated rather than guessed. Running
without it prints exactly which file to copy.

### Three modes

| Your setup | `SQUAD_PID_MODE` | `COMPOSE_PROFILES` | `SQUAD_APPARMOR` |
|---|---|---|---|
| No Squad server yet — run one here | `service:squad` | `bundled-squad` | `docker-default` |
| A Squad container you already run | `container:<name>` | *(empty)* | `docker-default` |
| Squad native on the host (LinuxGSM) | `host` | *(empty)* | `unconfined` |

Host mode needs `apparmor=unconfined` because Docker's `docker-default` profile
permits ptrace only toward peers under the same profile; an unconfined host
process is refused at `/proc/<pid>/maps`, before the reader reaches any memory.

### Why the reader is privileged

`cap_drop: [ALL]` plus exactly two capabilities:

- `SYS_PTRACE` — `/proc/<pid>/maps` is mode 0444 but gated by the ptrace check;
- `DAC_READ_SEARCH` — `/proc/<pid>/mem` is mode 0600 and owned by the game's
  user, so the DAC check applies on top.

Both are needed. Docker's default capability set appears to work with only
`SYS_PTRACE`, but only because it still carries `DAC_OVERRIDE`. The reader is
still read-only: it never opens the game's memory for writing.

Installing the reader *into* the Squad image does not avoid this. It would be a
sibling of the game process rather than an ancestor, and `ptrace_scope=1` grants
attach to ancestors only — the same capability, plus a forked image and two
lifecycles behind one PID 1.

### What is mounted

| Mount | Why |
|---|---|
| `${SQUAD_DATA}:/squad:ro` | the kill-feed log, and nothing else |
| `sqreader-data:/data` | recordings and `stats/player_stats.db` |

In mode 1 the game writes to `${SQUAD_DATA}` too, and the upstream image runs as
an unprivileged user, so create it writable first:

```bash
mkdir -p squad-data && chmod 777 squad-data
```

The replay UI is published to `127.0.0.1:8080` by default. Put it behind the
nginx config in [`deploy/`](deploy/) rather than setting `SQREADER_BIND=0.0.0.0`
on a public box.

### Recording retention

Recordings grow by hundreds of MB a day and nothing else deletes them; on a box
that also runs the game, a full disk takes Squad down too. The container runs a
logrotate-style pruner beside the reader, with the same policy as the systemd
timer in `deploy/`:

| Variable | Default | |
|---|---|---|
| `RETENTION_INTERVAL` | `86400` | seconds between passes; `0` disables the pruner |
| `RETENTION_MAX_AGE_DAYS` | `90` | `0` disables this policy |
| `RETENTION_MAX_TOTAL_GB` | `150` | `0` disables this policy |
| `RETENTION_MIN_FREE_GB` | `50` | `0` disables this policy |
| `RETENTION_MIN_KEEP` | `3` | newest N are never pruned |

The match being recorded right now is never touched.

### Troubleshooting

**`no .env found — run: cp .env.example .env`** — exactly that.

**`service "sqreader" depends on undefined service "squad"`** — `SQUAD_PID_MODE`
is `service:squad` but `COMPOSE_PROFILES` does not contain `bundled-squad`.
Uncomment a whole mode block, not one line of it.

**`PermissionError` on `/proc/<pid>/mem`** — in host mode, set
`SQUAD_APPARMOR=unconfined`. Otherwise check that `cap_add` still lists both
`SYS_PTRACE` and `DAC_READ_SEARCH`.

**`WARNING: no Squad log found — the kill feed will be INCOMPLETE`** — the
`/squad` mount is wrong. `SQUAD_DATA` must be the install root, the directory
that *contains* `SquadGame/`. This degrades quietly: the reader keeps running
and the stats look plausible while undercounting kills.

**`[degraded] cannot read the game`** on first boot — normal. The reader
re-resolves the game on every retry, so it simply waits out SteamCMD's initial
download.

### Plugins and optional config

Plugins (anti-cheat) and the alert webhook are driven by a
`sqreader.config.json`. Create one from `sqreader.config.example.json` and
uncomment the matching volume line in `docker-compose.yml` — the reader's
working directory is `/app`, so a file mounted at `/app/sqreader.config.json`
is found without setting anything else.

### Not included

The image is built from source, so remote self-update is inert — upgrade by
pulling the repo and running `docker compose build`. Central push stays off;
it needs `sqreader enroll` and the `push` extra (`pip install .[push]`).
````

- [ ] **Step 2: Verify the documented commands match what was built**

Run:

```bash
# Every variable the compose file reads must be discoverable in the template,
# commented or not. COMPOSE_PROFILES is exempt: Compose consumes it directly,
# so it never appears as a ${...} interpolation.
for v in $(grep -oP '\$\{\K[A-Z_]+' docker-compose.yml | sort -u); do
  grep -qF "$v" .env.example || echo "MISSING FROM .env.example: $v"
done
# -F, not plain grep: GNU grep's BRE will not match the literal string
# "${NAME}", so the unquoted form silently reports every variable as unused.
for v in $(grep -oP '^[A-Z_]+(?==)' .env.example | sort -u); do
  [ "$v" = COMPOSE_PROFILES ] && continue
  grep -qF "\${$v" docker-compose.yml || echo "UNUSED IN docker-compose.yml: $v"
done
echo "consistency check done"
```

Expected: no `MISSING`/`UNUSED` lines — just `consistency check done`. Each one
is a real documentation bug: a `MISSING` variable silently takes its compose
default forever, an `UNUSED` one is a knob the operator turns with no effect.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document running the reader in Docker"
```

---

## Deviations from the spec

Two, both additive, both flagged for review:

1. **`SQREADER_DATA_DIR`** (Task 1) is a container-internal seam so the
   entrypoint can be run by a test harness that cannot write `/data`. Already
   folded back into the spec's env table.
2. **The compose-mode tests live in `tests/test_docker_entrypoint.py`** rather
   than a second file, matching the spec's file list literally. They are
   separated by a section comment in the repo's existing style.
