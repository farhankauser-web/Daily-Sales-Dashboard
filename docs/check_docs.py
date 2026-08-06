#!/usr/bin/env python3
"""
Consistency sweep for docs/. Run before calling any section complete.

    python docs/check_docs.py            # everything
    python docs/check_docs.py marketing  # one section

Checks, in order of how often each has actually caught something:

  1. broken relative links (code spans and template placeholders ignored)
  2. gap ids: defined once, listed in the section table, indexed in docs/gaps.md
  3. gaps carry every required field, including Classification
  4. decision ids: defined once, listed, and cited by at least one document
  5. ids referenced anywhere but never defined
  6. duplicated business rules across documents in a section
  7. implementation leakage into business prose
  8. *(pending)* markers naming a document that now exists

Exit status is non-zero if anything fails, so it can gate a commit.
"""
from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
SKIP_DIRS = {'templates'}
ID_RE = r'[A-Z]{2,5}-(?!D-)[A-Z]+-\d{3}'
# Legacy two-part ids (FIN-001, BA-001, INFRA-001) predate the
# <SECTION>-<AREA>-<nnn> scheme and are held in the root index until their
# section is written. Recognised so they cannot hide from the checks.
LEGACY_ID_RE = r'(?<![A-Z-])[A-Z]{2,5}-\d{3}(?![\d-])'
DEC_RE = r'[A-Z]{2,4}-D-\d{3}'

failures: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def strip_code(text: str) -> str:
    """Blank out fenced blocks and inline code so examples are not linted."""
    text = re.sub(r'```.*?```', lambda m: '\n' * m.group(0).count('\n'),
                  text, flags=re.S)
    return re.sub(r'`[^`\n]*`', '', text)


def md_files(section: str | None) -> list[Path]:
    out = []
    for p in sorted(DOCS.rglob('*.md')):
        rel = p.relative_to(DOCS)
        if rel.parts and rel.parts[0] in SKIP_DIRS:
            continue
        if section and (not rel.parts or rel.parts[0] != section):
            continue
        out.append(p)
    return out


def sections() -> list[str]:
    return sorted(d.name for d in DOCS.iterdir()
                  if d.is_dir() and d.name not in SKIP_DIRS
                  and (d / 'gaps.md').exists())


# ── 1. links ────────────────────────────────────────────────────────────────
def check_links(files: list[Path]) -> None:
    for p in files:
        body = strip_code(p.read_text())
        for m in re.finditer(r'\[[^\]]*\]\(([^)]+)\)', body):
            t = m.group(1).split('#')[0].strip()
            if not t or t.startswith(('http://', 'https://', 'mailto:')):
                continue
            if '<' in t or '>' in t:       # template placeholder
                continue
            if not (p.parent / t).resolve().exists():
                fail(f'broken link  {p.relative_to(DOCS)} → {t}')


# ── 2-3. gaps ───────────────────────────────────────────────────────────────
REQUIRED_GAP_FIELDS = ['**Priority**', '**Status**', '**Classification**',
                       '**Current behaviour**', '**Expected behaviour**',
                       '**Evidence**', '**Recommendation**']


def check_gaps(section: str) -> None:
    gp = DOCS / section / 'gaps.md'
    if not gp.exists():
        return
    text = gp.read_text()
    bodies = re.findall(rf'^## `({ID_RE})`', text, re.M)
    dupes = [i for i, n in collections.Counter(bodies).items() if n > 1]
    if dupes:
        fail(f'{section}: gap id defined twice: {dupes}')

    head = re.split(r'^---$', text, maxsplit=1, flags=re.M)[0]
    listed = set(re.findall(rf'^\| `({ID_RE})`', head, re.M))
    legacy_listed = set(re.findall(rf'^\| `({LEGACY_ID_RE})`', head, re.M))
    legacy_bodies = set(re.findall(rf'^## `({LEGACY_ID_RE})`', text, re.M))
    orphan_legacy = legacy_listed - legacy_bodies
    if orphan_legacy:
        fail(f'{section}: summary row with no body (legacy id): '
             f'{sorted(orphan_legacy)}')
    closed_part = text.split('## Closed')[-1] if '## Closed' in text else ''
    closed = set(re.findall(rf'^\| `({ID_RE})`', closed_part, re.M))

    missing_row = set(bodies) - listed
    if missing_row:
        fail(f'{section}: gap has a body but no summary row: {sorted(missing_row)}')
    orphan_row = listed - set(bodies)
    if orphan_row:
        fail(f'{section}: summary row with no body: {sorted(orphan_row)}')

    root = (DOCS / 'gaps.md').read_text()
    not_indexed = [g for g in bodies if f'`{g}`' not in root]
    if not_indexed:
        fail(f'{section}: open gap missing from the root index: {not_indexed}')

    blocks = re.split(r'\n(?=## `)', text)
    for b in blocks:
        m = re.match(rf'## `({ID_RE})`', b)
        if not m:
            continue
        missing = [f for f in REQUIRED_GAP_FIELDS if f not in b]
        if missing:
            fail(f'{section}: {m.group(1)} missing {missing}')
    notes.append(f'{section}: {len(bodies)} open gaps, {len(closed)} closed')


# ── 4. decisions ────────────────────────────────────────────────────────────
REQUIRED_DEC_FIELDS = ['**Context**', '**Decision**',
                       '**Alternatives considered**', '**Reason**',
                       '**Consequences**']


