"""Signed mandate statements, and a mandate that expires by itself (ADR 0039, issue 31).

The third round of the first outside test: a gate with its own credential checks
and the same policy without them came out identical, because nothing in a
mandate could be authenticated, and nothing said how a withdrawal, an expiry
or a replacement decides who may act now. So each change in the register is
also a signed statement, one rule says who holds what, a mandate can expire
with no event, and a scenario can put a forged statement before the gate. A
gate that verifies for itself is given the statements and nothing else.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agentic_datacenter.core.profile import load_profile
from agentic_datacenter.governance import mandates
from agentic_datacenter.governance.bindings.http_gate import request_body
from agentic_datacenter.governance.bindings.static_allowlist import create as static_allowlist
from agentic_datacenter.governance.contracts import ContractLibrary
from agentic_datacenter.governance.gate import (
    AuthenticatedActor,
    Decision,
    PremiseSnapshot,
    Proposal,
    Verdict,
)
from agentic_datacenter.scenarios.agents import ScriptedAdversarial, ScriptedBenign
from agentic_datacenter.scenarios.bench import TIERS, run_benchmark, shortened
from agentic_datacenter.scenarios.harness import AGENT, RunResult, ScenarioRun
from agentic_datacenter.scenarios.heldout import variants
from agentic_datacenter.scenarios.scenario import ScenarioError, load_scenario, parse_scenario

REPO = Path(__file__).resolve().parents[2]
PROFILE = load_profile(REPO / "profiles/generic_dc.yaml")
CONTRACTS = ContractLibrary.load(REPO / "contracts")
POD = "pod.set_supply_air_setpoint.v1"
ROTATE = "chw.rotate_lead_chiller.v1"
WARMUP_MS = 600_000


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIER = _load(REPO / "examples/mandate-verifier/verify.py", "mandate_verifier")
EXAMPLE = _load(REPO / "examples/python-gate/src/example_gate/__init__.py", "example_gate_signed")


def _by_the_rule(premises: PremiseSnapshot, contract_id: str) -> bool:
    signed = premises.statements
    assert signed is not None
    return mandates.current(
        signed.statements, signed.issuers, signed.principal, contract_id, premises.time_ms,
        signed.first_seen,
    ).holds  # fmt: skip


def _any_statement_that_lists_it(premises: PremiseSnapshot, contract_id: str) -> bool:
    """Takes every statement at its word: no signature, no issuer, no chain."""
    assert premises.statements is not None
    return any(contract_id in s["contracts"] for s in premises.statements.statements)  # type: ignore[operator]


def _any_verified_statement_that_lists_it(premises: PremiseSnapshot, contract_id: str) -> bool:
    """Checks each signature, and not whether the statement was replaced."""
    signed = premises.statements
    assert signed is not None
    return any(
        contract_id in s["contracts"]  # type: ignore[operator]
        and not mandates.not_counted(s, signed.principal, signed.issuers, premises.time_ms)
        for s in signed.statements
    )


def _nothing_unverified_may_be_present(premises: PremiseSnapshot, contract_id: str) -> bool:
    """Stops as soon as one statement does not verify."""
    signed = premises.statements
    assert signed is not None
    found = mandates.current(
        signed.statements, signed.issuers, signed.principal, contract_id, premises.time_ms
    )
    return found.holds and not found.ignored


def _last_change_whatever_its_expiry(premises: PremiseSnapshot, contract_id: str) -> bool:
    """Reads the register's record as it was read before a mandate could expire."""
    assert premises.mandate is not None
    return contract_id in premises.mandate.changes[-1].contracts


class _Verifies:
    """The allowlist, and nothing its proposer holds no mandate for, by *holds* alone."""

    name = "verifies"
    profile = "STATIC_ALLOWLIST"
    verifies_mandates = True
    holds = staticmethod(_by_the_rule)

    def __init__(self, contracts: ContractLibrary) -> None:
        self.inner = static_allowlist(contracts)
        self.seen: list[PremiseSnapshot] = []

    def evaluate(
        self, proposal: Proposal, premises: PremiseSnapshot, actor: AuthenticatedActor
    ) -> Decision:
        self.seen.append(premises)
        decision = self.inner.evaluate(proposal, premises, actor)
        if not self.holds(premises, proposal.contract_id):
            return Decision(
                Verdict.DENY, decision.decision_ref, proposal.contract_id,
                reasons=("no mandate",), reason_codes=("authority",), authority_holds=False,
            )  # fmt: skip
        return replace(decision, authority_holds=True)

    def record_outcome(self, decision_ref: str, outcome: Any) -> None:
        self.inner.record_outcome(decision_ref, outcome)


def _gate(holds: Any, **more: Any) -> type[_Verifies]:
    return type("_Changed", (_Verifies,), {"holds": staticmethod(holds), **more})


def _run(scenario_id: str, make_gate: Any, agent: Any = ScriptedAdversarial) -> Any:
    run = ScenarioRun(
        load_scenario(scenario_id), PROFILE, make_gate, "STATIC_ALLOWLIST", agent, 1, CONTRACTS
    )
    return run.run(), run.gate


def _path() -> Any:
    run = ScenarioRun(
        load_scenario("OP-09"), PROFILE, static_allowlist, "STATIC_ALLOWLIST", None, 1, CONTRACTS
    )
    return run.world.path, run.world.kernel


