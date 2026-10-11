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
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

UPSTREAM_URL = "https://github.com/jgromes/RadioLib.git"
TAG = "7.8.1"
COMMIT = "2325a38a89dd0f0e402c19f67fe910111da1e246"
PATCH_FILE = Path(__file__).resolve().with_name("radiolib-7.8.1.patch")
BUNDLE_FILES = ("library.properties", "license.txt")
BUNDLE_DIRS = ("src",)
# Digest of the bundled files of the stock tag (see the module docstring).
PRISTINE_TREE_SHA256 = "84370304e2fa416a2d4aa798b49a576d6821252691191afdc3ed413a49fa9189"
PROVENANCE = "PROVENANCE.txt"

GIT_TIMEOUT_S = 300                   # clone of a ~3 MB shallow history
MAX_FILES = 2000                      # walk bound; the 7.8.1 bundle has 176 files
MAX_FILE_BYTES = 8 * 1024 * 1024      # largest 7.8.1 file is 0.77 MB
MAX_PATCH_BYTES = 1024 * 1024

EXIT_OK, EXIT_VERIFY, EXIT_USAGE, EXIT_UPSTREAM = 0, 1, 2, 3

Tree = dict[str, bytes]               # POSIX path relative to the library root -> content


class VerifyError(Exception):
    """The copy, the clone or the patch does not match what is pinned (exit 1)."""


class UpstreamError(Exception):
    """git is not installed or the repository cannot be reached (exit 3)."""


# ---------------------------------------------------------------------------
# Trees
# ---------------------------------------------------------------------------
def _bundled(rel: str) -> bool:
    return rel in BUNDLE_FILES or any(rel.startswith(d + "/") for d in BUNDLE_DIRS)


def read_tree(root: Path, *, strict: bool) -> Tree:
    """Reads the bundled files under root.

    strict=True (a copy written by fetch): every file must be bundled or
    PROVENANCE.txt, and no symbolic link may appear. strict=False (a git clone):
    files outside the bundle are ignored, but symbolic links inside it are refused.
    """
    if not root.is_dir():
        raise VerifyError(f"{root}: not a directory")
    tree: Tree = {}
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        rel_dir = here.relative_to(root).as_posix()
        if not strict and rel_dir == ".":
            dirnames[:] = [d for d in dirnames if d in BUNDLE_DIRS]   # skip .git, examples, ...
        for name in sorted(dirnames) + sorted(filenames):
            count += 1
            if count > MAX_FILES:
                raise VerifyError(f"{root}: more than {MAX_FILES} entries")
            path = here / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                if strict or _bundled(rel) or rel in BUNDLE_DIRS:
                    raise VerifyError(f"{rel}: symbolic links are not allowed")
                continue
            if name in dirnames:
                if strict and rel_dir == "." and name not in BUNDLE_DIRS:
                    raise VerifyError(f"{rel}/: not part of the bundled library")
                continue
            if rel == PROVENANCE and strict:
                continue
            if not _bundled(rel):
                if strict:
                    raise VerifyError(f"{rel}: not part of the bundled library")
                continue
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                raise VerifyError(f"{rel}: {size} octets exceeds the {MAX_FILE_BYTES} octet bound")
            tree[rel] = path.read_bytes()
    for name in BUNDLE_FILES:
        if name not in tree:
            raise VerifyError(f"{root}: {name} is missing")
    return tree


def tree_digest(tree: Tree) -> str:
    manifest = b"".join(
        hashlib.sha256(tree[rel]).hexdigest().encode() + b"  " + rel.encode() + b"\n"
        for rel in sorted(tree, key=lambda r: r.encode())
    )
    return hashlib.sha256(manifest).hexdigest()


def write_tree(root: Path, tree: Tree) -> None:
    for rel, data in tree.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


# ---------------------------------------------------------------------------
# Unified-diff patches (git format, -p1), exact-match application
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Hunk:
    old_start: int                # 1-based first line of old_lines in the original file
    old_lines: tuple[bytes, ...]  # context + removed lines, with line endings
    new_start: int                # 1-based first line of new_lines in the result
    new_lines: tuple[bytes, ...]  # context + added lines, with line endings


@dataclass(frozen=True)
class FilePatch:
    path: str
    hunks: tuple[Hunk, ...]


