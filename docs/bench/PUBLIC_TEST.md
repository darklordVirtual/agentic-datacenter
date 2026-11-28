# Public test

**Registration is always open.** Seasons follow one another, four weeks each, on a frozen benchmark. [Register your gate](https://github.com/agentic-datacenter/agentic-datacenter/issues/new?template=gate-registration.yml) with three fields; installation and the interface check can follow. The [calendar](#seasons) says when the open season closes.

New here? [How the benchmark works](HOW-IT-WORKS.md) explains it on one page, and how to run it at any time without a season.

How a governance gate from any project is tested on Agentic-Datacenter in the open, and what makes the result worth something. The question, hypotheses and analysis are those of the [preregistration](PREREGISTRATION.md); this page is the procedure.

## Seasons

A public test runs in seasons, one after another without a gap, so a season is always open.

- **Season 1** opened on 2026-11-20 and closes on 2027-01-13 at 12:00 UTC. It was first set to close on 2026-12-02, and was moved before that round so that the repository is public for weeks before registration closes ([preregistration](PREREGISTRATION.md), 2026-11-20).
- **Every later season** opens at the instant the one before it closes, and closes four weeks later at 12:00 UTC.

A season fixes three things before any participant can know its held-out scenarios:

1. **A frozen benchmark version.** `agentic-datacenter bench freeze` writes the hashes of the profile, contracts, scenarios and oracle to a file, and the version is tagged. A season uses the version that is frozen when it opens. Today that is `benchmark-v1.7` ([VERSION.json](VERSION.json)). It freezes all 42 scenarios of the library: the 23 of `benchmark-v1`, `AD-11` to `AD-14` (ADR 0028), `AD-15` and `OP-14` (ADR 0038), `AD-16`, `AD-17` and `OP-15` (ADR 0039), `AD-18`, `AD-19` and `OP-16` (ADR 0040), `AD-20`, `OP-17` and `OP-18` (ADR 0041), and `AD-21`, `AD-22`, `OP-19` and `OP-20`, proposed from outside (ADR 0048). The records of `benchmark-v1` to `benchmark-v1.6`, none of which ran a season, are in `versions/`. A new version is named in `seasons.json`, with the season it first applies to, before that season opens. A card from any other version is refused for the season.
2. **A beacon round.** The secret that draws the held-out variants of Track D is the output of the drand "quicknet" public randomness beacon at the round emitted when the season closes. The rounds of the coming year are in the calendar below, so each is published long before it exists. No one, the authors of Agentic-Datacenter included, can know it before the round is emitted; afterwards anyone can fetch it and check it (`tools/heldout_beacon.py`).
3. **A registration deadline**, the instant the round is emitted. A gate takes part in a season's Track D only if it was registered before then, with the exact version it will run.

<!-- seasons: made by tools/season.py from docs/bench/seasons.json; do not edit -->
| Season | Opens (UTC) | Registration closes and the round is emitted (UTC) | Beacon round | Benchmark version |
| --- | --- | --- | --- | --- |
| 1 | 2026-11-20 | 2027-01-13 12:00 | `35679412` | `benchmark-v1.7` |
| 2 | 2027-01-13 12:00 | 2027-02-10 12:00 | `36485812` | `benchmark-v1.7` |
| 3 | 2027-02-10 12:00 | 2027-03-10 12:00 | `37292212` | `benchmark-v1.7` |
| 4 | 2027-03-10 12:00 | 2027-04-07 12:00 | `38098612` | `benchmark-v1.7` |
| 5 | 2027-04-07 12:00 | 2027-05-05 12:00 | `38905012` | `benchmark-v1.7` |
| 6 | 2027-05-05 12:00 | 2027-06-02 12:00 | `39711412` | `benchmark-v1.7` |
| 7 | 2027-06-02 12:00 | 2027-06-30 12:00 | `40517812` | `benchmark-v1.7` |
| 8 | 2027-06-30 12:00 | 2027-07-28 12:00 | `41324212` | `benchmark-v1.7` |
| 9 | 2027-07-28 12:00 | 2027-08-25 12:00 | `42130612` | `benchmark-v1.7` |
| 10 | 2027-08-25 12:00 | 2027-09-22 12:00 | `42937012` | `benchmark-v1.7` |
| 11 | 2027-09-22 12:00 | 2027-10-20 12:00 | `43743412` | `benchmark-v1.7` |
| 12 | 2027-10-20 12:00 | 2027-11-17 12:00 | `44549812` | `benchmark-v1.7` |
| 13 | 2027-11-17 12:00 | 2027-12-15 12:00 | `45356212` | `benchmark-v1.7` |
<!-- /seasons -->

- **Which season is open now:** `python tools/season.py current`. The page is not rewritten when a season ends.
- **Where it comes from:** `docs/bench/seasons.json` holds the first deadline, the length of a season and the benchmark versions. The calendar is made from it, and a test fails if the two differ.
- **Check a round's time yourself:** `python tools/heldout_beacon.py round-at 2027-01-13T12:00:00Z`.

**What counts as registered.**

- An issue opened with the registration form takes part in the season that is open when it is opened, and in every season after it for as long as the issue stays open. Close the issue to leave.
- The version that counts for a season is the one the issue names at that season's deadline. GitHub keeps every edit of an issue, so that version is read from the issue's history, not from the issue as it is later.
- Whether an issue was open at a deadline is read from its close and reopen events, not from whether it is closed today. An issue closed at the deadline and opened again later did not take part.
- Nothing is guessed. If the text or the state of an issue at the deadline cannot be established from its history, the issue is listed with that said and without a version: an entry removed from the edit history, or a history that does not hold the whole text after each edit. The list carries the SHA-256 of each text it read.
- Right after a deadline the maintainers run `python tools/season_registrations.py --season <n>` and commit the list it writes, `docs/bench/season-<n>-registrations.json`. Anyone can run it again and get the same list.
- The built-in gates are registered by what the frozen version pins: `REMORA_PIN` and the code at the version's tag.

**What a season does not do by itself.** The calendar needs no one. The work of a season does: listing the registrations after the deadline, the maintainers' own runs, and publishing the results. A season nobody reported a result for has an empty result list, and that is said.

## Taking part

1. **Register a fixed version first.** Use the [gate registration form](https://github.com/agentic-datacenter/agentic-datacenter/issues/new?template=gate-registration.yml). Only three fields are required: project name, exact immutable version (full commit SHA, package hash or image digest), and intended connection method (choose "not sure" if needed). You do **not** need to install anything or paste test output to register. You may change the version until the deadline; the one the issue names at the deadline is the one that runs in Track D.
2. **Connect and check your gate later.** Run `agentic-datacenter gate check <gate>` (see [Test your own gate](../guides/bring-your-own-gate.md)), and add the output and operating profile to your issue before submitting a result. A registration alone is not a passing check or a benchmark result. If integration changes the version you registered, update it **before** the deadline to remain eligible for Track D.
3. **Run the public tracks at any time** on the frozen version, at its tag. The main branch carries the next version while it is being made, and a season's run made there is refused.

    ```sh
    git checkout benchmark-v1.7 && uv sync    # the tag of the season's version, from the calendar
    uv run agentic-datacenter bench run --tier season --gate <gate> --gate static_allowlist --jobs 8 --out build/season1
    ```

    The 30 seeds can be split over machines with `--first-seed` and `--seeds`, and the parts merged with `agentic-datacenter bench merge` ([Tiers](TIERS.md)). To try a gate first, use `--tier quick`. A card from `--tier standard` is seeds 1 to 3 of the season, and can be checked against them.

4. **Run Track D after the round:**

    ```sh
    python tools/heldout_beacon.py secret --round <round> --out build/heldout.secret
    uv run agentic-datacenter bench run --tier season --gate <gate> --jobs 8 \
        --heldout-secret-file build/heldout.secret --out build/season-d
    uv run agentic-datacenter bench check build/season-d/result_card.json --gate <gate>
    ```

5. **Check it for publication:** `uv run agentic-datacenter bench check <card> --gate <gate> --publication`. It refuses an incomplete card and one not of the frozen version.
6. **Report it** with the "Gate result" issue form. It is published as a self-reported result (below). A registration that consents to be listed by name puts the system in [the table of enrolled systems](participants.md), with its latest figures beside the built-in gates.

**The full-replay check is the maintainers' work, not a participant's.**

- The maintainers run seed 1 of every season with `--tier full`, on a machine of their own, for the built-in gates `static_allowlist` and `remora_governed` at least: one gate that keeps no state and one that does.
- What is published with the season is the comparison: for each proposal, whether the bounded replay and the replay to the end gave it the same label. It checks the season's bounds. It is not a result of a gate and is ranked nowhere.
- A participant is not asked to run `full`, and it is no condition for a result.
- The maintainers do not run a participant's gate. Nothing of a participant's runs on the maintainers' machine: every card is made by whoever reports it, and checked by its SHA-256 (see Reproduction).
- A participant who wants the same check for their own gate can run `--tier full --seeds 1 --replay-jobs <n>` and attach the card. It is published beside their result and changes nothing in it.

## Built-in gates

| Gate | Project | Profile |
| --- | --- | --- |
| `null_gate` · `static_allowlist` | baselines: no gate; role and range checks | `UNGOVERNED` · `STATIC_ALLOWLIST` |
| `remora` · `remora_governed` · `remora_shadow` | REMORA, pinned in `REMORA_PIN` | `DECISION_ONLY` · `GOVERNED` · `SHADOW` |

The built-in gates are written by Agentic-Datacenter's authors and are reported apart from the others. The maintainers ran one season of `remora_governed` at the tag `benchmark-v1`, a version that ran no season and was replaced, by five later versions and then `benchmark-v1.7`, before the repository was public; it is a development run and is not reported. Every run so far is a development run.

## Verified and self-reported results

Every published result says which of two kinds it is. A list of results shows the kind beside each card, and does not rank the two kinds together without saying so.

| | Verified | Self-reported |
| --- | --- | --- |
| Run by | the maintainers | a participant |
| Where | the benchmark runner (`.github/workflows/bench-runner.yml`), on a machine of the maintainers | the participant's own machine |
| Code | the season's frozen tag, checked out by the workflow (`gh workflow run bench-runner.yml --ref <tag>`) | what the participant says they checked out |
| Record | `provenance.json` from the workflow run, attested once the repository is public ([Where a result came from](../guides/bench-runner.md#where-a-result-came-from)) | the card and its SHA-256, and `bench record` if the participant ran it |
| Which gates | the built-in gates only, because the maintainers do not run a participant's gate | any gate |

Before publication the maintainers run `bench check --publication` on a self-reported card. That shows the card is complete and of the frozen version. It does not show that the run happened as reported. A self-reported result becomes reproduced when someone other than the gate's authors makes a card with the same SHA-256 (see Reproduction); it is then marked as reproduced, and is still self-reported.

## Reproduction

A result card is the same, byte for byte, for the same frozen version, gate, agents and seeds, whatever the machine and the number of processes (`ADC-BEN-006`). A result counts as reproduced when someone other than the gate's authors makes a card with the same SHA-256. Every participant who runs a built-in gate beside their own therefore also reproduces part of the benchmark on a second machine, which the preregistration asks for before any result is published.

A gate that is not deterministic, or a served gate that keeps state between runs, cannot be reproduced; its card says what it says once.

## What a season does not settle

- The scenarios of the public tracks are in this repository, and a gate may have been tuned to them. Only Track D is held out.
- The simulator, contracts and oracle are written by Agentic-Datacenter's authors. A gate written by them is reported apart from the others.
- A card is a simulation result. It says nothing about a real facility.
