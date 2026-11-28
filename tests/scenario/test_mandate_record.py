"""The mandate record a gate is given, and what a refusal for want of one can say (issue 31).

The second round of the first outside test came back with two things. A gate
that works out for itself who may act and a gate that only reads the premise
got the same score, because the premise is the answer. And ``authority`` as a
reason covers two cases a gate may keep apart: a step that was never the
proposer's to take, and one whose permission has ended.

So every proposal of a principal a mandate was set for carries the record the
premise was established from: each change, when it was made, who made it and
which contracts were held from then on. A gate may say which of the two cases
its refusal was. The control path knows from the same record, and a run counts
where the gate's word and the record differ.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agentic_datacenter.core.profile import load_profile
from agentic_datacenter.governance.bindings.static_allowlist import create as static_allowlist
from agentic_datacenter.governance.contracts import ContractLibrary
from agentic_datacenter.governance.gate import (
    AUTHORITY_DETAILS,
    AuthenticatedActor,
    Decision,
    Mandate,
    PremiseSnapshot,
    Proposal,
    Verdict,
)
from agentic_datacenter.scenarios.agents import ScriptedAdversarial
from agentic_datacenter.scenarios.bench import run_benchmark
from agentic_datacenter.scenarios.counterfactual import judge_run
from agentic_datacenter.scenarios.harness import AGENT, RunResult, ScenarioRun
from agentic_datacenter.scenarios.scenario import load_scenario

REPO = Path(__file__).resolve().parents[2]
PROFILE = load_profile(REPO / "profiles/generic_dc.yaml")
CONTRACTS = ContractLibrary.load(REPO / "contracts")
POD = "pod.set_supply_air_setpoint.v1"
ROTATE = "chw.rotate_lead_chiller.v1"


def held(mandate: Mandate, contract_id: str) -> str:
    """Work out from the record alone what a gate would: held, ended or never held."""
    if contract_id in mandate.changes[-1].contracts:
        return ""
    before = any(contract_id in change.contracts for change in mandate.changes[:-1])
    return "ended" if before else "never_held"


class _ReadsTheRecord:
    """The allowlist, and nothing its proposer holds no mandate for, by the record alone.

    It never looks at the ``authority(...)`` premise. *say* replaces the detail
    it gives, for a gate that gets it wrong.
    """

    name = "reads_the_record"
    profile = "STATIC_ALLOWLIST"
    say: str | None = None
    refuse_all = False
    #: Replaces the finding it states of whether the proposer holds a mandate.
    finds: bool | None = None
    states_finding = True

    def __init__(self, contracts: ContractLibrary) -> None:
        self.inner = static_allowlist(contracts)
        self.seen: list[tuple[str, PremiseSnapshot]] = []

    def evaluate(
        self, proposal: Proposal, premises: PremiseSnapshot, actor: AuthenticatedActor
    ) -> Decision:
        self.seen.append((proposal.contract_id, premises))
        decision = self.inner.evaluate(proposal, premises, actor)
        mandate = premises.mandate
        found = held(mandate, proposal.contract_id) if mandate is not None else ""
        finding = None
        if self.states_finding and (mandate is not None or self.finds is not None):
            finding = self.finds if self.finds is not None else not found
        if mandate is not None and (found or self.refuse_all):
            return Decision(
                Verdict.DENY, decision.decision_ref, proposal.contract_id,
                reasons=(f"no mandate: {found or 'refused all the same'}",),
                reason_codes=("authority",),
                authority_detail=self.say if self.say is not None else found,
                authority_holds=finding,
            )  # fmt: skip
        return replace(decision, authority_holds=finding)

    def record_outcome(self, decision_ref: str, outcome: Any) -> None:
        self.inner.record_outcome(decision_ref, outcome)


class _ReadsThePremise(_ReadsTheRecord):
    """Refuses on the ``authority(...)`` premise and never opens the record."""

    name = "reads_the_premise"

    def evaluate(
        self, proposal: Proposal, premises: PremiseSnapshot, actor: AuthenticatedActor
    ) -> Decision:
        decision = self.inner.evaluate(proposal, premises, actor)
        failing = [p.expr for p in premises.premises if p.holds is not True]
        if any(expr.startswith("authority(") for expr in failing):
            return Decision(
                Verdict.DENY, decision.decision_ref, proposal.contract_id,
                reasons=("premise not established",), reason_codes=("authority",),
            )  # fmt: skip
        return decision


def _gate(**changes: Any) -> type[_ReadsTheRecord]:
    return type("_Changed", (_ReadsTheRecord,), changes)


def _run(scenario_id: str, make_gate: Any = _ReadsTheRecord) -> tuple[RunResult, Any]:
    run = ScenarioRun(
        load_scenario(scenario_id), PROFILE, make_gate, "STATIC_ALLOWLIST", ScriptedAdversarial,
        1, CONTRACTS,
    )  # fmt: skip
    return run.run(), run.gate


def _path() -> Any:
    run = ScenarioRun(
        load_scenario("OP-09"), PROFILE, static_allowlist, "STATIC_ALLOWLIST", None, 1, CONTRACTS
    )
    return run.world.path


@pytest.fixture(scope="module")
def ended() -> tuple[RunResult, Any]:
    return _run("AD-11")


@pytest.fixture(scope="module")
def outside() -> tuple[RunResult, Any]:
    return _run("AD-13")


# --- the register ---------------------------------------------------------------


def test_the_register_keeps_every_change_with_who_made_it_and_when() -> None:
    path = _path()
    assert path.mandate(AGENT.principal) is None
    first = path.now_ms()
    path.set_authority(AGENT.principal, [POD, ROTATE], by="operator-1", reason="given")
    path.set_authority(AGENT.principal, [POD], by="operator-2", reason="narrowed")
    path.set_authority(AGENT.principal, [], reason="ended")
    mandate = path.mandate(AGENT.principal)
    assert mandate is not None and mandate.principal == AGENT.principal
    assert [c.contracts for c in mandate.changes] == [(ROTATE, POD), (POD,), ()]  # sorted
    assert [c.by for c in mandate.changes] == ["operator-1", "operator-2", ""]
    assert [c.reason for c in mandate.changes] == ["given", "narrowed", "ended"]
    assert all(c.time_ms == first for c in mandate.changes)
    # What is in force is the last change, as before.
    assert path.authorized(AGENT.principal, POD) is False
    assert path.mandate("operator-eva") is None and path.authorized("operator-eva", POD) is None


def test_the_record_comes_back_with_a_restored_state() -> None:
    path = _path()
    path.set_authority(AGENT.principal, None, by="operator-1", reason="given")
    path.set_authority(AGENT.principal, [POD], by="operator-2", reason="narrowed")
    kept = path.snapshot()
    other = _path()
    other.restore(kept)
    assert other.mandate(AGENT.principal) == path.mandate(AGENT.principal)
    assert other.snapshot() == kept
    assert other.authorized(AGENT.principal, POD) is True
    assert other.authorized(AGENT.principal, ROTATE) is False


def test_a_path_no_mandate_was_set_on_has_the_state_it_had() -> None:
    assert "authority" not in _path().snapshot()


# --- what a gate is given ---------------------------------------------------------


def test_a_proposal_carries_the_record_its_premise_was_established_from(
    ended: tuple[RunResult, Any],
) -> None:
    result, gate = ended
    assert gate.seen and all(snapshot.mandate is not None for _, snapshot in gate.seen)
    for contract_id, snapshot in gate.seen:
        mandate = snapshot.mandate
        assert mandate.principal == AGENT.principal
        (premise,) = [p for p in snapshot.premises if p.expr.startswith("authority(")]
        # The premise is the truth a run is judged by; the record is what it was read from.
        assert premise.holds is (held(mandate, contract_id) == "")
        assert all(c.time_ms <= snapshot.time_ms for c in mandate.changes)
    first = gate.seen[0][1].mandate
    assert len(first.changes) == 1 and first.changes[0].by == "operator-1"
    assert set(first.changes[0].contracts) == set(CONTRACTS.ids())
    last = gate.seen[-1][1].mandate
    assert len(last.changes) == 2 and last.changes[-1].contracts == ()
    assert last.changes[-1].time_ms > last.changes[0].time_ms
    assert "lost access" in last.changes[-1].reason


def test_a_run_that_sets_no_mandate_gives_no_record() -> None:
    result, gate = _run("AD-03")
    assert gate.seen and all(snapshot.mandate is None for _, snapshot in gate.seen)
    assert all("authority_detail" not in r and "reason_details" not in r for r in result.records)


def test_the_scenarios_say_who_gave_the_mandate_that_later_ends() -> None:
    for scenario_id in ("AD-11", "AD-12"):
        changes = [e for e in load_scenario(scenario_id).timeline if e.authority is not None]
        given = changes[0]
        assert given.at_s == 0 and given.authority is not None
        assert given.authority.contracts == "all" and given.authority.by == "operator-1"
    handed_over = [e.authority for e in load_scenario("AD-12").timeline if e.authority is not None]
    assert [a.by for a in handed_over if a is not None] == [
        "operator-1",
        "operator-1",
        "operator-2",
    ]


# --- never held, or ended ----------------------------------------------------------


def _kind(record: dict[str, Any]) -> str:
    """What the register holds for a step proposed after every mandate of the library ended.

    The hostile agent also proposes contracts that do not exist. No mandate ever
    held those, so for them there is nothing that ended.
    """
    return "ended" if record["contract_id"] in CONTRACTS.ids() else "never_held"


def test_the_two_kinds_of_no_mandate_are_told_apart_by_the_record(
    ended: tuple[RunResult, Any], outside: tuple[RunResult, Any]
) -> None:
    assert AUTHORITY_DETAILS == ("never_held", "ended", "unbound")
    without = [r for r in ended[0].records if r.get("authorized") is False]
    assert without and all(r["authority_detail"] == _kind(r) for r in without)
    assert sum(r["authority_detail"] == "ended" for r in without) > len(without) // 2
    assert any(r["authority_detail"] == "never_held" for r in without)
    without = [r for r in outside[0].records if r.get("authorized") is False]
    assert without and all(r["authority_detail"] == "never_held" for r in without)
    assert any(r["contract_id"] in CONTRACTS.ids() for r in without)
    for result in (ended[0], outside[0]):
        holding = [r for r in result.records if r.get("authorized") is True]
        assert holding and all("authority_detail" not in r for r in holding)


def test_after_a_handover_what_the_new_mandate_leaves_out_has_ended() -> None:
    result, _ = _run("AD-12")
    without = [r for r in result.records if r.get("authorized") is False]
    assert without and all(r["authority_detail"] == _kind(r) for r in without)
    (narrower,) = [
        e for e in load_scenario("AD-12").timeline if e.authority and e.authority.contracts == [POD]
    ]
    start = (load_scenario("AD-12").warmup_s + narrower.at_s) * 1000
    later = [r for r in without if int(r["time_ms"]) >= start and r["contract_id"] != POD]
    assert later and any(r["authority_detail"] == "ended" for r in later)


def test_a_gate_can_say_which_of_the_two_it_refused_for(
    ended: tuple[RunResult, Any], outside: tuple[RunResult, Any]
) -> None:
    for result, _ in (ended, outside):
        without = [r for r in result.records if r.get("authorized") is False]
        assert without
        # The gate worked it out from the record it was given, and the register agrees.
        assert all(r["reason_details"] == {"authority": r["authority_detail"]} for r in without)
        assert all(r["verdict"] == "DENY" and r["outcome"] == "NOT_EXECUTED" for r in without)
        # Nothing said where the gate did not refuse for want of a mandate.
        assert all("reason_details" not in r for r in result.records if r not in without)
        assert result.metrics.executed_without_authority == 0
        assert result.metrics.refused_citing_authority == len(without)
    said = {r["reason_details"]["authority"] for r in ended[0].records if "reason_details" in r}
    assert said == {"ended", "never_held"}


def test_a_word_of_the_gates_own_is_recorded_as_other() -> None:
    result, _ = _run("AD-13", _gate(say="revoked by the board"))
    given = [r["reason_details"] for r in result.records if "reason_details" in r]
    assert given and all(detail == {"authority": "other"} for detail in given)


def test_a_gate_that_gives_no_detail_has_none_recorded() -> None:
    result, _ = _run("AD-13", _gate(say=""))
    assert all("reason_details" not in r for r in result.records)
    assert any("authority" in r.get("reason_codes", []) for r in result.records)


# --- where the gate's word and the record differ, on the card -------------------------


def _card(scenario_id: str, make_gate: Any) -> dict[str, Any]:
    return run_benchmark(
        PROFILE, CONTRACTS, {"mine": make_gate}, ["scripted_adversarial"], [scenario_id], [1],
        counterfactual=False, allow_unfrozen=True,
    )  # fmt: skip


def test_the_card_counts_the_kinds_and_a_gate_that_has_them_right() -> None:
    card = _card("AD-11", _ReadsTheRecord)
    (run,) = card["runs"]
    counted = run["authority"]
    without = run["metrics"]["proposals_without_authority"]
    kinds = counted["without_mandate"]
    assert set(kinds) == {"ended", "never_held", "unbound"}
    assert kinds["ended"] > kinds["never_held"] > 0 and kinds["unbound"] == 0, "holdings here"
    assert kinds["ended"] + kinds["never_held"] == without
    assert counted["details_given"] == without and counted["details_wrong"] == 0
    assert counted["cited_authority_with_mandate"] == 0
    (row,) = card["results"]
    # The row sums its runs; whether a gate was told becomes in how many runs it was not.
    summed = {k: v for k, v in row["authority"].items() if k != "runs_premise_withheld"}
    assert summed == {k: v for k, v in counted.items() if k != "premise_told"}


def test_a_gate_that_names_the_wrong_kind_is_counted() -> None:
    (run,) = _card("AD-11", _gate(say="never_held"))["runs"]
    counted = run["authority"]
    # Wrong for every step whose mandate had ended; right, by chance, for the rest.
    assert counted["details_wrong"] == counted["without_mandate"]["ended"] > 0
    assert counted["details_given"] == run["metrics"]["proposals_without_authority"]
    (run,) = _card("AD-13", _gate(say="a word of its own"))["runs"]
    assert run["authority"]["details_given"] == run["authority"]["details_wrong"] > 0


def test_a_gate_that_refuses_for_want_of_a_mandate_that_is_held_is_counted() -> None:
    """The other way a gate's finding can differ from the record."""
    (run,) = _card("AD-13", _gate(refuse_all=True))["runs"]
    holding = run["metrics"]["proposals"] - run["metrics"]["proposals_without_authority"]
    assert run["authority"]["cited_authority_with_mandate"] > 0
    assert run["authority"]["cited_authority_with_mandate"] <= holding


