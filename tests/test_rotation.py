import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead.rotation import cap_record, decide, effective_quota  # noqa: E402

NOW = 1_800_000_000.0
H = 3600.0
LEADS = {"a": {"plan": "pa"}, "b": {"plan": "pb"}, "c": {"plan": "pc"}}
PROFILE = {"lead_order": ["a", "b", "c"]}
ROT = {"return_above_pct": 50, "cap_expiry_hours": 5, "probe_every_min": 15}


def snap(used, resets_in=2 * H, observed=NOW - 60):
    return {"windows": [{"name": "five_hour", "used_pct": used, "resets_at": NOW + resets_in}], "observed_at": observed}


def cap(resets_in):
    return {"resets_at": NOW + resets_in, "window": "five_hour", "source": "statusline"}


def run(current=None, pinned=None, quota=None, caps=None, moment="start", rot=ROT):
    return decide(PROFILE, LEADS, rot, current, pinned, quota or {}, caps or {}, NOW, moment)


class Decide(unittest.TestCase):
    def test_order_when_nothing_known(self):
        d = run()
        self.assertEqual((d.lead, d.reason), ("a", "order"))

    def test_cap_midsession_switches_when_other_eligible(self):
        d = run(current="a", caps={"pa": cap(H)}, moment="running")
        self.assertEqual((d.lead, d.reason), ("b", "capped"))

    def test_cap_midsession_all_capped_stays_with_notice(self):
        caps = {"pa": cap(3 * H), "pb": cap(H), "pc": cap(2 * H)}
        d = run(current="a", caps=caps, moment="running")
        self.assertEqual((d.lead, d.reason), ("a", "none-eligible"))
        self.assertIn("earliest reset", d.notice)

    def test_low_not_capped_midsession_stays(self):
        d = run(current="a", quota={"pa": snap(95, resets_in=4 * H)}, moment="running")
        self.assertEqual((d.lead, d.reason), ("a", "stay"))

    def test_low_long_reset_at_start_skips(self):
        d = run(quota={"pa": snap(95, resets_in=4 * H)})
        self.assertEqual((d.lead, d.reason), ("b", "quota-low"))

    def test_low_short_reset_at_start_keeps(self):
        d = run(quota={"pa": snap(95, resets_in=0.5 * H)})
        self.assertEqual(d.lead, "a")

    def test_unknown_quota_is_eligible(self):
        d = run(caps={"pa": cap(H)})
        self.assertEqual(d.lead, "b")  # pb has no quota at all: unknown, eligible

    def test_return_after_reset_uses_remaining_not_used(self):
        caps = {"pa": cap(-60)}  # reset passed
        d = run(current="b", quota={"pa": snap(2, resets_in=5 * H, observed=NOW - 30)}, caps=caps, moment="boundary")
        self.assertEqual((d.lead, d.reason), ("a", "returned"))

    def test_no_return_midturn(self):
        d = run(current="b", caps={"pa": cap(-60)}, moment="running")
        self.assertEqual((d.lead, d.reason), ("b", "stay"))

    def test_return_with_unknown_quota_after_reset(self):
        d = run(current="b", caps={"pa": cap(-60)}, moment="boundary")
        self.assertEqual((d.lead, d.reason), ("a", "returned"))

    def test_no_return_while_remaining_below_threshold(self):
        d = run(current="b", quota={"pa": snap(70, resets_in=5 * H)}, caps={"pa": cap(-60)}, moment="boundary")
        self.assertEqual(d.lead, "b")

    def test_custom_return_threshold_used(self):
        rot = dict(ROT, return_above_pct=20)
        d = run(current="b", quota={"pa": snap(70, resets_in=5 * H)}, caps={"pa": cap(-60)}, moment="boundary", rot=rot)
        self.assertEqual(d.lead, "a")

    def test_stale_snapshot_after_reset_is_unknown(self):
        stale = snap(100, resets_in=-10)  # reading from before its own window reset
        self.assertIsNone(effective_quota(stale, NOW))
        self.assertIsNotNone(effective_quota(snap(10), NOW))

    def test_cap_without_reset_uses_expiry(self):
        c = cap_record(NOW, resets_at=None, expiry_hours=5)
        self.assertEqual(c["resets_at"], NOW + 5 * H)
        self.assertEqual(cap_record(NOW, resets_at=NOW + 10, expiry_hours=5)["resets_at"], NOW + 10)

    def test_recovered_pin_does_not_interrupt_running_fallback(self):
        d = run(current="a", pinned="c", moment="running")
        self.assertEqual((d.lead, d.reason), ("a", "stay"))

    def test_start_honours_return_threshold_after_cap_expired(self):
        # an expired cap is still passed in; 30% remaining is below the 50% return threshold
        d = run(quota={"pa": snap(70, resets_in=5 * H)}, caps={"pa": cap(-60)})
        self.assertEqual(d.lead, "b")

    def test_all_capped_at_start_offers_no_lead(self):
        d = run(caps={"pa": cap(H), "pb": cap(2 * H), "pc": cap(3 * H)})
        self.assertEqual(d.reason, "none-eligible")
        self.assertIsNone(d.lead)

    def test_pinned_wins(self):
        d = run(pinned="c")
        self.assertEqual((d.lead, d.reason), ("c", "pinned"))

    def test_pinned_capped_falls_through_keeps_pin(self):
        d = run(pinned="c", caps={"pc": cap(H)})
        self.assertEqual(d.lead, "a")
        self.assertIn("pinned", d.notice)



