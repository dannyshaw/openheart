# OpenHeart

A tiny, standalone tool that gives [Claude Code](https://docs.anthropic.com/en/docs/claude-code) a heartbeat. It runs `claude -p` on a cron schedule with your `HEARTBEAT.md` as the prompt — no opinions on what the heartbeat *does*. That's up to you.

Use it to check emails, review calendars, maintain memory files, monitor systems, triage inboxes, or anything else you'd want an agent doing in the background.

## How it works

1. You write a `HEARTBEAT.md` — this is the prompt Claude receives each run
2. OpenHeart calls `claude -p` with your heartbeat as a system prompt, running in your project directory so Claude picks up your `.mcp.json`, `CLAUDE.md`, and other project config
3. Output is logged to `~/.openheart/logs/` and printed to stdout
4. Cron handles the scheduling. No daemon, no background process — if the machine is off, it just doesn't run

## Requirements

- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated

## Install

```bash
# With uv (recommended)
uv tool install openheart

# From source
uv tool install -e /path/to/openheart

# Or with pip
pip install openheart
```

## Quick start

```bash
# 1. Create a HEARTBEAT.md in your project (see HEARTBEAT.md.example)
cp HEARTBEAT.md.example /path/to/your/project/HEARTBEAT.md

# 2. Test it
cd /path/to/your/project
openheart run --force

# 3. Install the cron job
openheart install --heartbeat HEARTBEAT.md --interval 30

# 4. Verify
openheart status
```

## CLI

### `openheart run [OPTIONS]`

Run a single heartbeat.

| Flag | Default | Description |
|------|---------|-------------|
| `--heartbeat` | `HEARTBEAT.md` | Path to heartbeat file |
| `--model` | `sonnet` | Claude model to use |
| `--budget` | `0.50` | Max USD spend per run |
| `--dir` | `.` | Project directory (Claude runs here) |
| `--allowed-tools` | — | Comma-separated tool whitelist |
| `--quiet-start` | `23` | Quiet hours start (24h) |
| `--quiet-end` | `8` | Quiet hours end (24h) |
| `--force` | `false` | Run even during quiet hours |

### `openheart install [OPTIONS]`

Install a cron job and save settings to `~/.openheart/settings.json`. Accepts all `run` flags plus:

| Flag | Default | Description |
|------|---------|-------------|
| `--interval` | `30` | Minutes between runs |

### `openheart uninstall`

Remove the cron job.

### `openheart status`

Show current settings, cron schedule, last run info, and log paths.

### `openheart config [KEY] [VALUE]`

View or update saved settings.

```bash
openheart config              # show all settings
openheart config model        # get a single setting
openheart config model haiku  # set a single setting
```

## Settings

All configuration lives in `~/.openheart/settings.json`. CLI flags override settings; `openheart install` saves them.

| Key | Default | Description |
|-----|---------|-------------|
| `heartbeat` | `HEARTBEAT.md` | Path to heartbeat file |
| `model` | `sonnet` | Claude model |
| `budget` | `0.50` | Max USD per run |
| `dir` | `.` | Project directory |
| `allowed_tools` | `null` | Comma-separated tool whitelist |
| `quiet_start` | `23` | Quiet hours start (24h) |
| `quiet_end` | `8` | Quiet hours end (24h) |
| `interval` | `30` | Minutes between cron runs |

Only non-default values are persisted, so the file stays minimal:

```json
{
  "heartbeat": "/home/you/project/HEARTBEAT.md",
  "dir": "/home/you/project"
}
```

## Quiet hours

Quiet hours are enforced in the runner, not just cron — so `openheart run` during quiet hours silently exits unless you pass `--force`. Cron is also limited to the active window (e.g. `8-22` for defaults) as a belt-and-suspenders measure.

## Logs

All logs go to `~/.openheart/logs/`, keeping your project repo clean:

- `YYYY-MM-DD.log` — timestamped entries from each run
- `cron.log` — stdout/stderr from cron (for debugging cron issues)

## Writing a HEARTBEAT.md

Your heartbeat file is just a markdown prompt. Claude receives it as a system prompt along with a user message: `"Run heartbeat. Current time: {iso_timestamp}"`.

Since Claude runs in your project directory, it has access to everything it normally would in a Claude Code session — your `CLAUDE.md`, `.mcp.json`, MCP servers, and any files in the project.

Some ideas for what to put in it:

- Check email for urgent messages
- Review calendar for upcoming events
- Scan WhatsApp/Slack for unread messages
- Maintain memory or context files
- Monitor budgets or financial data
- Triage an inbox of action items
- Run health checks on services

See [`HEARTBEAT.md.example`](HEARTBEAT.md.example) for a starter template.

## Security

OpenHeart runs Claude with `--dangerously-skip-permissions` since it's unattended. To constrain what Claude can do, use `--allowed-tools` to whitelist specific tools:

```bash
openheart install --allowed-tools "Read,Grep,Glob,WebSearch"
```

## Design decisions

- **Settings file + CLI overrides** — `~/.openheart/settings.json` stores defaults, CLI flags override per-invocation
- **No daemon** — just cron. Dead simple. No PID files, no process management
- **Quiet hours in the runner** — not just in the cron schedule, so manual runs respect them too
- **Logs outside the project** — your repo stays clean
- **One cron entry** — `openheart run` reads settings from the file, so the crontab line is minimal

## License

MIT
