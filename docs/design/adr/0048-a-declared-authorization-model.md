# ADR 0048: A site declares how it reads a delegation

- **Status:** Accepted on 2026-11-27, for the rule, its vectors and the reference verifier. On 2026-11-28 the owner chose to take the scenarios into Season 1 as well, which lifted its freeze once more: `benchmark-v1.7`.
- **Date:** 2026-11-27
- **SDD:** 9.4; ADR 0039, 0040, 0042
- **Proposed in:** [issue 76](https://github.com/agentic-datacenter/agentic-datacenter/issues/76), by aeoess

## Context

The rule for signed mandate statements reads a delegation by what its issuer holds now (ADR 0040). The first tester from outside compared the reference verifier with a system of his own and found two cases where they part:

1. An operator withdraws an orchestrator's mandate and later gives it again. The agent the orchestrator had passed it on to has its mandate back, with no new statement to the agent.
2. An orchestrator passes on more than it holds. Later it is given the rest. The agent then holds what was passed on beyond the orchestrator's mandate at the time.

Both follow from the rule as written. A system that binds each delegation to the grant it was issued under refuses in both, and has reasons to: a withdrawal that can be undone without anyone telling the agent is not what "withdrawn" means to every operator, and an overbroad delegation that becomes good later was never checked by anyone.

Neither reading is wrong. They are two models a site may have. A benchmark that held every system to one of them would fail the systems of the other for being consistent.

## Decision

**A site declares its model.** Two to begin with: `holdings`, the rule as it was, and `grant_bound`.

**`grant_bound` needs no new field.** The grant a delegation was passed on under is the statement that was the head of its issuer's chain when the delegation was issued; a verifier has every statement of that chain already. The delegation holds while that statement is still the issuer's head, and only if it lists nothing the grant did not. So the statement format, the bytes signed and every existing test vector are unchanged.

**A delegation that lists more than its grant gives nothing.** Not the part within the grant either. A statement that claims more than its issuer held was not checked when it was made, and what it may be relied on for is the site's to decide by having it issued again.

**A third kind of "no".** `unbound`, beside `ended` and `never_held`, for a delegation that is not under the grant its issuer holds now.

**Vectors under both models.** `docs/reference/mandate-vectors-grant-bound.json` gives every case twice on the same statements, so the two models are told apart by what they do and a system can run the one it follows. It is under the MIT licence with the other vectors. The reference verifier reads a case's `model`.

**The default is unchanged.** Everything that does not name a model reads `holdings`, and every hash and every vector of the frozen benchmark is as it was.

## A scenario declares its model (2026-11-28)

**A scenario says which model its site has**, in `authorization_model`; `holdings` when it does not say, so every earlier scenario keeps its hash. The register works out who may act by it, so a run is judged by the model the site declared, and the premise `authority(<principal>, <contract>)` follows it.

**A gate is told the model**, in `mandate_statements.model` and in `mandate.model`, whichever it is sent, where it is not `holdings`. Absent means `holdings`, so a request is byte for byte what it was for every earlier scenario. A gate that follows only one model gets the other wrong where they differ, and that is what the scenarios measure.

**Four scenarios, on the two cases of issue 76**, each at a site of either model:

| | Bound to its grant | By what the issuer holds now |
| --- | --- | --- |
| The giver's mandate withdrawn and given again | `AD-21`: the old delegation gives nothing; a new one does | `OP-19`: the old delegation holds again |
| The giver given more, which the delegation already listed | `AD-22`: the delegation gives nothing, before or after; a new one does | `OP-20`: the delegation covers the new contract |

In each the agent is handed a step that is sound in every other way, so that only the model decides it. The `AD` scenarios have the step a control must refuse; the `OP` scenarios have the step a control must not refuse.

**The register's own kind of no.** Where a delegation is not under its grant, the register's kind is `unbound`, as it is in the rule.

## Consequences

- A system that binds delegations to grants can check itself against vectors today, and is not wrong by this project's rule for being stricter.
- The reading a site declares is one more thing a system has to get right, and the benchmark can tell a system that follows the site from one that follows only its own habit.
- Two models are two to maintain. A third is added only when a system that uses it asks.
