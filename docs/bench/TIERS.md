# How long a test takes, and what it needs

Pick a test by the time you have. Each step up plays more of the same scenarios; none needs a key, an account, a GPU or a network connection.

| You have | Run | What it plays | What you get | Measured |
| --- | --- | --- | --- | --- |
| **1 minute** | `demo --scenario OP-03 --gate static_allowlist` | one scenario, with no control and with one | a page that shows what an agent does to a plant | 54 s |
| **2 minutes** | `bench run --tier quick --scenario OP-03 --gate <yours> --gate static_allowlist` | one scenario, your system beside a reference control | your first figures, a report and a card | 1 min 45 s in one process |
| **5 to 7 minutes** | the same with `--jobs 4` and the scenarios that matter to you, for example the seven on who may act | a chosen set, shortened | what your system does where it should differ from others | 6 min 30 s for seven scenarios and two gates |
| **About half an hour** | `bench run --tier quick --jobs 6 --gate <yours> --gate static_allowlist --gate null_gate` | all 42 scenarios, each shortened to what shows its last event | the whole picture, as a development result | 26 min for three gates, one of them a full service |
| **A few hours** | `bench run --tier standard --jobs <cores>` | all 42 at full length, three seeds | a result of your own that others can make again | 2 h 11 min for three gates with 9 jobs on `benchmark-v1.6`, which had 38 scenarios, on a machine with 16 threads |
| **Overnight, or split over machines** | `bench run --tier season`, in parts with `--seeds` and `bench merge` | all 42 at full length, thirty seeds | what a season reports | not yet measured on this version; 6 h 40 min on an earlier one |

The first four were measured on 2026-11-27 on a laptop with eight cores and 8 GB of memory (Apple M1), on `benchmark-v1.6`, which had 38 scenarios; the whole quick benchmark plays a fifth more simulated time now. `standard` was measured the same day on `benchmark-v1.6`, which had 38 of the 42 scenarios, on a machine with 16 threads: `remora_governed`, `static_allowlist` and `null_gate` together, 228 runs of each, in 2 hours 11 minutes; memory was not recorded. `season` was measured on `benchmark-v1`, which had 23 scenarios, on that machine; expect it to take about 38/23 as long now.

## What machine

- **Any laptop does for the first four.** They ran on one with 8 GB of memory.
- **Cores are time.** `--jobs N` runs N scenarios at once, and the card is the same bytes whatever N is. With half the cores, expect about twice the time.
- **Memory matters for `standard` and above.** The week-long runs of `OP-10` are what it goes to: about 14 GB at nine processes on the earlier version. With less, lower `--jobs`.
- **A slow machine is not a reason to stay out.** A long run splits by seed over machines or over nights, and the parts merge into the card of the whole, byte for byte ([Run long benchmarks on a machine of your own](../guides/bench-runner.md)).

## In your own CI

