#!/usr/bin/env python3
"""Build and verify the two Arduino App Lab packages (contract v3 sections 18, 23, 30, 41).

Each package is a zip whose single top-level folder is an App, the form Arduino App Lab
imports (My Apps -> open any App -> the menu next to its name -> Import App; the App lands
in ~/ArduinoApps/<folder> on the board):

    dist/vgq1-spacecraft-unoq.zip          folder vgq1-spacecraft     Arduino UNO Q
    dist/dssq-groundstation-ventunoq.zip   folder dssq-groundstation  Arduino VENTUNO Q
    dist/SHA256SUMS                        check with: cd dist && sha256sum -c SHA256SUMS

What a package contains
  * The files git tracks under the App folder ('git ls-files <App>'), as they are in the
    working tree; untracked files never enter. Excluded even when tracked: hidden files and
    folders (App Lab's .cache among them), __pycache__ and *.pyc, tests (tests/, test/,
    test_*.py, *_test.py, conftest.py), data/ (App Lab's persistent data folder),
    python/egse_data/ and python/archive/ (runtime data of the EGSE and of the host-mode
    station), dist/ and App Lab's release.yaml. A file that git stores with LF line ends
    but the checkout converted to CRLF (core.autocrlf) is packaged with LF, so a Windows
    checkout gives the same octets.
  * sketch/libraries/RadioLib and sketch/libraries/Arduino_RouterBridge, the folders
    sketch.yaml names with "- dir:": the patched copies written by tools/radiolib.py and
    tools/routerbridge.py, with their PROVENANCE.txt and license file. By default they are
    fetched from GitHub (tag commit and pristine SHA-256 verified, exact-hunk patch);
    --libs-from DIR reuses copies those tools wrote earlier. Either way, the exact octets
    that are packaged pass the tools' offline 'check' first.
  * Rejected (exit 1): a requirements.txt (contract 23: App Lab would pip-install it from
    the internet at the first start), tracked files under sketch/libraries/, symbolic
    links and submodules.

Secrets never enter a package. The build is REFUSED (exit 4, nothing written) when
  * a file or folder whose name matches *sdls_key*, http_token*, *.key or *.pem (any case)
    exists anywhere under an App folder on disk, tracked or not, data/ and the local library
    copies included. Move such files out of the App folders (keep them safe: they are the
    link keys) or package from a clean clone;
  * a packaged file holds key material: nothing but 64 hex digits (the sdls_key.hex
    format) or a definition of kVgqSdlsKey[32] (the sdls_key.h format);
  * the packaged station.toml sets [gds] http_token or [commanding] auth_token.
Keys are generated after the import (tools/gen_sdls_key.py; see the App READMEs).

Reproducible: entries in path order (a folder before its contents) with explicit folder
entries; every timestamp is SOURCE_DATE_EPOCH when set, else the committer date of the
newest commit that touched a packaging input (the App folders, this tool, the library tools
and their patches), so a later commit that only adds dist/ does not change it; attributes
normalised (Unix "made by", folders 0755, files 0644, *.sh 0755); deflate level 6; no extra
fields, no comments. Two builds of the same inputs are byte-identical (with the same zlib,
whose version the build prints).

Verification, run on every build before anything is written and by --verify on the zips in
--out: one top-level folder named as the App (a valid App Lab folder name); canonical order,
one timestamp and the normalised attributes on every entry; CRCs; the App layout (app.yaml,
README.md, python/main.py, sketch/sketch.ino, sketch/sketch.yaml and the App's own required
files); no requirements.txt, no excluded names, no key files or key material; app.yaml
(name, icon, description; no other key but ports); sketch.yaml (default profile, platform
arduino:zephyr, the App's board, "- dir: libraries/RadioLib" and "- dir:
libraries/Arduino_RouterBridge", every "- dir:" present); exactly the two libraries, each
passing its offline 'check'; the ground configuration (secrets empty, web port published by
app.yaml ports); every Python file compiles. --verify also checks SHA256SUMS. --build adds a
real compilation of each unpacked sketch, which must exit with status 0 and log
"Using library Arduino_RouterBridge at version 0.4.3 in folder:
<unpacked sketch>/libraries/Arduino_RouterBridge" (the bundled patched copy, not the
platform's).

Usage
  python3 tools/package_apps.py [--out DIR] [--libs-from DIR] [--build [--compiler PATH]]
  python3 tools/package_apps.py --verify [--out DIR] [--build [--compiler PATH]]
--compiler names an acompile.sh-style wrapper, called as 'PATH <unoq|ventunoq> <sketch>
--out <dir>', or arduino-cli itself, called as 'arduino-cli compile --profile default -v
--build-path <dir> <sketch>'; the default is $VGQ_ACOMPILE, else arduino-cli from PATH.

Exit status: 0 success; 1 a check failed (packaging input, package, bundled library or
compilation); 2 usage or environment error (not a git work tree, git missing, no compiler
for --build, bad SOURCE_DATE_EPOCH); 3 a library could not be fetched (GitHub unreachable);
4 refused: secret material in an App folder or in a package.
Python 3.11+, standard library only; needs the git command line.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import warnings
import zipfile
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # tools/libbundle.py & co, also under python -I
import libbundle
import radiolib
import routerbridge

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "dist"
SUMS_NAME = "SHA256SUMS"
LIB_DIR = "sketch/libraries"                  # where sketch.yaml's "- dir: libraries/<name>" point
LIBRARIES: tuple[libbundle.LibrarySpec, ...] = (radiolib.SPEC, routerbridge.SPEC)
BRIDGE = routerbridge.SPEC                    # the copy --build must see compiled

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_UPSTREAM, EXIT_SECRET = 0, 1, 2, 3, 4

# ---- Zip format ---------------------------------------------------------------------------
UNIX = 3                                      # "version made by" host: App Lab reads Unix modes
DIR_MODE, FILE_MODE, EXEC_MODE = 0o755, 0o644, 0o755
EXEC_SUFFIXES = (".sh",)
MSDOS_DIR_FLAG = 0x10
COMPRESS_LEVEL = 6                            # zlib's default: within 1 % of level 9, 3x faster
DOS_EPOCH_MIN = 315532800                     # 1980-01-01T00:00:00Z, the earliest zip timestamp
DOS_EPOCH_MAX = 4354819198                    # 2107-12-31T23:59:58Z, the latest
FLAG_ENCRYPTED, FLAG_DATA_DESCRIPTOR = 0x1, 0x8
APP_FOLDER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_ -]*")   # arduino-app-cli app.IsValidFolderName

# ---- Bounds (a package to verify may come from anywhere) -----------------------------------
MAX_ENTRIES = 4000                            # the packages hold about 300
MAX_TOTAL_BYTES = 64 * 1024 * 1024            # unpacked size of one package (about 13 MB)
GIT_TIMEOUT_S = 120
COMPILE_TIMEOUT_S = 3600                      # one sketch with the real toolchain takes minutes

# ---- Secrets -------------------------------------------------------------------------------
SECRET_NAME_PATTERNS = ("*sdls_key*", "http_token*", "*.key", "*.pem")
HEX_KEY_RE = re.compile(rb"\s*[0-9A-Fa-f]{64}\s*")          # tools/gen_sdls_key.py ground key file
C_KEY_RE = re.compile(rb"kVgqSdlsKey\s*\[\s*32\s*\]\s*=")   # tools/gen_sdls_key.py flight header
STATION_SECRETS = (("gds", "http_token"), ("commanding", "auth_token"))

# ---- Exclusions (paths relative to the App folder) ------------------------------------------
TEST_DIRS = frozenset({"tests", "test"})
TEST_FILES = ("test_*.py", "*_test.py", "conftest.py")
DATA_DIRS = (("data",), ("python", "egse_data"), ("python", "archive"))

# ---- app.yaml ------------------------------------------------------------------------------
APP_KEYS_REQUIRED = ("name", "icon", "description")
APP_KEYS_ALLOWED = frozenset({*APP_KEYS_REQUIRED, "ports"})


@dataclass(frozen=True)
class AppSpec:
    """One App Lab App of the repository; its folder is also the package's top-level folder."""

    folder: str
    zip_name: str
    board: str                                # FQBN arduino:zephyr:<board>; acompile.sh board name
    required: tuple[str, ...]                 # files every package of this App must contain
    station_toml: str | None = None           # packaged ground configuration (secrets, web port)


