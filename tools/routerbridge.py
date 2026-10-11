#!/usr/bin/env python3
"""Arduino_RouterBridge 0.4.3 for the VGQ-1 / DSS-Q1 Arduino sketches: fetch, patch, verify.

Contract v3 section 41 (same policy as RadioLib, sections 25.2 and 30): both sketches
build against the Arduino_RouterBridge release that the arduino:zephyr platform
ships (0.4.3, its libraryDependencies), obtained from the upstream repository,
verified, patched with tools/arduino_routerbridge-0.4.3.patch and bundled next to
the sketch as sketch/libraries/Arduino_RouterBridge ("- dir:
libraries/Arduino_RouterBridge" in sketch.yaml), where arduino-cli prefers it to the
platform's copy. The patch gives the library's "bridge" thread a measured stack and
keeps provide_safe() methods off that thread (they run only from update_safe() in
__loopHook, on the loop thread). Its dependency Arduino_RPClite 0.3.1 and the other
platform libraries (MsgPack, DebugLog, ArxContainer, ArxTypeTraits) are unchanged
and still come from the platform. The repository keeps only the patch and this
tool; the boards never download the library.

Commands
  fetch DEST [--repo URL] [--unpatched]
      git clone --depth 1 --branch 0.4.3 of the upstream repository (or of the
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

Bundled files: library.properties, LICENSE (MPL-2.0 text, kept unchanged) and the
complete src/ tree (examples, extras and the README are not bundled). The pristine
digest is the SHA-256 of the sha256sum-style manifest "<sha256 of file>  <path>\\n"
sorted by path, i.e. the same value as
  (cd DIR && find library.properties LICENSE src -type f | LC_ALL=C sort |
   xargs sha256sum | sha256sum)
over a stock copy.

Exit status: 0 success; 1 verification or patch failure (do not use the copy);
2 usage error; 3 git is missing or the repository cannot be reached.
Python 3.11+, standard library only; needs the git command line for fetch.
The fetch / patch / verify engine is shared with tools/radiolib.py (tools/libbundle.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tools/libbundle.py, also under python -I
import libbundle

SPEC = libbundle.LibrarySpec(
    tool="routerbridge.py",
    name="Arduino_RouterBridge",
    upstream_url="https://github.com/arduino-libraries/Arduino_RouterBridge.git",
    tag="0.4.3",
    commit="d681b87211864148367f2d77096ae84c5badcb2f",
    patch_file=Path(__file__).resolve().with_name("arduino_routerbridge-0.4.3.patch"),
    bundle_files=("library.properties", "LICENSE"),
    bundle_dirs=("src",),
    # Digest of the bundled files of the stock tag (see the module docstring).
    pristine_tree_sha256="c86d601236aef85b351c39c472273cd8bf96c3d38a8a18c708788945b5813194",
    contract="contract v3 section 41",
    license_line="MPL-2.0 (LICENSE, unchanged; the modified file is src/bridge.h, its source is this copy)",
    max_files=200,                    # walk bound; the 0.4.3 bundle has 11 files
    max_file_bytes=1024 * 1024,       # largest 0.4.3 file is 17 kB (LICENSE)
    description="Fetch, patch and verify Arduino_RouterBridge 0.4.3 for the VGQ sketches.",
)


def main(argv: list[str] | None = None) -> int:
    return libbundle.main(SPEC, argv)


if __name__ == "__main__":
    sys.exit(main())