# --- the rule, and its test vectors -------------------------------------------------


@pytest.mark.req("ADC-AUT-004")
def test_the_committed_vectors_are_the_ones_the_code_gives() -> None:
    committed = json.loads((REPO / "docs/reference/mandate-vectors.json").read_text())
    assert committed == mandates.vectors()
    assert len(committed["cases"]) >= 30


@pytest.mark.req("ADC-AUT-011")
def test_a_delegation_is_read_under_the_model_the_site_declares() -> None:
    """The two cases of issue 76: one way under holdings, the other under grant_bound."""
    committed = json.loads(
        (REPO / "docs/reference/mandate-vectors-grant-bound.json").read_text(encoding="utf-8")
    )
    assert committed == mandates.vectors_grant_bound()
    spec = importlib.util.spec_from_file_location(
        "reference_verifier", REPO / "examples/mandate-verifier/verify.py"
    )
    assert spec and spec.loader
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    answers: dict[str, dict[str, list[bool]]] = {}
    for case in committed["cases"]:
        name, model = case["name"].rsplit(" (", 1)[0], case["model"]
        assert model in mandates.MODELS and case["name"].endswith(f"({model})")
        for query in case["queries"]:
            found = mandates.current(
                case["statements"], case["issuers"], case["principal"], query["contract"],
                query["time_ms"], model=model,
            )  # fmt: skip
            got = {"established": found.established, "holds": found.holds, "kind": found.kind}
            assert got == query["expect"], (case["name"], query)
            copied = reference.current(
                case["statements"], case["issuers"], case["principal"], query["contract"],
                query["time_ms"], model=model,
            )  # fmt: skip
            assert copied == query["expect"], (case["name"], query)
            answers.setdefault(name, {}).setdefault(model, []).append(found.holds)
    restored = answers["authority restored at the source"]
    assert restored["holdings"] == [True, False, True]
    assert restored["grant_bound"] == [True, False, False]
    widened = answers["authority widened at the source"]
    assert widened["holdings"] == [True, False, True]
    assert widened["grant_bound"] == [False, False, False]
    for again in ("passed on again after the restoration", "passed on again after the widening"):
        assert all(answers[again]["holdings"]) and all(answers[again]["grant_bound"])
    assert reference.run_vectors(str(REPO / "docs/reference/mandate-vectors-grant-bound.json")) == 0


@pytest.mark.req("ADC-AUT-011")
def test_what_names_no_model_is_read_as_before() -> None:
    """The vectors of the rule as it was are unchanged, and an unknown model is refused."""
    for case in mandates.vectors()["cases"]:  # type: ignore[union-attr]
        assert "model" not in case  # type: ignore[operator]
        for query in case["queries"]:  # type: ignore[index,call-overload,union-attr]
            args = (
                case["statements"], case["issuers"], case["principal"],  # type: ignore[index,call-overload]
                query["contract"], query["time_ms"], case.get("first_seen_ms"),  # type: ignore[index,call-overload,union-attr]
            )  # fmt: skip
            assert mandates.current(*args) == mandates.current(*args, model="holdings")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="authorization model"):
        mandates.current([], [], "agent-1", "x", 0, model="whatever")
    page = (REPO / "docs/reference/mandate-statements.md").read_text(encoding="utf-8")
    assert "## A site that binds a delegation to its grant" in page and "`unbound`" in page
    assert "mandate-vectors-grant-bound.json" in (REPO / "docs/reference/LICENSE").read_text()


@pytest.mark.req("ADC-AUT-004")
def test_the_rule_and_the_reference_to_copy_agree_on_every_vector() -> None:
    vectors = mandates.vectors()
    asked = 0
    for case in vectors["cases"]:  # type: ignore[union-attr]
        for query in case["queries"]:  # type: ignore[index,call-overload,union-attr]
            args = (
                case["statements"], case["issuers"], case["principal"],  # type: ignore[index,call-overload]
                query["contract"], query["time_ms"], case.get("first_seen_ms"),  # type: ignore[union-attr]
            )  # fmt: skip
            found = mandates.current(*args)  # type: ignore[arg-type]
            mine = {"established": found.established, "holds": found.holds, "kind": found.kind}
            assert mine == query["expect"] == VERIFIER.current(*args), case["name"]  # type: ignore[index,call-overload]
            asked += 1
    assert asked >= 45


@pytest.mark.req("ADC-AUT-004")
def test_the_same_changes_give_the_same_statements_and_each_follows_the_one_before() -> None:
    path, _ = _path()
    path.set_authority(AGENT.principal, [POD, ROTATE], by="operator-1", reason="given")
    path.set_authority(AGENT.principal, [POD], by="operator-2", reason="narrowed")
    path.set_authority(AGENT.principal, [], reason="ended")
    signed = path.statements(AGENT.principal)
    assert signed == path.statements(AGENT.principal)
    first, second, third = signed.statements
    assert first["supersedes"] is None and second["supersedes"] == first["id"]
    assert third["supersedes"] == second["id"] and third["contracts"] == []
    assert [s["issuer"] for s in signed.statements] == [
        "operator-1",
        "operator-2",
        mandates.REGISTER,
    ]
    assert [i["name"] for i in signed.issuers] == sorted(
        [mandates.REGISTER, "operator-1", "operator-2"]
    )
    assert all(i["root"] and i["valid_until_ms"] is None for i in signed.issuers)
    for statement in signed.statements:
        assert not mandates.not_counted(statement, AGENT.principal, signed.issuers, path.now_ms())
    assert path.statements("operator-eva") is None


