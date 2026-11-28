"""A gate served over HTTP: any governance system, in any language, behind one URL.

The binding turns each call of the gate interface into one JSON request
(``agentic_datacenter.gate.http/1``, schema in ``docs/reference/gate-http-v1.schema.json``)
and reads the service's verdict back. What makes a decision safe to execute
stays on this side and cannot be widened by the service:

- the writes an ``ALLOW`` authorizes are the writes of the contract instance,
  as the binding read them; the service cannot name others;
- the decision reference, its nonce and its expiry are made here, from
  everything decided, on simulated time;
- a service that cannot be reached, answers late, or answers with anything
  that is not a valid verdict, gets ``ABSTAIN`` (fail closed), and the reason
  is on the decision.

Configured by environment, so a benchmark made in several processes
configures every process the same way:

``ADC_GATE_URL``
    Base URL of the service, for example ``http://127.0.0.1:8787``. Required.
``ADC_GATE_NAME``
    The name the gate reports on cards and evidence. Default ``http_gate``.
``ADC_GATE_PROFILE``
    The operating profile (SDD 9.6) the gate runs under. Default
    ``DECISION_ONLY``: the gate decides and Agentic-Datacenter's command service executes.
``ADC_GATE_TIMEOUT_S``
    Seconds to wait for an answer. Default 10.
``ADC_GATE_ALLOW_REMOTE``
    Set to ``1`` to allow a URL outside loopback. Simulation state is then
    sent to that host.
``ADC_GATE_DERIVES_AUTHORITY``
    Set to ``1`` for a service that works out for itself who may act. A request
    then carries the mandate record and not the premise
    ``authority(<principal>, <contract>)``; the run is still judged by that
    premise (ADR 0031).
``ADC_GATE_VERIFIES_MANDATES``
    Set to ``1`` for a service that authenticates the mandate itself. A request
    then carries the signed statements and the trusted issuers
    (``mandate_statements``), and neither that premise nor the register's
    record; the run is still judged by the premise (ADR 0039).
``ADC_GATE_CONTEXT``
    Set to ``1`` for a service that wants a read-only sample of the plant with
    each request: temperatures, load, interlocks, margins and alarms, each with
    value, unit, quality and simulated time (``context``, ADR 0036). Each
    reading is signed by its source (ADR 0041).
``ADC_GATE_RECEIPT_KEY``
    The public key the service signs its receipts with: raw Ed25519 in base64,
    several separated by commas. The signature of the ``receipt`` in each answer
    is then checked, and the decision record and the action trace say what was
    found (ADR 0041). Without it no signature is checked.
``ADC_GATE_EPOCH``
    The UTC instant simulated time 0 is given as in ``issued_at`` and
    ``observed_at``. Default ``2026-01-01T00:00:00Z``. Fixed, never the wall
    clock, so a run is the same run.
``ADC_GATE_STATEFUL``
    Set to ``1`` for a service that remembers earlier requests (a session,
    a budget, a ledger). It keys what it keeps by the request's ``session``;
    Agentic-Datacenter then replays a counterfactual branch from the start of the run in
    a new session, instead of from the instant of the decision.

A service that answers in words of its own (``permit``, ``block``, ``ask``,
``require_approval``, ``skip`` and the others in :data:`VERDICT_ALIASES`) is
read as the Agentic-Datacenter verdict they mean, and its own word is kept on the decision.

``command_gate`` is the same binding over a command instead of a URL, for a
gate that runs as a hook: ``ADC_GATE_COMMAND`` is run once per request, the
request is written to its standard input, and its answer is read from its
standard output (``ADC_GATE_URL`` is not used).
"""

from __future__ import annotations

import base64
import ipaddress
import itertools
import json
import os
import shlex
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from agentic_datacenter.core.state import JsonValue
from agentic_datacenter.governance import attest
from agentic_datacenter.governance.envelope_ref import DECISION_TTL_MS, decision_ref
from agentic_datacenter.governance.gate import (
    OUTCOMES,
    AuthenticatedActor,
    Decision,
    Mandate,
    MandateChange,
    Outcome,
    PremiseSnapshot,
    Proposal,
    Verdict,
)
from agentic_datacenter.governance.interop import (
    EPOCH,
    action_ref,
    correlation_id,
    parse_epoch,
    receipt_kept,
    utc,
)