def full_pool(applies_to, resets_in=H, used=100):
    return {"name": "fableWeekly", "used_pct": used, "resets_at": NOW + resets_in, "applies_to": applies_to}


def pooled(plan, *pools):
    return {plan: {"unknown": True, "pools": list(pools)}}


SHARED = {"a": {"plan": "pa"}, "b": {"plan": "pa"}, "c": {"plan": "pc"}}


class Pools(unittest.TestCase):
    def test_full_pool_blocks_only_named_lead_at_start(self):
        d = decide(PROFILE, SHARED, ROT, None, None, pooled("pa", full_pool(["a"])), {}, NOW, "start")
        self.assertEqual((d.lead, d.reason), ("b", "pool-full"))

    def test_pool_not_full_or_past_reset_or_malformed_blocks_nobody(self):
        for p in (full_pool(["a"], used=99), full_pool(["a"], resets_in=-1), {"applies_to": ["a"]},
                  {"used_pct": 100, "applies_to": "a", "resets_at": NOW + H}, "junk",
                  {"used_pct": 100, "resets_at": None, "applies_to": ["a"]}):
            d = decide(PROFILE, SHARED, ROT, None, None, pooled("pa", p), {}, NOW, "start")
            self.assertEqual((d.lead, d.reason), ("a", "order"), p)

    def test_pool_alone_never_switches_mid_session(self):
        d = run(current="a", quota=pooled("pa", full_pool(["a"])), moment="running")
        self.assertEqual((d.lead, d.reason), ("a", "stay"))

    def test_cap_switch_skips_pool_full_candidate(self):
        d = run(current="a", quota=pooled("pb", full_pool(["b"])), caps={"pa": cap(H)}, moment="running")
        self.assertEqual((d.lead, d.reason), ("c", "capped"))

    def test_cap_switch_with_every_candidate_pool_full_stays(self):
        q = {**pooled("pb", full_pool(["b"])), **pooled("pc", full_pool(["c"]))}
        d = run(current="a", quota=q, caps={"pa": cap(H)}, moment="running")
        self.assertEqual((d.lead, d.reason), ("a", "none-eligible"))

    def test_boundary_leaves_a_pool_full_current(self):
        d = run(current="b", quota=pooled("pb", full_pool(["b"])), caps={"pa": cap(H)}, moment="boundary")
        self.assertEqual((d.lead, d.reason), ("c", "pool-full"))

    def test_boundary_does_not_return_to_pool_full_lead(self):
        d = run(current="b", quota=pooled("pa", full_pool(["a"])), caps={"pa": cap(-60)}, moment="boundary")
        self.assertEqual((d.lead, d.reason), ("b", "stay"))

    def test_pinned_pool_full_falls_through_with_notice(self):
        d = run(pinned="c", quota=pooled("pc", full_pool(["c"])))
        self.assertEqual(d.lead, "a")
        self.assertIn("pinned lead c", d.notice)

    def test_every_lead_pool_full_starts_nobody(self):
        q = pooled("pa", full_pool(["a", "b"]))
        q.update(pooled("pc", full_pool(["c"], resets_in=2 * H)))
        d = decide(PROFILE, SHARED, ROT, None, None, q, {}, NOW, "start")
        self.assertEqual((d.lead, d.reason), (None, "none-eligible"))
        self.assertIn("full quota pool", d.notice)
        self.assertIn(f"{NOW + H:.0f}", d.notice)


if __name__ == "__main__":
    unittest.main()