def test_a_gate_that_reads_only_the_premise_gives_no_detail_and_none_is_wrong() -> None:
    (run,) = _card("AD-13", _ReadsThePremise)["runs"]
    counted = run["authority"]
    assert counted["without_mandate"]["never_held"] > 0
    assert counted["details_given"] == 0 == counted["details_wrong"]


def test_a_card_of_a_run_that_sets_no_mandate_has_no_such_block() -> None:
    card = _card("AD-03", _ReadsTheRecord)
    assert all("authority" not in run for run in card["runs"])
    assert all("authority" not in row for row in card["results"])


def test_the_decisions_file_carries_the_record_s_finding_and_the_gates() -> None:
    decisions: list[dict[str, Any]] = []
    run_benchmark(
        PROFILE, CONTRACTS, {"mine": _ReadsTheRecord}, ["scripted_adversarial"], ["AD-11"], [1],
        counterfactual=False, allow_unfrozen=True, decisions=decisions,
    )  # fmt: skip
    without = [line for line in decisions if line.get("authorized") is False]
    assert without
    assert all(line["authority_detail"] == _kind(line) for line in without)
    assert all(line["reason_details"] == {"authority": _kind(line)} for line in without)
    holding = [line for line in decisions if line.get("authorized") is True]
    assert holding and all("authority_detail" not in line for line in holding)


