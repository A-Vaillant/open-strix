---
name: algedonic-signals
description: Understand and implement watchers — event-triggered monitoring hooks that detect behavioral drift, agreement violations, and operational anomalies. Use when the user wants to monitor agent behavior, detect drift patterns, set up turn_complete hooks, or understand the watchers.json system.
---

# Algedonic Signals — Behavioral Monitoring via Watchers

Algedonic signals bypass the management hierarchy to report pain/pleasure directly
to the operator. In Stafford Beer's Viable System Model, they are the channel that
lets S5 (identity/policy) hear from S1 (operations) without every layer in between
filtering the signal.

For AI agents, this means: **monitoring that the agent cannot suppress, rationalize,
or choose to ignore.**

## The Watcher System

Watchers are the infrastructure for algedonic signals. They extend the existing
poller framework with event triggers — a watcher can fire on a cron schedule
(like a poller) **or** on an agent event (like `turn_complete`).

### watchers.json

Skills declare watchers in a `watchers.json` file alongside `SKILL.md`:

```json
{
  "watchers": [
    {
      "name": "codex-bypass",
      "command": "python check_codex_usage.py",
      "trigger": "turn_complete"
    },
    {
      "name": "daily-health",
      "command": "python health_check.py",
      "cron": "0 12 * * *"
    }
  ]
}
```

Each watcher must have `name`, `command`, and exactly one of:
- **`cron`** — fires on a schedule (same as pollers)
- **`trigger`** — fires on an agent event

### Valid Triggers

| Trigger | When It Fires | What the Watcher Receives |
|---------|--------------|--------------------------|
| `turn_complete` | After the agent finishes processing an event | `{trigger, trace_id, events_path}` |
| `session_start` | When the agent session begins | `{trigger, trace_id, events_path}` |
| `session_end` | When the agent session ends | `{trigger, trace_id, events_path}` |

### Input Contract

Event-triggered watchers receive a JSON object on **stdin**:

```json
{
  "trigger": "turn_complete",
  "trace_id": "20260326T213000Z-a1b2c3d4",
  "events_path": "/home/user/agent/logs/events.jsonl"
}
```

The watcher then:
1. Reads `events_path`
2. Filters by `trace_id` to scope to the current turn
3. Runs its analysis
4. Emits JSONL findings to **stdout**

This is deliberately minimal — the watcher has full access to `events.jsonl` and
can read as much historical context as it needs.

### Output Contract

Each line of stdout is a JSON finding:

```json
{"signal": "codex_bypass", "severity": "warn", "message": "Edited 3 code files without delegating to Codex", "route": "operator"}
```

Fields:
- **`signal`** — identifier for the finding type
- **`severity`** — `info`, `warn`, or `error`
- **`message`** — human-readable description
- **`route`** — where the signal goes: `"log"` (default), `"agent"`, `"operator"`

### Routing

| Route | Behavior |
|-------|----------|
| `log` | Written to events.jsonl as `watcher_signal`. Passive — operator checks when they want. |
| `agent` | Enqueued as a new agent event. The agent sees the watcher's message in its next turn. |
| `operator` | Logged. (Operator notification channel is deployment-specific — configure via env vars.) |

### Environment Variables

Watchers receive the same env vars as pollers:

| Variable | Description |
|----------|-------------|
| `STATE_DIR` | The skill directory (writable, for state files) |
| `WATCHER_NAME` | The watcher's name from watchers.json |

Plus custom env vars from the `env` field and the agent's existing environment.

## Backward Compatibility

`pollers.json` files continue to work exactly as before. The `watchers.json` format
is an extension, not a replacement. Skills can use either or both.

## Anti-Pattern Catalog

These are documented cases where agent monitoring would have caught problems earlier:

| Anti-Pattern | Detection Shape | Watcher Type |
|---|---|---|
| **Agreement violation** (e.g., writing code instead of delegating) | Session-level: action present + expected delegation absent | `turn_complete` |
| **Behavioral shift after model upgrade** | Cross-session: trigger threshold changed | `turn_complete` with historical comparison |
| **Silent sessions** | Session-level: many tool calls, no communication | `turn_complete` |
| **Metric gaming** | Cross-session: metric improving but purpose not served | Cron-based review |
| **Performative compliance** | Session-level: raising concern then immediately resolving it | `turn_complete` (hard to detect mechanically) |

## In-Process vs Out-of-Process

Two complementary monitoring patterns:

**Out-of-process watchers (this system):**
- Structurally independent — can't be suppressed by the agent
- Fire after the action (observe and report)
- Best for: behavioral drift, agreement violations, operator alerting

**In-process hooks (e.g., Claude Code PreToolUse):**
- Fast — can block actions before they happen
- Part of the agent process (agent can potentially rationalize around them)
- Best for: known dangerous actions, safety gates

Use both. Watchers for detection, in-process hooks for prevention.

## Writing a Watcher

See `examples/` in this skill directory for complete working examples.

The simplest useful watcher:

```python
#!/usr/bin/env python3
"""Detect turns with many tool calls but no communication."""
import json
import sys

context = json.loads(sys.stdin.readline())
trace_id = context["trace_id"]
events_path = context["events_path"]

with open(events_path) as f:
    events = [json.loads(line) for line in f if line.strip()]

turn_events = [e for e in events if e.get("session_id") == trace_id]
tool_calls = [e for e in turn_events if e.get("type") == "tool_call"]
sends = [e for e in tool_calls if e.get("tool") == "send_message"]

if len(tool_calls) > 10 and not sends:
    print(json.dumps({
        "signal": "silent_session",
        "severity": "warn",
        "message": f"Session had {len(tool_calls)} tool calls but sent no messages",
        "route": "log",
    }))
```
