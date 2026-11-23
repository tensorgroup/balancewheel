"""Tests for docs/usage-map/check_stories.py: the stories on the Who tab point only at real
places, people, stories and map boxes."""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAP = ROOT / "docs" / "usage-map"
_spec = importlib.util.spec_from_file_location("check_stories", MAP / "check_stories.py")
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


def valid():
    return {
        "why": {"lead": "x", "cards": [{"id": "c1", "title": "t", "text": "x", "stories": ["s1"]}]},
        "org": {"name": "Windup Workshop", "teams": [{"id": "t1", "name": "Team"}],
                "people": [{"id": "p1", "name": "P", "team": "t1", "role": "r", "personas": ["you"], "stories": ["s1"]}]},
        "places": [{"id": "a", "title": "A", "kind": "machine", "owner": "p1", "node": "you"},
                   {"id": "b", "title": "B", "kind": "repo", "owner": "t1"}],
        "stories": [{"id": "s1", "person": "p1", "title": "S", "why": "w",
                     "steps": [{"place": "a", "label": "L", "text": "x", "node": "cmd:lead", "truth": "b",
                                "moves": [{"from": "a", "to": "b", "what": "w", "kind": "share"}]}]}],
    }


IDS = {"root", "you", "cmd:lead"}


class LoadTest(unittest.TestCase):
    def test_load_js_json_strips_wrapper(self):
        self.assertEqual(cs.load_js_json('// c\nwindow.X = {"a": 1};\n', "X"), {"a": 1})

    def test_map_ids_collects_generated_and_infra(self):
        gen = {"t": {"groups": [{"id": "g", "items": [{"id": "cmd:lead"}]}]}}
        ids = cs.map_ids(gen, "{ id: 'you', x: 1 }")
        self.assertTrue({"g", "cmd:lead", "you", "root"} <= ids)