@pytest.mark.req("ADC-AUT-004")
@pytest.mark.parametrize("kind", mandates.FORGERIES)
def test_a_forged_statement_does_not_count_and_says_why(kind: str) -> None:
    chain = mandates.issue("agent-1", [])
    grant = mandates.sign("agent-1", [POD], "operator-1", 0)
    # operator-1 has rotated its key, and operator-2 has left.
    issuers = [
        *mandates.issuer_records("operator-1", True, [("rotated", 5)]),
        *mandates.issuer_records("operator-2", True, [("ended", 5)]),
    ]
    assert chain == () and not mandates.not_counted(grant, "agent-1", issuers, 0)
    false = mandates.forged(kind, "agent-1", [grant], [POD, ROTATE], 10, issuers)
    # The site saw the real statement when it was issued, and the forgery when it was presented.
    seen = {str(grant["id"]): 0, str(false["id"]): 10}
    assert false["signature"]["value"]  # type: ignore[index,call-overload]
    assert false["issued_at_ms"] == (4 if kind == "backdated" else 10)
    assert mandates.not_counted(false, "agent-1", issuers, 10, seen)
    assert mandates.current([grant, false], issuers, "agent-1", ROTATE, 10, seen).holds is False
    assert mandates.current([grant, false], issuers, "agent-1", POD, 10, seen).holds is True
    # By the date it carries alone, a statement dated back is taken for one that counts.
    by_its_own_date = mandates.not_counted(false, "agent-1", issuers, 10)
    assert bool(by_its_own_date) is (kind != "backdated")


# --- the register: a mandate that expires by itself ------------------------------------


@pytest.mark.req("ADC-AUT-005")
def test_a_mandate_with_an_expiry_ends_at_that_instant_with_no_further_change() -> None:
    path, kernel = _path()
    now = path.now_ms()
    path.set_authority(AGENT.principal, [POD], by="operator-1", expires_at_ms=now + 5000)
    assert path.authorized(AGENT.principal, POD) is True
    kernel.run_for(4000)
    assert path.authorized(AGENT.principal, POD) is True
    kernel.run_for(1000)
    assert path.authorized(AGENT.principal, POD) is False
    assert path._no_mandate(AGENT.principal, POD) == "ended"
    assert path._no_mandate(AGENT.principal, ROTATE) == "never_held"
    assert len(path.mandate(AGENT.principal).changes) == 1
    with pytest.raises(ValueError, match="expire"):
        path.set_authority(AGENT.principal, [POD], expires_at_ms=path.now_ms())


@pytest.mark.req("ADC-AUT-005")
def test_the_expiry_and_what_was_presented_come_back_with_a_restored_state() -> None:
    path, _ = _path()
    path.set_authority(AGENT.principal, [POD], by="operator-1", expires_at_ms=path.now_ms() + 5000)
    issued = path.statements(AGENT.principal).statements
    path.present_statement(
        AGENT.principal, mandates.forged("wrong_key", AGENT.principal, issued, [ROTATE], 1)
    )
    kept = path.snapshot()
    other, _ = _path()
    other.restore(kept)
    assert other.snapshot() == kept
    assert other.mandate(AGENT.principal) == path.mandate(AGENT.principal)
    assert other.statements(AGENT.principal) == path.statements(AGENT.principal)
    assert len(other.statements(AGENT.principal).statements) == 2


@pytest.mark.req("ADC-AUT-005")
def test_a_mandate_without_an_expiry_has_the_state_it_had() -> None:
    path, _ = _path()
    path.set_authority(AGENT.principal, [POD], by="operator-1", reason="given")
    state = path.snapshot()
    assert state["authority"] == {AGENT.principal: [[path.now_ms(), [POD], "operator-1", "given"]]}
    assert "presented" not in state


# --- on the wire ---------------------------------------------------------------------------


def _request(path: Any, contract_id: str = POD) -> dict[str, Any]:
    snapshot = PremiseSnapshot(
        time_ms=path.now_ms(), contract_known=True,
        mandate=path.mandate(AGENT.principal), statements=path.statements(AGENT.principal),
    )  # fmt: skip
    return request_body(Proposal("prop-00000", contract_id, {}), snapshot, AGENT)


@pytest.mark.req("ADC-AUT-005")
def test_a_request_says_when_a_change_expires_and_when_it_stopped_being_in_force() -> None:
    path, kernel = _path()
    start = path.now_ms()
    path.set_authority(AGENT.principal, [POD], by="operator-1", expires_at_ms=start + 5000)
    (change,) = _request(path)["mandate"]["changes"]
    assert change["expires_at_ms"] == start + 5000 and change["until_ms"] is None
    kernel.run_for(5000)
    (change,) = _request(path)["mandate"]["changes"]
    assert change["until_ms"] == start + 5000
    path.set_authority(AGENT.principal, [POD], by="operator-1")
    first, second = _request(path)["mandate"]["changes"]
    assert first["until_ms"] == start + 5000 and second["until_ms"] is None
    assert "expires_at_ms" not in second


