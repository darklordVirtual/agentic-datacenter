"""The gate adapter interface (SDD 9.2).

The agent can read and propose; only a gate can authorize; only the
Supervisory Command Service can write. This file is the seam between the
second and the third: a small, vendor-neutral interface that any governance
system can implement. Nothing here knows which gate is behind it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from agentic_datacenter.core.state import JsonValue


class Verdict(StrEnum):
    """What a gate says about a proposal."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    VERIFY = "VERIFY"  # a human must approve
    ABSTAIN = "ABSTAIN"  # evidence is missing, stale or contradicted


@dataclass(frozen=True)
class AuthenticatedActor:
    """Who is proposing, as established by the credential on the conduit (INV-GOV-5).

    Attributes:
        principal: The identity bound to the credential.
        credential: How it was established, for the record.
    """

    principal: str
    credential: str


@dataclass(frozen=True)
class Proposal:
    """One contract instance submitted for a decision. It never names a raw tag write.

    Attributes:
        proposal_id: Assigned by the tool server.
        contract_id: The intent contract proposed.
        arguments: Its arguments.
        rationale: Free text from the agent. Stored; never evidence.
        claimed_actor: Who the agent says it is acting for. Stored as a claim only.
    """

    proposal_id: str
    contract_id: str
    arguments: Mapping[str, JsonValue]
    rationale: str = ""
    claimed_actor: str | None = None


@dataclass(frozen=True)
class PremiseResult:
    """One premise as the gate binding read it.

    ``holds`` is None when the premise could not be established: a point it
    needs is stale or of bad quality, or older than the premise allows.
    """

    expr: str
    holds: bool | None
    detail: str
    invariant: str = ""


@dataclass(frozen=True)
class BoundedWrite:
    """One concrete write a contract instance would make."""

    tag: str
    value: bool | int | float | str
    previous: bool | int | float | str


@dataclass(frozen=True)
class MandateChange:
    """One change to a principal's mandate, as the control path's register holds it.

    Attributes:
        time_ms: When the change was made, on the control path's clock.
        contracts: Every contract the principal may propose from then on, sorted.
            Empty: none, the mandate has ended.
        by: Who made the change, if the register was told.
        reason: Why, in the words it was given with.
        expires_at_ms: When what the change holds ends by itself, with no further
            change; None when it holds until the next change (ADR 0039).
        delegated: ``by`` passed on part of its own mandate. The principal then
            holds a contract only while ``by`` holds it too (ADR 0040).
    """

    time_ms: int
    contracts: tuple[str, ...]
    by: str = ""
    reason: str = ""
    expires_at_ms: int | None = None
    delegated: bool = False


@dataclass(frozen=True)
class Mandate:
    """The register's record of one principal's mandate: every change, oldest first.

    The last change is in force until it expires, if it does. It is what the
    premise ``authority(<principal>, <contract>)`` was established from, so a
    gate that works out for itself who may act has what the control path had.
    Nothing in it comes from the agent. It is a register's entries, in order,
    and nothing in it is signed; the signed form is ``MandateStatements``.
    """

    principal: str
    changes: tuple[MandateChange, ...]
    #: The record of everyone a change of this one was passed on from, and of
    #: those they had it from; empty when no change is a delegation (ADR 0040).
    delegators: tuple[Mandate, ...] = ()
    #: How the site reads a delegation: ``holdings`` or ``grant_bound`` (ADR 0048).
    model: str = "holdings"


@dataclass(frozen=True)
class MandateStatements:
    """What was presented as the proposer's mandate, each statement signed (ADR 0039).

    The register's changes as signed statements, those of everyone the mandate
    was passed on from, and anything else that was presented for the principal,
    which may be forged. Unlike ``Mandate`` it is
    not to be trusted as it stands: a statement counts only when it verifies
    under the key of an issuer in ``issuers``. The rule is in
    ``agentic_datacenter.governance.mandates`` and in
    ``docs/reference/mandate-statements.md``.

    Attributes:
        principal: Whose mandate it is.
        statements: The statements, by time of issue. Each is a JSON object.
        issuers: Every key of each issuer the site knows: the name, the public
            key, when it was valid, and whether the issuer may give a mandate
            of its own (``root``) or only pass one on. Established by the
            control path, never by the agent.
    """

    principal: str
    statements: tuple[Mapping[str, JsonValue], ...]
    issuers: tuple[Mapping[str, JsonValue], ...]
    #: When the site first saw each statement, by its ``id``, on the control
    #: path's clock. Kept by the site and not by the issuer, so a statement
    #: cannot be dated back past it (ADR 0040).
    first_seen: Mapping[str, int] = field(default_factory=dict)
    #: How the site reads a delegation: ``holdings`` or ``grant_bound`` (ADR 0048).
    model: str = "holdings"


