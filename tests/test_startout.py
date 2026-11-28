import io
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead.startout import Report  # noqa: E402


def report(tty, width=80, home="/home/u"):
    out = io.StringIO()
    return Report(out, tty=tty, home=home, width=width), out


class Plain(unittest.TestCase):
    def test_every_line_has_the_prefix_and_no_marks_or_headers(self):
        r, out = report(False)
        r.header("/home/u/app")
        r.ok("Checks", "pi sandbox: passed")
        r.bad("Checks", "pi guard: failed")
        r.wait("Checks", "testing. Please wait.")
        r.line("Updates", "pi: current")
        r.say("refusing: x")
        self.assertEqual(out.getvalue().splitlines(),
                         ["bw start: pi sandbox: passed", "bw start: pi guard: failed", "bw start: testing. Please wait.",
                          "bw start: pi: current", "bw start: refusing: x"])

    def test_labels_rows_and_details_keep_their_old_shape(self):
        r, out = report(False)
        r.line("Lead", "lp (order)", label="lead")
        r.rows("Quota", [("pp", "63% left"), ("pcc", "unknown")], "quota")
        r.detail("Lead", "#!/bin/sh")
        self.assertEqual(out.getvalue().splitlines(),
                         ["bw start: lead: lp (order)", "bw start: quota pp: 63% left", "bw start: quota pcc: unknown",
                          "bw start:   #!/bin/sh"])

    def test_paths_stay_absolute_in_plain_text(self):
        r, out = report(False)
        r.line("Also", "registry: /home/u/state/registry.json")
        self.assertIn("/home/u/state/registry.json", out.getvalue())


class DryRunHeader(unittest.TestCase):
    def test_both_forms_say_nothing_is_run(self):
        r, out = report(True)
        r.header("/home/u/app", dry_profile="p")
        self.assertEqual(out.getvalue(), "bw start · ~/app\n  dry run (profile p): nothing is run or written\n")
        r, out = report(False)
        r.header("/home/u/app", dry_profile="p")
        self.assertEqual(out.getvalue(), "bw start: dry run for /home/u/app (profile p); nothing is run or written\n")
        r, out = report(False)
        r.header("/home/u/app")
        self.assertEqual(out.getvalue(), "")


class Terminal(unittest.TestCase):
    def test_header_sections_and_marks(self):
        r, out = report(True)
        r.header("/home/u/code/app")
        r.line("Updates", "skipped: nothing to do")
        r.wait("Checks", "testing.")
        r.ok("Checks", "pi sandbox: passed")
        r.bad("Checks", "pi guard: failed")
        r.line("Lead", "pinned", label="lead")
        self.assertEqual(out.getvalue(),
                         "bw start · ~/code/app\n"
                         "\nUpdates\n  · skipped: nothing to do\n"
                         "\nChecks\n  … testing.\n  ✓ pi sandbox: passed\n  ✗ pi guard: failed\n"
                         "\nLead\n  · pinned\n")

    def test_a_raw_line_keeps_its_absolute_paths_so_the_command_can_be_pasted(self):
        r, out = report(True)
        r.line("Lead", "would start the lead here: env X=/home/u/c /home/u/bw lead", raw=True)
        r.bad("Lead", "run it yourself: cd '/home/u/app' && x", raw=True)
        r.line("Lead", "log /home/u/l")
        self.assertEqual(out.getvalue().splitlines()[-3:],
                         ["  · would start the lead here: env X=/home/u/c /home/u/bw lead",
                          "  ✗ run it yourself: cd '/home/u/app' && x", "  · log ~/l"])

    def test_tilde_only_replaces_a_whole_home_path(self):
        r, _ = report(True)
        self.assertEqual(r.tilde("log: /home/u/.local/x and /home/user/y and /home/u"), "log: ~/.local/x and /home/user/y and ~")
        self.assertEqual(r.tilde("(/home/u/a)"), "(~/a)")
        self.assertEqual(r.tilde("nothing here"), "nothing here")

    def test_long_lines_wrap_with_a_hanging_indent_and_paths_stay_whole(self):
        r, out = report(True, width=60)
        r.wait("Checks", "testing the pi lead's sandbox and guard. This takes about a minute and uses a little model quota. "
                         "Please wait. /home/u/a/very/long/path/that/does/not/fit/in/the/line/at/all")
        lines = out.getvalue().splitlines()[2:]
        self.assertTrue(lines[0].startswith("  … testing"))
        self.assertTrue(all(ln.startswith("    ") for ln in lines[1:]), lines)
        self.assertIn("~/a/very/long/path/that/does/not/fit/in/the/line/at/all", lines[-1])

    def test_rows_align_on_the_longest_name(self):
        r, out = report(True)
        r.rows("Quota", [("claude-max", "63% left"), ("kimi", "unknown")], "quota")
        self.assertEqual(out.getvalue().splitlines()[2:], ["  claude-max  63% left", "  kimi        unknown"])

    def test_details_print_under_their_line_with_no_mark(self):
        r, out = report(True)
        r.line("Lead", "would write /home/u/f:")
        r.detail("Lead", "exec /home/u/bw lead")
        self.assertEqual(out.getvalue().splitlines()[-2:], ["  · would write ~/f:", "      exec ~/bw lead"])

    def test_a_later_section_waits_for_an_open_earlier_one(self):
        r, out = report(True)
        r.begin("Updates")
        r.line("Updates", "pi: updated")
        r.ok("Checks", "pi sandbox: passed")
        r.line("Updates", "kimi: current")
        self.assertNotIn("Checks", out.getvalue())
        r.end("Updates")
        self.assertEqual(out.getvalue(),
                         "\nUpdates\n  · pi: updated\n  · kimi: current\n"
                         "\nChecks\n  ✓ pi sandbox: passed\n")

    def test_a_wait_line_is_never_held_and_prints_under_the_section_on_screen(self):
        r, out = report(True)
        r.begin("Updates")
        r.line("Updates", "pi: updated")
        r.wait("Checks", "testing pi")
        r.ok("Checks", "pi sandbox: passed")
        self.assertEqual(out.getvalue(), "\nUpdates\n  · pi: updated\n  … testing pi\n")
        r.end("Updates")
        self.assertEqual(out.getvalue().splitlines()[-2:], ["Checks", "  ✓ pi sandbox: passed"])
        r, out = report(True)  # nothing on screen yet: its own section
        r.begin("Updates")
        r.wait("Checks", "testing pi")
        self.assertEqual(out.getvalue(), "\nChecks\n  … testing pi\n")

    def test_the_epilogue_waits_for_the_lead(self):
        r, out = report(True)
        r.begin("Lead")
        r.line("Also", "registry: /x")
        r.ok("Lead", "claude, starting here")
        self.assertEqual(out.getvalue(), "\nLead\n  ✓ claude, starting here\n")
        r.end("Lead")
        self.assertTrue(out.getvalue().endswith("\nAlso\n  · registry: /x\n"))

    def test_parallel_writers_never_split_a_line(self):
        r, out = report(True)
        r.begin("Lead")

        def work(i):
            for j in range(50):
                r.ok("Checks", f"line {i}-{j}")
        ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        lines = [ln for ln in out.getvalue().splitlines() if ln.strip() and ln != "Checks"]
        self.assertEqual(len(lines), 200)
        self.assertTrue(all(ln.startswith("  ✓ line ") for ln in lines))


if __name__ == "__main__":
    unittest.main()
