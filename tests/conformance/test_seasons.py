"""Seasons follow one another by a rule, so one is always open (ADR 0027)."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
PUBLIC_TEST = REPO / "docs/bench/PUBLIC_TEST.md"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / f"tools/{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    return _load("season")


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def test_season_1_is_the_season_that_was_published(tool: ModuleType) -> None:
    first = tool.season(1)
    assert first["deadline"] == "2027-01-13T12:00:00Z"
    assert first["round"] == 35679412
    assert first["version"] == "benchmark-v1.7"
    assert first["opens"] == "2026-11-20"


def test_each_season_opens_when_the_one_before_closes_and_lasts_four_weeks(
    tool: ModuleType,
) -> None:
    assert tool.season(2)["opens"] == tool.season(1)["deadline"]
    assert tool.season(2)["deadline"] == "2027-02-10T12:00:00Z"
    assert tool.season(3)["deadline"] == "2027-03-10T12:00:00Z"
    for number in range(2, 40):
        this, before = tool.season(number), tool.season(number - 1)
        assert this["opens"] == before["deadline"]
        assert _at(this["deadline"]) - _at(this["opens"]) == timedelta(days=28)


def test_the_round_of_a_season_is_the_one_emitted_at_its_deadline(tool: ModuleType) -> None:
    """Published before it is emitted, and at least two weeks after the season opens."""
    beacon = _load("heldout_beacon")
    for number in range(1, 40):
        this = tool.season(number)
        assert beacon.emitted_at(this["round"]) == int(_at(this["deadline"]).timestamp())
        opened = _at(this["opens"] if "T" in this["opens"] else this["opens"] + "T00:00:00Z")
        assert _at(this["deadline"]) - opened >= timedelta(days=14)


def test_a_season_is_always_open(tool: ModuleType) -> None:
    assert tool.current(_at("2026-11-20T00:00:00Z"))["number"] == 1
    assert tool.current(_at("2027-01-13T11:59:59Z"))["number"] == 1
    # At the instant of the round the season is closed and the next is open.
    assert tool.current(_at("2027-01-13T12:00:00Z"))["number"] == 2
    assert tool.current(_at("2027-02-10T12:00:00Z"))["number"] == 3
    # Season 1 was set by hand and is longer; after it, none is longer than four weeks.
    moment = _at(tool.season(1)["deadline"])
    for _ in range(200):
        open_now = tool.current(moment)
        assert _at(open_now["deadline"]) > moment
        assert _at(open_now["deadline"]) - moment <= timedelta(days=28)
        moment += timedelta(days=11, hours=7)


def test_a_season_uses_the_benchmark_version_frozen_when_it_opens(tool: ModuleType) -> None:
    plan = dict(tool.load())
    assert tool.season(9, plan)["version"] == "benchmark-v1.7"
    plan["versions"] = [*plan["versions"], {"from_season": 5, "tag": "benchmark-v2"}]
    assert [tool.season(n, plan)["version"] for n in (4, 5, 9)] == [
        "benchmark-v1.7", "benchmark-v2", "benchmark-v2",
    ]  # fmt: skip


def test_the_calendar_on_the_page_is_the_one_the_rule_gives(tool: ModuleType) -> None:
    page = PUBLIC_TEST.read_text(encoding="utf-8")
    assert tool.rendered(page) == page, "run: python tools/season.py calendar --write"
    listed = tool.load()["listed"]
    assert listed >= 13, "a year of seasons is published ahead"
    for number in (1, 2, listed):
        assert f"| {number} | " in page and str(tool.season(number)["round"]) in page


def test_the_command_says_which_season_is_open(
    tool: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert tool.main(["current", "--at", "2027-01-20T08:00:00Z"]) == 0
    said = capsys.readouterr().out
    assert "Season 2" in said and "2027-02-10T12:00:00Z" in said
    assert str(tool.season(2)["round"]) in said and "benchmark-v1.7" in said
    assert tool.main(["calendar", "--check"]) == 0


def test_the_readme_and_the_form_name_no_season_that_will_pass() -> None:
    """They are not rewritten when a season ends, so they must not say which one is open."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    form = (REPO / ".github/ISSUE_TEMPLATE/gate-registration.yml").read_text(encoding="utf-8")
    for text in (readme, form):
        assert "Season 1" not in text and "13 January" not in text and "35679412" not in text
        assert "docs/bench/PUBLIC_TEST.md" in text
    assert "always open" in readme and "always open" in form


def _issue(number: int, opened: str, closed: str | None, version: str) -> dict[str, Any]:
    body = (
        "### 01 · Project / gate name\n\nMy Gate\n\n"
        f"### 02 · Exact version (must be fixed)\n\n{version}\n\n"
        "### 03 · How will you connect it?\n\nHTTP service (http_gate)\n\n"
        "### Project URL (optional)\n\n_No response_\n"
    )
    return {
        "number": number, "url": f"https://example.invalid/{number}", "createdAt": opened,
        "closedAt": closed, "author": {"login": "someone"}, "body": body,
        "userContentEdits": {"nodes": []},
    }  # fmt: skip


def test_a_registration_stays_for_every_season_until_its_issue_is_closed(
    tool: ModuleType,
) -> None:
    registrations = _load("season_registrations").registrations
    second = tool.season(2)
    deadline = _at(second["deadline"])
    issues = [
        _issue(1, "2026-11-25T10:00:00Z", None, "still open from season 1"),
        _issue(2, "2026-11-26T10:00:00Z", "2027-01-05T10:00:00Z", "closed in season 1"),
        _issue(3, "2026-11-27T10:00:00Z", "2027-01-20T10:00:00Z", "closed in season 2"),
        _issue(4, "2027-01-25T10:00:00Z", None, "opened in season 2"),
        _issue(5, "2027-02-11T10:00:00Z", None, "opened in season 3"),
    ]
    listed = registrations(issues, deadline, standing=True)
    assert [entry["issue"] for entry in listed] == [1, 4]
    assert listed[0]["closed_at"] is None
    # Without the opening of the season, as before: every issue opened by the deadline.
    assert [e["issue"] for e in registrations(issues, deadline)] == [1, 2, 3, 4]


def test_the_plan_is_a_small_file_anyone_can_read() -> None:
    plan = json.loads((REPO / "docs/bench/seasons.json").read_text(encoding="utf-8"))
    assert plan["length_days"] == 28
    assert plan["first_deadline"] == "2027-01-13T12:00:00Z"
    assert plan["versions"][0] == {"from_season": 1, "tag": "benchmark-v1.7"}
