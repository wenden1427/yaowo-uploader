import os
import tempfile
import unittest

import updater
import main as uploader_main


class UpdaterVersionTests(unittest.TestCase):
    def setUp(self):
        self._orig_exists = updater.os.path.exists
        self._orig_run = updater.subprocess.run
        self._had_open = hasattr(updater, "open")
        self._orig_open = getattr(updater, "open", None)

    def tearDown(self):
        updater.os.path.exists = self._orig_exists
        updater.subprocess.run = self._orig_run
        if self._had_open:
            updater.open = self._orig_open
        elif hasattr(updater, "open"):
            delattr(updater, "open")

    def test_local_version_prefers_version_file_over_git_head(self):
        git_sha = "a" * 40
        version_marker = "2026.06.17-cos.3"

        updater.os.path.exists = lambda path: True

        class Result:
            returncode = 0
            stdout = git_sha + "\n"

        updater.subprocess.run = lambda *args, **kwargs: Result()

        class FakeFile:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return version_marker

        updater.open = lambda *args, **kwargs: FakeFile()

        self.assertEqual(updater._get_local_version(), version_marker)

    def test_local_version_falls_back_to_git_head_when_version_file_missing(self):
        git_sha = "a" * 40

        updater.os.path.exists = lambda path: False

        class Result:
            returncode = 0
            stdout = git_sha + "\n"

        updater.subprocess.run = lambda *args, **kwargs: Result()

        self.assertEqual(updater._get_local_version(), git_sha)

    def test_local_version_falls_back_to_version_file_outside_git_repo(self):
        version_file_sha = "b" * 40

        updater.os.path.exists = lambda path: path == updater.VERSION_FILE

        class Result:
            returncode = 128
            stdout = ""

        updater.subprocess.run = lambda *args, **kwargs: Result()

        class FakeFile:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return version_file_sha

        updater.open = lambda *args, **kwargs: FakeFile()

        self.assertEqual(updater._get_local_version(), version_file_sha)

    def test_check_update_accepts_commit_sha_written_by_old_updater(self):
        remote_version = "2026.06.17-cos"
        remote_sha = "c" * 40
        prompts = []
        orig_local = updater._get_local_version
        orig_remote = updater._get_remote_version
        orig_remote_sha = updater._get_remote_commit_sha
        orig_ask = updater.messagebox.askyesno
        try:
            updater._get_local_version = lambda: remote_sha
            updater._get_remote_version = lambda: remote_version
            updater._get_remote_commit_sha = lambda: remote_sha
            updater.messagebox.askyesno = lambda *args, **kwargs: prompts.append(args) or True

            self.assertTrue(updater.check_and_update(None))
            self.assertEqual(prompts, [])
        finally:
            updater._get_local_version = orig_local
            updater._get_remote_version = orig_remote
            updater._get_remote_commit_sha = orig_remote_sha
            updater.messagebox.askyesno = orig_ask

    def test_check_update_prompts_when_local_matches_neither_remote_marker_nor_sha(self):
        prompts = []
        updates = []
        orig_local = updater._get_local_version
        orig_remote = updater._get_remote_version
        orig_remote_sha = updater._get_remote_commit_sha
        orig_ask = updater.messagebox.askyesno
        orig_update = updater._do_update
        try:
            updater._get_local_version = lambda: "old-version"
            updater._get_remote_version = lambda: "2026.06.17-cos"
            updater._get_remote_commit_sha = lambda: "c" * 40
            updater.messagebox.askyesno = lambda *args, **kwargs: prompts.append(args) or True
            updater._do_update = lambda remote: updates.append(remote) or True

            self.assertTrue(updater.check_and_update(None))
            self.assertEqual(len(prompts), 1)
            self.assertEqual(updates, ["2026.06.17-cos"])
        finally:
            updater._get_local_version = orig_local
            updater._get_remote_version = orig_remote
            updater._get_remote_commit_sha = orig_remote_sha
            updater.messagebox.askyesno = orig_ask
            updater._do_update = orig_update


class UpdaterCopyTests(unittest.TestCase):
    def test_copy_update_tree_updates_nested_assets_and_preserves_user_files(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            os.makedirs(os.path.join(source, "uploader"))
            os.makedirs(os.path.join(source, "python-portable"))
            with open(os.path.join(source, "gui.py"), "w", encoding="utf-8") as handle:
                handle.write("new gui")
            with open(os.path.join(source, "store_profiles.yaml"), "w", encoding="utf-8") as handle:
                handle.write("repository defaults")
            with open(os.path.join(source, "uploader", "template.xlsx"), "wb") as handle:
                handle.write(b"new template")
            with open(os.path.join(source, "python-portable", "python.exe"), "wb") as handle:
                handle.write(b"do not copy")
            with open(os.path.join(target, "store_profiles.yaml"), "w", encoding="utf-8") as handle:
                handle.write("user settings")

            updater._copy_update_tree(source, target)

            with open(os.path.join(target, "gui.py"), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "new gui")
            with open(os.path.join(target, "store_profiles.yaml"), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "user settings")
            with open(os.path.join(target, "uploader", "template.xlsx"), "rb") as handle:
                self.assertEqual(handle.read(), b"new template")
            self.assertFalse(os.path.exists(os.path.join(target, "python-portable", "python.exe")))


class BundledTemplateTests(unittest.TestCase):
    def test_startup_syncs_template_delivered_by_legacy_updater(self):
        original_skill_dir = uploader_main.SKILL_DIR
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                uploader_main.SKILL_DIR = temp_dir
                os.makedirs(os.path.join(temp_dir, "uploader"))
                source = os.path.join(temp_dir, "default_template.xlsx")
                destination = os.path.join(temp_dir, "uploader", "韩国上传模板.xlsx")
                with open(source, "wb") as handle:
                    handle.write(b"new template")
                with open(destination, "wb") as handle:
                    handle.write(b"old template")

                self.assertIsNone(uploader_main.sync_bundled_template())
                with open(destination, "rb") as handle:
                    self.assertEqual(handle.read(), b"new template")
        finally:
            uploader_main.SKILL_DIR = original_skill_dir


if __name__ == "__main__":
    unittest.main()
