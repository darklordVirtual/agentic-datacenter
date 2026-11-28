"""A quick run reaches what each scenario is about (issue 31).

The quick tier shortened every scenario to 30 minutes, whatever was in it. A
scenario whose event comes later was then played without that event. In the
authority scenarios AD-11 and AD-12 the mandate ends after 30 minutes: a quick
run counted no step proposed without a mandate, and a gate that checks only
operating limits passed both. Eight scenarios of the library had an event cut
off in this way.

An agent acts at the decision points of a scenario. Five scenarios pace them
ten minutes or more apart, and 30 minutes then held few of them, or none: in
AD-05 no agent proposed anything in a quick run, and in AD-14 and OP-09 the
work that needs a person, which is what they are about, was proposed after
the run had ended.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentic_datacenter.core.profile import load_profile
from agentic_datacenter.governance.bindings.static_allowlist import create as static_allowlist
from agentic_datacenter.governance.contracts import ContractLibrary
from agentic_datacenter.scenarios.bench import (
    DECISION_POINTS_IN_A_SHORTENED_RUN,
    MERGE_KEYS,
    TIERS,
    run_benchmark,
    shortened,
)
from agentic_datacenter.scenarios.scenario import library_ids, load_scenario

REPO = Path(__file__).resolve().parents[2]
PROFILE = load_profile(REPO / "profiles/generic_dc.yaml")
CONTRACTS = ContractLibrary.load(REPO / "contracts")
QUICK_S = int(str(TIERS["quick"]["seconds"]))
REACH_S = int(str(TIERS["quick"].get("reach_s") or 0))


def test_only_the_quick_tier_shortens_and_it_says_how_far_it_reaches() -> None:
    assert (QUICK_S, REACH_S) == (1800, 7200)
    for name in ("standard", "season", "full"):
        assert TIERS[name]["seconds"] is None and TIERS[name].get("reach_s") is None
    assert "duration_reach_s" in MERGE_KEYS


@pytest.mark.parametrize(
    ("scenario_id", "length"),
    [
        ("AD-11", 4800),  # the mandate ends at 2000 s, the work order comes at 3000 s
        ("AD-12", 5400),  # the narrower mandate is given at 3600 s
        ("OP-03", 3600),  # the chiller trips at 1800 s, the instant a quick run ended
        ("OP-07", 7200),  # decision points 30 minutes apart: as far as the reach goes
        ("AD-13", 1800),  # nothing after 30 minutes: as before
        ("AD-14", 3600),  # decision points 10 minutes apart: six of them
        ("OP-09", 3600),  # the same
        ("OP-10", 5400),  # 15 minutes apart
        ("AD-05", 7200),  # 36 minutes apart: none of them was in 30 minutes
    ],
)
def test_a_scenario_with_an_event_after_30_minutes_gets_them_from_that_event(
    scenario_id: str, length: int
) -> None:
    assert shortened(load_scenario(scenario_id), QUICK_S, REACH_S) == length


def test_no_event_within_reach_is_cut_off_and_the_reach_is_the_limit() -> None:
    longer = []
    for scenario_id in library_ids():
        scenario = load_scenario(scenario_id)
        length = shortened(scenario, QUICK_S, REACH_S)
        assert length is not None and QUICK_S <= length <= REACH_S
        played = min(length, scenario.duration_s)
        cut = [e.at_s for e in scenario.timeline if played <= e.at_s < REACH_S]
        assert not cut, f"{scenario_id}: events at {cut} s are not in a run of {played} s"
        instants = [e.at_s for e in scenario.timeline]
        # A mandate that expires by itself ends at an instant no event marks (ADR 0039).
        instants += [
            e.at_s + e.authority.expires_after_s
            for e in scenario.timeline
            if e.authority is not None and e.authority.expires_after_s is not None
        ]
        late = [t for t in instants if QUICK_S <= t < min(REACH_S, scenario.duration_s)]
        paced = DECISION_POINTS_IN_A_SHORTENED_RUN * scenario.decision_every_s
        wanted = max(QUICK_S, paced, max(late) + QUICK_S if late else 0)
        assert length == min(REACH_S, wanted)
        if length != QUICK_S:
            longer.append(scenario_id)
    assert longer == [
        "AD-01", "AD-05", "AD-09", "AD-11", "AD-12", "AD-14", "AD-16", "AD-17", "AD-18", "AD-19",
        "AD-21", "AD-22", "OP-01", "OP-03", "OP-07", "OP-09", "OP-10", "OP-12", "OP-17", "OP-19",
        "OP-20",
    ]  # fmt: skip


def test_a_shortened_run_holds_six_decision_points_or_goes_as_far_as_the_reach() -> None:
    assert DECISION_POINTS_IN_A_SHORTENED_RUN == 6
    for scenario_id in library_ids():
        scenario = load_scenario(scenario_id)
        length = shortened(scenario, QUICK_S, REACH_S)
        assert length is not None
        played = min(length, scenario.duration_s)
        points = played // scenario.decision_every_s
        whole = played == scenario.duration_s
        assert points >= 6 or played == REACH_S or whole, (scenario_id, played, points)


def _example_gate() -> Any:
    """The example gate of ``examples/python-gate``: it asks a person where a contract does."""
    import importlib.util

    path = REPO / "examples/python-gate/src/example_gate/__init__.py"
    spec = importlib.util.spec_from_file_location("example_gate_for_quick", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.create


@pytest.mark.parametrize("scenario_id", ["AD-14", "OP-09"])
def test_a_quick_run_reaches_the_work_that_needs_a_person(scenario_id: str) -> None:
    """What these two are about is proposed at the fourth decision point, at 40 minutes."""
    card = run_benchmark(
        PROFILE, CONTRACTS, {"example": _example_gate()}, ["scripted_adversarial"],
        [scenario_id], [1], QUICK_S, counterfactual=False, allow_unfrozen=True, reach_s=REACH_S,
    )  # fmt: skip
    (run,) = card["runs"]
    assert run["duration_s"] == 3600
    assert run["metrics"]["approval_required"] > 0 and run["metrics"]["approvals_requested"] > 0
    if scenario_id == "AD-14":
        # The operator who has lost access answers, and is turned away.
        assert run["metrics"]["approvals_refused_without_standing"] > 0


def test_every_agent_gets_to_propose_in_a_quick_run_of_the_slowest_scenario() -> None:
    card = run_benchmark(
        PROFILE, CONTRACTS, {"static_allowlist": static_allowlist},
        ["scripted_adversarial", "scripted_benign"], ["AD-05"], [1], QUICK_S,
        counterfactual=False, allow_unfrozen=True, reach_s=REACH_S,
    )  # fmt: skip
    assert [run["duration_s"] for run in card["runs"]] == [7200, 7200]
    assert all(run["metrics"]["proposals"] > 0 for run in card["runs"])


def test_an_event_near_the_reach_does_not_take_the_run_beyond_it() -> None:
    from agentic_datacenter.scenarios.scenario import parse_scenario

    late = parse_scenario({
        "schema_version": 1, "id": "OP-99", "title": "t", "track": "A", "duration_s": 86400,
        "pass_criteria": [{"metric": "it_kwh_lost", "max": 0}],
        "timeline": [{"at_s": 7000, "note": "late"}, {"at_s": 7200, "note": "beyond"}],
    })  # fmt: skip
    assert shortened(late, QUICK_S, REACH_S) == REACH_S


def test_a_length_given_by_hand_is_the_length() -> None:
    """``--seconds`` shortens every scenario to exactly that; only a tier reaches further."""
    for scenario_id in ("AD-11", "AD-12", "OP-07"):
        assert shortened(load_scenario(scenario_id), 1800, None) == 1800
        assert shortened(load_scenario(scenario_id), None, None) is None
        assert shortened(load_scenario(scenario_id), None, REACH_S) is None


def _runs(card: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(run["scenario"]): run for run in card["runs"]}


@pytest.fixture(scope="module")
def quick_card(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The card of the command a first-time user is given, for the scenarios of issue 31."""
    from agentic_datacenter.scenarios.cli import bench

    out = tmp_path_factory.mktemp("quick")
    code = bench([
        "run", "--tier", "quick", "--scenario", "AD-11", "--scenario", "AD-12",
        "--gate", "static_allowlist", "--agent", "scripted_adversarial", "--no-counterfactual",
        "--out", str(out),
    ])  # fmt: skip
    assert code == 0
    card: dict[str, Any] = json.loads((out / "result_card.json").read_text(encoding="utf-8"))
    return card


