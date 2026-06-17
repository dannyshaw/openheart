"""Tests for command building and cost parsing.

Runnable with pytest, or standalone: `python tests/test_runner.py`.
"""

from __future__ import annotations

import json

from openheart.runner import _parse_result, build_command


def test_budget_zero_omits_cap():
    cmd = build_command("HB", "haiku", 0, None)
    assert "--max-budget-usd" not in cmd


def test_budget_none_omits_cap():
    cmd = build_command("HB", "haiku", None, None)
    assert "--max-budget-usd" not in cmd


def test_positive_budget_sets_cap():
    cmd = build_command("HB", "haiku", 0.5, None)
    assert "--max-budget-usd" in cmd
    assert cmd[cmd.index("--max-budget-usd") + 1] == "0.5"


def test_always_requests_json_output():
    cmd = build_command("HB", "haiku", 0, None)
    assert cmd[cmd.index("--output-format") + 1] == "json"


def test_allowed_tools_expand():
    cmd = build_command("HB", "haiku", 0, "Read, Grep")
    # each whitelisted tool becomes its own --allowedTools flag
    assert cmd.count("--allowedTools") == 2
    assert "Read" in cmd and "Grep" in cmd


def test_parse_result_extracts_text_and_cost():
    payload = json.dumps(
        {"result": "all good", "total_cost_usd": 0.1234, "duration_ms": 4200, "num_turns": 3}
    )
    text, cost, meta = _parse_result(payload)
    assert text == "all good"
    assert cost == 0.1234
    assert meta["duration_ms"] == 4200
    assert meta["num_turns"] == 3


def test_parse_result_non_json_falls_back_to_raw():
    text, cost, meta = _parse_result("Not logged in")
    assert text == "Not logged in"
    assert cost is None
    assert meta == {}


def test_parse_result_empty():
    assert _parse_result("") == ("", None, {})


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
