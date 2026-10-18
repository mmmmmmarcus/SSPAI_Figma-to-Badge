#!/usr/bin/env python3
"""Create a local venv, install dependencies, check Blender and build demo inputs."""
import argparse
import os
from pathlib import Path
import subprocess
import venv

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--blender', help='Blender executable; otherwise BLENDER_BIN/PATH/macOS application')
a = p.parse_args()
venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
def run(args):
    subprocess.run([str(x) for x in args], cwd=ROOT, check=True)
run([python, '-m', 'pip', 'install', '-e', '.'])
import sys
sys.path.insert(0, str(ROOT))
from badgegen.cli import blender_path
blender = blender_path(a.blender)
run([python, '-m', 'badgegen', '--blender', blender, 'doctor'])
run([blender, '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1', '--python', ROOT/'scripts/create_demo.py'])
print('Ready. Run .venv/bin/python -m badgegen batch examples/demo-gold.json examples/demo-silver.json --output output')
print('Windows: use .venv\\Scripts\\python.exe. Keep BLENDER_BIN set if Blender is outside PATH.')
