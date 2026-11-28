"""Host-side coding sandbox controller. No model-controlled host commands."""
import difflib
import io
import json
import os
import re
import subprocess
import tarfile
import threading
import time
import uuid
from pathlib import Path

from coding_sandbox.policy import allowed_source, MAX_FILE_BYTES, MAX_SOURCE_BYTES, MAX_FILES

IMAGE = "my-ai-agent-coding:local"
HOST_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT = Path(__file__).resolve().parent


class CodingError(RuntimeError):
    pass


class CodingCancelled(CodingError):
    pass


def process(argv, *, data=b"", timeout=30, cap=32_000, stop=None):
    """Bound both streams and wait; killing docker exec is followed by container cleanup."""
    env = {"PATH": HOST_PATH, "HOME": str(Path.home()), "LANG": "C.UTF-8",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    buffers = [bytearray(), bytearray()]
    truncated = [False, False]
    with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env, start_new_session=True) as proc:
        def drain(pipe, index):
            while True:
                chunk = pipe.read(8192)
                if not chunk:
                    break
                remaining = max(0, cap - len(buffers[index]))
                buffers[index].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated[index] = True

        def feed():
            try:
                proc.stdin.write(data)
            except (BrokenPipeError, OSError):
                pass
            finally:
                proc.stdin.close()

        readers = [threading.Thread(target=drain, args=(proc.stdout, 0), daemon=True),
                   threading.Thread(target=drain, args=(proc.stderr, 1), daemon=True),
                   threading.Thread(target=feed, daemon=True)]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if stop is not None and stop.is_set():
                    raise CodingCancelled("Coding run cancelled.")
                if time.monotonic() >= deadline:
                    raise CodingError("Process timed out; workspace will be destroyed.")
                time.sleep(0.05)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for reader in readers:
                reader.join(timeout=2)
        if any(reader.is_alive() for reader in readers):
            raise CodingError("Process output did not close.")
        return proc.returncode, bytes(buffers[0]), bytes(buffers[1]), any(truncated)


def checked(argv, **kwargs):
    status, out, _, truncated = process(argv, **kwargs)
    if status != 0 or truncated:
        raise CodingError("Required local operation failed or exceeded its output limit.")
    return out


def committed_source(root=ROOT):
    """Read only selected regular UTF-8 blobs from the current commit, never dirty files."""
    def git(*args, **kwargs):
        return checked(["git", "-C", str(root), *args], **kwargs)

    revision = git("rev-parse", "HEAD").decode().strip()
    if not re.fullmatch(r"[a-f0-9]{40,64}", revision):
        raise CodingError("Invalid source commit.")
    listing = git("ls-tree", "-r", "-z", revision, cap=1_000_000)
    files = {}
    total = 0
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, path_bytes = record.split(b"\t", 1)
        path = path_bytes.decode("utf-8", errors="replace")
        mode, kind, blob = meta.decode("ascii").split()
        if not allowed_source(path):
            continue
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise CodingError("Unsupported source object; symlinks/submodules are excluded.")
        raw = git("cat-file", "blob", blob, cap=MAX_FILE_BYTES)
        try:
            files[path] = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CodingError("Selected source contains non-UTF-8 data.") from exc
        total += len(raw)
        if total > MAX_SOURCE_BYTES or len(files) > MAX_FILES:
            raise CodingError("Committed source exceeds MVP workspace limits.")
    if not {"app.py", "chat.py", "scripts/check.py"} <= files.keys():
        raise CodingError("This MVP requires the My AI Agent source checkout.")
    return revision, files


