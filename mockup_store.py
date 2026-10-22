"""Private-code background collections with atomic, revision-checked writes."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import fcntl

from painting_mockup import load_image

SCHEMA_VERSION = 1
MAX_BACKGROUNDS = 20
MAX_FILE_BYTES = 8 * 1024 * 1024


class StorageError(Exception):
    pass


class CollectionConflict(StorageError):
    pass


class CollectionNotFound(StorageError):
    pass


class CollectionStore:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("MOCKUP_DATA_DIR", "data/mockups"))

    def _directory(self, code):
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", code):
            raise CollectionNotFound("Invalid collection code.")
        return self.root / sha256(code.encode()).hexdigest()

    @contextmanager
    def _lock(self, directory):
        if not directory.is_dir():
            raise CollectionNotFound("Collection not found. Check your code.")
        with (directory / ".lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _atomic_write(self, target, data):
        path = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                path = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(path, target)
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    def _metadata(self, directory):
        path = directory / "collection.json"
        if path.stat().st_size > 512 * 1024:
            raise StorageError("Collection metadata is too large.")
        try:
            document = json.loads(path.read_text())
            if document["version"] != SCHEMA_VERSION:
                raise StorageError("Unsupported collection version.")
            if not isinstance(document["revision"], int) or document["revision"] < 0:
                raise ValueError("revision")
            if not isinstance(document["backgrounds"], list) or len(document["backgrounds"]) > MAX_BACKGROUNDS:
                raise ValueError("backgrounds")
            return document
        except (ValueError, KeyError, TypeError) as error:
            raise StorageError("Collection metadata could not be read.") from error

    def create(self):
        code = secrets.token_urlsafe(32)
        directory = self._directory(code)
        directory.mkdir(parents=True, mode=0o700)
        try:
            self._atomic_write(directory / "collection.json", json.dumps({"version": SCHEMA_VERSION, "revision": 0, "backgrounds": []}).encode())
        except Exception:
            # No usable collection was published; leave the random directory unreferenced.
            raise
        return code

    def load(self, code):
        directory = self._directory(code)
        with self._lock(directory):
            metadata = self._metadata(directory)
            records = []
            for index, item in enumerate(metadata["backgrounds"]):
                identifier = item.get("id", "")
                if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
                    raise StorageError("Invalid stored background identifier.")
                path = directory / f"{identifier}.image"
                if path.stat().st_size > MAX_FILE_BYTES:
                    raise StorageError("Stored image is too large.")
                data = path.read_bytes()
                if sha256(data).hexdigest() != identifier:
                    raise StorageError("Stored image failed its integrity check.")
                image = load_image(data)
                records.append({"id": identifier, "name": str(item["name"]), "size": image.size,
                                "data": data, "index": index, "points": item.get("points", []),
                                "detection": item.get("detection", {"source": "manual", "reason": "Saved corners."})})
            return {"revision": metadata["revision"], "backgrounds": records}

    def save(self, code, backgrounds, points, detections, expected_revision):
        if len(backgrounds) > MAX_BACKGROUNDS:
            raise StorageError("A collection supports at most 20 backgrounds.")
        directory = self._directory(code)
        with self._lock(directory):
            previous = self._metadata(directory)
            if previous["revision"] != expected_revision:
                raise CollectionConflict("This collection was changed in another session. Reload it before saving again.")
            items, identifiers = [], set()
            for bg in backgrounds:
                identifier = sha256(bg["data"]).hexdigest()
                if identifier != bg["id"] or identifier in identifiers or len(bg["data"]) > MAX_FILE_BYTES:
                    raise StorageError("Invalid or duplicate background.")
                identifiers.add(identifier)
                path = directory / f"{identifier}.image"
                if not path.exists():
                    load_image(bg["data"])
                    self._atomic_write(path, bg["data"])
                items.append({"id": identifier, "name": bg["name"], "size": list(bg["size"]),
                              "points": points.get(identifier, []), "detection": detections.get(identifier, {"source": "manual", "reason": "Saved corners."})})
            revision = expected_revision + 1
            payload = json.dumps({"version": SCHEMA_VERSION, "revision": revision, "backgrounds": items}, allow_nan=False).encode()
            self._atomic_write(directory / "collection.json", payload)
            # Clean removed images only after the new metadata is committed.
            for old in previous["backgrounds"]:
                identifier = old["id"]
                if identifier not in identifiers and re.fullmatch(r"[a-f0-9]{64}", identifier):
                    try:
                        (directory / f"{identifier}.image").unlink(missing_ok=True)
                    except OSError:
                        pass
            return revision
