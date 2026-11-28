"""Explicit local setup: build the isolated coding worker without sending the checkout."""
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="agent-coding-build-") as temp:
        dest = Path(temp)
        for name in ("Dockerfile", "policy.py", "worker.py"):
            shutil.copyfile(ROOT / "coding_sandbox" / name, dest / name)
        shutil.copyfile(ROOT / "requirements.txt", dest / "requirements.txt")
        subprocess.run(["docker", "build", "-t", "my-ai-agent-coding:local", str(dest)], check=True)


if __name__ == "__main__":
    main()