def source_tar(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for path, text in sorted(files.items()):
            if not allowed_source(path):
                raise CodingError("Unapproved source path.")
            raw = text.encode("utf-8")
            entry = tarfile.TarInfo(path)
            entry.size = len(raw)
            entry.mode = 0o644
            archive.addfile(entry, io.BytesIO(raw))
    return stream.getvalue()


def verified_snapshot(value):
    if not isinstance(value, dict) or len(value) > MAX_FILES:
        raise CodingError("Invalid workspace snapshot.")
    total = 0
    for path, content in value.items():
        if not allowed_source(path) or not isinstance(content, str):
            raise CodingError("Snapshot contains an unapproved path or non-text content.")
        size = len(content.encode("utf-8"))
        total += size
        if size > MAX_FILE_BYTES or total > MAX_SOURCE_BYTES:
            raise CodingError("Workspace snapshot exceeds limits.")
    return value


def make_patch(before, after):
    """Compute the diff outside the worker against the original committed source."""
    parts = []
    for path in sorted(before.keys() | after.keys()):
        if before.get(path) == after.get(path):
            continue
        old, new = before.get(path), after.get(path)
        parts.append(f"diff --git a/{path} b/{path}\n")
        if old is None:
            parts.append("new file mode 100644\n")
        if new is None:
            parts.append("deleted file mode 100644\n")
        if not old and not new:
            continue
        for line in difflib.unified_diff(
            (old or "").splitlines(keepends=True), (new or "").splitlines(keepends=True),
            fromfile="/dev/null" if old is None else f"a/{path}",
            tofile="/dev/null" if new is None else f"b/{path}", lineterm="\n",
        ):
            parts.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    patch = "".join(parts)
    if len(patch.encode("utf-8")) > 180_000:
        raise CodingError("Patch too large for the first coding workflow.")
    return patch


class DockerWorkspace:
    """Run in a local Linux Docker worker with no mounts, network, or host credentials."""
    def __init__(self, files, stop):
        self.files = files
        self.stop = stop
        self.name = "my-ai-agent-coding-" + uuid.uuid4().hex
        self.base = None
        self.started = False

    def __enter__(self):
        try:
            context = checked(["docker", "context", "show"], cap=2000).decode().strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", context):
                raise CodingError("Unsupported Docker context.")
            self.base = ["docker", "--context", context]
            endpoint = checked(self.base + ["context", "inspect", context,
                               "--format", "{{.Endpoints.docker.Host}}"], cap=2000).decode().strip()
            if not endpoint.startswith("unix://"):
                raise CodingError("Only a local Unix-socket Docker engine is supported.")
            if checked(self.base + ["info", "--format", "{{.OSType}}"]).strip() != b"linux":
                raise CodingError("A Linux Docker engine is required.")
            # Never pull an image as an implicit side effect of a coding task.
            image = checked(self.base + ["image", "inspect", IMAGE, "--format", "{{.Id}}"]).decode().strip()
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
                raise CodingError("Build the local coding image first.")
            self.started = True
            checked(self.base + ["run", "-d", "--pull=never", "--name", self.name,
                "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "128",
                "--memory", "1024m", "--cpus", "2", "--ipc", "none", "--init",
                "--user", "10001:10001", "--log-driver", "none",
                "--tmpfs", "/workspace:rw,nosuid,nodev,noexec,size=64m,uid=10001,gid=10001,mode=0700",
                "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
                "--entrypoint", "/bin/sleep", image, "infinity"], timeout=30, stop=self.stop)
            checked(self.exec_args(["tar", "-xf", "-", "-C", "/workspace"]),
                    data=source_tar(self.files), stop=self.stop)
            return self
        except BaseException:
            self.close()
            raise

    def exec_args(self, argv):
        return self.base + ["exec", "-i", "-u", "10001:10001", "-w", "/workspace", self.name, *argv]

    def action(self, operation, **args):
        data = json.dumps({"operation": operation, **args}).encode()
        if len(data) > MAX_FILE_BYTES * 2:
            raise CodingError("Tool arguments exceed limit.")
        limit = 40_000_000 if operation == "snapshot" else 48_000
        code, out, _, truncated = process(
            self.exec_args(["python", "/opt/coding/worker.py"]), data=data,
            cap=limit, timeout=25, stop=self.stop)
        if truncated:
            raise CodingError("Workspace output exceeded limit.")
        try:
            answer = json.loads(out)
        except (ValueError, UnicodeDecodeError) as exc:
            raise CodingError("Invalid worker response.") from exc
        if code != 0 or "error" in answer:
            if code != 2 or not isinstance(answer.get("error"), str):
                raise CodingError("Workspace helper failed.")
            return {"error": answer["error"]}
        return answer["result"]

    def command(self, argv, timeout=60):
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 32
                or any(not isinstance(a, str) or not a or len(a) > 2000 or "\0" in a for a in argv)
                or argv[0] not in {"python", "python3", "node", "ls", "grep", "find", "cat", "git"}):
            return {"error": "Use a supported argument-array command; no host shell."}
        code, out, err, truncated = process(self.exec_args(argv), timeout=timeout, stop=self.stop, cap=14000)
        return {"exit_code": code, "stdout": out.decode(errors="replace"),
                "stderr": err.decode(errors="replace"), "truncated": truncated}

    def snapshot(self):
        return verified_snapshot(self.action("snapshot"))

    def close(self):
        if self.base and self.started:
            # A missing/unreachable container is reported rather than silently ignored.
            code, _, err, _ = process(self.base + ["rm", "-f", self.name], timeout=20, cap=2000)
            if code != 0 and b"No such container" not in err:
                raise CodingError("Could not confirm worker removal. Inspect local Docker before another run.")
            self.started = False

    def __exit__(self, *_):
        self.close()
