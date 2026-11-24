"""`bw start`'s guard review offer: when it is due, who sees it, and what a yes runs. Hermetic."""
import io, os, random, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from lead.guard import gate, offer, record, review  # noqa: E402

DAY = 86400
NOW = 1_800_000_000.0


class Tty(io.StringIO):
    def __init__(self, text="", tty=True):
        super().__init__(text)
        self.tty = tty

    def isatty(self):
        return self.tty


def cfg(mode="auto"):
    return {"profiles": {"p": {"lead_order": ["open", "lg"], "protected_branches": ["main"], "deploy_commands": []}},
            "leads": {"open": {"runtime": "kimi", "autonomy": "bypass", "autonomy_source": "config"},
                      "lg": {"runtime": "pi", "autonomy": "guard", "autonomy_source": "config"}},
            "guard": {"mode": {"p": mode}, "model": "test/judge"}}


class OfferCase(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)
        self.config = cfg()
        self.key = gate.current_key(self.config, "p", "pi")

    def seed(self, stops=3, allows=3, marked=0, key=None):
        fields = dict(zip(record.EVIDENCE_KEY, key or self.key))
        n = 0
        for kind, count in (("ask", stops), ("allow", allows)):
            for i in range(count):
                n += 1
                record.append(self.state, {"type": "decision", "id": f"{kind}{i}", **fields, "decision": kind,
                                           "source": "rule", "rule_decision": kind, "self_test": False,
                                           "mode": "shadow", "call": f"cmd {kind}{i}"})
        for i in range(marked):
            record.append(self.state, {"type": "mark", "decision_id": f"ask{i}", "verdict": "right",
                                       "sampled": False, "ts": NOW})

    def maybe(self, text="\n", config=None, tty=True, now=NOW):
        stdin, out = Tty(text, tty), Tty(tty=tty)
        offer.maybe_offer(config or self.config, self.state, "p", stdin, out, now, rng=random.Random(1))
        return out.getvalue()

    def marks(self):
        return [e for e in record.load(self.state)["decisions"] if "mark" in e]

    def test_offer_only_on_a_tty(self):
        self.seed()
        self.assertEqual(self.maybe("y\n", tty=False), "")
        self.assertIsNone(offer.last_offer(self.state, "p"))

    def test_enter_skips_and_records_the_day(self):
        self.seed()
        out = self.maybe("\n")
        self.assertIn("review 5 guard decisions now? [y/N] ", out)
        self.assertNotIn("[r]ight", out)
        self.assertEqual(self.marks(), [])
        self.assertEqual(offer.last_offer(self.state, "p"), NOW)

    def test_eof_skips(self):
        self.seed()
        self.assertNotIn("[r]ight", self.maybe(""))

    def test_once_a_day_under_the_counts(self):
        self.seed()
        self.assertIn("review", self.maybe("\n", now=NOW))
        self.assertEqual(self.maybe("\n", now=NOW + DAY - 1), "")
        self.assertIn("review", self.maybe("\n", now=NOW + DAY))

    def test_not_offered_when_counts_met_and_not_acting(self):
        s = {"reviewed": 50, "sampled_allows": 20}
        self.seed()
        self.assertEqual(offer.due(self.config, self.state, "p", "pi", s, "shadow", 5, NOW), 0)
        s = {"reviewed": 49, "sampled_allows": 20}
        self.assertEqual(offer.due(self.config, self.state, "p", "pi", s, "shadow", 5, NOW), 5)
        s = {"reviewed": 50, "sampled_allows": 19}
        self.assertEqual(offer.due(self.config, self.state, "p", "pi", s, "shadow", 5, NOW), 5)

    def test_three_a_week_after_promotion_with_at_least_two_stops(self):
        s = {"reviewed": 90, "sampled_allows": 40}
        self.assertEqual(offer.due(self.config, self.state, "p", "pi", s, "acting", 5, NOW), 3)
        offer.record_offer(self.state, "p", NOW)
        self.assertEqual(offer.due(self.config, self.state, "p", "pi", s, "acting", 5, NOW + 7 * DAY - 1), 0)
        self.assertEqual(offer.due(self.config, self.state, "p", "pi", s, "acting", 5, NOW + 7 * DAY), 3)
        self.seed(stops=6, allows=6)
        got = review.offer_pick(record.load(self.state)["decisions"], self.key, 3, True, random.Random(1))
        self.assertEqual(sorted(bool(d.get("sampled")) for d in got), [False, False, True])

    def test_before_promotion_a_quarter_are_allows(self):
        self.seed(stops=6, allows=6)
        got = review.offer_pick(record.load(self.state)["decisions"], self.key, 5, False, random.Random(1))
        self.assertEqual(len(got), 5)
        self.assertEqual(sum(1 for d in got if d.get("sampled")), 1)

    def test_a_short_pool_is_filled_from_the_other_side(self):
        self.seed(stops=1, allows=6)
        ds = record.load(self.state)["decisions"]
        self.assertEqual(len(review.offer_pick(ds, self.key, 5, False, random.Random(1))), 5)
        self.assertEqual(len(review.offer_pick(ds, self.key, 3, True, random.Random(1))), 3)

    def test_y_runs_the_review_on_the_offered_items(self):
        self.seed(stops=4, allows=4)
        out = self.maybe("y\nr\nw\ns\nr\nr\n")
        self.assertIn("[r]ight", out)
        marks = self.marks()
        self.assertEqual(len(marks), 4)  # one of the five was skipped
        self.assertEqual(sum(1 for d in marks if d["mark"]["sampled"]), 1)
        self.assertEqual(offer.last_offer(self.state, "p"), NOW)

    def test_no_offer_when_nothing_is_unmarked(self):
        self.seed(stops=2, allows=0, marked=2)
        self.assertEqual(self.maybe("y\n"), "")
        self.assertIsNone(offer.last_offer(self.state, "p"))  # the day's slot is not spent

    def test_no_offer_for_a_shadow_pin(self):
        self.seed()
        self.assertEqual(self.maybe("y\n", config=cfg("shadow")), "")

    def test_no_guarded_lead_no_offer(self):
        self.seed()
        config = cfg()
        config["leads"]["lg"]["autonomy"] = "bypass"
        self.assertEqual(self.maybe("y\n", config=config), "")

    def test_offered_review_uses_the_gate_key(self):
        newer = ("p", "pi", "other-rules", "d", None, None)  # the most recent key in the log, not the gate's
        self.seed()
        self.seed(stops=1, allows=0, key=newer)
        self.assertNotEqual(review.select_key(record.load(self.state)["decisions"]), self.key)
        out = self.maybe("y\nq\n")
        self.assertIn(f"reviewing {review.key_label(self.key)}", out)
        self.assertIn(f"summary for {review.key_label(self.key)}", out)
        self.assertNotIn("other-rules", out)


if __name__ == "__main__":
    unittest.main()