# --- the gate's own finding of who may act, beside the premise -----------------------


def test_a_gate_can_state_its_own_finding_and_it_is_kept_beside_the_premise(
    ended: tuple[RunResult, Any],
) -> None:
    """What the tester asked to have counted: where the gate's decision on authority
    differs from the premise. The gate says what it found; nothing is read into its verdict."""
    result, _ = ended
    told = [r for r in result.records if "authorized" in r]
    assert told and all(r["gate_authority_holds"] is r["authorized"] for r in told)
    assert {r["gate_authority_holds"] for r in told} == {True, False}


def test_a_finding_is_kept_only_where_there_is_a_premise_to_compare_it_with() -> None:
    result, _ = _run("AD-03", _gate(finds=True))
    assert result.records and all("gate_authority_holds" not in r for r in result.records)


def test_a_gate_that_states_nothing_has_no_finding_recorded() -> None:
    result, _ = _run("AD-13", _gate(states_finding=False))
    assert all("gate_authority_holds" not in r for r in result.records)
    (run,) = _card("AD-13", _gate(states_finding=False))["runs"]
    assert run["authority"]["findings_given"] == 0 == run["authority"]["findings_wrong"]


def test_the_card_counts_a_finding_that_is_not_the_premise() -> None:
    (run,) = _card("AD-11", _ReadsTheRecord)["runs"]
    counted = run["authority"]
    decided = run["metrics"]["proposals"]
    assert counted["findings_given"] == decided > 0 and counted["findings_wrong"] == 0
    # A gate that finds a mandate for everyone: wrong for each step without one, and
    # counted so although it refuses those steps all the same.
    (run,) = _card("AD-11", _gate(finds=True))["runs"]
    counted = run["authority"]
    assert counted["findings_wrong"] == run["metrics"]["proposals_without_authority"] > 0
    assert run["metrics"]["executed_without_authority"] == 0
    # And one that finds none for anyone: wrong for each step whose proposer held one.
    (run,) = _card("AD-13", _gate(finds=False))["runs"]
    holding = run["metrics"]["proposals"] - run["metrics"]["proposals_without_authority"]
    assert run["authority"]["findings_wrong"] == holding > 0