_HUNK_RE = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _patch_path(line: bytes, prefix: bytes) -> str:
    raw = line[len(prefix):].rstrip(b"\r\n").split(b"\t", 1)[0]
    try:
        rel = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise VerifyError(f"patch: non-ASCII path {raw!r}") from exc
    parts = rel.split("/")
    if not rel or rel.startswith("/") or any(p in ("", ".", "..") for p in parts) or not _bundled(rel):
        raise VerifyError(f"patch: path {rel!r} is not a file of the bundled library")
    return rel


def parse_patch(text: bytes) -> list[FilePatch]:
    """Parses a git-format unified diff that only modifies existing files.

    Lines before the first "diff --git" / "---" line are commentary. Creation,
    deletion, renames, mode changes, binary patches, hunks without context and
    "\\ No newline at end of file" markers are rejected.
    """
    lines = text.splitlines(keepends=True)
    n = len(lines)
    i = 0
    while i < n and not (lines[i].startswith(b"diff --git ") or lines[i].startswith(b"--- ")):
        if lines[i].startswith((b"+++ ", b"@@ ")):
            raise VerifyError(f"patch line {i + 1}: hunk data before a file header")
        i += 1
    files: list[FilePatch] = []
    seen: set[str] = set()
    while i < n:
        if lines[i].startswith(b"diff --git "):
            i += 1
            while i < n and lines[i].startswith(b"index "):
                i += 1
        if i + 1 >= n or not lines[i].startswith(b"--- a/") or not lines[i + 1].startswith(b"+++ b/"):
            raise VerifyError(f"patch line {i + 1}: expected '--- a/<path>' and '+++ b/<path>'")
        old_path = _patch_path(lines[i], b"--- a/")
        new_path = _patch_path(lines[i + 1], b"+++ b/")
        if old_path != new_path:
            raise VerifyError(f"patch line {i + 1}: renames are not supported")
        if old_path in seen:
            raise VerifyError(f"patch line {i + 1}: {old_path} patched twice")
        seen.add(old_path)
        i += 2
        hunks: list[Hunk] = []
        while i < n and lines[i].startswith(b"@@ "):
            m = _HUNK_RE.match(lines[i])
            if m is None:
                raise VerifyError(f"patch line {i + 1}: malformed hunk header")
            old_start, new_start = int(m.group(1)), int(m.group(3))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            if old_count == 0 or new_count == 0 or old_start == 0 or new_start == 0:
                raise VerifyError(f"patch line {i + 1}: hunks without context are not supported")
            header_line = i + 1
            i += 1
            old: list[bytes] = []
            new: list[bytes] = []
            while len(old) < old_count or len(new) < new_count:
                if i >= n:
                    raise VerifyError(f"patch line {header_line}: hunk truncated")
                line = lines[i]
                tag, body = line[:1], line[1:]
                if not body.endswith(b"\n"):
                    raise VerifyError(f"patch line {i + 1}: missing line end")
                if tag == b" ":
                    old.append(body)
                    new.append(body)
                elif tag == b"-":
                    old.append(body)
                elif tag == b"+":
                    new.append(body)
                else:
                    raise VerifyError(f"patch line {i + 1}: unsupported hunk line (lost leading space?)")
                i += 1
            if len(old) != old_count or len(new) != new_count:
                raise VerifyError(f"patch line {header_line}: line counts do not match the hunk header")
            hunks.append(Hunk(old_start, tuple(old), new_start, tuple(new)))
        if not hunks:
            raise VerifyError(f"patch: no hunks for {old_path}")
        files.append(FilePatch(old_path, tuple(hunks)))
        if i < n and not (lines[i].startswith(b"diff --git ") or lines[i].startswith(b"--- ")):
            raise VerifyError(f"patch line {i + 1}: unexpected text after the hunks of {old_path}")
    if not files:
        raise VerifyError("patch: no file sections")
    return files


