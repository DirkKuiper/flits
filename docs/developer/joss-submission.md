# JOSS manuscript preparation

The manuscript is `paper/paper.md`, with references in `paper/paper.bib` and
software citation metadata in `CITATION.cff`. The JOSS Paper workflow validates
these files and builds an official JOSS draft PDF on relevant pull requests.

## Validate the manuscript

From the repository root, in a Python environment:

```bash
python -m pip install -r paper/requirements.txt
python paper/check.py
cffconvert --validate
```

The checker verifies section coverage, word count, citation keys, ORCID
checksums, matching title and author identifiers, and unfinished author markers.
For the final reference audit, also run:

```bash
python paper/check.py --check-dois
```

This requires `curl` and network access. It checks DOI resolution and compares
bibliographic titles, volumes, issues, and pages with Crossref. A resolving DOI
can still point to the wrong article. Author lists, attribution, and references
without DOIs also need human review.

## Build the official proof

Download the `paper` artifact from the JOSS Paper workflow, or build locally
with [Inara](https://github.com/openjournals/inara) and Docker:

```bash
docker run --rm --volume "$PWD/paper:/data" \
  --user "$(id -u):$(id -g)" --env JOURNAL=joss openjournals/inara
```

Inspect `paper/paper.pdf`, including every reference page. The draft watermark
and placeholder DOI, publication details, and submission date are supplied by
the journal template and are replaced during the publication process.

## Check the supporting materials

Run the [software checks](testing.md), the
[GBT reference workflow](../guided-workflow.md), and the
[RM-synthesis reference example](../analysis/rm-synthesis.md). The Reference
Workflows action checks the GBT measurements and portable replay on Linux,
using both the published package and the current checkout with pinned
dependencies. The example data remain a separate downloadable release asset;
the reference snapshot and expected results are in `docs/examples/`.

Before submission, the authors should review the final proof and agree on
authorship, affiliations, contribution credit, funding acknowledgements,
conflicts of interest, and the AI disclosure. Confirm that its statements about
human review and scientific decisions accurately describe the work. The
disclosure names the known assistants and explicitly records the absence of a
systematic historical model/version record; it does not invent missing details.

The submission form should identify any related science or methods papers,
including manuscripts in preparation or under review. Retain factual evidence
of the research uses described in the paper, such as a project report or
analysis record, in case the editors request it. Confirm credit for any
additional contributors before submission.

## Submission and the final archive

Follow the current [JOSS submission requirements](https://joss.readthedocs.io/en/latest/submitting.html)
and [paper guidance](https://joss.readthedocs.io/en/latest/paper.html). The
manuscript and software must be available together in the public repository;
a submission branch is allowed. Use the branch containing the final reviewed
manuscript if it has not yet been merged.

After review, archive the exact final tagged software release with Zenodo or
figshare and provide its version and archival DOI. A final archive DOI is not
required for the initial submission. Preserve the example data, provenance,
and sharing terms in a suitable archive as well; a GitHub release asset alone
does not establish that a separate archival deposit exists.