#: The wire format. A service answers with the same value or is not believed.
SCHEMA = "agentic_datacenter.gate.http/1"
#: Operating profiles a served gate may declare (SDD 9.6). ``SHADOW`` executes
#: nothing; ``GOVERNED`` is for a gate that also authorizes execution itself,
#: which this binding does not do.
SERVED_PROFILES = ("DECISION_ONLY", "STATIC_ALLOWLIST", "UNGOVERNED", "SHADOW")
_MAX_REASONS = 16
_MAX_TEXT = 500
#: Words governance systems use for their verdicts, and the Agentic-Datacenter verdict each
#: one means. Matched without regard to case, ``-`` or spaces. A word that
#: names an allowance with conditions Agentic-Datacenter cannot enforce is not here.
VERDICT_ALIASES: dict[str, Verdict] = {
    **{w: Verdict.ALLOW for w in (
        "allow", "allowed", "accept", "accepted", "permit", "permitted", "approve",
        "approved", "admit", "admitted", "execute", "proceed",
    )},
    **{w: Verdict.DENY for w in (
        "deny", "denied", "block", "blocked", "forbid", "forbidden", "refuse",
        "refused", "reject", "rejected",
    )},
    **{w: Verdict.VERIFY for w in (
        "verify", "escalate", "escalated", "ask", "review", "require_approval",
        "require_auth", "requires_approval", "human_review",
    )},
    **{w: Verdict.ABSTAIN for w in (
        "abstain", "abstained", "skip", "skipped", "unknown", "insufficient_evidence",
        "out_of_scope", "no_decision",
    )},
}  # fmt: skip
_SESSIONS = itertools.count(1)
#: What an outcome message carries of the evaluate request it answers (ADR 0036).
OUTCOME_CORRELATION = ("correlation_id", "action_ref", "proposal_id", "verdict")
#: The most of an answer that is read. A longer one is a failure of the service.
MAX_ANSWER_BYTES = 1 << 20


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: simulation state goes to the configured URL or nowhere."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


#: No proxy from the environment and no redirects. A proxy set in ``http_proxy``
#: would otherwise receive what is meant for a service on loopback, and a
#: redirect could send it to a host that was never checked.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def read_verdict(word: object) -> Verdict | None:
    """Return the Agentic-Datacenter verdict a service's word means, or None if it means none."""
    key = str(word).strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return Verdict(key.upper())
    except ValueError:
        return VERDICT_ALIASES.get(key)


class HttpGateConfigError(ValueError):
    """The binding is not configured, or is configured to leave loopback unasked."""