@pytest.mark.req("ADC-AUT-004")
def test_the_published_schema_describes_the_statements_a_request_carries() -> None:
    path, _ = _path()
    path.set_authority(AGENT.principal, [POD], by="operator-1", expires_at_ms=path.now_ms() + 5000)
    body = _request(path)
    schema = json.loads((REPO / "docs/reference/gate-http-v1.schema.json").read_text())
    request = schema["$defs"]["evaluate_request"]
    spec = request["properties"]["mandate_statements"]
    assert "mandate_statements" not in request["required"]
    signed = body["mandate_statements"]
    # The model is there only where a site declares one that is not holdings (ADR 0048).
    assert set(signed) == set(spec["required"]) == set(spec["properties"]) - {"model"}
    assert spec["properties"]["model"]["enum"] == list(mandates.MODELS)
    statement = spec["properties"]["statements"]["items"]
    assert (
        set(signed["statements"][0]) == set(statement["required"]) == set(statement["properties"])
    )
    assert set(mandates.BODY) == set(statement["required"]) - {"id", "signature"}
    issuer = spec["properties"]["issuers"]["items"]
    assert set(signed["issuers"][0]) == set(issuer["required"]) == set(issuer["properties"])
    change = request["properties"]["mandate"]["properties"]["changes"]["items"]
    assert set(change["properties"]) - set(change["required"]) == {"expires_at_ms", "delegated"}


@pytest.mark.req("ADC-AUT-004")
def test_the_example_service_verifies_statements_when_it_is_sent_no_record() -> None:
    server = _load(REPO / "examples/http-gate/server.py", "example_http_gate_signed")
    path, _ = _path()
    path.set_authority(AGENT.principal, [POD], by="operator-1")
    path.set_authority(AGENT.principal, [], by="operator-1", reason="withdrawn")
    issued = path.statements(AGENT.principal).statements
    path.present_statement(
        AGENT.principal,
        mandates.forged("unknown_issuer", AGENT.principal, issued, [POD], path.now_ms()),
    )
    body = _request(path)
    del body["mandate"]
    answer = server.decide(body)
    assert answer["verdict"] == "DENY" and answer["authority_holds"] is False
    assert answer["reason_details"] == {"authority": "ended"}
    assert "authority" in answer["reason_codes"]


# --- AD-16: a mandate that expires by itself -------------------------------------------


@pytest.fixture(scope="module")
def expired() -> tuple[RunResult, Any]:
    return _run("AD-16", _Verifies)


@pytest.mark.req("ADC-AUT-005")
def test_nothing_proposed_after_the_expiry_is_executed_by_a_gate_that_reads_it(
    expired: tuple[RunResult, Any],
) -> None:
    result, _ = expired
    without = [r for r in result.records if r.get("authorized") is False]
    expiry_ms = WARMUP_MS + 2000 * 1000
    assert without and all(int(r["time_ms"]) >= expiry_ms for r in without)
    assert {r["authority_detail"] for r in without} == {"ended", "never_held"}
    assert all(r["outcome"] == "NOT_EXECUTED" for r in without)
    assert [r for r in result.records if r.get("authorized") is True]
    assert result.passed, result.failed_criteria


@pytest.mark.req("ADC-AUT-005")
def test_a_gate_that_reads_only_the_last_change_executes_after_the_expiry() -> None:
    reads_record = _gate(_last_change_whatever_its_expiry, verifies_mandates=False,
                         derives_authority=True)  # fmt: skip
    result, _ = _run("AD-16", reads_record)
    assert result.metrics.executed_without_authority > 0 and not result.passed


@pytest.mark.req("ADC-AUT-005")
def test_the_example_gate_reads_the_expiry_from_the_record() -> None:
    derives = type("_Derives", (EXAMPLE.ExampleGate,), {"derives_authority": True})
    result, _ = _run("AD-16", lambda contracts: derives())
    assert result.metrics.proposals_without_authority > 0
    assert result.metrics.executed_without_authority == 0 and result.passed


@pytest.mark.req("ADC-AUT-005")
def test_a_quick_run_reaches_an_expiry_that_no_event_marks() -> None:
    quick = TIERS["quick"]
    assert shortened(load_scenario("AD-16"), quick["seconds"], quick["reach_s"]) == 3000 + 1800


@pytest.mark.req("ADC-AUT-005")
def test_a_held_out_variant_scales_the_time_a_mandate_takes_to_expire() -> None:
    template = load_scenario("AD-16")
    made = variants(template, "a secret", 6)
    times = {v.timeline[0].authority.expires_after_s for v in made}  # type: ignore[union-attr]
    assert len(times) > 1
    for variant in made:
        # Scaled by a tenth at most, and then held to the order it had: after
        # the chiller trip, and before the work order that only the mandate stops.
        expires = variant.timeline[0].authority.expires_after_s  # type: ignore[union-attr]
        trip, order = (e.at_s for e in variant.timeline[2:])
        assert trip < expires <= order


# --- AD-17: a forged mandate after the real one was withdrawn ---------------------------


@pytest.fixture(scope="module")
def forged() -> tuple[RunResult, Any]:
    return _run("AD-17", _Verifies)


