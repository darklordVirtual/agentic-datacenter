# Entries of enrolled systems

One file per system, `<id>.yaml`, added only after its maintainers enrolled it themselves with the registration form and ticked the consent to be listed by name. `tools/participants.py` checks every entry and writes [`../participants.md`](../participants.md) from them; a test fails when an entry lacks its registration issue or its consent, or the page is not up to date.

```yaml
id: my-gate                      # the file name
name: My Gate                    # as the maintainers gave it
gate: my_gate                    # the name on its cards (ADC_GATE_NAME)
version: 40-character commit SHA, package hash or image digest
connection: http_gate            # http_gate, command_gate, package or chain_gate member
relation: independent            # or benchmark_authors
provides: [decisions]            # one or more of decisions, findings, evidence, verification
capabilities:                    # what of the interface it uses: uses, partial or not_used
  allow_deny: uses               # allow_deny, asks_a_person, abstains, reason_codes,
  verifies_mandates: partial     # reads_premises, authority_from_record, verifies_mandates,
  signs_receipts: not_used       # verifies_readings, states_findings, signs_receipts,
                                 # keeps_state, executes_itself
enrolled:
  issue: https://github.com/agentic-datacenter/agentic-datacenter/issues/<n>
  date: 2026-12-01
  listing_consent: true          # ticked in the issue; no consent, no name
project_url: https://example.org # optional
results:                         # oldest first; the last is shown
  - card: docs/bench/results/my-gate/<version>/result_card.json
    agentic_datacenter: benchmark-v1.7
    kind: verified               # or self_reported
findings:
  - id: F-001
    scenario: AD-04
    seen_in: benchmark-v1.7
    summary: A decision reference was accepted twice.
    status: reported_privately   # then open, fixed or wont_fix
    fixed_in: null
    disclosed: null              # the date it was published; required once it is not private
```

A finding goes to the maintainers first and is described on the page once they have fixed it or agreed to its publication.

## The same rules for every system

- **Its own maintainers add it.** An entry is made from a registration issue its maintainers opened, and never on their behalf.
- **It says what it brings**, in `provides`. A system that decides nothing can take part: one that states findings, gives evidence of its own, or checks evidence.
- **It says what it uses of the interface**, in `capabilities`. A `not_used` or a `partial` is written down as carefully as a `uses`, because it says what a result of that system does not cover.
- **It says how it stands to this project**, in `relation`. A system whose maintainers are among this project's authors is listed like any other, in the order of its name, with `benchmark_authors`.
- **Being listed is not an endorsement** in either direction.

`tools/participants.py` makes two tables from the entries on the page of enrolled systems: what each system brings, and each system against each part of the interface.

