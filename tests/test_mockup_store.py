from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from mockup_store import CollectionConflict, CollectionNotFound, CollectionStore, StorageError


def background(name="room.png", color="white"):
    stream = BytesIO()
    Image.new("RGB", (120, 100), color).save(stream, "PNG")
    data = stream.getvalue()
    return {"id": sha256(data).hexdigest(), "name": name, "data": data, "size": (120, 100), "index": 0}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = CollectionStore(self.directory.name)
        self.code = self.store.create()
        self.bg = background("../../my room.png")
        self.points = {self.bg["id"]: [[10, 10], [90, 10], [90, 80], [10, 80]]}
        self.detections = {self.bg["id"]: {"source": "manual", "reason": "Adjusted"}}

    def save(self, revision=0, records=None):
        return self.store.save(self.code, records if records is not None else [self.bg], self.points, self.detections, revision)

    def test_roundtrip_new_store_and_isolated_collections(self):
        self.assertEqual(self.save(), 1)
        loaded = CollectionStore(self.directory.name).load(self.code)
        self.assertEqual(loaded["revision"], 1)
        self.assertEqual(loaded["backgrounds"][0]["data"], self.bg["data"])
        self.assertEqual(loaded["backgrounds"][0]["points"], self.points[self.bg["id"]])
        self.assertEqual(loaded["backgrounds"][0]["detection"], self.detections[self.bg["id"]])
        other_code = self.store.create()
        self.assertEqual(self.store.load(other_code)["backgrounds"], [])
        with self.assertRaises(CollectionNotFound):
            self.store.load("../" + self.code)
        with self.assertRaises(CollectionNotFound):
            self.store.load("a" * 43)
        for path in Path(self.directory.name).rglob("*"):
            self.assertNotIn(self.code, str(path))
            if path.is_file():
                self.assertNotIn(self.code.encode(), path.read_bytes())

    def test_conflict_then_reload_and_save(self):
        first = self.store.load(self.code)
        second = self.store.load(self.code)
        self.save(first["revision"])
        with self.assertRaises(CollectionConflict):
            self.save(second["revision"], [])
        self.assertEqual(len(self.store.load(self.code)["backgrounds"]), 1)
        self.save(self.store.load(self.code)["revision"], [])
        self.assertEqual(self.store.load(self.code)["backgrounds"], [])
        self.assertFalse(any(Path(self.directory.name).rglob("*.image")))

    def test_failed_atomic_metadata_write_preserves_previous_collection(self):
        self.save()
        with patch("mockup_store.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.save(1, [])
        loaded = self.store.load(self.code)
        self.assertEqual(loaded["revision"], 1)
        self.assertEqual(loaded["backgrounds"][0]["data"], self.bg["data"])

    def test_duplicates_limits_and_tampered_image(self):
        with self.assertRaises(StorageError):
            self.save(records=[self.bg, self.bg])
        with self.assertRaises(StorageError):
            self.save(records=[self.bg]*21)
        self.save()
        image_path = next(Path(self.directory.name).rglob("*.image"))
        image_path.write_bytes(b"broken")
        with self.assertRaises(StorageError):
            self.store.load(self.code)


if __name__ == "__main__":
    unittest.main()
