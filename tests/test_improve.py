import json, os, sys, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "metrics"))
import store  # noqa: E402
from lead import improve, inbox  # noqa: E402
from lead.improve import _when  # noqa: E402

NOW = 2_000_000_000.0
TS = "2033-05-18T00:00:00Z"  # just before NOW
OLD = "2020-01-01T00:00:00Z"
STOPPED = {"strong_case": 0.5, "only_person_knows": 0.1, "outward_or_irreversible": 0.1, "high_stakes": 0.1}


def data(**kw):
    return {"events": [], "sessions": [], "leads": [], "ratings": [], "panels": [], **kw}


def ask(i, answer=None, frm="user", reversed_=False, **kw):
    item = {"type": "ask", "id": i, "ts": NOW - 100, "text": "q", "status": "open", **kw}
    if answer is not None:
        item.update(status="answered", answer={"text": answer, "from": frm, "ts": NOW - 50, "shadow": False})
    if reversed_:
        item["reversed"] = True
    return item


def triage_ev(i, verdict="ask", reason="strong_case", failed=False, scores=STOPPED, ts=TS):
    return {"type": "triage", "id": i, "verdict": verdict, "reason": reason, "failed": failed, "scores": scores, "ts": ts}


def keys(d=None, items=(), **kw):
    return [s["key"] for s in improve.suggestions(d or data(), list(items), NOW, **kw)]


