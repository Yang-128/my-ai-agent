"""Run offline project checks from any working directory."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    sources = list(ROOT.glob("*.py"))
    for directory in ("scripts", "tests"):
        sources.extend((ROOT / directory).rglob("*.py"))
    for path in sorted(sources):
        compile(path.read_bytes(), str(path.relative_to(ROOT)), "exec")

    for path in sorted((ROOT / "static").rglob("*.js")):
        subprocess.run(["node", "--check", path], check=True)

    with tempfile.TemporaryDirectory() as data_dir:
        os.environ["MY_AI_AGENT_DATA_DIR"] = data_dir
        suite = unittest.defaultTestLoader.discover("tests")
        if suite.countTestCases() == 0:
            sys.exit("No unit tests discovered in tests/")
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
