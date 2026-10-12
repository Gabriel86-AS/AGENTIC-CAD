"""T2.5: offscreen renderer, render_part tool, image tool results in both backends, and the L3v look loop
driven by scripted part agents and a scripted judge (no LLM)."""
import asyncio
import json
from pathlib import Path

import anthropic
import httpx2
import numpy as np
import pytest
import trimesh
from PIL import Image

from agents import look, stages
from agents.trace import Trace
from harness import tools as harness_tools
from llm.agent_sdk_backend import AgentSDKBackend, sdk_tool
from llm.backend import LLMBackend, RunResult, ToolCall
from llm.claude_api_backend import ClaudeAPIBackend
from llm.tools import PartWorkspace, ToolSpec, WithImages
from schemas.plan import AssemblyPlan
from validate.render import VIEWS, _camera, render_views

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "bench" / "golden" / "b1_3"
PLAN = AssemblyPlan.model_validate(json.loads((GOLDEN / "plan.json").read_text(encoding="utf-8")))
ARM = (GOLDEN / "parts" / "arm.py").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def no_flex(monkeypatch):
    monkeypatch.setattr(harness_tools, "FLEX", False)


def _png(path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB")).astype(int)


# ---- renderer ----------------------------------------------------------------------------------
def test_render_views_box_with_frame(tmp_path):
    box = trimesh.creation.box((120, 20, 10))
    out = render_views([{"mesh": box}], [("tool", (60, 0, 0), (1, 0, 0), (0, 0, 1))], tmp_path, "bar", title="bar")
    assert list(out) == list(VIEWS)
    for v, p in out.items():
        img = _png(p)
        assert img.shape == (768, 768, 3), v
        assert (img.sum(axis=2) < 740).mean() > 0.02, v           # the part covers some of the frame
        red = (img[..., 0] > 180) & (img[..., 1] < 90) & (img[..., 2] < 90)
        blue = (img[..., 2] > 180) & (img[..., 0] < 90) & (img[..., 1] < 160)
        assert red.any() or blue.any(), v                          # connector axes drawn
        border = np.concatenate([img[:, :3].reshape(-1, 3), img[:, -3:].reshape(-1, 3), img[-3:].reshape(-1, 3)])
        assert (border > 245).all(), v                             # framed: nothing touches the edges


def test_camera_frames_long_parts():
    pts = np.array([[x, y, z] for x in (-150, 150) for y in (-5, 5) for z in (0, 10)], float)
    for v, (d, up) in VIEWS.items():
        focal, pos, upv, scale = _camera(pts, d, up, diag=300.0)
        right = np.cross(focal - pos, upv)
        right /= np.linalg.norm(right)
        assert np.abs((pts - focal) @ right).max() <= scale + 1e-6, v
        assert np.abs((pts - focal) @ upv).max() <= scale + 1e-6, v


# ---- tool results with images ------------------------------------------------------------------
def test_toolspec_with_images(tmp_path):
    png = tmp_path / "a.png"
    Image.new("RGB", (4, 4), "white").save(png)
    spec = ToolSpec("r", "", {"type": "object"}, lambda a: WithImages({"ok": True}, [png]))
    text, err, imgs = spec.invoke({})
    assert json.loads(text) == {"ok": True} and not err and imgs == [png]
    assert spec.call({}) == (text, False)


@pytest.fixture
def ws(tmp_path):
    w = PartWorkspace(tmp_path, "arm", plan=PLAN)
    w.write_file({"path": "parts/arm.py", "content": ARM})
    return w


def test_render_part_tool(ws):
    text, err = ws.tools()[4].call({})
    assert err and "compile_part first" in text
    assert ws.compile_part({})["ok"]
    text, err, imgs = ws.tools()[4].invoke({"views": ["iso", "top"]})
    assert not err and json.loads(text)["views"] == ["iso", "top"] and [p.name for p in imgs] == ["arm_iso.png", "arm_top.png"]
    _, err, _ = ws.tools()[4].invoke({"views": ["side"]})
    assert err
    for _ in range(2):
        ws.render_part({"views": ["front"]})
    text, err = ws.tools()[4].call({})
    assert err and "render budget" in text


def _api_reply(content, stop):
    return {"id": "msg", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5", "content": content,
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5}}


