# SPDX-License-Identifier: MIT
"""A governance gate served over HTTP, for Agentic-Datacenter's ``http_gate`` binding.

Standard library only. Replace ``decide`` with a call into your own system;
everything else is the wire format (``agentic_datacenter.gate.http/1``).

    python examples/http-gate/server.py --port 8787
    ADC_GATE_URL=http://127.0.0.1:8787 ADC_GATE_NAME=example agentic-datacenter gate check http_gate

The policy below is an example, not a recommendation: it refuses what cannot
be formed or exceeds a bound or limit, abstains when a premise cannot be
established, asks a person when the contract says so, and allows the rest.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SCHEMA = "agentic_datacenter.gate.http/1"
VERIFIER = Path(__file__).resolve().parents[1] / "mandate-verifier" / "verify.py"


def no_mandate(request: dict[str, Any]) -> str:
    """Say which kind of no mandate the proposer has, from the mandate record alone.

    ``mandate`` is in the request once a mandate was set for the proposer: every
    change, oldest first. The last one is in force while its ``until_ms`` is
    null; a change that expired by itself has the instant there. ``ended`` when
    a change held this contract, ``never_held`` when none did, and nothing when
    the mandate holds or there is no record.

    A service started with ADC_GATE_VERIFIES_MANDATES=1 is sent no record, only
    signed statements (``mandate_statements``). Those are verified, not read.
    """
    record = request.get("mandate")
    if not record:
        return no_mandate_by_statements(request)
    contract = request["proposal"]["contract_id"]
    # A change marked ``delegated`` was passed on by ``by``, whose own record is
    # among ``delegators``: it holds only while ``by`` holds the contract too.
    records = {r["principal"]: r for r in [record, *record.get("delegators", [])]}
    # How the site reads a delegation (ADR 0048); holdings when it does not say.
    model = record.get("model", "holdings")
    if holds_by_record(records, record["principal"], contract, model):
        return ""
    if unbound_by_record(records, record["principal"], contract, model):
        return "unbound"
    return "ended" if ever_by_record(records, record["principal"], contract) else "never_held"


def under_grant(records: dict[str, Any], change: dict[str, Any]) -> bool:
    """Whether a delegation is under the grant its issuer holds now (``grant_bound``).

    The grant is the issuer's change in force when the delegation was made. It
    must still be the issuer's last change, and list everything the delegation lists.
    """
    given = records.get(change["by"], {}).get("changes", [])
    earlier = [c for c in given if c["time_ms"] <= change["time_ms"]]
    if not earlier or earlier[-1] is not given[-1]:
        return False
    return set(change["contracts"]) <= set(given[-1]["contracts"])


def unbound_by_record(
    records: dict[str, Any], principal: str, contract: str, model: str, depth: int = 8
) -> bool:
    """Whether the mandate fails only because a delegation is not under its grant."""
    if model != "grant_bound" or principal not in records or depth == 0:
        return False
    last = records[principal]["changes"][-1]
    if contract not in last["contracts"] or last["until_ms"] is not None:
        return False
    if not last.get("delegated"):
        return False
    if not under_grant(records, last):
        return True
    return unbound_by_record(records, last["by"], contract, model, depth - 1)


def holds_by_record(
    records: dict[str, Any], principal: str, contract: str, model: str = "holdings", depth: int = 8
) -> bool:
    """Whether the last change of *principal* is in force, lists the contract, and may give it."""
    if principal not in records or depth == 0:
        return False
    last = records[principal]["changes"][-1]
    if contract not in last["contracts"] or last["until_ms"] is not None:
        return False
    if not last.get("delegated"):
        return True
    if model == "grant_bound" and not under_grant(records, last):
        return False
    return holds_by_record(records, last["by"], contract, model, depth - 1)


def ever_by_record(records: dict[str, Any], principal: str, contract: str, depth: int = 8) -> bool:
    """Whether any change ever gave *principal* the contract."""
    if principal not in records or depth == 0:
        return False
    return any(
        contract in change["contracts"]
        and (
            not change.get("delegated")
            or ever_by_record(records, change["by"], contract, depth - 1)
        )
        for change in records[principal]["changes"]
    )


def no_mandate_by_statements(request: dict[str, Any]) -> str:
    """The same from signed statements, with ``examples/mandate-verifier/verify.py``.

    Needs the ``cryptography`` package, and is loaded only when a request has
    statements and no record. ``not_established`` when the statements that
    verify are not one chain.
    """
    if not request.get("mandate_statements"):
        return ""
    spec = importlib.util.spec_from_file_location("mandate_verifier", VERIFIER)
    assert spec and spec.loader
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    found = verifier.from_request(request)
    if found["holds"]:
        return ""
    return found["kind"] or "not_established"


def decide(request: dict[str, Any]) -> dict[str, Any]:
    """Return the answer to one ``evaluate`` request. Must be deterministic."""
    premises = request["premises"]
    refusals = list(premises["schema_errors"]) + list(premises["bound_errors"])
    refusals += list(premises["limit_errors"])
    if not premises["contract_known"]:
        refusals.insert(0, "unknown contract")
    unknown = [p["expr"] for p in premises["premises"] if p["holds"] is None]
    failed = [p["expr"] for p in premises["premises"] if p["holds"] is False]
    # Who may act. The premise authority(<principal>, <contract>) says it, and so
    # does the mandate record. A service started with ADC_GATE_DERIVES_AUTHORITY=1
    # is sent the record alone, so this one decides from the record and reads
    # the premise only when there is no record.
    kind = no_mandate(request)
    no_authority = bool(kind) or any(e.startswith("authority(") for e in failed)
    if kind and not any(e.startswith("authority(") for e in failed):
        failed.append(f"the mandate record: {kind}")
    # Why, in a word, for every way the step is wrong, not only the first found.
    codes = [
        code
        for code, applies in (
            ("authority", no_authority),
            ("limit", bool(premises["bound_errors"] or premises["limit_errors"])),
            (
                "premise",
                any(not e.startswith(("authority(", "the mandate record")) for e in failed),
            ),
            ("evidence", bool(unknown)),
            ("malformed", bool(premises["schema_errors"]) or not premises["contract_known"]),
        )
        if applies
    ]
    more: dict[str, Any] = {}
    if request.get("mandate") or request.get("mandate_statements"):
        # Optional: this gate's own finding, compared with the premise on the card.
        more["authority_holds"] = not kind
    if kind in ("ended", "never_held", "unbound"):
        # Optional: which kind of no mandate the refusal is for.
        more["reason_details"] = {"authority": kind}
    if refusals:
        return {"verdict": "DENY", "reasons": refusals, "reason_codes": codes, **more}
    if unknown:
        return {"verdict": "ABSTAIN", "reasons": [f"cannot establish: {e}" for e in unknown],
                "reason_codes": codes, **more}  # fmt: skip
    if failed:
        return {"verdict": "DENY", "reasons": [f"premise does not hold: {e}" for e in failed],
                "reason_codes": codes, **more}  # fmt: skip
    if premises["approval"] in ("one_operator", "two_person"):
        approvers = 2 if premises["approval"] == "two_person" else 1
        return {"verdict": "VERIFY", "reasons": ["contract requires approval"],
                "required_approvers": approvers, "reason_codes": ["approval"], **more}  # fmt: skip
    if premises["approval"] == "not_available":
        return {"verdict": "DENY", "reasons": ["contract cannot be approved here"],
                "reason_codes": ["policy"], **more}  # fmt: skip
    return {"verdict": "ALLOW", "reasons": ["premises hold; within bounds and limits"], **more}


class Handler(BaseHTTPRequestHandler):
    """Answers ``POST /evaluate`` and ``POST /outcome``."""

    def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
        """Read one JSON request and answer it."""
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if body.get("schema") != SCHEMA:
            self._send(400, {"schema": SCHEMA, "error": "unknown schema"})
        elif self.path == "/evaluate":
            self._send(200, {"schema": SCHEMA, **decide(body)})
        elif self.path == "/outcome":
            self._send(200, {"schema": SCHEMA})
        else:
            self._send(404, {"schema": SCHEMA, "error": "not found"})

    def _send(self, status: int, answer: dict[str, Any]) -> None:
        data = json.dumps(answer).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Stay quiet; a benchmark makes many requests."""


def main() -> None:
    """Serve on loopback until interrupted."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"serving {SCHEMA} on http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
