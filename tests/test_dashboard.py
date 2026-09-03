import json, os, sys, tempfile, unittest
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics"))
import config as cfg  # noqa: E402
import dashboard  # noqa: E402
import store  # noqa: E402

FX = HERE / "fixtures"
CONFIG = cfg.load_config(str(FX / "config.json"))
SECTIONS = ("now", "roster", "timeline", "disputes", "findings", "watches")


def svgs(html):
    out = []
    i = 0
    while True:
        s = html.find("<svg", i)
        if s < 0:
            return out
        close = html.find("</svg>", s)
        if close < 0:
            raise AssertionError("unterminated <svg> element")
        e = close + len("</svg>")
        out.append(html[s:e]); i = e


class Render(unittest.TestCase):
    def test_all_sections_present_and_svg_is_well_formed(self):
        html = dashboard.render(store.load(str(FX / "modern.jsonl")), CONFIG, None)
        for s in SECTIONS:
            self.assertIn(f'<section id="{s}"', html)
        self.assertGreaterEqual(len(svgs(html)), 2)
        for svg in svgs(html):
            ET.fromstring(svg)  # raises on malformed markup

    def test_empty_log_and_one_panel_log_render(self):
        html = dashboard.render(store.load("/nonexistent.jsonl"), CONFIG, None)
        for s in SECTIONS:
            self.assertIn(f'<section id="{s}"', html)
        self.assertIn("no panels logged yet", html)
        d = store.load(str(FX / "modern.jsonl")); d["panels"] = d["panels"][:1]
        html = dashboard.render(d, CONFIG, None)
        for svg in svgs(html):
            ET.fromstring(svg)

    def test_model_supplied_text_is_escaped_and_control_chars_dropped(self):
        d = store.load(str(FX / "modern.jsonl"))
        d["panels"][0]["findings_detail"][0]["claim"] = "<script>alert(1)</script>"
        d["panels"][0]["target"] = "x <b>bold</b>\ud800"
        html = dashboard.render(d, CONFIG, None)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<b>bold</b>", html)
        self.assertNotIn("", html); self.assertNotIn("\ud800", html)
        for svg in svgs(html):
            ET.fromstring(svg)
        html.encode("utf-8")  # no lone surrogates survive

    def test_model_change_guide_stacked_counts_and_amendment_badge(self):
        d = store.load(str(FX / "modern.jsonl"))
        html = dashboard.render(d, CONFIG, None)
        self.assertIn("model change: beta", html)    # beta's model changed between p1 and p2
        self.assertIn('class="stack confirmed"', html)
        self.assertIn('class="stack refuted"', html)
        self.assertIn("amended 1×", html)            # p1 carries one panel-amend

    def test_timeline_uses_real_dates_with_irregular_spacing(self):
        xs = dashboard.timeline_positions(["2026-01-02T00:00:00Z", "2026-01-05T00:00:00Z", "2026-01-12T00:00:00Z"], width=300)
        self.assertEqual(xs[0], 0.0); self.assertEqual(xs[-1], 300.0)
        self.assertAlmostEqual(xs[1], 90.0, places=3)  # 3 of 10 days
        self.assertEqual(dashboard.timeline_positions(["2026-01-02T00:00:00Z"], width=300), [0.0])
        # unparseable timestamps are pinned to the left edge, not 'now' (deterministic renders)
        self.assertEqual(dashboard.timeline_positions([None, "2026-01-02T00:00:00Z", "2026-01-12T00:00:00Z"], width=300)[0], 0.0)
        self.assertEqual(dashboard.timeline_positions(["garbage"], width=300), [0.0])

    def test_circle_radius_is_sqrt_of_count(self):
        self.assertAlmostEqual(dashboard.radius_for(16) / dashboard.radius_for(4), 2.0)

    def test_right_now_reports_open_watches_freshness_and_verification(self):
        d = store.load(str(FX / "modern.jsonl"))
        state = {"alpha": {"verified": True, "runtime_version": "1.0"}, "beta": {"verified": False, "failed_probe": "redirect"}}
        html = dashboard.render(d, CONFIG, state)
        self.assertIn("1 verified", html); self.assertIn("1 unverified", html)
        self.assertIn("0 open", html)
        self.assertIn("log freshness", html)

    def test_roster_meter_states_and_usage_share(self):
        d = store.load(str(FX / "modern.jsonl"))
        html = dashboard.render(d, CONFIG, None)
        self.assertIn("no unique recorded", html)   # a seat with no unique recorded in window
        self.assertIn("usage share", html)
        self.assertIn("(67%)", html)                 # beta: 2 of 3 usage events

    def test_timeline_height_grows_with_roster(self):
        d = store.load(str(FX / "modern.jsonl"))
        big = dict(CONFIG, seats={f"s{i}": {"runtime": "pi", "model": "m", "roles": []} for i in range(12)})
        html = dashboard.render(d, big, None)
        self.assertIn('height="220"', html)          # 136 + 6 * (12 configured + alpha and beta from the log)

    def test_main_out_without_directory_component(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.environ["BALANCEWHEEL_METRICS_LOG"] = str(FX / "modern.jsonl")
            os.environ["BALANCEWHEEL_CONFIG"] = str(FX / "config.json")
            try:
                self.assertTrue(Path(dashboard.main(["--out", "d.html"])).exists())
            finally:
                del os.environ["BALANCEWHEEL_METRICS_LOG"]; del os.environ["BALANCEWHEEL_CONFIG"]; os.chdir(cwd)


class Main(unittest.TestCase):
    def test_main_writes_file_into_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["BALANCEWHEEL_METRICS_LOG"] = str(FX / "modern.jsonl")
            os.environ["BALANCEWHEEL_CONFIG"] = str(FX / "config.json")
            try:
                out = dashboard.main(["--state-dir", tmp])
            finally:
                del os.environ["BALANCEWHEEL_METRICS_LOG"]; del os.environ["BALANCEWHEEL_CONFIG"]
            self.assertTrue(Path(out).exists())
            self.assertIn("<title>", Path(out).read_text())


if __name__ == "__main__":
    unittest.main()
