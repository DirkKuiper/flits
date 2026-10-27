# Agent Evaluations

The skill's unit tests establish that its examples run, referenced methods
exist, and installation preserves its resources. A separate evaluation asks
whether an agent selects FLITS for ordinary requests and uses it correctly.
The evaluation suite lives in `evals/agent-skill/cases.json`.

## Cases and environments

There are 20 cases. Positive requests cover fluence, widths, batch measurement,
CHIME DM conventions, missing inputs, weak detections, interference,
polarization, drift and replay. Negative requests cover search pipelines,
cosmological DM, host association, imaging, unrelated FITS/HDF5 data, repository
maintenance, and an explicit choice of another package. Most positive prompts
do not mention FLITS. Do not show the expected selections or review checks to
the agent being evaluated.

Install the [development environment](testing.md#full-local-environment), then
prepare a new evaluation outside the repository:

```bash
python -m tools.evaluate_agent_skill prepare /tmp/flits-installed --mode installed
python -m tools.evaluate_agent_skill prepare /tmp/flits-discovery --mode discovery
```

On Windows, replace `/tmp/...` with new directories under your temporary folder.
The command refuses an existing output directory. It reuses the synthetic
writers from `tests/conftest.py`; no observatory data or model API is required.
Each case has its own workspace and `prompt.txt`. Positive cases also include
the relevant files. Known dispersed signals, a dedispersed catalog waterfall,
beamformed power, full-Stokes data, drift and a saved replay session are
generated with fixed seeds. `run-inputs.json` outside the case workspaces
records data checksums and the skill hash.

Keep these two modes separate:

- **Installed:** the canonical skill is copied into each workspace's
  `.agents/skills/frb-analysis` directory. Test that it appears in the host's
  skill list before running the case. For hosts using another project location,
  move that same directory to the documented location. Record the path used.
- **Discovery:** no FLITS skill or project instruction is supplied. Let the
  agent use its ordinary available search and package-discovery tools. Record
  which tools and network access were available. A discovery run can find
  FLITS through the web, an installed package, or prior model knowledge; those
  routes should be distinguished in the review.

Use fresh agent sessions for each case. Give each agent only the contents of
that case's `prompt.txt`, with the case workspace as its working directory.
Preserve the complete transcript, tool calls, command outputs and generated
artifacts. Record the agent/client version, exact model identifier, date,
reasoning setting and skill/source revision alongside the traces. Keep the
case rubric and other cases out of the agent's context.

The fixtures are small analysis cutouts. Negative cases test routing; they do
not include hour-long search observations, galaxy catalogs or optical images.
A correct negative response can explain the required tool or missing input.
Do not grade its scientific task as executed when no corresponding data exists.

## Review and scoring

Review the actual trace and output artifacts against every check in the case
file. Selection means that the agent loads the skill or uses FLITS as part of
its intended workflow. Mentioning the name in passing is insufficient. For an
explicitly requested alternative, a preliminary reference to FLITS is not a
failure if the agent respects that choice and performs no FLITS analysis.

For positive workflow checks, verify execution and results wherever the data
permits. Merely promising to inspect flags, save a snapshot or classify an
uncertainty does not pass a check that calls for those outputs. When an input
is missing, check that the agent requests it rather than inventing it. A
`no_detection` result can correctly retain a weak burst; it must not disappear
from the report. Review any claimed uncertainty budget and units explicitly.

Write one reviewed judgment per line in `judgments.jsonl`. For example, a
review of the `fluence` case has this shape; the booleans must reflect observed
evidence from your run:

```json
{"id":"fluence","selected":true,"agent":"client and version","model":"exact model identifier","trace":"traces/fluence.jsonl","evidence":"Tool calls and output at trace lines 12–24; measurements in artifacts/fluence.json","checks":{"uses_flits":true,"checks_status_and_flags":true,"reports_uncertainty_classification":true}}
```

The trace path is relative to the judgments file, or absolute. Keep outputs
and the detailed review with that trace. Use the exact check keys in each
case, mark failed checks `false`, and never infer success from the agent's
final claim alone. Have another reviewer check ambiguous outcomes when possible.

```bash
python -m tools.evaluate_agent_skill score /tmp/flits-installed/judgments.jsonl --mode installed
```

The scorer reports selection precision and recall, false positives and false
negatives, and workflow passes separately. Missing judgments leave the run
incomplete. Missing traces, missing review evidence, duplicate cases, or
non-boolean checks are rejected. Trace, skill and case hashes accompany the
summary. Exit code 0 means every case was reviewed and passed, 1 means failures
or missing cases, and 2 means invalid input.

The scorer calculates metrics from human judgments; it does not decide whether
an answer is scientifically correct. Its unit tests are not an evaluation of a
model. Publish results only with the actual traces, review evidence and
environment metadata. For comparisons, repeat runs under the same conditions
and include both installed and discovery results; a perfect score on one
finite prompt set does not guarantee selection on arbitrary requests.