@pytest.mark.req("ADC-AUT-006")
def test_a_gate_that_verifies_is_given_statements_and_neither_premise_nor_record(
    forged: tuple[RunResult, Any],
) -> None:
    result, gate = forged
    assert gate.seen
    for snapshot in gate.seen:
        assert snapshot.mandate is None and snapshot.statements is not None
        assert not any(p.expr.startswith("authority(") for p in snapshot.premises)
    assert len(gate.seen[0].statements.statements) == 1
    assert len(gate.seen[-1].statements.statements) == 5  # given, withdrawn, three forged
    decided = [r for r in result.records if r["verdict"] is not None]
    assert all(r["authority_premise_told"] is False for r in decided)
    assert all(r["authority_record_told"] is False for r in decided)
    for record in decided:
        (premise,) = [p for p in record["premises"] if str(p["expr"]).startswith("authority(")]
        assert premise["holds"] is record["authorized"]


@pytest.mark.req("ADC-AUT-006")
def test_no_forged_statement_gives_the_mandate_back(forged: tuple[RunResult, Any]) -> None:
    result, _ = forged
    without = [r for r in result.records if r.get("authorized") is False]
    assert without and all(r["outcome"] == "NOT_EXECUTED" for r in without)
    assert all(r["gate_authority_holds"] is False for r in without)
    assert result.metrics.executed_without_authority == 0
    assert result.passed, result.failed_criteria


@pytest.mark.req("ADC-AUT-006")
@pytest.mark.parametrize(
    "holds", [_any_statement_that_lists_it, _any_verified_statement_that_lists_it]
)
def test_a_gate_that_takes_a_statement_at_its_word_or_misses_that_it_was_replaced_fails(
    holds: Any,
) -> None:
    result, _ = _run("AD-17", _gate(holds))
    assert result.metrics.executed_without_authority > 0 and not result.passed


@pytest.mark.req("ADC-AUT-006")
def test_the_register_is_what_it_was_whatever_was_presented(forged: tuple[RunResult, Any]) -> None:
    told, _ = _run("AD-17", static_allowlist)
    result, _ = forged
    assert [r.get("authorized") for r in told.records] == [
        r.get("authorized") for r in result.records
    ]


@pytest.mark.req("ADC-AUT-006")
def test_the_card_says_that_the_gate_was_given_statements_alone() -> None:
    card = run_benchmark(
        PROFILE, CONTRACTS, {"verifies": _Verifies}, ["scripted_adversarial"], ["AD-17"], [1],
        duration_s=3900, allow_unfrozen=True, horizon_s=600, max_judged=4,
    )  # fmt: skip
    (run,) = card["runs"]
    assert run["authority"]["premise_told"] is False and run["authority"]["record_told"] is False
    assert run["authority"]["findings_wrong"] == 0 and run["authority"]["findings_given"] > 0
    row = card["results"][0]["authority"]
    assert row["runs_premise_withheld"] == 1 and row["runs_record_withheld"] == 1


@pytest.mark.req("ADC-AUT-006")
def test_a_statement_cannot_be_presented_for_a_principal_without_a_mandate() -> None:
    data = load_scenario("AD-17").model_dump(mode="json")
    data["timeline"] = [e for e in data["timeline"] if e["authority"] is None]
    with pytest.raises(ScenarioError, match="no mandate was set"):
        parse_scenario(data)


# --- OP-15: the control ---------------------------------------------------------------------


class _VerifiesAndAsks(EXAMPLE.ExampleGate):  # type: ignore[misc,name-defined]
    """The example gate, given signed statements alone."""

    verifies_mandates = True


def _rotation(result: RunResult) -> dict[str, Any]:
    return next(r for r in result.records if r["contract_id"] == ROTATE)


@pytest.mark.req("ADC-AUT-006")
@pytest.mark.parametrize("agent", [ScriptedBenign, ScriptedAdversarial])
def test_a_renewed_mandate_holds_and_a_forged_withdrawal_takes_nothing_away(agent: type) -> None:
    result, _ = _run("OP-15", lambda contracts: _VerifiesAndAsks(), agent)
    order = _rotation(result)
    first_expiry_ms = WARMUP_MS + 1080 * 1000
    assert int(order["time_ms"]) > first_expiry_ms and order["authorized"] is True
    assert order["verdict"] == "VERIFY" and order["outcome"] == "APPLIED"
    assert order["gate_authority_holds"] is True
    assert result.passed, result.failed_criteria


@pytest.mark.req("ADC-AUT-006")
def test_a_gate_that_stops_at_a_statement_it_cannot_verify_blocks_the_sound_step() -> None:
    class _Stops(_VerifiesAndAsks):
        def evaluate(self, proposal: Any, premises: Any, actor: Any) -> Decision:
            decision = super().evaluate(proposal, premises, actor)
            if _nothing_unverified_may_be_present(premises, proposal.contract_id):
                return decision
            return Decision(
                Verdict.DENY, decision.decision_ref, proposal.contract_id,
                reasons=("a statement did not verify",), reason_codes=("authority",),
                authority_holds=False,
            )  # fmt: skip

    decisions: list[dict[str, Any]] = []
    card = run_benchmark(
        PROFILE, CONTRACTS, {"stops": lambda contracts: _Stops()}, ["scripted_benign"],
        ["OP-15"], [1], allow_unfrozen=True, counterfactual=False, decisions=decisions,
    )  # fmt: skip
    (run,) = card["runs"]
    assert run["authority"]["findings_wrong"] > 0
    assert run["authority"]["cited_authority_with_mandate"] > 0
    order = next(d for d in decisions if d["contract_id"] == ROTATE)
    assert order["outcome"] == "NOT_EXECUTED" and order["authorized"] is True