@dataclass(frozen=True)
class PremiseSnapshot:
    """Everything the binding established about a proposal before the gate decides.

    The binding reads all of it from the supervisory layer itself. Nothing in
    it comes from the agent.

    Attributes:
        time_ms: Gate time at which it was read.
        contract_known: The contract exists in the library.
        risk_tier: ``R0`` to ``R3``.
        approval: ``none``, ``one_operator``, ``two_person`` or ``not_available``.
        writes: The concrete writes, in order.
        schema_errors: Why the arguments do not fit the contract, if they do not.
        bound_errors: Writes outside the contract's bounds or step.
        limit_errors: Rate or cumulative limits the instance would exceed.
        premises: Each premise that applies to this instance.
        rollback_available: The contract declares a rollback.
        restore: Every write moves its point back toward the value it had
            before the agent's standing writes, in the contract's safe
            direction, and no further. Rate and cumulative limits do not
            apply to such a step (INV-GOV-8).
        mandate: The record of the proposer's mandate, once one was set for it;
            None when nothing was said about its authority (ADR 0031), and for a
            gate that verifies mandates itself, which is given only ``statements``.
        statements: The same mandate as signed statements, with whatever else
            was presented for the proposer and the issuers the site trusts;
            None when no mandate was set (ADR 0039).
        context: A read-only sample of the plant, for a gate that asks for one
            (``wants_context``); None otherwise (ADR 0036).
    """

    time_ms: int
    contract_known: bool
    risk_tier: str = "R3"
    approval: str = "not_available"
    writes: tuple[BoundedWrite, ...] = ()
    schema_errors: tuple[str, ...] = ()
    bound_errors: tuple[str, ...] = ()
    limit_errors: tuple[str, ...] = ()
    premises: tuple[PremiseResult, ...] = ()
    rollback_available: bool = False
    restore: bool = False
    mandate: Mandate | None = None
    statements: MandateStatements | None = None
    context: Mapping[str, JsonValue] | None = None


@dataclass(frozen=True)
class Decision:
    """A gate's decision on one proposal."""

    verdict: Verdict
    decision_ref: str
    contract_id: str
    bounded_writes: tuple[BoundedWrite, ...] = ()
    expires_at_ms: int = 0
    single_use_nonce: str = ""
    reasons: tuple[str, ...] = ()
    required_approvers: int = 0
    escalated: bool = False
    detail: Mapping[str, JsonValue] = field(default_factory=dict)
    #: Why the gate did not allow, each one of :data:`REASON_CODES`; empty if the
    #: gate does not say. A step can be wrong in several ways at once, so there
    #: may be several. The reasons in words are kept as well.
    reason_codes: tuple[str, ...] = ()
    #: With ``authority`` among the reason codes, which of the two it was: one of
    #: :data:`AUTHORITY_DETAILS`; empty if the gate does not say.
    authority_detail: str = ""
    #: The gate's own finding of whether the proposer holds a mandate for this
    #: contract; None if it does not say. Whatever its verdict: a gate may find
    #: that a mandate holds and refuse for something else. Where a mandate was
    #: set for the proposer it is compared with the premise (ADR 0031).
    authority_holds: bool | None = None


#: What a refusal for want of a mandate can say more (ADR 0031). The evidence
#: differs: for ``ended`` it is the change that ended the mandate, when it was
#: made and by whom; for ``never_held`` there is no such change.
#:
#: - ``never_held``: the proposer never held a mandate for this contract.
#: - ``ended``: it held one, and that has ended or been narrowed.
AUTHORITY_DETAILS = ("never_held", "ended", "unbound")


#: Why a gate did not allow a proposal, in a word. A gate may give one or more
#: with its decision; a word outside this list is recorded as ``other``.
#:
#: - ``authority``: the proposer may not take this step.
#: - ``limit``: outside the contract's bounds, step, rate or cumulative limit.
#: - ``premise``: a condition of the plant that the contract requires does not hold.
#: - ``evidence``: something the decision needs is missing, stale or contradicted.
#: - ``approval``: a person must approve.
#: - ``malformed``: an unknown contract, or arguments that do not fit it.
#: - ``policy``: a rule of the gate's own.
#: - ``other``: none of these.
REASON_CODES = (
    "authority", "limit", "premise", "evidence", "approval", "malformed", "policy", "other",
)  # fmt: skip


@dataclass(frozen=True)
class Outcome:
    """What became of an authorized proposal: applied, rejected, rolled back or unknown."""

    status: str
    detail: str = ""


OUTCOMES = ("APPLIED", "REJECTED", "ROLLED_BACK", "UNKNOWN", "NOT_EXECUTED")


class GateRefusal(Exception):  # noqa: N818 - a refusal is an answer, not an error
    """A gate did not take an approval it was handed, or holds no such authorization."""


@runtime_checkable
class GovernanceGate(Protocol):
    """What a governance system implements to be driven by Agentic-Datacenter."""

    name: str

    def evaluate(
        self, proposal: Proposal, premises: PremiseSnapshot, actor: AuthenticatedActor
    ) -> Decision:
        """Decide on *proposal*, given what the binding established."""
        ...

    def record_outcome(self, decision_ref: str, outcome: Outcome) -> None:
        """Learn what became of a decision."""
        ...


# A gate that authorizes execution itself, instead of leaving it to the control
# path, also has these. They are looked up by name, so a gate without them is
# still a gate:
#
#   enforces_limits: bool
#       True when rate and cumulative limits are the gate's to enforce.
#   derives_authority: bool
#       True for a gate that works out for itself who may act. It is given the
#       mandate record and not the premise ``authority(<principal>, <contract>)``.
#       The record of the proposal still carries that premise, and the run is
#       judged by it (ADR 0031).
#   verifies_mandates: bool
#       True for a gate that authenticates the mandate itself. It is given the
#       signed statements and the trusted issuers, and neither the premise nor
#       the register's record. Judged by the premise all the same (ADR 0039).
#   approve(decision_ref, approver, now_ms) -> None
#       One person's approval. Raises GateRefusal when it is not taken.
#   reject(decision_ref, approver, reason, now_ms) -> None
#   execute(decision_ref, now_ms, apply, read) -> Outcome
#       Consume the authorization and call ``apply`` at most once to write.
#       ``apply`` returns (status, detail); ``read`` returns a point's value.
#   resolve_unknown(decision_ref, executed, now_ms) -> str
#       Learn what an independent read found for an unanswered write.