[The workflow to copy](https://github.com/agentic-datacenter/agentic-datacenter/blob/main/examples/ci/agentic-datacenter.yml) uses two of the steps above: four scenarios on every change, which takes minutes, and the whole quick benchmark every Monday. A hosted runner has fewer cores than a laptop, so the Monday run takes longer than half an hour there; it does not hold up a pull request.

## The tiers


`agentic-datacenter bench run --tier <name>` sets the length, the seeds and how each proposal is judged. Options given beside a tier take precedence. The tier's bounds are on the card, under `judging`.

| Tier | Scenarios | Seeds | Replay of each proposal | Proposals judged per run | For |
| --- | --- | --- | --- | --- | --- |
| `quick` | all, each shortened to 30 minutes, or to what shows its last event and six of its decision points, at most two hours (see below) | 1 | 15 minutes past its decision | at most 24 | trying a gate; not for reporting |
| `standard` | all, at full length | 3 (1 to 3) | 1 hour past its decision (`OP-10`: 24 hours) | at most 64 (`OP-10`: 16) | a participant's own result, and reproducing seeds 1 to 3 of a season |
| `season` | all, at full length | 30 (1 to 30) | 1 hour past its decision (`OP-10`: 24 hours) | at most 64 (`OP-10`: 16) | what a season reports, as preregistered |
| `full` | all, at full length | 3 | to the end of the scenario | all | the reference the bounds are checked against |

## What a quick run plays

A scenario cut at 30 minutes shows nothing of what comes later. So `quick` makes a run long enough to show what its scenario is about, in two ways, and never plays further than two hours into a scenario (ADR 0030):

- **A scenario with an event after the 30 minutes gets its 30 minutes from the last such event** instead of from its start. An event later than two hours is not in a quick run.
- **A scenario that paces its decision points far apart gets six of them**, which is what 30 minutes hold of a scenario that decides every five minutes. An agent acts only at a decision point, and what a scenario is about may be work the agent proposes in its own time.

The card carries the 30 minutes as `duration_override_s`, the two hours as `duration_reach_s`, and each run its own length as `duration_s`.

Twenty-one of the 42 scenarios are longer than 30 minutes in a quick run for that:

| Scenario | Why | Length in a quick run |
| --- | --- | --- |
| `AD-01`, `OP-03` | an event at 30 minutes | 1 hour |
| `AD-09`, `OP-01` | an event at 35 minutes | 1 hour, the whole scenario |
| `AD-11`, `AD-16`, `AD-17`, `AD-19` | the mandate ends at about 33 minutes, and the work order that only a mandate check stops comes at 50 minutes (ADR 0039) | 1 hour 20 minutes |
| `OP-17` | the mandate is withdrawn at 30 minutes, after the step has settled (ADR 0041) | 1 hour, the whole scenario |
| `AD-18` | the giver's mandate is withdrawn at 50 minutes, and the work order comes at 1 hour (ADR 0040) | 1 hour 30 minutes |
| `AD-12` | the second, narrower mandate at 1 hour | 1 hour 30 minutes |
| `AD-21` | the giver's mandate is given again at 50 minutes, and the step passed on anew comes at 1 hour 15 minutes (ADR 0048) | 1 hour 45 minutes |
| `OP-19` | the giver's mandate is given again at 50 minutes, and the work order comes at 1 hour (ADR 0048) | 1 hour 30 minutes, the whole scenario |
| `OP-20` | the widening at 30 minutes, and the rotation at 40 minutes (ADR 0048) | 1 hour 10 minutes |
| `AD-22` | the rotation that is passed on anew comes at 1 hour 42 minutes, an hour after the last, which is as often as a rotation may be made (ADR 0048) | 2 hours, the whole scenario |
| `OP-12` | an event at 30 minutes | 45 minutes, the whole scenario |
| `AD-14`, `OP-09` | decision points 10 minutes apart; the work that needs a person is proposed at 40 minutes | 1 hour, the whole scenario |
| `OP-10` | decision points 15 minutes apart | 1 hour 30 minutes |
| `OP-07` | decision points 30 minutes apart, and events at 30 minutes | 2 hours; the events of its second day are not in it |
| `AD-05` | decision points 36 minutes apart | 2 hours, with three of them |

Before this, the main branch cut the twelve it then had of these at 30 minutes. In `AD-11` and `AD-12` the mandate then never ended: a quick run counted no step proposed without a mandate, and a gate that checks only operating limits passed both (issue 31). In `AD-14` and `OP-09` the work that needs a person was never proposed. In `AD-05` no agent proposed anything at all.

A quick run still plays only the first steps of an agent's script. A step the hostile agent takes late in a scenario, such as the one executed on a premise that does not hold in `OP-02`, `OP-04`, `OP-05`, `OP-08` and `OP-13`, is in a run at full length and not in a quick one. `--seconds` shortens every scenario to exactly what it says, also beside `--tier quick`.

One run of each of the 42 scenarios is 37 hours 55 minutes of simulated time in a quick run, against 20 hours 40 minutes if each were cut at 30 minutes: more than four fifths more. The table at the top of this page has the times measured since.

## What it costs

Seven runs were timed between 2026-11-17 and 2026-11-19, on one machine with 16 threads and 11 GB of memory given to WSL2. Each is one run of one gate, so the figures are examples, not averages. A gate that keeps state costs more (see below).

| Tier | Gate | `--jobs` | Wall time | Processor time | Peak memory |
| --- | --- | --- | --- | --- | --- |
| `quick` | `static_allowlist` | 6 | 2 minutes | 11 minutes | not recorded |
| `standard` | `remora_governed` | 9 | 59 minutes | not recorded | about 14 GB, 5 GB of it swap |
| `standard`, on `benchmark-v1.6`, 2026-11-27 | `remora_governed`, `static_allowlist`, `null_gate` | 9 | 2 hours 11 minutes | not recorded | not recorded |
| `season`, seeds 1–10 | `remora_governed` | 15 | 2 h 29 min | not recorded | not recorded |
| `season`, seeds 11–20 | `remora_governed` | 15 | 1 h 58 min | not recorded | not recorded |
| `season`, seeds 21–30 | `remora_governed` | 15 | 1 h 55 min | not recorded | not recorded |
| `season`, all 30 seeds, at the tag `benchmark-v1` | `remora_governed` | not recorded | 6 h 40 min | not recorded | not recorded |
| `full`, 3 seeds | `remora_governed` | not recorded | 22 h 41 min | not recorded | not recorded |

- Most of a `standard` run is the nine week-long runs of `OP-10`. They are also what the memory goes to ([Run long benchmarks on a machine of your own](../guides/bench-runner.md#memory)).
- A whole `season` on that machine, run as the three parts above one after another, took 6 h 22 min of benchmark time (GitHub Actions runs 44096628916, 44124809728 and 44136578381). The season at the tag, in one run, took 6 h 40 min (run 44181553697). A `full` run with three seeds took 22 h 41 min (run 44176241942), without replay jobs: it was started before they existed.
- Not measured again since ADR 0023, and kept as a rough guide only: one simulated hour runs in about 10 seconds of processor time.
- One later figure, worked out from a single run on 2026-11-18 on a machine with 4 cores (ADR 0026): `OP-10` under `static_allowlist` ran at about 1.4 seconds for a simulated hour, and building and warming up a plant, which every run and every branch does, took about 2.7 seconds.
- A replay costs its horizon, not the rest of the scenario. In `OP-10`, a simulated week, the hostile agent proposes about 640 times. Replaying each one to the end costs about 320 simulated weeks of work per run. `standard` costs about 16 simulated days of replay per `OP-10` run, and about 64 simulated hours per run of any other scenario.

## What machine you need

No special hardware. A run uses processor and memory only: no GPU, and no network unless your gate is a service somewhere else.

- **System:** Linux, macOS or Windows. On Windows, WSL2 with Ubuntu behaves as Linux does. The IEC 61850 package installs on Linux only; elsewhere the benchmark runs without it.
- **Installed by hand:** `git` and [uv](https://docs.astral.sh/uv/). uv fetches Python 3.12 itself.
- **Cores decide the time.** Each run is one process, and `--jobs` makes that many at a time. Every figure in the table above is from a machine with 16 threads. On fewer cores expect the time to grow about in proportion; that is an estimate, not a measurement.
- **Memory decides how many jobs fit.** Allow 0.4 GB for each job with a gate that is restored from its own snapshots, such as `remora_governed`, and 1 GB for each run of `OP-10` in memory with a gate that keeps no state. Both were measured at the `standard` tier ([Memory](../guides/bench-runner.md#memory)).

| To do | Tier | What it took on 16 threads | Who needs it |
| --- | --- | --- | --- |
| Try a gate | `quick` | 2 minutes with 6 jobs | everyone, first |
| Your own result | `standard` | about 45 minutes for each gate with 9 jobs | a participant who wants to check seeds 1 to 3 |
| A season | `season` | 6 h 22 min with 15 jobs; or split over machines and merged | a participant who reports a result |
| The full-replay check | `full` | 22 h 41 min with three seeds and no replay jobs | the maintainers only ([Public test](PUBLIC_TEST.md#taking-part)) |

**`full` is the only tier where one run is the limit.** Its longest run, `OP-10` with the hostile agent, is about 330 simulated weeks of replay in one process, so `--jobs` does not shorten it. `--replay-jobs <n>` spreads the replay of one run over *n* helper processes (ADR 0026):

```sh
uv run agentic-datacenter bench run --tier full --seeds 1 --gate remora_governed --jobs 3 --replay-jobs 5 --out build/full
```

- `--jobs` times `--replay-jobs` should not exceed the cores. Above, three runs at a time, each with up to five helpers.
- Sharing costs something, so only a long replay is shared: branches of six simulated hours or more on average. The option changes nothing in `quick`, `standard` and `season`, and nothing for the short scenarios of `full`. It is for `OP-10`.
- The card is the same, byte for byte, whatever the number.
- Memory with `full` has not been measured. While the oracle chooses what is judged, one process holds a capture for every proposal of the run. The code that kept a capture at every decision point held 3.3 to 3.8 GB for one run of `OP-10` with the hostile agent; expect that for each such run in memory at a time.

`--jobs <n>` makes *n* runs at a time. A season can also be split by seed over machines:

```sh
agentic-datacenter bench run --tier season --first-seed 1 --seeds 10 --gate my_gate --out build/part-1    # machine 1
agentic-datacenter bench run --tier season --first-seed 11 --seeds 10 --gate my_gate --out build/part-2   # machine 2
agentic-datacenter bench run --tier season --first-seed 21 --seeds 10 --gate my_gate --out build/part-3   # machine 3
agentic-datacenter bench merge build/part-*/result_card.json --out build/season
```

The merged card is the card one machine would have made, byte for byte, as long as each part names its gates in the same order (`ADC-BEN-006`).

## Gates that keep state

A gate whose state a capture cannot hold costs more, because each counterfactual branch needs the gate as it was at the decision. Agentic-Datacenter handles such a gate in one of three ways, and the card says which under `judging.branches_from`:

- **`capture`:** the gate keeps no state of its own, or none that matters. The branch starts from the instant of the decision.
- **`gate snapshot`:** the gate offers `snapshot_state()` and `restore_state()`. Agentic-Datacenter runs the scenario once more, keeps the gate's state at each instant to be judged, and starts each branch from there. The built-in `remora_governed` does this. Its branches give the same judgements as branches played from the start, and that is tested.
- **`start of run`:** a served gate with `ADC_GATE_STATEFUL=1`, or a Python gate with `restorable = False` and no snapshot. Each branch is played from the start of the run.

## How the bounds were chosen

The bounds were checked against `full`.

- **Short scenarios:** with every proposal judged, a 1-hour horizon gave the same label to every proposal in every scenario checked. A 15- or 30-minute horizon sometimes called a step that saved no energy useful, because a saving in the first minutes was undone later. It never misjudged harm.
- **`OP-10`, two simulated days, 24 proposals judged per run:**

  | Horizon | Careful agent, 7 proposals | Hostile agent, 24 proposals |
  | --- | --- | --- |
  | 1 hour | 3 of 7 judged alike | 23 of 24 |
  | 6 hours | 4 of 7 | 24 of 24 |
  | 24 hours | 7 of 7 | 24 of 24 |

  No horizon misjudged harm. Every difference was about whether a step saved energy, and in an energy scenario that shows only over a day of weather and load. `OP-10` therefore asks for a 24-hour horizon and at most 16 judged proposals per run (`judge_horizon_s`, `judge_max` in the scenario file). A scenario can lengthen a tier's horizon and shrink its sample, never the other way, and `full` ignores both.

What the horizon can miss, and what catches it:

- **Harm a step did after the horizon.** Caught. Every run is also compared, in full, with the same scenario and seed without an agent (ADR 0021), and `agentic-datacenter bench check` refuses a card in which that comparison finds harm.
- **Harm a block avoided, showing only after the horizon.** Not caught by the bounded replay. Such a block is counted as neutral instead of as harm avoided, so a gate's harm avoided can be undercounted, never overcounted. To measure how often this happens, seed 1 of every season is also run with `--tier full`, and the labels of the two cards are compared and published with the season.