COMMON_REQUIRED = ("app.yaml", "README.md", "python/main.py", "sketch/sketch.ino", "sketch/sketch.yaml")
APPS: tuple[AppSpec, ...] = (
    AppSpec("vgq1-spacecraft", "vgq1-spacecraft-unoq.zip", "unoq",
            (*COMMON_REQUIRED, "python/cfdp_tx.py", "sketch/src/config.h")),
    AppSpec("dssq-groundstation", "dssq-groundstation-ventunoq.zip", "ventunoq",
            (*COMMON_REQUIRED, "python/dssq/__init__.py", "python/config/station.toml",
             "host/install_station.sh", "host/dssq-gds.service"),
            station_toml="python/config/station.toml"),
)


class UsageError(Exception):
    """Wrong arguments or a missing prerequisite (exit 2)."""


class CheckError(Exception):
    """A packaging input failed a check (exit 1); the message lists every finding."""


class SecretError(Exception):
    """Secret material in an App folder or in a packaged file (exit 4)."""


@dataclass(frozen=True)
class Finding:
    """One verification failure; `secret` marks key material (exit 4 instead of 1)."""

    text: str
    secret: bool = False


@dataclass(frozen=True)
class BundledLibrary:
    """A verified library copy: file path relative to the library folder -> content."""

    spec: libbundle.LibrarySpec
    files: dict[str, bytes]
    note: str


@dataclass(frozen=True)
class Compiler:
    """How --build compiles a sketch: an acompile.sh-style wrapper or arduino-cli itself."""

    path: str
    wrapper: bool


# =============================================================================================
# Rules on names and content
# =============================================================================================
def is_secret_name(name: str) -> bool:
    """True when a file or folder name looks like a key or token file (case-insensitive)."""
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, pattern) for pattern in SECRET_NAME_PATTERNS)


def secret_content_reason(data: bytes) -> str | None:
    """Why a file's content is key material, or None."""
    if HEX_KEY_RE.fullmatch(data):
        return "holds nothing but 64 hex digits (the SDLS key file format)"
    if C_KEY_RE.search(data):
        return "defines kVgqSdlsKey[32] (the SDLS key header format)"
    return None


def exclusion_reason(rel: str) -> str | None:
    """Why a path relative to the App folder never enters a package, or None."""
    parts = rel.split("/")
    name = parts[-1]
    if any(part.startswith(".") for part in parts):
        return "hidden file or folder"
    if any(tuple(parts[:len(prefix)]) == prefix for prefix in DATA_DIRS):
        return "runtime data"
    if rel == "release.yaml":
        return "App Lab release manifest"
    if "__pycache__" in parts or name.endswith((".pyc", ".pyo")):
        return "Python cache"
    if any(part in TEST_DIRS for part in parts[:-1]) or any(fnmatch.fnmatchcase(name, p) for p in TEST_FILES):
        return "test"
    if "dist" in parts[:-1]:
        return "build output"
    return None


def input_error(rel: str) -> str | None:
    """Why a (not excluded) path relative to the App folder must not be packaged at all, or None."""
    if rel.split("/")[-1].lower() == "requirements.txt":
        return ("requirements.txt is not allowed in an App: App Lab would pip-install it from the "
                "internet at the first start (contract 23)")
    if rel == LIB_DIR or rel.startswith(LIB_DIR + "/"):
        return ("the bundled libraries are added by this tool from verified copies; "
                "sketch/libraries/ must not be tracked")
    return None


def file_mode(name: str) -> int:
    """Normalised permission bits of a packaged file."""
    return EXEC_MODE if name.endswith(EXEC_SUFFIXES) else FILE_MODE


