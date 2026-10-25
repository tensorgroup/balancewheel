import unittest

from lead.guard.lexer import Op, Word, lex

H = "/home/u"


class LexerTest(unittest.TestCase):
    def test_quoted_semicolon_is_a_word_and_bare_one_is_an_op(self):
        self.assertEqual(lex("echo ';'", H), [Word("echo", False, False), Word(";", True, False)])
        self.assertEqual(lex("echo ;", H), [Word("echo", False, False), Op(";")])

    def test_newline_is_an_op(self):
        self.assertEqual(lex("a\nb", H), [Word("a", False, False), Op("\n"), Word("b", False, False)])

    def test_newline_inside_quotes_is_data(self):
        self.assertEqual(lex("echo 'a\nb'", H)[1], Word("a\nb", True, False))

    def test_expansion_marks_unexpanded(self):
        for src in ("echo $X", 'echo "$X"', "echo `id`", 'echo "`id`"', "echo a\\ b", "echo $(id)",
                    "echo {a,b}", "echo ~root", "echo ~+", 'echo "a\\nb"'):
            self.assertTrue(any(isinstance(t, Word) and t.unexpanded for t in lex(src, H)), src)

    def test_single_quotes_and_escaped_quote_do_not(self):
        self.assertFalse(lex("echo '$X'", H)[1].unexpanded)
        self.assertEqual(lex('echo "a\\"b"', H)[1], Word('a"b', True, False))

    def test_tilde_expands_only_at_word_start(self):
        self.assertEqual(lex("cat ~/x", H)[1], Word("/home/u/x", False, False))
        self.assertEqual(lex("cat ~", H)[1], Word("/home/u", False, False))
        self.assertEqual(lex("cat a~/x", H)[1], Word("a~/x", False, False))
        self.assertEqual(lex('cat "~/x"', H)[1], Word("~/x", True, False))

    def test_operators(self):
        ops = [t.op for t in lex("a;b&c&&d|e||f>g>>h<i<<j<<<k 2>l &>m >&2 (n)", H) if isinstance(t, Op)]
        self.assertEqual(ops, [";", "&", "&&", "|", "||", ">", ">>", "<", "<<", "<<<", "2>", "&>", ">&", "(", ")"])

    def test_fd_dup(self):
        self.assertEqual(lex("ls 2>&1", H), [Word("ls", False, False), Op(">&"), Word("1", False, False)])

    def test_pipe_ampersand_is_a_pipe(self):
        self.assertEqual([t.op for t in lex("a |& b", H) if isinstance(t, Op)], ["|"])

    def test_empty_quotes_make_an_empty_word(self):
        self.assertEqual(lex("echo ''", H)[1], Word("", True, False))

    def test_adjacent_quotes_join(self):
        self.assertEqual(lex("echo a'b'\"c\"", H)[1], Word("abc", True, False))

    def test_unterminated_quote_raises(self):
        for src in ("echo 'x", 'echo "x', 'echo "x\\"'):
            with self.assertRaises(ValueError, msg=src):
                lex(src, H)


if __name__ == "__main__":
    unittest.main()
