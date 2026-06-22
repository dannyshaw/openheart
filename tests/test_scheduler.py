"""Tests for the LaunchAgent scheduling logic (pure functions in cli.py).

Runnable with pytest, or standalone: `python tests/test_scheduler.py`.
"""

from __future__ import annotations

from openheart.cli import (
    LAUNCHD_LABEL,
    _active_hours,
    _build_plist,
    _path_env,
    _schedule,
    _should_use_launchd,
)


def test_active_hours_night_quiet_wraps_midnight():
    # Quiet 22:00-08:00 -> active 08..21
    assert _active_hours(22, 8) == list(range(8, 22))


def test_active_hours_default_quiet():
    # Defaults quiet_start=23, quiet_end=8 -> active 08..22
    assert _active_hours(23, 8) == list(range(8, 23))


def test_active_hours_matches_runner_quiet_logic():
    # The window must be exactly the complement of the runner's quiet check,
    # otherwise launchd would fire when the runner would no-op (or vice versa).
    for qs, qe in [(22, 8), (23, 8), (8, 22), (0, 0)]:
        active = set(_active_hours(qs, qe))
        for h in range(24):
            quiet = is_quiet_hours_at(qs, qe, h)
            assert (h in active) == (not quiet), (qs, qe, h)


def is_quiet_hours_at(quiet_start, quiet_end, hour):
    if quiet_start > quiet_end:
        return hour >= quiet_start or hour < quiet_end
    return quiet_start <= hour < quiet_end


def test_schedule_calendar_for_dividing_interval():
    kind, entries = _schedule(15, 22, 8)
    assert kind == "calendar"
    # 14 active hours (8..21) x 4 minutes = 56 entries
    assert len(entries) == 14 * 4
    assert {"Hour": 8, "Minute": 0} in entries
    assert {"Hour": 21, "Minute": 45} in entries
    # nothing in quiet hours
    assert all(8 <= e["Hour"] <= 21 for e in entries)


def test_schedule_calendar_hourly():
    kind, entries = _schedule(60, 22, 8)
    assert kind == "calendar"
    assert all(e["Minute"] == 0 for e in entries)
    assert len(entries) == 14


def test_schedule_falls_back_to_interval_for_non_dividing():
    # 45 does not divide 60 -> 24/7 StartInterval in seconds
    assert _schedule(45, 22, 8) == ("interval", 45 * 60)


def test_schedule_falls_back_to_interval_for_over_an_hour():
    assert _schedule(120, 22, 8) == ("interval", 120 * 60)


def test_should_use_launchd_explicit_overrides_platform():
    assert _should_use_launchd("launchd") is True
    assert _should_use_launchd("cron") is False


def test_path_env_includes_binary_dir_first():
    path = _path_env("/Users/x/.local/bin/openheart")
    parts = path.split(":")
    assert parts[0] == "/Users/x/.local/bin"
    assert "/opt/homebrew/bin" in parts
    assert "/usr/bin" in parts


def test_path_env_always_includes_local_bin():
    # Even if openheart runs from somewhere odd (e.g. a uv ephemeral venv),
    # ~/.local/bin (where Claude Code installs) must be on PATH.
    from pathlib import Path

    path = _path_env("/tmp/some/.venv/bin/openheart")
    parts = path.split(":")
    assert str(Path.home() / ".local" / "bin") in parts


def test_build_plist_calendar():
    schedule = _schedule(15, 22, 8)
    plist = _build_plist(
        ["/bin/openheart", "run"],
        __import__("pathlib").Path("/proj"),
        schedule,
        __import__("pathlib").Path("/log/launchd.log"),
        "/bin:/usr/bin",
    )
    assert plist["Label"] == LAUNCHD_LABEL
    assert plist["ProgramArguments"] == ["/bin/openheart", "run"]
    assert plist["RunAtLoad"] is False
    assert "StartCalendarInterval" in plist
    assert "StartInterval" not in plist
    assert plist["EnvironmentVariables"]["PATH"] == "/bin:/usr/bin"


def test_build_plist_interval():
    plist = _build_plist(
        ["/bin/openheart", "run"],
        __import__("pathlib").Path("/proj"),
        ("interval", 2700),
        __import__("pathlib").Path("/log/launchd.log"),
        "/bin",
    )
    assert plist["StartInterval"] == 2700
    assert "StartCalendarInterval" not in plist


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {e!r}")
    raise SystemExit(1 if failures else 0)
