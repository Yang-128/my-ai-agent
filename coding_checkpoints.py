"""Private, bounded local source checkpoints. Never apply a model-generated patch on the host."""
import json
import os
import re
import tempfile
from pathlib import Path

from terminal_runner import CodingError, verified_snapshot

MAX_BYTES = 40_000_000
MAX_SAVED = 12


class CheckpointStore:
    def __init__(self, directory=None):
        if directory is None:
            directory = Path.home() / ".my-ai-agent" / "coding-checkpoints"
        self.directory = Path(directory)

    def prepare(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise CodingError("Invalid coding checkpoint directory.")
        if self.directory.stat().st_uid != os.getuid():
            raise CodingError("Coding checkpoint directory owner mismatch.")
        os.chmod(self.directory, 0o700)

    def path(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise CodingError("Invalid checkpoint identifier.")
        return self.directory / (identifier + ".json")

    def read(self, identifier):
        self.prepare()
        path = self.path(identifier)
        try:
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                if os.fstat(stream.fileno()).st_uid != os.getuid():
                    raise CodingError("Checkpoint owner mismatch.")
                raw = stream.read(MAX_BYTES + 1)
        except OSError as exc:
            raise CodingError("Checkpoint unavailable.") from exc
        if len(raw) > MAX_BYTES:
            raise CodingError("Checkpoint exceeds the size limit.")
        try:
            record = json.loads(raw)
            if (record["id"] != identifier or type(record["issue"]) is not int
                    or not re.fullmatch(r"[a-f0-9]{40,64}", record["revision"])
                    or not isinstance(record["history"], list) or len(record["history"]) > 12
                    or any(not isinstance(item, dict) for item in record["history"])):
                raise ValueError("Invalid checkpoint metadata")
            verified_snapshot(record["files"])
        except (ValueError, TypeError, KeyError) as exc:
            raise CodingError("Invalid checkpoint data.") from exc
        return record

    def save(self, identifier, issue, revision, files, history):
        self.prepare()
        path = self.path(identifier)
        if not path.exists() and len(list(self.directory.glob("*.json"))) >= MAX_SAVED:
            raise CodingError("Checkpoint capacity reached; review and delete an old checkpoint first.")
        record = {"id": identifier, "issue": issue, "revision": revision,
                  "files": verified_snapshot(files), "history": history[-12:]}
        raw = json.dumps(record, ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_BYTES:
            raise CodingError("Checkpoint exceeds the size limit.")
        fd, name = tempfile.mkstemp(prefix=".checkpoint-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def list(self):
        self.prepare()
        result = []
        for path in sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:MAX_SAVED]:
            try:
                record = self.read(path.stem)
            except CodingError:
                continue
            last = record["history"][-1] if record["history"] else {}
            result.append({"id": record["id"], "issue": record["issue"], "revision": record["revision"],
                           "last_model": last.get("model"), "last_reason": last.get("reason")})
        return result

    def delete(self, identifier):
        self.prepare()
        path = self.path(identifier)
        if path.is_symlink():
            raise CodingError("Invalid checkpoint path.")
        path.unlink(missing_ok=True)
