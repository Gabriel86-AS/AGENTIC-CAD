"""LLMBackend: the one interface agents talk to (plan §2.3, design rule 4).

Two implementations: AgentSDKBackend (prototype, Claude Agent SDK) and ClaudeAPIBackend
(Messages API + tool runner). Both take the same ToolSpec list and return a RunResult.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from llm.tools import ToolSpec

DEFAULT_MODEL = "claude-sonnet-5-5"  # part agents (plan §2.3 model routing)


@dataclass
class ToolCall:
    name: str
    input: dict
    output: str
    is_error: bool


@dataclass
class RunResult:
    backend: str
    model: str
    final_text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)  # calls blocked before reaching a tool
    turns: int = 0
    usage: dict = field(default_factory=dict)
    error: str | None = None


class LLMBackend(ABC):
    name: str

    def __init__(self, model: str = DEFAULT_MODEL, max_turns: int = 12):
        self.model = model
        self.max_turns = max_turns

    @abstractmethod
    def run(self, system: str, prompt: str, tools: list[ToolSpec]) -> RunResult:
        """Run one agent session with only `tools` available; return the trace."""
