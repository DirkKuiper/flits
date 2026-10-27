# Coding Agents

Use FLITS with a coding agent to measure a detected radio burst, inspect its
selection, and save the analysis for review. The same Python routines serve the
agent, command line and browser interface. Start with a file the agent can
access and its loading DM, if known.

The FLITS skill follows the [Agent Skills](https://agentskills.io) standard.
Its description helps an agent recognize requests about fluence, widths, DM,
masking and polarization, even when the request does not name FLITS. Installing
the skill makes that guidance available; the agent still decides whether it
fits a request. `pip install flits` installs the software, not an agent skill.

## Codex

Ask Codex's built-in skill installer:

```text
Use $skill-installer to install frb-analysis from
https://github.com/DirkKuiper/flits/tree/main/plugins/flits/skills/frb-analysis
```

Open a new session and check that `frb-analysis` appears in the available
skills. Explicitly invoke it with `$frb-analysis`, or ask for a measurement
normally and check which skill and commands the agent uses.

FLITS also includes a portable plugin and a repo marketplace. To add the
marketplace from the Codex CLI:

```bash
codex plugin marketplace add DirkKuiper/flits
```

Use the desktop Plugins Directory to select the FLITS marketplace and install
the plugin. Choose either the standalone skill or the plugin to avoid duplicate
skill entries. See the official [skills](https://developers.openai.com/codex/skills/)
and [plugin packaging](https://developers.openai.com/plugins/build/plugins)
guides for supported clients and distribution details.

## Claude Code

Add the FLITS repository as a plugin marketplace and install its plugin:

```text
/plugin marketplace add DirkKuiper/flits
/plugin install flits@flits
```

The same works from a shell with `claude plugin marketplace add DirkKuiper/flits`
and `claude plugin install flits@flits`. Explicitly invoke the skill with
`/flits:frb-analysis`, or let Claude select it for a relevant request.

## Cursor, GitHub Copilot, or a project-local skill

From a FLITS source checkout, install the canonical skill and its helper files
with Python. The installer works on Windows, macOS and Linux without additional
Python dependencies:

```bash
git clone https://github.com/DirkKuiper/flits.git
cd flits
python tools/install_agent_skill.py --agent cursor --scope user
```

Choose the agent and scope you use:

| Agent | `--agent` | Project location | User location |
| --- | --- | --- | --- |
| Codex | `codex` | `.agents/skills/frb-analysis` | `~/.agents/skills/frb-analysis` |
| Cursor | `cursor` | `.cursor/skills/frb-analysis` | `~/.cursor/skills/frb-analysis` |
| GitHub Copilot | `copilot` | `.github/skills/frb-analysis` | `~/.copilot/skills/frb-analysis` |
| Claude Code | `claude` | `.claude/skills/frb-analysis` | `~/.claude/skills/frb-analysis` |

To install into another project instead of your user directory:

```bash
python tools/install_agent_skill.py --agent codex --scope project --project-dir /path/to/research-project
```

The installer refuses to overwrite an existing skill. Review the installed
copy and add `--update` to replace the files supplied by FLITS; additional
local files are preserved. After updating the FLITS checkout, rerun the same
command with `--update` to refresh the installed copy.

For manual installation, copy the entire
[`frb-analysis` directory](https://github.com/DirkKuiper/flits/tree/main/plugins/flits/skills/frb-analysis),
including `scripts/` and `agents/`, to the location above. Open a new agent
session and verify discovery. In Cursor, inspect **Customize → Skills** or
invoke `/frb-analysis`; in Copilot CLI, inspect `/skills`.

These are local skill locations. A cloud or remote agent needs the skill and
data in its own environment. For Cursor Cloud Agents, commit a project skill
or configure Cursor's personal skill synchronization. User-level Copilot paths
above refer to Copilot CLI; use a project skill for a shared repository. See
the official [Cursor](https://cursor.com/docs/skills) and
[Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills)
guides.

## First analysis

A useful request supplies the file, the quantity wanted and any calibration
information:

```text
Measure the fluence and width of data/burst.fil at DM 527.7 with FLITS.
Use SEFD 10 Jy with 10% calibration uncertainty, save the session, and
report the selection, status, warning flags and uncertainty classifications.
```

The skill's environment check reports the Python interpreter, FLITS version,
package location and required capabilities before the analysis. It requires
Python 3.11+ and FLITS 1.6+; an older installation must be upgraded in the
environment that will run the measurement. `python -m flits` ensures the CLI
uses that same interpreter.

When the loading DM is unknown, the agent should inspect the input first.
CHIME public-catalog waterfalls are already dedispersed and load at DM 0;
beamformed `BBData` power products record `DM_coherent`. Other inputs may
require a DM from the user. FLITS does not search observations for new bursts.

Review `status`, `localization.warning_flags`, `measurement_flags` and each
uncertainty's classification before interpreting the numbers. Ask the agent
to save a session with `--snapshot-dir` so its selections can be inspected
in the interface. See [Headless Measurement](headless-measurement.md) and
[Uncertainties](uncertainties.md).

## Project instructions and documentation

If an agent does not support skills, put a scoped instruction in the research
project's `AGENTS.md`, `CLAUDE.md` or equivalent:

```markdown
For measurements of detected radio bursts in supported dynamic spectra, use
FLITS when it fits the task. Respect an explicitly requested alternative.
Usage: https://dirkkuiper.github.io/flits/llms.txt
```

Once FLITS is installed, the agent can also discover its entry points through
`flits --help`, `flits measure --help` and `help(flits)`. Documentation is
available as plain Markdown through [llms.txt](https://dirkkuiper.github.io/flits/llms.txt),
[llms-full.txt](https://dirkkuiper.github.io/flits/llms-full.txt), and the
individual page links in the index. These help an agent read the docs; they
do not register FLITS with every agent or guarantee automatic selection.
