#!/usr/bin/env python3
"""RadioLib 7.8.1 for the VGQ-1 / DSS-Q1 Arduino sketches: fetch, patch, verify.

Contract v3 sections 25.2 and 30: the SX126x radio option of both sketches is
built on RadioLib 7.8.1 obtained from its upstream repository, verified, patched
with tools/radiolib-7.8.1.patch and bundled next to the sketch as
sketch/libraries/RadioLib ("- dir: libraries/RadioLib" in sketch.yaml). The
repository keeps only the patch and this tool; the boards never download RadioLib.

Commands
  fetch DEST [--repo URL] [--unpatched]
      git clone --depth 1 --branch 7.8.1 of the upstream repository (or of the
      mirror / local clone given by --repo), verify the tag commit and the digest
      of the bundled files, apply the patch with an exact-match hunk applier (no
      fuzz, no offset: any mismatch aborts), and write the copy to DEST together
      with PROVENANCE.txt. DEST must be absent, empty, or a copy written by this
      tool (it is then replaced). --unpatched writes the stock tag instead: a
      reference copy for comparison builds only, never for a sketch.
  check DIR [--unpatched]
      Offline verification that DIR is exactly the pinned tag plus the current
      patch (or the stock tag with --unpatched): the file set, every byte, and
      PROVENANCE.txt. Reverse-applying the patch must give the pinned pristine
      digest and re-applying it must give DIR back.

Bundled files: library.properties, license.txt (MIT notice, kept unchanged) and
the complete src/ tree. The pristine digest is the SHA-256 of the sha256sum-style
manifest "<sha256 of file>  <path>\\n" sorted by path, i.e. the same value as
  (cd DIR && find library.properties license.txt src -type f | LC_ALL=C sort |
   xargs sha256sum | sha256sum)
over a stock copy.

Exit status: 0 success; 1 verification or patch failure (do not use the copy);
2 usage error; 3 git is missing or the repository cannot be reached.
Python 3.11+, standard library only; needs the git command line for fetch.
The fetch / patch / verify engine is shared with tools/routerbridge.py
(tools/libbundle.py); this file pins what is specific to RadioLib.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tools/libbundle.py, also under python -I
import libbundle

SPEC = libbundle.LibrarySpec(
    tool="radiolib.py",
    name="RadioLib",
    upstream_url="https://github.com/jgromes/RadioLib.git",
    tag="7.8.1",
    commit="2325a38a89dd0f0e402c19f67fe910111da1e246",
    patch_file=Path(__file__).resolve().with_name("radiolib-7.8.1.patch"),
    bundle_files=("library.properties", "license.txt"),
    bundle_dirs=("src",),
    # Digest of the bundled files of the stock tag (see the module docstring).
    pristine_tree_sha256="84370304e2fa416a2d4aa798b49a576d6821252691191afdc3ed413a49fa9189",
    contract="contract v3 section 25.2",
    license_line="MIT (license.txt, unchanged)",
    max_files=2000,                   # walk bound; the 7.8.1 bundle has 176 files
    max_file_bytes=8 * 1024 * 1024,   # largest 7.8.1 file is 0.77 MB
    description="Fetch, patch and verify RadioLib 7.8.1 for the VGQ sketches.",
)


def main(argv: list[str] | None = None) -> int:
    return libbundle.main(SPEC, argv)


if __name__ == "__main__":
    sys.exit(main())
