import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.sync_project import ProjectSynchronizer, SyncError


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


class ProjectSynchronizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.remote = root / "remote.git"
        self.source = root / "source"
        self.checkout = root / "checkout"
        git(root, "init", "--bare", str(self.remote))
        git(root, "clone", str(self.remote), str(self.source))
        git(self.source, "config", "user.name", "Test User")
        git(self.source, "config", "user.email", "test@example.com")
        (self.source / "data.txt").write_text("v1\n", encoding="utf-8")
        git(self.source, "add", "data.txt")
        git(self.source, "commit", "-m", "initial")
        git(self.source, "branch", "-M", "main")
        git(self.source, "push", "-u", "origin", "main")
        git(root, "clone", "--branch", "main", str(self.remote), str(self.checkout))
        self.syncer = ProjectSynchronizer(self.checkout)

    def tearDown(self):
        self.temp.cleanup()

    def push_remote_update(self):
        (self.source / "data.txt").write_text("v2\n", encoding="utf-8")
        git(self.source, "add", "data.txt")
        git(self.source, "commit", "-m", "update")
        git(self.source, "push")

    def test_detects_and_fast_forwards_remote_update(self):
        self.push_remote_update()
        before = self.syncer.status()
        self.assertEqual((before.ahead, before.behind), (0, 1))
        after = self.syncer.sync()
        self.assertTrue(after.is_current)
        self.assertEqual((self.checkout / "data.txt").read_text(), "v2\n")

    def test_untracked_files_are_preserved(self):
        local_only = self.checkout / "local-only.txt"
        local_only.write_text("private\n", encoding="utf-8")
        self.push_remote_update()
        self.syncer.sync()
        self.assertEqual(local_only.read_text(), "private\n")

    def test_tracked_changes_stop_sync(self):
        (self.checkout / "data.txt").write_text("local edit\n", encoding="utf-8")
        self.push_remote_update()
        with self.assertRaisesRegex(SyncError, "尚未提交"):
            self.syncer.sync()


if __name__ == "__main__":
    unittest.main()
