#!/usr/bin/env python3
"""Build VortexNatro.zip with the SAME exclusions as .gitignore.

Written after a leak: the first zip included settings/ - personal hotkey config and
a 36 KB runtime log - while the git repo correctly excluded it. A build step has to
apply the same rules as the repository, or the artefact contradicts the source.
"""
import zipfile
from pathlib import Path

SRC = Path(r'C:\Users\RDC\Desktop\NatroVortex')
OUT = Path(r'C:\Users\RDC\Desktop\VortexNatro.zip')

# never ship: per-user state, VCS metadata, caches, generated profiles
EXCLUDE_DIRS = {'settings', '.git', '__pycache__', 'profiles'}
EXCLUDE_SUFFIX = {'.pyc'}

if OUT.exists():
    OUT.unlink()

n = 0
skipped = []
with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as z:
    for p in sorted(SRC.rglob('*')):
        if not p.is_file():
            continue
        rel = p.relative_to(SRC)
        if any(part in EXCLUDE_DIRS for part in rel.parts) or p.suffix in EXCLUDE_SUFFIX:
            skipped.append(rel.as_posix())
            continue
        z.write(p, (Path('VortexNatro') / rel).as_posix())
        n += 1

print(f"  {OUT.name}: {OUT.stat().st_size/1048576:.1f} MB, {n} files")
tops = sorted({s.split('/')[0] for s in skipped})
print(f"  excluded {len(skipped)} file(s) from: {tops}")

z = zipfile.ZipFile(OUT)
names = z.namelist()
for k in ('lib/nm_humanize.ahk', 'lib/nm_verify.ahk', 'tools/ai_advisor.py',
          'NATRO_OFFICIAL_README.md', 'LICENSE.md', 'submacros/AutoHotkey32.exe'):
    print(f"    {k:<32} {'in' if any(x.endswith(k) for x in names) else 'MISSING'}")
bad = [x for x in names if '/settings/' in x or '.pyc' in x]
print(f"    leaks: {len(bad)} {bad[:3] if bad else ''}")