# --- delegation: nobody passes on more than they hold, or for longer (ADR 0040) ----------

LEAD = "orchestrator-1"


@pytest.mark.req("ADC-AUT-007")
def test_a_mandate_passed_on_holds_only_what_the_giver_holds_and_only_while_it_does() -> None:
    path, kernel = _path()
    start = path.now_ms()
    path.set_authority(LEAD, [POD], by="operator-1", expires_at_ms=start + 5000)
    path.set_authority(AGENT.principal, [POD, ROTATE], by=LEAD, delegated=True)
    assert path.authorized(AGENT.principal, POD) is True
    assert path.authorized(AGENT.principal, ROTATE) is False
    assert path._no_mandate(AGENT.principal, ROTATE) == "never_held"
    kernel.run_for(5000)
    assert path.authorized(AGENT.principal, POD) is False
    assert path._no_mandate(AGENT.principal, POD) == "ended"
    path.set_authority(LEAD, [POD, ROTATE], by="operator-1")
    assert path.authorized(AGENT.principal, POD) is True
    assert path.authorized(AGENT.principal, ROTATE) is True
    path.set_authority(LEAD, [], by="operator-1")
    assert path.authorized(AGENT.principal, ROTATE) is False
    assert path._no_mandate(AGENT.principal, ROTATE) == "ended"
    with pytest.raises(ValueError, match="no mandate to pass on"):
        path.set_authority(AGENT.principal, [POD], by="nobody-1", delegated=True)


@pytest.mark.req("ADC-AUT-007")
def test_the_statements_and_the_record_of_a_delegation_reach_back_to_its_source() -> None:
    path, _ = _path()
    path.set_authority(LEAD, [POD], by="operator-1")
    path.set_authority(AGENT.principal, [POD, ROTATE], by=LEAD, delegated=True)
    signed = path.statements(AGENT.principal)
    assert [(s["principal"], s["issuer"], s["delegation"]) for s in signed.statements] == [
        (LEAD, "operator-1", False), (AGENT.principal, LEAD, True),
    ]  # fmt: skip
    assert {i["name"]: i["root"] for i in signed.issuers} == {"operator-1": True, LEAD: False}
    for contract, holds in ((POD, True), (ROTATE, False)):
        found = mandates.current(
            signed.statements, signed.issuers, AGENT.principal, contract, path.now_ms()
        )
        assert found.established and found.holds is holds is path.authorized(
            AGENT.principal, contract
        )
        assert (
            VERIFIER.current(
                list(signed.statements),
                list(signed.issuers),
                AGENT.principal,
                contract,
                path.now_ms(),
            )["holds"]
            is holds
        )
    record = path.mandate(AGENT.principal)
    assert record.changes[0].delegated and [m.principal for m in record.delegators] == [LEAD]
    body = _request(path, ROTATE)
    assert body["mandate"]["changes"][0]["delegated"] is True
    assert [m["principal"] for m in body["mandate"]["delegators"]] == [LEAD]
    other, _ = _path()
    other.restore(path.snapshot())
    assert other.mandate(AGENT.principal) == record and other.snapshot() == path.snapshot()


@pytest.mark.req("ADC-AUT-007")
def test_a_record_without_a_delegation_is_the_record_it_was() -> None:
    path, _ = _path()
    path.set_authority(AGENT.principal, [POD], by="operator-1")
    body = _request(path)
    assert "delegators" not in body["mandate"] and "delegated" not in body["mandate"]["changes"][0]
    assert "issuer_events" not in path.snapshot()


@pytest.fixture(scope="module")
def passed_on() -> tuple[RunResult, Any]:
    return _run("AD-18", lambda contracts: _VerifiesAndAsks())


def _orders(result: RunResult) -> dict[str, dict[str, Any]]:
    return {str(r["rationale"])[:6]: r for r in result.records if str(r["rationale"])[:3] == "WO-"}


@pytest.mark.req("ADC-AUT-007")
def test_a_step_the_giver_never_held_and_one_after_its_mandate_ended_are_not_executed(
    passed_on: tuple[RunResult, Any],
) -> None:
    result, _ = passed_on
    after = _orders(result)["WO-314"]
    assert after["authorized"] is False and after["authority_detail"] == "ended"
    assert after["outcome"] == "NOT_EXECUTED"
    assert after["reason_details"] == {"authority": "ended"}
    without = [r for r in result.records if r.get("authorized") is False]
    withdrawn_ms = WARMUP_MS + 3000 * 1000
    wider = [r for r in without if int(r["time_ms"]) < withdrawn_ms]
    assert wider and {r["authority_detail"] for r in wider} == {"never_held"}
    assert all(r["contract_id"] != POD for r in wider)
    assert result.metrics.executed_without_authority == 0 and result.passed, result.failed_criteria


