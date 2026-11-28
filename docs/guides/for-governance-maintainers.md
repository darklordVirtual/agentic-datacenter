# For maintainers of governance systems

A signature shows who made a decision. Agentic-Datacenter shows whether the decision was safe: what the plant would have done had the action been carried out, and had it not. That is evidence a governance system cannot make about itself, and this page says how to get it for yours, what it costs, and what it does not give.

Want to try it before reading on? [Your first half hour](first-half-hour.md) needs no knowledge of data centers.

## What you get

1. **Figures you can publish about your own gate.** The share of harmful steps it let through and of safe, useful steps it blocked, against a careful and a hostile agent. How often it asked a person, and how many steps were carried out without a mandate. They stand beside the built-in gates', on the same scenarios, seeds and judge, in a plain summary (`summary.md`) and a card anyone can make again byte for byte, with an evidence bundle anyone can verify.
2. **A third party that has run against your system.** A published run names the version, the scenarios and the commit it came from, so others can check it without asking you or us.
3. **Receipts checked against what happened.** Each request and its outcome carry one `correlation_id` and an `action_ref` over the action in canonical JSON. `gate verify-receipts` checks that every receipt you export names the right action, verdict and outcome. Run the same check over the receipts of several systems, and differences between them show up here, not at a customer's.
4. **Failure tests you would rarely write yourself.** A decision used twice, floods of requests, slow drift toward a limit, stale data, a mandate that ended, expired or is narrower than the step, one passed on by someone who did not hold it, a forged mandate statement, a signature made with a retired key, a gate that lets a step through when it fails instead of stopping, a dependence on the wall clock, a gate that answers differently on replay. Every decision comes with its log, so you see why.
5. **A cyber-physical test site without hardware.** Cooling, power, generators and OT protocols, simulated. With `ADC_GATE_CONTEXT=1` your gate is sent the temperatures, load, interlocks, margins and alarms it would weigh at a site, each with value, unit, quality and time.
6. **Testing that keeps going as this project grows.** A workflow you copy into your repository ([`examples/ci/agentic-datacenter.yml`](https://github.com/agentic-datacenter/agentic-datacenter/blob/main/examples/ci/agentic-datacenter.yml)) tests your gate on every change and every week, on two tracks: the frozen version, for figures that compare with everyone's, and the main branch, to see early what is coming. A change is answered in minutes, on four scenarios; the whole quick benchmark runs every Monday. Each run says where your gate did worse than on the run before (`bench compare`). A new scenario, better equipment data or a model someone contributed reaches your tests the next Monday, without your doing anything.
7. **A place beside the others, once you enrol.** No system is named in this project until its maintainers enrol it themselves and agree to be listed. Then it stands in [the table of enrolled systems](../bench/participants.md), its figures beside the built-in gates' on the same card, and weaknesses a run shows are sent to you first, and described there once you have fixed them or agreed.
8. **A pilot with others.** `chain_gate` puts several governance systems in front of the same actions. The strictest verdict decides, and each system's own verdict, reasons and receipts are kept, so a pilot shows what each one adds.

## What it costs, in order

How long each kind of test takes, from one minute to overnight, and on what machine: [How long a test takes, and what it needs](../bench/TIERS.md).

| Step | Time | Command |
| --- | --- | --- |
| Wrap your system as a command, from the MIT starter | an hour to a day | [`examples/command-gate/adapter.py`](https://github.com/agentic-datacenter/agentic-datacenter/blob/main/examples/command-gate/adapter.py) |
| Check the interface | a minute | `uv run agentic-datacenter gate check command_gate` |
| A first result beside the built-in gates | minutes | `uv run agentic-datacenter bench run --tier quick --gate command_gate --gate static_allowlist --out build/mine`, then read `build/mine/summary.md` |
| Check your receipts | a minute | [Preserve the policy's meaning](which-way-in.md#preserve-the-policys-meaning) |
| Test on every change | ten minutes once | copy the workflow above |
| A full result for publication | a few hours on one machine | `--tier standard` on a frozen version; see the [public test](../bench/PUBLIC_TEST.md) |

Testing and evaluation are free under the licence. A gate connected over HTTP or as a command holds no code of this project, and this project's licence does not reach it. A gate kept in a repository of your own is yours ([Bring your own gate](bring-your-own-gate.md#licence)).

## What you do not get

- **No certification, and no claim of safety in production.** The facility is generic and not calibrated against a real data center.
- **A result no better than the scenarios.** No benchmark result has been reported yet, and no run has been reproduced on a second machine.
- **No compliance.** A card can document testing, for example as input to a risk-management file. It is not conformity with any regulation.

## The risk, said plainly

A published card can show that harm got through. You decide what is published: nothing leaves your machine unless you send it, and a development run is labelled as one.

## Why trust a benchmark whose owner also owns a gate

The owner of this project also owns REMORA, one of the systems it measures. REMORA is [enrolled](../bench/participants.md) with the same form as any other system, listed under the same rules, and marked as its authors' own. That is also why:

- every version is frozen and tagged before it is run, and a season runs on a frozen version;
- the analysis is preregistered ([preregistration](../bench/PREREGISTRATION.md)), and the hidden scenarios are drawn from a public randomness beacon after the deadline;
- the gates written by this project's authors are marked on every card and summary;
- a card can be made again byte for byte by anyone, from the version, the seeds and the gate;
- the rules that keep different kinds of gates comparable are written down ([fairness](../bench/FAIRNESS.md)).

A run against your system published by this project says which relation it has to you.

## What comes next, and how to shape it

- **In `benchmark-v1.7` already:** a site that declares how it reads a delegation (proposed by an outside tester); signed mandates with expiry, withdrawal, delegation, an issuer's standing, key rotation and forged statements; a step in progress when its mandate ends; readings signed by their source; and a check of your receipts' signatures ([Signed mandate statements](../reference/mandate-statements.md), ADR 0039 to 0041).
- **Conditional permissions**: a permission that caps a step, judged on the step as carried out (ADR 0037).
- **More scenarios, data and participants**, which every run of the workflow picks up.

Enrol with the [registration form](https://github.com/agentic-datacenter/agentic-datacenter/issues/new?template=gate-registration.yml), and tick the box to be listed by name if you want to be. Tell us what your system needs in an issue: a field, a kind of verdict. And [propose a scenario](propose-a-scenario.md): one that joins a frozen version is run against every enrolled system from then on, yours included.
