import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead import ccswitch, registry, runner, sandbox  # noqa: E402
from lead.projects import worktree_key  # noqa: E402
from lead.proc import Result  # noqa: E402

NOW = 1_800_000_000.0


def fake_run(argv, timeout, **kw):
    return Result(0, "1.2.3\n", "", False)


class RegistryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.state = os.path.join(self.root, "state")
        self.proj = os.path.join(self.root, "app")
        os.makedirs(self.proj)
        self.config = {
            "plans": {"pc": {"billing": "plan", "quota": "statusline"},
                      "pp": {"billing": "plan", "quota": "probe"},
                      "ps": {"billing": "metered", "quota": "none"}},
            "leads": {"lc": {"runtime": "claude-code", "model": "m-claude", "plan": "pc", "autonomy": "bypass"},
                      "lp": {"runtime": "pi", "model": "m-pi", "plan": "pp", "autonomy": "bypass"}},
            "seats": {"s1": {"runtime": "codex", "model": "m-codex", "plan": "ps", "fallback": "s2"},
                      "s2": {"runtime": "pi", "model": "m-pi", "plan": "pp", "fallback": None}},
            "tools": {"claude": {"update": ["brew", "upgrade", "claude"]}, "pi": {"update": ["npm", "i", "pi"]},
                      "codex": {}},
            "profiles": {"p": {"lead_order": ["lc", "lp"],
                               "checkpoints": {"review": {"seats": ["s1", "s2"], "required": [], "min_seats": 1}}}},
            "default_profile": "p", "projects": {self.proj: "p"},
            "rotation": {"cap_expiry_hours": 5, "return_above_pct": 50, "probe_every_min": 15},
            "state_dir": self.state, "warnings": []}
        self.saved = {k: os.environ.get(k) for k in ("BW_ORCA_SUPPORT", "BW_CCSWITCH_DIR")}
        os.environ["BW_ORCA_SUPPORT"] = os.path.join(self.root, "no-orca")
        os.environ["BW_CCSWITCH_DIR"] = os.path.join(self.root, "no-cc-switch")
        p = mock.patch.object(sandbox, "backend", return_value="sandbox-exec")
        p.start(); self.addCleanup(p.stop)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def build(self, **kw):
        return registry.build(self.config, self.state, NOW, run=fake_run, **kw)

    def by(self, reg, kind, name):
        return next(n for n in reg[kind] if n["name"] == name)

    def test_every_edge_kind_once_per_pair_and_none_dangling(self):
        reg = self.build()
        edges = [(e["from"], e["to"], e["kind"]) for e in reg["edges"]]
        self.assertEqual(len(edges), len(set(edges)))
        self.assertEqual({e[2] for e in edges}, {"project→profile", "profile→lead", "profile→seat", "lead→tool",
                                                 "seat→tool", "tool→plan", "plan→model"})
        ids = {n["id"] for k in ("tools", "plans", "models", "seats", "leads", "profiles", "projects") for n in reg[k]}
        for f, t, _ in edges:
            self.assertIn(f, ids); self.assertIn(t, ids)
        self.assertIn(("tool:pi", "plan:pp", "tool→plan"), edges)  # lp and s2 both reach it: one edge
        self.assertIn((f"project:{self.proj}", "profile:p", "project→profile"), edges)
        self.assertIn(("profile:p", "seat:s2", "profile→seat"), edges)
        self.assertIn(("plan:pp", "model:m-pi", "plan→model"), edges)

    def test_runtime_with_no_tool_makes_no_edge(self):
        del self.config["tools"]["codex"]
        reg = self.build()
        self.assertFalse([e for e in reg["edges"] if e["from"] == "seat:s1" and e["kind"] == "seat→tool"])

    def test_capped_plan_node_is_capped(self):
        os.makedirs(os.path.join(self.state, "caps"))
        with open(os.path.join(self.state, "caps", "pc.json"), "w") as f:
            json.dump({"resets_at": NOW + 3600, "window": "five_hour", "source": "manual"}, f)
        reg = self.build()
        self.assertEqual(self.by(reg, "plans", "pc")["health"], "capped")
        self.assertEqual(self.by(reg, "leads", "lc")["health"], "capped")
        self.assertNotEqual(self.by(reg, "plans", "pp")["health"], "capped")
        self.assertIsNotNone(self.by(reg, "plans", "pc")["cap"])

    def test_missing_seats_json_is_unknown_and_present_is_read(self):
        reg = self.build()
        self.assertEqual(self.by(reg, "seats", "s1")["verified"], "unknown")
        self.assertEqual(self.by(reg, "seats", "s1")["health"], "unknown")
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, "seats.json"), "w") as f:
            json.dump({"s1": {"verified": True}, "s2": {"verified": False}}, f)
        reg = self.build()
        self.assertEqual(self.by(reg, "seats", "s1")["verified"], "verified")
        self.assertEqual(self.by(reg, "seats", "s2")["verified"], "pending")
        with open(os.path.join(self.state, "seats.json"), "w") as f:
            f.write("[not an object")
        self.assertEqual(self.by(self.build(), "seats", "s1")["verified"], "unknown")

    def test_rebuild_after_delete_is_identical_apart_from_generated_at(self):
        a = self.build()
        registry.write(self.state, a)
        path = os.path.join(self.state, "registry.json")
        self.assertTrue(os.path.exists(path))
        os.unlink(path)
        b = self.build()
        for r in (a, b):
            r.pop("generated_at")
        self.assertEqual(a, b)

    def test_everything_external_absent_does_not_raise(self):
        cfg = {"plans": {}, "leads": {}, "seats": {}, "profiles": {}, "projects": {}, "state_dir": self.state}
        reg = registry.build(cfg, self.state, NOW, run=fake_run)
        for k in ("tools", "plans", "models", "seats", "leads", "profiles", "projects", "edges"):
            self.assertEqual(reg[k], [])
        reg = self.build()  # no cc-switch, no Orca, no start.json
        self.assertEqual(self.by(reg, "models", "m-pi")["price"], "unavailable")
        self.assertEqual(self.by(reg, "tools", "pi")["auth"], "unknown")

    def test_tool_fields(self):
        os.makedirs(self.state)
        with open(os.path.join(self.state, "start.json"), "w") as f:
            json.dump({"tools": {"pi": {"after": "9.9", "last_update_at": 123.0}}}, f)
        reg = self.build()
        pi = self.by(reg, "tools", "pi")
        self.assertEqual((pi["version"], pi["last_update_at"], pi["install"], pi["runtime"]), ("9.9", 123.0, "npm", "pi"))
        claude = self.by(reg, "tools", "claude")
        self.assertEqual((claude["version"], claude["install"], claude["last_update_at"]), ("1.2.3", "brew", None))
        self.assertEqual(self.by(reg, "tools", "codex")["install"], "")

    def test_prices_from_ccswitch(self):
        with mock.patch.object(ccswitch, "prices", return_value={"m-pi": {"input": "1", "output": "2"}}):
            reg = self.build()
        self.assertEqual(self.by(reg, "models", "m-pi")["price"], {"input": "1", "output": "2"})
        self.assertEqual(self.by(reg, "models", "m-codex")["price"], "unavailable")
        self.assertEqual(self.by(reg, "models", "m-pi")["tools"], ["pi"])
        self.assertEqual(self.by(reg, "models", "m-pi")["plans"], ["pp"])
        with mock.patch.object(ccswitch, "prices", side_effect=RuntimeError("boom")):
            self.assertEqual(self.by(self.build(), "models", "m-pi")["price"], "unavailable")

    def test_locks_held_without_disturbing_the_holder(self):
        reg = self.build()
        self.assertEqual(self.by(reg, "tools", "pi")["locks"], "free")
        from lead.locks import RuntimeLock
        lock = RuntimeLock(self.state, "pi")
        try:
            self.assertTrue(lock.try_shared())
            reg = self.build()
            self.assertEqual(self.by(reg, "tools", "pi")["locks"], "held")
            self.assertEqual(self.by(reg, "tools", "claude")["locks"], "free")
            self.assertTrue(lock.try_shared())  # the holder still holds it, undisturbed
        finally:
            lock.release()
        self.assertEqual(self.by(self.build(), "tools", "pi")["locks"], "free")

    def test_project_current_lead_and_pin(self):
        key = worktree_key(self.proj)
        p = self.by(self.build(), "projects", self.proj)
        self.assertEqual((p["profile"], p["current_lead"], p["pinned"]), ("p", None, "auto"))
        lk = runner.Lock(self.state, key)
        self.assertTrue(lk.acquire())
        lk.write({"lead": "lp", "started": NOW})
        runner.write_pin(self.state, key, "lc")
        try:
            p = self.by(self.build(), "projects", self.proj)
            self.assertEqual((p["current_lead"], p["pinned"]), ("lp", "lc"))
        finally:
            os.close(lk.fd)
        stale = self.by(self.build(), "projects", self.proj)  # a leftover file with nobody holding it
        self.assertIsNone(stale["current_lead"])

    def test_health_values_are_in_the_set(self):
        reg = self.build()
        for k in ("tools", "plans", "models", "seats", "leads", "profiles", "projects"):
            for n in reg[k]:
                self.assertIn(n["health"], ("verified", "pending", "capped", "unknown"), (k, n))
                self.assertEqual(n["id"].split(":", 1)[0], k[:-1])

    def test_write_is_atomic_json(self):
        registry.write(self.state, self.build())
        with open(os.path.join(self.state, "registry.json")) as f:
            self.assertIn("generated_at", json.load(f))
        self.assertEqual([n for n in os.listdir(self.state) if n.endswith(".tmp")], [])

    def test_bw_registry_writes_and_prints(self):
        cfg = {"schema_version": 2, "plans": {"pa": {"billing": "plan", "quota": "statusline"}},
               "leads": {"la": {"runtime": "codex", "model": "m", "plan": "pa"}},
               "seats": {"s1": {"runtime": "codex", "model": "x", "plan": "pa"}},
               "profiles": {"p": {"lead_order": ["la"]}}, "default_profile": "p",
               "state_dir": self.state, "metrics_log": os.path.join(self.root, "m.jsonl")}
        cfg_path = os.path.join(self.root, "c.json")
        with open(cfg_path, "w") as f:
            json.dump(cfg, f)
        env = dict(os.environ, BALANCEWHEEL_CONFIG=cfg_path, BW_STATE_DIR=self.state)
        bw = str(Path(__file__).resolve().parent.parent / "bin" / "bw")
        r = subprocess.run([sys.executable, bw, "registry"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("wrote", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.state, "registry.json")))
        r = subprocess.run([sys.executable, bw, "registry", "--print"], env=env, capture_output=True, text=True)
        self.assertEqual(json.loads(r.stdout)["leads"][0]["name"], "la")


if __name__ == "__main__":
    unittest.main()