def test_a_quick_run_shows_the_steps_proposed_after_a_mandate_ended(
    quick_card: dict[str, Any],
) -> None:
    runs = _runs(quick_card)
    assert runs["AD-11"]["duration_s"] == 4800 and runs["AD-12"]["duration_s"] == 5400
    for scenario_id in ("AD-11", "AD-12"):
        metrics = runs[scenario_id]["metrics"]
        assert metrics["proposals_without_authority"] > 0, scenario_id
        # A gate that checks only limits executes them, and does not pass the scenario.
        assert metrics["executed_without_authority"] > 0, scenario_id
        assert runs[scenario_id]["passed"] is False
        assert any("executed_without_authority" in c for c in runs[scenario_id]["failed_criteria"])


def test_the_card_says_what_it_was_shortened_to_and_how_far_it_reached(
    quick_card: dict[str, Any],
) -> None:
    assert quick_card["duration_override_s"] == 1800
    assert quick_card["duration_reach_s"] == 7200
    # The run without an agent is as long as the run it is compared with.
    lengths = {(b["scenario"], b["duration_s"]) for b in quick_card["baselines"]}
    assert lengths == {("AD-11", 4800), ("AD-12", 5400)}


def test_the_narrower_mandate_of_ad_12_is_in_a_quick_run(quick_card: dict[str, Any]) -> None:
    """Not only the gap between the two mandates: the second one too, which a run cut at
    the handover never reached."""
    scenario = load_scenario("AD-12")
    given = max(e.at_s for e in scenario.timeline if e.authority is not None)
    assert _runs(quick_card)["AD-12"]["duration_s"] == given + QUICK_S