def entry_sort_key(name: str) -> tuple[str, ...]:
    """Canonical entry order: by path components, so a folder precedes its contents."""
    return tuple(name.rstrip("/").split("/"))


def zip_date_time(epoch: int) -> tuple[int, int, int, int, int, int]:
    """The zip (MS-DOS, UTC) timestamp of `epoch`: clamped to 1980, even seconds."""
    if epoch > DOS_EPOCH_MAX:
        raise UsageError(f"timestamp {epoch} is after 2107, the last date a zip can hold")
    t = time.gmtime(max(epoch, DOS_EPOCH_MIN))
    return (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec - t.tm_sec % 2)


def station_secret_findings(label: str, data: bytes) -> tuple[list[str], list[str]]:
    """(secret findings, other errors) for a packaged station.toml."""
    try:
        cfg = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [], [f"{label}: not valid TOML: {exc}"]
    secrets: list[str] = []
    for table, key in STATION_SECRETS:
        section = cfg.get(table, {})
        value = section.get(key, "") if isinstance(section, dict) else ""
        if value != "":
            secrets.append(f"{label}: [{table}] {key} is set; it is a secret and must stay empty in a "
                           "package (the App creates or reads it under <App>/data)")
    return secrets, []


# =============================================================================================
# git
# =============================================================================================
def _git(repo: Path, *args: str) -> bytes:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=GIT_TIMEOUT_S,
                              env=env, check=False)
    except FileNotFoundError as exc:
        raise UsageError("the git command line is not installed (packages are built from the files git "
                         "tracks)") from exc
    except subprocess.TimeoutExpired as exc:
        raise UsageError(f"git {args[0]} timed out after {GIT_TIMEOUT_S} s") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise UsageError(f"git {' '.join(args[:2])} failed: {detail}")
    return proc.stdout


def require_work_tree(repo: Path) -> None:
    """The packages are built from what git tracks: refuse to run outside a work tree."""
    if _git(repo, "rev-parse", "--is-inside-work-tree").strip() != b"true":
        raise UsageError(f"{repo} is not a git work tree")


def crlf_converted(repo: Path, folder: str) -> set[str]:
    """Paths that git stores with LF line ends but the checkout converted to CRLF."""
    converted: set[str] = set()
    for record in _git(repo, "ls-files", "--eol", "-z", "--", folder).split(b"\0"):
        meta, sep, raw = record.partition(b"\t")
        fields = meta.split()
        if sep and len(fields) >= 2 and fields[0] == b"i/lf" and fields[1] == b"w/crlf":
            converted.add(raw.decode("utf-8", "replace"))
    return converted


def packaging_inputs(repo: Path) -> list[str]:
    """Paths whose newest commit dates the packages: the Apps, this tool, the library tools and patches."""
    tools = ["tools/package_apps.py", "tools/libbundle.py"]
    for spec in LIBRARIES:
        tools.append(f"tools/{spec.tool}")
        try:
            tools.append(spec.patch_file.resolve().relative_to(repo.resolve()).as_posix())
        except ValueError:
            tools.append(f"tools/{spec.patch_file.name}")
    return [app.folder for app in APPS] + tools


def source_date_epoch(repo: Path) -> tuple[int, str]:
    """The packages' timestamp and where it comes from (see the module docstring)."""
    raw = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if raw:
        if not re.fullmatch(r"[0-9]{1,12}", raw):
            raise UsageError(f"SOURCE_DATE_EPOCH={raw!r} is not a number of seconds")
        return int(raw), "SOURCE_DATE_EPOCH"
    try:
        found = _git(repo, "log", "-1", "--format=%ct %h", "--", *packaging_inputs(repo)).decode().strip()
        origin = "the newest commit that touched a packaging input"
        if not found:
            found = _git(repo, "log", "-1", "--format=%ct %h").decode().strip()
            origin = "HEAD (no commit touched the packaging inputs yet)"
    except UsageError as exc:
        raise UsageError(f"{exc}\nCommit first, or set SOURCE_DATE_EPOCH.") from exc
    seconds, _, short = found.partition(" ")
    return int(seconds), f"commit {short}, {origin}"


def uncommitted_count(repo: Path) -> int | None:
    """Files under the App folders that differ from HEAD (None when there is no HEAD)."""
    try:
        out = _git(repo, "diff", "--name-only", "-z", "HEAD", "--", *(app.folder for app in APPS))
    except UsageError:
        return None
    return sum(1 for path in out.split(b"\0") if path)


# =============================================================================================
# Inputs: secrets on disk, tracked App files, bundled libraries
# =============================================================================================
def _raise(exc: OSError) -> None:
    raise exc


def secret_files_on_disk(repo: Path) -> list[str]:
    """Every file or folder under an App folder whose name looks like a key or token, tracked or not."""
    hits: list[str] = []
    for app in APPS:
        root = repo / app.folder
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, onerror=_raise):
            for name in (*dirnames, *filenames):
                if is_secret_name(name):
                    hits.append((Path(dirpath) / name).relative_to(repo).as_posix())
    return sorted(hits)


