"""Core logic: build claude command, execute, log."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


LOG_DIR = Path.home() / ".openheart" / "logs"
COST_LEDGER = LOG_DIR / "costs.jsonl"


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
    budget: float | None,
    allowed_tools: str | None,
) -> list[str]:
    """Build the claude CLI command.

    A budget <= 0 (or None) means no hard cap — we omit --max-budget-usd and
    rely on cost logging for visibility plus the run timeout as a backstop.
    Output is requested as JSON so we can capture the per-run cost.
    """
    timestamp = datetime.now().astimezone().isoformat()
    user_prompt = f"Run heartbeat. Current time: {timestamp}"

    cmd = [
        "claude",
        "-p", user_prompt,
        "--model", model,
        "--append-system-prompt", heartbeat_content,
        "--dangerously-skip-permissions",
        "--output-format", "json",
    ]

    if budget and budget > 0:
        cmd.extend(["--max-budget-usd", str(budget)])

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

    text, cost, meta = _parse_result(result.stdout)
    if result.stderr:
        text = (text + "\n" + result.stderr).strip() if text else result.stderr.strip()

    # Print to stdout
    print(text)
    if cost is not None:
        duration_s = (meta.get("duration_ms") or 0) / 1000
        print(
            f"[openheart] cost=${cost:.4f} duration={duration_s:.1f}s "
            f"turns={meta.get('num_turns', '?')}"
        )

    # Log to file + cost ledger
    _write_log(text, result.returncode, cost)
    _record_cost(cost, result.returncode, model, meta)

    return result.returncode


def _parse_result(stdout: str) -> tuple[str, float | None, dict]:
    """Parse `claude --output-format json` stdout.

    Returns (text, cost_usd, meta). Falls back to (raw, None, {}) when the
    output isn't the expected JSON (e.g. an early CLI error, or older claude).
    """
    stdout = (stdout or "").strip()
    if not stdout:
        return "", None, {}
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        return stdout, None, {}
    if not isinstance(data, dict):
        return stdout, None, {}
    text = data.get("result") or ""
    cost = data.get("total_cost_usd")
    meta = {k: data.get(k) for k in ("duration_ms", "num_turns", "is_error", "subtype")}
    return text, cost, meta


def _record_cost(cost: float | None, returncode: int, model: str, meta: dict) -> None:
    """Append one run's cost to the JSONL ledger (~/.openheart/logs/costs.jsonl)."""
    if cost is None:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now().astimezone().isoformat(),
        "cost_usd": round(cost, 6),
        "model": model,
        "exit": returncode,
        "duration_ms": meta.get("duration_ms"),
        "num_turns": meta.get("num_turns"),
        "is_error": meta.get("is_error"),
    }
    with open(COST_LEDGER, "a") as f:
        f.write(json.dumps(record) + "\n")


def cost_summary() -> dict:
    """Aggregate the cost ledger: today's and all-time totals + run counts."""
    summary = {"today_usd": 0.0, "today_runs": 0, "total_usd": 0.0, "total_runs": 0}
    if not COST_LEDGER.exists():
        return summary
    today = datetime.now().strftime("%Y-%m-%d")
    for line in COST_LEDGER.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        cost = rec.get("cost_usd") or 0.0
        summary["total_usd"] += cost
        summary["total_runs"] += 1
        if str(rec.get("ts", "")).startswith(today):
            summary["today_usd"] += cost
            summary["today_runs"] += 1
    return summary


def _write_log(output: str, returncode: int, cost: float | None = None) -> None:
    """Append timestamped entry to daily log file."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"{today}.log"

    timestamp = datetime.now().astimezone().isoformat()
    separator = "=" * 60
    cost_str = f" cost=${cost:.4f}" if cost is not None else ""

    entry = f"\n{separator}\n[{timestamp}] exit={returncode}{cost_str}\n{separator}\n{output}\n"

    with open(log_file, "a") as f:
        f.write(entry)