def _loopback(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def request_body(
    proposal: Proposal,
    premises: PremiseSnapshot,
    actor: AuthenticatedActor,
    session: str = "",
    epoch: str = EPOCH,
) -> dict[str, JsonValue]:
    """Return the JSON body of an ``evaluate`` request.

    ``correlation`` is the same in the ``outcome`` message of this proposal:
    its ``correlation_id``, the content address of the proposed action
    (``action_ref``), and the instant as UTC from a fixed epoch (``issued_at``).
    ``context`` is there only for a gate that asked for it (ADR 0036).

    ``tool_call`` is the same proposal seen as a tool call, for a gate that
    decides on the action itself rather than on an intent contract.
    ``mandate`` is there only when a mandate was set for the proposer: the
    register's record of it, every change oldest first (ADR 0031).
    ``mandate_statements`` is the same mandate as signed statements, with
    whatever else was presented and the issuers the site trusts (ADR 0039).
    """
    body: dict[str, JsonValue] = {
        "schema": SCHEMA,
        "session": session,
        "tool_call": {
            "name": proposal.contract_id,
            "arguments": dict(proposal.arguments),
            "writes": [{"tag": w.tag, "value": w.value} for w in premises.writes],
        },
        "proposal": {
            "proposal_id": proposal.proposal_id,
            "contract_id": proposal.contract_id,
            "arguments": dict(proposal.arguments),
            "rationale": proposal.rationale,
            "claimed_actor": proposal.claimed_actor,
        },
        "premises": {
            "time_ms": premises.time_ms,
            "contract_known": premises.contract_known,
            "risk_tier": premises.risk_tier,
            "approval": premises.approval,
            "writes": [
                {"tag": w.tag, "value": w.value, "previous": w.previous} for w in premises.writes
            ],
            "schema_errors": list(premises.schema_errors),
            "bound_errors": list(premises.bound_errors),
            "limit_errors": list(premises.limit_errors),
            "premises": [
                {"expr": p.expr, "holds": p.holds, "detail": p.detail, "invariant": p.invariant}
                for p in premises.premises
            ],
            "rollback_available": premises.rollback_available,
            "restore": premises.restore,
        },
        "actor": {"principal": actor.principal, "credential": actor.credential},
        "correlation": correlation(proposal, premises.time_ms, actor, session, epoch),
    }
    if premises.context is not None:
        body["context"] = dict(premises.context)
    if premises.mandate is not None:
        body["mandate"] = _record(premises.mandate, premises.time_ms)
        if premises.mandate.model != "holdings":
            # Only where a site declares another model (ADR 0048): absent is holdings,
            # so a request is what it was for every earlier scenario.
            body["mandate"]["model"] = premises.mandate.model  # type: ignore[index]
        if premises.mandate.delegators:
            # Only where a change was passed on from someone (ADR 0040).
            body["mandate"]["delegators"] = [  # type: ignore[index]
                _record(giver, premises.time_ms) for giver in premises.mandate.delegators
            ]
    if premises.statements is not None:
        body["mandate_statements"] = {
            "principal": premises.statements.principal,
            "statements": [dict(s) for s in premises.statements.statements],
            "issuers": [dict(i) for i in premises.statements.issuers],
            "first_seen_ms": dict(premises.statements.first_seen),
        }
        if premises.statements.model != "holdings":
            # How the site reads a delegation, only where it is not holdings (ADR 0048).
            body["mandate_statements"]["model"] = premises.statements.model  # type: ignore[index]
    return body


def _record(mandate: Mandate, now_ms: int) -> dict[str, JsonValue]:
    """Return one principal's record as a request carries it."""
    changes = mandate.changes
    return {
        "principal": mandate.principal,
        "changes": [
            {
                "time_ms": change.time_ms,
                # When it stopped being in force, replaced by the next change or
                # expired; null for the one in force.
                "until_ms": _until_ms(change, later, now_ms),
                "contracts": list(change.contracts),
                "by": change.by,
                "reason": change.reason,
                # Only on a change that expires by itself (ADR 0039).
                **(
                    {"expires_at_ms": change.expires_at_ms}
                    if change.expires_at_ms is not None
                    else {}
                ),
                # Only on a change that passes on part of the mandate of ``by`` (ADR 0040).
                **({"delegated": True} if change.delegated else {}),
            }
            for change, later in zip(changes, [*changes[1:], None], strict=True)
        ],
    }


def _until_ms(change: MandateChange, later: MandateChange | None, now_ms: int) -> int | None:
    """Return when *change* stopped being in force: replaced, or expired; None if it is."""
    ends = [later.time_ms] if later is not None else []
    if change.expires_at_ms is not None and (later is not None or now_ms >= change.expires_at_ms):
        ends.append(change.expires_at_ms)
    return min(ends) if ends else None


def correlation(
    proposal: Proposal, time_ms: int, actor: AuthenticatedActor, session: str, epoch: str = EPOCH
) -> dict[str, JsonValue]:
    """Return what ties an ``evaluate`` request, its ``outcome`` and a receipt together."""
    return {
        "correlation_id": correlation_id(session, proposal.proposal_id),
        "action_ref": action_ref(
            session, proposal.proposal_id, proposal.contract_id, proposal.arguments,
            actor.principal, time_ms,
        ),
        "issued_at": utc(time_ms, epoch),
        "time_anchor": epoch,
    }  # fmt: skip


def _text(value: Any) -> str:
    return str(value)[:_MAX_TEXT]


class HttpGate:
    """A ``GovernanceGate`` whose decisions come from a service over HTTP."""

    def __init__(
        self,
        url: str,
        name: str = "http_gate",
        profile: str = "DECISION_ONLY",
        timeout_s: float = 10.0,
        allow_remote: bool = False,
        stateful: bool = False,
        derives_authority: bool = False,
        wants_context: bool = False,
        epoch: str = EPOCH,
        verifies_mandates: bool = False,
    ) -> None:
        """Create the binding. Nothing is sent until the first decision."""
        self._setup(
            name, profile, timeout_s, stateful, derives_authority, wants_context, epoch,
            verifies_mandates,
        )  # fmt: skip
        if not url:
            raise HttpGateConfigError("ADC_GATE_URL is not set")
        if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            raise HttpGateConfigError(f"not an http(s) URL: {url}")
        if not allow_remote and not _loopback(url):
            raise HttpGateConfigError(
                f"{url} is not on loopback; set ADC_GATE_ALLOW_REMOTE=1 to send it simulation state"
            )
        self.url = url.rstrip("/")

    def _setup(
        self,
        name: str,
        profile: str,
        timeout_s: float,
        stateful: bool,
        derives_authority: bool,
        wants_context: bool = False,
        epoch: str = EPOCH,
        verifies_mandates: bool = False,
    ) -> None:
        if profile not in SERVED_PROFILES:
            raise HttpGateConfigError(f"profile {profile} is not one of {SERVED_PROFILES}")
        try:
            parse_epoch(epoch)
        except ValueError as exc:
            raise HttpGateConfigError(f"ADC_GATE_EPOCH: {exc}") from exc
        #: The control path sends a sample of the plant to a gate that wants one.
        self.wants_context = wants_context
        #: Public keys the service's receipts are checked against, raw Ed25519 in
        #: base64; empty when none was given, and then no signature is checked.
        self.receipt_keys: tuple[str, ...] = ()
        self.context_epoch = epoch
        #: What ties each decision reference to its request, for the outcome message.
        self._correlated: dict[str, dict[str, JsonValue]] = {}
        self.url = ""
        self.name = name
        self.profile = profile
        self.timeout_s = timeout_s
        #: A service that keeps state cannot be put back to an earlier instant.
        self.restorable = not stateful
        #: A service that works out for itself who may act is not sent the premise.
        self.derives_authority = derives_authority or verifies_mandates
        #: A service that authenticates the mandate itself is sent signed statements alone.
        self.verifies_mandates = verifies_mandates
        #: One per gate, and Agentic-Datacenter builds a gate per run and per replay.
        self.session = f"{name}-{os.getpid()}-{next(_SESSIONS)}"
        self.outcomes: dict[str, Outcome] = {}
        #: Why the service failed to give a usable answer, once per failed decision.
        self.failures: list[str] = []

    def _post(self, path: str, body: Mapping[str, JsonValue]) -> Any:
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body, sort_keys=True).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _OPENER.open(request, timeout=self.timeout_s) as response:
            text = response.read(MAX_ANSWER_BYTES + 1)
        if len(text) > MAX_ANSWER_BYTES:
            raise ValueError(f"the answer is longer than {MAX_ANSWER_BYTES} bytes")
        return json.loads(text.decode("utf-8"))

    def _answer(
        self, body: Mapping[str, JsonValue]
    ) -> tuple[Verdict, tuple[str, ...], dict[str, Any]]:
        """Ask the service; on any failure, abstain and say why."""
        verdict, reasons, answer = self._ask(body)
        if not answer:
            self.failures.append(reasons[0])
        return verdict, reasons, answer

    def _ask(
        self, body: Mapping[str, JsonValue]
    ) -> tuple[Verdict, tuple[str, ...], dict[str, Any]]:
        try:
            answer = self._post("/evaluate", body)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            return Verdict.ABSTAIN, (f"gate service did not answer: {_text(error)}",), {}
        if not isinstance(answer, dict) or answer.get("schema") != SCHEMA:
            return Verdict.ABSTAIN, (f"gate service answered without schema {SCHEMA}",), {}
        verdict = read_verdict(answer.get("verdict"))
        if verdict is None:
            return (
                Verdict.ABSTAIN,
                (f"gate service gave no valid verdict: {_text(answer.get('verdict'))}",),
                {},
            )
        reasons = answer.get("reasons")
        if not isinstance(reasons, list) or not reasons:
            return Verdict.ABSTAIN, ("gate service gave a verdict without reasons",), {}
        return verdict, tuple(_text(r) for r in reasons[:_MAX_REASONS]), answer

    def evaluate(
        self, proposal: Proposal, premises: PremiseSnapshot, actor: AuthenticatedActor
    ) -> Decision:
        """Ask the service for a verdict and bind it to the instance's own writes."""
        body = request_body(proposal, premises, actor, self.session, self.context_epoch)
        verdict, reasons, answer = self._answer(body)
        answered = verdict
        if verdict is Verdict.ALLOW and not premises.writes:
            verdict, reasons = Verdict.DENY, reasons + ("nothing that can be executed",)
        approvers = answer.get("required_approvers", 1 if verdict is Verdict.VERIFY else 0)
        if not isinstance(approvers, int) or approvers < 0:
            approvers = 1 if verdict is Verdict.VERIFY else 0
        if verdict is Verdict.VERIFY:
            approvers = max(approvers, 1)
        service_ref = answer.get("decision_id")
        given = answer.get("reason_codes")
        codes = given if isinstance(given, list) else []
        details = answer.get("reason_details")
        said = details.get("authority") if isinstance(details, dict) else None
        finding = answer.get("authority_holds")
        detail: dict[str, JsonValue] = {"served_by": self.url}
        native = answer.get("verdict")
        if native is not None and str(native) != verdict.value:
            detail["service_verdict"] = _text(native)
        if service_ref is not None:
            detail["service_decision_id"] = _text(service_ref)
        tie = body["correlation"]
        assert isinstance(tie, dict)
        detail.update(
            session=self.session, correlation_id=tie["correlation_id"],
            action_ref=tie["action_ref"], issued_at=tie["issued_at"],
        )  # fmt: skip
        if answer:
            # What the service said, before this binding's own checks (ADR 0036).
            detail["answered_verdict"] = answered.value
        receipt = receipt_kept(answer.get("receipt"))
        if receipt is not None:
            detail["service_receipt"] = receipt
        if self.receipt_keys:
            # Only for a gate whose key was given before the run (ADR 0041).
            detail["receipt_signature"] = attest.receipt_signature(
                answer.get("receipt"), self.receipt_keys
            )
        writes = premises.writes if verdict is Verdict.ALLOW else ()
        ref = decision_ref(
            self.name,
            proposal.proposal_id,
            proposal.contract_id,
            verdict.value,
            [[w.tag, w.value] for w in writes],
            list(reasons),
            premises.time_ms,
        )
        self._correlated[ref] = {
            "correlation_id": tie["correlation_id"], "action_ref": tie["action_ref"],
            "proposal_id": proposal.proposal_id, "verdict": verdict.value,
        }  # fmt: skip
        return Decision(
            verdict,
            ref,
            proposal.contract_id,
            writes,
            premises.time_ms + DECISION_TTL_MS,
            ref[-12:],
            reasons,
            required_approvers=approvers,
            escalated=bool(answer.get("escalated", False)),
            detail=detail,
            reason_codes=tuple(_text(c) for c in codes[:_MAX_REASONS] if isinstance(c, str) and c),
            authority_detail=_text(said) if isinstance(said, str) else "",
            authority_holds=finding if isinstance(finding, bool) else None,
        )

    def record_outcome(self, decision_ref: str, outcome: Outcome) -> None:
        """Keep the outcome and tell the service. A service that does not listen changes nothing."""
        self.outcomes[decision_ref] = outcome
        body: dict[str, JsonValue] = {
            "schema": SCHEMA,
            "session": self.session,
            "decision_ref": decision_ref,
            "status": outcome.status if outcome.status in OUTCOMES else "UNKNOWN",
            "detail": outcome.detail,
            # The same correlation_id and action_ref as the evaluate request (ADR 0036);
            # null for an outcome of a decision this service was not asked about.
            **self._correlated.get(decision_ref, dict.fromkeys(OUTCOME_CORRELATION)),
        }
        try:
            self._post("/outcome", body)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass


