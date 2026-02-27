"""Typer CLI: run, install, uninstall, status, config."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

from openheart import settings
from openheart.runner import LOG_DIR, run_heartbeat

app = typer.Typer(help="OpenHeart — Claude Code heartbeat scheduler.")

CRON_MARKER = "# openheart"


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
    budget: float = typer.Option(0.50, help="Max USD per run."),
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
    budget: float = typer.Option(0.50, help="Max USD per run."),
) -> None:
    """Install heartbeat as a cron job. Saves settings to ~/.openheart/settings.json."""
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
    """Remove heartbeat cron job."""
    existing = _get_crontab()
    lines = [line for line in existing.splitlines() if CRON_MARKER not in line]

    if len(lines) == len(existing.splitlines()):
        typer.echo("No openheart cron job found.")
        return

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

    # Check cron
    typer.echo("")
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


if __name__ == "__main__":
    app()
