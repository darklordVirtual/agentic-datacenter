"""The control path from proposal to write (SDD 9.1).

1. The agent proposes a contract instance. It never names a raw tag write.
2. The binding reads the contract's premises itself from the supervisory layer.
3. The gate decides: allow, deny, verify (a human must approve) or abstain.
4. For allow, or verify after approval, the writes go to the Supervisory
   Command Service, each with a single-use decision reference. The service
   checks tier, range and rate again.
5. The outcome is recorded with the decision and given back to the gate.

What happens after the decision depends on the operating profile (SDD 9.6).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace

from agentic_datacenter.core.scheduler import Kernel
from agentic_datacenter.core.state import JsonValue
from agentic_datacenter.governance import mandates
from agentic_datacenter.governance.bindings.null_gate import NullGate
from agentic_datacenter.governance.contracts import ContractLibrary, fill
from agentic_datacenter.governance.envelope_ref import DECISION_TTL_MS, write_ref
from agentic_datacenter.governance.gate import (
    AUTHORITY_DETAILS,
    REASON_CODES,
    AuthenticatedActor,
    BoundedWrite,
    Decision,
    GateRefusal,
    GovernanceGate,
    Mandate,
    MandateChange,
    MandateStatements,
    Outcome,
    PremiseResult,
    Proposal,
    Verdict,
)
from agentic_datacenter.governance.interop import EPOCH, GATE_DETAIL_KEPT, plant_context
from agentic_datacenter.governance.mandates import HOLDINGS
from agentic_datacenter.governance.premises import (
    AppliedWrite,
    PremiseReader,
    build_snapshot,
    check_expr,
    split_hold,
)
from agentic_datacenter.supervisory.command_service import Actor, CommandService
from agentic_datacenter.supervisory.overview import Overview
from agentic_datacenter.supervisory.points import PointDatabase

#: Operating profile -> whether an allowed proposal is executed (SDD 9.6).
PROFILES = {
    "BASELINE_RULES": False,
    "UNGOVERNED": True,
    "STATIC_ALLOWLIST": True,
    "SHADOW": False,
    "GOVERNED": True,
    "DECISION_ONLY": True,
}
#: Profiles in which a contract's rollback is watched. Without a gate, and with
#: an allowlist that only checks ranges, nothing enforces the rest of a contract.
ROLLBACK_PROFILES = frozenset({"GOVERNED", "DECISION_ONLY", "SHADOW"})
#: What the record says when an approval came from someone without standing.
NO_STANDING = "holds no standing to approve this contract"
#: Keys of a record kept for evidence and left out of the state (ADR 0036, ADR 0038).
UNHASHED_RECORD_KEYS = ("gate_detail", "trace")


def _trace(record: dict[str, JsonValue], key: str, entry: dict[str, JsonValue]) -> None:
    """Add *entry* to the list *key* of a record's approval trace."""
    trace = record.setdefault("trace", {})
    assert isinstance(trace, dict)
    listed = trace.setdefault(key, [])
    assert isinstance(listed, list)
    listed.append(entry)


_ROLLBACK_ACTOR = Actor("rollback-monitor", "operator")
#: How long an unanswered write is left before the plant is read to see what became of it.
UNKNOWN_RESOLVE_MS = 30_000
#: How long a premise of an applied step may stay unestablished before the step is undone.
PREMISE_LOSS_HOLD_S = 10
#: How long after it was written a step counts as in progress: one decision
#: interval of most scenarios. A step whose proposer loses its mandate within
#: that time is undone, if undoing moves the plant toward safety (ADR 0041).
IN_PROGRESS_S = 300
#: How the outcome detail of a step undone for that reason begins.
MANDATE_ENDED_IN_PROGRESS = "mandate ended while the step was in progress"
#: The outcome detail of a proposal a person has not answered yet, and how the
#: detail of one a person rejected begins. Both say that a person was asked.
WAITING_FOR_APPROVAL = "waiting for approval"
REJECTED_BY = "rejected by "
_UNGATED = NullGate()


@dataclass
class _Watch:
    """An applied proposal whose rollback trigger and premises are being watched.

    *writes* holds, per point, the value to go back to: the value before the
    first of the agent's writes that are still standing on that point, not
    merely the one before the latest step. *premises* are the premises the
    step was allowed on, filled in, with their freshness limits.
    """

    proposal_id: str
    trigger: str
    since_ms: int
    writes: list[tuple[str, bool | int | float | str]]
    premises: list[tuple[str, float]] = field(default_factory=list)
    lost_since_ms: int = -1
    #: Why the step is being undone, once that is decided. The undo is then
    #: tried every second until every write is accepted.
    undo: str = ""
    #: The agent's steps still standing on these points, oldest first: the
    #: proposals an undo takes back. A step back toward safety is not one of them.
    chain: list[str] = field(default_factory=list)