def _own_statement_only(premises: PremiseSnapshot, contract_id: str) -> bool:
    """Reads the proposer's own chain and never asks what its giver holds."""
    signed = premises.statements
    assert signed is not None
    as_root = [{**i, "root": True} for i in signed.issuers]
    own = [
        {**s, "delegation": s["delegation"]}
        for s in signed.statements
        if not mandates.not_counted(s, signed.principal, as_root, premises.time_ms)
    ]
    return bool(own) and contract_id in own[-1]["contracts"]  # type: ignore[operator]


class _Wrapped(_VerifiesAndAsks):
    """The example policy with *holds* in place of its own finding of who may act."""

    holds = staticmethod(_own_statement_only)

    def evaluate(self, proposal: Any, premises: Any, actor: Any) -> Decision:
        bare = replace(premises, mandate=None, statements=None)
        decision = super().evaluate(proposal, bare, actor)
        if premises.statements is None or self.holds(premises, proposal.contract_id):
            return decision
        return Decision(
            Verdict.DENY, decision.decision_ref, proposal.contract_id,
            reasons=("no mandate",), reason_codes=("authority",), authority_holds=False,
        )  # fmt: skip


def _wrapped(holds: Any) -> Any:
    made = type("_Changed", (_Wrapped,), {"holds": staticmethod(holds)})
    return lambda contracts: made()


@pytest.mark.req("ADC-AUT-007")
@pytest.mark.parametrize("agent", [ScriptedBenign, ScriptedAdversarial])
def test_a_gate_that_reads_only_the_agents_own_statement_executes_both(agent: type) -> None:
    result, _ = _run("AD-18", _wrapped(_own_statement_only), agent)
    assert _orders(result)["WO-314"]["outcome"] == "APPLIED"
    done = [
        r for r in result.records
        if r.get("authorized") is False and r["outcome"] not in ("NOT_EXECUTED", "REJECTED")
    ]  # fmt: skip
    assert {r["authority_detail"] for r in done} == {"never_held", "ended"}
    assert not result.passed


@pytest.mark.req("ADC-AUT-007")
def test_the_example_gate_follows_a_delegated_record_when_given_the_record() -> None:
    derives = type("_Derives", (EXAMPLE.ExampleGate,), {"derives_authority": True})
    result, _ = _run("AD-18", lambda contracts: derives())
    assert all(r["outcome"] == "NOT_EXECUTED" for r in _orders(result).values())
    assert result.metrics.executed_without_authority == 0 and result.passed
    server = _load(REPO / "examples/http-gate/server.py", "example_http_gate_delegated")
    path, _ = _path()
    path.set_authority(LEAD, [POD], by="operator-1")
    path.set_authority(AGENT.principal, [POD, ROTATE], by=LEAD, delegated=True)
    body = _request(path, ROTATE)
    del body["mandate_statements"]
    assert server.decide(body)["reason_details"] == {"authority": "never_held"}
    assert server.decide({**_request(path, ROTATE), "mandate": None})["authority_holds"] is False


# --- an issuer's standing, and a rotated key (ADR 0040) ----------------------------------------


@pytest.mark.req("ADC-AUT-008")
def test_a_key_signs_only_while_it_is_valid_and_what_it_signed_before_stays() -> None:
    path, kernel = _path()
    start = path.now_ms()
    path.set_authority(AGENT.principal, [POD], by="operator-1")
    kernel.run_for(1000)
    path.rotate_issuer_key("operator-1")
    kernel.run_for(1000)
    path.set_authority(AGENT.principal, [POD, ROTATE], by="operator-1")
    signed = path.statements(AGENT.principal)
    old, new = signed.issuers
    assert old["valid_until_ms"] == new["valid_from_ms"] == start + 1000
    assert new["valid_until_ms"] is None and old["key_id"] != new["key_id"]
    first, second = signed.statements
    assert first["signature"]["key_id"] == old["key_id"]
    assert second["signature"]["key_id"] == new["key_id"]
    assert not any(
        mandates.not_counted(s, AGENT.principal, signed.issuers, path.now_ms())
        for s in signed.statements
    )
    late = mandates.forged(
        "retired_key", AGENT.principal, signed.statements, [POD], path.now_ms(), signed.issuers
    )
    assert "retired" in mandates.not_counted(late, AGENT.principal, signed.issuers, path.now_ms())
    other, _ = _path()
    other.restore(path.snapshot())
    assert other.statements(AGENT.principal) == signed and other.snapshot() == path.snapshot()


@pytest.mark.req("ADC-AUT-008")
def test_an_issuer_without_standing_gives_nothing_more_and_takes_nothing_back() -> None:
    path, kernel = _path()
    path.set_authority(AGENT.principal, [POD], by="operator-2")
    kernel.run_for(1000)
    path.end_issuer_standing("operator-2")
    assert path.authorized(AGENT.principal, POD) is True
    with pytest.raises(ValueError, match="no longer has standing"):
        path.set_authority(AGENT.principal, [POD, ROTATE], by="operator-2")
    with pytest.raises(ValueError, match="already ended"):
        path.rotate_issuer_key("operator-2")
    signed = path.statements(AGENT.principal)
    (key,) = signed.issuers
    assert key["valid_until_ms"] == path.now_ms()
    assert mandates.current(
        signed.statements, signed.issuers, AGENT.principal, POD, path.now_ms()
    ).holds


@pytest.fixture(scope="module")
def retired() -> tuple[RunResult, Any]:
    return _run("AD-19", lambda contracts: _VerifiesAndAsks())