def collect_app_files(repo: Path, app: AppSpec) -> tuple[dict[str, bytes], dict[str, str], list[str], list[str]]:
    """The App's packaged files (path relative to the App folder -> content), the excluded tracked
    paths with the reason, and the secret findings and input errors."""
    files: dict[str, bytes] = {}
    excluded: dict[str, str] = {}
    secrets: list[str] = []
    errors: list[str] = []
    prefix = app.folder + "/"
    converted = crlf_converted(repo, app.folder)
    unmerged: set[str] = set()
    for record in _git(repo, "ls-files", "-s", "-z", "--", app.folder).split(b"\0"):
        if not record:
            continue
        meta, _, raw = record.partition(b"\t")
        fields = meta.split(b" ")
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{raw!r}: the file name is not UTF-8")
            continue
        if len(fields) != 3 or not path.startswith(prefix):
            raise UsageError(f"unexpected git ls-files output: {record!r}")
        mode, stage = fields[0].decode(), fields[2].decode()
        rel = path[len(prefix):]
        if stage != "0":
            unmerged.add(path)
            continue
        reason = exclusion_reason(rel)
        if reason is not None:
            excluded[rel] = reason
            continue
        problem = input_error(rel)
        if problem is not None:
            errors.append(f"{path}: {problem}")
            continue
        if mode not in ("100644", "100755"):
            errors.append(f"{path}: git mode {mode} (a symbolic link or a submodule) cannot be packaged")
            continue
        full = repo / path
        try:
            st = full.lstat()
        except FileNotFoundError:
            errors.append(f"{path}: tracked but missing from the working tree (restore it or remove it from git)")
            continue
        if not stat.S_ISREG(st.st_mode):
            errors.append(f"{path}: not a regular file in the working tree")
            continue
        data = full.read_bytes()
        if path in converted:
            data = data.replace(b"\r\n", b"\n")
        why = secret_content_reason(data)
        if why is not None:
            secrets.append(f"{path}: {why}")
        files[rel] = data
    errors.extend(f"{path}: unmerged (resolve the conflict first)" for path in sorted(unmerged))
    if not files and not errors:
        errors.append(f"{app.folder}/: no tracked files (is the App folder committed or staged?)")
    if app.station_toml is not None and app.station_toml in files:
        found, bad = station_secret_findings(prefix + app.station_toml, files[app.station_toml])
        secrets.extend(found)
        errors.extend(bad)
    return files, excluded, secrets, errors


def acquire_libraries(libs_from: Path | None, work: Path) -> list[BundledLibrary]:
    """The patched library copies to bundle: fetched from GitHub (default) or read from `libs_from`.

    The files are read into memory, written to a private folder and checked THERE with the
    tools' offline check, so the octets that are packaged are exactly the ones verified.
    Raises libbundle.VerifyError (exit 1) or libbundle.UpstreamError (exit 3).
    """
    libs: list[BundledLibrary] = []
    for spec in LIBRARIES:
        if libs_from is None:
            source = work / "fetched" / spec.name
            fetched = libbundle.fetch(spec, source, spec.upstream_url, unpatched=False)
        else:
            source = libs_from / spec.name
            fetched = f"{source}: copy written earlier by tools/{spec.tool}"
        tree = libbundle.read_tree(spec, source, strict=True)
        provenance = source / libbundle.PROVENANCE
        if not provenance.is_file():
            raise libbundle.VerifyError(f"{source}: {libbundle.PROVENANCE} is missing (not written by "
                                        f"tools/{spec.tool})")
        tree[libbundle.PROVENANCE] = provenance.read_bytes()
        private = work / "checked" / spec.name
        libbundle.write_tree(private, tree)
        checked = libbundle.check(spec, private, unpatched=False)
        libs.append(BundledLibrary(spec, tree, f"{fetched}\n  {checked}"))
    return libs


# =============================================================================================
# Building a zip
# =============================================================================================
def build_zip(top: str, files: dict[str, bytes], date_time: tuple[int, int, int, int, int, int]) -> bytes:
    """A reproducible zip of `files` (path relative to `top` -> content) under the folder `top`."""
    folders = {top}
    for rel in files:
        parts = rel.split("/")
        folders.update("/".join([top, *parts[:i]]) for i in range(1, len(parts)))
    clash = folders & {f"{top}/{rel}" for rel in files}
    if clash:
        raise CheckError(f"{sorted(clash)[0]}: both a file and a folder")
    entries: list[tuple[str, bytes | None]] = [(f"{folder}/", None) for folder in folders]
    entries.extend((f"{top}/{rel}", data) for rel, data in files.items())
    entries.sort(key=lambda entry: entry_sort_key(entry[0]))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time)
            info.create_system = UNIX
            if data is None:
                info.external_attr = ((stat.S_IFDIR | DIR_MODE) << 16) | MSDOS_DIR_FLAG
                info.CRC = 0
                archive.mkdir(info)
            else:
                info.external_attr = (stat.S_IFREG | file_mode(name)) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data, compresslevel=COMPRESS_LEVEL)
    return buffer.getvalue()


# =============================================================================================
# Verifying a zip
# =============================================================================================
def _safe_name(name: str, top: str) -> bool:
    body = name[:-1] if name.endswith("/") else name
    parts = body.split("/")
    return (parts[0] == top and "\\" not in name and "\0" not in name
            and all(part not in ("", ".", "..") for part in parts))


def check_entries(app: AppSpec, archive: zipfile.ZipFile) -> tuple[list[Finding], bool]:
    """Structural checks of every entry; the flag is False when extracting would be unsafe."""
    infos = archive.infolist()
    top = app.folder
    if not infos:
        return [Finding("the archive is empty")], False
    if len(infos) > MAX_ENTRIES:
        return [Finding(f"{len(infos)} entries, more than the {MAX_ENTRIES} allowed")], False
    total = sum(info.file_size for info in infos)
    if total > MAX_TOTAL_BYTES:
        return [Finding(f"{total} octets unpacked, more than the {MAX_TOTAL_BYTES} allowed")], False
    names = [info.filename for info in infos]
    unsafe = [name for name in names if not _safe_name(name, top)]
    if unsafe:
        return [Finding(f"{name!r}: unsafe name or outside the folder {top}/") for name in unsafe[:10]], False
    if len(set(names)) != len(names):
        return [Finding("duplicate entry names")], False
    findings: list[Finding] = []
    if not APP_FOLDER_RE.fullmatch(top):
        findings.append(Finding(f"{top!r} is not a valid App Lab folder name"))
    if names[0] != f"{top}/":
        findings.append(Finding(f"the first entry is {names[0]!r}, not the folder {top}/"))
    if names != sorted(names, key=entry_sort_key):
        findings.append(Finding("entries are not in canonical (path) order"))
    if archive.comment:
        findings.append(Finding("the archive has a comment"))
    folders = {name for name in names if name.endswith("/")}
    stamp = infos[0].date_time
    for info in infos:
        name = info.filename
        parent = name.rstrip("/").rpartition("/")[0]
        if parent and f"{parent}/" not in folders:
            findings.append(Finding(f"{name}: no entry for its folder {parent}/"))
        if info.date_time != stamp:
            findings.append(Finding(f"{name}: timestamp {info.date_time} differs from {stamp}"))
        if info.create_system != UNIX:
            findings.append(Finding(f"{name}: not made by a Unix host (App Lab would lose the file modes)"))
        if info.extra or info.comment:
            findings.append(Finding(f"{name}: extra field or comment present"))
        if info.flag_bits & (FLAG_ENCRYPTED | FLAG_DATA_DESCRIPTOR):
            findings.append(Finding(f"{name}: encrypted or streamed entry"))
        if info.is_dir():
            want_attr = ((stat.S_IFDIR | DIR_MODE) << 16) | MSDOS_DIR_FLAG
            ok = info.external_attr == want_attr and info.compress_type == zipfile.ZIP_STORED and not info.file_size
        else:
            want_attr = (stat.S_IFREG | file_mode(name)) << 16
            ok = info.external_attr == want_attr and info.compress_type == zipfile.ZIP_DEFLATED
        if not ok:
            findings.append(Finding(f"{name}: attributes {info.external_attr:#x} / method {info.compress_type} "
                                    f"(expected {want_attr:#x}, normalised)"))
    return findings, True