def test_claude_api_backend_sends_images_offline(ws):
    """Prompt images go into the first user message; render_part images into its tool_result."""
    ws.compile_part({})
    script = [_api_reply([{"type": "tool_use", "id": "t1", "name": "render_part", "input": {"views": ["iso"]}}], "tool_use"),
              _api_reply([{"type": "text", "text": "looks right"}], "end_turn")]
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=script[len(sent) - 1])

    client = anthropic.Anthropic(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    png = ws.root / "renders" / "x.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), "white").save(png)
    res = ClaudeAPIBackend(client=client).run("sys", "look", ws.tools(), images=[png])
    assert res.error is None and res.final_text == "looks right"
    first = sent[0]["messages"][0]["content"]
    assert [b["type"] for b in first] == ["text", "image"] and first[1]["source"]["media_type"] == "image/png"
    result = [b for m in sent[1]["messages"] if m["role"] == "user" and isinstance(m["content"], list)
              for b in m["content"] if b.get("type") == "tool_result"][0]
    assert [b["type"] for b in result["content"]] == ["text", "image"]
    assert res.tool_calls[0].images and res.tool_calls[0].images[0].endswith("arm_iso.png")


def test_agent_sdk_image_prompt_and_tool_content(tmp_path):
    png = tmp_path / "a.png"
    Image.new("RGB", (4, 4), "white").save(png)

    async def first():
        return [m async for m in AgentSDKBackend._with_images("judge", [png])]
    msgs = asyncio.run(first())
    content = msgs[0]["message"]["content"]
    assert len(msgs) == 1 and [b["type"] for b in content] == ["text", "image"]
    calls = []
    spec = ToolSpec("render_part", "r", {"type": "object", "properties": {}}, lambda a: WithImages({"ok": True}, [png]))
    out = asyncio.run(sdk_tool(spec, calls).handler({}))
    assert [c["type"] for c in out["content"]] == ["text", "image"] and out["content"][1]["mimeType"] == "image/png"
    assert calls[0].images == [str(png)] and out["is_error"] is False


# ---- L3v loop ----------------------------------------------------------------------------------
class Scripted(LLMBackend):
    name = "scripted"

    def __init__(self, role, script, seen):
        super().__init__(model="scripted-" + role)
        self.script, self.seen, self.role = script, seen, role

    def run(self, system, prompt, tools, images=None):
        self.seen.append((self.role, prompt, list(images or [])))
        by = {t.name: t for t in tools}
        calls = []

        def call(name, args):
            text, err = by[name].call(args)
            calls.append(ToolCall(name, args, text, err))
            return json.loads(text)

        return RunResult(self.name, self.model, self.script(prompt, call) or "", calls)


MAJOR = {"matches_intent": False, "issues": [{"severity": "major", "where": "beam", "what": "the beam is missing"}]}
PASS = {"matches_intent": True, "issues": [{"severity": "minor", "where": "fork", "what": "sharp corners"}]}


def backends(verdicts, part_sources, seen):
    def part(prompt, call):
        src = part_sources.pop(0) if part_sources else ARM
        call("write_file", {"path": "parts/arm.py", "content": src})
        call("compile_part", {})
        return "done"

    def judge(prompt, call):
        v = verdicts.pop(0)
        if v == "bad":   # inconsistent verdict is rejected; the judge then gives up
            assert not call("submit_verdict", {"matches_intent": True, "issues": MAJOR["issues"]})["ok"]
            return "?"
        assert call("submit_verdict", v)["ok"]
        return "verdict"

    roles = {"part": part, "part_escalated": part, "look": judge}
    return lambda role: Scripted(role, roles[role], seen)


def _run(tmp_path, verdicts, sources, seen):
    return stages.part_agent(PLAN, "arm", tmp_path, backends(verdicts, sources, seen), Trace(None), escalate=False)


def test_look_repair_round_then_pass(tmp_path):
    seen = []
    out = _run(tmp_path, [MAJOR, PASS], [], seen)
    assert out.ok and out.sessions == 2 and out.look["status"] == "pass" and out.look["rounds"] == 2
    roles = [r for r, _, _ in seen]
    assert roles == ["part", "look", "part", "look"]
    assert len(seen[1][2]) == 4 and all(p.exists() for p in seen[1][2])        # 4 views to the judge
    assert "the beam is missing" in seen[2][1] and "L3v" in seen[2][1]         # issues reach the part agent
    assert "horn_seat" in seen[1][1] and "Intent" in seen[1][1]                # contract + built connectors
    hist = json.loads((tmp_path / "look" / "arm.json").read_text())
    assert [r["matches_intent"] for r in hist["rounds"]] == [False, True]
    # unchanged program after a pass: not judged again
    seen2 = []
    again = _run(tmp_path, [], [], seen2)
    assert again.look == {"status": "pass", "rounds": 0, "cached": True} and [r for r, _, _ in seen2] == ["part"]


def test_look_persistent_disagreement_is_a_warning(tmp_path):
    seen = []
    out = _run(tmp_path, [MAJOR, MAJOR], [], seen)
    assert out.ok and out.look["status"] == "warning" and out.look["rounds"] == 2 and out.sessions == 2


def test_look_repair_that_breaks_compile_is_rolled_back(tmp_path):
    seen = []
    broken = ARM.replace("def build(P):", "def build(P):\n    raise ValueError('oops')", 1)
    out = _run(tmp_path, [MAJOR], [ARM, broken], seen)
    assert out.ok and out.look["status"] == "warning" and "kept the passing program" in out.look["reason"]
    assert (tmp_path / "parts" / "arm.py").read_text(encoding="utf-8") == ARM
    assert (tmp_path / "build" / "arm.FCStd").exists()


def test_look_judge_without_verdict_does_not_block(tmp_path):
    out = _run(tmp_path, ["bad"], [], [])
    assert out.ok and out.look["status"] == "skipped"


def test_look_feedback_levels():
    from schemas.review import LookVerdict
    fb = look.feedback(LookVerdict.model_validate({"matches_intent": False, "issues": MAJOR["issues"] + PASS["issues"]}))
    assert [f["level"] for f in fb] == ["failure", "warning"] and all(f["gate"] == "L3v" for f in fb)