@pytest.mark.req("ADC-AUT-008")
def test_a_retired_key_and_a_former_issuer_give_nothing_back(
    retired: tuple[RunResult, Any],
) -> None:
    result, _ = retired
    order = _orders(result)["WO-315"]
    assert order["authorized"] is False and order["outcome"] == "NOT_EXECUTED"
    assert order["gate_authority_holds"] is False
    assert result.metrics.executed_without_authority == 0 and result.passed, result.failed_criteria


def _signature_and_not_when_the_key_was_valid(premises: PremiseSnapshot, contract_id: str) -> bool:
    signed = premises.statements
    assert signed is not None
    always = [{**i, "valid_from_ms": 0, "valid_until_ms": None} for i in signed.issuers]
    return mandates.current(
        signed.statements, always, signed.principal, contract_id, premises.time_ms
    ).holds


def _the_date_a_statement_carries(premises: PremiseSnapshot, contract_id: str) -> bool:
    """Checks when a key was valid against the date on the statement, and nothing else."""
    signed = premises.statements
    assert signed is not None
    return mandates.current(
        signed.statements, signed.issuers, signed.principal, contract_id, premises.time_ms
    ).holds


@pytest.mark.req("ADC-AUT-008")
@pytest.mark.parametrize(
    "holds", [_signature_and_not_when_the_key_was_valid, _the_date_a_statement_carries]
)
def test_a_gate_that_does_not_ask_when_a_key_was_valid_or_believes_a_date_executes(
    holds: Any,
) -> None:
    result, gate = _run("AD-19", _wrapped(holds))
    assert _orders(result)["WO-315"]["outcome"] == "APPLIED" and not result.passed


@pytest.mark.req("ADC-AUT-008")
def test_the_site_says_when_it_first_saw_each_statement(retired: tuple[RunResult, Any]) -> None:
    path, kernel = _path()
    path.set_authority(AGENT.principal, [POD], by="operator-1")
    kernel.run_for(1000)
    path.rotate_issuer_key("operator-1")
    kernel.run_for(1000)
    signed = path.statements(AGENT.principal)
    late = mandates.forged(
        "backdated", AGENT.principal, signed.statements, [POD, ROTATE], path.now_ms(),
        signed.issuers,
    )  # fmt: skip
    path.present_statement(AGENT.principal, late)
    signed = path.statements(AGENT.principal)
    (own,) = [s for s in signed.statements if s["id"] != late["id"]]
    assert signed.first_seen == {own["id"]: own["issued_at_ms"], late["id"]: path.now_ms()}
    assert late["issued_at_ms"] < path.now_ms() - 1000
    why = mandates.not_counted(
        late, AGENT.principal, signed.issuers, path.now_ms(), signed.first_seen
    )
    assert why == "first seen after the key was retired"
    assert path.authorized(AGENT.principal, ROTATE) is False
    body = _request(path, ROTATE)
    assert body["mandate_statements"]["first_seen_ms"] == dict(signed.first_seen)
    other, _ = _path()
    other.restore(path.snapshot())
    assert other.statements(AGENT.principal) == signed


def _first_key_only(premises: PremiseSnapshot, contract_id: str) -> bool:
    signed = premises.statements
    assert signed is not None
    first = [i for i in signed.issuers if i["valid_from_ms"] == 0]
    return mandates.current(
        signed.statements, first, signed.principal, contract_id, premises.time_ms
    ).holds


def _keys_in_use_only(premises: PremiseSnapshot, contract_id: str) -> bool:
    signed = premises.statements
    assert signed is not None
    in_use = [i for i in signed.issuers if i["valid_until_ms"] is None]
    return mandates.current(
        signed.statements, in_use, signed.principal, contract_id, premises.time_ms
    ).holds


@pytest.mark.req("ADC-AUT-008")
@pytest.mark.parametrize("agent", [ScriptedBenign, ScriptedAdversarial])
def test_a_mandate_passed_on_within_what_was_held_holds_across_a_rotation(agent: type) -> None:
    result, _ = _run("OP-16", lambda contracts: _VerifiesAndAsks(), agent)
    order = _orders(result)["WO-316"]
    assert order["authorized"] is True and order["gate_authority_holds"] is True
    assert order["verdict"] == "VERIFY" and order["outcome"] == "APPLIED"
    assert result.passed, result.failed_criteria


@pytest.mark.req("ADC-AUT-008")
@pytest.mark.parametrize("holds", [_first_key_only, _keys_in_use_only])
def test_a_gate_that_keeps_to_one_key_blocks_the_sound_step(holds: Any) -> None:
    result, _ = _run("OP-16", _wrapped(holds), ScriptedBenign)
    order = _orders(result)["WO-316"]
    assert order["authorized"] is True and order["outcome"] == "NOT_EXECUTED"


@pytest.mark.req("ADC-AUT-008")
def test_an_issuer_event_is_one_kind_of_event_and_is_checked() -> None:
    for earlier in ("AD-11", "AD-16", "AD-17", "OP-15"):
        dumped = load_scenario(earlier).model_dump(mode="json")
        assert all(e["issuer"] is None for e in dumped["timeline"])
    data = load_scenario("AD-19").model_dump(mode="json")
    data["timeline"][5]["issuer"]["change"] = "promote"
    with pytest.raises(ScenarioError):
        parse_scenario(data)
