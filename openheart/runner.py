"""Core logic: build claude command, execute, log."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path


LOG_DIR = Path.home() / ".openheart" / "logs"


def is_quiet_hours(quiet_start: int, quiet_end: int) -> bool:
    """Check if current time falls within quiet hours."""
    hour = datetime.now().hour
    if quiet_start > quiet_end:
        # Wraps midnight, e.g. 23-8
        return hour >= quiet_start or hour < quiet_end
    else:
        return quiet_start <= hour < quiet_end


def build_command(
    heartbeat_content: str,
    model: str,
    budget: float,
    allowed_tools: str | None,
) -> list[str]:
    """Build the claude CLI command."""
    timestamp = datetime.now().astimezone().isoformat()
    user_prompt = f"Run heartbeat. Current time: {timestamp}"

    cmd = [
        "claude",
        "-p", user_prompt,
        "--model", model,
        "--append-system-prompt", heartbeat_content,
        "--dangerously-skip-permissions",
        "--max-budget-usd", str(budget),
    ]

    if allowed_tools:
        for tool in allowed_tools.split(","):
            tool = tool.strip()
            if tool:
                cmd.extend(["--allowedTools", tool])

    return cmd


def run_heartbeat(
    heartbeat_path: Path,
    model: str,
    budget: float,
    project_dir: Path,
    allowed_tools: str | None,
    quiet_start: int,
    quiet_end: int,
    force: bool,
) -> int:
    """Execute a heartbeat run. Returns exit code."""
    # Quiet hours check
    if not force and is_quiet_hours(quiet_start, quiet_end):
        msg = f"Quiet hours ({quiet_start:02d}:00-{quiet_end:02d}:00). Use --force to override."
        print(msg)
        return 0

    # Read heartbeat file
    heartbeat_file = heartbeat_path if heartbeat_path.is_absolute() else project_dir / heartbeat_path
    if not heartbeat_file.exists():
        print(f"Error: Heartbeat file not found: {heartbeat_file}", file=sys.stderr)
        return 1

    heartbeat_content = heartbeat_file.read_text()

    # Build and run command
    cmd = build_command(heartbeat_content, model, budget, allowed_tools)

    timestamp = datetime.now().astimezone().isoformat()
    print(f"[openheart] {timestamp} — running heartbeat from {heartbeat_file}")
    print(f"[openheart] model={model} budget=${budget} dir={project_dir}")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
        )
    except FileNotFoundError:
        print("Error: 'claude' CLI not found. Is it installed and on PATH?", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("Error: Heartbeat timed out after 10 minutes.", file=sys.stderr)
        return 1

    output = result.stdout
    if result.stderr:
        output += "\n" + result.stderr

    # Print to stdout
    print(output)

    # Log to file
    _write_log(output, result.returncode)

    return result.returncode


def _write_log(output: str, returncode: int) -> None:
    """Append timestamped entry to daily log file."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"{today}.log"

    timestamp = datetime.now().astimezone().isoformat()
    separator = "=" * 60

    entry = f"\n{separator}\n[{timestamp}] exit={returncode}\n{separator}\n{output}\n"

    with open(log_file, "a") as f:
        f.write(entry)
