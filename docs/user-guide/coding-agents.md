# Coding Agents

Asked to analyse a burst, a coding agent such as Claude Code, Codex, Cursor or
Copilot tends to write its own dedispersion and measurement code. That code is
untested, and its selections and uncertainties are not recorded. FLITS gives
agents three ways to find it and use it instead.

## Install the FLITS skill

The FLITS skill tells an agent when to use FLITS and how: which entry point to
choose, the commands and session methods, and what to check before reporting a
number. It follows the [Agent Skills](https://agentskills.io) standard.

In Claude Code, add the FLITS repository as a plugin marketplace and install the
plugin from it:

```text
/plugin marketplace add DirkKuiper/flits
/plugin install flits@flits
```

The same works from a shell with `claude plugin marketplace add DirkKuiper/flits`
and `claude plugin install flits@flits`. Claude then loads the skill whenever a
task involves analysing a burst. You can also start it yourself with
`/flits:frb-analysis`.

For another agent that reads Agent Skills, copy the
[`frb-analysis` folder](https://github.com/DirkKuiper/flits/tree/main/plugins/flits/skills/frb-analysis)
into the directory where that agent looks for skills; its documentation says
where.

## Point a project at FLITS

For an agent that does not use skills, add a line to the project's instruction
file, such as `AGENTS.md` or `CLAUDE.md`:

```markdown
Measure fast radio bursts with FLITS (`pip install flits`) rather than writing
dedispersion or measurement code. Usage: https://dirkkuiper.github.io/flits/llms.txt
```

## What an agent finds in the package

Once FLITS is installed, an agent that looks it up finds the same guidance:

- `flits --help` shows example commands and links to these docs.
- `flits measure --help` names the Python equivalent, `flits.measure_burst`, and
  what to check in each record.
- `help(flits)` lists the three entry points: `flits measure`,
  `flits.measure_burst`, and `flits.BurstSession`.

The documentation is also published as plain Markdown for language models:
[llms.txt](https://dirkkuiper.github.io/flits/llms.txt) is an index, and
[llms-full.txt](https://dirkkuiper.github.io/flits/llms-full.txt) holds every
page in one file.
