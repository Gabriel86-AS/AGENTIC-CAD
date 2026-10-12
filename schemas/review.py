"""Vision gate verdicts (P2 §3.4): L3v part look check, L8 assembly review."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Literal["major", "minor"]   # major: the design is wrong for the request; minor: polish or unsure
    where: str = Field(min_length=1)      # owner, joint or pair as named in the bbox table / plan
    what: str = Field(min_length=1)
    route: Literal["code", "design"]      # code: one part's shape, within its connector contract; design: the layout
    parts: list[str] = Field(default_factory=list)   # plan parts to regenerate (route code)


class Scores(BaseModel):
    model_config = ConfigDict(extra="forbid")
    geometry: int = Field(ge=1, le=5)     # overall form and proportions read as the requested device
    detail: int = Field(ge=1, le=5)       # features, seating of purchased parts, finish
    motion: int = Field(ge=1, le=5)       # each joint moves what it should, about a sensible axis, usefully
    reasons: dict[Literal["geometry", "detail", "motion"], str] = Field(default_factory=dict)


class ReviewVerdict(BaseModel):
    """L8: the assembled design against the request. route_hint is "pass" iff no issue is major; "design" if any
    major issue is a design one (a revised layout regenerates parts anyway), else "code"."""
    model_config = ConfigDict(extra="forbid")
    scores: Scores
    issues: list[ReviewIssue] = Field(default_factory=list)
    route_hint: Literal["pass", "code", "design"]

    @model_validator(mode="after")
    def _consistent(self):
        major = [i for i in self.issues if i.severity == "major"]
        want = "pass" if not major else "design" if any(i.route == "design" for i in major) else "code"
        if self.route_hint != want:
            raise ValueError("route_hint must be %r for these issues (pass iff no major issue; design if any major "
                             "issue routes design; else code)" % want)
        for i in major:
            if i.route == "code" and not i.parts:
                raise ValueError("major code issue at %r names no part to regenerate (parts: [...])" % i.where)
        return self
