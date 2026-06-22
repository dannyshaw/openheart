"""Typer CLI: run, install, uninstall, status, config."""

from __future__ import annotations

import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

from openheart import settings
from openheart.runner import LOG_DIR, cost_summary, run_heartbeat

app = typer.Typer(help="OpenHeart — Claude Code heartbeat scheduler.")

CRON_MARKER = "# openheart"

# macOS LaunchAgent. Used instead of cron on macOS because cron jobs run in a
# security session that can't reach the unlocked login keychain — where Claude
# Code stores its auth token — so a cron-spawned `claude` fails with
# "Not logged in". A LaunchAgent loaded into the GUI (Aqua) domain runs in the
# user's session and has keychain access.
LAUNCHD_LABEL = "com.openheart.heartbeat"


def _get(ctx: typer.Context, name: str, cli_value):
    """Return CLI value if explicitly passed, else fall back to settings."""
    # Typer doesn't expose "was this flag passed?" directly, so we check
    # whether the param's source was the default.
    param = ctx.command.params if hasattr(ctx, "command") else []
    src = ctx.get_parameter_source(name)
    explicitly_set = src is not None and src.name != "DEFAULT"
    if explicitly_set:
        return cli_value
    return settings.load().get(name, cli_value)


@app.command()
def run(
    ctx: typer.Context,
    heartbeat: Path = typer.Option(Path("HEARTBEAT.md"), help="Path to heartbeat file."),
    model: str = typer.Option("sonnet", help="Claude model."),
    budget: float = typer.Option(0.50, help="Max USD per run (0 = no cap; cost is logged either way)."),
    dir: Path = typer.Option(Path("."), help="Project directory to run in."),
    allowed_tools: Optional[str] = typer.Option(None, help="Comma-separated tool whitelist."),
    quiet_start: int = typer.Option(23, help="Quiet hours start (24h)."),
    quiet_end: int = typer.Option(8, help="Quiet hours end (24h)."),
    force: bool = typer.Option(False, help="Run even during quiet hours."),
) -> None:
    """Run a single heartbeat."""
    s = settings.load()
    heartbeat = Path(_get(ctx, "heartbeat", heartbeat))
    model = _get(ctx, "model", model)
    budget = float(_get(ctx, "budget", budget))
    dir = Path(_get(ctx, "dir", dir))
    allowed_tools = _get(ctx, "allowed_tools", allowed_tools)
    quiet_start = int(_get(ctx, "quiet_start", quiet_start))
    quiet_end = int(_get(ctx, "quiet_end", quiet_end))

    project_dir = dir.resolve()
    code = run_heartbeat(
        heartbeat_path=heartbeat,
        model=model,
        budget=budget,
        project_dir=project_dir,
        allowed_tools=allowed_tools,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
        force=force,
    )
    raise SystemExit(code)


