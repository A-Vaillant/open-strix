"""Tests for the tool-indicator callback handler and its config parsing."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from open_strix.config import (
    AppConfig,
    RepoLayout,
    _parse_tool_indicators,
    load_config,
)
from open_strix.tool_indicators import (
    DEFAULT_EXCLUDE_TOOLS,
    ToolIndicatorHandler,
    ToolIndicatorsConfig,
    arg_hint_for,
    format_entry,
)


class TestArgHintFor:
    def test_file_tools_use_basename(self) -> None:
        assert arg_hint_for("read_file", {"file_path": "/a/b/c/notes.md"}) == "notes.md"
        assert arg_hint_for("write_file", {"file_path": "state/phone-book.md"}) == "phone-book.md"
        assert arg_hint_for("edit_file", {"file_path": "foo.txt"}) == "foo.txt"

    def test_file_tools_return_none_when_missing(self) -> None:
        assert arg_hint_for("read_file", {}) is None
        assert arg_hint_for("read_file", {"file_path": ""}) is None

    def test_bash_returns_first_word(self) -> None:
        assert arg_hint_for("bash", {"command": "ls -la /tmp"}) == "ls"
        assert arg_hint_for("bash", {"command": "  python script.py  "}) == "python"

    def test_bash_none_when_blank(self) -> None:
        assert arg_hint_for("bash", {"command": "   "}) is None
        assert arg_hint_for("bash", {}) is None

    def test_fetch_url_returns_hostname(self) -> None:
        assert arg_hint_for("fetch_url", {"url": "https://example.com/foo/bar"}) == "example.com"
        assert arg_hint_for("fetch_url", {"url": "http://sub.domain.test:8080/x"}) == "sub.domain.test"

    def test_fetch_url_none_when_unparseable(self) -> None:
        assert arg_hint_for("fetch_url", {"url": "not-a-url"}) is None
        assert arg_hint_for("fetch_url", {}) is None

    def test_web_search_truncates_query(self) -> None:
        short = arg_hint_for("web_search", {"query": "what is agency"})
        assert short == "what is agency"
        long_query = "a" * 60
        result = arg_hint_for("web_search", {"query": long_query})
        assert result is not None
        assert len(result) <= 30
        assert result.endswith("…")

    def test_memory_block_name(self) -> None:
        assert arg_hint_for("create_memory_block", {"name": "alice"}) == "alice"
        assert arg_hint_for("update_memory_block", {"block_name": "persona"}) == "persona"
        assert arg_hint_for("delete_memory_block", {"name": "x"}) == "x"

    def test_schedule_name(self) -> None:
        assert arg_hint_for("add_schedule", {"name": "morning-check"}) == "morning-check"
        assert arg_hint_for("remove_schedule", {"name": "old-job"}) == "old-job"

    def test_glob_returns_pattern(self) -> None:
        assert arg_hint_for("glob", {"pattern": "**/*.py"}) == "**/*.py"

    def test_lookup_accepts_query_or_name(self) -> None:
        assert arg_hint_for("lookup", {"query": "alice"}) == "alice"
        assert arg_hint_for("lookup", {"name": "bob"}) == "bob"

    def test_unknown_tool_returns_none(self) -> None:
        assert arg_hint_for("mystery_tool", {"x": 1}) is None

    def test_non_dict_input(self) -> None:
        assert arg_hint_for("read_file", None) is None
        assert arg_hint_for("read_file", "string") is None


class TestFormatEntry:
    def test_known_tool_with_hint(self) -> None:
        entry = format_entry("write_file", "notes.md", True)
        assert entry.startswith("✍️ ")
        assert entry.endswith(" (notes.md)")
        assert "~" in entry

    def test_known_tool_hint_disabled(self) -> None:
        entry = format_entry("write_file", "notes.md", False)
        assert entry.startswith("✍️ ")
        assert entry.endswith("~")
        assert "(" not in entry

    def test_known_tool_without_hint(self) -> None:
        entry = format_entry("read_file", None, True)
        assert entry.startswith("🔍 ")
        assert entry.endswith("~")

    def test_unknown_tool_uses_default_emoji(self) -> None:
        assert format_entry("mystery", None, True) == "🔧 mystery~"
        assert format_entry("mystery", "foo", True) == "🔧 mystery~ (foo)"


class TestParseToolIndicators:
    def test_none_returns_defaults(self) -> None:
        cfg = _parse_tool_indicators(None)
        assert cfg.enabled is False
        assert cfg.dm_only is True
        assert cfg.include_tools == []
        assert cfg.exclude_tools == list(DEFAULT_EXCLUDE_TOOLS)
        assert cfg.batch_window_ms == 1500
        assert cfg.arg_hints is True

    def test_non_dict_returns_defaults(self) -> None:
        assert _parse_tool_indicators("bad").enabled is False
        assert _parse_tool_indicators(42).exclude_tools == list(DEFAULT_EXCLUDE_TOOLS)

    def test_enabled_override(self) -> None:
        cfg = _parse_tool_indicators({"enabled": True})
        assert cfg.enabled is True

    def test_custom_lists(self) -> None:
        cfg = _parse_tool_indicators({
            "include_tools": ["bash", "write_file"],
            "exclude_tools": ["read_file"],
        })
        assert cfg.include_tools == ["bash", "write_file"]
        assert cfg.exclude_tools == ["read_file"]

    def test_comma_separated_string_lists(self) -> None:
        cfg = _parse_tool_indicators({"include_tools": "bash, write_file"})
        assert cfg.include_tools == ["bash", "write_file"]

    def test_batch_window_floored_at_zero(self) -> None:
        cfg = _parse_tool_indicators({"batch_window_ms": -100})
        assert cfg.batch_window_ms == 0

    def test_disable_arg_hints(self) -> None:
        cfg = _parse_tool_indicators({"arg_hints": False})
        assert cfg.arg_hints is False

    def test_dm_only_override(self) -> None:
        cfg = _parse_tool_indicators({"dm_only": False})
        assert cfg.dm_only is False


class TestAppConfigToolIndicators:
    def test_default_is_disabled(self) -> None:
        cfg = AppConfig()
        assert cfg.tool_indicators.enabled is False
        assert cfg.tool_indicators.dm_only is True

    def test_load_config_picks_up_yaml(self, tmp_path: Path) -> None:
        data = {
            "model": "test-model",
            "tool_indicators": {
                "enabled": True,
                "dm_only": False,
                "exclude_tools": ["write_todos"],
                "batch_window_ms": 500,
            },
        }
        (tmp_path / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert config.tool_indicators.enabled is True
        assert config.tool_indicators.dm_only is False
        assert config.tool_indicators.exclude_tools == ["write_todos"]
        assert config.tool_indicators.batch_window_ms == 500

    def test_load_config_without_key_uses_defaults(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text(yaml.safe_dump({"model": "x"}), encoding="utf-8")
        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert config.tool_indicators.enabled is False
        assert config.tool_indicators.exclude_tools == list(DEFAULT_EXCLUDE_TOOLS)


def _make_handler(
    config: ToolIndicatorsConfig,
    sent: list[str],
    errors: list[tuple[str, Exception]] | None = None,
    fail: bool = False,
) -> ToolIndicatorHandler:
    async def send(text: str) -> None:
        if fail:
            raise RuntimeError("boom")
        sent.append(text)

    def on_error(text: str, exc: Exception) -> None:
        if errors is not None:
            errors.append((text, exc))

    return ToolIndicatorHandler(config, send, on_error)


class TestToolIndicatorHandler:
    @pytest.mark.asyncio
    async def test_single_tool_flushes_on_close(self) -> None:
        sent: list[str] = []
        cfg = ToolIndicatorsConfig(enabled=True, batch_window_ms=50)
        h = _make_handler(cfg, sent)
        await h.on_tool_start(
            {"name": "write_file"},
            "",
            inputs={"file_path": "/x/notes.md"},
        )
        await h.close()
        assert len(sent) == 1
        assert sent[0].startswith("✍️ ")
        assert sent[0].endswith(" (notes.md)")

    @pytest.mark.asyncio
    async def test_multiple_tools_coalesce(self) -> None:
        sent: list[str] = []
        cfg = ToolIndicatorsConfig(enabled=True, batch_window_ms=5000)
        h = _make_handler(cfg, sent)
        await h.on_tool_start({"name": "write_file"}, "", inputs={"file_path": "a.md"})
        await h.on_tool_start({"name": "read_file"}, "", inputs={"file_path": "b.md"})
        await h.on_tool_start({"name": "bash"}, "", inputs={"command": "ls"})
        await h.close()
        assert len(sent) == 1
        assert "✍️" in sent[0] and "(a.md)" in sent[0]
        assert "🔍" in sent[0] and "(b.md)" in sent[0]
        assert "⚙️" in sent[0] and "(ls)" in sent[0]
        assert " · " in sent[0]

    @pytest.mark.asyncio
    async def test_timer_flushes_mid_stream(self) -> None:
        sent: list[str] = []
        cfg = ToolIndicatorsConfig(enabled=True, batch_window_ms=20)
        h = _make_handler(cfg, sent)
        await h.on_tool_start({"name": "write_file"}, "", inputs={"file_path": "a.md"})
        await asyncio.sleep(0.08)  # well past batch window
        await h.on_tool_start({"name": "read_file"}, "", inputs={"file_path": "b.md"})
        await h.close()
        # first batch should have flushed on its own; second flushes on close
        assert len(sent) == 2
        assert "✍️" in sent[0] and "(a.md)" in sent[0]
        assert "🔍" in sent[1] and "(b.md)" in sent[1]

    @pytest.mark.asyncio
    async def test_excluded_tool_skipped(self) -> None:
        sent: list[str] = []
        cfg = ToolIndicatorsConfig(enabled=True, exclude_tools=["write_todos"])
        h = _make_handler(cfg, sent)
        await h.on_tool_start({"name": "write_todos"}, "", inputs={})
        await h.close()
        assert sent == []

    @pytest.mark.asyncio
    async def test_include_allowlist_filters(self) -> None:
        sent: list[str] = []
        cfg = ToolIndicatorsConfig(enabled=True, include_tools=["bash"])
        h = _make_handler(cfg, sent)
        await h.on_tool_start({"name": "bash"}, "", inputs={"command": "ls"})
        await h.on_tool_start({"name": "write_file"}, "", inputs={"file_path": "a.md"})
        await h.close()
        assert len(sent) == 1
        assert "⚙️" in sent[0] and "(ls)" in sent[0]
        assert "✍️" not in sent[0]

    @pytest.mark.asyncio
    async def test_arg_hints_disabled(self) -> None:
        sent: list[str] = []
        cfg = ToolIndicatorsConfig(enabled=True, arg_hints=False)
        h = _make_handler(cfg, sent)
        await h.on_tool_start({"name": "write_file"}, "", inputs={"file_path": "a.md"})
        await h.close()
        assert len(sent) == 1
        assert sent[0].startswith("✍️ ")
        assert sent[0].endswith("~")
        assert "(" not in sent[0]

    @pytest.mark.asyncio
    async def test_send_error_is_reported(self) -> None:
        sent: list[str] = []
        errors: list[tuple[str, Exception]] = []
        cfg = ToolIndicatorsConfig(enabled=True)
        h = _make_handler(cfg, sent, errors, fail=True)
        await h.on_tool_start({"name": "bash"}, "", inputs={"command": "ls"})
        await h.close()
        assert sent == []
        assert len(errors) == 1
        assert isinstance(errors[0][1], RuntimeError)

    @pytest.mark.asyncio
    async def test_unknown_tool_name_uses_default_emoji(self) -> None:
        sent: list[str] = []
        cfg = ToolIndicatorsConfig(enabled=True)
        h = _make_handler(cfg, sent)
        await h.on_tool_start({"name": "mystery_tool"}, "", inputs={})
        await h.close()
        assert sent == ["🔧 mystery_tool~"]

    @pytest.mark.asyncio
    async def test_missing_tool_name_is_ignored(self) -> None:
        sent: list[str] = []
        cfg = ToolIndicatorsConfig(enabled=True)
        h = _make_handler(cfg, sent)
        await h.on_tool_start({}, "", inputs={})
        await h.close()
        assert sent == []

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self) -> None:
        sent: list[str] = []
        cfg = ToolIndicatorsConfig(enabled=True)
        h = _make_handler(cfg, sent)
        await h.on_tool_start({"name": "bash"}, "", inputs={"command": "ls"})
        await h.close()
        await h.close()  # should not blow up or double-send
        assert len(sent) == 1
