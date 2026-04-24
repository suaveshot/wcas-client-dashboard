"""Tests for dashboard_app/agents/activation_agent.py.

The Anthropic SDK is never touched. We fake the `client.beta.*` surface
with SimpleNamespace objects that mirror the real contract captured in
anthropic.types.beta.sessions.* (SDK 0.96.0).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-key")

import pytest

from dashboard_app.agents import activation_agent


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeStream:
    """Context-manager iterable matching client.beta.sessions.events.stream(...)."""

    def __init__(self, events: list):
        self._events = list(events)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        # Yield events one by one; new ones can be appended mid-loop via the
        # `extend` hook used by FakeEvents.send below.
        while self._events:
            yield self._events.pop(0)


class FakeEvents:
    def __init__(self, seed_events: list):
        # Copy so the stream can consume without mutating the test's list.
        self._stream = FakeStream(list(seed_events))
        self.sent: list[dict] = []
        # When we receive a tool_result, append these next events into the
        # live stream so the loop keeps yielding.
        self.next_after_send: list = []

    def stream(self, session_id: str):
        return self._stream

    def send(self, session_id: str, events):
        self.sent.append({"session_id": session_id, "events": list(events)})
        # If the test primed a follow-up burst, inject it into the live stream.
        if self.next_after_send:
            self._stream._events.extend(self.next_after_send)
            self.next_after_send = []


class FakeSessionsResource:
    def __init__(self, events: "FakeEvents"):
        self.events = events
        self.created_kwargs: list[dict] = []
        self.deleted: list[str] = []
        self._next_id = 1

    def create(self, **kwargs):
        sid = f"sess_{self._next_id}"
        self._next_id += 1
        self.created_kwargs.append(kwargs)
        return SimpleNamespace(id=sid)

    def delete(self, session_id: str):
        self.deleted.append(session_id)


class FakeAgentsResource:
    def __init__(self):
        self.created_kwargs: list[dict] = []

    def create(self, **kwargs):
        self.created_kwargs.append(kwargs)
        return SimpleNamespace(id="agent_abc", version="v1")


class FakeEnvironmentsResource:
    def __init__(self):
        self.created_kwargs: list[dict] = []

    def create(self, **kwargs):
        self.created_kwargs.append(kwargs)
        return SimpleNamespace(id="env_xyz")


class FakeBeta:
    def __init__(self, events: FakeEvents):
        self.agents = FakeAgentsResource()
        self.environments = FakeEnvironmentsResource()
        self.sessions = FakeSessionsResource(events)


class FakeAnthropic:
    def __init__(self, seed_events: list, **_kwargs):
        self.events = FakeEvents(seed_events)
        self.beta = FakeBeta(self.events)


# Shortcut event factories matching anthropic.types.beta.sessions.*
def ev_assistant(text: str):
    return SimpleNamespace(
        type="agent.message",
        content=[SimpleNamespace(type="text", text=text)],
    )


def ev_tool_use(call_id: str, name: str, input_: dict | None = None):
    return SimpleNamespace(
        type="agent.custom_tool_use",
        id=call_id,
        name=name,
        input=input_ or {},
    )


def ev_idle_requires(event_ids: list[str]):
    return SimpleNamespace(
        type="session.status_idle",
        stop_reason=SimpleNamespace(type="requires_action", event_ids=event_ids),
    )


def ev_idle_end_turn():
    return SimpleNamespace(
        type="session.status_idle",
        stop_reason=SimpleNamespace(type="end_turn"),
    )


def ev_idle_exhausted():
    return SimpleNamespace(
        type="session.status_idle",
        stop_reason=SimpleNamespace(type="retries_exhausted"),
    )


def ev_span_usage(input_tokens: int, output_tokens: int):
    return SimpleNamespace(
        type="span.model_request_end",
        model_usage=SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # Predictable cost log path.
    monkeypatch.setenv("COST_LOG_PATH", str(tmp_path / "cost.jsonl"))
    # Force opus so prompt has predictable shape; tests don't care about model.
    monkeypatch.setenv("ACTIVATION_AGENT_MODEL", "claude-opus-4-7")
    activation_agent._reset_module_cache_for_tests()
    yield
    activation_agent._reset_module_cache_for_tests()


def _make_client(events: list) -> FakeAnthropic:
    return FakeAnthropic(events)


# ---------------------------------------------------------------------------
# System prompt invariants
# ---------------------------------------------------------------------------


# Keep em/en dashes out of this test source per the brand rule. Build them
# at runtime from code points when we actually need them.
_EM = chr(0x2014)
_EN = chr(0x2013)


def test_system_prompt_never_uses_em_dashes():
    assert _EM not in activation_agent.SYSTEM_PROMPT
    assert _EN not in activation_agent.SYSTEM_PROMPT


def test_system_prompt_forbids_ai_self_reference():
    # Collapse whitespace so assertions aren't sensitive to line wrapping.
    p = " ".join(activation_agent.SYSTEM_PROMPT.lower().split())
    # The prompt must TELL the agent not to say "I'm an AI" (positive mention OK)
    assert "i'm an ai" in p
    assert "never name the model or vendor running you" in p
    # And should not leak the model name itself
    assert "claude" not in p
    assert "opus" not in p
    assert "anthropic" not in p


def test_system_prompt_lists_happy_path_tools():
    for name in [
        "fetch_site_facts", "confirm_company_facts", "request_credential",
        "activate_pipeline", "capture_baseline", "create_ga4_property",
        "verify_gsc_domain", "mark_activation_complete",
    ]:
        assert name in activation_agent.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Lifecycle caching
# ---------------------------------------------------------------------------


def test_get_agent_id_creates_once_and_caches():
    client = _make_client([])
    aid1 = activation_agent.get_agent_id(client=client)
    aid2 = activation_agent.get_agent_id(client=client)
    assert aid1 == aid2 == "agent_abc"
    # Only ONE create call hit the SDK.
    assert len(client.beta.agents.created_kwargs) == 1
    # Cached to disk for cross-process reuse.
    assert (Path(os.environ["TENANT_ROOT"]) / "_platform" / "agents").exists()


def test_get_agent_id_reloads_from_disk_across_processes():
    client = _make_client([])
    first = activation_agent.get_agent_id(client=client)
    # Simulate a fresh process.
    activation_agent._reset_module_cache_for_tests()
    second = activation_agent.get_agent_id(client=client)
    assert first == second
    # Did NOT re-create on the SDK.
    assert len(client.beta.agents.created_kwargs) == 1


def test_get_environment_id_creates_once_and_caches():
    client = _make_client([])
    eid1 = activation_agent.get_environment_id(client=client)
    eid2 = activation_agent.get_environment_id(client=client)
    assert eid1 == eid2 == "env_xyz"
    assert len(client.beta.environments.created_kwargs) == 1


def test_get_or_create_session_writes_and_reloads():
    client = _make_client([])
    sid1 = activation_agent.get_or_create_session("acme", client=client)
    sid2 = activation_agent.get_or_create_session("acme", client=client)
    assert sid1 == sid2
    # Only one session.create call.
    assert len(client.beta.sessions.created_kwargs) == 1


def test_reset_session_clears_file_and_deletes_remote():
    client = _make_client([])
    sid = activation_agent.get_or_create_session("acme", client=client)
    assert activation_agent.reset_session("acme", client=client) is True
    assert sid in client.beta.sessions.deleted
    # Next call creates a fresh session.
    sid2 = activation_agent.get_or_create_session("acme", client=client)
    assert sid2 != sid
    # On a fresh tenant without a stored session, reset returns False.
    assert activation_agent.reset_session("unused", client=client) is False


# ---------------------------------------------------------------------------
# run_turn: happy paths + error paths
# ---------------------------------------------------------------------------


def test_run_turn_rejects_empty_message():
    client = _make_client([])
    with pytest.raises(ValueError):
        activation_agent.run_turn("acme", "", client=client)
    with pytest.raises(ValueError):
        activation_agent.run_turn("acme", "   ", client=client)


def test_run_turn_assistant_only_reaches_end_turn():
    seed = [
        ev_assistant("Welcome in. What brings you here today?"),
        ev_span_usage(123, 45),
        ev_idle_end_turn(),
    ]
    client = _make_client(seed)
    result = activation_agent.run_turn("acme", "hi", client=client)
    assert result["reached_idle"] is True
    assert any(e.get("role") == "assistant" for e in result["events"])
    assert result["usage"]["input_tokens"] == 123
    assert result["usage"]["output_tokens"] == 45
    # Cost recorded
    assert result["usage"]["usd"] > 0


def test_run_turn_dispatches_tool_and_sends_result(monkeypatch):
    # Stub activation_tools.dispatch so no real tenant I/O happens.
    calls = []
    def fake_dispatch(tid, name, args):
        calls.append((tid, name, args))
        return True, {"status": "saved", "fields_recorded": ["name", "city"]}
    monkeypatch.setattr(activation_agent.activation_tools, "dispatch", fake_dispatch)

    seed = [
        ev_tool_use("tu_1", "confirm_company_facts", {"name": "Acme"}),
        ev_idle_requires(["tu_1"]),
    ]
    client = _make_client(seed)
    # After we send the tool_result, the stream should yield the assistant's follow-up.
    client.events.next_after_send = [
        ev_assistant("Saved your basics."),
        ev_span_usage(50, 20),
        ev_idle_end_turn(),
    ]

    result = activation_agent.run_turn("acme", "looks right", client=client)

    assert result["reached_idle"] is True
    assert calls == [("acme", "confirm_company_facts", {"name": "Acme"})]
    # user.custom_tool_result was sent back with the correct id + non-error.
    sends = client.events.sent
    assert any(
        any(
            ev["type"] == "user.custom_tool_result"
            and ev["custom_tool_use_id"] == "tu_1"
            and ev["is_error"] is False
            for ev in s["events"]
        )
        for s in sends
    )
    # A tool event pill + an assistant bubble both landed in the UI event list.
    kinds = [e.get("role") for e in result["events"]]
    assert "tool" in kinds
    assert "assistant" in kinds


def test_run_turn_tool_error_surfaces_is_error_true(monkeypatch):
    def fake_dispatch(tid, name, args):
        return False, {"error": "bad thing", "tool": name}
    monkeypatch.setattr(activation_agent.activation_tools, "dispatch", fake_dispatch)

    seed = [
        ev_tool_use("tu_err", "confirm_company_facts", {}),
        ev_idle_requires(["tu_err"]),
    ]
    client = _make_client(seed)
    client.events.next_after_send = [
        ev_assistant("That one failed. Let me try again."),
        ev_idle_end_turn(),
    ]

    result = activation_agent.run_turn("acme", "go", client=client)

    # The tool_result event flagged is_error=True.
    sent_results = [
        ev for s in client.events.sent for ev in s["events"]
        if ev["type"] == "user.custom_tool_result"
    ]
    assert len(sent_results) == 1
    assert sent_results[0]["is_error"] is True
    # And the agent still got to narrate the failure.
    assert result["reached_idle"] is True
    assistant_texts = [e["text"] for e in result["events"] if e.get("role") == "assistant"]
    assert any("failed" in t.lower() for t in assistant_texts)


def test_run_turn_retries_exhausted_reports_system_error():
    seed = [ev_idle_exhausted()]
    client = _make_client(seed)
    result = activation_agent.run_turn("acme", "go", client=client)
    assert result["reached_idle"] is False
    assert any(e.get("role") == "system" for e in result["events"])


def test_run_turn_post_filters_em_dashes_in_assistant_text():
    text_with_em = f"Your site looks clean {_EM} plumber in Ventura with 4.6 stars."
    seed = [
        ev_assistant(text_with_em),
        ev_idle_end_turn(),
    ]
    client = _make_client(seed)
    result = activation_agent.run_turn("acme", "go", client=client)
    asst = [e["text"] for e in result["events"] if e.get("role") == "assistant"]
    assert asst and _EM not in asst[0]


def test_run_turn_respects_cost_cap(monkeypatch):
    # Force the cap check to return False.
    monkeypatch.setattr(
        activation_agent.cost_tracker, "should_allow",
        lambda _tid: (False, "Daily tenant cap reached ($2.00)"),
    )
    client = _make_client([])
    result = activation_agent.run_turn("acme", "go", client=client)
    assert result["reached_idle"] is False
    # No session even created.
    assert len(client.beta.sessions.created_kwargs) == 0
    assert any("cap" in e.get("text", "").lower() for e in result["events"] if e.get("role") == "system")


def test_run_turn_records_cost_via_cost_tracker(monkeypatch):
    seen = []
    def fake_record(**kwargs):
        seen.append(kwargs)
        return 0.0042
    monkeypatch.setattr(activation_agent.cost_tracker, "record_call", fake_record)

    seed = [
        ev_assistant("ok"),
        ev_span_usage(100, 50),
        ev_span_usage(10, 5),
        ev_idle_end_turn(),
    ]
    client = _make_client(seed)
    activation_agent.run_turn("acme", "go", client=client)

    assert len(seen) == 1
    call = seen[0]
    assert call["tenant_id"] == "acme"
    assert call["kind"] == "activation_turn"
    # Usage accumulated across both span events.
    assert call["input_tokens"] == 110
    assert call["output_tokens"] == 55


def test_run_turn_two_consecutive_calls_reuse_session():
    seed1 = [ev_assistant("hi"), ev_idle_end_turn()]
    client = _make_client(seed1)
    activation_agent.run_turn("acme", "first", client=client)
    # Re-seed the stream for turn 2 with fresh events.
    client.events._stream._events.extend([ev_assistant("again"), ev_idle_end_turn()])
    activation_agent.run_turn("acme", "second", client=client)
    # Only ONE session was created across two turns.
    assert len(client.beta.sessions.created_kwargs) == 1


def test_run_turn_unknown_tool_still_completes(monkeypatch):
    # Real dispatch returns (False, {"error": "unknown tool ..."}).
    seed = [
        ev_tool_use("tu_x", "i_do_not_exist", {}),
        ev_idle_requires(["tu_x"]),
    ]
    client = _make_client(seed)
    client.events.next_after_send = [
        ev_assistant("That tool isn't available."),
        ev_idle_end_turn(),
    ]
    result = activation_agent.run_turn("acme", "go", client=client)
    assert result["reached_idle"] is True
    # Tool event pill reports the failure.
    tool_events = [e for e in result["events"] if e.get("role") == "tool"]
    assert tool_events and tool_events[0]["ok"] is False
