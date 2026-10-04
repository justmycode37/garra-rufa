"""Rebuild source/evidence discovery and the deployment bundle in one command."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
if not (ROOT / "data/cache/connections").is_dir() and "--cache" not in sys.argv:
    # No packet cache yet: fetch the anchor packets from the live Monarch/Open Targets APIs.
    print("data/cache/connections is missing; running garra.atlas prefetch-packets first", flush=True)
    subprocess.run([sys.executable, "-m", "garra.atlas", "prefetch-packets"],
                   cwd=ROOT, env=environment, check=True)
subprocess.run([sys.executable, "-m", "garra.discovery.build_snapshot", *sys.argv[1:]],
               cwd=ROOT, env=environment, check=True)
subprocess.run([sys.executable, str(ROOT / "scripts/export_hosted_discovery.py")],
               cwd=ROOT, env=environment, check=True)
