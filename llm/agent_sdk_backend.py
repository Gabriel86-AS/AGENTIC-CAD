"""AgentSDKBackend: Claude Agent SDK with every built-in tool removed (plan §2.3, §3.4).

Restriction layers (S11):
  1. tools=[]                      no built-in tools at all (Bash, Read, Write, Web*, Agent, ...)
  2. in-process MCP server         our ToolSpecs only, exposed as mcp__agenticcad__<name>
  3. permission_mode="dontAsk" +   anything not pre-approved is denied, never prompted
     allowed_tools = our tools
  4. PreToolUse hook               denies any tool outside our server (defense in depth) and records it
  5. setting_sources=[], strict_mcp_config=True, skills=[]
                                   no user/project settings, CLAUDE.md, hooks, MCP servers or skills leak in
  6. the workspace policy itself   write_file accepts only parts/<own>.py (llm/tools.py)
"""
import asyncio
import os
import tempfile

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from llm.backend import LLMBackend, RunResult, ToolCall
from llm.tools import ToolSpec, image_b64

SERVER = "agenticcad"
# Belt and braces: names that must never be callable even if a preset re-enabled them
BUILTINS = ["Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep", "WebFetch",
            "WebSearch", "Task", "Agent", "TodoWrite", "Skill", "SlashCommand", "KillShell", "BashOutput"]


def mcp_name(spec: ToolSpec) -> str:
    return f"mcp__{SERVER}__{spec.name}"


def make_guard(allowed: set[str], denied: list[str]):
    """PreToolUse hook: deny anything that is not one of our tools."""

    async def guard(input_data, tool_use_id, context):
        name = input_data.get("tool_name", "")
        if name in allowed:
            return {}
        denied.append(name)
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"{name} is not available in this workspace",
        }}

    return guard


def sdk_tool(spec: ToolSpec, calls: list[ToolCall]):
    """One ToolSpec as an in-process MCP tool; images (render_part) become MCP image content after the text."""
    @tool(spec.name, spec.description, spec.input_schema)
    async def handler(args):
        text, is_error, images = await asyncio.to_thread(spec.invoke, args)
        calls.append(ToolCall(spec.name, dict(args), text, is_error, [str(p) for p in images]))
        return {"content": [{"type": "text", "text": text}] + [
            {"type": "image", "data": image_b64(p), "mimeType": "image/png"} for p in images],
            "is_error": is_error}

    return handler


class AgentSDKBackend(LLMBackend):
    name = "agent_sdk"

    def __init__(self, model=None, max_turns=12, cwd=None, max_budget_usd: float | None = 1.0):
        super().__init__(**({"model": model} if model else {}), max_turns=max_turns)
        self.cwd = cwd
        self.max_budget_usd = max_budget_usd

    def options(self, system, tools: list[ToolSpec], calls: list[ToolCall], denied: list[str]):
        server = create_sdk_mcp_server(SERVER, tools=[sdk_tool(s, calls) for s in tools])
        allowed = {mcp_name(s) for s in tools}
        return ClaudeAgentOptions(
            system_prompt=system,
            model=self.model,
            max_turns=self.max_turns,
            max_budget_usd=self.max_budget_usd,
            tools=[],
            mcp_servers={SERVER: server},
            strict_mcp_config=True,
            allowed_tools=sorted(allowed),
            disallowed_tools=BUILTINS,
            permission_mode="dontAsk",
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[make_guard(allowed, denied)])]},
            setting_sources=[],
            skills=[],
            cwd=self.cwd,
        )

    @staticmethod
    async def _with_images(prompt: str, images):
        """Streaming-input prompt: one user message, text then the images (the string form takes no images)."""
        yield {"type": "user", "parent_tool_use_id": None, "session_id": "default", "message": {"role": "user", "content": [
            {"type": "text", "text": prompt}] + [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                              "data": image_b64(p)}} for p in images]}}

    async def _run(self, system: str, prompt: str, tools: list[ToolSpec], images=None) -> RunResult:
        calls: list[ToolCall] = []
        denied: list[str] = []
        res = RunResult(backend=self.name, model=self.model, final_text="", tool_calls=calls, denied_tools=denied)
        texts = []
        ours = {mcp_name(s) for s in tools}
        # System prompt goes through a file: inline it hits the Windows 32k command-line limit (WinError 206)
        fd, path = tempfile.mkstemp(prefix="agenticcad_sys_", suffix=".md")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(system)
        try:
            async for msg in query(prompt=self._with_images(prompt, images) if images else prompt, options=self.options({"type": "file", "path": path}, tools, calls, denied)):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            texts.append(block.text)
                        elif isinstance(block, ToolUseBlock) and block.name not in ours and block.name not in denied:
                            denied.append(block.name)  # attempted a tool that is not ours (hook may also record it)
                elif isinstance(msg, ResultMessage):
                    res.turns = msg.num_turns
                    res.usage = {"usage": msg.usage, "total_cost_usd": msg.total_cost_usd}
                    if msg.is_error:
                        res.error = msg.subtype
        finally:
            os.unlink(path)
        res.final_text = texts[-1] if texts else ""
        return res

    def run(self, system: str, prompt: str, tools: list[ToolSpec], images=None) -> RunResult:
        return asyncio.run(self._run(system, prompt, tools, images))
