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