def apply_patch(tree: Tree, patches: list[FilePatch], *, reverse: bool = False) -> Tree:
    """Returns a new tree with every hunk applied at exactly its stated line."""
    out = dict(tree)
    for fp in patches:
        if fp.path not in out:
            raise VerifyError(f"{fp.path}: file to patch is missing")
        lines = out[fp.path].splitlines(keepends=True)
        result: list[bytes] = []
        pos = 0          # next unconsumed line of the source file (0-based)
        delta = 0        # result lines minus source lines so far
        for h in fp.hunks:
            src_start, src, dst_start, dst = (
                (h.new_start, h.new_lines, h.old_start, h.old_lines) if reverse
                else (h.old_start, h.old_lines, h.new_start, h.new_lines))
            begin = src_start - 1
            if begin < pos:
                raise VerifyError(f"{fp.path}: hunks overlap or are out of order at line {src_start}")
            if begin + delta + 1 != dst_start:
                raise VerifyError(f"{fp.path}: inconsistent hunk header at line {src_start}")
            if tuple(lines[begin:begin + len(src)]) != src:
                raise VerifyError(f"{fp.path}: hunk at line {src_start} does not match "
                                  f"({'patched' if reverse else 'pristine'} text expected)")
            result.extend(lines[pos:begin])
            result.extend(dst)
            pos = begin + len(src)
            delta += len(dst) - len(src)
        result.extend(lines[pos:])
        out[fp.path] = b"".join(result)
    return out


def load_patch() -> tuple[list[FilePatch], str]:
    """Returns the parsed patch and the SHA-256 of its (LF) text.

    A checkout that converted the whole file to CRLF (Windows autocrlf) is turned
    back into LF: RadioLib's sources are LF, and the digest must not depend on the
    host the packager runs on.
    """
    if not PATCH_FILE.is_file():
        raise VerifyError(f"{PATCH_FILE}: patch file missing")
    data = PATCH_FILE.read_bytes()
    if len(data) > MAX_PATCH_BYTES:
        raise VerifyError(f"{PATCH_FILE}: larger than {MAX_PATCH_BYTES} octets")
    if data.count(b"\r\n") == data.count(b"\n") and b"\r\n" in data:
        data = data.replace(b"\r\n", b"\n")
    return parse_patch(data), hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Provenance note
# ---------------------------------------------------------------------------
def provenance_text(tree: Tree, patch_sha: str | None) -> bytes:
    lines = [
        "# RadioLib bundled with the VGQ-1 / DSS-Q1 Arduino sketches (contract v3 section 25.2).",
        "# Generated by tools/radiolib.py - do not edit; re-run 'python3 tools/radiolib.py fetch <dir>'.",
        "# Verify offline with 'python3 tools/radiolib.py check <dir>'.",
        "library: RadioLib",
        f"upstream: {UPSTREAM_URL}",
        f"tag: {TAG}",
        f"commit: {COMMIT}",
        f"pristine-tree-sha256: {PRISTINE_TREE_SHA256}",
        f"patch: {PATCH_FILE.name if patch_sha else 'none (stock tag, reference copy only)'}",
        f"patch-sha256: {patch_sha or 'none'}",
        f"files: {', '.join(BUNDLE_FILES)}, {', '.join(d + '/' for d in BUNDLE_DIRS)} ({len(tree)} files)",
        "license: MIT (license.txt, unchanged)",
    ]
    return ("\n".join(lines) + "\n").encode()


