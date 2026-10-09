"""S11 offline tests: write restriction, SDK option hardening, API backend via a mocked transport."""
import asyncio
import json

import anthropic
import httpx2
import pytest

from llm.agent_sdk_backend import AgentSDKBackend, make_guard, mcp_name
from llm.claude_api_backend import ClaudeAPIBackend
from llm.tools import PartWorkspace

BOX = "import Part\nresult['volume'] = Part.makeBox(10, 20, 30).Volume\nresult['pass'] = True\n"


@pytest.fixture
def ws(tmp_path):
    docs = tmp_path / "sdk_docs"
    docs.mkdir()
    (docs / "readme.md").write_text("docs", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    return PartWorkspace(tmp_path, "upper_arm", read_roots=[docs])


@pytest.mark.parametrize("path", [
    "parts/forearm.py",                 # another part
    "parts/../parts/forearm.py",        # traversal to another part
    "parts/upper_arm.py/../x.py",
    "../outside.py",
    "plan.json",
    "parts/upper_arm.pyc",
])
def test_write_outside_own_file_is_denied(ws, path):
    text, is_error = ws.tools()[1].call({"path": path, "content": "x"})
    assert is_error and json.loads(text)["error"] == "denied"
    assert not (ws.root / path).resolve().exists()


def test_write_absolute_path_elsewhere_is_denied(ws, tmp_path_factory):
    other = tmp_path_factory.mktemp("other") / "upper_arm.py"
    _, is_error = ws.tools()[1].call({"path": str(other), "content": "x"})
    assert is_error and not other.exists()


def test_write_own_file_allowed_both_spellings(ws):
    for path in ("parts/upper_arm.py", str(ws.root / "parts" / "upper_arm.py")):
        _, is_error = ws.tools()[1].call({"path": path, "content": BOX})
        assert not is_error
    assert ws.own.read_text(encoding="utf-8") == BOX


def test_read_restricted_to_own_file_and_read_roots(ws):
    read = ws.tools()[0]
    assert read.call({"path": "sdk_docs/readme.md"})[1] is False
    assert read.call({"path": "secret.txt"})[1] is True
    assert read.call({"path": "sdk_docs/../secret.txt"})[1] is True


def test_compile_part_runs_in_worker(ws):
    ws.write_file({"path": "parts/upper_arm.py", "content": BOX})
    text, is_error = ws.tools()[2].call({})
    out = json.loads(text)
    assert not is_error and out["ok"] and out["result"]["volume"] == pytest.approx(6000.0)


def test_agent_sdk_options_disable_builtins(ws):
    opts = AgentSDKBackend().options("sys", ws.tools(), [], [])
    assert opts.tools == []
    assert opts.permission_mode == "dontAsk"
    assert opts.setting_sources == [] and opts.strict_mcp_config is True and opts.skills == []
    assert set(opts.allowed_tools) == {mcp_name(s) for s in ws.tools()}
    assert {"Bash", "Write", "Edit", "WebFetch"} <= set(opts.disallowed_tools)


def test_agent_sdk_guard_hook(ws):
    denied = []
    guard = make_guard({mcp_name(s) for s in ws.tools()}, denied)
    run = lambda name: asyncio.run(guard({"tool_name": name}, "id", None))
    assert run("mcp__agenticcad__write_file") == {}
    for name in ("Write", "Bash", "mcp__other__write_file"):
        assert run(name)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert denied == ["Write", "Bash", "mcp__other__write_file"]


def _api_reply(content, stop):
    return {"id": "msg", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def test_claude_api_backend_tool_loop_offline(ws):
    """Scripted Messages API: write own file, try another part's file, compile, finish."""
    script = [
        _api_reply([{"type": "tool_use", "id": "t1", "name": "write_file",
                     "input": {"path": "parts/upper_arm.py", "content": BOX}}], "tool_use"),
        _api_reply([{"type": "tool_use", "id": "t2", "name": "write_file",
                     "input": {"path": "parts/forearm.py", "content": "x"}}], "tool_use"),
        _api_reply([{"type": "tool_use", "id": "t3", "name": "compile_part", "input": {}}], "tool_use"),
        _api_reply([{"type": "text", "text": "done"}], "end_turn"),
    ]
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=script[len(sent) - 1])

    client = anthropic.Anthropic(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    res = ClaudeAPIBackend(client=client).run("sys", "build it", ws.tools())

    assert res.error is None and res.final_text == "done" and res.turns == 4
    assert [(c.name, c.is_error) for c in res.tool_calls] == [
        ("write_file", False), ("write_file", True), ("compile_part", False)]
    assert {t["name"] for t in sent[0]["tools"]} == {"read_file", "write_file", "compile_part"}
    # the denial reached the model as an error tool_result
    denial = [b for m in sent[2]["messages"] if m["role"] == "user" and isinstance(m["content"], list)
              for b in m["content"] if b.get("type") == "tool_result" and b.get("tool_use_id") == "t2"]
    assert denial and denial[0]["is_error"] is True
    assert not (ws.root / "parts" / "forearm.py").exists()
