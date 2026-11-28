"""Opt-in real Docker smoke test; no Gemini calls or repository data."""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from terminal_runner import DockerWorkspace  # noqa: E402


def main():
    stop = threading.Event()
    fixture = {"fixture.py": "value = False\n"}
    with DockerWorkspace(fixture, stop) as worker:
        assert worker.snapshot() == fixture
        assert "error" in worker.action("read_file", path="../.env")
        check = worker.command(["python", "-c", (
            "import os, pathlib, socket; "
            "assert os.geteuid() != 0; "
            "assert not any(k in os.environ for k in "
            "('GEMINI_API_KEY','MY_AI_AGENT_CODING_TOKEN','GOOGLE_API_KEY','DOCKER_HOST')); "
            "assert not pathlib.Path('/var/run/docker.sock').exists(); "
            "assert not pathlib.Path('/workspace/.git').exists(); "
            "assert not pathlib.Path('/workspace/.env').exists(); "
            "assert socket.socket().connect_ex(('1.1.1.1',443)) != 0; "
            "print('isolated fixture OK')"
        )])
        assert check["exit_code"] == 0, check
        assert worker.action("replace", path="fixture.py", old="False", new="True")["changed"] == "fixture.py"
        assert worker.snapshot() == {"fixture.py": "value = True\n"}
        failed = worker.command(["python", "-c", "raise SystemExit(7)"])
        assert failed["exit_code"] == 7
    print("PASS: Docker fixture inspection, editing, isolation checks, process exit and cleanup.")


if __name__ == "__main__":
    main()