def read_contents(archive: zipfile.ZipFile) -> tuple[dict[str, bytes], list[Finding]]:
    """Every file entry's content, read once; zipfile checks each entry's CRC-32 on the way."""
    contents: dict[str, bytes] = {}
    findings: list[Finding] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        try:
            contents[info.filename] = archive.read(info)
        except (zipfile.BadZipFile, zlib.error, OSError) as exc:
            findings.append(Finding(f"{info.filename}: unreadable ({exc})"))
    return contents, findings


def check_names_and_content(app: AppSpec, names: list[str], contents: dict[str, bytes]) -> list[Finding]:
    """No excluded name, no requirements.txt, no key file or key material in the package."""
    findings: list[Finding] = []
    prefix = app.folder + "/"
    for name in names:
        rel = name[len(prefix):].rstrip("/")
        if not rel:
            continue
        if any(is_secret_name(part) for part in rel.split("/")):
            findings.append(Finding(f"{rel}: a key or token file", secret=True))
        if rel.split("/")[-1].lower() == "requirements.txt":
            findings.append(Finding(f"{rel}: requirements.txt must not be in an App (contract 23)"))
        if rel == LIB_DIR or rel.startswith(LIB_DIR + "/"):
            continue                          # library content is checked against the pinned digests
        reason = exclusion_reason(rel)
        if reason is not None:
            findings.append(Finding(f"{rel}: must not be packaged ({reason})"))
        why = secret_content_reason(contents[name]) if name in contents else None
        if why is not None:
            findings.append(Finding(f"{rel}: {why}", secret=True))
    return findings


def extract(names: list[str], contents: dict[str, bytes], dest: Path) -> None:
    """Writes the entries of a package whose names passed check_entries (no absolute or '..' paths)."""
    for name in names:
        target = dest.joinpath(*name.rstrip("/").split("/"))
        if name.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents.get(name, b""))


def _yaml_scalar(text: str) -> str:
    value = text.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    comment = value.find(" #")
    value = (value[:comment] if comment >= 0 else value).strip()
    if ": " in value or value.endswith(":") or value[:1] in tuple("&*!|>%@`{[]}'\"#,"):
        raise ValueError(f"{value[:40]!r} is not a plain YAML scalar (quote it)")
    return value


_YAML_KEY_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*):(?:[ \t]+(.*))?")
_YAML_ITEM_RE = re.compile(r"[ \t]*-[ \t]+(.*)")


def parse_app_descriptor(text: str) -> dict[str, str | list[str]]:
    """Reads app.yaml in the small YAML subset App descriptors use: top-level `key: scalar`
    lines and `key:` followed by indented `- item` lines (or `key: [a, b]`). Raises ValueError
    on anything else, so an unexpected construct is reported rather than misread."""
    result: dict[str, str | list[str]] = {}
    items: list[str] | None = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            item = _YAML_ITEM_RE.fullmatch(line)
            if item is not None:
                if items is None:
                    raise ValueError("list item without a list key")
                items.append(_yaml_scalar(item.group(1)))
                continue
            match = _YAML_KEY_RE.fullmatch(line)
            if match is None:
                raise ValueError("not a top-level 'key: value' line")
            key, value = match.group(1), (match.group(2) or "").strip()
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            items = None
            if not value or value.startswith("#"):
                items = []
                result[key] = items
            elif value.startswith("["):
                if not value.endswith("]"):
                    raise ValueError("unterminated flow list")
                inner = value[1:-1].strip()
                result[key] = [_yaml_scalar(part) for part in inner.split(",")] if inner else []
            else:
                result[key] = _yaml_scalar(value)
        except ValueError as exc:
            raise ValueError(f"line {number}: {exc}") from None
    return result


def looks_like_one_emoji(icon: str) -> bool:
    """Approximates App Lab's single-emoji rule for the icon (go.emoji in arduino-app-cli):
    non-empty, no ASCII and no blanks, at most 10 code points (sequences included)."""
    return 0 < len(icon) <= 10 and all(ord(ch) > 0x7F and not ch.isspace() for ch in icon)


def check_app_yaml(text: str) -> tuple[list[Finding], list[int]]:
    """Findings for app.yaml and the ports it publishes."""
    try:
        desc = parse_app_descriptor(text)
    except ValueError as exc:
        return [Finding(f"app.yaml: {exc}")], []
    findings: list[Finding] = []
    for key in APP_KEYS_REQUIRED:
        value = desc.get(key)
        if not isinstance(value, str) or not value.strip():
            findings.append(Finding(f"app.yaml: {key} must be a non-empty text"))
    icon = desc.get("icon")
    if isinstance(icon, str) and icon and not looks_like_one_emoji(icon):
        findings.append(Finding(f"app.yaml: icon {icon!r} is not a single emoji"))
    findings.extend(Finding(f"app.yaml: key {key!r} is not used by these Apps (allowed: "
                            f"{', '.join(sorted(APP_KEYS_ALLOWED))}; no Bricks)")
                    for key in sorted(set(desc) - APP_KEYS_ALLOWED))
    ports: list[int] = []
    raw_ports = desc.get("ports", [])
    if not isinstance(raw_ports, list):
        findings.append(Finding("app.yaml: ports must be a list"))
    else:
        for item in raw_ports:
            if re.fullmatch(r"[0-9]{1,5}", item) and 1 <= int(item) <= 65535:
                ports.append(int(item))
            else:
                findings.append(Finding(f"app.yaml: port {item!r} is not 1..65535"))
    return findings, ports