def test_a_card_made_without_a_tier_has_no_reach_on_it() -> None:
    """A card made as before is the card it was: the key is there only when a reach was set."""
    card = run_benchmark(
        PROFILE, CONTRACTS, {"static_allowlist": static_allowlist}, ["scripted_benign"],
        ["AD-13"], [1], 600, counterfactual=False, allow_unfrozen=True,
    )  # fmt: skip
    assert "duration_reach_s" not in card
    assert card["duration_override_s"] == 600 and card["runs"][0]["duration_s"] == 600


def test_seconds_beside_the_tier_takes_precedence(tmp_path: Path) -> None:
    from agentic_datacenter.scenarios.cli import bench

    code = bench([
        "run", "--tier", "quick", "--seconds", "900", "--scenario", "AD-11",
        "--gate", "static_allowlist", "--agent", "scripted_benign", "--no-counterfactual",
        "--out", str(tmp_path),
    ])  # fmt: skip
    assert code == 0
    card = json.loads((tmp_path / "result_card.json").read_text(encoding="utf-8"))
    assert card["duration_override_s"] == 900 and "duration_reach_s" not in card
    assert card["runs"][0]["duration_s"] == 900


def test_the_summary_printed_after_a_run_says_what_was_executed_without_a_mandate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The first outside tester had to read these figures out of the card."""
    from agentic_datacenter.scenarios.cli import bench

    arguments = [
        "run", "--tier", "quick", "--gate", "static_allowlist", "--agent", "scripted_benign",
        "--no-counterfactual",
    ]  # fmt: skip
    assert bench([*arguments, "--scenario", "AD-13", "--out", str(tmp_path / "a")]) == 0
    said = capsys.readouterr().out
    card = json.loads((tmp_path / "a" / "result_card.json").read_text(encoding="utf-8"))
    (row,) = card["results"]
    assert row["proposals_without_authority"] > 0
    assert (
        f"without a mandate: {row['executed_without_authority']} executed of "
        f"{row['proposals_without_authority']} proposed" in said
    )
    # A run in which no mandate was set says nothing of it.
    assert bench([*arguments, "--scenario", "AD-06", "--out", str(tmp_path / "b")]) == 0
    assert "mandate" not in capsys.readouterr().out
