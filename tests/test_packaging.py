"""tools/package_apps.py: the two Arduino App Lab packages (contract v3 sections 18, 23, 30, 41).

The packager runs in a throw-away git repository holding a copy of the files git tracks in
the two App folders plus the packaging tools, committed with a fixed date, so every test is
independent of the developer's working tree, keys and git configuration.

The bundled libraries come, in this order, from $VGQ_BUNDLED_LIBS (a folder with RadioLib/
and Arduino_RouterBridge/ written by tools/radiolib.py / tools/routerbridge.py fetch), from
the pytest cache (.pytest_cache, re-checked offline on every run), or from ONE fetch into
that cache (GitHub, or the mirrors $VGQ_RADIOLIB_REPO / $VGQ_ROUTERBRIDGE_REPO). Without
any of them the tests that need the libraries are skipped; the refusal and rule tests never
need them.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
_spec = importlib.util.spec_from_file_location("package_apps", TOOLS / "package_apps.py")
assert _spec is not None and _spec.loader is not None
pa = importlib.util.module_from_spec(_spec)
sys.modules["package_apps"] = pa                # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(pa)

APP_FOLDERS = tuple(app.folder for app in pa.APPS)
TOOL_FILES = ("package_apps.py", "libbundle.py", "radiolib.py", "routerbridge.py",
              *(spec.patch_file.name for spec in pa.LIBRARIES))
COMMIT_DATE = "2026-01-02T03:04:06+00:00"      # even seconds: the zip resolution is 2 s
COMMIT_STAMP = (2026, 1, 2, 3, 4, 6)
MIRROR_ENV = {"RadioLib": "VGQ_RADIOLIB_REPO", "Arduino_RouterBridge": "VGQ_ROUTERBRIDGE_REPO"}
GIT_ENV = {
    "GIT_AUTHOR_NAME": "packaging test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "packaging test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_AUTHOR_DATE": COMMIT_DATE, "GIT_COMMITTER_DATE": COMMIT_DATE,
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
}
NO_LIBS = "no-such-folder"                      # --libs-from that must never be reached


# =============================================================================================
# Helpers and fixtures
# =============================================================================================
def git(repo: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    proc = subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout


def commit_all(repo: Path, message: str = "change") -> None:
    git(repo, "add", "-A", "--force")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)


def run_packager(repo: Path, *args: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **GIT_ENV, **extra_env}
    env.pop("SOURCE_DATE_EPOCH", None)
    return subprocess.run([sys.executable, str(repo / "tools" / "package_apps.py"), *args], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=900, check=False)


def zip_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def rewrite_zip(path: Path, mutate: Callable[[list[tuple[zipfile.ZipInfo, bytes]]], None]) -> None:
    """Rewrites a package in place after `mutate` changed its (ZipInfo, content) list, and updates
    SHA256SUMS next to it, so only the check aimed at can fail."""
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    mutate(entries)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for info, data in entries:
            archive.writestr(info, data, compresslevel=1)      # the level is not checked
    path.write_bytes(buffer.getvalue())
    sums = path.parent / pa.SUMS_NAME
    lines = [line for line in sums.read_text().splitlines() if not line.endswith(f"  {path.name}")]
    lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    sums.write_text("\n".join(sorted(lines, key=lambda line: line[66:])) + "\n")


def new_entry(name: str, data: bytes, like: zipfile.ZipInfo) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, like.date_time)
    info.create_system = pa.UNIX
    info.external_attr = like.external_attr
    info.compress_type = zipfile.ZIP_DEFLATED
    return info, data


@pytest.fixture(scope="session")
def template_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A git repository with the tracked App files and the packaging tools, one commit."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    listing = subprocess.run(["git", "ls-files", "-z", "--", *APP_FOLDERS], cwd=ROOT, capture_output=True,
                             check=False)
    if listing.returncode != 0:
        pytest.skip("the repository is not a git checkout (the packager builds from tracked files)")
    repo = tmp_path_factory.mktemp("template") / "repo"
    repo.mkdir()
    for rel in filter(None, listing.stdout.decode().split("\0")):
        source = ROOT / rel
        if source.is_file() and not source.is_symlink():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, repo / rel)
    (repo / "tools").mkdir()
    for name in TOOL_FILES:
        shutil.copyfile(TOOLS / name, repo / "tools" / name)
    git(repo, "-c", "init.defaultBranch=main", "init", "-q")
    commit_all(repo, "snapshot")
    return repo


@pytest.fixture
def repo(template_repo: Path, tmp_path: Path) -> Path:
    """A private copy of the template repository that a test may change."""
    copy = tmp_path / "repo"
    shutil.copytree(template_repo, copy, symlinks=True)
    return copy


@pytest.fixture(scope="session")
def bundled_libs(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A folder with verified RadioLib and Arduino_RouterBridge copies (see the module docstring)."""
    given = os.environ.get("VGQ_BUNDLED_LIBS")
    if given:
        for spec in pa.LIBRARIES:
            try:
                pa.libbundle.check(spec, Path(given) / spec.name, unpatched=False)
            except (pa.libbundle.VerifyError, OSError) as exc:
                pytest.fail(f"VGQ_BUNDLED_LIBS={given}: {exc}")
        return Path(given)
    cache = getattr(request.config, "cache", None)
    root = Path(cache.mkdir("vgq_bundled_libs")) if cache is not None else tmp_path_factory.mktemp("libs")
    for spec in pa.LIBRARIES:
        dest = root / spec.name
        try:
            pa.libbundle.check(spec, dest, unpatched=False)
            continue                            # fetched by an earlier run, still exact
        except (pa.libbundle.VerifyError, OSError):
            pass
        if dest.exists():
            shutil.rmtree(dest)
        mirror = os.environ.get(MIRROR_ENV[spec.name]) or spec.upstream_url
        try:
            pa.libbundle.fetch(spec, dest, mirror, unpatched=False)
        except pa.libbundle.UpstreamError as exc:
            pytest.skip(f"{spec.name}: no local copy and the repository is not reachable ({exc})")
    return root