_DIR_RE = re.compile(r"\s*-\s*dir:\s*(\S+)\s*")


def check_sketch_yaml(app: AppSpec, sketch: Path) -> list[Finding]:
    """The build profile App Lab uses: default profile, platform, board, the bundled libraries."""
    try:
        lines = (sketch / "sketch.yaml").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return [Finding(f"sketch/sketch.yaml: {exc}")]
    findings: list[Finding] = []
    dirs = [match.group(1) for line in lines if (match := _DIR_RE.fullmatch(line))]
    for spec in LIBRARIES:
        if f"libraries/{spec.name}" not in dirs:
            findings.append(Finding(f'sketch/sketch.yaml: no "- dir: libraries/{spec.name}" line'))
    for entry in dirs:
        parts = entry.split("/")
        if entry.startswith("/") or "\\" in entry or ":" in entry or ".." in parts:
            findings.append(Finding(f"sketch/sketch.yaml: '- dir: {entry}' must be relative to the sketch"))
        elif not (sketch.joinpath(*parts) / "library.properties").is_file():
            findings.append(Finding(f"sketch/sketch.yaml: '- dir: {entry}' is not a library in the package"))
    if not any(re.fullmatch(r"default_profile:\s*default\s*", line) for line in lines):
        findings.append(Finding("sketch/sketch.yaml: 'default_profile: default' missing (App Lab builds it)"))
    if not any(re.fullmatch(r"\s*-\s*platform:\s*arduino:zephyr\s*", line) for line in lines):
        findings.append(Finding("sketch/sketch.yaml: '- platform: arduino:zephyr' missing"))
    fqbns = [m.group(1) for line in lines if (m := re.fullmatch(r"\s*fqbn:\s*(\S+)\s*", line))]
    findings.extend(Finding(f"sketch/sketch.yaml: fqbn {fqbn} is not arduino:zephyr:{app.board}")
                    for fqbn in fqbns if fqbn != f"arduino:zephyr:{app.board}")
    return findings


def check_libraries(app_dir: Path) -> list[Finding]:
    """Exactly the two bundled libraries, each passing its tool's offline check."""
    root = app_dir.joinpath(*LIB_DIR.split("/"))
    present = sorted(path.name for path in root.iterdir()) if root.is_dir() else []
    expected = sorted(spec.name for spec in LIBRARIES)
    if present != expected:
        return [Finding(f"{LIB_DIR}/ holds {present}, expected {expected}")]
    findings: list[Finding] = []
    for spec in LIBRARIES:
        try:
            libbundle.check(spec, root / spec.name, unpatched=False)
        except (libbundle.VerifyError, OSError, UnicodeDecodeError) as exc:
            findings.append(Finding(f"{LIB_DIR}/{spec.name}: {exc}"))
    return findings


def check_station_toml(app: AppSpec, app_dir: Path, ports: list[int]) -> list[Finding]:
    """The packaged ground configuration: secrets empty, the web port published by app.yaml."""
    if app.station_toml is None:
        return []
    path = app_dir.joinpath(*app.station_toml.split("/"))
    data = path.read_bytes()
    secrets, errors = station_secret_findings(app.station_toml, data)
    findings = [Finding(text, secret=True) for text in secrets] + [Finding(text) for text in errors]
    if not errors:
        gds = tomllib.loads(data.decode("utf-8")).get("gds", {})
        port = gds.get("http_port", 8080) if isinstance(gds, dict) else 8080
        if port not in ports:
            findings.append(Finding(f"{app.station_toml}: [gds] http_port {port} is not published by app.yaml "
                                    f"ports {ports} (the dashboard would be unreachable from the LAN)"))
    return findings


def check_python(app_dir: Path) -> list[Finding]:
    """Every packaged Python file compiles (catches truncated or conflicted files)."""
    findings: list[Finding] = []
    for path in sorted(app_dir.rglob("*.py")):
        rel = path.relative_to(app_dir).as_posix()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                compile(path.read_bytes(), rel, "exec", dont_inherit=True)
            except (SyntaxError, ValueError) as exc:
                findings.append(Finding(f"{rel}: does not compile: {exc}"))
    return findings


def find_compiler(arg: str | None) -> Compiler:
    """The compiler for --build (see the module docstring)."""
    wanted = arg or os.environ.get("VGQ_ACOMPILE") or ""
    if wanted:
        found = shutil.which(wanted) or (wanted if Path(wanted).is_file() else None)
        if found is None:
            raise UsageError(f"compiler {wanted!r} not found")
        return Compiler(found, wrapper=Path(found).name not in ("arduino-cli", "arduino-cli.exe"))
    cli = shutil.which("arduino-cli")
    if cli is None:
        raise UsageError("--build needs a compiler: --compiler PATH (an acompile.sh-style wrapper or "
                         "arduino-cli), $VGQ_ACOMPILE, or arduino-cli on PATH")
    return Compiler(cli, wrapper=False)