class Rules(unittest.TestCase):
    def test_nothing_to_say_on_no_data(self):
        self.assertEqual(keys(), [])

    def test_reversals_tighten_only_past_the_minimum_sample(self):
        items = [ask(str(i), "ok", "triage") for i in range(3)] + [ask("r", "no", reversed_=True)]
        self.assertEqual(keys(items=items), [])
        items += [ask("x", "ok", "triage")]
        (s,) = improve.suggestions(data(), items, NOW)
        self.assertEqual(s["key"], "triage-reversed")
        self.assertIn("1 of 5", s["evidence"])

    def test_rubber_stamped_questions_loosen_by_the_gate_that_stopped_them(self):
        items = [ask(str(i), "yes, go ahead") for i in range(5)]
        evs = [triage_ev(str(i)) for i in range(5)]
        (s,) = improve.suggestions(data(events=evs), items, NOW)
        self.assertEqual(s["key"], "triage-rubber-stamped:strong_case")
        self.assertIn("5 of 5", s["evidence"])

    def test_real_answers_are_not_rubber_stamps(self):
        items = [ask(str(i), "no, use the other table because of the export") for i in range(5)]
        self.assertEqual(keys(data(events=[triage_ev(str(i)) for i in range(5)]), items), [])

    def test_agreement_never_argues_for_weakening_a_safety_gate(self):
        for gate in ("outward_or_irreversible", "high_stakes"):
            scores = {**STOPPED, "strong_case": 0.9, gate: 0.31}
            items = [ask(str(i), "yes") for i in range(5)]
            evs = [triage_ev(str(i), reason=gate, scores=scores) for i in range(5)]
            self.assertEqual(keys(data(events=evs), items), [])

    def test_a_question_stopped_by_two_gates_is_not_evidence_for_either(self):
        scores = {**STOPPED, "only_person_knows": 0.9}
        items = [ask(str(i), "yes") for i in range(5)]
        evs = [triage_ev(str(i), scores=scores) for i in range(5)]
        self.assertEqual(keys(data(events=evs), items), [])

    def test_the_configured_thresholds_are_the_ones_used(self):
        items = [ask(str(i), "yes") for i in range(5)]
        evs = [triage_ev(str(i)) for i in range(5)]
        th = {**improve.triage.DEFAULT_THRESHOLDS, "strong_case": 0.4}
        self.assertEqual(keys(data(events=evs), items, thresholds=th), [])

    def test_a_denylist_reason_is_never_suggested_loose(self):
        items = [ask(str(i), "yes") for i in range(5)]
        evs = [triage_ev(str(i), reason="denylist", scores=None) for i in range(5)]
        self.assertEqual(keys(data(events=evs), items), [])

    def test_shadow_agreement_says_turn_it_on(self):
        items = [ask(str(i), "yes", triage={"verdict": "decide"}) for i in range(5)]
        (s,) = improve.suggestions(data(), items, NOW)
        self.assertEqual(s["key"], "triage-shadow")
        self.assertIn("acting", s["action"])

    def test_shadow_disagreement_says_tighten(self):
        items = [ask(str(i), "no, the other one please, it matters", triage={"verdict": "decide"}) for i in range(5)]
        (s,) = improve.suggestions(data(), items, NOW)
        self.assertIn("tighten", s["action"])

    def test_a_shadow_reversal_is_not_a_reversal(self):
        items = [ask(str(i), "no") for i in range(5)]
        self.assertEqual(keys(items=items), [])

    def test_leads_skipping_the_recommendation_line(self):
        evs = [triage_ev(str(i), reason="no recommendation", scores=None) for i in range(5)]
        self.assertEqual(keys(data(events=evs)), ["ask-no-recommendation"])

    def test_triage_failures(self):
        evs = [triage_ev(str(i), reason="key unset", failed=True, scores=None) for i in range(3)]
        self.assertEqual(keys(data(events=evs)), ["triage-failing"])

    def test_poor_ratings_count_each_session_once(self):
        sessions = [{"session_id": str(i), "lead": "astra"} for i in range(3)]
        ratings = [{"session_id": str(i), "value": "bad", "ts": TS} for i in range(3)]
        self.assertEqual(keys(data(sessions=sessions, ratings=ratings)), ["lead-rated-poorly:astra"])
        one = [{"session_id": "0", "value": "bad", "ts": TS} for _ in range(3)]
        self.assertEqual(keys(data(sessions=sessions, ratings=one)), [])

    def test_repeated_caps_and_overrides(self):
        leads = [{"reason": "capped", "from": {"lead": "claude"}, "to": {"lead": "astra"}, "ts": TS} for _ in range(3)]
        leads += [{"reason": "override", "from": {"lead": "claude"}, "to": {"lead": "pi"}, "ts": TS} for _ in range(3)]
        self.assertEqual(sorted(keys(data(leads=leads))), ["lead-capped:claude", "lead-forced:pi"])

    def test_old_evidence_does_not_count(self):
        leads = [{"reason": "capped", "from": {"lead": "claude"}, "ts": OLD} for _ in range(3)]
        self.assertEqual(keys(data(leads=leads)), [])

    def test_a_seat_absent_on_real_logged_panels(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "m.jsonl")
            for n in range(3):
                store.append(log, {"type": "panel", "ts": f"2033-05-1{n}T00:00:00Z", "target": "t", "seats": ["a"],
                                   "absent": {"gem": "usage cap"}})
            self.assertEqual(keys(store.load(log)), ["seat-absent:gem"])

    def test_only_recent_panels_count(self):
        panels = [{"absent": {"gem": "x"}, "ts": TS} for _ in range(3)] + [{"absent": {}, "ts": TS} for _ in range(5)]
        self.assertEqual(keys(data(panels=panels)), [])

    def test_notes_show_who_wrote_them(self):
        notes = [{"id": "n1", "text": "the pop-up came too late", "from": "astra", "ts": NOW}]
        (s,) = improve.suggestions(data(), [], NOW, notes=notes)
        self.assertEqual(s["key"], "note:n1")
        self.assertIn("astra noted: the pop-up came too late", s["evidence"])

    def test_a_note_with_no_id_is_skipped(self):
        self.assertEqual(keys(notes=[{"text": "x"}]), [])

    def test_done_stays_gone_until_there_is_new_evidence(self):
        evs = [triage_ev(str(i), reason="key unset", failed=True, scores=None) for i in range(3)]
        done = {"triage-failing": _when(TS) + 1}
        self.assertEqual(keys(data(events=evs), done=done), [])
        newer = [triage_ev(f"n{i}", reason="key unset", failed=True, scores=None, ts=NOW - 1) for i in range(3)]
        self.assertEqual(keys(data(events=evs + newer), done=done), ["triage-failing"])

    def test_a_done_note_never_returns(self):
        notes = [{"id": "n1", "text": "x", "ts": NOW}]
        self.assertEqual(keys(notes=notes, done={"note:n1": 1}), [])