def check_decisions(section: str) -> None:
    dp = DOCS / section / 'decisions.md'
    if not dp.exists():
        return
    text = dp.read_text()
    bodies = re.findall(rf'^## `({DEC_RE})`', text, re.M)
    listed = set(re.findall(rf'^\| `({DEC_RE})`', text, re.M))
    if set(bodies) ^ listed:
        fail(f'{section}: decision table/body mismatch: '
             f'{sorted(set(bodies) ^ listed)}')
    for b in re.split(r'\n(?=## `)', text):
        m = re.match(rf'## `({DEC_RE})`', b)
        if not m:
            continue
        missing = [f for f in REQUIRED_DEC_FIELDS if f not in b]
        if missing:
            fail(f'{section}: {m.group(1)} missing {missing}')

    cited = set()
    for p in (DOCS / section).glob('*.md'):
        if p.name == 'decisions.md':
            continue
        cited |= set(re.findall(rf'`({DEC_RE})`', p.read_text()))
    uncited = sorted(set(bodies) - cited)
    if uncited:
        fail(f'{section}: decision never cited by any document: {uncited}')
    notes.append(f'{section}: {len(bodies)} decisions')


# ── 5. dangling ids ─────────────────────────────────────────────────────────
def check_dangling(files: list[Path]) -> None:
    defined, closed = set(), set()
    for s in sections():
        t = (DOCS / s / 'gaps.md').read_text()
        defined |= set(re.findall(rf'^## `({ID_RE})`', t, re.M))
        if '## Closed' in t:
            closed |= set(re.findall(rf'^\| `({ID_RE})`',
                                     t.split('## Closed')[-1], re.M))
    known_external = {'ARCH', 'INFRA'}
    referenced = set()
    for p in files:
        referenced |= set(re.findall(rf'`({ID_RE})`', p.read_text()))
    dangling = sorted(r for r in referenced - defined - closed
                      if r.split('-')[0] not in known_external)
    if dangling:
        fail(f'id referenced but never defined: {dangling}')


# ── 6. duplicated business rules ────────────────────────────────────────────
STOP = {'their', 'which', 'there', 'where', 'other', 'these', 'those',
        'never', 'always', 'because', 'rather', 'without'}


def check_duplicate_rules(section: str) -> None:
    rules: dict[str, list] = {}
    for p in (DOCS / section).glob('*.md'):
        if p.name in ('README.md', 'gaps.md', 'decisions.md',
                      'RETROSPECTIVE.md'):
            continue
        s = p.read_text()
        if '## Business rules' not in s:
            continue
        blk = s.split('## Business rules')[1].split('\n## ')[0]
        for m in re.finditer(r'^\d+\.\s+(.+?)(?=^\d+\.|\Z)', blk, re.M | re.S):
            txt = ' '.join(m.group(1).split())
            # A rule that links to a sibling document is a POINTER to the rule's
            # single home, not a restatement of it — that is the pattern
            # methodology.md asks for, so it must not read as duplication.
            if re.search(r'\]\([\w./-]+\.md\)', txt):
                continue
            key = frozenset(w.lower().strip('*.,—:()')
                            for w in txt.split()
                            if len(w) > 4
                            and w.lower().strip('*.,—:()') not in STOP)
            rules.setdefault(p.name, []).append((txt[:70], key))
    names = list(rules)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for ta, ka in rules[a]:
                for tb, kb in rules[b]:
                    if ka and kb and len(ka & kb) / max(len(ka), len(kb)) > 0.55:
                        fail(f'{section}: rule stated twice —\n'
                             f'      {a}: {ta}\n      {b}: {tb}')


# ── 7. implementation leakage ───────────────────────────────────────────────
LEAK = {
    'ORM call': r'\b\w+\.objects\.',
    'field definition': r'\w+\s*=\s*models\.',
    'function def': r'\bdef \w+\(',
    'SQL': r'\b(SELECT|INSERT INTO|JOIN)\b',
    'HTML tag': r'<(div|span|table|input|script)\b',
}


def check_leakage(section: str) -> None:
    for p in (DOCS / section).glob('*.md'):
        if p.name in ('gaps.md', 'decisions.md', 'RETROSPECTIVE.md'):
            continue                      # evidence blocks legitimately show code
        body = strip_code(p.read_text())
        body = body.split('\n', 1)[1] if '\n' in body else body
        hits = {k: len(re.findall(v, body)) for k, v in LEAK.items()}
        hits = {k: v for k, v in hits.items() if v}
        if hits:
            fail(f'{section}: implementation detail in {p.name}: {hits}')


# ── 8. stale pending markers ────────────────────────────────────────────────
def check_pending(files: list[Path]) -> None:
    for p in files:
        for m in re.finditer(r'([\w./-]+\.md) \*\(pending\)\*', p.read_text()):
            target = p.parent / m.group(1)
            if target.exists():
                fail(f'{p.relative_to(DOCS)}: marks {m.group(1)} pending, '
                     f'but it exists — upgrade to a link')


def main() -> int:
    section = sys.argv[1] if len(sys.argv) > 1 else None
    files = md_files(section)
    if not files:
        print(f'no documents found for {section!r}')
        return 2

    check_links(files)
    check_dangling(files)
    check_pending(files)
    for s in ([section] if section else sections()):
        if (DOCS / s).is_dir():
            check_gaps(s)
            check_decisions(s)
            check_duplicate_rules(s)
            check_leakage(s)

    for n in notes:
        print(f'  · {n}')
    print(f'  · {len(files)} documents checked')
    if failures:
        print(f'\n✗ {len(failures)} problem(s):\n')
        for f in failures:
            print(f'  {f}')
        return 1
    print('\n✓ consistent')
    return 0


if __name__ == '__main__':
    sys.exit(main())