def compile_sketch(compiler: Compiler, board: str, sketch: Path, out: Path) -> tuple[list[Finding], list[str]]:
    """Compiles an unpacked sketch; returns the findings and the memory-use lines of the log."""
    out.mkdir(parents=True, exist_ok=True)
    if compiler.wrapper:
        cmd = [compiler.path, board, str(sketch), "--out", str(out)]
    else:
        cmd = [compiler.path, "compile", "--profile", "default", "-v", "--build-path", str(out), str(sketch)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=COMPILE_TIMEOUT_S,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [Finding(f"compile ({board}): {exc}")], []
    log = proc.stdout + "\n" + proc.stderr
    log_file = out / "compile.log"               # acompile.sh keeps the verbose log there
    if log_file.is_file():
        log += "\n" + log_file.read_text(encoding="utf-8", errors="replace")
    lines = log.splitlines()
    memory = list(dict.fromkeys(line.strip() for line in lines
                                if line.startswith(("Sketch uses", "Global variables use"))))
    findings: list[Finding] = []
    if proc.returncode != 0:
        errors = [line for line in lines if "error" in line.lower()][:12] or lines[-12:]
        findings.append(Finding(f"compile ({board}): exit status {proc.returncode}\n    " + "\n    ".join(errors)))
    folders = {str(sketch / "libraries" / BRIDGE.name), os.path.realpath(sketch / "libraries" / BRIDGE.name)}
    pattern = "|".join(re.escape(folder) for folder in sorted(folders))
    used = re.compile(rf"^Using library {re.escape(BRIDGE.name)} at version {re.escape(BRIDGE.tag)} "
                      rf"in folder: (?:{pattern})\s*$", re.MULTILINE)
    match = used.search(log)
    if match is None:
        seen = [line.strip() for line in lines if line.startswith(f"Using library {BRIDGE.name} ")]
        findings.append(Finding(f"compile ({board}): the log does not show the bundled {BRIDGE.name} "
                                f"{BRIDGE.tag} from the package (seen: {seen or 'no such line'})"))
    else:
        memory.append(match.group(0).strip())
    return findings, memory


def verify_package(app: AppSpec, data: bytes, work: Path,
                   compiler: Compiler | None) -> tuple[list[Finding], list[str]]:
    """Every check of the module docstring on one package; returns the findings and notes."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        return [Finding(f"not a zip archive: {exc}")], []
    with archive:
        findings, safe = check_entries(app, archive)
        if not safe:
            return findings, []
        names = archive.namelist()
        contents, unreadable = read_contents(archive)
    findings += unreadable
    findings += check_names_and_content(app, names, contents)
    extract(names, contents, work)
    app_dir = work / app.folder
    findings.extend(Finding(f"{req}: missing") for req in app.required
                    if not app_dir.joinpath(*req.split("/")).is_file())
    if findings:
        return findings, []                   # the layout is broken: the deeper checks would only cascade
    try:
        app_findings, ports = check_app_yaml((app_dir / "app.yaml").read_bytes().decode("utf-8"))
    except UnicodeDecodeError as exc:
        app_findings, ports = [Finding(f"app.yaml: not UTF-8 ({exc})")], []
    findings += app_findings
    findings += check_sketch_yaml(app, app_dir / "sketch")
    findings += check_libraries(app_dir)
    findings += check_station_toml(app, app_dir, ports)
    findings += check_python(app_dir)
    notes: list[str] = []
    if compiler is not None and not findings:
        compile_findings, notes = compile_sketch(compiler, app.board, app_dir / "sketch", work.parent / "build")
        findings += compile_findings
    return findings, notes


def summarise(app: AppSpec, data: bytes) -> str:
    """Size, SHA-256 and file counts per top-level folder of one package."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [info.filename for info in archive.infolist() if not info.is_dir()]
    counts: dict[str, int] = {}
    for name in names:
        rel = name[len(app.folder) + 1:]
        parts = rel.split("/")
        if rel.startswith(LIB_DIR + "/"):
            key = "/".join(parts[:3]) + "/"
        elif len(parts) == 1:
            key = "top level"
        else:
            key = parts[0] + "/"
        counts[key] = counts.get(key, 0) + 1
    order = sorted(counts, key=lambda key: (key != "top level", entry_sort_key(key)))
    detail = ", ".join(f"{key} {counts[key]}" for key in order)
    return (f"{app.zip_name}: {len(data)} octets, sha256 {hashlib.sha256(data).hexdigest()}\n"
            f"  {len(names)} files: {detail}")


def verify_all(packages: dict[str, bytes], work: Path, compiler: Compiler | None) -> int:
    """Verifies every package; prints the findings. Returns the exit status (0, 1 or 4)."""
    status = EXIT_OK
    for app in APPS:
        data = packages.get(app.zip_name)
        if data is None:
            continue
        findings, notes = verify_package(app, data, work / app.zip_name / "unpacked", compiler)
        if findings:
            print(f"FAIL {app.zip_name}:")
            for finding in findings:
                print(f"  {'SECRET ' if finding.secret else ''}{finding.text}")
            status = EXIT_SECRET if any(f.secret for f in findings) or status == EXIT_SECRET else EXIT_FAIL
        else:
            built = " and compiled" if compiler is not None else ""
            print(f"ok   {app.zip_name}: layout, attributes, libraries and configuration verified{built}")
            for note in notes:
                print(f"     {note}")
    return status


def check_sums(out: Path, packages: dict[str, bytes]) -> list[Finding]:
    """SHA256SUMS lists exactly the packages, with their digests."""
    path = out / SUMS_NAME
    try:
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        return [Finding(f"{SUMS_NAME}: {exc}")]
    listed: dict[str, str] = {}
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](\S+)", line)
        if match is None:
            findings.append(Finding(f"{SUMS_NAME} line {number}: not '<sha256>  <file>'"))
            continue
        listed[match.group(2)] = match.group(1)
    if sorted(listed) != sorted(app.zip_name for app in APPS):
        findings.append(Finding(f"{SUMS_NAME} lists {sorted(listed)}, expected the two packages"))
    for name, data in packages.items():
        if name in listed and listed[name] != hashlib.sha256(data).hexdigest():
            findings.append(Finding(f"{SUMS_NAME}: {name} has a different SHA-256"))
    return findings