class Stamp(unittest.TestCase):
    def test_short_agreement_counts(self):
        for t in ("yes", "ok", "OK.", "go ahead", "sounds good", "agree", "looks good, do it", "recommended", "go with your recommendation"):
            self.assertTrue(improve.rubber_stamp(t), t)

    def test_anything_else_does_not(self):
        for t in ("no", "yes but use the other one", "", "ok, however rename it first and keep the old name working"):
            self.assertFalse(improve.rubber_stamp(t), t)


class Notes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = self.tmp.name + "/s"
        self.asks = Path(inbox.asks_dir(self.state, "k")); self.asks.mkdir(parents=True)

    def test_a_published_note_is_not_a_question(self):
        (self.asks / "n.ask").write_text("@improve\nThe user keeps telling me to stop asking about naming.")
        recs, _ = inbox.ingest(self.state, "k", "/w", "la", "s1")
        self.assertEqual(recs, [])
        self.assertEqual(inbox.load(self.state)["items"], [])
        (n,) = inbox.notes(self.state)
        self.assertIn("naming", n["text"])
        self.assertEqual((n["from"], n["project"]), ("la", "k"))
        self.assertEqual(list(self.asks.iterdir()), [])

    def test_a_note_on_the_header_line_is_still_a_note(self):
        (self.asks / "n.ask").write_text("@improve the nudge fires too often")
        self.assertEqual(inbox.ingest(self.state, "k", "/w", "la", "s1")[0], [])
        self.assertEqual(inbox.notes(self.state)[0]["text"], "the nudge fires too often")

    def test_notes_are_held_to_the_session_limit_across_drains(self):
        for drain in range(2):
            for n in range(inbox.MAX_ASKS_PER_SESSION):
                (self.asks / f"n{drain}-{n}.ask").write_text(f"@improve note {drain} {n}")
            inbox.ingest(self.state, "k", "/w", "la", "s1")
        self.assertEqual(len(inbox.notes(self.state)), inbox.MAX_ASKS_PER_SESSION)

    def test_answering_a_shadow_item_is_not_a_reversal(self):
        (self.asks / "a.ask").write_text("q?")
        (r,) = inbox.ingest(self.state, "k", "/w", "la", "s1")[0]
        inbox.triage_answer(self.state, r["id"], {}, shadow=True)
        inbox.answer(self.state, r["id"], "yes")
        self.assertNotIn("reversed", inbox.load(self.state)["items"][0])

    def test_an_empty_note_is_dropped(self):
        (self.asks / "n.ask").write_text("@improve\n   \n")
        inbox.ingest(self.state, "k", "/w", "la", "s1")
        self.assertEqual(inbox.notes(self.state), [])

    def test_a_note_can_be_added_by_the_user(self):
        inbox.note(self.state, None, "/w", "user", "s", "too many nudges", "abc")
        self.assertEqual(inbox.notes(self.state)[0]["text"], "too many nudges")

    def test_a_superseded_decision_is_marked_reversed(self):
        (self.asks / "a.ask").write_text("q?")
        (r,) = inbox.ingest(self.state, "k", "/w", "la", "s1")[0]
        inbox.triage_answer(self.state, r["id"], {})
        inbox.answer(self.state, r["id"], "no")
        self.assertTrue(inbox.load(self.state)["items"][0]["reversed"])


class State(unittest.TestCase):
    def test_done_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(improve.load_done(d), {})
            improve.mark_done(d, "seat-absent:gem", now=5)
            self.assertEqual(improve.load_done(d), {"seat-absent:gem": 5})
            Path(d, improve.DONE_FILE).write_text("junk")
            self.assertEqual(improve.load_done(d), {})
            Path(d, improve.DONE_FILE).write_text('{"a": "x", "b": 3}')
            self.assertEqual(improve.load_done(d), {"b": 3})
