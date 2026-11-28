"""A site declares how it reads a delegation, and a run is judged by it (ADR 0048, issue 76).

AD-21 and AD-22 declare that a delegation is bound to its grant; OP-19 and
OP-20 are the same events at a site that reads a delegation by what its issuer
holds now. The register, a gate given the record and a gate given signed
statements alone must agree on every work order.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from agentic_datacenter.core.profile import load_profile
from agentic_datacenter.governance.bindings.static_allowlist import create
from agentic_datacenter.governance.contracts import ContractLibrary
from agentic_datacenter.governance.gate import Verdict
from agentic_datacenter.scenarios.agents import ScriptedBenign
from agentic_datacenter.scenarios.harness import run_scenario
from agentic_datacenter.scenarios.scenario import load_scenario, parse_scenario

REPO = Path(__file__).resolve().parents[2]
PROFILE = load_profile(REPO / "profiles" / "generic_dc.yaml")
CONTRACTS = ContractLibrary.load(REPO / "contracts")
#: The standing of each work order, as aeoess's table in issue 76 gives it.
EXPECTED = {
    "AD-21": [True, False, False, True],
    "OP-19": [True, False, True],
    "AD-22": [False, False, True],
    "OP-20": [False, True],
}


def reads_the_premise(contracts: ContractLibrary) -> Any:
    """The limits-only control, and a refusal whenever the register says no mandate."""
    inner = create(contracts)

    class ReadsThePremise(type(inner)):  # type: ignore[misc]
        name = "reads_the_premise"

        def evaluate(self, proposal, premises, actor):  # type: ignore[no-untyped-def]
            decision = super().evaluate(proposal, premises, actor)
            no = [
                p.expr for p in premises.premises
                if p.expr.startswith("authority(") and p.holds is False
            ]  # fmt: skip
            if not no:
                return decision
            return dataclasses.replace(
                decision, verdict=Verdict.DENY, bounded_writes=(), reasons=tuple(no),
                reason_codes=("authority",),
            )  # fmt: skip

    return ReadsThePremise(inner.allowed)


def orders(scenario_id: str) -> list[dict[str, Any]]:
    result = run_scenario(
        load_scenario(scenario_id), PROFILE, reads_the_premise(CONTRACTS), "DECISION_ONLY",
        ScriptedBenign(), 1, CONTRACTS,
    )  # fmt: skip
    return [r for r in result.records if "WO-5" in str(r.get("rationale", ""))]


@pytest.mark.req("ADC-AUT-012")
@pytest.mark.parametrize("scenario_id", sorted(EXPECTED))
def test_each_work_order_has_the_standing_the_proposal_gives(scenario_id: str) -> None:
    found = orders(scenario_id)
    assert [r["authorized"] for r in found] == EXPECTED[scenario_id]
    for record in found:
        # A step without a mandate is not carried out; one with it is not refused for
        # authority (a rotation is refused by the limits-only control for its risk tier).
        if record["authorized"]:
            assert "authority" not in (record.get("reason_codes") or []), record["rationale"]
        else:
            assert record["outcome"] != "APPLIED", record["rationale"]
    model = load_scenario(scenario_id).authorization_model
    assert model == ("grant_bound" if scenario_id.startswith("AD") else "holdings")


@pytest.mark.req("ADC-AUT-012")
def test_a_delegation_not_under_its_grant_is_said_to_be_unbound() -> None:
    found = orders("AD-21")
    assert [r.get("authority_detail") for r in found if not r["authorized"]] == [
        "unbound", "unbound",
    ]  # fmt: skip
    assert [r.get("authority_detail") for r in orders("OP-19") if not r["authorized"]] == ["ended"]


@pytest.mark.req("ADC-AUT-012")
def test_a_gate_is_told_the_model_and_every_earlier_scenario_keeps_its_hash() -> None:
    from agentic_datacenter.scenarios.harness import ScenarioRun

    run = ScenarioRun(
        load_scenario("AD-21"), PROFILE, reads_the_premise, "DECISION_ONLY",
        ScriptedBenign, 1, CONTRACTS,
    )  # fmt: skip
    path = run.world.path
    assert path.authorization_model == "grant_bound"
    run.advance(1)
    statements = path.statements("agent-1")
    assert statements is not None and statements.model == "grant_bound"
    record = path.mandate("agent-1")
    assert record is not None and record.model == "grant_bound"
    assert all(d.model == "grant_bound" for d in record.delegators)
    state = path.snapshot()
    assert isinstance(state, dict) and state["authorization_model"] == "grant_bound"
    plain = parse_scenario({
        "schema_version": 1, "id": "AD-99", "title": "x", "track": "C", "duration_s": 60,
    })  # fmt: skip
    told = parse_scenario({
        "schema_version": 1, "id": "AD-99", "title": "x", "track": "C", "duration_s": 60,
        "authorization_model": "holdings",
    })  # fmt: skip
    assert plain.hash() == told.hash(), "the default leaves a scenario's hash as it was"
