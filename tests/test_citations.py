"""Citation tags in code against docs/12_Design_Decisions.md, appendix B.

Code comments, tests and scripts cite the project's two design records by short tags:
"contract N" / "contract N.M" (the v3 interface contract, also written "contract v3
section N", "contract §N" or as a list "contract 2.2, 14, 25"), "PB-x" / "PB-x.y" (the
Phase B integration specification), "C-nn" (its conflict register), "F-nn" (its follow-up
list) and "I1".."I4" (the Phase C interface decisions). The records are not in the
repository; appendix B of docs/12 maps every tag to its topic and to the place where that
subject is documented now. This test keeps the two in step, both ways:

  * every tag found in the code has a row in appendix B (a new citation fails until its
    row is added);
  * every row of appendix B names a tag that still appears in the code (a removed citation
    fails until its row is removed).

Scanned: vgq1-spacecraft/, dssq-groundstation/, nicla_sense_me_aru/, tools/, tests/ and
.github/ - source, script, configuration and Markdown files - but not the bundled
libraries/ folders, patch files, caches, runtime archives or this file.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "12_Design_Decisions.md"
APPENDIX = "## Appendix B - Citation tags in code"
TOPS = ("vgq1-spacecraft", "dssq-groundstation", "nicla_sense_me_aru", "tools", "tests", ".github")
SUFFIXES = {".py", ".h", ".c", ".cpp", ".ino", ".sh", ".yml", ".yaml", ".toml", ".md", ".html", ".js",
            ".service"}
NAMES = {"Makefile"}
SKIP_DIRS = {"__pycache__", "libraries", "vectors", "data", "archive", ".pytest_cache", ".mypy_cache",
             ".ruff_cache", ".venv", "venv"}
SELF = Path(__file__).resolve()

NUM = r"\d{1,2}(?:\.\d{1,2})?"
# List separators inside one citation: "2.2, 14, 25", "23/27", "16.3 / 17.1", "25.2 and 30".
SEP = r"\s*(?:,|/|&|\+|\band\b|\bor\b)\s*(?:sections?\s+|§\s*)?"
CONTRACT_RE = re.compile(r"\b[Cc]ontract(?:\s+v3)?(?:\s+sections?|\s+sec\.?)?\s*§?\s*(" + NUM
                         + r"(?:" + SEP + NUM + r")*)\b")
NUM_RE = re.compile(NUM)
PB_RE = re.compile(r"\bPB-(\d{1,2}(?:\.\d{1,2}[a-z]?)?)\b")
CONFLICT_RE = re.compile(r"\bC-(\d{2})\b")
FOLLOWUP_RE = re.compile(r"\bF-(\d{2})\b")
INTERFACE_RE = re.compile(r"\bI([1-4])\b")
ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|(.*)\|\s*$")


def tags_in(text: str) -> set[str]:
    """Every citation tag in `text`, normalised ("contract 13.4", "PB-8.4a", "C-06", "F-25", "I3")."""
    found: set[str] = set()
    for m in CONTRACT_RE.finditer(text):
        found.update("contract " + n for n in NUM_RE.findall(m.group(1)))
    found.update("PB-" + m.group(1) for m in PB_RE.finditer(text))
    found.update("C-" + m.group(1) for m in CONFLICT_RE.finditer(text))
    found.update("F-" + m.group(1) for m in FOLLOWUP_RE.finditer(text))
    found.update("I" + m.group(1) for m in INTERFACE_RE.finditer(text))
    return found


def code_files() -> Iterator[Path]:
    for top in TOPS:
        root = REPO / top
        if not root.is_dir():
            continue
        stack = [root]
        while stack:                                    # bounded by the tree; no symlinked dirs
            d = stack.pop()
            for p in sorted(d.iterdir()):
                if p.is_symlink():
                    continue
                if p.is_dir():
                    if p.name not in SKIP_DIRS:
                        stack.append(p)
                elif (p.suffix in SUFFIXES or p.name in NAMES) and p.resolve() != SELF:
                    yield p


def code_tags() -> dict[str, set[str]]:
    """Tag -> the files (relative to the repository) that cite it."""
    where: dict[str, set[str]] = {}
    for p in code_files():
        for tag in tags_in(p.read_text(encoding="utf-8", errors="replace")):
            where.setdefault(tag, set()).add(p.relative_to(REPO).as_posix())
    return where


def appendix_rows() -> list[tuple[str, list[str]]]:
    """(tag, other cells) of every table row of appendix B."""
    lines = DOC.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(APPENDIX)
    except ValueError:
        pytest.fail(f"{DOC.name}: heading {APPENDIX!r} not found")
    rows: list[tuple[str, list[str]]] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        m = ROW_RE.match(line)
        if m:
            rows.append((m.group(1), [c.strip() for c in m.group(2).split("|")]))
    return rows


def test_tag_extraction_reads_every_citation_form() -> None:
    assert tags_in("long-range radio option (contract 2.2, 14, 25)") == {
        "contract 2.2", "contract 14", "contract 25"}
    assert tags_in("fitted (contract v3 sections 2, 9, 10 and 14):") == {
        "contract 2", "contract 9", "contract 10", "contract 14"}
    assert tags_in("(contract 23/27, PB-7 F-13)") == {"contract 23", "contract 27", "PB-7", "F-13"}
    assert tags_in("Contract v3 sections 25.2 and 30: the SX126x") == {"contract 25.2", "contract 30"}
    assert tags_in("contract §13.4; Phase B spec PB-8.4a, C-03") == {"contract 13.4", "PB-8.4a", "C-03"}
    assert tags_in("(contract 9: CH 23, profile 1,") == {"contract 9"}
    assert tags_in("interface decision I1; C50 / I4") == {"I1", "I4"}
    assert tags_in("Wire2 = I2C3, C09, E22-400T33D, 433 MHz") == set()


def test_appendix_b_is_well_formed() -> None:
    rows = appendix_rows()
    assert rows, f"{DOC.name}: appendix B has no table rows"
    tags = [t for t, _ in rows]
    dupes = sorted({t for t in tags if tags.count(t) > 1})
    assert not dupes, f"{DOC.name} appendix B: duplicate rows {dupes}"
    bad = [t for t, cells in rows if len(cells) != 2 or not all(cells) or tags_in(f"`{t}`") != {t}]
    assert not bad, f"{DOC.name} appendix B: rows need a valid tag, a topic and a location: {bad}"
    text = DOC.read_text(encoding="utf-8")
    anchors = set(re.findall(r'<a id="(d-\d+)"></a>', text))
    dead = sorted({a for _, cells in rows for a in re.findall(r"\(#(d-\d+)\)", cells[1])} - anchors)
    assert not dead, f"{DOC.name} appendix B: links to decisions that do not exist: {dead}"


def test_every_tag_in_code_has_a_row_in_appendix_b() -> None:
    documented = {t for t, _ in appendix_rows()}
    where = code_tags()
    missing = sorted(set(where) - documented)
    assert not missing, (
        f"citation tags in code without a row in {DOC.name} appendix B (add the row: tag, topic, "
        "where documented now):\n" + "\n".join(f"  {t}: {', '.join(sorted(where[t]))}" for t in missing))


def test_every_row_of_appendix_b_is_still_cited_in_code() -> None:
    cited = set(code_tags())
    stale = sorted({t for t, _ in appendix_rows()} - cited)
    assert not stale, f"{DOC.name} appendix B rows whose tag no longer appears in the code (remove them): {stale}"
