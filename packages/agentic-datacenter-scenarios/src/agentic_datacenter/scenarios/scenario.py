"""Scenario files: a timeline of injected events and machine-checkable pass criteria (SDD 10)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentic_datacenter.core.state import state_hash
from agentic_datacenter.governance.interop import CONTEXT_POINTS

SCHEMA_VERSION = 1
LIBRARY = Path(__file__).parent / "library"


class ScenarioError(ValueError):
    """A scenario file is missing, unreadable or invalid."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuthorityChange(_Model):
    """A change to the mandate of a principal: the contracts it may propose from then on.

    ``contracts`` is the whole mandate after the change: ``all`` for every
    contract, a list for exactly those, an empty list to end it. ``reason`` is
    shown with the alarms, for example who lost access or who took over.
    ``by`` is who made the change. Both are kept in the mandate record a gate
    is given with each proposal (ADR 0031).
    """

    contracts: list[str] | Literal["all"]
    principal: str = "agent-1"
    reason: str = ""
    by: str = ""
    #: What the change holds ends by itself this long after it, with no further
    #: change; None when it holds until the next one (ADR 0039).
    expires_after_s: Annotated[int, Field(gt=0)] | None = None
    #: ``by`` passes on part of its own mandate: the principal holds a contract
    #: only while ``by`` holds it too (ADR 0040).
    delegated: bool = False


#: Event kinds added after scenarios were first hashed; absent, they are not hashed.
_LATER_EVENT_KINDS = ("work_order", "presented", "issuer", "spoofed_reading")


class Presented(_Model):
    """A statement presented as a principal's mandate that must not count (ADR 0039).

    It is put among the signed statements a gate is sent and changes nothing in
    the register, so nothing in what a run is judged by. ``forgery`` is how it
    fails: signed by someone the site does not trust (``unknown_issuer``), in a
    trusted issuer's name with another key (``wrong_key``), a real statement
    changed after it was signed (``altered``), signed now with a key its issuer
    has rotated out (``retired_key``), signed now by an issuer whose standing
    has ended (``former_issuer``), or signed with a retired key and dated
    before it was retired (``backdated``, ADR 0040). ``contracts`` is what it
    claims.
    """

    forgery: Literal[
        "unknown_issuer", "wrong_key", "altered", "retired_key", "former_issuer", "backdated"
    ]
    contracts: list[str] | Literal["all"] = "all"
    principal: str = "agent-1"


class WorkOrder(_Model):
    """A work order an operator hands the agent: the agent proposes it as given (ADR 0038).

    It makes the same step reach the gate whatever the agent, so a scenario can
    test what becomes of one step, for example one that needs a person and
    whose mandate ends while it waits.
    """

    contract: str
    arguments: dict[str, bool | int | float | str] = Field(default_factory=dict)
    rationale: str = ""


class IssuerChange(_Model):
    """Something happens to an issuer of mandates (ADR 0040).

    ``rotate_key`` retires the key it signs with and starts its next one.
    ``end_standing`` retires the key and starts none: the issuer may give no
    more mandates. What it signed before stays as it was in both cases.
    """

    name: str
    change: Literal["rotate_key", "end_standing"]


class Event(_Model):
    """One injected event.

    Exactly one of ``inject`` (a fault or scenario input of a module),
    ``link`` (the supervisory network up or down), ``note`` (free text shown
    with the alarms), ``attempt`` (a connection or request tried across the
    zone model), ``replay`` (a used decision reference submitted again) or
    ``operator_write`` (a person at a workstation writes a tag through the
    command service), ``command_timeout`` (the next agent write goes
    unanswered, landed or not), ``agent_clock`` (the agent's own clock runs
    ``skew_s`` ahead), ``relay_request`` (an MMS request to a protection
    relay, put to the conduit and to the relay itself), ``authority`` (the
    mandate of a principal ends, passes on or is narrowed), ``work_order``
    (an operator hands the agent a step to propose), ``presented`` (a forged
    mandate statement is put before the gate), ``issuer`` (an issuer of
    mandates rotates its key or loses its standing) or ``spoofed_reading`` (a
    false value is put in place of a reading a gate is sent) is given.
    """

    at_s: Annotated[int, Field(ge=0)]
    module: str | None = None
    inject: str | None = None
    params: dict[str, bool | int | float | str] = Field(default_factory=dict)
    link: Literal["up", "down"] | None = None
    note: str | None = None
    attempt: dict[str, str] | None = None
    replay: bool | None = None
    operator_write: dict[str, bool | int | float | str] | None = None
    command_timeout: dict[str, bool] | None = None
    agent_clock: dict[str, int] | None = None
    relay_request: dict[str, str] | None = None
    authority: AuthorityChange | None = None
    work_order: WorkOrder | None = None
    presented: Presented | None = None
    issuer: IssuerChange | None = None
    #: ``{tag, value}``: someone on the network puts a false value in place of
    #: a reading, in the context a gate asks for. The plant is untouched (ADR 0041).
    spoofed_reading: dict[str, bool | int | float | str] | None = None


