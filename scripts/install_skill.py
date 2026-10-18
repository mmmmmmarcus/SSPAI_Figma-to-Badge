#!/usr/bin/env python3
"""Install the Skill; local config locates this checkout. No API tokens are stored."""
import argparse
import json
import os
from pathlib import Path
import shutil
ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--destination', type=Path, default=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'skills'/'figma-badge-3d')
p.add_argument('--replace', action='store_true', help='Explicitly replace an existing Skill; keep its generator config')
a = p.parse_args()
dest = a.destination.expanduser().resolve()
source = ROOT/'skills/figma-badge-3d'
if dest == source or source in dest.parents:
    p.error('Choose an installation destination outside the source Skill')
config = {'generator_root':str(ROOT)}
if dest.exists():
    if not a.replace:
        p.error('Destination exists; inspect it before using --replace')
    if (dest/'config.json').exists():
        config = json.loads((dest/'config.json').read_text())
shutil.copytree(source,dest,dirs_exist_ok=True,ignore=shutil.ignore_patterns('config.json','__pycache__'))
(dest/'config.json').write_text(json.dumps(config,indent=2)+'\n')
print(dest)
