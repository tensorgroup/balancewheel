import hashlib
import os
import sqlite3
import stat
import tempfile
import unittest
from unittest import mock

from lead import sqlite_ro


def fingerprint(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest(), os.stat(path).st_mtime_ns


class SqliteRoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.realpath(self.tmp.name)
        self.state = os.path.join(root, "state")
        self.db = os.path.join(root, "app", "app.db")
        os.makedirs(os.path.dirname(self.db))
        # The app: a WAL database, a writer that stays open, a committed row never checkpointed.
        self.writer = sqlite3.connect(self.db)
        self.writer.execute("PRAGMA journal_mode = WAL")
        self.writer.execute("PRAGMA wal_autocheckpoint = 0")
        self.writer.execute("CREATE TABLE t (k TEXT PRIMARY KEY, v TEXT)")
        self.writer.commit()
        self.writer.execute("INSERT INTO t VALUES ('a', 'committed-in-wal')")
        self.writer.commit()

    def tearDown(self):
        self.writer.close()
        self.tmp.cleanup()

    def files(self):
        return {s: fingerprint(self.db + s) for s in ("", "-wal", "-shm")}

    def sealed(self):
        return os.path.join(self.state, "sealed")

    def test_reader_sees_uncheckpointed_row_and_source_files_unchanged(self):
        self.assertIsNotNone(self.files()["-wal"])
        self.assertIsNotNone(self.files()["-shm"])
        before = self.files()
        res, why = sqlite_ro.query(self.db, lambda c: c.execute("SELECT v FROM t WHERE k='a'").fetchone()[0], self.state)
        self.assertIsNone(why)
        self.assertEqual(res, "committed-in-wal")
        self.assertEqual(self.files(), before)

    def test_write_inside_callback_raises(self):
        seen = {}

        def fn(conn):
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO t VALUES ('b', 'x')")
            seen["ok"] = True
            return 1
        res, why = sqlite_ro.query(self.db, fn, self.state)
        self.assertEqual((res, why), (1, None))
        self.assertTrue(seen["ok"])

    def test_copy_is_sealed_private_and_removed(self):
        seen = {}

        def fn(conn):
            path = conn.execute("PRAGMA database_list").fetchone()[2]
            seen["path"] = path
            seen["dir_mode"] = stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode)
            seen["sealed_mode"] = stat.S_IMODE(os.stat(self.sealed()).st_mode)
            return None
        sqlite_ro.query(self.db, fn, self.state)
        self.assertTrue(os.path.realpath(seen["path"]).startswith(self.sealed() + os.sep))
        self.assertEqual(seen["dir_mode"], 0o700)
        self.assertEqual(seen["sealed_mode"], 0o700)
        self.assertFalse(os.path.exists(os.path.dirname(seen["path"])))
        self.assertEqual(os.listdir(self.sealed()), [])

    def test_copy_removed_when_callback_raises(self):
        def fn(conn):
            raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            sqlite_ro.query(self.db, fn, self.state)
        self.assertEqual(os.listdir(self.sealed()), [])

    def test_shm_never_copied(self):
        copied = []
        real = sqlite_ro._copy

        def rec(src, dst):
            copied.append(src)
            real(src, dst)
        with mock.patch.object(sqlite_ro, "_copy", rec):
            _, why = sqlite_ro.query(self.db, lambda c: 1, self.state)
        self.assertIsNone(why)
        self.assertEqual(sorted(copied), [self.db, self.db + "-wal"])

    def test_oversized_is_unknown(self):
        res, why = sqlite_ro.query(self.db, lambda c: 1, self.state, max_bytes=10)
        self.assertIsNone(res)
        self.assertIn("too large to snapshot", why)

    def test_absent_db_is_unknown(self):
        res, why = sqlite_ro.query(self.db + ".nope", lambda c: 1, self.state)
        self.assertIsNone(res)
        self.assertIn("absent", why)

    def test_missing_table_names_it(self):
        res, why = sqlite_ro.query(self.db, lambda c: c.execute("SELECT * FROM gone").fetchall(), self.state)
        self.assertIsNone(res)
        self.assertIn("no such table: gone", why)

    def test_corrupt_copy_is_inconsistent(self):
        bad = os.path.join(os.path.dirname(self.db), "bad.db")
        with open(bad, "wb") as f:
            f.write(b"not a database" * 100)
        res, why = sqlite_ro.query(bad, lambda c: c.execute("SELECT 1 FROM t").fetchall(), self.state)
        self.assertIsNone(res)
        self.assertEqual(why, sqlite_ro.INCONSISTENT)
        self.assertEqual(os.listdir(self.sealed()), [])


if __name__ == "__main__":
    unittest.main()
