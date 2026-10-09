"""ClaudeAPIBackend: Messages API + the anthropic tool runner (plan §2.3; the product default later).

Only our ToolSpecs are sent as tools, so there is nothing else to disable; the workspace policy
(llm/tools.py) enforces the write restriction exactly as for the Agent SDK backend.
Needs ANTHROPIC_API_KEY (or an injected client).
"""
import anthropic
from anthropic.lib.tools import BetaFunctionTool, ToolError

from llm.backend import LLMBackend, RunResult, ToolCall
from llm.tools import ToolSpec


class ClaudeAPIBackend(LLMBackend):
    name = "claude_api"

    def __init__(self, model=None, max_turns=12, client: anthropic.Anthropic | None = None, max_tokens: int = 4096):
        super().__init__(**({"model": model} if model else {}), max_turns=max_turns)
        self.client = client
        self.max_tokens = max_tokens

    @staticmethod
    def adapt(spec: ToolSpec, calls: list[ToolCall]) -> BetaFunctionTool:
        def fn(**kwargs):
            text, is_error = spec.call(kwargs)
            calls.append(ToolCall(spec.name, kwargs, text, is_error))
            if is_error:
                raise ToolError(text)  # runner returns it with is_error: true
            return text

        return BetaFunctionTool(fn, name=spec.name, description=spec.description, input_schema=spec.input_schema)

    def run(self, system: str, prompt: str, tools: list[ToolSpec]) -> RunResult:
        client = self.client or anthropic.Anthropic()
        calls: list[ToolCall] = []
        res = RunResult(backend=self.name, model=self.model, final_text="", tool_calls=calls)
        runner = client.beta.messages.tool_runner(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            tools=[self.adapt(s, calls) for s in tools],
            max_iterations=self.max_turns,
        )
        usage = {"input_tokens": 0, "output_tokens": 0}
        try:
            for message in runner:
                res.turns += 1
                usage["input_tokens"] += message.usage.input_tokens
                usage["output_tokens"] += message.usage.output_tokens
                texts = [b.text for b in message.content if b.type == "text"]
                if texts:
                    res.final_text = texts[-1]
        except anthropic.APIError as e:
            res.error = f"{type(e).__name__}: {e}"
        res.usage = usage
        return res
