"""setup/backup.sh and setup/restore.sh, run against a throwaway HOME.

The scripts only ever touch paths under $HOME (via "~/" in the path list) and the backup
root, so pointing both at a temporary directory exercises the real code end to end.
"""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKUP = REPO / "setup" / "backup.sh"
RESTORE = REPO / "setup" / "restore.sh"

PATHS = """\
# test list
~/.claude/CLAUDE.md
~/.claude/scripts
~/.codex/config.toml
~/.config/git/ignore
~/.pi/agent/models.json
"""


class SetupScriptsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-setup-"))
        self.home = self.tmp / "home"
        self.root = self.home / ".balancewheel" / "backups"
        (self.home / ".claude" / "scripts").mkdir(parents=True)
        (self.home / ".claude" / "CLAUDE.md").write_text("original rules\n")
        (self.home / ".claude" / "scripts" / "seat.sh").write_text("#!/bin/sh\necho seat\n")
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "config.toml").write_text('model = "old"\n')
        # ~/.config/git/ignore and ~/.pi/agent/models.json deliberately absent
        self.paths = self.tmp / "paths.txt"
        self.paths.write_text(PATHS)
        self.env = dict(os.environ, HOME=str(self.home), BALANCEWHEEL_BACKUP_ROOT=str(self.root))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, script, *args, expect=0):
        r = subprocess.run(["bash", str(script), *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, expect, f"{script.name} {args}: rc={r.returncode}\n{r.stdout}\n{r.stderr}")
        return r

    def backup(self, *extra):
        r = self.run_script(BACKUP, "--paths", str(self.paths), *extra)
        return Path(r.stdout.strip()) if r.stdout.strip() else None, r

    def test_backup_copies_present_paths_and_records_absent_ones(self):
        dest, r = self.backup()
        self.assertTrue(dest.is_dir() and dest.parent == self.root)
        self.assertEqual((dest / ".claude" / "CLAUDE.md").read_text(), "original rules\n")
        self.assertEqual((dest / ".claude" / "scripts" / "seat.sh").read_text(), "#!/bin/sh\necho seat\n")
        self.assertEqual((dest / ".codex" / "config.toml").read_text(), 'model = "old"\n')
        self.assertFalse((dest / ".config").exists())
        manifest = (dest / "manifest.txt").read_text().splitlines()
        self.assertIn("present ~/.claude/CLAUDE.md", manifest)
        self.assertIn("present ~/.claude/scripts", manifest)
        self.assertIn("absent ~/.config/git/ignore", manifest)
        self.assertIn("absent ~/.pi/agent/models.json", manifest)
        self.assertEqual((dest / "paths.txt").read_text(), PATHS)
        self.assertIn("backed up 3 path(s)", r.stderr)
        # originals untouched
        self.assertEqual((self.home / ".claude" / "CLAUDE.md").read_text(), "original rules\n")

    def test_backup_dry_run_writes_nothing(self):
        dest, r = self.backup("--dry-run")
        self.assertIsNone(dest)
        self.assertFalse(self.root.exists())
        self.assertIn("would copy", r.stderr)
        self.assertIn("3 path(s) would be backed up", r.stderr)

    def test_backup_never_touches_credentials(self):
        (self.home / ".codex" / "auth.json").write_text('{"token": "secret"}')
        (self.home / ".pi" / "agent").mkdir(parents=True)
        (self.home / ".pi" / "agent" / "auth.json").write_text('{"token": "secret"}')
        real_list = REPO / "setup" / "paths.txt"
        r = self.run_script(BACKUP, "--paths", str(real_list))
        dest = Path(r.stdout.strip())
        found = [p for p in dest.rglob("auth.json")]
        self.assertEqual(found, [], "a credential file was backed up")
        listed = [l.strip() for l in real_list.read_text().splitlines() if l.strip() and not l.startswith("#")]
        self.assertFalse(any("auth" in l or "secret" in l for l in listed), "paths.txt must not list credentials")

    def test_restore_puts_originals_back_and_takes_a_safety_backup(self):
        dest, _ = self.backup()
        stamp = dest.name
        # "setup" edits things
        (self.home / ".claude" / "CLAUDE.md").write_text("edited rules\n")
        (self.home / ".claude" / "scripts" / "seat.sh").write_text("changed\n")
        (self.home / ".claude" / "scripts" / "new-wrapper.sh").write_text("added\n")
        (self.home / ".codex" / "config.toml").unlink()
        (self.home / ".config" / "git").mkdir(parents=True)
        (self.home / ".config" / "git" / "ignore").write_text("created by setup\n")
        time.sleep(1.1)  # distinct timestamp for the safety backup
        r = self.run_script(RESTORE, stamp)
        self.assertIn("safety backup of the current state:", r.stderr)
        self.assertIn("restored 3 path(s) from " + stamp, r.stderr)
        self.assertEqual((self.home / ".claude" / "CLAUDE.md").read_text(), "original rules\n")
        self.assertEqual((self.home / ".claude" / "scripts" / "seat.sh").read_text(), "#!/bin/sh\necho seat\n")
        self.assertFalse((self.home / ".claude" / "scripts" / "new-wrapper.sh").exists(), "directory restore must replace wholesale")
        self.assertEqual((self.home / ".codex" / "config.toml").read_text(), 'model = "old"\n')
        # absent-at-backup-time paths are reported, not deleted
        self.assertTrue((self.home / ".config" / "git" / "ignore").exists())
        self.assertIn("did not exist when this backup was taken", r.stderr)
        # the safety backup holds the edited state
        backups = sorted(p for p in self.root.iterdir() if p.is_dir())
        self.assertEqual(len(backups), 2)
        safety = [b for b in backups if b.name != stamp][0]
        self.assertEqual((safety / ".claude" / "CLAUDE.md").read_text(), "edited rules\n")
        self.assertEqual((safety / ".claude" / "scripts" / "new-wrapper.sh").read_text(), "added\n")

    def test_restore_defaults_to_latest_and_dry_run_changes_nothing(self):
        first, _ = self.backup()
        (self.home / ".claude" / "CLAUDE.md").write_text("second state\n")
        time.sleep(1.1)
        second, _ = self.backup()
        (self.home / ".claude" / "CLAUDE.md").write_text("third state\n")
        r = self.run_script(RESTORE, "--dry-run")
        self.assertIn("would restore", r.stderr)
        self.assertIn(second.name, r.stderr)
        self.assertEqual((self.home / ".claude" / "CLAUDE.md").read_text(), "third state\n")
        self.assertEqual(len([p for p in self.root.iterdir()]), 2, "dry run must not take a safety backup")
        time.sleep(1.1)
        self.run_script(RESTORE)
        self.assertEqual((self.home / ".claude" / "CLAUDE.md").read_text(), "second state\n")

    def test_restore_list_and_bad_timestamp(self):
        r = self.run_script(RESTORE, "--list")
        self.assertIn("no backups under", r.stdout)
        dest, _ = self.backup()
        r = self.run_script(RESTORE, "--list")
        self.assertIn(dest.name + "  3 path(s)", r.stdout)
        self.assertIn("    ~/.claude/CLAUDE.md", r.stdout)
        r = self.run_script(RESTORE, "19990101T000000Z", expect=1)
        self.assertIn("no backup named", r.stderr)
        self.assertEqual((self.home / ".claude" / "CLAUDE.md").read_text(), "original rules\n")
        self.assertEqual(len([p for p in self.root.iterdir()]), 1, "a failed restore must not take a safety backup")

    def test_help_flags(self):
        for s in (BACKUP, RESTORE):
            r = self.run_script(s, "--help")
            self.assertIn("Usage:", r.stdout)


if __name__ == "__main__":
    unittest.main()