class CheckTest(unittest.TestCase):
    def one_error(self, data, needle):
        errs = cs.check(data, IDS)
        self.assertEqual(len(errs), 1, errs)
        self.assertIn(needle, errs[0])

    def test_valid_stories_pass(self):
        self.assertEqual(cs.check(valid(), IDS), [])

    def test_unknown_place(self):
        d = valid(); d["stories"][0]["steps"][0]["place"] = "nowhere"
        self.one_error(d, "nowhere")

    def test_unknown_move_end(self):
        d = valid(); d["stories"][0]["steps"][0]["moves"][0]["to"] = "zz"
        self.one_error(d, "zz")

    def test_unknown_move_kind(self):
        d = valid(); d["stories"][0]["steps"][0]["moves"][0]["kind"] = "teleport"
        self.one_error(d, "teleport")

    def test_unknown_node(self):
        d = valid(); d["stories"][0]["steps"][0]["node"] = "cmd:gone"
        self.one_error(d, "cmd:gone")

    def test_unknown_place_node(self):
        d = valid(); d["places"][0]["node"] = "gone"
        self.one_error(d, "gone")

    def test_unknown_person(self):
        d = valid(); d["stories"][0]["person"] = "ghost"
        self.one_error(d, "ghost")

    def test_unknown_card_story(self):
        d = valid(); d["why"]["cards"][0]["stories"] = ["s9"]
        self.one_error(d, "s9")

    def test_unknown_person_story(self):
        d = valid(); d["org"]["people"][0]["stories"] = ["s1", "s9"]
        self.one_error(d, "s9")

    def test_unknown_truth(self):
        d = valid(); d["stories"][0]["steps"][0]["truth"] = "qq"
        self.one_error(d, "qq")

    def test_person_team_unknown(self):
        d = valid(); d["org"]["people"][0]["team"] = "t9"
        self.one_error(d, "t9")

    def test_unknown_owner(self):
        d = valid(); d["places"][1]["owner"] = "o9"
        self.one_error(d, "o9")

    def test_bad_story_id(self):
        d = valid(); d["stories"][0]["id"] = "a/b"
        d["why"]["cards"][0]["stories"] = ["a/b"]; d["org"]["people"][0]["stories"] = ["a/b"]
        self.one_error(d, "a/b")

    def test_story_without_steps(self):
        d = valid(); d["stories"][0]["steps"] = []
        self.one_error(d, "no steps")

    def test_unknown_status(self):
        d = valid(); d["stories"][0]["steps"][0]["status"] = "planed"
        self.one_error(d, "planed")

    def test_unknown_place_kind(self):
        d = valid(); d["places"][0]["kind"] = "cloud"
        self.one_error(d, "cloud")

    def test_step_on_planned_box_must_be_planned(self):
        errs = cs.check(valid(), IDS, planned={"cmd:lead"})
        self.assertEqual(len(errs), 1); self.assertIn("planned", errs[0])
        d = valid(); d["stories"][0]["steps"][0]["status"] = "planned"
        self.assertEqual(cs.check(d, IDS, planned={"cmd:lead"}), [])

    def test_persona_must_be_a_persona_box(self):
        errs = cs.check(valid(), IDS, personas={"lead"})
        self.assertEqual(len(errs), 1); self.assertIn("you", errs[0])

    def test_malformed_entries_each_get_a_message(self):
        d = valid(); del d["stories"][0]["id"]
        d["places"].append("oops")
        errs = cs.check(d, IDS)
        self.assertTrue(any("needs a id" in e for e in errs), errs)
        self.assertTrue(any("places entry 3: must be an object" in e for e in errs), errs)

    def test_moves_must_be_a_list(self):
        d = valid(); d["stories"][0]["steps"][0]["moves"] = {}
        self.one_error(d, "must be a list")

    def test_null_step_is_an_error(self):
        d = valid(); d["stories"][0]["steps"].append(None)
        self.one_error(d, "must be an object")

    def test_required_names(self):
        for path, needle in ((("org", "people", 0), "name"), (("stories", 0), "title"), (("places", 0), "title")):
            d = valid(); obj = d[path[0]] if len(path) == 2 else d[path[0]][path[1]]
            del obj[path[-1]][needle]
            self.one_error(d, f"needs a {needle}")

    def test_move_needs_what(self):
        d = valid(); del d["stories"][0]["steps"][0]["moves"][0]["what"]
        self.one_error(d, "needs a what")

    def test_duplicate_place_id(self):
        d = valid(); d["places"].append(dict(d["places"][1]))
        self.one_error(d, "duplicate place")

    def test_story_not_listed_by_person(self):
        d = valid(); d["org"]["people"][0]["stories"] = []
        self.one_error(d, "does not list it")

    def test_step_in_planned_place_must_be_planned(self):
        d = valid(); d["places"][0]["status"] = "planned"
        self.one_error(d, "place 'a' is planned")

    def test_infra_marks(self):
        personas, planned = cs.infra_marks("{ id: 'you', kind: 'persona' },\n{ id: 'x', status: 'planned' }")
        self.assertEqual((personas, planned), ({"you"}, {"x"}))

    def test_step_needs_label(self):
        d = valid(); del d["stories"][0]["steps"][0]["label"]
        self.one_error(d, "label")

    def test_duplicate_story_id(self):
        d = valid(); d["stories"].append(dict(d["stories"][0]))
        self.one_error(d, "duplicate")


class RealDataTest(unittest.TestCase):
    def test_real_data_is_valid(self):
        self.assertEqual(cs.main(quiet=True), 0)

    def test_real_planned_boxes_are_seen(self):
        # infra_marks reads id and status from one line; a reformat must not hide them.
        _, planned = cs.infra_marks((MAP / "infra.js").read_text(encoding="utf-8"))
        self.assertTrue({"seatsgen", "paneltpl", "packsexport", "guardacting"} <= planned, planned)


if __name__ == "__main__":
    unittest.main()