@pytest.fixture(scope="session")
def built(template_repo: Path, bundled_libs: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The two packages and SHA256SUMS built once from the template repository."""
    out = tmp_path_factory.mktemp("dist")
    proc = run_packager(template_repo, "--out", str(out), "--libs-from", str(bundled_libs))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return out


# =============================================================================================
# Rules (no repository, no libraries)
# =============================================================================================
@pytest.mark.parametrize(("rel", "reason"), [
    ("python/main.py", None),
    ("sketch/src/fsw/flight.cpp", None),
    ("host/install_station.sh", None),
    ("python/dssq/display/dashboard.html", None),
    ("data/boot_count.txt", "runtime data"),
    ("python/egse_data/boot_count.txt", "runtime data"),
    ("python/archive/gds.sqlite", "runtime data"),
    ("python/dssq/__pycache__/gds.cpython-313.pyc", "Python cache"),
    ("python/stale.pyc", "Python cache"),
    ("tests/test_x.py", "test"),
    ("python/test_local.py", "test"),
    ("python/conftest.py", "test"),
    ("sketch/test/data.h", "test"),
    ("dist/old.zip", "build output"),
    (".cache/sketch/sketch.ino.o", "hidden file or folder"),
    ("python/.env", "hidden file or folder"),
    ("release.yaml", "App Lab release manifest"),
])
def test_exclusion_rules(rel: str, reason: str | None) -> None:
    assert pa.exclusion_reason(rel) == reason


@pytest.mark.parametrize(("name", "secret"), [
    ("sdls_key.h", True), ("sdls_key.hex", True), ("SDLS_KEY.HEX", True), ("old_sdls_key.hex.bak", True),
    ("http_token", True), ("station.pem", True), ("radio.key", True),
    ("sdls.h", False), ("sdls.cpp", False), ("station.toml", False), ("keyboard.cpp", False),
])
def test_secret_names(name: str, secret: bool) -> None:
    assert pa.is_secret_name(name) is secret


def test_secret_content() -> None:
    assert pa.secret_content_reason(b"00" * 32 + b"\n") is not None                       # sdls_key.hex
    assert pa.secret_content_reason(b"static const uint8_t kVgqSdlsKey[32] = {0x01};") is not None
    assert pa.secret_content_reason(b"sdls_tc_.configure(VGQ_SDLS_TC, kVgqSdlsKey, sizeof kVgqSdlsKey);") is None
    assert pa.secret_content_reason(b"pristine-tree-sha256: " + b"ab" * 32 + b"\n") is None


def test_requirements_and_libraries_are_input_errors() -> None:
    assert "contract 23" in (pa.input_error("python/requirements.txt") or "")
    assert pa.input_error("sketch/libraries/RadioLib/src/RadioLib.h") is not None
    assert pa.input_error("python/main.py") is None


def test_entry_order_puts_a_folder_before_its_contents() -> None:
    names = ["a/b", "a-b/x", "a/", "a/b/c", "a/b/", "README.md"]
    assert sorted(names, key=pa.entry_sort_key) == ["README.md", "a/", "a/b", "a/b/", "a/b/c", "a-b/x"]


def test_zip_timestamps() -> None:
    assert pa.zip_date_time(0) == (1980, 1, 1, 0, 0, 0)                     # clamped to the zip epoch
    assert pa.zip_date_time(1767323047) == COMMIT_STAMP                    # odd second rounded down
    with pytest.raises(pa.UsageError):
        pa.zip_date_time(pa.DOS_EPOCH_MAX + 2)


def test_app_descriptors_of_both_apps() -> None:
    for app, ports in zip(pa.APPS, ([], [8080]), strict=True):
        findings, found = pa.check_app_yaml((ROOT / app.folder / "app.yaml").read_text(encoding="utf-8"))
        assert findings == [] and found == ports


@pytest.mark.parametrize(("text", "expected"), [
    ("name: A\nicon: x\ndescription: d\n", "single emoji"),
    ("name: A\ndescription: d\n", "icon must be"),
    ("name: A\nicon: \U0001F4E1\ndescription: d\nbricks:\n  - arduino:web_ui\n", "no Bricks"),
    ("name: A\nicon: \U0001F4E1\ndescription: d\nports: [70000]\n", "1..65535"),
    ("name: A\nicon: \U0001F4E1\ndescription: d\nnested:\n  key: value\n", "not a top-level"),
])
def test_app_descriptor_findings(text: str, expected: str) -> None:
    findings, _ = pa.check_app_yaml(text)
    assert any(expected in finding.text for finding in findings), findings


# =============================================================================================
# Refusals and rejected inputs (stop before any library is fetched)
# =============================================================================================
@pytest.mark.parametrize("planted", [
    "vgq1-spacecraft/sketch/src/sdls_key.h",
    "dssq-groundstation/data/sdls_key.hex",
    "dssq-groundstation/python/config/sdls_key.hex",
    "vgq1-spacecraft/data/backup/SDLS_KEY.HEX.old",
    "dssq-groundstation/data/http_token",
])
def test_refuses_a_key_file_anywhere_in_an_app_folder(repo: Path, tmp_path: Path, planted: str) -> None:
    path = repo / planted
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("00" * 32 + "\n")                                  # untracked: still refused
    out = tmp_path / "dist"
    proc = run_packager(repo, "--out", str(out), "--libs-from", NO_LIBS)
    assert proc.returncode == pa.EXIT_SECRET, proc.stdout + proc.stderr
    assert "REFUSED" in proc.stderr and planted in proc.stderr
    assert not out.exists()


@pytest.mark.parametrize(("rel", "content"), [
    ("dssq-groundstation/python/config/link.txt", b"0123456789abcdef" * 4 + b"\n"),
    ("vgq1-spacecraft/sketch/src/keys_copy.h", b"static const uint8_t kVgqSdlsKey[32] = {0x00};\n"),
])
def test_refuses_key_material_in_a_packaged_file(repo: Path, tmp_path: Path, rel: str, content: bytes) -> None:
    (repo / rel).write_bytes(content)
    commit_all(repo)
    proc = run_packager(repo, "--out", str(tmp_path / "dist"), "--libs-from", NO_LIBS)
    assert proc.returncode == pa.EXIT_SECRET, proc.stdout + proc.stderr
    assert rel in proc.stderr


@pytest.mark.parametrize(("key", "line"), [("http_token", 'http_token = "abc123"'),
                                           ("auth_token", 'auth_token = "second-operator"')])
def test_refuses_a_token_in_the_packaged_station_toml(repo: Path, tmp_path: Path, key: str, line: str) -> None:
    toml = repo / "dssq-groundstation" / "python" / "config" / "station.toml"
    text = toml.read_text(encoding="utf-8")
    assert f'{key} = ""' in text
    toml.write_text(text.replace(f'{key} = ""', line, 1), encoding="utf-8")   # uncommitted edit: packaged
    proc = run_packager(repo, "--out", str(tmp_path / "dist"), "--libs-from", NO_LIBS)
    assert proc.returncode == pa.EXIT_SECRET, proc.stdout + proc.stderr
    assert key in proc.stderr


@pytest.mark.parametrize(("rel", "expected"), [
    ("dssq-groundstation/python/requirements.txt", "contract 23"),
    ("vgq1-spacecraft/sketch/libraries/RadioLib/src/RadioLib.h", "sketch/libraries/ must not be tracked"),
])
def test_rejects_inputs(repo: Path, tmp_path: Path, rel: str, expected: str) -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text("x\n")
    commit_all(repo)
    proc = run_packager(repo, "--out", str(tmp_path / "dist"), "--libs-from", NO_LIBS)
    assert proc.returncode == pa.EXIT_FAIL, proc.stdout + proc.stderr
    assert expected in proc.stderr


def test_needs_a_git_work_tree(tmp_path: Path) -> None:
    (tmp_path / "tools").mkdir()
    for name in TOOL_FILES:
        shutil.copyfile(TOOLS / name, tmp_path / "tools" / name)
    proc = run_packager(tmp_path, "--out", str(tmp_path / "dist"), "--libs-from", NO_LIBS,
                        GIT_CEILING_DIRECTORIES=str(tmp_path.parent))      # never an enclosing repository
    assert proc.returncode == pa.EXIT_USAGE, proc.stdout + proc.stderr
    assert "not a git repository" in proc.stderr


@pytest.mark.parametrize("args", [["--verify", "--libs-from", "x"], ["--compiler", "x"]])
def test_usage_errors(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        pa.main(args)
    assert exc.value.code == pa.EXIT_USAGE


# =============================================================================================
# Building (needs the libraries)
# =============================================================================================
def test_packages_are_reproducible(template_repo: Path, bundled_libs: Path, built: Path, tmp_path: Path) -> None:
    again = tmp_path / "again"
    proc = run_packager(template_repo, "--out", str(again), "--libs-from", str(bundled_libs))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for name in (*(app.zip_name for app in pa.APPS), pa.SUMS_NAME):
        assert (again / name).read_bytes() == (built / name).read_bytes(), name
    listed = dict(reversed(line.split("  ")) for line in (built / pa.SUMS_NAME).read_text().splitlines())
    for app in pa.APPS:
        assert listed[app.zip_name] == hashlib.sha256((built / app.zip_name).read_bytes()).hexdigest()


def test_timestamp_source(template_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    epoch, origin = pa.source_date_epoch(template_repo)
    assert epoch == 1767323046 and "newest commit that touched a packaging input" in origin
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    assert pa.source_date_epoch(template_repo) == (1700000000, "SOURCE_DATE_EPOCH")
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "yesterday")
    with pytest.raises(pa.UsageError):
        pa.source_date_epoch(template_repo)


def test_build_zip_is_deterministic_and_normalised() -> None:
    files = {"python/main.py": b"print()\n", "host/run.sh": b"#!/bin/sh\n", "app.yaml": b"name: x\n"}
    stamp = pa.zip_date_time(1700000000)
    data = pa.build_zip("app", files, stamp)
    assert data == pa.build_zip("app", dict(reversed(files.items())), stamp)    # input order is irrelevant
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
    assert [info.filename for info in infos] == ["app/", "app/app.yaml", "app/host/", "app/host/run.sh",
                                                "app/python/", "app/python/main.py"]
    assert {info.date_time for info in infos} == {time.gmtime(1700000000)[:6]}
    assert [info.external_attr >> 16 for info in infos] == [0o40755, 0o100644, 0o40755, 0o100755, 0o40755,
                                                           0o100644]


def test_package_layout(template_repo: Path, built: Path) -> None:
    for app in pa.APPS:
        top = app.folder
        with zipfile.ZipFile(built / app.zip_name) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            assert names[0] == f"{top}/" and all(name.startswith(f"{top}/") for name in names)
            assert names == sorted(names, key=pa.entry_sort_key)
            assert {info.date_time for info in infos} == {COMMIT_STAMP}         # the commit date
            for info in infos:
                assert info.create_system == pa.UNIX and not info.extra and not info.comment
                mode = info.external_attr >> 16
                if info.is_dir():
                    assert mode == 0o40755
                else:
                    assert mode == (0o100755 if info.filename.endswith(".sh") else 0o100644), info.filename
            for required in app.required:
                assert f"{top}/{required}" in names
            for spec in pa.LIBRARIES:
                lib = f"{top}/sketch/libraries/{spec.name}/"
                for name in ("PROVENANCE.txt", "library.properties", *spec.bundle_files):
                    assert lib + name in names
            sketch_yaml = archive.read(f"{top}/sketch/sketch.yaml").decode()
            assert "- dir: libraries/RadioLib" in sketch_yaml
            assert "- dir: libraries/Arduino_RouterBridge" in sketch_yaml
            # Every tracked, non-excluded App file is packaged unchanged.
            packaged = {name[len(top) + 1:] for name in names if not name.endswith("/")}
            tracked = git(template_repo, "ls-files", "--", top).split("\n")
            for path in filter(None, tracked):
                rel = path[len(top) + 1:]
                if pa.exclusion_reason(rel) is None:
                    assert archive.read(path) == (template_repo / path).read_bytes(), path
                    packaged.discard(rel)
            assert all(rel.startswith("sketch/libraries/") for rel in packaged), packaged
        assert not any(name.split("/")[-1] == "requirements.txt" for name in names)
        assert not any("__pycache__" in name or name.startswith(f"{top}/data/") for name in names)
    host = zip_names(built / "dssq-groundstation-ventunoq.zip")
    assert "dssq-groundstation/host/install_station.sh" in host


def test_excludes_tests_caches_data_and_untracked_files(repo: Path) -> None:
    tracked_junk = [
        "vgq1-spacecraft/tests/test_egse.py", "vgq1-spacecraft/python/test_local.py",
        "vgq1-spacecraft/python/conftest.py", "dssq-groundstation/python/dssq/__pycache__/gds.cpython-313.pyc",
        "dssq-groundstation/python/dssq/stale.pyc", "dssq-groundstation/data/archive/gds.sqlite",
        "vgq1-spacecraft/data/cadu_20260101.bin", "vgq1-spacecraft/python/egse_data/boot_count.txt",
        "dssq-groundstation/python/archive/20260101/events.log", "vgq1-spacecraft/dist/old.zip",
        "vgq1-spacecraft/.cache/sketch/sketch.ino.o", "dssq-groundstation/.vscode/settings.json",
        "vgq1-spacecraft/release.yaml",
    ]
    untracked = ["vgq1-spacecraft/python/stray.py", "dssq-groundstation/python/dssq/notes.txt"]
    for rel in tracked_junk:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("junk\n")
    commit_all(repo)
    for rel in untracked:
        (repo / rel).write_text("stray\n")
    tracked = set(git(repo, "ls-files").splitlines())
    for app in pa.APPS:
        files, excluded, secrets, errors = pa.collect_app_files(repo, app)
        assert secrets == [] and errors == []
        prefix = app.folder + "/"
        assert {prefix + rel for rel in excluded} == {rel for rel in tracked_junk if rel.startswith(prefix)}
        assert not {prefix + rel for rel in files} & {*tracked_junk, *untracked}
        assert {prefix + rel for rel in files} <= tracked


def test_crlf_checkout_is_packaged_with_lf(repo: Path) -> None:
    rel = "python/main.py"
    path = repo / "dssq-groundstation" / rel
    original = path.read_bytes()
    git(repo, "config", "core.autocrlf", "true")
    path.unlink()
    git(repo, "checkout", "--", f"dssq-groundstation/{rel}")              # the checkout writes CRLF
    assert b"\r\n" in path.read_bytes() and b"\r\n" not in original
    files, _, _, errors = pa.collect_app_files(repo, pa.APPS[1])
    assert errors == [] and files[rel] == original


# =============================================================================================
# Verifying (needs the libraries)
# =============================================================================================
def test_verify_accepts_the_built_packages(built: Path) -> None:
    proc = run_packager(ROOT, "--verify", "--out", str(built))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("ok   ") == 2


def _patch_radiolib(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> None:
    for i, (info, data) in enumerate(entries):
        if info.filename.endswith("sketch/libraries/RadioLib/src/BuildOptUser.h"):
            entries[i] = (info, data + b"// changed\n")


def _add_requirements(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> None:
    main = next(info for info, _ in entries if info.filename.endswith("/python/main.py"))
    entries.append(new_entry(main.filename.replace("main.py", "requirements.txt"), b"pyserial\n", main))
    entries.sort(key=lambda entry: pa.entry_sort_key(entry[0].filename))


def _add_key(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> None:
    yaml = next(info for info, _ in entries if info.filename.endswith("/sketch/sketch.yaml"))
    entries.append(new_entry(yaml.filename.replace("sketch.yaml", "src/sdls_key.h"), b"// key\n", yaml))
    entries.sort(key=lambda entry: pa.entry_sort_key(entry[0].filename))


def _add_data(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> None:
    yaml = next(info for info, _ in entries if info.filename.endswith("/app.yaml"))
    top = yaml.filename.split("/")[0]
    folder = zipfile.ZipInfo(f"{top}/data/", yaml.date_time)
    folder.create_system, folder.external_attr = pa.UNIX, (0o40755 << 16) | 0x10
    entries.extend([(folder, b""), new_entry(f"{top}/data/boot_count.txt", b"7\n", yaml)])
    entries.sort(key=lambda entry: pa.entry_sort_key(entry[0].filename))


def _private_mode(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> None:
    for info, _ in entries:
        if info.filename.endswith("/app.yaml"):
            info.external_attr = 0o100600 << 16


def _drop_bridge_dir(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> None:
    for i, (info, data) in enumerate(entries):
        if info.filename.endswith("/sketch/sketch.yaml"):
            entries[i] = (info, data.replace(b"- dir: libraries/Arduino_RouterBridge", b""))


def tampered(built: Path, mutate: Callable[[list[tuple[zipfile.ZipInfo, bytes]]], None]) -> bytes:
    """The spacecraft package of `built` after `mutate`, as bytes."""
    with zipfile.ZipFile(built / "vgq1-spacecraft-unoq.zip") as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    mutate(entries)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for info, data in entries:
            archive.writestr(info, data, compresslevel=1)      # the level is not checked
    return buffer.getvalue()


@pytest.mark.parametrize(("mutate", "secret", "expected"), [
    (_patch_radiolib, False, "sketch/libraries/RadioLib"),
    (_add_requirements, False, "requirements.txt"),
    (_add_key, True, "a key or token file"),
    (_add_data, False, "data/boot_count.txt: must not be packaged (runtime data)"),
    (_private_mode, False, "attributes"),
    (_drop_bridge_dir, False, "- dir: libraries/Arduino_RouterBridge"),
])
def test_verify_rejects_a_changed_package(built: Path, mutate: Callable[..., None], secret: bool,
                                          expected: str) -> None:
    with tempfile.TemporaryDirectory(prefix="unpacked-") as work:     # 13 MB: not kept after the test
        findings, _ = pa.verify_package(pa.APPS[0], tampered(built, mutate), Path(work), None)
    hits = [finding for finding in findings if expected in finding.text]
    assert hits, findings
    assert any(finding.secret for finding in findings) is secret


def test_verify_exit_status_for_key_material_is_4(built: Path, tmp_path: Path) -> None:
    out = tmp_path / "dist"
    shutil.copytree(built, out)
    rewrite_zip(out / "vgq1-spacecraft-unoq.zip", _add_key)
    proc = run_packager(ROOT, "--verify", "--out", str(out))
    assert proc.returncode == pa.EXIT_SECRET, proc.stdout + proc.stderr
    assert "SECRET" in proc.stdout and "ok   dssq-groundstation-ventunoq.zip" in proc.stdout


def test_verify_rejects_stale_sums_and_missing_packages(built: Path, tmp_path: Path) -> None:
    out = tmp_path / "dist"
    shutil.copytree(built, out)
    sums = out / pa.SUMS_NAME
    lines = sums.read_text().splitlines()
    lines = [("0" * 64 + line[64:]) if line.endswith("  vgq1-spacecraft-unoq.zip") else line for line in lines]
    sums.write_text("\n".join(lines) + "\n")
    (out / "dssq-groundstation-ventunoq.zip").unlink()
    proc = run_packager(ROOT, "--verify", "--out", str(out))
    assert proc.returncode == pa.EXIT_FAIL, proc.stdout + proc.stderr
    assert "FAIL dssq-groundstation-ventunoq.zip: missing" in proc.stdout
    assert "SHA256SUMS: vgq1-spacecraft-unoq.zip has a different SHA-256" in proc.stdout


FAKE_COMPILER = """#!{python}
# acompile.sh stand-in: <board> <sketch> --out <dir>; writes <dir>/compile.log like the real one.
import pathlib, sys
board, sketch, out = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[4])
folder = {folder}
(out / "compile.log").write_text(
    f"Using library Arduino_RouterBridge at version 0.4.3 in folder: {{folder}} \\n"
    "Sketch uses 1000 bytes (1%) of program storage space. Maximum is 100000 bytes.\\n")
