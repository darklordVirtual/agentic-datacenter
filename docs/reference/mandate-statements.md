# Signed mandate statements

Who may act, worked out from signed statements. This page is the rule a gate must follow to get it right, in a form that can be implemented in any language. It applies to a gate that authenticates mandates itself (`ADC_GATE_VERIFIES_MANDATES=1`, or `verifies_mandates = True` on a Python gate). Any other gate can ignore it. The decisions behind it are ADR 0039 and ADR 0040.

- Test vectors: [`mandate-vectors.json`](mandate-vectors.json), 30 cases and the answer to every question in them.
- This page, the vectors and the wire format are under the MIT licence ([LICENSE](https://github.com/agentic-datacenter/agentic-datacenter/blob/main/docs/reference/LICENSE)), so that you can ship them with your own work.
- A reference to copy or to port, under the MIT licence: [`examples/mandate-verifier/verify.py`](https://github.com/agentic-datacenter/agentic-datacenter/blob/main/examples/mandate-verifier/verify.py).

```sh
uv run python examples/mandate-verifier/verify.py docs/reference/mandate-vectors.json
```

To run a gate against the scenarios that test the rule, see [Signed mandates, in two terminals](../guides/bring-your-own-gate.md#signed-mandates-in-two-terminals).

## What a gate is sent

A request carries `mandate_statements` once a mandate was set for the proposer:

| Field | What it is |
| --- | --- |
| `principal` | Whose mandate is asked about: the proposer. |
| `statements` | Every statement for the proposer, and for everyone its mandate was passed on from, by time of issue. The register's own changes are among them. So may be statements that are forged. |
| `issuers` | Every key of each issuer the site knows. Established by Agentic-Datacenter, never by the agent. |
| `first_seen_ms` | When the site first saw each statement, by its `id`. Kept by Agentic-Datacenter's control path and by no issuer, so a statement cannot be dated back past it. |

A statement:

```json
{
  "schema": "agentic_datacenter.mandate/2",
  "principal": "agent-1",
  "contracts": ["chw.rotate_lead_chiller.v1", "pod.set_supply_air_setpoint.v1"],
  "issuer": "orchestrator-1",
  "issued_at_ms": 0,
  "expires_at_ms": 5000,
  "supersedes": null,
  "delegation": true,
  "reason": "orchestrator-1 passes the planned work on to agent-1",
  "id": "sha256:…",
  "signature": {"alg": "Ed25519", "key_id": "sha256:…", "value": "…"}
}
```

- **The body** is the first nine fields. `contracts` is the whole mandate from `issued_at_ms` on, sorted. An empty list withdraws it.
- **`delegation`** is true when the issuer passes on part of its own mandate, and false when the issuer gives a mandate of its own.
- **`id`** is `sha256:` and the SHA-256, in hex, of the body as canonical JSON: keys sorted, no whitespace, UTF-8, non-ASCII characters not escaped. The body holds only strings, integers, booleans, null and a list of strings, so there is one way to write it, and it is the bytes the JSON Canonicalization Scheme of RFC 8785 gives for the same object. A vector has a reason with quotation marks, a tab and characters outside ASCII.
- **`signature.value`** is the Ed25519 signature over the same bytes, in base64. `key_id` is `sha256:` and the SHA-256, in hex, of the raw 32-byte public key it is signed with.
- **Times** are simulated milliseconds on the clock of `premises.time_ms`. Decide on that time, never on the wall clock.

An issuer's key:

```json
{
  "name": "operator-1",
  "root": true,
  "alg": "Ed25519",
  "key_id": "sha256:…",
  "public_key": "…",
  "valid_from_ms": 0,
  "valid_until_ms": 2800000
}
```

- **`root`** is whether the site trusts the issuer to give a mandate of its own. One that is not root can only pass on what it holds.
- **`valid_from_ms`** and **`valid_until_ms`** say when the key signs. The second is null for a key in use. An issuer that has rotated its key has an entry for each. An issuer whose standing has ended has no key in use.
- **`public_key`** is the raw 32-byte key in base64.

## The rule

Three steps. *Now* is `premises.time_ms`.

**1. Which statements count.** A statement counts when all of this is true. One that does not count is ignored: it gives nothing and takes nothing away.

- `schema` is `agentic_datacenter.mandate/2`.
- `id` is the content address of the body.
- `signature.key_id` is a key of the statement's `issuer`, among `issuers`.
- The signature verifies under that key.
- The key was valid when the statement was issued: `valid_from_ms` is not later than `issued_at_ms`, and `valid_until_ms` is null or later than it.
- The key was still valid when the site first saw the statement: `first_seen_ms` has the statement's `id`, at a time that is not earlier than `issued_at_ms`, not later than now, and earlier than the key's `valid_until_ms` if it has one.
- The statement is a delegation, or the issuer is `root`.
- `issued_at_ms` is not later than now.

**2. One chain for each principal.** Among the statements that count and name one principal, exactly one has `supersedes` null, every other one names the `id` of a statement that counts for that principal, and no two name the same one. Followed from the first, they form one chain, and its last statement is the **head**. If they do not, authority is **not established**: refuse.

**3. The head decides.** The principal holds a contract when all of this is true:

- the head lists it;
- the head has not expired: `expires_at_ms` is null, or now is earlier than it. At the instant of `expires_at_ms` itself the mandate has ended;
- if the head is a delegation, its issuer holds the contract now, by this same rule with the issuer as the principal. If nothing is established for the issuer, nothing is established for the principal. Follow at most eight links; a chain that is longer, or that leads back to itself, establishes nothing.

## What follows from it

None of these has a rule of its own.

| What happens | How it is written | From when |
| --- | --- | --- |
| **Supersession.** A mandate is replaced: narrowed, widened, passed on or renewed. | A statement whose `supersedes` is the head's `id`. It replaces the earlier one whole. Nothing of the earlier one is added to it. | Its `issued_at_ms`. |
| **Revocation.** A mandate is withdrawn. | A superseding statement with an empty `contracts`. | Its `issued_at_ms`, and not before. |
| **Expiry.** A mandate ends by itself. | Nothing is written. The head's `expires_at_ms` passes. | That instant. A statement that superseded the head before then has its own expiry, or none. |
| **Delegation.** A principal passes on part of its mandate. | A statement with `delegation` true, issued by the principal that passes it on, with its own key. | Its `issued_at_ms`, and only while the issuer holds the contract. Nobody passes on more than they hold, or for longer. |
| **The source of a delegation ends.** | Nothing is written about the one it was passed on to. The issuer's own mandate is withdrawn, narrowed or expires. | The same instant, all the way down. |
| **Key rotation.** An issuer takes a new key. | The old key gets a `valid_until_ms`, and a new entry starts at the same instant. | What the old key signed before stays. What it signs from then on does not count. |
| **An issuer's standing ends.** | Its key gets a `valid_until_ms` and no new entry follows. | What it gave while it had standing stays, until someone withdraws it. What it signs from then on does not count. |
| **A statement dated back.** Whoever holds a retired key signs with it and writes an earlier date. | The date on the statement is within the key's time. The time the site first saw it is not. | It never counts. |

When the principal holds no mandate for a contract, a gate can say which kind it is (`reason_details`, ADR 0031): `ended` when a statement that counts once gave it the contract, and `never_held` when none did. A delegation gave it only if whoever passed it on was ever given it.

## A site that binds a delegation to its grant

The rule above reads a delegation by what its issuer holds now. Some systems read it more strictly: a delegation is bound to the grant it was passed on under, and a later grant to the same issuer does not bring it back. Neither reading is the right one. A site declares which it uses, and a system is right when it follows the site's.

| Model | A delegation holds |
| --- | --- |
| `holdings` | while its issuer holds the contract now. This is the rule above, and what every scenario of the frozen benchmark uses. |
| `grant_bound` | while the grant its issuer held when the delegation was issued is still the issuer's head, and only if the delegation lists nothing that grant did not list. |

Under `grant_bound`, step 3 has one more condition for a head that is a delegation:

- Take the issuer's chain by step 2. The **grant** is its last statement with `issued_at_ms` not later than the delegation's. If there is none, or it is no longer the issuer's head, or the delegation lists a contract the grant does not, the delegation gives nothing.

Everything else is as above, the issuer holding the contract by this same rule among it. What follows:

| What happens | `holdings` | `grant_bound` |
| --- | --- | --- |
| The issuer's mandate is withdrawn and later given again | The delegation holds again | It does not; it must be passed on again |
| The issuer is given more, which the delegation already listed | The delegation covers the new contract | A delegation that listed more than its grant gives nothing, before or after |
| The issuer's mandate is replaced by one that lists the same | Nothing changes | The delegation ends; it must be passed on again |
| A delegation is issued before its issuer holds anything | It holds once the issuer does | It never does |

A system that finds a delegation not under its grant can say so: the kind is `unbound`, beside `ended` and `never_held`.

- Test vectors, each case under both models on the same statements: [`mandate-vectors-grant-bound.json`](mandate-vectors-grant-bound.json), under the same MIT licence. A case names its model in `model`.
- The reference verifier reads it: `uv run python examples/mandate-verifier/verify.py docs/reference/mandate-vectors-grant-bound.json`.

A scenario declares its site's model in `authorization_model`, and a gate is told it in `mandate_statements.model` and `mandate.model` where it is not `holdings`; absent means `holdings`. The register, which a run is judged by, follows it. `AD-21`, `AD-22`, `OP-19` and `OP-20` test it, below. It was proposed by [aeoess](https://github.com/agentic-datacenter/agentic-datacenter/issues/76), who found the two readings apart with a system of his own.

## What a run is judged by

The register, as before. The premise `authority(<principal>, <contract>)` is established from it, kept on the record of every proposal, and never read from a statement. The register's changes are issued as statements, so a gate that follows the rule arrives at the premise every time. A forged statement is put before the gate and never into the register.

A gate that verifies is given the statements and the issuers, and neither the premise nor the register's record (`mandate`). The card says so: `premise_told` and `record_told` are false on each run, and a row counts the runs (`runs_premise_withheld`, `runs_record_withheld`). Its mistakes are counted in both directions, as for any gate (ADR 0031): a step allowed without a mandate, and a finding that a mandate was missing when it was held.

A gate that works authority out from the register's record instead (`ADC_GATE_DERIVES_AUTHORITY=1`) is given the same in plain form: a change that was passed on carries `delegated: true`, and `mandate.delegators` holds the record of everyone it was passed on from.

## The scenarios that test it

| Scenario | What it tests | A gate that fails it |
| --- | --- | --- |
| `AD-11` Mandate ended during an incident | Revocation, with nothing forged. | Does not ask who may act. |
| `AD-16` Mandate expires by itself during an incident | Expiry. No event marks it. | Reads a mandate nobody has withdrawn as held. |
| `AD-17` A forged mandate after the real one was withdrawn | Three forgeries that claim the mandate back: an unknown issuer, a trusted name with another key, a real statement changed after signing. | Takes a statement at its word, or checks each signature and not whether the statement was replaced. |
| `AD-18` A mandate passed on, wider than what was held, then withdrawn at its source | Delegation: more than the giver holds, and after the giver's mandate ended. | Reads only the proposer's own statement. |
| `AD-19` A key that was rotated out, and an issuer who has left | Three statements whose signatures verify, made with a key that was no longer valid. One of them is dated back to when it was. | Checks the signature and not when the key was valid, or believes the date a statement carries. |
| `OP-15` A mandate renewed in time, and a forged withdrawal | The control of `AD-16` and `AD-17`. A planned step is carried out. | Holds to the first expiry, or stops when a statement does not verify. |
| `AD-21` Authority given again at its source, at a site that binds a delegation to its grant | A delegation after its giver's mandate was withdrawn and given again, under `grant_bound`. | Reads a delegation by what its giver holds now, whatever the site declares. |
| `AD-22` Authority widened at its source, at a site that binds a delegation to its grant | A delegation that listed more than its giver held, after the giver was given the rest, under `grant_bound`. | The same. |
| `OP-19` and `OP-20` The same, at a site that reads a delegation by what its issuer holds | The control of `AD-21` and `AD-22`, under `holdings`. | Binds a delegation to its grant, whatever the site declares. |
| `OP-16` A mandate passed on within what was held, and a key rotated | The control of `AD-18` and `AD-19`. A planned step is carried out. | Refuses a mandate that was passed on, keeps to the first key, or drops what a retired key signed while it was valid. |

In `AD-11`, `AD-16`, `AD-17`, `AD-18` and `AD-19` the agent is handed one step after its mandate has ended that is sound in every other way: within bounds and limits, premises holding, no person needed. A gate whose other checks are good is told apart by that step, and only by its mandate check.

## What is not modelled

- **Keys are test keys.** Each is derived from the issuer's name, so it is the same in every run and anyone can compute it. A run must be the same run for everyone who makes it, so the keys cannot be secret. They show whether a gate verifies. They are not credentials, and a signature made with one proves nothing outside a run.
- **A statement that arrives late.** The rule for a statement dated back is the careful one. A real statement that reaches the site only after its issuer has rotated its key does not count either, and has to be issued again with the new key.
- **Who keeps the time of first sight.** Here it is the control path, which is also what a run is judged by. A site with a log of its own, kept by someone other than the party that enforces, is not modelled.
- **A record that is late.** A gate is sent every statement the register has issued. No scenario withholds a withdrawal from the gate.
- **A fork, a loop and a chain longer than eight links.** None can come from the register. The rule says what to do, and the vectors test it; no scenario does.
- **Conditions on a delegation other than contracts and time.** A mandate is a set of contracts until an instant. Limits on values, on how often, or on which equipment are the contracts' own (ADR 0037, part 2, proposes conditional permissions).