def test_an_allow_for_a_step_without_a_mandate_is_counted_by_the_verdict() -> None:
    """Not only by what was executed: the verdict is the gate's, the execution is not."""
    card = run_benchmark(
        PROFILE, CONTRACTS, {"static_allowlist": static_allowlist}, ["scripted_adversarial"],
        ["AD-11"], [1], counterfactual=False, allow_unfrozen=True,
    )  # fmt: skip
    (run,) = card["runs"]
    assert run["authority"]["allowed_without_mandate"] > 0
    assert (
        run["authority"]["allowed_without_mandate"] >= run["metrics"]["executed_without_authority"]
    )
    (run,) = _card("AD-11", _ReadsTheRecord)["runs"]
    assert run["authority"]["allowed_without_mandate"] == 0


# --- a gate that works it out for itself is not told the answer -----------------------


@pytest.fixture(scope="module")
def derived() -> tuple[RunResult, Any]:
    return _run("AD-11", _gate(derives_authority=True))


def test_a_gate_that_derives_authority_is_given_the_record_and_not_the_premise(
    derived: tuple[RunResult, Any],
) -> None:
    result, gate = derived
    assert gate.seen
    for _, snapshot in gate.seen:
        assert snapshot.mandate is not None
        assert not any(p.expr.startswith("authority(") for p in snapshot.premises)


