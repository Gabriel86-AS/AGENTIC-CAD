"""Vision gate verdicts (P2 §3.4): L3v part look check. The L8 assembly review joins it in T2.6."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LookIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Literal["major", "minor"]   # major: the part is wrong for its intent; minor: cosmetic or unsure
    where: str = Field(min_length=1)      # feature / face / connector, in the part's own words
    what: str = Field(min_length=1)


class LookVerdict(BaseModel):
    """L3v: does the rendered part match its intent? matches_intent is false iff some issue is major."""
    model_config = ConfigDict(extra="forbid")
    matches_intent: bool
    issues: list[LookIssue] = Field(default_factory=list)