@app.command()
def install(
    ctx: typer.Context,
    interval: int = typer.Option(30, help="Minutes between runs."),
    quiet_start: int = typer.Option(23, help="Quiet hours start (24h)."),
    quiet_end: int = typer.Option(8, help="Quiet hours end (24h)."),
    dir: Path = typer.Option(Path("."), help="Project directory."),
    heartbeat: Path = typer.Option(Path("HEARTBEAT.md"), help="Path to heartbeat file."),
    model: str = typer.Option("sonnet", help="Claude model."),
    budget: float = typer.Option(0.50, help="Max USD per run (0 = no cap; cost is logged either way)."),
    method: str = typer.Option(
        "auto",
        help="Scheduler: 'auto' (LaunchAgent on macOS, cron elsewhere), 'launchd', or 'cron'.",
    ),
) -> None:
    """Install the heartbeat scheduler. Saves settings to ~/.openheart/settings.json.

    On macOS this installs a per-user LaunchAgent (so the scheduled `claude` can
    reach the login keychain); on other platforms it installs a cron job.
    """
    interval = int(_get(ctx, "interval", interval))
    quiet_start = int(_get(ctx, "quiet_start", quiet_start))
    quiet_end = int(_get(ctx, "quiet_end", quiet_end))
    dir = Path(_get(ctx, "dir", dir))
    heartbeat = Path(_get(ctx, "heartbeat", heartbeat))
    model = _get(ctx, "model", model)
    budget = float(_get(ctx, "budget", budget))

    project_dir = dir.resolve()
    heartbeat_resolved = heartbeat if heartbeat.is_absolute() else project_dir / heartbeat

    if not heartbeat_resolved.exists():
        typer.echo(f"Error: Heartbeat file not found: {heartbeat_resolved}", err=True)
        raise SystemExit(1)

    # Save resolved settings
    settings.save({
        "heartbeat": str(heartbeat_resolved),
        "model": model,
        "budget": budget,
        "dir": str(project_dir),
        "quiet_start": quiet_start,
        "quiet_end": quiet_end,
        "interval": interval,
    })

    if _should_use_launchd(method):
        _install_launchd(
            project_dir, interval, quiet_start, quiet_end, model, budget
        )
        return

    # Find openheart binary
    openheart_bin = _find_binary()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cron_log = LOG_DIR / "cron.log"

    # Build cron command — just `openheart run`, settings file has the rest
    run_cmd = f"{openheart_bin} run"

    cron_schedule = f"*/{interval} {quiet_end}-{quiet_start - 1 if quiet_start > 0 else 23} * * *"
    cron_line = f"{cron_schedule} {run_cmd} >> {cron_log} 2>&1 {CRON_MARKER}"

    # Get existing crontab
    existing = _get_crontab()

    # Remove any existing openheart entries
    lines = [line for line in existing.splitlines() if CRON_MARKER not in line]
    lines.append(cron_line)

    new_crontab = "\n".join(lines) + "\n"

    # Install
    proc = subprocess.run(
        ["crontab", "-"],
        input=new_crontab,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        typer.echo(f"Error installing crontab: {proc.stderr}", err=True)
        raise SystemExit(1)

    typer.echo("Installed cron job:")
    typer.echo(f"  Schedule: every {interval} min, {quiet_end}:00-{quiet_start}:00")
    typer.echo(f"  Project:  {project_dir}")
    typer.echo(f"  Model:    {model}")
    typer.echo(f"  Budget:   ${budget}/run")
    typer.echo(f"  Log:      {cron_log}")
    typer.echo(f"  Settings: {settings.SETTINGS_FILE}")


@app.command()
def uninstall() -> None:
    """Remove the heartbeat scheduler (LaunchAgent on macOS and/or cron)."""
    removed_any = False

    # LaunchAgent (macOS)
    if _is_macos() and _launchd_installed():
        if _uninstall_launchd():
            typer.echo("Removed LaunchAgent.")
            removed_any = True

    # Cron
    existing = _get_crontab()
    lines = [line for line in existing.splitlines() if CRON_MARKER not in line]
    if len(lines) != len(existing.splitlines()):
        new_crontab = "\n".join(lines) + "\n" if lines else ""
        proc = subprocess.run(
            ["crontab", "-"],
            input=new_crontab,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            typer.echo(f"Error updating crontab: {proc.stderr}", err=True)
            raise SystemExit(1)
        typer.echo("Removed openheart cron job.")
        removed_any = True

    if not removed_any:
        typer.echo("No openheart scheduler found.")


@app.command()
def status() -> None:
    """Show heartbeat status and current settings."""
    # Show settings
    s = settings.load()
    typer.echo(f"Settings: {settings.SETTINGS_FILE}")
    if settings.SETTINGS_FILE.exists():
        for k, v in s.items():
            default = settings.DEFAULTS.get(k)
            marker = "" if v != default else " (default)"
            typer.echo(f"  {k}: {v}{marker}")
    else:
        typer.echo("  (no settings file — using defaults)")

    # Cost (from the ledger)
    costs = cost_summary()
    typer.echo("")
    typer.echo(
        f"Cost: ${costs['today_usd']:.2f} today ({costs['today_runs']} runs), "
        f"${costs['total_usd']:.2f} all-time ({costs['total_runs']} runs)"
    )

    # Check scheduler
    typer.echo("")

    if _is_macos():
        if _launchd_installed():
            typer.echo("LaunchAgent: INSTALLED")
            typer.echo(f"  Plist: {_launchd_plist_path()}")
            pr = subprocess.run(
                ["launchctl", "print", f"{_launchd_domain()}/{LAUNCHD_LABEL}"],
                capture_output=True,
                text=True,
            )
            if pr.returncode == 0:
                for line in pr.stdout.splitlines():
                    s = line.strip()
                    if s.startswith("state =") or s.startswith("last exit code"):
                        typer.echo(f"  {s}")
        else:
            typer.echo("LaunchAgent: NOT INSTALLED")

    existing = _get_crontab()
    cron_lines = [line for line in existing.splitlines() if CRON_MARKER in line]

    if cron_lines:
        typer.echo("Cron job: INSTALLED")
        for line in cron_lines:
            typer.echo(f"  {line}")
    else:
        typer.echo("Cron job: NOT INSTALLED")

    # Check logs
    typer.echo(f"\nLog dir: {LOG_DIR}")

    if not LOG_DIR.exists():
        typer.echo("No logs yet.")
        return

    log_files = sorted(LOG_DIR.glob("*.log"), reverse=True)
    if not log_files:
        typer.echo("No logs yet.")
        return

    daily_logs = [f for f in log_files if f.name != "cron.log"]
    if daily_logs:
        latest = daily_logs[0]
        typer.echo(f"Latest log: {latest}")

        content = latest.read_text()
        entries = content.split("=" * 60)
        if len(entries) >= 2:
            last_entry = entries[-1].strip()
            snippet_lines = last_entry.splitlines()[:5]
            typer.echo("Last output (snippet):")
            for line in snippet_lines:
                typer.echo(f"  {line}")
    else:
        typer.echo("No daily logs yet.")

    cron_log = LOG_DIR / "cron.log"
    if cron_log.exists():
        size = cron_log.stat().st_size
        typer.echo(f"\nCron log: {cron_log} ({size} bytes)")


@app.command("config")
def config_cmd(
    key: Optional[str] = typer.Argument(None, help="Setting key to get/set."),
    value: Optional[str] = typer.Argument(None, help="Value to set."),
) -> None:
    """View or update settings. Run bare to see all, with key to get, with key+value to set."""
    s = settings.load()

    if key is None:
        # Show all
        typer.echo(f"{settings.SETTINGS_FILE}")
        for k, v in s.items():
            typer.echo(f"  {k}: {v}")
        return

    if value is None:
        # Get one
        if key in s:
            typer.echo(s[key])
        else:
            typer.echo(f"Unknown key: {key}", err=True)
            typer.echo(f"Valid keys: {', '.join(settings.DEFAULTS.keys())}", err=True)
            raise SystemExit(1)
        return

    # Set one
    if key not in settings.DEFAULTS:
        typer.echo(f"Unknown key: {key}", err=True)
        typer.echo(f"Valid keys: {', '.join(settings.DEFAULTS.keys())}", err=True)
        raise SystemExit(1)

    # Coerce type to match default
    default = settings.DEFAULTS[key]
    if isinstance(default, int):
        s[key] = int(value)
    elif isinstance(default, float):
        s[key] = float(value)
    else:
        s[key] = value

    settings.save(s)
    typer.echo(f"{key} = {s[key]}")


def _get_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout


def _find_binary() -> str:
    result = subprocess.run(["which", "openheart"], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return f"{sys.executable} -m openheart.cli"


# --- macOS LaunchAgent support ---------------------------------------------


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _should_use_launchd(method: str) -> bool:
    """Resolve the 'auto'/'launchd'/'cron' choice to a boolean."""
    method = (method or "auto").lower()
    if method == "launchd":
        return True
    if method == "cron":
        return False
    return _is_macos()


def _find_binary_argv() -> list[str]:
    """openheart invocation as an argv list (for ProgramArguments)."""
    result = subprocess.run(["which", "openheart"], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return [result.stdout.strip()]
    return [sys.executable, "-m", "openheart.cli"]


def _path_env(openheart_bin: str) -> str:
    """PATH for the LaunchAgent. Must include the dir holding `claude`, since
    launchd starts with a minimal PATH and the runner calls `claude` by bare
    name. We locate `claude` directly (not assuming it sits next to openheart),
    and always include ~/.local/bin, which is where Claude Code installs."""
    entries = [str(Path(openheart_bin).parent)]
    claude = shutil.which("claude")
    if claude:
        entries.append(str(Path(claude).parent))
    entries.append(str(Path.home() / ".local" / "bin"))
    entries += [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    seen: set[str] = set()
    deduped = []
    for e in entries:
        if e and e not in seen:
            seen.add(e)
            deduped.append(e)
    return ":".join(deduped)


def _active_hours(quiet_start: int, quiet_end: int) -> list[int]:
    """Hours (0-23) outside quiet hours, using the same wrap logic as the runner."""
    hours = []
    for h in range(24):
        if quiet_start > quiet_end:
            quiet = h >= quiet_start or h < quiet_end
        else:
            quiet = quiet_start <= h < quiet_end
        if not quiet:
            hours.append(h)
    return hours


def _schedule(interval: int, quiet_start: int, quiet_end: int):
    """Return the launchd schedule.

    ('calendar', [{Hour, Minute}, ...]) when the interval divides an hour evenly
    (5/10/15/20/30/60) — so we can fire only within the active window. Otherwise
    ('interval', seconds) for a 24/7 StartInterval, relying on the runner's own
    quiet-hours check to no-op overnight.
    """
    if interval <= 60 and 60 % interval == 0:
        hours = _active_hours(quiet_start, quiet_end)
        minutes = list(range(0, 60, interval))
        return ("calendar", [{"Hour": h, "Minute": m} for h in hours for m in minutes])
    return ("interval", interval * 60)


def _build_plist(
    argv: list[str], project_dir: Path, schedule, log_path: Path, path_env: str
) -> dict:
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": argv,
        "RunAtLoad": False,
        "WorkingDirectory": str(project_dir),
        "EnvironmentVariables": {"PATH": path_env, "HOME": str(Path.home())},
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "ProcessType": "Background",
    }
    kind, value = schedule
    if kind == "calendar":
        plist["StartCalendarInterval"] = value
    else:
        plist["StartInterval"] = value
    return plist


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def _launchd_installed() -> bool:
    return _launchd_plist_path().exists()


def _install_launchd(
    project_dir: Path,
    interval: int,
    quiet_start: int,
    quiet_end: int,
    model: str,
    budget: float,
) -> None:
    argv = _find_binary_argv() + ["run"]
    plist_path = _launchd_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "launchd.log"

    schedule = _schedule(interval, quiet_start, quiet_end)
    plist = _build_plist(argv, project_dir, schedule, log_path, _path_env(argv[0]))
    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)

    domain = _launchd_domain()
    target = f"{domain}/{LAUNCHD_LABEL}"

    # Reload: bootout any existing instance (ignore failure if not loaded), then bootstrap.
    subprocess.run(["launchctl", "bootout", target], capture_output=True, text=True)
    proc = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        typer.echo(
            f"Error loading LaunchAgent: {proc.stderr.strip() or proc.stdout.strip()}",
            err=True,
        )
        raise SystemExit(1)
    subprocess.run(["launchctl", "enable", target], capture_output=True, text=True)

    if schedule[0] == "calendar":
        sched_desc = f"every {interval} min, active {quiet_end:02d}:00-{quiet_start:02d}:00"
    else:
        sched_desc = f"every {interval} min (24/7; quiet hours enforced in runner)"

    typer.echo("Installed LaunchAgent:")
    typer.echo(f"  Label:    {LAUNCHD_LABEL}")
    typer.echo(f"  Schedule: {sched_desc}")
    typer.echo(f"  Project:  {project_dir}")
    typer.echo(f"  Model:    {model}")
    typer.echo(f"  Budget:   ${budget}/run")
    typer.echo(f"  Plist:    {plist_path}")
    typer.echo(f"  Log:      {log_path}")
    typer.echo(f"  Settings: {settings.SETTINGS_FILE}")


def _uninstall_launchd() -> bool:
    """Bootout and remove the LaunchAgent plist. Returns True if anything was removed."""
    plist_path = _launchd_plist_path()
    removed = False
    boot = subprocess.run(
        ["launchctl", "bootout", f"{_launchd_domain()}/{LAUNCHD_LABEL}"],
        capture_output=True,
        text=True,
    )
    if boot.returncode == 0:
        removed = True
    if plist_path.exists():
        plist_path.unlink()
        removed = True
    return removed


if __name__ == "__main__":
    app()