class Criterion(_Model):
    """One pass criterion: a metric and the most, or the least, it may be."""

    metric: str
    max: float | None = None
    min: float | None = None


class Scenario(_Model):
    """One scripted scenario.

    ``planned_work`` lists the contracts for which a work permit exists during
    the scenario. The scripted operator approves nothing else that needs a
    person.
    """

    schema_version: Literal[1]
    #: ``OP`` operational and ``AD`` adversarial (SDD 10.1, 10.2); ``WF`` a what-if
    #: scenario, kept in the what-if package and out of this library (SDD 10.4).
    id: Annotated[str, Field(pattern=r"^((OP|AD|WF|GR|WK)-\d{2}|WK-\d{4}W\d{2}-\d)$")]
    title: str
    track: Literal["A", "B", "C", "D"]
    duration_s: Annotated[int, Field(gt=0)]
    decision_every_s: Annotated[int, Field(gt=0)] = 300
    warmup_s: Annotated[int, Field(ge=0)] = 600
    objective: Literal["resilience", "energy"] = "resilience"
    timeline: tuple[Event, ...] = ()
    pass_criteria: tuple[Criterion, ...] = ()
    planned_work: tuple[str, ...] = ()
    #: How the site reads a delegation (ADR 0048): ``holdings``, the default, or
    #: ``grant_bound``. The register judges the run by it, and a gate is told it.
    authorization_model: Literal["holdings", "grant_bound"] = "holdings"
    expected: str = ""
    #: What the scenario needs of a bounded replay (ADR 0023): a branch at least
    #: this long, and at most this many proposals judged per run. A tier with
    #: unbounded replay ignores both.
    judge_horizon_s: Annotated[int, Field(gt=0)] | None = None
    judge_max: Annotated[int, Field(gt=0)] | None = None

    def judging(
        self, horizon_s: int | None, max_judged: int | None
    ) -> tuple[int | None, int | None]:
        """Return this scenario's replay bounds under a tier's *horizon_s* and *max_judged*."""
        if horizon_s is not None and self.judge_horizon_s is not None:
            horizon_s = max(horizon_s, self.judge_horizon_s)
        if max_judged is not None and self.judge_max is not None:
            max_judged = min(max_judged, self.judge_max)
        return horizon_s, max_judged

    def hash(self) -> str:
        """Return the hash that identifies this scenario in an evidence bundle.

        An event kind added later is left out of an event that does not use it,
        so every scenario written before it keeps its hash (ADR 0038).
        """
        dumped = self.model_dump(mode="json")
        if dumped.get("authorization_model") == "holdings":
            dumped.pop("authorization_model")  # so every earlier scenario keeps its hash
        for event in dumped["timeline"]:
            for later in _LATER_EVENT_KINDS:
                if event.get(later) is None:
                    event.pop(later, None)
            change = event.get("authority")
            if change is not None and change.get("expires_after_s") is None:
                change.pop("expires_after_s", None)
            if change is not None and not change.get("delegated"):
                change.pop("delegated", None)
        return state_hash(dumped)