class CommandGate(HttpGate):
    """The same binding, with each request answered by a command instead of a URL.

    The command gets ``{"endpoint": "/evaluate" or "/outcome", "request": ...}``
    on standard input and writes the answer as JSON on standard output. Exit
    status 2 with nothing written means ``DENY``, as a hook that blocks would.
    """

    def __init__(
        self,
        command: str,
        name: str = "command_gate",
        profile: str = "DECISION_ONLY",
        timeout_s: float = 10.0,
        stateful: bool = False,
        derives_authority: bool = False,
        wants_context: bool = False,
        epoch: str = EPOCH,
        verifies_mandates: bool = False,
    ) -> None:
        """Create the binding. The command is not run until the first decision."""
        if not command.strip():
            raise HttpGateConfigError("ADC_GATE_COMMAND is not set")
        self._setup(
            name, profile, timeout_s, stateful, derives_authority, wants_context, epoch,
            verifies_mandates,
        )  # fmt: skip
        self.command = shlex.split(command)
        self.url = "command:" + self.command[0]

    def _post(self, path: str, body: Mapping[str, JsonValue]) -> Any:
        message = json.dumps({"endpoint": path, "request": body}, sort_keys=True)
        try:
            done = subprocess.run(  # noqa: S603 - the command the user configured
                self.command, input=message, capture_output=True, text=True,
                timeout=self.timeout_s, check=False,
            )  # fmt: skip
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(f"no answer within {self.timeout_s:g} s") from error
        if done.returncode == 2 and not done.stdout.strip():
            reason = done.stderr.strip() or "the command refused (exit status 2)"
            return {"schema": SCHEMA, "verdict": "DENY", "reasons": [reason]}
        if done.returncode != 0:
            raise OSError(f"the command exited with status {done.returncode}")
        return json.loads(done.stdout) if path == "/evaluate" else None


