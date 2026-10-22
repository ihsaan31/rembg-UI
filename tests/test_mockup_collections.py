from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from frame_detection import FrameDetection
from mockup_store import CollectionStore
from test_mockup_store import background
from test_frame_detection import Upload


class CollectionAppTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict("os.environ", {"MOCKUP_DATA_DIR": self.directory.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.background = background()
        self.uploads = [Upload("room.png", self.background["data"])]
        self.points = [[10, 10], [100, 10], [100, 90], [10, 90]]
        self.value = None
        self.args = {}
        def canvas(**kwargs):
            self.args = kwargs
            return self.value
        self.patches = [patch("streamlit.file_uploader", side_effect=lambda *args, **kwargs: self.uploads if kwargs["key"].startswith("mockup-backgrounds") else []),
                        patch("mockup_ui.corner_canvas", side_effect=canvas),
                        patch("mockup_ui.detect_frame", return_value=FrameDetection(self.points))]
        self.detector = None
        for item in self.patches:
            result = item.start()
            self.addCleanup(item.stop)
            self.detector = result
        self.app_path = str(Path(__file__).resolve().parents[1] / "app.py")

    def app(self):
        return AppTest.from_file(self.app_path, default_timeout=30)

    def open_code(self, app, code):
        app.text_input(key="collection-open-code").set_value(code)
        next(button for button in app.button if button.label == "Buka dengan kode pribadi").click().run()

    def test_create_autosave_edit_reopen_without_upload_or_detection(self):
        app = self.app().run()
        app.button(key="collection-create").click().run()
        self.assertEqual(list(app.exception), [])
        code = app.session_state["mockup-collection-code"]
        saved = CollectionStore().load(code)
        self.assertEqual(len(saved["backgrounds"]), 1)
        edited = [[11, 11], [99, 11], [99, 89], [11, 89]]
        self.value = {"identity": self.args["identity"], "event": "move", "points": edited}
        app.run()
        self.assertEqual(CollectionStore().load(code)["backgrounds"][0]["points"], edited)
        calls_before_reopen = self.detector.call_count
        self.value = None
        self.uploads = []
        other = self.app().run()
        self.open_code(other, code)
        self.assertEqual(list(other.exception), [])
        self.assertEqual(other.session_state["mockup-points"][self.background["id"]], edited)
        self.assertEqual(self.detector.call_count, calls_before_reopen)
        # Invalid access never replaces the active collection.
        self.open_code(other, "x"*43)
        self.assertEqual(other.session_state["mockup-collection-code"], code)

    def test_confirm_delete_does_not_readd_file_still_in_uploader(self):
        app = self.app().run()
        app.button(key="collection-create").click().run()
        code = app.session_state["mockup-collection-code"]
        identifier = self.background["id"]
        app.button(key=f"mockup-delete-{identifier}").click().run()
        self.assertEqual(len(CollectionStore().load(code)["backgrounds"]), 1)
        app.button(key=f"mockup-confirm-delete-{identifier}").click().run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(CollectionStore().load(code)["backgrounds"], [])
        app.run()
        self.assertEqual(CollectionStore().load(code)["backgrounds"], [])

    def test_write_failure_retry_and_conflict_preserve_session_work(self):
        app = self.app().run()
        with patch("mockup_store.CollectionStore.save", side_effect=OSError("disk full")):
            app.button(key="collection-create").click().run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(len(app.session_state["mockup-background-records"]), 1)
        app.run()
        app.button(key="collection-save-retry").click().run()
        code = app.session_state["mockup-collection-code"]
        store = CollectionStore()
        remote = store.load(code)
        store.save(code, [], {}, {}, remote["revision"])
        self.value = {"identity": self.args["identity"], "event": "move", "points": []}
        app.run()
        self.assertTrue(app.session_state["mockup-storage-conflict"])
        self.assertEqual(len(app.session_state["mockup-background-records"]), 1)
        self.assertEqual(store.load(code)["backgrounds"], [])
        self.value = None
        self.uploads = []  # New uploader identity after reload has no pending files.
        app.button(key="collection-reload").click().run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.session_state["mockup-background-records"], [])


if __name__ == "__main__":
    unittest.main()