sys.exit({status})
"""


def fake_compiler(tmp_path: Path, folder: str, status: int) -> Path:
    script = tmp_path / "fake_acompile.py"
    script.write_text(FAKE_COMPILER.format(python=sys.executable, folder=folder, status=status))
    script.chmod(0o755)
    return script


@pytest.mark.parametrize(("folder", "status", "expected"), [
    ('sketch / "libraries" / "Arduino_RouterBridge"', 0, None),
    ('"/opt/arduino15/internal/Arduino_RouterBridge_0.4.3/Arduino_RouterBridge"', 0, "does not show"),  # platform's
    ('sketch / "libraries" / "Arduino_RouterBridge"', 1, "exit status 1"),
])
def test_compile_result_is_checked(tmp_path: Path, folder: str, status: int, expected: str | None) -> None:
    compiler = pa.Compiler(str(fake_compiler(tmp_path, folder, status)), wrapper=True)
    sketch = tmp_path / "unpacked" / "vgq1-spacecraft" / "sketch"
    sketch.mkdir(parents=True)
    findings, notes = pa.compile_sketch(compiler, "unoq", sketch, tmp_path / "build")
    if expected is None:
        assert findings == [] and notes[0].startswith("Sketch uses 1000 bytes")
        assert notes[-1].startswith("Using library Arduino_RouterBridge at version 0.4.3 in folder: ")
    else:
        assert any(expected in finding.text for finding in findings), findings


def test_verify_with_build_compiles_both_sketches(built: Path, tmp_path: Path) -> None:
    script = fake_compiler(tmp_path, 'sketch / "libraries" / "Arduino_RouterBridge"', 0)
    proc = run_packager(ROOT, "--verify", "--out", str(built), "--build", "--compiler", str(script))
    assert proc.returncode == pa.EXIT_OK, proc.stdout + proc.stderr
    assert proc.stdout.count("verified and compiled") == 2 and proc.stdout.count("Sketch uses 1000 bytes") == 2