def _common(env: Mapping[str, str], default_name: str) -> dict[str, Any]:
    return {
        "name": env.get("ADC_GATE_NAME", default_name),
        "profile": env.get("ADC_GATE_PROFILE", "DECISION_ONLY"),
        "timeout_s": float(env.get("ADC_GATE_TIMEOUT_S", "10")),
        "stateful": env.get("ADC_GATE_STATEFUL", "") == "1",
        "derives_authority": env.get("ADC_GATE_DERIVES_AUTHORITY", "") == "1",
        "verifies_mandates": env.get("ADC_GATE_VERIFIES_MANDATES", "") == "1",
        "wants_context": env.get("ADC_GATE_CONTEXT", "") == "1",
        "epoch": env.get("ADC_GATE_EPOCH", EPOCH),
    }


def from_environment(environ: Mapping[str, str] | None = None) -> HttpGate:
    """Build the binding from ``ADC_GATE_*`` variables."""
    env = os.environ if environ is None else environ
    gate = HttpGate(
        env.get("ADC_GATE_URL", ""),
        allow_remote=env.get("ADC_GATE_ALLOW_REMOTE", "") == "1",
        **_common(env, "http_gate"),
    )
    gate.receipt_keys = _receipt_keys(env)
    return gate


def _receipt_keys(env: Mapping[str, str]) -> tuple[str, ...]:
    """Return the public keys of ``ADC_GATE_RECEIPT_KEY``: one or more, separated by commas."""
    keys = tuple(k.strip() for k in env.get("ADC_GATE_RECEIPT_KEY", "").split(",") if k.strip())
    for key in keys:
        try:
            raw = base64.b64decode(key, validate=True)
        except ValueError:
            raw = b""
        if len(raw) != 32:
            raise HttpGateConfigError(
                "ADC_GATE_RECEIPT_KEY: each key is a raw Ed25519 public key, 32 bytes in base64"
            )
    return keys


def command_from_environment(environ: Mapping[str, str] | None = None) -> CommandGate:
    """Build the command binding from ``ADC_GATE_*`` variables."""
    env = os.environ if environ is None else environ
    gate = CommandGate(env.get("ADC_GATE_COMMAND", ""), **_common(env, "command_gate"))
    gate.receipt_keys = _receipt_keys(env)
    return gate


def create(contracts: object) -> HttpGate:
    """Build the gate for a benchmark run. The contract library is not used."""
    return from_environment()


def create_command(contracts: object) -> CommandGate:
    """Build the command gate for a benchmark run. The contract library is not used."""
    return command_from_environment()
