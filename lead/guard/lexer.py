"""lexer.py — a small shell lexer that keeps operators apart from quoted text.

shlex would read a newline as whitespace and drop the difference between `;` and `';'`, turning a
second command into an argument of the first. This one yields Word(text, quoted, unexpanded) and
Op(op). A word is unexpanded when the shell would rewrite it in a way the guard cannot see: an
unquoted or double-quoted `$` or backtick, an unquoted backslash, an unquoted `{` (brace
expansion), or a `~user`. A leading `~` or `~/` becomes `home`. Quoted text is data. An unquoted `#` at the start of a word begins a comment that runs to the end of the line.
Unterminated
quotes raise ValueError; heredoc and subshell bodies are not parsed (the rules treat them as gray).
When the only unexpanded parts of a word are plain `$NAME` or `${NAME}` references, `unexpanded`
is a Refs tuple (truthy) of (start, end, name, in_double_quotes) spans in `text`, so the rules can
expand exactly the variables bw set for the lead. Any other unexpanded part makes it True.
"""
import re
from collections import namedtuple

Word = namedtuple("Word", "text quoted unexpanded")
Op = namedtuple("Op", "op")
_REF = re.compile(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})")


class Refs(tuple):
    """The plain variable references in a word; never empty, so always truthy."""

_OPS = ["<<<", "&>>", "<<", ">>", "&>", ">&", "&&", "||", "|&", ";", "&", "|", "<", ">", "(", ")"]
_STOP = " \t\n;&|<>()"


def _match(s, i):
    for op in _OPS:
        if s.startswith(op, i):
            return op
    return None


def lex(command, home):
    out, buf, quoted, unexp, have, refs = [], [], False, False, False, []
    i, n = 0, len(command)

    def flush():
        nonlocal buf, quoted, unexp, have, refs
        if have:
            out.append(Word("".join(buf), quoted, True if unexp else Refs(refs) if refs else False))
        buf, quoted, unexp, have, refs = [], False, False, False, []

    def ref(j, dq):
        """A plain $NAME or ${NAME} at j: record its span, return where it ends (None if not one)."""
        m = _REF.match(command, j)
        if not m:
            return None
        start = sum(len(b) for b in buf)
        buf.append(m.group(0))
        refs.append((start, start + len(m.group(0)), m.group(1) or m.group(2), dq))
        return m.end()

    while i < n:
        c = command[i]
        if c in " \t":
            flush()
            i += 1
        elif c == "\n":
            flush()
            out.append(Op("\n"))
            i += 1
        elif c == "#" and not have:  # a comment: the rest of the line is not the shell's input
            j = command.find("\n", i)
            i = n if j < 0 else j
        elif c == "'":
            j = command.find("'", i + 1)
            if j < 0:
                raise ValueError("unterminated single quote")
            buf.append(command[i + 1:j])
            quoted = have = True
            i = j + 1
        elif c == '"':
            j, quoted, have = i + 1, True, True
            while True:
                if j >= n:
                    raise ValueError("unterminated double quote")
                d = command[j]
                if d == '"':
                    break
                if d == "\\" and command[j + 1:j + 2] in ('"', "\\"):
                    buf.append(command[j + 1])
                    j += 2
                    continue
                if d == "$":
                    end = ref(j, True)
                    if end is not None:
                        j = end
                        continue
                if d in "$`\\":
                    unexp = True
                buf.append(d)
                j += 1
            i = j + 1
        elif c == "\\":
            unexp = have = True
            buf.append(command[i + 1:i + 2])
            i += 2
        elif c == "$" and _REF.match(command, i):
            i = ref(i, False)
            have = True
        elif c in "$`{":
            unexp = have = True
            buf.append(c)
            i += 1
        elif c == "~" and not have:
            nxt = command[i + 1:i + 2]
            if nxt == "" or nxt in "/" + _STOP:
                buf.append(home)
            else:
                unexp = True
                buf.append("~")
            have = True
            i += 1
        elif c in ";&|<>()":
            fd = ""
            if have and not quoted and not unexp and "".join(buf) in ("1", "2") and c in "<>":
                fd, buf, have = "".join(buf), [], False
            flush()
            op = _match(command, i)
            i += len(op)
            op = {"|&": "|", "&>>": "&>"}.get(op, op)
            if fd == "2" and op in (">", ">>"):
                op = "2>"
            out.append(Op(op))
        else:
            buf.append(c)
            have = True
            i += 1
    flush()
    return out