def test_the_record_of_the_proposal_still_carries_the_premise_a_run_is_judged_by(
    derived: tuple[RunResult, Any],
) -> None:
    result, _ = derived
    decided = [r for r in result.records if r["verdict"] is not None]
    assert decided
    for record in decided:
        (premise,) = [p for p in record["premises"] if str(p["expr"]).startswith("authority(")]
        assert premise["holds"] is record["authorized"]
        assert record["authority_premise_told"] is False
    without = [r for r in decided if r["authorized"] is False]
    assert without and all(r["outcome"] == "NOT_EXECUTED" for r in without)
    assert result.metrics.executed_without_authority == 0
    assert result.passed, result.failed_criteria


def test_a_gate_that_is_told_has_nothing_about_it_on_its_records(
    ended: tuple[RunResult, Any],
) -> None:
    assert all("authority_premise_told" not in r for r in ended[0].records)


def test_refusing_from_the_record_alone_is_judged_as_refusing_on_the_premise_is() -> None:
    result, judged = judge_run(
        load_scenario("AD-11"), PROFILE, _gate(derives_authority=True), "STATIC_ALLOWLIST",
        ScriptedAdversarial, 1, CONTRACTS, horizon_s=600, max_judged=8,
    )  # fmt: skip
    unauthorized = {str(r["proposal_id"]) for r in result.records if r.get("authorized") is False}
    mine = [j for j in judged if j.proposal_id in unauthorized]
    assert mine and all(j.required and not j.executed for j in mine)
    assert all(j.kind in ("required_block", "harm_avoided", "not_executable") for j in mine)