def parse_scenario(data: object) -> Scenario:
    """Validate scenario *data*."""
    if not isinstance(data, dict):
        raise ScenarioError("a scenario must be a mapping")
    try:
        scenario = Scenario.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'scenario'}: {e['msg']}" for e in exc.errors()
        )
        raise ScenarioError(f"invalid scenario: {problems}") from exc
    for criterion in scenario.pass_criteria:
        if (criterion.max is None) == (criterion.min is None):
            raise ScenarioError(
                f"{scenario.id}: criterion {criterion.metric} needs exactly one of max or min"
            )
    for event in scenario.timeline:
        kinds = sum(
            x is not None
            for x in (
                *(event.inject, event.link, event.note, event.attempt),
                event.replay,
                event.operator_write,
                event.command_timeout,
                event.agent_clock,
                event.relay_request,
                event.authority,
                event.work_order,
                event.presented,
                event.issuer,
                event.spoofed_reading,
            )
        )
        if kinds != 1 or (event.inject is not None) != (event.module is not None):
            raise ScenarioError(
                f"{scenario.id}: event at {event.at_s} s needs exactly one of inject (with "
                "module), link, note, attempt, replay, operator_write, command_timeout, "
                "agent_clock, relay_request, authority, work_order, presented, issuer or "
                "spoofed_reading"
            )
        if event.at_s >= scenario.duration_s:
            raise ScenarioError(f"{scenario.id}: event at {event.at_s} s is after the end")
        problem = _event_problem(scenario, event)
        if problem:
            raise ScenarioError(f"{scenario.id}: event at {event.at_s} s: {problem}")
        if event.presented is not None and not any(
            e.authority is not None
            and e.authority.principal == event.presented.principal
            and e.at_s <= event.at_s
            for e in scenario.timeline
        ):
            raise ScenarioError(
                f"{scenario.id}: a statement is presented at {event.at_s} s for a principal "
                "no mandate was set for"
            )
    return scenario


#: Who signs a forgery of the kind ``unknown_issuer`` (``governance.mandates.OUTSIDER``).
_OUTSIDER = "contractor-7"
#: The forgeries that need an issuer whose key was retired earlier in the scenario,
#: and which change of an issuer gives one.
_NEEDS_ISSUER_CHANGE = {
    "retired_key": "rotate_key", "backdated": "rotate_key", "former_issuer": "end_standing",
}  # fmt: skip


def _event_problem(scenario: Scenario, event: Event) -> str:
    """Say what is wrong with an event that the run would only find when it got there."""
    earlier = [e for e in scenario.timeline if e.at_s < event.at_s]
    if event.authority is not None and event.authority.by == _OUTSIDER:
        return f"{_OUTSIDER} signs the forgery of an unknown issuer and cannot give a mandate"
    if event.presented is not None:
        needed = _NEEDS_ISSUER_CHANGE.get(event.presented.forgery)
        if needed and not any(e.issuer is not None and e.issuer.change == needed for e in earlier):
            return f"a {event.presented.forgery} forgery needs an earlier issuer event, {needed}"
    if event.issuer is not None and event.issuer.change == "end_standing":
        if any(
            e.authority is not None and e.authority.by == event.issuer.name
            for e in scenario.timeline
            if e.at_s == event.at_s
        ):
            return f"{event.issuer.name} makes a change at the instant its standing ends"
    if event.spoofed_reading is not None:
        tag = str(event.spoofed_reading.get("tag", ""))
        if "value" not in event.spoofed_reading or not any(
            re.search(pattern, tag) for patterns in CONTEXT_POINTS.values() for pattern in patterns
        ):
            return f"{tag or 'no tag'} is not a reading of the context, with a value"
    return ""


def load_scenario(source: str | Path) -> Scenario:
    """Read a scenario by id from the library, or from a path."""
    path = Path(source)
    if not path.suffix:
        path = LIBRARY / f"{source}.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ScenarioError(f"cannot read scenario {path}: {exc}") from exc
    try:
        return parse_scenario(data)
    except ScenarioError as exc:
        raise ScenarioError(f"{path}: {exc}") from exc


def library_ids() -> list[str]:
    """Return the ids of every scenario in the library, sorted."""
    return sorted(p.stem for p in LIBRARY.glob("*.yaml"))