class GovernedPath:
    """Runs proposals through a gate and, where the profile says so, to the plant."""

    def __init__(
        self,
        kernel: Kernel,
        points: PointDatabase,
        service: CommandService,
        library: ContractLibrary,
        gate: GovernanceGate,
        profile: str,
        key_sets: Mapping[str, tuple[str, ...]],
    ) -> None:
        """Create the path for one gate under one operating profile.

        Raises:
            ValueError: If the profile is unknown.
            ContractError: If the library does not fit the facility or uses an
                unsupported premise.
        """
        if profile not in PROFILES:
            raise ValueError(f"unknown operating profile {profile!r}")
        self._kernel, self._points, self._service = kernel, points, service
        self.library, self.gate, self.profile = library, gate, profile
        self._key_sets = {name: tuple(keys) for name, keys in key_sets.items()}
        self.notes: list[str] = []
        self._reader = PremiseReader(points, Overview(points, self.notes))
        library.check_against(points, self._key_sets)
        for contract_id in library.ids():
            contract = library.get(contract_id)
            assert contract is not None
            for premise in contract.premises:
                check_expr(premise.expr)
            if contract.rollback:
                check_expr(contract.rollback.trigger)
        #: Proposal ids whose decision is replaced in a counterfactual replay:
        #: ``execute`` (as if no gate stood in the way) or ``block``.
        self.forced: dict[str, str] = {}
        #: Every change to each principal's mandate, oldest first; the last is in
        #: force. None until a mandate is first set: then nothing is said about
        #: authority, and every principal may propose every contract, as before
        #: authority was modelled.
        self._authority: dict[str, list[MandateChange]] | None = None
        #: Statements presented for a principal that the register did not issue.
        self._presented: dict[str, list[dict[str, JsonValue]]] = {}
        #: When each of them was first seen, by its ``id``.
        self._presented_at: dict[str, int] = {}
        #: What happened to an issuer's key: ``("rotated", ms)`` or ``("ended", ms)``.
        self._issuer_events: dict[str, list[tuple[str, int]]] = {}
        #: How this site reads a delegation (ADR 0048). Set before the run starts.
        self.authorization_model = mandates.HOLDINGS
        #: Readings someone on the network has put a false value in place of, by tag.
        self._spoofed: dict[str, JsonValue] = {}
        self._records: list[dict[str, JsonValue]] = []
        self._history: list[AppliedWrite] = []
        self._watches: list[_Watch] = []
        #: Unanswered writes: proposal id, when, and what was meant to be written.
        self._unknown: list[tuple[str, int, list[BoundedWrite]]] = []
        self._gate_limits = bool(getattr(gate, "enforces_limits", False))
        #: A gate that works out for itself who may act is not told the answer.
        self._derives_authority = bool(getattr(gate, "derives_authority", False))
        #: A gate that authenticates the mandate itself is given signed statements alone.
        self._verifies_mandates = bool(getattr(gate, "verifies_mandates", False))
        #: How far ahead the agent's own clock runs (scenario AD-09). Reported, never used.
        self.agent_clock_skew_ms = 0
        kernel.state.register("governance.path", self, tracked=True)
        kernel.add_task("governance.rollback", self._watch_rollbacks, period_ms=1000)
        kernel.add_task("governance.unknown", self._resolve_unknowns, period_ms=1000)

    def now_ms(self) -> int:
        """Return the time on this path's clock."""
        return self._kernel.clock.now_ms

    def add_note(self, text: str) -> None:
        """Add free text that is shown with the alarms: a work order, an operator comment."""
        self.notes.append(text)
        self._kernel.state.touch("governance.path")

    # -- authority -----------------------------------------------------------------

    def set_authority(
        self,
        principal: str,
        contracts: Sequence[str] | None,
        by: str = "",
        reason: str = "",
        expires_at_ms: int | None = None,
        delegated: bool = False,
    ) -> None:
        """Set the mandate of *principal*: the contracts it may propose from now on.

        None is every contract of the library; an empty sequence ends the
        mandate. A principal a mandate was set for holds it only for what was
        set, and every proposal it makes carries the premise
        ``authority(<principal>, <contract>)`` beside the contract's own. It is
        established here, from this register, never from what the agent says.
        For a person, the mandate is what that person may approve: an approval
        from someone without it is not taken. A principal no mandate was ever
        set for is not restricted.

        With *expires_at_ms*, what is set ends by itself at that instant on this
        path's clock, with no further change (ADR 0039).

        With *delegated*, *by* passes on part of its own mandate: *principal*
        holds a contract only while *by* holds it too, so never more than *by*
        holds and never for longer (ADR 0040).

        The register keeps every change, with the time, *by* (who made it) and
        *reason*, and gives them to the gate with each proposal (:meth:`mandate`),
        also as signed statements (:meth:`statements`).

        Raises:
            ValueError: If a contract is not in the library, the expiry is not
                after now, *by* has lost its standing as an issuer, or a
                delegation is from someone no mandate was set for.
        """
        known = set(self.library.ids())
        wanted = known if contracts is None else set(contracts)
        if not wanted <= known:
            raise ValueError(f"no such contract: {', '.join(sorted(wanted - known))}")
        now = self._kernel.clock.now_ms
        if expires_at_ms is not None and expires_at_ms <= now:
            raise ValueError("a mandate cannot expire before it is given")
        issuer = by or mandates.REGISTER
        if mandates.generation_at(self._issuer_events.get(issuer, ()), now) is None:
            raise ValueError(f"{issuer} no longer has standing to give a mandate")
        if delegated and (self._authority is None or by not in self._authority or by == principal):
            raise ValueError(f"{by or 'nobody'} has no mandate to pass on to {principal}")
        if self._authority is None:
            self._authority = {}
        change = MandateChange(now, tuple(sorted(wanted)), by, reason, expires_at_ms, delegated)
        self._authority.setdefault(principal, []).append(change)
        self._kernel.state.touch("governance.path")

    def rotate_issuer_key(self, issuer: str) -> None:
        """Retire the key *issuer* signs with and start its next one (ADR 0040).

        What the old key signed before now still counts; nothing it signs from
        now on does.

        Raises:
            ValueError: If the issuer's standing has ended.
        """
        self._issuer_event(issuer, "rotated")

    def end_issuer_standing(self, issuer: str) -> None:
        """End *issuer*'s standing to give mandates: its key is retired and none follows.

        What it gave while it had standing stays until someone withdraws it
        (ADR 0040).

        Raises:
            ValueError: If the issuer's standing has already ended.
        """
        self._issuer_event(issuer, "ended")

    def _issuer_event(self, issuer: str, kind: str) -> None:
        now = self._kernel.clock.now_ms
        events = self._issuer_events.setdefault(issuer, [])
        if mandates.generation_at(events, now) is None:
            raise ValueError(f"the standing of {issuer} has already ended")
        made_now = self._authority is not None and any(
            (change.by or mandates.REGISTER) == issuer and change.time_ms == now
            for changes in self._authority.values()
            for change in changes
        )
        if kind == "ended" and made_now:
            # A statement issued at the instant its key is retired cannot count,
            # and the register would hold what no statement shows.
            raise ValueError(
                f"{issuer} made a change at this instant; its standing can end a moment later"
            )
        events.append((kind, now))
        self._kernel.state.touch("governance.path")

    def set_authorization_model(self, model: str) -> None:
        """Declare how this site reads a delegation: ``holdings`` or ``grant_bound`` (ADR 0048).

        Raises:
            ValueError: If *model* is not one of ``mandates.MODELS``.
        """
        if model not in mandates.MODELS:
            raise ValueError(f"an authorization model is one of {', '.join(mandates.MODELS)}")
        self.authorization_model = model
        self._kernel.state.touch("governance.path")

    def _under_grant(self, change: MandateChange) -> bool:
        """Whether a delegation is under the grant its issuer holds now (``grant_bound``).

        The grant is the issuer's change in force when the delegation was made.
        It must still be the issuer's last change, and list everything the
        delegation lists.
        """
        assert self._authority is not None
        given = self._authority.get(change.by, [])
        earlier = [c for c in given if c.time_ms <= change.time_ms]
        if not earlier or earlier[-1] is not given[-1]:
            return False
        return set(change.contracts) <= set(given[-1].contracts)

    def _unbound(self, principal: str, contract_id: str, depth: int) -> bool:
        """Whether a mandate fails only because a delegation is not under its grant."""
        assert self._authority is not None
        changes = self._authority.get(principal)
        if self.authorization_model != mandates.GRANT_BOUND or not changes or depth == 0:
            return False
        last = changes[-1]
        expired = last.expires_at_ms is not None and self._kernel.clock.now_ms >= last.expires_at_ms
        if contract_id not in last.contracts or expired or not last.delegated:
            return False
        if not self._under_grant(last):
            return True
        return self._unbound(last.by, contract_id, depth - 1)

    def authorized(self, principal: str, contract_id: str) -> bool | None:
        """Whether *principal* holds a mandate for *contract_id*; None if none was set for it."""
        if self._authority is None or principal not in self._authority:
            return None
        return self._holds(principal, contract_id, mandates.MAX_DEPTH)

    def _holds(self, principal: str, contract_id: str, depth: int) -> bool:
        assert self._authority is not None
        changes = self._authority.get(principal)
        if not changes or depth == 0:
            return False
        last = changes[-1]
        expired = last.expires_at_ms is not None and self._kernel.clock.now_ms >= last.expires_at_ms
        if contract_id not in last.contracts or expired:
            return False
        if not last.delegated:
            return True
        if self.authorization_model == mandates.GRANT_BOUND and not self._under_grant(last):
            return False
        # Nobody passes on more than they hold, or for longer than they hold it.
        return self._holds(last.by, contract_id, depth - 1)

    def _ever_held(self, principal: str, contract_id: str, depth: int) -> bool:
        assert self._authority is not None
        if depth == 0:
            return False
        return any(
            contract_id in change.contracts
            and (not change.delegated or self._ever_held(change.by, contract_id, depth - 1))
            for change in self._authority.get(principal, ())
        )

    def _lineage(self, principal: str) -> list[str]:
        """Return everyone *principal*'s mandate was passed on from, nearest first."""
        assert self._authority is not None
        seen: list[str] = []
        queue = [principal]
        while queue:
            for change in self._authority.get(queue.pop(0), ()):
                if change.delegated and change.by not in seen and change.by != principal:
                    seen.append(change.by)
                    queue.append(change.by)
        return seen

    def mandate(self, principal: str) -> Mandate | None:
        """Return the record of *principal*'s mandate; None if none was ever set for it."""
        if self._authority is None or principal not in self._authority:
            return None
        model = self.authorization_model
        return Mandate(
            principal,
            tuple(self._authority[principal]),
            tuple(
                Mandate(p, tuple(self._authority[p]), (), model) for p in self._lineage(principal)
            ),
            model,
        )

    def issuers(self) -> tuple[dict[str, JsonValue], ...]:
        """Return every key of each issuer the site knows, by name and then by age.

        An issuer is everyone who has made a change in the register. It is
        ``root`` when one of its changes was a mandate of its own to give, and
        not when it has only passed on what it held.
        """
        if self._authority is None:
            return ()
        root: dict[str, bool] = {}
        for changes in self._authority.values():
            for change in changes:
                name = change.by or mandates.REGISTER
                root[name] = root.get(name, False) or not change.delegated
        return tuple(
            record
            for name in sorted(root)
            for record in mandates.issuer_records(
                name, root[name], self._issuer_events.get(name, ())
            )
        )

    def statements(self, principal: str, presented: bool = True) -> MandateStatements | None:
        """Return *principal*'s mandate as signed statements; None if none was ever set.

        The register's changes, each signed by who made it with the key it had
        then, those of everyone the mandate was passed on from, and whatever
        else was presented for the principal (:meth:`present_statement`), by
        time of issue. Without *presented*, only what the register issued.
        """
        if self._authority is None or principal not in self._authority:
            return None

        def generation(issuer: str, time_ms: int) -> int:
            found = mandates.generation_at(self._issuer_events.get(issuer, ()), time_ms)
            assert found is not None  # set_authority refuses an issuer without standing
            return found

        both: list[Mapping[str, JsonValue]] = []
        # From the source of the mandate down to the principal.
        for who in [*reversed(self._lineage(principal)), principal]:
            both += mandates.issue(who, self._authority[who], generation)
        # The register sees its own statements as they are issued.
        first_seen = {str(s["id"]): int(str(s["issued_at_ms"])) for s in both}
        for shown in self._presented.get(principal, []) if presented else []:
            both.append(shown)
            first_seen.setdefault(str(shown["id"]), self._presented_at.get(str(shown["id"]), 0))
        # Stable: statements of one instant stay in the order they were made.
        both.sort(key=lambda s: int(str(s["issued_at_ms"])))
        return MandateStatements(
            principal, tuple(both), self.issuers(), first_seen, self.authorization_model
        )

    def spoof_reading(self, tag: str, value: JsonValue) -> None:
        """Put *value* in place of the reading of *tag* in what a gate is sent as context.

        The plant and the premises are untouched: only the context a gate asks
        for carries it, in a row that names the point's source and is signed by
        someone else (ADR 0041).

        Raises:
            KeyError: If there is no such point.
        """
        self._points.read(tag)
        self._spoofed[tag] = value
        self._kernel.state.touch("governance.path")

    def present_statement(self, principal: str, statement: Mapping[str, JsonValue]) -> None:
        """Put *statement* among what a gate is sent as *principal*'s mandate.

        It changes nothing in the register, so nothing in what a run is judged
        by. A scenario uses it for a statement that must not count (ADR 0039).
        """
        self._presented.setdefault(principal, []).append(copy.deepcopy(dict(statement)))
        # When it was first seen here: the site's own record, which no issuer writes.
        self._presented_at.setdefault(str(statement["id"]), self._kernel.clock.now_ms)
        self._kernel.state.touch("governance.path")

    def _no_mandate(self, principal: str, contract_id: str) -> str:
        """Say which kind of no mandate it is, from the register: one of ``AUTHORITY_DETAILS``.

        ``ended`` when a change held the contract, the last one included if it
        has expired or whoever passed it on no longer holds it; ``never_held``
        otherwise. A delegation held it only if whoever passed it on ever did.
        The time before the first change is not a mandate: nothing was said
        about authority then.
        """
        if self._unbound(principal, contract_id, mandates.MAX_DEPTH):
            return "unbound"
        held = self._ever_held(principal, contract_id, mandates.MAX_DEPTH)
        return "ended" if held else "never_held"

    # -- proposing -----------------------------------------------------------------

    def propose(
        self,
        actor: AuthenticatedActor,
        contract_id: str,
        arguments: Mapping[str, JsonValue],
        rationale: str = "",
        claimed_actor: str | None = None,
        claimed_time_ms: int | None = None,
    ) -> dict[str, JsonValue]:
        """Submit one contract instance and return its record.

        *claimed_time_ms* is what the agent says the time is. It is stored as a
        claim; every decision and every expiry is on this path's own clock.
        """
        now = self._kernel.clock.now_ms
        proposal = Proposal(
            f"prop-{len(self._records):05d}", contract_id, dict(arguments), rationale, claimed_actor
        )
        record: dict[str, JsonValue] = {
            "proposal_id": proposal.proposal_id, "time_ms": now, "profile": self.profile,
            "gate": self.gate.name, "principal": actor.principal, "credential": actor.credential,
            "claimed_actor": claimed_actor, "claimed_time_ms": claimed_time_ms,
            "contract_id": contract_id,
            "arguments": dict(arguments), "rationale": rationale,
            "verdict": None, "decision_ref": None, "reasons": [], "writes": [],
            "premises": [], "limit_errors": [], "required_approvers": 0, "approvers": [],
            "escalated": False, "approval_refusals": [], "was_unknown": False,
            "gate_overreach": False, "contract_errors": [],
            "outcome": "NOT_EXECUTED", "outcome_detail": "",
        }  # fmt: skip
        self._records.append(record)
        self._kernel.state.touch("governance.path")
        if self.profile == "BASELINE_RULES":
            record["outcome_detail"] = "this profile takes no proposals"
            return copy.deepcopy(record)
        snapshot = build_snapshot(
            self.library.get(contract_id),
            arguments,
            self._reader,
            self._points,
            self._key_sets,
            self._history,
            now,
            self.restore_origins(),
        )
        authorized = self.authorized(actor.principal, contract_id)
        shown = snapshot
        if authorized is not None:
            # Told to the gate as a premise, the form in which every gate is
            # already told what was established about a proposal.
            held = "holds a mandate" if authorized else "holds no mandate"
            premise = PremiseResult(
                f"authority({actor.principal}, {contract_id})",
                authorized,
                f"{actor.principal} {held} for this contract",
            )
            # The record the premise was established from goes to every gate, for
            # one that works out for itself who may act (ADR 0031).
            shown = replace(
                snapshot,
                mandate=self.mandate(actor.principal),
                statements=self.statements(actor.principal),
            )
            snapshot = replace(shown, premises=(*snapshot.premises, premise))
            if self._verifies_mandates:
                # Such a gate is given what was presented, signed, and neither the
                # answer nor the register's record (ADR 0039). Judged by the premise.
                shown = replace(shown, mandate=None)
                record["authority_premise_told"] = False
                record["authority_record_told"] = False
            elif not self._derives_authority:
                shown = snapshot
            else:
                # Such a gate is given the record and not the answer. The record
                # of the proposal keeps the premise: the run is judged by it.
                record["authority_premise_told"] = False
            record["authorized"] = authorized
            if not authorized:
                record["authority_detail"] = self._no_mandate(actor.principal, contract_id)
        if getattr(self.gate, "wants_context", False):
            # A read-only sample of the plant, for a gate that asks for one (ADR 0036).
            epoch = str(getattr(self.gate, "context_epoch", EPOCH))
            context = plant_context(
                self._points, Overview(self._points, []), now, epoch, self._spoofed
            )
            shown = replace(shown, context=context)
        decision = self.gate.evaluate(proposal, shown, actor)
        # What the gate said of its own decision, kept even if it is replaced below.
        kept = {k: decision.detail[k] for k in GATE_DETAIL_KEPT if k in decision.detail}
        # Whatever a gate returns, an ALLOW authorizes at most the writes of this
        # contract instance, as built here (INV-GOV-2). A gate from another
        # project is not trusted to keep to that; one that names other writes is
        # refused, and the attempt is counted against it.
        overreach = decision.verdict is Verdict.ALLOW and not set(decision.bounded_writes) <= set(
            snapshot.writes
        )
        if overreach:
            decision = Decision(
                Verdict.DENY,
                decision.decision_ref,
                proposal.contract_id,
                reasons=(
                    "control path: the gate authorized writes this contract instance does not make",
                    *decision.reasons,
                ),
            )
        record["gate_overreach"] = overreach
        forced = self.forced.get(proposal.proposal_id)
        if forced == "execute":
            decision = _UNGATED.evaluate(proposal, snapshot, actor)
        elif forced == "block":
            decision = Decision(
                Verdict.DENY,
                decision.decision_ref + "-blocked",
                proposal.contract_id,
                reasons=("counterfactual: not executed",),
            )
        if kept:
            # Only for a gate that says any of it, so every other gate has the
            # records, and the state hashes, it had before (ADR 0036).
            record["gate_detail"] = kept
        if decision.reason_codes:
            # Only when the gate gave any, so a gate that gives none has the
            # records, and the state hashes, it had before. In the list's order,
            # each once.
            given = [c if c in REASON_CODES else "other" for c in decision.reason_codes]
            record["reason_codes"] = [c for c in REASON_CODES if c in given]
        if decision.authority_holds is not None and authorized is not None:
            # The gate's own finding, kept beside the premise it is compared with.
            # Only where a mandate was set: elsewhere there is nothing to compare.
            record["gate_authority_holds"] = bool(decision.authority_holds)
        if (
            decision.authority_detail
            and decision.verdict is not Verdict.ALLOW
            and record.get("authorized") is False
        ):
            # Which kind of no mandate the gate refused for, if it said, and only where
            # a mandate was set and does not hold (ADR 0031, decision 11). A word of
            # its own is kept as ``other``, as with the codes.
            said = decision.authority_detail
            record["reason_details"] = {"authority": said if said in AUTHORITY_DETAILS else "other"}
        record.update({
            "verdict": decision.verdict.value, "decision_ref": decision.decision_ref,
            "reasons": list(decision.reasons), "escalated": decision.escalated,
            "writes": [[w.tag, w.value, w.previous] for w in decision.bounded_writes],
            "premises": [asdict(p) for p in snapshot.premises],
            "limit_errors": list(snapshot.limit_errors),
            "contract_errors": [*snapshot.schema_errors, *snapshot.bound_errors],
            "required_approvers": decision.required_approvers,
            "expires_at_ms": decision.expires_at_ms,
        })  # fmt: skip
        self._kernel.events.publish("governance.decision", {
            "proposal_id": proposal.proposal_id, "verdict": decision.verdict.value,
            "contract_id": contract_id, "decision_ref": decision.decision_ref,
        })  # fmt: skip
        if decision.verdict is Verdict.ALLOW:
            if PROFILES[self.profile] or forced == "execute":
                self._execute(
                    record, decision.bounded_writes, decision.decision_ref, forced == "execute"
                )
            else:
                self._finish(record, "NOT_EXECUTED", "shadow: decisions are recorded only")
        elif decision.verdict is Verdict.VERIFY:
            record["outcome_detail"] = WAITING_FOR_APPROVAL
        else:
            self._finish(record, "NOT_EXECUTED", decision.verdict.value.lower())
        return copy.deepcopy(record)

    # -- approval ---------------------------------------------------------------------

    def approve(self, proposal_id: str, approver: str) -> dict[str, JsonValue]:
        """Record one human approval; execute when enough distinct people have approved.

        The premises are read again at that moment. If one no longer holds, the
        proposal is not executed: an approval is for the plant as it was shown.
        """
        record = self._pending(proposal_id)
        approvers = record["approvers"]
        assert isinstance(approvers, list)
        if approver == record["principal"]:
            raise ValueError("the proposer cannot approve its own proposal")
        now = self._kernel.clock.now_ms
        self._kernel.state.touch("governance.path")
        if approver not in approvers:
            if self.authorized(approver, str(record["contract_id"])) is False:
                # Someone who has lost access. It is refused here, before any
                # gate is asked: who may approve is not a gate's to decide.
                refused = record["approval_refusals"]
                assert isinstance(refused, list)
                refused.append(f"{approver}: {NO_STANDING}")
                _trace(record, "refusals", {"by": approver, "at_ms": now, "why": NO_STANDING})
                return copy.deepcopy(record)
            hook = getattr(self.gate, "approve", None)
            if hook is not None:
                try:
                    hook(str(record["decision_ref"]), approver, now)
                except GateRefusal as exc:
                    refused = record["approval_refusals"]
                    assert isinstance(refused, list)
                    refused.append(f"{approver}: {exc}")
                    _trace(record, "refusals", {"by": approver, "at_ms": now, "why": str(exc)})
                    return copy.deepcopy(record)
            approvers.append(approver)
            _trace(record, "approvals", {"by": approver, "at_ms": now})
        if len(approvers) < int(str(record["required_approvers"])):
            return copy.deepcopy(record)
        if not PROFILES[self.profile]:
            self._finish(record, "NOT_EXECUTED", "shadow: decisions are recorded only")
            return copy.deepcopy(record)
        arguments = record["arguments"]
        assert isinstance(arguments, dict)
        snapshot = build_snapshot(
            self.library.get(str(record["contract_id"])),
            arguments,
            self._reader,
            self._points,
            self._key_sets,
            self._history,
            now,
            self.restore_origins(),
        )
        failed = [p.expr for p in snapshot.premises if p.holds is not True]
        proposer, contract_id = str(record["principal"]), str(record["contract_id"])
        # The check made when the last approval came in, before anything is written:
        # when, and whether the proposer still held a mandate then (ADR 0038).
        trace = record.setdefault("trace", {})
        assert isinstance(trace, dict)
        trace["checked_at_ms"] = now
        trace["mandate_at_check"] = self.authorized(proposer, contract_id)
        if self.authorized(proposer, contract_id) is False:
            # Asked for while the proposer held a mandate, answered after it ended.
            failed.append(f"authority({proposer}, {contract_id})")
        problems = [*snapshot.schema_errors, *snapshot.bound_errors]
        if not self._gate_limits:
            problems += snapshot.limit_errors
        if failed or problems:
            detail = "; ".join(
                [*problems, *(f"premise no longer established: {f}" for f in failed)]
            )
            self._finish(record, "REJECTED", detail)
        else:
            self._execute(record, snapshot.writes, f"{record['decision_ref']}+approved")
        return copy.deepcopy(record)

    def reject(self, proposal_id: str, approver: str, reason: str) -> dict[str, JsonValue]:
        """Refuse a proposal that is waiting for approval."""
        record = self._pending(proposal_id)
        hook = getattr(self.gate, "reject", None)
        if hook is not None:
            try:
                hook(str(record["decision_ref"]), approver, reason, self._kernel.clock.now_ms)
            except GateRefusal as exc:
                reason = f"{reason} (gate: {exc})"
        _trace(record, "rejections", {
            "by": approver, "at_ms": self._kernel.clock.now_ms, "why": reason,
        })  # fmt: skip
        self._finish(record, "REJECTED", f"{REJECTED_BY}{approver}: {reason}")
        return copy.deepcopy(record)

    def _pending(self, proposal_id: str) -> dict[str, JsonValue]:
        for record in self._records:
            if record["proposal_id"] == proposal_id:
                waiting = (
                    record["verdict"] == "VERIFY"
                    and record["outcome_detail"] == WAITING_FOR_APPROVAL
                )
                if not waiting:
                    raise ValueError(f"{proposal_id} is not waiting for approval")
                return record
        raise KeyError(proposal_id)

    # -- execution ---------------------------------------------------------------------

    def _execute(
        self,
        record: dict[str, JsonValue],
        writes: tuple[BoundedWrite, ...],
        reference: str,
        ungated: bool = False,
    ) -> None:
        """Write what was authorized, through the gate when the gate releases execution.

        *ungated* is the counterfactual "as if no gate stood in the way": the
        gate is not asked to release anything.
        """
        now = self._kernel.clock.now_ms
        done: list[BoundedWrite] = []
        release = None if ungated else getattr(self.gate, "execute", None)
        if release is None or record["decision_ref"] is None:
            status, detail = self._apply(record, writes, reference, done)
        else:
            outcome = release(
                str(record["decision_ref"]),
                now,
                lambda: self._apply(record, writes, reference, done),
                lambda tag: self._points.read(tag).value,
            )
            status, detail = outcome.status, outcome.detail
        if status == "APPLIED":
            self._applied(record, done)
        elif status == "UNKNOWN":
            # The grant is spent and nobody knows whether the write landed. It is
            # not tried again; the plant is read later to find out (ADC-GOV-007).
            record["was_unknown"] = True
            self._unknown.append((str(record["proposal_id"]), now, list(writes)))
        self._finish(record, status, detail)

    def _apply(
        self,
        record: dict[str, JsonValue],
        writes: tuple[BoundedWrite, ...],
        reference: str,
        done: list[BoundedWrite],
    ) -> tuple[str, str]:
        """Send each write to the command service once. Return the status and why."""
        now = self._kernel.clock.now_ms
        agent = Actor(str(record["principal"]), "agent")
        reason = f"{record['contract_id']} ({record['proposal_id']})"
        for index, write in enumerate(writes):
            ref = write_ref(reference, index)
            self._service.authorize(ref, write.tag, write.value, now + DECISION_TTL_MS)
            result = self._service.write(write.tag, write.value, agent, reason, decision_ref=ref)
            if result.code == "TIMEOUT":
                return "UNKNOWN", f"{write.tag}: no answer from the command service"
            if not result.accepted:
                for earlier in reversed(done):
                    self._service.write(
                        earlier.tag,
                        earlier.previous,
                        _ROLLBACK_ACTOR,
                        f"undo partial {record['proposal_id']}",
                    )
                done.clear()
                return "REJECTED", f"{write.tag}: {result.code}"
            done.append(write)
        return "APPLIED", ""

    def _applied(self, record: dict[str, JsonValue], done: list[BoundedWrite]) -> None:
        """Count applied writes against the limits and start watching the rollback trigger."""
        now = self._kernel.clock.now_ms
        for write in done:
            numeric = isinstance(write.value, float) and isinstance(write.previous, int | float)
            delta = float(write.value) - float(write.previous) if numeric else 0.0
            self._history.append(AppliedWrite(now, str(record["contract_id"]), write.tag, delta))
        contract = self.library.get(str(record["contract_id"]))
        watched = self.profile in ROLLBACK_PROFILES
        if watched and contract is not None and contract.rollback is not None:
            arguments = record["arguments"]
            assert isinstance(arguments, dict)
            text = {name: str(value) for name, value in arguments.items()}
            tags = {w.tag for w in done}
            # A newer write to a tag replaces the watch on the older one, and
            # inherits how far back the older one would have gone (INV-GOV-7).
            origins = self.restore_origins()
            replaced = [w for w in self._watches if tags & {t for t, _ in w.writes}]
            self._watches = [w for w in self._watches if all(w is not r for r in replaced)]
            restore = [(w.tag, origins.get(w.tag, w.previous)) for w in done]
            if all(_same(value, w.value) for (_, value), w in zip(restore, done, strict=True)):
                return  # back where the agent started: nothing left to undo
            directions = set()
            for write in done:
                if isinstance(write.value, float) and isinstance(write.previous, int | float):
                    if write.value > float(write.previous):
                        directions.add("increase")
                    elif write.value < float(write.previous):
                        directions.add("decrease")
            # A step only in the safe direction leaves part of an earlier step
            # standing. What is left is watched on that step's premises; the
            # undo goes to the origin, so it never moves the plant up again.
            inherited = [i for w in replaced for i in (w.chain or [w.proposal_id])]
            chain = list(dict.fromkeys(inherited))
            if directions == {contract.safe_direction()}:
                premises = [p for w in replaced for p in w.premises]
            else:
                chain.append(str(record["proposal_id"]))
                premises = [
                    (fill(p.expr, text), p.max_age_s)
                    for p in contract.premises
                    if p.when == "always" or p.when in directions
                ]
            trigger = fill(contract.rollback.trigger, text)
            self._watches.append(
                _Watch(str(record["proposal_id"]), trigger, -1, restore, premises, chain=chain)
            )

    def restore_origins(self) -> dict[str, float]:
        """Return, per point under watch, the numeric value it had before the agent moved it."""
        out: dict[str, float] = {}
        for watch in self._watches:
            for tag, value in watch.writes:
                if isinstance(value, int | float) and not isinstance(value, bool):
                    out[tag] = float(value)
        return out

    def _resolve_unknowns(self) -> None:
        """Find out what became of an unanswered write, from the plant and nothing else."""
        if not self._unknown:
            return
        now = self._kernel.clock.now_ms
        keep = []
        for proposal_id, since, writes in self._unknown:
            if now - since < UNKNOWN_RESOLVE_MS:
                keep.append((proposal_id, since, writes))
                continue
            in_place = [w for w in writes if self._points.read(w.tag).value == w.value]
            landed = len(in_place) == len(writes)
            for record in self._records:
                if record["proposal_id"] != proposal_id:
                    continue
                told = ""
                hook = getattr(self.gate, "resolve_unknown", None)
                if hook is not None and record["decision_ref"] is not None:
                    # The gate is told about the first write, the one it meters.
                    first = bool(in_place) and in_place[0] == writes[0]
                    told = f"; gate: {hook(str(record['decision_ref']), first, now)}"
                if landed:
                    self._applied(record, in_place)
                    status, found = "APPLIED", "the write is in place"
                elif not in_place:
                    status, found = "REJECTED", "the write is not in place"
                else:
                    # Half a contract instance is not a state to leave the plant in.
                    for write in reversed(in_place):
                        self._service.write(
                            write.tag, write.previous, _ROLLBACK_ACTOR,
                            f"restore after partly applied {proposal_id}",
                        )  # fmt: skip
                    status = "ROLLED_BACK"
                    found = f"{len(in_place)} of {len(writes)} writes in place; restored"
                self._finish(
                    record, status, f"was unknown; an independent read found {found}{told}"
                )
        self._unknown = keep
        self._kernel.state.touch("governance.path")

    def _finish(self, record: dict[str, JsonValue], status: str, detail: str) -> None:
        record["outcome"], record["outcome_detail"] = status, detail
        trace = record.get("trace")
        if isinstance(trace, dict):
            trace["finished_at_ms"] = self._kernel.clock.now_ms
        self._kernel.state.touch("governance.path")
        if record["decision_ref"] is not None:
            self.gate.record_outcome(str(record["decision_ref"]), Outcome(status, detail))
        self._kernel.events.publish("governance.outcome", {
            "proposal_id": record["proposal_id"], "outcome": status, "detail": detail,
        })  # fmt: skip

    # -- rollback ----------------------------------------------------------------------

    def _watch_rollbacks(self) -> None:
        """Undo an applied step whose trigger fires or whose premises or mandate are lost.

        The first two checks fail safe. A trigger that cannot be read counts as fired
        (INV-GOV-3 applied to undoing), and a premise the step was allowed on
        that no longer holds, or can no longer be established, for
        ``PREMISE_LOSS_HOLD_S`` undoes it (INV-GOV-7). The undo goes back to
        the value before the agent's first standing write on each point and is
        made by the rollback monitor, so no limit of the agent's holds it back.

        A step is in progress for ``IN_PROGRESS_S`` after it was written. If the
        mandate it was taken under ends in that time, withdrawn, expired or
        ended at its source, the step is undone, but only when every write then
        moves in the contract's safe direction. A step whose undoing would move
        the plant away from safety is left standing (ADR 0041).
        """
        if not self._watches:
            return
        now = self._kernel.clock.now_ms
        keep = []
        for watch in self._watches:
            if not watch.undo:
                watch.undo = (
                    self._fired(watch, now)
                    or self._premise_lost(watch, now)
                    or self._mandate_lost(watch, now)
                )
                if not watch.undo:
                    keep.append(watch)
                    continue
                self._kernel.state.touch("governance.path")
            refused = []
            for tag, previous in watch.writes:
                result = self._service.write(
                    tag, previous, _ROLLBACK_ACTOR, f"rollback of {watch.proposal_id}: {watch.undo}"
                )
                if not result.accepted:
                    refused.append(f"{tag}: {result.code}")
            if refused:
                # Not undone until the plant has every value back: try again.
                keep.append(watch)
                continue
            undone = set(watch.chain or [watch.proposal_id])
            for record in self._records:
                if record["proposal_id"] in undone and record["outcome"] == "APPLIED":
                    self._finish(record, "ROLLED_BACK", watch.undo)
        self._watches = keep

    def _fired(self, watch: _Watch, now: int) -> str:
        condition, hold_s = split_hold(watch.trigger)
        holds = self._reader.evaluate(condition, 3600.0, now).holds is not False
        if not holds:
            if watch.since_ms >= 0:
                watch.since_ms = -1
                self._kernel.state.touch("governance.path")
            return ""
        if watch.since_ms < 0:
            watch.since_ms = now
            self._kernel.state.touch("governance.path")
        return watch.trigger if now - watch.since_ms >= hold_s * 1000 else ""

    def _mandate_lost(self, watch: _Watch, now: int) -> str:
        """Say why a step in progress is undone for want of a mandate; empty if it is not.

        The steps a watch would undo are those of its chain. Each is judged by
        its own proposer, its own contract and the time of its own writes.
        """
        if self._authority is None:
            return ""
        tags = {tag for tag, _ in watch.writes}
        standing = set(watch.chain or [watch.proposal_id])
        for record in self._records:
            if record["proposal_id"] not in standing or record.get("authorized") is not True:
                continue  # taken without a mandate: counted as that, and not this case
            principal, contract_id = str(record["principal"]), str(record["contract_id"])
            if self.authorized(principal, contract_id) is not False:
                continue
            recent = any(
                w.contract_id == contract_id and w.tag in tags
                and now - w.time_ms <= IN_PROGRESS_S * 1000
                for w in self._history
            )  # fmt: skip
            if not recent:
                continue  # nothing of this contract was written lately: no need to read the log
            marked = f"({record['proposal_id']})"
            written = [
                int(str(entry["time_ms"]))
                for entry in self._service.change_log()
                if entry["accepted"]
                and entry["tag"] in tags
                and str(entry["reason"]).endswith(marked)
            ]
            if not written or now - max(written) > IN_PROGRESS_S * 1000:
                continue
            contract = self.library.get(contract_id)
            safe = contract.safe_direction() if contract is not None else None
            if safe is not None and self._undo_is_safe(watch, safe):
                return f"{MANDATE_ENDED_IN_PROGRESS}: authority({principal}, {contract_id})"
        return ""

    def _undo_is_safe(self, watch: _Watch, safe: str) -> bool:
        """Whether writing every point of *watch* back moves it in the direction *safe*."""
        for tag, previous in watch.writes:
            current = self._points.read(tag).value
            numeric = isinstance(current, int | float) and isinstance(previous, int | float)
            if not numeric or isinstance(current, bool):
                return False
            back = "decrease" if float(previous) < float(current) else "increase"
            if float(previous) != float(current) and back != safe:
                return False  # undoing would move the plant away from safety
        return True

    def _premise_lost(self, watch: _Watch, now: int) -> str:
        lost = [
            result
            for result in (self._reader.evaluate(e, age, now) for e, age in watch.premises)
            if result.holds is not True
        ]
        if not lost:
            if watch.lost_since_ms >= 0:
                watch.lost_since_ms = -1
                self._kernel.state.touch("governance.path")
            return ""
        if watch.lost_since_ms < 0:
            watch.lost_since_ms = now
            self._kernel.state.touch("governance.path")
        if now - watch.lost_since_ms < PREMISE_LOSS_HOLD_S * 1000:
            return ""
        first = lost[0]
        state = "not established" if first.holds is None else "no longer holds"
        return f"premise {state}: {first.expr} ({first.detail})"

    # -- reading ------------------------------------------------------------------------

    def records(self) -> list[dict[str, JsonValue]]:
        """Return a copy of every proposal record, oldest first."""
        return copy.deepcopy(self._records)

    def record(self, proposal_id: str) -> dict[str, JsonValue]:
        """Return a copy of one proposal record."""
        for record in self._records:
            if record["proposal_id"] == proposal_id:
                return copy.deepcopy(record)
        raise KeyError(proposal_id)

    def snapshot(self) -> JsonValue:
        """Return every record, the applied-write history, the watches and the mandate register."""
        state = self._snapshot()
        if self._authority is not None:
            # Only once a mandate was set, so a run that never sets one has the
            # state, and the state hash, it had before authority was modelled.
            assert isinstance(state, dict)
            state["authority"] = {
                principal: [
                    # The expiry only where one was set, so a run without any has
                    # the state it had before a mandate could expire (ADR 0039).
                    [c.time_ms, list(c.contracts), c.by, c.reason]
                    + ([c.expires_at_ms] if c.expires_at_ms is not None or c.delegated else [])
                    + ([True] if c.delegated else [])
                    for c in changes
                ]
                for principal, changes in sorted(self._authority.items())
            }
        if self._presented:
            assert isinstance(state, dict)
            state["presented"] = copy.deepcopy(dict(sorted(self._presented.items())))
            state["presented_at"] = dict(sorted(self._presented_at.items()))
        if self._spoofed:
            assert isinstance(state, dict)
            state["spoofed"] = dict(sorted(self._spoofed.items()))
        if self.authorization_model != mandates.HOLDINGS:
            # Only where a site declares another model, so every earlier run keeps its hash.
            assert isinstance(state, dict)
            state["authorization_model"] = self.authorization_model
        if self._issuer_events:
            assert isinstance(state, dict)
            state["issuer_events"] = {
                name: [[kind, at_ms] for kind, at_ms in events]
                for name, events in sorted(self._issuer_events.items())
            }
        return state

    def _snapshot(self) -> JsonValue:
        return {
            # A served gate's correlation names the session of this execution, which
            # differs between executions of the same run, and the trace of an approval
            # is bookkeeping for evidence that nothing in the run reads. Both are kept on
            # the record and out of the state, so the state hash and the card do not
            # depend on them (ADR 0036, ADR 0038).
            "records": [
                {k: copy.deepcopy(v) for k, v in r.items() if k not in UNHASHED_RECORD_KEYS}
                for r in self._records
            ],
            "notes": list(self.notes),
            "history": [[h.time_ms, h.contract_id, h.tag, h.delta] for h in self._history],
            "watches": [
                [
                    w.proposal_id,
                    w.trigger,
                    w.since_ms,
                    [[t, v] for t, v in w.writes],
                    [[e, a] for e, a in w.premises],
                    w.lost_since_ms,
                    w.undo,
                    list(w.chain),
                ]
                for w in self._watches
            ],
            "unknown": [
                [p, since, [[w.tag, w.value, w.previous] for w in ws]]
                for p, since, ws in self._unknown
            ],
        }

    def restore(self, state: JsonValue) -> None:
        """Put back a state returned by :meth:`snapshot`."""
        if not isinstance(state, Mapping):
            raise ValueError("control-path snapshot must be a mapping")
        records, history, watches = state["records"], state["history"], state["watches"]
        assert isinstance(records, list) and isinstance(history, list) and isinstance(watches, list)
        self._records = [dict(r) for r in records if isinstance(r, Mapping)]
        notes = state.get("notes", [])
        assert isinstance(notes, list)
        self.notes[:] = [str(n) for n in notes]
        self._history = [
            AppliedWrite(int(str(h[0])), str(h[1]), str(h[2]), float(str(h[3])))
            for h in history
            if isinstance(h, list | tuple)
        ]
        self._watches = []
        for w in watches:
            assert isinstance(w, list | tuple) and isinstance(w[3], list | tuple)
            restore = [(str(t[0]), _scalar(t[1])) for t in w[3] if isinstance(t, list | tuple)]
            watched = w[4] if len(w) > 4 and isinstance(w[4], list | tuple) else []
            premises = [
                (str(t[0]), float(str(t[1]))) for t in watched if isinstance(t, list | tuple)
            ]
            lost = int(str(w[5])) if len(w) > 5 else -1
            undo = str(w[6]) if len(w) > 6 else ""
            kept = w[7] if len(w) > 7 and isinstance(w[7], list | tuple) else []
            self._watches.append(
                _Watch(
                    str(w[0]),
                    str(w[1]),
                    int(str(w[2])),
                    restore,
                    premises,
                    lost,
                    undo,
                    [str(i) for i in kept],
                )
            )
        mandates = state.get("authority")
        self._authority = None
        if isinstance(mandates, Mapping):
            self._authority = {}
            for principal, changes in mandates.items():
                assert isinstance(changes, list | tuple)
                kept = []
                for change in changes:
                    assert isinstance(change, list | tuple) and isinstance(change[1], list | tuple)
                    kept.append(
                        MandateChange(
                            int(str(change[0])),
                            tuple(str(c) for c in change[1]),
                            str(change[2]),
                            str(change[3]),
                            int(str(change[4]))
                            if len(change) > 4 and change[4] is not None
                            else None,
                            len(change) > 5 and bool(change[5]),
                        )
                    )
                self._authority[str(principal)] = kept
        seen = state.get("presented_at")
        self._presented_at = (
            {str(k): int(str(v)) for k, v in seen.items()} if isinstance(seen, Mapping) else {}
        )
        spoofed = state.get("spoofed")
        self._spoofed = dict(spoofed) if isinstance(spoofed, Mapping) else {}
        self.authorization_model = str(state.get("authorization_model", HOLDINGS))
        self._issuer_events = {}
        kept_events = state.get("issuer_events")
        if isinstance(kept_events, Mapping):
            for name, events in kept_events.items():
                assert isinstance(events, list | tuple)
                self._issuer_events[str(name)] = [(str(e[0]), int(str(e[1]))) for e in events]
        presented = state.get("presented")
        self._presented = {}
        if isinstance(presented, Mapping):
            for principal, given in presented.items():
                assert isinstance(given, list | tuple)
                self._presented[str(principal)] = [dict(s) for s in given]
        self._unknown = []
        for u in state.get("unknown", []):  # type: ignore[union-attr]
            assert isinstance(u, list | tuple) and isinstance(u[2], list | tuple)
            meant = [
                BoundedWrite(str(t[0]), _scalar(t[1]), _scalar(t[2]))
                for t in u[2]
                if isinstance(t, list | tuple)
            ]
            self._unknown.append((str(u[0]), int(str(u[1])), meant))


def _scalar(value: JsonValue) -> bool | int | float | str:
    if not isinstance(value, bool | int | float | str):
        raise ValueError(f"not a point value: {value!r}")
    return value


def decision_fields(decision: Decision) -> dict[str, JsonValue]:
    """Return the fields of a decision that go into a decision record."""
    return {
        "verdict": decision.verdict.value, "decision_ref": decision.decision_ref,
        "contract_id": decision.contract_id, "reasons": list(decision.reasons),
        "expires_at_ms": decision.expires_at_ms, "nonce": decision.single_use_nonce,
        "required_approvers": decision.required_approvers, "escalated": decision.escalated,
    }  # fmt: skip


def _same(a: bool | int | float | str, b: bool | int | float | str) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float) and not isinstance(a, bool):
        return abs(float(a) - float(b)) < 1e-9
    return a == b
