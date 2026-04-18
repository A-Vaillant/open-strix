from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from langchain_core.callbacks import AsyncCallbackHandler


DEFAULT_EXCLUDE_TOOLS: tuple[str, ...] = (
    "write_todos",
    "send_message",
)

_TOOL_EMOJI: dict[str, str] = {
    "read_file": "🔍",
    "glob": "🔍",
    "list_messages": "🔍",
    "lookup": "🔍",
    "write_file": "✍️",
    "edit_file": "✍️",
    "journal": "✍️",
    "bash": "⚙️",
    "fetch_url": "🌐",
    "web_search": "🌐",
    "create_memory_block": "🧠",
    "update_memory_block": "🧠",
    "delete_memory_block": "🧠",
    "list_memory_blocks": "🧠",
    "add_schedule": "⏰",
    "list_schedules": "⏰",
    "remove_schedule": "⏰",
    "reload_pollers": "⏰",
    "react": "💫",
}
_DEFAULT_EMOJI = "🔧"


@dataclass
class ToolIndicatorsConfig:
    enabled: bool = False
    dm_only: bool = True
    include_tools: list[str] = field(default_factory=list)
    exclude_tools: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_TOOLS))
    batch_window_ms: int = 1500
    arg_hints: bool = True


def _basename(path_like: Any) -> str | None:
    if not isinstance(path_like, str) or not path_like.strip():
        return None
    try:
        return PurePosixPath(path_like).name or None
    except Exception:
        return None


def _hostname(url: Any) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        host = urlparse(url).hostname
        return host or None
    except Exception:
        return None


def _truncate(text: str, limit: int = 40) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def arg_hint_for(tool_name: str, tool_input: Any) -> str | None:
    """Extract a tiny contextual hint from a tool's input dict. Returns None if no useful hint."""
    if not isinstance(tool_input, dict):
        return None

    if tool_name in ("read_file", "write_file", "edit_file"):
        return _basename(tool_input.get("file_path"))
    if tool_name == "glob":
        pattern = tool_input.get("pattern")
        if isinstance(pattern, str):
            return _truncate(pattern, 30)
    if tool_name == "bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            first = command.strip().split(None, 1)
            return first[0] if first else None
    if tool_name == "fetch_url":
        return _hostname(tool_input.get("url"))
    if tool_name == "web_search":
        query = tool_input.get("query")
        if isinstance(query, str):
            return _truncate(query, 30)
    if tool_name in ("create_memory_block", "update_memory_block", "delete_memory_block"):
        name = tool_input.get("name") or tool_input.get("block_name")
        if isinstance(name, str) and name.strip():
            return _truncate(name, 30)
    if tool_name in ("add_schedule", "remove_schedule"):
        name = tool_input.get("name")
        if isinstance(name, str) and name.strip():
            return _truncate(name, 30)
    if tool_name == "lookup":
        query = tool_input.get("query") or tool_input.get("name")
        if isinstance(query, str):
            return _truncate(query, 30)
    return None


def format_entry(tool_name: str, hint: str | None, show_hint: bool) -> str:
    emoji = _TOOL_EMOJI.get(tool_name, _DEFAULT_EMOJI)
    if show_hint and hint:
        return f"{emoji} {tool_name} ({hint})"
    return f"{emoji} {tool_name}"


SendCoro = Callable[[str], Awaitable[None]]


class ToolIndicatorHandler(AsyncCallbackHandler):
    """Per-turn handler. Buffers tool-start events and flushes a coalesced line
    to Discord after `batch_window_ms` of quiet. `flush()` should be called at
    turn end to drain any remaining buffer.

    `send` is a coroutine the caller supplies — caller controls the Discord
    channel and can skip outbound-message bookkeeping.
    """

    raise_error = False

    def __init__(
        self,
        config: ToolIndicatorsConfig,
        send: SendCoro,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self._config = config
        self._send = send
        self._on_error = on_error
        self._buffer: list[str] = []
        self._timer: asyncio.TimerHandle | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._closed = False

    def _tool_allowed(self, tool_name: str) -> bool:
        if tool_name in self._config.exclude_tools:
            return False
        allow = self._config.include_tools
        if allow and tool_name not in allow:
            return False
        return True

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if self._closed:
            return
        tool_name = ""
        if isinstance(serialized, dict):
            tool_name = str(serialized.get("name") or "")
        if not tool_name:
            tool_name = str(kwargs.get("name") or "")
        if not tool_name or not self._tool_allowed(tool_name):
            return

        hint = arg_hint_for(tool_name, inputs) if inputs is not None else None
        self._buffer.append(format_entry(tool_name, hint, self._config.arg_hints))
        self._reschedule_flush()

    def _reschedule_flush(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        delay = max(0.0, self._config.batch_window_ms / 1000.0)
        self._timer = loop.call_later(delay, self._schedule_flush)

    def _schedule_flush(self) -> None:
        self._timer = None
        if self._closed or not self._buffer:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._flush_task = loop.create_task(self._flush())

    async def _flush(self) -> None:
        if not self._buffer:
            return
        entries, self._buffer = self._buffer, []
        text = " · ".join(entries)
        try:
            await self._send(text)
        except Exception as exc:  # pragma: no cover - defensive
            if self._on_error is not None:
                try:
                    self._on_error(text, exc)
                except Exception:
                    pass

    async def flush(self) -> None:
        """Cancel any pending timer and flush the buffer immediately."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._flush_task is not None and not self._flush_task.done():
            try:
                await self._flush_task
            except Exception:
                pass
            self._flush_task = None
        await self._flush()

    async def close(self) -> None:
        self._closed = True
        await self.flush()
