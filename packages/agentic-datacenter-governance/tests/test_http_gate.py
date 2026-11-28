"""ADC-GOV-008: a gate served over HTTP is driven like any other, and fails closed."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from agentic_datacenter.governance.bindings.http_gate import (
    SCHEMA,
    VERDICT_ALIASES,
    CommandGate,
    HttpGate,
    HttpGateConfigError,
    command_from_environment,
    from_environment,
    read_verdict,
)
from agentic_datacenter.governance.conformance import check_gate
from agentic_datacenter.governance.gate import (
    AuthenticatedActor,
    BoundedWrite,
    Outcome,
    PremiseResult,
    PremiseSnapshot,
    Proposal,
    Verdict,
)

REPO = Path(__file__).resolve().parents[3]
ACTOR = AuthenticatedActor("agent", "test")
WRITE = BoundedWrite("cooling.chw_plant[CHW].chws_sp_c", 18.5, 18.0)
CLEAN = PremiseSnapshot(
    time_ms=5000, contract_known=True, risk_tier="R1", approval="none", writes=(WRITE,),
    premises=(PremiseResult("all(pod.supply_air_c <= 25.0)", True, "holds"),),
    rollback_available=True,
)  # fmt: skip
PROPOSAL = Proposal("prop-1", "chw.set_supply_setpoint.v1", {"setpoint_c": 18.5})


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _serve(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _answering(answer: Callable[[dict[str, Any]], Any]) -> type[BaseHTTPRequestHandler]:
    seen: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        requests = seen

        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, **body})
            data = json.dumps(answer(body)).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    return Handler


@pytest.fixture
def example_url() -> Iterator[str]:
    server = _load(REPO / "examples/http-gate/server.py", "example_http_gate_server")
    yield from _serve(server.Handler)


@pytest.mark.req("ADC-GOV-008")
@pytest.mark.req("ADC-GOV-004")
def test_the_example_service_passes_the_gate_interface_suite_through_the_binding(
    example_url: str,
) -> None:
    check_gate(lambda: HttpGate(example_url, name="example"))


@pytest.mark.req("ADC-GOV-008")
def test_request_carries_what_the_binding_established_and_outcomes_are_reported() -> None:
    handler = _answering(lambda body: {"schema": SCHEMA, "verdict": "ALLOW", "reasons": ["ok"]})
    for url in _serve(handler):
        gate = HttpGate(url)
        decision = gate.evaluate(PROPOSAL, CLEAN, ACTOR)
        gate.record_outcome(decision.decision_ref, Outcome("APPLIED", "read back"))
    evaluate, outcome = handler.requests  # type: ignore[attr-defined]
    assert evaluate["path"] == "/evaluate" and evaluate["schema"] == SCHEMA
    assert evaluate["premises"]["writes"] == [{"tag": WRITE.tag, "value": 18.5, "previous": 18.0}]
    assert evaluate["premises"]["time_ms"] == 5000
    assert evaluate["actor"] == {"principal": "agent", "credential": "test"}
    assert outcome == {
        "path": "/outcome", "schema": SCHEMA, "session": gate.session,
        "decision_ref": decision.decision_ref, "status": "APPLIED", "detail": "read back",
        # The same correlation as the request (ADR 0036).
        "correlation_id": evaluate["correlation"]["correlation_id"],
        "action_ref": evaluate["correlation"]["action_ref"],
        "proposal_id": PROPOSAL.proposal_id, "verdict": "ALLOW",
    }  # fmt: skip
    assert evaluate["session"] == gate.session
    assert evaluate["tool_call"] == {
        "name": PROPOSAL.contract_id, "arguments": {"setpoint_c": 18.5},
        "writes": [{"tag": WRITE.tag, "value": 18.5}],
    }  # fmt: skip
    assert decision.verdict is Verdict.ALLOW and decision.bounded_writes == (WRITE,)
    assert decision.expires_at_ms > CLEAN.time_ms and decision.single_use_nonce


@pytest.mark.req("ADC-GOV-008")
def test_the_service_cannot_widen_what_an_allow_authorizes() -> None:
    def answer(body: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": SCHEMA, "verdict": "ALLOW", "reasons": ["ok"],
            "bounded_writes": [{"tag": "electrical.mv_switchgear[MV].cb_open", "value": True}],
            "decision_ref": "mine", "expires_at_ms": 10**12,
        }  # fmt: skip

    for url in _serve(_answering(answer)):
        decision = HttpGate(url).evaluate(PROPOSAL, CLEAN, ACTOR)
    assert decision.bounded_writes == (WRITE,)
    assert decision.decision_ref.startswith("dec-")
    assert decision.expires_at_ms == CLEAN.time_ms + 30_000


@pytest.mark.req("ADC-GOV-008")
@pytest.mark.parametrize(
    "answer",
    [
        {"verdict": "ALLOW", "reasons": ["no schema"]},
        {"schema": SCHEMA, "verdict": "MAYBE", "reasons": ["bad verdict"]},
        {
            "schema": SCHEMA,
            "verdict": "allow_with_caps",
            "reasons": ["conditions Agentic-Datacenter cannot hold"],
        },
        {"schema": SCHEMA, "verdict": "ALLOW", "reasons": []},
        ["not", "an", "object"],
    ],
)
def test_an_answer_that_is_not_a_valid_verdict_abstains(answer: Any) -> None:
    for url in _serve(_answering(lambda body: answer)):
        decision = HttpGate(url).evaluate(PROPOSAL, CLEAN, ACTOR)
    assert decision.verdict is Verdict.ABSTAIN
    assert decision.bounded_writes == ()


@pytest.mark.req("ADC-GOV-008")
def test_a_service_that_does_not_answer_abstains() -> None:
    for served in _serve(_answering(lambda body: {})):
        url = served  # the server is shut down; nothing listens on the port any more
    gate = HttpGate(url, timeout_s=2)
    decision = gate.evaluate(PROPOSAL, CLEAN, ACTOR)
    assert decision.verdict is Verdict.ABSTAIN
    assert decision.reasons[0].startswith("gate service did not answer")
    gate.record_outcome(decision.decision_ref, Outcome("NOT_EXECUTED"))


@pytest.mark.req("ADC-GOV-008")
def test_an_address_outside_loopback_needs_consent() -> None:
    with pytest.raises(HttpGateConfigError):
        HttpGate("http://gate.example.org:8787")
    HttpGate("http://gate.example.org:8787", allow_remote=True)
    with pytest.raises(HttpGateConfigError):
        from_environment({})
    with pytest.raises(HttpGateConfigError):
        from_environment({"ADC_GATE_URL": "http://127.0.0.1:1", "ADC_GATE_PROFILE": "GOVERNED"})
    gate = from_environment({"ADC_GATE_URL": "http://[::1]:1", "ADC_GATE_NAME": "theirs"})
    assert (gate.name, gate.profile) == ("theirs", "DECISION_ONLY")


@pytest.mark.req("ADC-GOV-004")
def test_the_python_template_passes_the_gate_interface_suite() -> None:
    template = _load(REPO / "examples/python-gate/src/example_gate/__init__.py", "example_gate")
    check_gate(template.ExampleGate)
    assert template.create(None).profile == "DECISION_ONLY"


@pytest.mark.req("ADC-GOV-008")
def test_the_published_schema_matches_what_the_binding_sends_and_reads() -> None:
    from agentic_datacenter.governance.bindings.http_gate import request_body
    from agentic_datacenter.governance.gate import OUTCOMES

    schema = json.loads((REPO / "docs/reference/gate-http-v1.schema.json").read_text())
    assert schema["$id"] == SCHEMA
    defs = schema["$defs"]
    body = request_body(PROPOSAL, CLEAN, ACTOR)
    assert set(body) == set(defs["evaluate_request"]["required"])
    for part in ("proposal", "premises", "actor"):
        spec = defs["evaluate_request"]["properties"][part]
        assert set(body[part]) == set(spec["required"]) == set(spec["properties"])  # type: ignore[arg-type]
    aliases = {word: verdict.value for word, verdict in VERDICT_ALIASES.items()}
    assert defs["verdict_aliases"]["const"] == aliases
    assert defs["outcome_request"]["properties"]["status"]["enum"] == list(OUTCOMES)
    assert set(defs["outcome_request"]["required"]) == set(defs["outcome_request"]["properties"])


@pytest.mark.req("ADC-GOV-008")
@pytest.mark.parametrize(
    ("word", "verdict"),
    [
        ("permit", Verdict.ALLOW), ("Admitted", Verdict.ALLOW), ("block", Verdict.DENY),
        ("forbid", Verdict.DENY), ("ask", Verdict.VERIFY), ("require-approval", Verdict.VERIFY),
        ("ESCALATE", Verdict.VERIFY), ("skip", Verdict.ABSTAIN), ("out of scope", Verdict.ABSTAIN),
        ("VERIFY", Verdict.VERIFY), ("defer", None), ("modify", None),
    ],
)  # fmt: skip
def test_a_service_may_answer_in_its_own_words(word: str, verdict: Verdict | None) -> None:
    assert read_verdict(word) is verdict


@pytest.mark.req("ADC-GOV-008")
def test_an_own_word_is_read_and_kept_on_the_decision() -> None:
    handler = _answering(lambda body: {"schema": SCHEMA, "verdict": "block", "reasons": ["no"]})
    for url in _serve(handler):
        decision = HttpGate(url).evaluate(PROPOSAL, CLEAN, ACTOR)
    assert decision.verdict is Verdict.DENY
    assert dict(decision.detail)["service_verdict"] == "block"


@pytest.mark.req("ADC-GOV-008")
def test_a_stateful_service_gets_a_session_per_gate_and_is_not_restored() -> None:
    env = {"ADC_GATE_URL": "http://127.0.0.1:1"}
    one, two = from_environment(env), from_environment(env)
    assert one.session != two.session and one.restorable
    assert not from_environment({**env, "ADC_GATE_STATEFUL": "1"}).restorable


def _command(tmp_path: Path, body: str) -> str:
    script = tmp_path / "hook.py"
    script.write_text("import json, sys\nmessage = json.load(sys.stdin)\n" + body)
    return f"{sys.executable} {script}"


@pytest.mark.req("ADC-GOV-008")
@pytest.mark.req("ADC-GOV-004")
def test_a_command_gate_is_driven_over_standard_input_and_output(tmp_path: Path) -> None:
    command = _command(tmp_path, (
        "request = message['request']\n"
        "if message['endpoint'] == '/evaluate':\n"
        "    p = request['premises']\n"
        "    bad = p['schema_errors'] or not p['contract_known'] or "
        "any(x['holds'] is not True for x in p['premises'])\n"
        "    print(json.dumps({'schema': request['schema'], "
        "'verdict': 'deny' if bad else 'allow', 'reasons': ['hook']}))\n"
    ))  # fmt: skip
    gate = command_from_environment({"ADC_GATE_COMMAND": command, "ADC_GATE_NAME": "hook"})
    check_gate(lambda: CommandGate(command, name="hook"))
    decision = gate.evaluate(PROPOSAL, CLEAN, ACTOR)
    assert decision.verdict is Verdict.ALLOW and decision.bounded_writes == (WRITE,)
    gate.record_outcome(decision.decision_ref, Outcome("APPLIED"))


@pytest.mark.req("ADC-GOV-008")
def test_a_command_that_exits_2_denies_and_one_that_fails_abstains(tmp_path: Path) -> None:
    refusing = _command(tmp_path, "print('not allowed here', file=sys.stderr)\nsys.exit(2)\n")
    decision = CommandGate(refusing).evaluate(PROPOSAL, CLEAN, ACTOR)
    assert decision.verdict is Verdict.DENY and decision.reasons[0] == "not allowed here"
    failing = _command(tmp_path, "sys.exit(1)\n")
    assert CommandGate(failing).evaluate(PROPOSAL, CLEAN, ACTOR).verdict is Verdict.ABSTAIN
    with pytest.raises(HttpGateConfigError):
        command_from_environment({})


# --- the mandate record, and which kind of no mandate (issue 31, ADR 0031) -------

SETPOINT = "chw.set_supply_setpoint.v1"


def _mandate(*held: tuple[str, ...]) -> Any:
    from agentic_datacenter.governance.gate import Mandate, MandateChange

    people = ("operator-1", "access-register", "operator-2")
    changes = tuple(
        MandateChange(1000 * (index + 1), contracts, people[index], f"change {index + 1}")
        for index, contracts in enumerate(held)
    )
    return Mandate(ACTOR.principal, changes)


def _without(mandate: Any) -> PremiseSnapshot:
    """CLEAN, for a proposer the register holds *mandate* for, with the premise it gives."""
    from dataclasses import replace

    holds = SETPOINT in mandate.changes[-1].contracts
    premise = PremiseResult(f"authority({ACTOR.principal}, {SETPOINT})", holds, "from the register")
    return replace(CLEAN, premises=(*CLEAN.premises, premise), mandate=mandate)


def test_the_mandate_record_is_in_a_request_only_when_one_was_set() -> None:
    from agentic_datacenter.governance.bindings.http_gate import request_body

    assert "mandate" not in request_body(PROPOSAL, CLEAN, ACTOR)
    body = request_body(PROPOSAL, _without(_mandate((SETPOINT, "a.v1"), ())), ACTOR)
    assert body["mandate"] == {
        "principal": "agent",
        "changes": [
            {"time_ms": 1000, "until_ms": 2000, "contracts": [SETPOINT, "a.v1"],
             "by": "operator-1", "reason": "change 1"},
            {"time_ms": 2000, "until_ms": None, "contracts": [], "by": "access-register",
             "reason": "change 2"},
        ],
    }  # fmt: skip
    schema = json.loads((REPO / "docs/reference/gate-http-v1.schema.json").read_text())
    request = schema["$defs"]["evaluate_request"]
    assert "mandate" in request["properties"] and "mandate" not in request["required"]
    spec = request["properties"]["mandate"]
    assert set(body["mandate"]) == set(spec["required"])  # type: ignore[arg-type]
    # A mandate that was passed on also carries the record of who passed it on (ADR 0040).
    # Optional: delegators, and the model where a site declares one that is not holdings.
    assert set(spec["properties"]) - set(spec["required"]) == {"delegators", "model"}
    change = spec["properties"]["changes"]["items"]
    assert set(body["mandate"]["changes"][0]) == set(change["required"])  # type: ignore[index,call-overload]
    # A change that ends by itself also carries when (ADR 0039).
    assert set(change["properties"]) - set(change["required"]) == {"expires_at_ms", "delegated"}
    answer = schema["$defs"]["evaluate_answer"]
    assert "reason_details" in answer["properties"] and "reason_details" not in answer["required"]


@pytest.mark.parametrize(
    ("details", "kept"),
    [
        ({"authority": "ended"}, "ended"),
        ({"authority": "never_held"}, "never_held"),
        (
            {"authority": "a word of its own"},
            "a word of its own",
        ),  # the control path keeps it as other
        ({"authority": 7}, ""),
        ({"limit": "rate"}, ""),
        ("ended", ""),
        (None, ""),
    ],
)
def test_a_service_may_say_which_kind_of_no_mandate_it_refused_for(details: Any, kept: str) -> None:
    def answer(body: dict[str, Any]) -> dict[str, Any]:
        out = {"schema": SCHEMA, "verdict": "DENY", "reasons": ["no mandate"]}
        return {**out, "reason_codes": ["authority"], "reason_details": details}

    handler = _answering(answer)
    for url in _serve(handler):
        decision = HttpGate(url).evaluate(PROPOSAL, _without(_mandate((SETPOINT,), ())), ACTOR)
    assert decision.verdict is Verdict.DENY and decision.reason_codes == ("authority",)
    assert decision.authority_detail == kept
    (request,) = handler.requests  # type: ignore[attr-defined]
    assert [c["contracts"] for c in request["mandate"]["changes"]] == [[SETPOINT], []]


@pytest.mark.parametrize(
    ("held", "verdict", "details"),
    [
        (((SETPOINT,), ()), "DENY", {"authority": "ended"}),
        (((SETPOINT, "a.v1"), ("a.v1",)), "DENY", {"authority": "ended"}),
        ((("a.v1",),), "DENY", {"authority": "never_held"}),
        (((), ("a.v1",)), "DENY", {"authority": "never_held"}),
        ((("a.v1",), (SETPOINT,)), "ALLOW", None),
    ],
)
def test_the_example_service_reads_the_kind_from_the_record(
    held: tuple[tuple[str, ...], ...], verdict: str, details: dict[str, str] | None
) -> None:
    from agentic_datacenter.governance.bindings.http_gate import request_body

    server = _load(REPO / "examples/http-gate/server.py", "example_http_gate_server")
    answer = server.decide(request_body(PROPOSAL, _without(_mandate(*held)), ACTOR))
    assert answer["verdict"] == verdict
    assert answer.get("reason_details") == details
    if details is not None:
        assert answer["reason_codes"] == ["authority"]
    # With no record in the request it says nothing more, as before.
    assert "reason_details" not in server.decide(request_body(PROPOSAL, CLEAN, ACTOR))


def test_the_example_package_reads_the_kind_from_the_record() -> None:
    template = _load(REPO / "examples/python-gate/src/example_gate/__init__.py", "example_gate")
    gate = template.create(None)
    ended = gate.evaluate(PROPOSAL, _without(_mandate((SETPOINT,), ())), ACTOR)
    assert ended.verdict is Verdict.DENY and "authority" in ended.reason_codes
    assert ended.authority_detail == "ended"
    never = gate.evaluate(PROPOSAL, _without(_mandate(("a.v1",))), ACTOR)
    assert never.verdict is Verdict.DENY and never.authority_detail == "never_held"
    holding = gate.evaluate(PROPOSAL, _without(_mandate(("a.v1",), (SETPOINT,))), ACTOR)
    assert holding.verdict is Verdict.ALLOW and holding.authority_detail == ""
    assert gate.evaluate(PROPOSAL, CLEAN, ACTOR).authority_detail == ""


def test_a_service_may_state_its_own_finding_of_who_may_act() -> None:
    for given, kept in ((True, True), (False, False), ("yes", None), (None, None)):

        def answer(body: dict[str, Any], given: Any = given) -> dict[str, Any]:
            out = {"schema": SCHEMA, "verdict": "DENY", "reasons": ["decided"]}
            return {**out, "authority_holds": given}

        for url in _serve(_answering(answer)):
            decision = HttpGate(url).evaluate(PROPOSAL, _without(_mandate((SETPOINT,))), ACTOR)
        assert decision.authority_holds is kept
    schema = json.loads((REPO / "docs/reference/gate-http-v1.schema.json").read_text())
    answer_spec = schema["$defs"]["evaluate_answer"]
    assert answer_spec["properties"]["authority_holds"]["type"] == "boolean"
    assert "authority_holds" not in answer_spec["required"]
    assert "ignore" in schema["description"]


def test_a_service_can_ask_not_to_be_told_the_answer() -> None:
    from agentic_datacenter.governance.bindings.http_gate import (
        command_from_environment,
        from_environment,
    )

    env = {"ADC_GATE_URL": "http://127.0.0.1:1"}
    assert not getattr(from_environment(env), "derives_authority", False)
    assert from_environment({**env, "ADC_GATE_DERIVES_AUTHORITY": "1"}).derives_authority is True
    command = {"ADC_GATE_COMMAND": "true", "ADC_GATE_DERIVES_AUTHORITY": "1"}
    assert command_from_environment(command).derives_authority is True


@pytest.mark.parametrize(
    ("held", "verdict", "finding", "details"),
    [
        (((SETPOINT,), ()), "DENY", False, {"authority": "ended"}),
        ((("a.v1",),), "DENY", False, {"authority": "never_held"}),
        ((("a.v1",), (SETPOINT,)), "ALLOW", True, None),
    ],
)
def test_the_example_service_decides_from_the_record_when_it_is_not_told(
    held: tuple[tuple[str, ...], ...], verdict: str, finding: bool, details: dict[str, str] | None
) -> None:
    """The request of a gate that derives authority: the record, and no premise for it."""
    from dataclasses import replace

    from agentic_datacenter.governance.bindings.http_gate import request_body

    server = _load(REPO / "examples/http-gate/server.py", "example_http_gate_server")
    untold = replace(CLEAN, mandate=_mandate(*held))
    assert not any(p.expr.startswith("authority(") for p in untold.premises)
    answer = server.decide(request_body(PROPOSAL, untold, ACTOR))
    assert answer["verdict"] == verdict and answer["authority_holds"] is finding
    assert answer.get("reason_details") == details
    if details is not None:
        assert answer["reason_codes"] == ["authority"]
    # Told as well, it answers the same.
    told = server.decide(request_body(PROPOSAL, _without(_mandate(*held)), ACTOR))
    assert (told["verdict"], told["authority_holds"]) == (verdict, finding)
    assert "authority_holds" not in server.decide(request_body(PROPOSAL, CLEAN, ACTOR))


def test_the_example_package_decides_from_the_record_when_it_is_not_told() -> None:
    from dataclasses import replace

    template = _load(REPO / "examples/python-gate/src/example_gate/__init__.py", "example_gate")
    gate = template.create(None)
    ended = gate.evaluate(PROPOSAL, replace(CLEAN, mandate=_mandate((SETPOINT,), ())), ACTOR)
    assert ended.verdict is Verdict.DENY and "authority" in ended.reason_codes
    assert ended.authority_detail == "ended" and ended.authority_holds is False
    holding = gate.evaluate(PROPOSAL, replace(CLEAN, mandate=_mandate((SETPOINT,))), ACTOR)
    assert holding.verdict is Verdict.ALLOW and holding.authority_holds is True
    assert gate.evaluate(PROPOSAL, CLEAN, ACTOR).authority_holds is None


def test_the_example_package_asks_for_as_many_people_as_the_contract_does() -> None:
    """It asked for one person whatever the contract said, so work that needs two was
    executed on one approval (AD-14 at full length)."""
    from dataclasses import replace

    template = _load(REPO / "examples/python-gate/src/example_gate/__init__.py", "example_gate")
    gate = template.create(None)
    for approval, people in (("one_operator", 1), ("two_person", 2)):
        asked = gate.evaluate(PROPOSAL, replace(CLEAN, risk_tier="R2", approval=approval), ACTOR)
        assert asked.verdict is Verdict.VERIFY and asked.required_approvers == people


def _status(code: int, headers: dict[str, str], body: bytes) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(code)
            for name, value in {**headers, "Content-Length": str(len(body))}.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    return Handler


@pytest.mark.req("ADC-GOV-008")
def test_a_proxy_in_the_environment_never_sees_what_is_meant_for_loopback(
    example_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxied: list[str] = []
    for proxy in _serve(_answering(lambda body: proxied.append("seen") or {})):
        for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
            monkeypatch.setenv(name, proxy)
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        decision = HttpGate(example_url).evaluate(PROPOSAL, CLEAN, ACTOR)
    assert proxied == []
    assert decision.verdict is not Verdict.ABSTAIN


@pytest.mark.req("ADC-GOV-008")
def test_a_redirect_is_not_followed() -> None:
    elsewhere: list[str] = []
    for target in _serve(_answering(lambda body: elsewhere.append("seen") or {})):
        moved = _status(307, {"Location": target + "/evaluate"}, b"")
        for url in _serve(moved):
            gate = HttpGate(url)
            decision = gate.evaluate(PROPOSAL, CLEAN, ACTOR)
    assert elsewhere == []
    assert decision.verdict is Verdict.ABSTAIN and gate.failures


@pytest.mark.req("ADC-GOV-008")
def test_an_answer_too_long_to_read_abstains() -> None:
    huge = b'{"pad": "' + b"x" * (1 << 20) + b'"}'
    for url in _serve(_status(200, {"Content-Type": "application/json"}, huge)):
        gate = HttpGate(url)
        decision = gate.evaluate(PROPOSAL, CLEAN, ACTOR)
    assert decision.verdict is Verdict.ABSTAIN
    assert "longer than" in decision.reasons[0] and len(gate.failures) == 1


@pytest.mark.req("ADC-GOV-008")
def test_a_failed_call_is_kept_on_the_gate_and_a_good_one_is_not(example_url: str) -> None:
    gate = HttpGate(example_url)
    gate.evaluate(PROPOSAL, CLEAN, ACTOR)
    assert gate.failures == []
