"""Per-turn loop detector wrapping in ToolsMixin._attach_loop_detector.

Identical (tool_name, args) calls past TOOL_LOOP_DETECTION_LIMIT must return
a loop-stop string instead of executing. Different args or different tools
do not count toward the same key. send_message is exempt.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.tools import tool

from open_strix.tools import (
    TOOL_LOOP_DETECTION_EXEMPT,
    TOOL_LOOP_DETECTION_LIMIT,
    ToolsMixin,
    _hash_tool_args,
)


class _Stub(ToolsMixin):
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._reset_tool_loop_tracker()

    def log_event(self, name: str, **fields: Any) -> None:
        self.events.append({"name": name, **fields})


def _build_journal_like_tool(call_log: list[dict[str, Any]]):
    @tool("journal_test")
    def journal_test(user_wanted: str, agent_did: str, predictions: str) -> str:
        """Test tool: append args to call_log and return a fake ack."""
        call_log.append(
            {"user_wanted": user_wanted, "agent_did": agent_did, "predictions": predictions},
        )
        return "Journal entry saved."

    return journal_test


def _build_async_echo_tool(call_log: list[str]):
    @tool("echo_test")
    async def echo_test(text: str) -> str:
        """Test tool: append text to call_log and echo."""
        call_log.append(text)
        return f"echoed: {text}"

    return echo_test


def test_hash_tool_args_is_order_invariant() -> None:
    a = _hash_tool_args({"x": 1, "y": "two"})
    b = _hash_tool_args({"y": "two", "x": 1})
    assert a == b
    assert _hash_tool_args(None) == _hash_tool_args({})


def test_identical_calls_hard_stop_after_limit() -> None:
    stub = _Stub()
    call_log: list[dict[str, Any]] = []
    tool_obj = _build_journal_like_tool(call_log)
    stub._attach_loop_detector([tool_obj])

    args = {"user_wanted": "a", "agent_did": "b", "predictions": "c"}
    for _ in range(TOOL_LOOP_DETECTION_LIMIT):
        result = tool_obj.func(**args)
        assert result == "Journal entry saved."

    blocked = tool_obj.func(**args)
    assert "LOOP DETECTED" in blocked
    assert "journal_test" in blocked
    assert len(call_log) == TOOL_LOOP_DETECTION_LIMIT, (
        "tool body must not run on the hard-stopped call"
    )
    assert any(e["name"] == "tool_loop_hard_stop" for e in stub.events)


def test_different_args_do_not_trip_detector() -> None:
    stub = _Stub()
    call_log: list[dict[str, Any]] = []
    tool_obj = _build_journal_like_tool(call_log)
    stub._attach_loop_detector([tool_obj])

    for i in range(TOOL_LOOP_DETECTION_LIMIT + 2):
        result = tool_obj.func(user_wanted=f"u{i}", agent_did="b", predictions="c")
        assert result == "Journal entry saved."

    assert len(call_log) == TOOL_LOOP_DETECTION_LIMIT + 2


def test_async_tool_is_wrapped() -> None:
    stub = _Stub()
    call_log: list[str] = []
    tool_obj = _build_async_echo_tool(call_log)
    stub._attach_loop_detector([tool_obj])

    async def run() -> tuple[list[Any], str]:
        results = []
        for _ in range(TOOL_LOOP_DETECTION_LIMIT):
            results.append(await tool_obj.coroutine(text="same"))
        blocked = await tool_obj.coroutine(text="same")
        return results, blocked

    results, blocked = asyncio.run(run())
    assert all(r == "echoed: same" for r in results)
    assert "LOOP DETECTED" in blocked
    assert len(call_log) == TOOL_LOOP_DETECTION_LIMIT


def test_send_message_is_exempt() -> None:
    assert "send_message" in TOOL_LOOP_DETECTION_EXEMPT
    stub = _Stub()

    @tool("send_message")
    async def fake_send_message(text: str) -> str:
        """Test tool: imitate send_message for exemption check."""
        return f"sent: {text}"

    original_coro = fake_send_message.coroutine
    stub._attach_loop_detector([fake_send_message])
    assert fake_send_message.coroutine is original_coro


def test_reset_clears_counts() -> None:
    stub = _Stub()
    call_log: list[dict[str, Any]] = []
    tool_obj = _build_journal_like_tool(call_log)
    stub._attach_loop_detector([tool_obj])

    args = {"user_wanted": "a", "agent_did": "b", "predictions": "c"}
    for _ in range(TOOL_LOOP_DETECTION_LIMIT + 1):
        tool_obj.func(**args)

    stub._reset_tool_loop_tracker()
    result = tool_obj.func(**args)
    assert result == "Journal entry saved."