# =============================================================================================
# Commands
# =============================================================================================
def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, FILE_MODE)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def build(repo: Path, out: Path, libs_from: Path | None, compiler: Compiler | None) -> int:
    """Builds, verifies and writes both packages and SHA256SUMS (see the module docstring)."""
    require_work_tree(repo)
    hits = secret_files_on_disk(repo)
    if hits:
        raise SecretError("\n".join(f"  {hit}" for hit in hits) + "\nMove these files out of the App folders "
                          "(keep them safe: they hold link keys or tokens) or package from a clean clone. "
                          "Nothing was written.")
    sources: dict[str, dict[str, bytes]] = {}
    secrets: list[str] = []
    errors: list[str] = []
    for app in APPS:
        files, excluded, app_secrets, app_errors = collect_app_files(repo, app)
        sources[app.folder] = files
        secrets += app_secrets
        errors += app_errors
        for rel, reason in sorted(excluded.items()):
            print(f"excluded {app.folder}/{rel} ({reason})")
    if secrets:
        raise SecretError("\n".join(f"  {text}" for text in secrets) + "\nNothing was written.")
    if errors:
        raise CheckError("packaging inputs rejected:\n" + "\n".join(f"  {text}" for text in errors))
    epoch, origin = source_date_epoch(repo)
    date_time = zip_date_time(epoch)
    print(f"timestamp {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(epoch))} ({origin}); "
          f"zlib {zlib.ZLIB_RUNTIME_VERSION}, deflate level {COMPRESS_LEVEL}")
    dirty = uncommitted_count(repo)
    if dirty:
        print(f"note: {dirty} files under the App folders differ from HEAD; the packages hold the working "
              "tree (for a release, commit first and build again)")
    with tempfile.TemporaryDirectory(prefix="package-apps-") as tmp:
        work = Path(tmp)
        libs = acquire_libraries(libs_from, work / "libs")
        for lib in libs:
            print(lib.note)
        packages: dict[str, bytes] = {}
        for app in APPS:
            files = dict(sources[app.folder])
            for lib in libs:
                files.update((f"{LIB_DIR}/{lib.spec.name}/{rel}", data) for rel, data in lib.files.items())
            packages[app.zip_name] = build_zip(app.folder, files, date_time)
        status = verify_all(packages, work / "verify", compiler)
    if status != EXIT_OK:
        print(f"nothing written to {out}")
        return status
    out.mkdir(parents=True, exist_ok=True)
    for name, data in sorted(packages.items()):
        _atomic_write(out / name, data)
    sums = "".join(f"{hashlib.sha256(packages[name]).hexdigest()}  {name}\n" for name in sorted(packages))
    _atomic_write(out / SUMS_NAME, sums.encode("ascii"))
    for app in APPS:
        print(summarise(app, packages[app.zip_name]))
    print(f"wrote {', '.join(sorted(packages))} and {SUMS_NAME} to {out}")
    return EXIT_OK


def verify_dist(out: Path, compiler: Compiler | None) -> int:
    """Verifies the packages and SHA256SUMS already in `out`."""
    packages: dict[str, bytes] = {}
    status = EXIT_OK
    for app in APPS:
        path = out / app.zip_name
        if not path.is_file():
            print(f"FAIL {app.zip_name}: missing from {out}")
            status = EXIT_FAIL
            continue
        packages[app.zip_name] = path.read_bytes()
    sums = check_sums(out, packages)
    if sums:
        print(f"FAIL {SUMS_NAME}:")
        for finding in sums:
            print(f"  {finding.text}")
        status = EXIT_FAIL
    with tempfile.TemporaryDirectory(prefix="package-apps-verify-") as tmp:
        result = verify_all(packages, Path(tmp), compiler)
    for app in APPS:
        if app.zip_name in packages:
            print(summarise(app, packages[app.zip_name]))
    if EXIT_SECRET in (status, result):
        return EXIT_SECRET
    return EXIT_FAIL if EXIT_FAIL in (status, result) else EXIT_OK


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="package_apps.py",
        description="Build (default) or verify the two Arduino App Lab packages of VGQ-1 / DSS-Q1: "
                    "dist/vgq1-spacecraft-unoq.zip and dist/dssq-groundstation-ventunoq.zip.",
        epilog="Exit status: 0 success; 1 a check failed; 2 usage or environment error; 3 a library could "
               "not be fetched; 4 refused: secret material in an App folder or a package.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, metavar="DIR",
                        help="where the packages and SHA256SUMS are written or verified (default: dist/)")
    parser.add_argument("--libs-from", type=Path, metavar="DIR",
                        help="bundle the RadioLib and Arduino_RouterBridge copies in DIR (written by "
                             "tools/radiolib.py / tools/routerbridge.py fetch) instead of fetching them; "
                             "they pass the offline check first")
    parser.add_argument("--verify", action="store_true",
                        help="verify the packages already in --out instead of building them")
    parser.add_argument("--build", action="store_true",
                        help="also compile each unpacked sketch with the real Arduino toolchain and require "
                             "the bundled patched Arduino_RouterBridge in the compile log")
    parser.add_argument("--compiler", metavar="PATH",
                        help="for --build: an acompile.sh-style wrapper ('PATH <board> <sketch> --out <dir>') "
                             "or arduino-cli (default: $VGQ_ACOMPILE, else arduino-cli from PATH)")
    args = parser.parse_args(argv)
    if args.verify and args.libs_from is not None:
        parser.error("--libs-from applies to a build, not to --verify")
    if args.compiler is not None and not args.build:
        parser.error("--compiler needs --build")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        compiler = find_compiler(args.compiler) if args.build else None
        if args.verify:
            return verify_dist(args.out, compiler)
        return build(REPO, args.out, args.libs_from, compiler)
    except SecretError as exc:
        print(f"package_apps.py: REFUSED - secret material must never enter a package:\n{exc}", file=sys.stderr)
        return EXIT_SECRET
    except UsageError as exc:
        print(f"package_apps.py: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except libbundle.UpstreamError as exc:
        print(f"package_apps.py: library not available: {exc} (or use --libs-from DIR)", file=sys.stderr)
        return EXIT_UPSTREAM
    except (CheckError, libbundle.VerifyError) as exc:
        print(f"package_apps.py: {exc}", file=sys.stderr)
        return EXIT_FAIL
    except OSError as exc:
        print(f"package_apps.py: {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
