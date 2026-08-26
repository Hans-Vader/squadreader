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