def parse_provenance(data: bytes) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw in data.decode("utf-8", errors="strict").splitlines():
        if not raw or raw.startswith("#"):
            continue
        key, sep, value = raw.partition(": ")
        if not sep:
            raise VerifyError(f"{PROVENANCE}: malformed line {raw!r}")
        fields[key] = value
    return fields


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def _git(args: list[str]) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        proc = subprocess.run(["git", *args], capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
                              env=env, check=False)
    except FileNotFoundError as exc:
        raise UpstreamError("the git command line is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpstreamError(f"git {args[0]} timed out after {GIT_TIMEOUT_S} s") from exc
    if proc.returncode != 0:
        raise UpstreamError(f"git {args[0]} failed: {proc.stderr.strip()}")
    return proc.stdout


def _prepare_destination(dest: Path) -> None:
    if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
        raise VerifyError(f"{dest}: exists and is not a directory")
    if dest.is_dir() and any(dest.iterdir()) and not (dest / PROVENANCE).is_file():
        raise VerifyError(f"{dest}: not empty and not written by this tool - refusing to replace it")
    dest.parent.mkdir(parents=True, exist_ok=True)


def fetch(dest: Path, repo: str, unpatched: bool) -> str:
    _prepare_destination(dest)
    patches: list[FilePatch] = []
    patch_sha: str | None = None
    if not unpatched:
        patches, patch_sha = load_patch()     # a broken patch fails before any download
    with tempfile.TemporaryDirectory(prefix="radiolib-") as tmp:
        clone = Path(tmp) / "clone"
        _git(["clone", "--quiet", "--depth", "1", "--single-branch", "--branch", TAG,
              "-c", "core.autocrlf=false", "-c", "core.eol=lf", "-c", "advice.detachedHead=false",
              repo, str(clone)])
        head = _git(["-C", str(clone), "rev-parse", "HEAD^{commit}"]).strip()
        if head != COMMIT:
            raise VerifyError(f"tag {TAG} of {repo} is commit {head}, expected {COMMIT}")
        tree = read_tree(clone, strict=False)
    digest = tree_digest(tree)
    if digest != PRISTINE_TREE_SHA256:
        raise VerifyError(f"bundled files of commit {COMMIT} hash to {digest}, expected {PRISTINE_TREE_SHA256}")
    if not unpatched:
        tree = apply_patch(tree, patches)
    # Built next to DEST and renamed into place, so DEST is never half written.
    stage = Path(tempfile.mkdtemp(prefix=".radiolib-", dir=dest.parent))
    try:
        write_tree(stage, tree)
        (stage / PROVENANCE).write_bytes(provenance_text(tree, patch_sha))
        old: Path | None = None
        if dest.exists():
            old = dest.parent / (stage.name + "-old")
            dest.rename(old)
        try:
            stage.rename(dest)
        except OSError:
            if old is not None:
                old.rename(dest)
            raise
        if old is not None:
            shutil.rmtree(old)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    kind = "stock tag (unpatched reference copy)" if unpatched else f"patched with {PATCH_FILE.name}"
    return f"{dest}: RadioLib {TAG} ({COMMIT[:12]}), {len(tree)} files, {kind}"


def check(root: Path, unpatched: bool) -> str:
    tree = read_tree(root, strict=True)
    prov_path = root / PROVENANCE
    if not prov_path.is_file():
        raise VerifyError(f"{root}: {PROVENANCE} is missing (not written by tools/radiolib.py)")
    prov = parse_provenance(prov_path.read_bytes())
    patches: list[FilePatch] = []
    patch_sha: str | None = None
    if not unpatched:
        patches, patch_sha = load_patch()
    expected = {"tag": TAG, "commit": COMMIT, "pristine-tree-sha256": PRISTINE_TREE_SHA256,
                "patch-sha256": patch_sha or "none"}
    for key, value in expected.items():
        if prov.get(key) != value:
            raise VerifyError(f"{PROVENANCE}: {key} is {prov.get(key)!r}, expected {value!r}")
    pristine = tree if unpatched else apply_patch(tree, patches, reverse=True)
    digest = tree_digest(pristine)
    if digest != PRISTINE_TREE_SHA256:
        raise VerifyError(f"{root}: files differ from tag {TAG} "
                          f"(digest {digest}{'' if unpatched else ' after removing the patch'})")
    if not unpatched and apply_patch(pristine, patches) != tree:
        raise VerifyError(f"{root}: patched files differ from {PATCH_FILE.name}")
    kind = "stock tag" if unpatched else f"+ {PATCH_FILE.name} (sha256 {patch_sha})"
    return f"ok: {root} is RadioLib {TAG} ({COMMIT[:12]}) {kind}, {len(tree)} files"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch, patch and verify RadioLib 7.8.1 for the VGQ sketches.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_fetch = sub.add_parser("fetch", help="clone the tag, verify, patch and write the copy to DEST")
    p_fetch.add_argument("dest", type=Path)
    p_fetch.add_argument("--repo", default=UPSTREAM_URL, help="mirror or local clone (default: upstream)")
    p_fetch.add_argument("--unpatched", action="store_true", help="write the stock tag (comparison builds only)")
    p_check = sub.add_parser("check", help="verify offline that DIR is the patched (or stock) tag")
    p_check.add_argument("dir", type=Path)
    p_check.add_argument("--unpatched", action="store_true", help="expect the stock tag")
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch":
            print(fetch(args.dest, args.repo, args.unpatched))
        else:
            print(check(args.dir, args.unpatched))
    except VerifyError as exc:
        print(f"radiolib.py: verification failed: {exc}", file=sys.stderr)
        return EXIT_VERIFY
    except UpstreamError as exc:
        print(f"radiolib.py: upstream not available: {exc}", file=sys.stderr)
        return EXIT_UPSTREAM
    except OSError as exc:
        print(f"radiolib.py: {exc}", file=sys.stderr)
        return EXIT_VERIFY
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
