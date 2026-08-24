# JOSS submission materials

`paper.md` and `paper.bib` are the submission artifacts for the Journal of Open
Source Software. Pushing a change under `paper/` builds a draft PDF through the
`JOSS Paper` workflow; download it from the run's artifacts to preview exactly
what reviewers will see.

Run the local structural checks with `python paper/check.py`. Add
`--check-dois` to repeat the DOI resolver check before submission.

## Before submitting

- [x] Replace the placeholder ORCID in `paper.md` and `CITATION.cff`.
- [x] Confirm the affiliation against the author's published affiliation.
- [x] Replace the repository-only `fitburst` citation with its published paper.
- [x] Verify every DOI in `paper.bib` resolves through <https://doi.org/>.
- [x] Include the sections required by the current JOSS paper format: Summary,
      Statement of need, State of the field, Software design, Research impact,
      AI usage disclosure, Acknowledgements, and References.
- [x] Keep the manuscript between 750 and 1750 words, excluding references.
- [x] Build and visually inspect the draft PDF.
- [ ] Confirm the funding and sponsor-involvement statement in `paper.md`.
- [ ] Wait until **10 September 2026** before submitting. The repository was
      created on 9 March 2026, and the current JOSS pre-review screen requires
      more than six months of public development history.
- [ ] Submit at <https://joss.theoj.org/papers/new>. At submission time, state
      that FLITS is already used in the ongoing multi-telescope campaign and
      disclose any related papers or papers in preparation.

## After review, before acceptance

JOSS asks for the archival DOI after review, not at initial submission. When
the editor says the review is complete:

- [ ] Make a fresh tagged release containing all review changes.
- [ ] Archive that exact release with Zenodo or figshare and verify that its
      title, author, ORCID, affiliation, version, and licence match the paper and
      `CITATION.cff`.
- [ ] Deposit the tutorial data in an archival record and replace the mutable
      GitHub release-asset URL in `docs/guided-workflow.md` with its DOI-backed
      URL. Relate the data record and software record in both directions if the
      archive service cannot place them in one record.
- [ ] Add the version-specific software DOI to `CITATION.cff`, post the release
      version and archive DOI in the JOSS review issue, and rebuild the proof.
