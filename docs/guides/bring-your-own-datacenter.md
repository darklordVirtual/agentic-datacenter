# Bring your own data center

Find which failure scenarios and operating limits apply to your facility model. Start with the included example, then describe your own power, cooling and IT load. Your configuration and reports stay on your machine. The simulator runs entirely on a model.

## Try the example

From the root of the repository after [setup](../index.md#try-it), run:

```sh
uv run agentic-datacenter facility check examples/byodc
```

Open `build/facility/facility_report.md`. Start with **Scenarios** to see which tests can run and what each skipped test needs. The five-minute simulated run shows IT load served, power draw and the hottest supply air.

For a one-page comparison, run:

```sh
uv run agentic-datacenter facility report examples/byodc --scenario OP-02 --scenario OP-05 --scenario AD-07 --scenario AD-10
```

Open `build/operator-report/operator_report.md`. It compares the built-in safety control with the no-gate baseline on the example facility, with a careful and a hostile scripted agent. The four scenarios are a utility loss, an air handler failure, an unsafe human request and an attempt to operate a relay. The report names the scenarios it was made from. In this short run the agent with no control harms nothing in these four. What differs is in the last column: four steps that need a person were executed without one when nothing stood in the way, and none with the built-in control. For a run in which the uncontrolled agent does harm the plant, see the [demo](../demo/result-report.md).

The command prints a line as each of its 20 runs ends. On a laptop with eight cores it took 51 seconds, and 1 minute 32 seconds while the laptop was busy with other work. With `--jobs 1` it took 4 minutes 26 seconds.

Without `--scenario`, the comparison covers every scenario that fits the facility: 40 of the 42 for the example, in 200 runs. Expect twenty minutes or a little more with eight processes, and several times that with one. It prints a line as each run ends.

## Describe your facility

1. Run `uv run agentic-datacenter facility init my-dc` to make a starter bundle.
2. In `my-dc/`, change the example topology, PODs and equipment values. Record a source for each value or mark it as assumed.
3. Run `uv run agentic-datacenter facility check my-dc` and consult the report before comparing safety controls.

The example models six PODs on the generic electrical and cooling plant. Its values are illustrative:

| PODs | Kind | IT load (kW) |
| --- | --- | --- |
| 01, 02 | AI training | 300 |
| 03, 04 | Enterprise | 150 |
| 05, 06 | Storage | 80 |

## A bundle

```text
my-dc/
├── datacenter.yaml        # name, site, what to include; key sets and tables may live here
├── topology/*.yaml        # modules and connections: power, cooling
├── pods/*.yaml            # the POD modules, each with its own equipment model if you like
└── equipment/*.yaml       # equipment models: maker, model, evidence and values
```

`datacenter.yaml`:

```yaml
schema_version: 2
name: my_dc
site: DC7
include: ["topology/*.yaml", "pods/*.yaml", "equipment/*.yaml"]
```

An included file holds any of `key_sets`, `tables`, `modules`, `connections`, `equipment` and `economics`.

- **Merging.** Lists are joined in the order of `include`. A name defined in two files is refused, with both files named.
- **Limits.** A bundle holds at most 256 files of at most 1 MiB each, and no path may lead outside it.

## Values per POD and equipment models

A module use names an equipment model, and an instance may name another, or give values of its own:

```yaml
modules:
  - id: cooling.pod
    for_each: pods
    instance: "{pods}"
    equipment: pod_enterprise
    overrides:
      "01": {equipment: pod_ai}
      "05": {params: {fan_kw: {value: 3.5, unit: kW, source: "site survey 2026-10"}}}
equipment:
  pod_ai:
    module: cooling.pod
    maker: Example
    model: AI air handling
    evidence: datasheet rev C
    params:
      design_it_kw: {value: 300.0, unit: kW, source: "datasheet rev C, table 2"}
```

- **Precedence.** The module's default comes first, then the equipment model, then the use's values, then the instance's override. The last one wins.
- **Provenance.** Every value carries `source` or `assumed: true`. The card counts how many of each your facility rests on.
- **Misspellings.** A parameter the module does not read is refused when the plant is built, so a misspelt name never leaves the default in place silently.

## What fits

`facility check` writes `facility_report.md` and `facility_report.json`:

- the IT load served against design, the power at the incomers and the hottest supply air, after five minutes with no agent;
- the contracts that load, and those that do not, with the first point or kind of equipment each lacks;
- the scenarios that fit, and those that do not, with what each needs. `OP-06` needs a POD 07, for example;
- what cannot be varied yet: three pumps in the pump station, two utility incomers per MV switchgear, six zone sensors per POD, and the oracle's limits of 27 °C and 32 °C. The number of chillers can be varied: `chillers`, 1 to 9, on `cooling.chw_plant`.

## Results on your facility

`bench run --facility` and `scenario run --facility` run only what fits. The card has a `facility` block:

- the name, the site and the profile hash;
- the SHA-256 of every file in the bundle;
- the equipment models used;
- the values counted by provenance;
- the contracts not loaded and the scenarios not run.

The card is labelled "result on a facility of its owner, not a benchmark result", and `bench check --publication` refuses it. It shows what each gate did on your description of your facility. It says nothing about the real site.

## Licence

The example bundle in `examples/byodc/` is under the MIT licence, like the other examples. A bundle of your own is yours.
