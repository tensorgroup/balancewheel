"""lexer.py — a small shell lexer that keeps operators apart from quoted text.

shlex would read a newline as whitespace and drop the difference between `;` and `';'`, turning a
second command into an argument of the first. This one yields Word(text, quoted, unexpanded) and
Op(op). A word is unexpanded when the shell would rewrite it in a way the guard cannot see: an
unquoted or double-quoted `$` or backtick, an unquoted backslash, an unquoted `{` (brace
expansion), or a `~user`. A leading `~` or `~/` becomes `home`. Quoted text is data. An unquoted `#` at the start of a word begins a comment that runs to the end of the line.
Unterminated
quotes raise ValueError; heredoc and subshell bodies are not parsed (the rules treat them as gray).
"""
from collections import namedtuple

Word = namedtuple("Word", "text quoted unexpanded")
Op = namedtuple("Op", "op")

_OPS = ["<<<", "&>>", "<<", ">>", "&>", ">&", "&&", "||", "|&", ";", "&", "|", "<", ">", "(", ")"]
_STOP = " \t\n;&|<>()"


def _match(s, i):
    for op in _OPS:
        if s.startswith(op, i):
            return op
    return None


def lex(command, home):
    out, buf, quoted, unexp, have = [], [], False, False, False
    i, n = 0, len(command)

    def flush():
        nonlocal buf, quoted, unexp, have
        if have:
            out.append(Word("".join(buf), quoted, unexp))
        buf, quoted, unexp, have = [], False, False, False

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
                if d in "$`\\":
                    unexp = True
                buf.append(d)
                j += 1
            i = j + 1
        elif c == "\\":
            unexp = have = True
            buf.append(command[i + 1:i + 2])
            i += 2
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
