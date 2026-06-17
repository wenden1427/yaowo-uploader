import os
import unittest

import updater


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

    def test_local_version_prefers_git_head_over_version_file(self):
        git_sha = "a" * 40
        version_file_sha = "b" * 40

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
                return version_file_sha

        updater.open = lambda *args, **kwargs: FakeFile()

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


if __name__ == "__main__":
    unittest.main()