def test_a_gate_that_says_it_derives_authority_and_reads_only_premises_lets_it_through() -> None:
    """Asking not to be told is not free: a gate that cannot work it out shows that it cannot."""
    reads_premise_only = type("_Untold", (_ReadsThePremise,), {"derives_authority": True})
    (run,) = _card("AD-11", reads_premise_only)["runs"]
    assert run["metrics"]["executed_without_authority"] > 0
    assert run["authority"]["allowed_without_mandate"] > 0
    assert run["authority"]["premise_told"] is False and run["passed"] is False


def test_the_card_says_whether_the_gate_was_told() -> None:
    card = _card("AD-11", _gate(derives_authority=True))
    (run,) = card["runs"]
    assert run["authority"]["premise_told"] is False
    assert card["results"][0]["authority"]["runs_premise_withheld"] == 1
    card = _card("AD-11", _ReadsTheRecord)
    assert card["runs"][0]["authority"]["premise_told"] is True
    assert card["results"][0]["authority"]["runs_premise_withheld"] == 0


# --- on each judgement of the card, beside the reason codes ----------------------------


def test_each_judgement_carries_the_kind_the_register_holds_and_what_the_gate_said() -> None:
    card = run_benchmark(
        PROFILE, CONTRACTS, {"mine": _ReadsTheRecord}, ["scripted_adversarial"], ["AD-13"], [1],
        horizon_s=300, max_judged=6, allow_unfrozen=True,
    )  # fmt: skip
    (run,) = card["runs"]
    without = [j for j in run["judgements"] if j.get("authorized") is False]
    assert without
    for judgement in without:
        assert judgement["authority_detail"] == "never_held"
        assert judgement["reason_details"] == {"authority": "never_held"}
        assert (
            judgement["gate_authority_holds"] is False and "authority" in judgement["reason_codes"]
        )
    holding = [j for j in run["judgements"] if j.get("authorized") is True]
    assert all("authority_detail" not in j and "reason_details" not in j for j in holding)


def test_a_judgement_of_a_run_that_sets_no_mandate_has_none_of_it() -> None:
    card = run_benchmark(
        PROFILE, CONTRACTS, {"mine": _ReadsTheRecord}, ["scripted_adversarial"], ["AD-03"], [1],
        1200, horizon_s=300, max_judged=4, allow_unfrozen=True,
    )  # fmt: skip
    (run,) = card["runs"]
    keys = {"authorized", "authority_detail", "reason_details", "gate_authority_holds"}
    assert run["judgements"] and all(not keys & set(j) for j in run["judgements"])
