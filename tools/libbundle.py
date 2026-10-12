"""Shared engine of tools/radiolib.py and tools/routerbridge.py: fetch, patch, verify.

Contract v3 sections 25.2, 30 and 41: a third-party Arduino library that needs a
fix we cannot make from our own code is obtained from its upstream repository at a
pinned release tag, verified (tag commit and SHA-256 of the bundled files), patched
with a documented exact-hunk patch from tools/ and bundled next to each sketch as
sketch/libraries/<name> ("- dir: libraries/<name>" in sketch.yaml). The repository
keeps only the patch and the tool; the boards never download the library.

Each tool describes its library with a LibrarySpec and calls main(spec). Commands:
  fetch DEST [--repo URL] [--unpatched]
      git clone --depth 1 --branch <tag> of the upstream repository (or of the
      mirror / local clone given by --repo), verify the tag commit and the digest
      of the bundled files, apply the patch with an exact-match hunk applier (no
      fuzz, no offset: any mismatch aborts), and write the copy to DEST together
      with PROVENANCE.txt. DEST must be absent, empty, or a copy written by the
      tool (it is then replaced). --unpatched writes the stock tag instead: a
      reference copy for comparison builds only, never for a sketch.
  check DIR [--unpatched]
      Offline verification that DIR is exactly the pinned tag plus the current
      patch (or the stock tag with --unpatched): the file set, every byte, and
      PROVENANCE.txt. Reverse-applying the patch must give the pinned pristine
      digest and re-applying it must give DIR back.

The pristine digest is the SHA-256 of the sha256sum-style manifest
"<sha256 of file>  <path>\\n" of the bundled files sorted by path, i.e. the same
value as
  (cd DIR && find <bundled files and dirs> -type f | LC_ALL=C sort |
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

PROVENANCE = "PROVENANCE.txt"
GIT_TIMEOUT_S = 300                   # shallow clone of one tag
MAX_PATCH_BYTES = 1024 * 1024

EXIT_OK, EXIT_VERIFY, EXIT_USAGE, EXIT_UPSTREAM = 0, 1, 2, 3

Tree = dict[str, bytes]               # POSIX path relative to the library root -> content


@dataclass(frozen=True)
class LibrarySpec:
    """What is pinned for one bundled library."""
    tool: str                         # script name, e.g. "radiolib.py" (messages, PROVENANCE)
    name: str                         # Arduino library name = bundled folder name
    upstream_url: str
    tag: str
    commit: str                       # full SHA-1 of the tag's commit
    patch_file: Path
    bundle_files: tuple[str, ...]     # top-level files that are bundled (all required)
    bundle_dirs: tuple[str, ...]      # top-level directories bundled completely
    pristine_tree_sha256: str         # digest of the bundled files of the stock tag
    contract: str                     # e.g. "contract v3 section 25.2"
    license_line: str                 # PROVENANCE "license:" value
    max_files: int                    # walk bound
    max_file_bytes: int
    description: str                  # argparse description


class VerifyError(Exception):
    """The copy, the clone or the patch does not match what is pinned (exit 1)."""


class UpstreamError(Exception):
    """git is not installed or the repository cannot be reached (exit 3)."""


# ---------------------------------------------------------------------------
# Trees
# ---------------------------------------------------------------------------
def _bundled(spec: LibrarySpec, rel: str) -> bool:
    return rel in spec.bundle_files or any(rel.startswith(d + "/") for d in spec.bundle_dirs)


def read_tree(spec: LibrarySpec, root: Path, *, strict: bool) -> Tree:
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
            dirnames[:] = [d for d in dirnames if d in spec.bundle_dirs]   # skip .git, examples, ...
        for name in sorted(dirnames) + sorted(filenames):
            count += 1
            if count > spec.max_files:
                raise VerifyError(f"{root}: more than {spec.max_files} entries")
            path = here / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                if strict or _bundled(spec, rel) or rel in spec.bundle_dirs:
                    raise VerifyError(f"{rel}: symbolic links are not allowed")
                continue
            if name in dirnames:
                if strict and rel_dir == "." and name not in spec.bundle_dirs:
                    raise VerifyError(f"{rel}/: not part of the bundled library")
                continue
            if rel == PROVENANCE and strict:
                continue
            if not _bundled(spec, rel):
                if strict:
                    raise VerifyError(f"{rel}: not part of the bundled library")
                continue
            size = path.stat().st_size
            if size > spec.max_file_bytes:
                raise VerifyError(f"{rel}: {size} octets exceeds the {spec.max_file_bytes} octet bound")
            tree[rel] = path.read_bytes()
    for name in spec.bundle_files:
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


def _patch_path(spec: LibrarySpec, line: bytes, prefix: bytes) -> str:
    raw = line[len(prefix):].rstrip(b"\r\n").split(b"\t", 1)[0]
    try:
        rel = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise VerifyError(f"patch: non-ASCII path {raw!r}") from exc
    parts = rel.split("/")
    if not rel or rel.startswith("/") or any(p in ("", ".", "..") for p in parts) or not _bundled(spec, rel):
        raise VerifyError(f"patch: path {rel!r} is not a file of the bundled library")
    return rel


def parse_patch(spec: LibrarySpec, text: bytes) -> list[FilePatch]:
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
        old_path = _patch_path(spec, lines[i], b"--- a/")
        new_path = _patch_path(spec, lines[i + 1], b"+++ b/")
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


def load_patch(spec: LibrarySpec) -> tuple[list[FilePatch], str]:
    """Returns the parsed patch and the SHA-256 of its (LF) text.

    A checkout that converted the whole file to CRLF (Windows autocrlf) is turned
    back into LF: the libraries' sources are LF, and the digest must not depend on
    the host the packager runs on.
    """
    if not spec.patch_file.is_file():
        raise VerifyError(f"{spec.patch_file}: patch file missing")
    data = spec.patch_file.read_bytes()
    if len(data) > MAX_PATCH_BYTES:
        raise VerifyError(f"{spec.patch_file}: larger than {MAX_PATCH_BYTES} octets")
    if data.count(b"\r\n") == data.count(b"\n") and b"\r\n" in data:
        data = data.replace(b"\r\n", b"\n")
    return parse_patch(spec, data), hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Provenance note
# ---------------------------------------------------------------------------
def provenance_text(spec: LibrarySpec, tree: Tree, patch_sha: str | None) -> bytes:
    lines = [
        f"# {spec.name} bundled with the VGQ-1 / DSS-Q1 Arduino sketches ({spec.contract}).",
        f"# Generated by tools/{spec.tool} - do not edit; re-run 'python3 tools/{spec.tool} fetch <dir>'.",
        f"# Verify offline with 'python3 tools/{spec.tool} check <dir>'.",
        f"library: {spec.name}",
        f"upstream: {spec.upstream_url}",
        f"tag: {spec.tag}",
        f"commit: {spec.commit}",
        f"pristine-tree-sha256: {spec.pristine_tree_sha256}",
        f"patch: {spec.patch_file.name if patch_sha else 'none (stock tag, reference copy only)'}",
        f"patch-sha256: {patch_sha or 'none'}",
        f"files: {', '.join(spec.bundle_files)}, {', '.join(d + '/' for d in spec.bundle_dirs)} ({len(tree)} files)",
        f"license: {spec.license_line}",
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


def fetch(spec: LibrarySpec, dest: Path, repo: str, unpatched: bool) -> str:
    _prepare_destination(dest)
    patches: list[FilePatch] = []
    patch_sha: str | None = None
    if not unpatched:
        patches, patch_sha = load_patch(spec)     # a broken patch fails before any download
    prefix = spec.name.lower()
    with tempfile.TemporaryDirectory(prefix=f"{prefix}-") as tmp:
        clone = Path(tmp) / "clone"
        _git(["clone", "--quiet", "--depth", "1", "--single-branch", "--branch", spec.tag,
              "-c", "core.autocrlf=false", "-c", "core.eol=lf", "-c", "advice.detachedHead=false",
              repo, str(clone)])
        head = _git(["-C", str(clone), "rev-parse", "HEAD^{commit}"]).strip()
        if head != spec.commit:
            raise VerifyError(f"tag {spec.tag} of {repo} is commit {head}, expected {spec.commit}")
        tree = read_tree(spec, clone, strict=False)
    digest = tree_digest(tree)
    if digest != spec.pristine_tree_sha256:
        raise VerifyError(f"bundled files of commit {spec.commit} hash to {digest}, "
                          f"expected {spec.pristine_tree_sha256}")
    if not unpatched:
        tree = apply_patch(tree, patches)
    # Built next to DEST and renamed into place, so DEST is never half written.
    stage = Path(tempfile.mkdtemp(prefix=f".{prefix}-", dir=dest.parent))
    try:
        write_tree(stage, tree)
        (stage / PROVENANCE).write_bytes(provenance_text(spec, tree, patch_sha))
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
    kind = "stock tag (unpatched reference copy)" if unpatched else f"patched with {spec.patch_file.name}"
    return f"{dest}: {spec.name} {spec.tag} ({spec.commit[:12]}), {len(tree)} files, {kind}"


def check(spec: LibrarySpec, root: Path, unpatched: bool) -> str:
    tree = read_tree(spec, root, strict=True)
    prov_path = root / PROVENANCE
    if not prov_path.is_file():
        raise VerifyError(f"{root}: {PROVENANCE} is missing (not written by tools/{spec.tool})")
    prov = parse_provenance(prov_path.read_bytes())
    patches: list[FilePatch] = []
    patch_sha: str | None = None
    if not unpatched:
        patches, patch_sha = load_patch(spec)
    expected = {"library": spec.name, "tag": spec.tag, "commit": spec.commit,
                "pristine-tree-sha256": spec.pristine_tree_sha256, "patch-sha256": patch_sha or "none"}
    for key, value in expected.items():
        if prov.get(key) != value:
            raise VerifyError(f"{PROVENANCE}: {key} is {prov.get(key)!r}, expected {value!r}")
    pristine = tree if unpatched else apply_patch(tree, patches, reverse=True)
    digest = tree_digest(pristine)
    if digest != spec.pristine_tree_sha256:
        raise VerifyError(f"{root}: files differ from tag {spec.tag} "
                          f"(digest {digest}{'' if unpatched else ' after removing the patch'})")
    if not unpatched and apply_patch(pristine, patches) != tree:
        raise VerifyError(f"{root}: patched files differ from {spec.patch_file.name}")
    kind = "stock tag" if unpatched else f"+ {spec.patch_file.name} (sha256 {patch_sha})"
    return f"ok: {root} is {spec.name} {spec.tag} ({spec.commit[:12]}) {kind}, {len(tree)} files"


def main(spec: LibrarySpec, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=spec.tool, description=spec.description)
    sub = parser.add_subparsers(dest="command", required=True)
    p_fetch = sub.add_parser("fetch", help="clone the tag, verify, patch and write the copy to DEST")
    p_fetch.add_argument("dest", type=Path)
    p_fetch.add_argument("--repo", default=spec.upstream_url, help="mirror or local clone (default: upstream)")
    p_fetch.add_argument("--unpatched", action="store_true", help="write the stock tag (comparison builds only)")
    p_check = sub.add_parser("check", help="verify offline that DIR is the patched (or stock) tag")
    p_check.add_argument("dir", type=Path)
    p_check.add_argument("--unpatched", action="store_true", help="expect the stock tag")
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch":
            print(fetch(spec, args.dest, args.repo, args.unpatched))
        else:
            print(check(spec, args.dir, args.unpatched))
    except VerifyError as exc:
        print(f"{spec.tool}: verification failed: {exc}", file=sys.stderr)
        return EXIT_VERIFY
    except UpstreamError as exc:
        print(f"{spec.tool}: upstream not available: {exc}", file=sys.stderr)
        return EXIT_UPSTREAM
    except OSError as exc:
        print(f"{spec.tool}: {exc}", file=sys.stderr)
        return EXIT_VERIFY
    return EXIT_OK
