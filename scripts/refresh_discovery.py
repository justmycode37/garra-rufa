"""Rebuild source/evidence discovery and the deployment bundle in one command."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
subprocess.run([sys.executable, "-m", "garra.discovery.build_snapshot", *sys.argv[1:]],
               cwd=ROOT, env=environment, check=True)
subprocess.run([sys.executable, str(ROOT / "scripts/export_hosted_discovery.py")],
               cwd=ROOT, env=environment, check=True)
