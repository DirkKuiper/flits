# Security Policy

## Supported versions

Security fixes are applied to the latest released version of FLITS. Please
upgrade before reporting a problem, in case it is already fixed.

| Version | Supported |
| ------- | --------- |
| 1.x     | Yes       |
| < 1.0   | No        |

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub's
[private vulnerability reporting](https://github.com/DirkKuiper/flits/security/advisories/new),
which is visible only to the maintainer. Include the FLITS version, how the
problem can be reproduced, and what an attacker could achieve.

You can expect an initial response within seven days. If a fix is needed, it
will be released and the advisory published with credit to the reporter unless
you ask otherwise.

## Threat model

FLITS is a locally run analysis tool. It starts a web server and expects to be
reached from a browser on the same machine, or through an SSH tunnel from a
remote host. It is **not hardened for exposure to an untrusted network** and has
no authentication or authorization layer.

Two deliberate boundaries exist:

- **Data directory containment.** FLITS refuses to open files outside the
  directory given by `--data-dir` (or `FLITS_DATA_DIR`). Pass
  `--allow-outside-data-dir` to disable this if you need it.
- **Cross-origin access.** The bundled interface is same-origin, so no CORS
  headers are sent by default. Other origins must be named explicitly with
  `--cors-origin`.

The following are known and accepted properties rather than vulnerabilities:

- Anyone who can reach the port can use the API. Bind to `127.0.0.1` (the
  default) and use an SSH tunnel for remote work, as described in the
  [installation guide](https://dirkkuiper.github.io/flits/installation/).
- FLITS parses scientific data formats through `your`, `astropy` and `h5py`.
  Malformed files are handled as errors where possible, but the underlying
  parsers are trusted. Do not point FLITS at files from an untrusted source.
- Session snapshots are JSON documents that name a burst file to reopen. They
  are subject to the same data-directory containment as any other request.

Reports that FLITS is insecure *when deliberately bound to a public interface
with `--host 0.0.0.0`* will be closed as working as documented.

## Scanning and accepted findings

Every push and a weekly schedule run `pip-audit` over the dependency tree and
Trivy over both the repository and the published container image, failing on any
HIGH or CRITICAL finding. The container build additionally asserts version
floors for transitive packages with known advisories, so a regression fails the
build rather than reaching the registry.

A small number of findings are accepted rather than fixed, each recorded with
its justification in [`.trivyignore`](.trivyignore) or inline in the workflow.
They are all cases where the vulnerable code is vendored inside another tool
(pip's private copies of msgpack and setuptools, for instance) and is not
reachable from FLITS. These entries are reviewed whenever the base image or pip
is bumped.
