"""Regenerate fixtures with real released serializers (deliberate maintenance only).

Run with the full FLITS test environment. Each release is extracted into an
isolated temporary directory and run against one deterministic synthetic file.
The generation environment is recorded: these are historical *code* outputs,
not a claim to reconstruct every release's original dependency environment.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
DEST = Path(__file__).resolve().parent
RELEASES = ("flits-v0.2.0", "flits-v0.2.1", "flits-v1.0.0", "flits-v1.1.0", "flits-v1.2.1")

RUN = r"""
import json, pathlib, sys
import flits
from flits.session import BurstSession
from flits.exports import create_export_snapshot
root, source, destination = map(pathlib.Path, sys.argv[1:])
assert pathlib.Path(flits.__file__).resolve().is_relative_to(root.resolve()), flits.__file__
destination.mkdir(parents=True, exist_ok=True)
session = BurstSession.from_file(str(source), dm=0.0, telescope="gbt", sefd_jy=10.0)
session.set_event_ms(110, 145)
session.add_offpulse_ms(0, 90)
session.set_time_factor(2)
session.set_freq_factor(2)
session.mask_channel_freq(float(session.freqs[0]))
session.set_notes("Synthetic historical compatibility fixture; not observatory data.")
session.compute_properties()
session.compute_widths()
session.optimize_dm(0.0, 2.0, 1.0)
session.run_temporal_structure_analysis(8.0)
snapshot = session.snapshot_dict()
# Only filesystem location/mtime are made portable. Numeric results and all
# layouts come from the unmodified release serializer.
snapshot["source"]["source_path"] = source.name
snapshot["source"]["mtime_unix"] = 0.0
if "data_dir_relative_path" in snapshot["source"]:
    snapshot["source"]["data_dir_relative_path"] = source.name
(destination / "session.json").write_text(json.dumps(snapshot, indent=2, allow_nan=False) + "\n")
bundle = create_export_snapshot(session, session_id="compatibility", include=["json", "csv", "npz", "plots", "window"], window_formats=["npz", "fil"])
manifest = bundle.manifest.to_dict()
for name, content in bundle.contents.items():
    # Artifact bytes remain verbatim, including the historical metadata.
    short = name.removeprefix(manifest["bundle_name"] + "_")
    (destination / short).write_bytes(content)
(destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"flits_version": flits.__version__, "session_schema": snapshot["schema_version"], "export_schema": manifest["schema_version"]}))
"""


def main() -> None:
    sys.path.insert(0, str(ROOT))
    from flits.io.sigproc import SigprocFilterbankHeader, build_sigproc_filterbank_bytes
    from flits.provenance import capture_environment

    rng = np.random.default_rng(314)
    data = rng.normal(0.0, 1.0, (16, 256))
    data += 20.0 * np.exp(-0.5 * ((np.arange(256) - 128.0) / 4.0) ** 2)[None, :]
    source = DEST / "synthetic.fil"
    source.write_bytes(
        build_sigproc_filterbank_bytes(
            data.astype(np.float32),
            SigprocFilterbankHeader(
                rawdatafile=source.name,
                source_name="COMPATIBILITY",
                nchans=16,
                foff=-1.0,
                fch1=1500.0,
                tsamp=0.001,
                tstart=60000.0,
                telescope_id=6,
                machine_id=0,
                src_raj=123456.78,
                src_dej=-123456.78,
                nbits=32,
                nifs=1,
            ),
        )
    )
    report = {"generation_environment": capture_environment(), "releases": []}
    for ref in RELEASES:
        commit = subprocess.check_output(["git", "rev-parse", f"{ref}^{{commit}}"], cwd=ROOT, text=True).strip()
        archive = subprocess.check_output(["git", "archive", ref, "flits"], cwd=ROOT)
        with tempfile.TemporaryDirectory(prefix="flits-historical-") as directory:
            root = Path(directory)
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(root, filter="data")
            shutil.copyfile(source, root / source.name)
            destination = DEST / ref
            result = subprocess.run(
                [sys.executable, "-c", RUN, str(root), source.name, str(destination)],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(f"{ref}: {result.stderr[-6000:]}")
            metadata = json.loads(result.stdout.strip().splitlines()[-1])
        record = {"tag": ref, "commit": commit, **metadata}
        report["releases"].append(record)
        print(record, flush=True)
    report["sha256"] = {
        path.relative_to(DEST).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(DEST.rglob("*"))
        if path.is_file() and path.suffix not in {".py", ".pyc", ".md"} and path.name != "generation.json"
    }
    (DEST / "generation.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
