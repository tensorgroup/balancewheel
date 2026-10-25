"""rules.py — what the guard decides about one normalised tool call. Pure; errs toward gray.

decide(call, ctx) -> Decision(decision, source, rule, reason). `block` and `ask` come from named
policy rules (source "rule"); anything unknown, unparseable, or unexpanded is "ask" with source
"gray", counted apart so the allow grammar can be tuned from real traffic. `allow` exists only for
the explicit grammar. A shell command is lexed (lexer.py), split into segments at every operator
except redirections, and each segment judged in this order: block, ask, gray, allow grammar. The
worst segment wins (block > ask > gray > allow). `cd <literal>` moves the cwd for later segments
only where it provably ran; otherwise the cwd is unknown and path-dependent allows turn gray.
ctx.push_dest(remote, refspec) -> branch name or None, and ctx.is_pristine(path) -> bool, are
called lazily; push_dest may return `origin/main` or `main`.
"""
import glob
import itertools
import os
import re
from collections import namedtuple

from lead.guard.lexer import Word, lex
from lead.sandbox import CANVAS_CONTROL_DIRS, SECRET_READS

RuleContext = namedtuple("RuleContext", "worktree git_dir common_dir home protected_branches deploy_commands push_dest is_pristine")
Decision = namedtuple("Decision", "decision source rule reason")


def blk(rule, why):
    return Decision("block", "rule", rule, why)


def ask(rule, why):
    return Decision("ask", "rule", rule, why)


def gry(rule, why):
    return Decision("ask", "gray", rule, why)


def allow(rule, why):
    return Decision("allow", "rule", rule, why)


def _rank(d):
    return 3 if d.decision == "block" else 2 if d.source == "rule" and d.decision == "ask" else 1 if d.decision == "ask" else 0


def _under(p, d, ci=False):
    if ci:
        p, d = p.lower(), d.lower()
    d = d.rstrip("/")
    return p == d or p.startswith(d + "/")


_FIRMLINK = "/System/Volumes/Data"
_DOTGIT = re.compile(r"(^|/)\.git(/|$)", re.I)
_GIT_CTRL = re.compile(r"(^|/)\.git/(hooks(/|$)|config(\.worktree)?$)", re.I)


def _canon(p):
    """The one way paths are compared: macOS firmlinks (realpath does not fold them) are stripped."""
    return p[len(_FIRMLINK):] or "/" if p == _FIRMLINK or p.startswith(_FIRMLINK + "/") else p


def _rp(a):
    try:
        return _canon(os.path.realpath(a))
    except (OSError, ValueError):
        return _canon(os.path.normpath(a))


def _paths(text, cwd):
    a = os.path.join(cwd, text)
    n = _canon(os.path.normpath(a))
    return {n, _rp(a), _rp(n)}


def _real(text, cwd):
    return _rp(os.path.join(cwd, text))


def _cluster(x):
    return set(x[1:]) if re.fullmatch(r"-[A-Za-z]+", x) else set()


def _has(t, shorts="", longs=()):
    """True if any option is in shorts or (an abbreviation of) longs; stops at `--`."""
    for x in t:
        if x == "--":
            break
        if x.startswith("--"):
            n = x.split("=")[0]
            if len(n) > 2 and any(d.startswith(n) for d in longs):
                return True
        elif _cluster(x) & set(shorts):
            return True
    return False


def _nonopt(t):
    out = []
    for x in t:
        if x == "--":
            break
        if not x.startswith("-"):
            out.append(x)
    return out


class _Env:
    def __init__(self, ctx):
        self.ctx, self.wt = ctx, _rp(ctx.worktree)

        def both(*p):
            j = os.path.join(*p)
            return {_canon(os.path.normpath(j)), _rp(j)}

        self.secrets = set().union(*[both(ctx.home, s) for s in SECRET_READS])
        self.canvas = set().union(*[both(ctx.home, s) for s in CANVAS_CONTROL_DIRS])
        dirs = (ctx.git_dir, ctx.common_dir)
        self.dotgit = os.path.join(self.wt, ".git")
        self.gitdirs = set().union(*[both(g) for g in dirs + (self.dotgit,)])
        self.hooks = set().union(*[both(g, "hooks") for g in dirs])
        self.cfg = {x.lower() for g in dirs for n in ("config", "config.worktree") for x in both(g, n)}
        self.dotgit_file = not os.path.isdir(self.dotgit)

    def hit(self, ps, dirs):
        return any(_under(p, d, True) for p in ps for d in dirs)

    def secret(self, ps):
        return self.hit(ps, self.secrets)

    def canvas_(self, ps):
        return self.hit(ps, self.canvas)

    def ancestor(self, ps):
        return any(s.lower() != p.lower() and _under(s, p, True) for p in ps for s in self.secrets)

    def guarded(self, ps):
        return (self.hit(ps, self.hooks) or any(p.lower() in self.cfg or _GIT_CTRL.search(p) for p in ps)
                or (self.dotgit_file and any(p.lower() == self.dotgit.lower() for p in ps)))

    def inside(self, real, strict=True):
        return (_under(real, self.wt) and not (strict and real == self.wt) and not self.hit({real}, self.gitdirs)
                and not _DOTGIT.search(real[len(self.wt):]))

    def nested(self, d):
        """True if d is inside a repo nested below the worktree root."""
        d = _rp(d)
        while _under(d, self.wt) and d != self.wt:
            if os.path.lexists(os.path.join(d, ".git")):
                return True
            d = os.path.dirname(d)
        return False


class _Seg:
    def __init__(self, before):
        self.words, self.redirs, self.before, self.after = [], [], before, None
        self.heredoc = self.bad = False


_WRITE_OPS = {">", ">>", "2>", "&>", ">&"}
_REDIR = _WRITE_OPS | {"<", "<<", "<<<"}


def _split(toks):
    segs, sub, cur, pend = [], False, _Seg(None), None
    for t in toks:
        if isinstance(t, Word):
            if pend:
                cur.redirs.append((pend, t))
                pend = None
            else:
                cur.words.append(t)
            continue
        op = t.op
        cur.bad = cur.bad or pend is not None
        if op in _REDIR:
            pend = op
            cur.heredoc = cur.heredoc or op == "<<"
            continue
        pend = None
        sub = sub or op in ("(", ")")
        if cur.words or cur.redirs or cur.bad:
            cur.after = op
            segs.append(cur)
        if cur.heredoc and op == "\n":
            return segs, sub  # the heredoc body is not parsed; nothing after it is judged
        cur = _Seg(";" if op in ("(", ")") else op)
    cur.bad = cur.bad or pend is not None
    if cur.words or cur.redirs or cur.bad:
        segs.append(cur)
    return segs, sub


_NOWRITE = ("/dev/null", "/dev/stdout", "/dev/stderr")


def _file_targets(seg):
    return [w for op, w in seg.redirs if op in _WRITE_OPS and w.text not in _NOWRITE
            and not (op in (">&", "&>") and re.fullmatch(r"\d+|-", w.text))]


_WRITERS = {"rm", "mkdir", "rmdir", "touch", "mv", "tee", "chmod", "chown", "truncate", "dd", "cp", "ln", "install"}
_LAST_ONLY = {"cp", "ln", "install"}


def _s_block(seg, env, cwd):
    w = seg.words
    name = w[0].text if w else ""
    base, args = os.path.basename(name), [x.text for x in w[1:]]
    if (base.lower() == "orca" and "terminal" in args) or (base.lower() == "herdr" and "pane" in args):
        return blk("block:canvas-control", f"{base} drives the canvas")
    texts = [x.text for x in w] + [x.text for op, x in seg.redirs if op not in ("<<", "<<<")]
    for t in texts:
        if "core.hookspath" in t.lower():
            return blk("block:hooks-path", "core.hooksPath would run attacker-chosen hooks")
        for c in {t, t.partition("=")[2]}:
            if c.startswith("~") and c[1:2] in ("", "/"):
                c = env.ctx.home + c[1:]
            if not c:
                continue
            ps = _paths(c, cwd)
            if env.secret(ps):
                return blk("block:secret-path", f"{c} is a credential path")
            if env.canvas_(ps):
                return blk("block:canvas-control", f"{c} is a canvas control path")
    targets = [x.text for x in _file_targets(seg)]
    if base in _WRITERS:
        pos = [a[3:] if base == "dd" and a.startswith("of=") else a for a in _nonopt(args)]
        targets += pos[-1:] if base in _LAST_ONLY else pos
    for t in targets:
        if env.guarded(_paths(t, cwd)):
            return blk("block:git-hooks-config", f"{t} is a git hook or config file")
    return None


_PUBLISH = {
    "gh": [("pr", "merge"), ("pr", "comment"), ("pr", "review"), ("pr", "create"), ("release",), ("repo", "delete"),
           ("issue", "comment"), ("issue", "create")],
    "npm": [("publish",)], "pnpm": [("publish",)], "yarn": [("publish",)], "twine": [("upload",)],
    "cargo": [("publish",)], "docker": [("push",)], "terraform": [("apply",), ("destroy",)],
    "kubectl": [("apply",), ("delete",)],
}
_MAIL = {"mail", "mailx", "sendmail", "mutt", "msmtp"}


def _git_parts(t):
    i = 0
    while i < len(t) and t[i].startswith("-"):
        i += 2 if t[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env") else 1
    return (t[i] if i < len(t) else None), t[i + 1:]


def _branch_name(d):
    return re.sub(r"^refs/heads/", "", d)


def _push(t, lit, env):
    pos, force = [], False
    for x in t:
        if x.startswith("--"):
            n = x.split("=")[0]
            if n.startswith("--force") or n in ("--delete", "--mirror"):
                force = True
            elif n == "--all":
                return ask("ask:push-all", "git push --all reaches every branch")
        elif _cluster(x) & {"f", "d"}:
            force = True
        elif not x.startswith("-"):
            pos.append(x)
    if force or any(r[:1] in ("+", ":") for r in pos):
        return ask("ask:force-push", "a forced or deleting push rewrites or removes remote history")
    if not lit:
        return None
    remote, specs, prot, dests = (pos[0] if pos else None), pos[1:], env.ctx.protected_branches, []
    for s in specs:
        dst = _branch_name(s.split(":")[-1])
        if dst in prot:
            dests.append((dst, True))
        elif dst in ("", "HEAD", "@") or "*" in dst:
            dests.append((env.ctx.push_dest(remote, s), False))
    if not specs:
        dests.append((env.ctx.push_dest(remote, None), False))
    for d, exact in dests:
        d = _branch_name(d or "")
        if d and (d in prot or (not exact and any(d.endswith("/" + p) for p in prot))):
            return ask("ask:protected-branch", f"push to protected branch {d}")
    return None


def _ask_git(t, lit, env):
    sub, rest = _git_parts(t)
    if sub == "push":
        return _push(rest, lit, env)
    if sub == "reset" and "--hard" in rest:
        return ask("ask:hard-reset", "git reset --hard discards work")
    if sub == "clean":
        fl = set().union(*[_cluster(x) for x in rest]) | ({"f"} if "--force" in rest else set())
        if "f" in fl and fl & {"d", "x", "X"}:
            return ask("ask:git-clean", "git clean -f with -d or -x deletes untracked files")
    if sub == "branch" and (_has(rest, "dD", ("--delete",))):
        return ask("ask:branch-delete", "deletes a branch")
    return None


def _s_ask(seg, env, cwd):
    w = seg.words
    if not w:
        return None
    name, t = w[0].text, [x.text for x in w[1:]]
    lit = not any(x.unexpanded for x in w)
    for dc in env.ctx.deploy_commands:
        toks = dc.split()
        if toks and [x.text for x in w[:len(toks)]] == toks:
            return ask("ask:deploy", f"matches the deploy command {dc!r}")
    if "/" in name:
        return None
    if name == "git":
        return _ask_git(t, lit, env)
    if name in _MAIL:
        return ask("ask:mail", f"{name} sends mail")
    no = _nonopt(t)
    for pat in _PUBLISH.get(name, []):
        if any(no[i:i + len(pat)] == list(pat) for i in range(len(no))):
            return ask("ask:publish", f"{name} {' '.join(pat)} publishes or changes something shared")
    return None


_INLINE = [(r"python[0-9.]*", "c", ()), (r"(ba|z|da|k)?sh", "c", ()), ("node", "ep", ("--eval", "--print")),
           ("perl", "eE", ()), ("ruby", "e", ())]


def _inline(base, t):
    for pat, letters, longs in _INLINE:
        if re.fullmatch(pat, base):
            for x in t:
                if x == "-m" and pat.startswith("python"):
                    break
                if _cluster(x) & set(letters) or x.split("=")[0] in longs:
                    return True
    return False


def _matches(env, cwd, t, write):
    """Judge what a glob really reaches (symlinks included); None if nothing is wrong."""
    for m in itertools.islice(glob.glob(os.path.join(cwd, t)), 200):
        ps = _paths(m, cwd)
        if env.secret(ps):
            return blk("block:secret-path", f"{t} matches a credential path")
        if env.canvas_(ps):
            return blk("block:canvas-control", f"{t} matches a canvas control path")
        if write and env.guarded(ps):
            return blk("block:git-hooks-config", f"{t} matches a git hook or config file")
        if write and not env.inside(_rp(m), strict=False):
            return gry("gray:outside-write", f"{t} matches a path outside the worktree or in a git dir")
    return None


def _glob(seg, env, cwd, writer):
    items = [(x, writer) for x in seg.words[1:]] + [(x, True) for x in _file_targets(seg)]
    items += [(w, False) for op, w in seg.redirs if op == "<"]
    for x, wr in items:
        t = x.text
        idx = [t.find(c) for c in "*?[" if c in t]
        if not idx:
            continue
        prefix, rest = t[:min(idx)], t[min(idx):]
        partial = prefix.rpartition("/")[2]
        head = prefix[:len(prefix) - len(partial)]
        d = _real(head or ".", cwd)
        bad = (not _under(d, env.wt) or env.hit({d}, env.gitdirs) or ".." in rest.split("/")
               or (partial and any(g.lower().startswith(os.path.join(d, partial).lower()) for g in env.gitdirs))
               or (writer and partial.startswith(".")))
        if bad:
            return gry("gray:glob", f"{t} may match outside the worktree or inside a git dir")
        m = _matches(env, cwd, t, wr)
        if m:
            return m
    return None


def _confined(t, env, cwd):
    for x in t:
        for c in (x, x.partition("=")[2]):
            if c and (c.startswith("/") or ".." in c.split("/")) and not _under(_real(c, cwd), env.wt):
                return gry("gray:outside-path", f"{c} is outside the worktree")
    return None


def _script(path, env, cwd, rule):
    real = _real(path, cwd)
    if env.inside(real) and env.ctx.is_pristine(real):
        return None
    return gry(rule, f"{path} is not a tracked, unmodified repo script inside the worktree")


def _noroot(t, env, cwd):
    for x in t:
        for c in (x, x.partition("=")[2]):
            if c and not c.startswith("-") and env.ancestor(_paths(c, cwd)):
                return gry("gray:secret-ancestor", f"{c} contains credential paths a recursive read would reach")
    return None


def _opt(why):
    return gry("gray:option", why)


_FIND_DENY = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}


def _h_read(name):
    def h(t, args, env, cwd):
        if name == "file" and _has(t, "C", ("--compile",)):
            return _opt("file -C writes a compiled magic file")
        if name == "rg" and _has(t, "", ("--pre", "--pre-glob", "--hostname-bin")):
            return _opt("rg option runs a program")
        if name == "find" and any(x in _FIND_DENY for x in t):
            return _opt("find primary executes or writes")
        if name == "tree" and _has(t, "o", ("--output",)):
            return _opt("tree -o writes a file")
        return _noroot(t, env, cwd)
    return h


_GIT_RO = {"status", "log", "diff", "show", "rev-parse", "ls-files", "blame", "grep", "add", "commit", "stash", "worktree"}
_BRANCH_L = ("--list", "--all", "--remotes", "--verbose", "--contains", "--no-contains", "--merged", "--no-merged",
             "--show-current", "--sort", "--format", "--points-at", "--abbrev", "--no-abbrev", "--color", "--no-color",
             "--column", "--no-column", "--ignore-case", "--quiet")
_TAG_L = ("--list", "--contains", "--no-contains", "--merged", "--no-merged", "--sort", "--format", "--points-at",
          "--column", "--no-column", "--color", "--no-color", "--ignore-case")
_FETCH_L = ("--all", "--prune", "--tags", "--no-tags", "--quiet", "--verbose", "--depth", "--unshallow", "--force",
            "--dry-run", "--prune-tags", "--jobs", "--no-recurse-submodules")


def _only(t, shorts, longs):
    """Every option must be in shorts/longs (exact long names); returns the positionals or None."""
    pos = []
    for x in t:
        if x.startswith("--") and x != "--":
            if x.split("=")[0] not in longs:
                return None
        elif x.startswith("-") and len(x) > 1:
            if not (_cluster(x) and _cluster(x) <= set(shorts)):
                return None
        else:
            pos.append(x)
    return pos


def _git_ok(sub, rest):
    if sub in ("status", "log", "diff", "show", "rev-parse", "ls-files", "blame", "add"):
        return not _has(rest, "", ("--output", "--ext-diff"))
    if sub == "grep":
        return not _has(rest, "O", ("--open-files-in-pager",))
    if sub == "commit":
        return not _has(rest, "eSpt", ("--edit", "--gpg-sign", "--template", "--interactive", "--patch"))
    if sub == "fetch":
        pos = _only(rest, "apqvtfn", _FETCH_L)
        return pos is not None and not any("::" in x for x in pos)
    if sub == "worktree":
        return rest[:1] == ["list"] and _only(rest[1:], "v", ("--porcelain", "--verbose")) == []
    if sub == "stash":
        return rest[:1] in (["list"], ["show"]) and not _has(rest, "", ("--output",))
    if sub == "branch":
        return _only(rest, "arvlqi", _BRANCH_L) is not None
    if sub == "tag":
        pos = _only(rest, "li", _TAG_L)
        listing = any(x in ("-l", "--list") or x.split("=")[0] in _TAG_L[1:5] + _TAG_L[7:8] for x in rest)
        return pos is not None and (not pos or listing)
    if sub == "remote":
        return rest in ([], ["-v"], ["--verbose"]) or (rest[:1] in (["show"], ["get-url"]) and _only(rest[1:], "", ()) is not None)
    if sub == "config":
        return rest[:1] and rest[0] in ("--get", "--get-all", "--get-regexp") and \
            _only(rest[1:], "z", ("--local", "--global", "--system", "--worktree", "--show-origin", "--show-scope", "--null")) is not None
    return False


def _h_git(t, args, env, cwd):
    if t[:1] == ["-C"]:
        if len(t) < 2 or not env.inside(_real(t[1], cwd), strict=False) and _real(t[1], cwd) != env.wt:
            return gry("gray:option", "git -C must name a path inside the worktree")
        cwd, t = _real(t[1], cwd), t[2:]
    if env.nested(cwd):
        return gry("gray:nested-repo", "a repo nested in the worktree has hooks and config the guard does not own")
    sub, rest = _git_parts(t)
    if t[:1] and t[0].startswith("-"):
        return _opt("git global options other than -C are outside the grammar")
    if sub in _GIT_RO | {"fetch", "branch", "tag", "remote", "config"}:
        return None if _git_ok(sub, rest) else _opt(f"git {sub} with these options is outside the grammar")
    return gry("gray:git-subcommand", f"git {sub} is not in the allow grammar")


_WL = {"rm": ("rRfiIvd", ("--recursive", "--force", "--verbose", "--dir")), "mkdir": ("pvm", ("--parents", "--verbose")),
       "touch": ("acmdrt", ("--no-create",)), "cp": ("rRapfiunvTtPLHl", ("--recursive", "--force", "--verbose", "--archive")),
       "mv": ("fiunvTt", ("--force", "--verbose", "--no-clobber"))}


def _h_write(name):
    def h(t, args, env, cwd):
        letters, longs = _WL[name]
        paths, end, rec = [], False, False
        for x in t:
            if not end and x == "--":
                end = True
            elif not end and x.startswith("--"):
                if x not in longs:
                    return _opt(f"{name} {x} is outside the grammar")
                rec = rec or x == "--recursive"
            elif not end and x.startswith("-") and len(x) > 1:
                if not set(x[1:]) <= set(letters):
                    return _opt(f"{name} {x} is outside the grammar")
                rec = rec or bool(set(x[1:]) & set("rR"))
            else:
                paths.append(x)
        if not paths:
            return gry("gray:no-path", f"{name} names no path")
        if all(env.inside(_real(p, cwd), strict=name in ("rm", "mv")) for p in paths):
            return None
        if name == "rm" and rec:
            return ask("ask:rm-outside", "recursive rm outside the worktree or into a git dir")
        return gry("gray:outside-write", f"{name} reaches outside the worktree or into a git dir")
    return h


def _runner(*subs, deny=()):
    def h(t, args, env, cwd):
        if not t or t[0] not in subs or any(x.split("=")[0].lstrip("-") in deny or x.startswith("-Z") for x in t[1:]):
            return _opt("runner subcommand or option is outside the grammar")
        return _confined(t, env, cwd)
    return h


def _h_npm(t, args, env, cwd):
    if not (t[:1] == ["test"] or (t[:1] == ["run"] and len(t) > 1 and not t[1].startswith("-"))):
        return _opt("only npm test and npm run <script>")
    if any(x.split("=")[0] in ("--script-shell", "--userconfig", "--globalconfig", "--prefix", "-g") for x in t):
        return _opt("npm option redirects scripts or config")
    return _confined(t, env, cwd)


def _h_make(t, args, env, cwd):
    if any(("=" in x) or (x.startswith("-") and not re.fullmatch(r"-(j\d*|k|s|n|B|i)", x)) for x in t):
        return _opt("make option or variable override is outside the grammar")
    return _confined(t, env, cwd)


def _h_python(t, args, env, cwd):
    if t[:2] in (["-m", "unittest"], ["-m", "pytest"]):
        return _confined(t, env, cwd)
    return _h_runner(t, args, env, cwd)


def _h_runner(t, args, env, cwd):
    if not t or t[0].startswith("-"):
        return gry("gray:script", "runner needs a plain script path first")
    return _script(t[0], env, cwd, "gray:script")


def _h_pytest(t, args, env, cwd):
    return _confined(t, env, cwd)


_HANDLERS = {n: _h_read(n) for n in ("ls", "cat", "head", "tail", "wc", "grep", "file", "stat", "diff", "rg", "find", "tree")}
_HANDLERS.update({n: (lambda *a: None) for n in ("pwd", "echo", "printf", "true", "which", "type", "cd")})
_HANDLERS.update({n: _h_write(n) for n in _WL})
_HANDLERS.update({"git": _h_git, "npm": _h_npm, "make": _h_make, "pytest": _h_pytest, "bash": _h_runner, "sh": _h_runner,
                  "node": _h_runner, "cargo": _runner("test", "build", "check", "clippy", "fmt", deny=("config",)),
                  "go": _runner("test", "build", "vet", deny=("exec", "toolexec", "o", "overlay", "vettool", "modfile", "pkgdir"))})


def _s_grammar(seg, env, cwd):
    w = seg.words
    if seg.bad:
        return gry("gray:redirect", "a redirection without a usable target")
    if seg.heredoc:
        return gry("gray:heredoc", "heredoc bodies are not parsed")
    if not w:
        return gry("gray:no-command", "a redirection with no command")
    name, t = w[0].text, [x.text for x in w[1:]]
    if re.match(r"[A-Za-z_][A-Za-z0-9_]*\+?=", name):
        return gry("gray:assignment", "a leading variable assignment changes how the command runs")
    if any(x.unexpanded for x in w + [x for _, x in seg.redirs]):
        return gry("gray:unexpanded", "the shell would expand a word the guard cannot see")
    base = os.path.basename(name)
    if name in ("eval", "exec", "source", "."):
        return gry("gray:eval", f"{name} runs text the guard cannot see")
    if name == "xargs":
        return gry("gray:xargs", "xargs builds commands from input")
    if _inline(base, t):
        return gry("gray:interpreter", f"{base} runs inline code")
    if "/" in name:
        return _script(name, env, cwd, "gray:path-command")
    g = _glob(seg, env, cwd, name in _WL)
    if g:
        return g
    for x in _file_targets(seg):
        if not env.inside(_real(x.text, cwd)):
            return gry("gray:outside-write", f"redirection into {x.text}: outside the worktree or into a git dir")
    h = _h_python if re.fullmatch(r"python[0-9.]*", name) else _HANDLERS.get(name)
    if h is None:
        return gry("gray:unknown-command", f"{name} is not in the allow grammar")
    return h(t, w[1:], env, cwd)


def _segment(seg, env, cwd):
    return _s_block(seg, env, cwd) or _s_ask(seg, env, cwd) or _s_grammar(seg, env, cwd) or \
        allow("allow:shell", "in the allow grammar")


def _cd(seg, env, cwd):
    a = [x.text for x in seg.words[1:]]
    if len(a) > 1 or (a and a[0].startswith("-")):
        return None
    path = os.path.normpath(os.path.join(cwd, a[0])) if a else env.ctx.home
    return path if os.path.isdir(path) and os.path.realpath(path) == path else None


_INTERP = re.compile(r"(ba|z)?sh|python[0-9.]*|node|perl|ruby")


def _shell(call, env):
    try:
        toks = lex(call.command or "", env.ctx.home)
    except ValueError as e:
        return gry("gray:unparseable", f"cannot parse the command ({e})")
    segs, sub = _split(toks)
    if not segs:
        return gry("gray:subshell" if sub else "gray:empty", "nothing the guard can judge")
    cwd, known, tainted, decs = _rp(call.cwd or env.ctx.worktree), True, False, []
    for seg in segs:
        d = _segment(seg, env, cwd)
        if not known and d.decision == "allow":
            d = gry("gray:cwd-unknown", "an earlier cd may not have run as written")
        if seg.words and seg.words[0].text == "cd" and known:
            new = _cd(seg, env, cwd) if d.decision == "allow" else None
            if new and seg.before not in ("|", "||") and seg.after not in ("|", "||", "&"):
                cwd, tainted = new, seg.before == "&&"
            else:
                known = False
        if tainted and seg.after in (";", "\n", "&", "||"):
            known, tainted = False, False
        decs.append(d)
    dl = False
    for k, seg in enumerate(segs):
        base = os.path.basename(seg.words[0].text) if seg.words else ""
        dl = dl and k > 0 and segs[k - 1].after == "|"
        if dl and _INTERP.fullmatch(base) and _rank(decs[k]) < 2:
            decs[k] = ask("ask:download-exec", "a download is piped into an interpreter")
        dl = dl or base in ("curl", "wget")
    if sub:
        decs.append(gry("gray:subshell", "subshells are not parsed"))
    worst = max(decs, key=_rank)
    return allow("allow:shell", "every segment is in the allow grammar") if _rank(worst) == 0 else worst


_SEND = ("send", "post", "comment", "message", "publish")


def _other(call, env):
    kind = call.kind
    if kind == "mcp":
        if any(s in (call.tool or "").lower() for s in _SEND):
            return ask("ask:mcp-send", f"MCP tool {call.tool} sends or publishes")
        return gry("gray:mcp", f"MCP tool {call.tool} is not in the allow grammar")
    if kind in ("read", "write"):
        if not call.paths:
            return gry("gray:no-path", f"{call.tool} carried no path")
        worst = allow(f"allow:{kind}", f"{kind} outside secret and git-control paths")
        for p in call.paths:
            ps = {_canon(os.path.normpath(p)), _rp(p)}
            if env.secret(ps):
                return blk("block:secret-path", f"{p} is a credential path")
            if env.canvas_(ps):
                return blk("block:canvas-control", f"{p} is a canvas control path")
            if kind == "write" and env.guarded(ps):
                return blk("block:git-hooks-config", f"{p} is a git hook or config file")
            if kind == "read" and env.ancestor(ps):
                worst = gry("gray:secret-ancestor", f"{p} contains credential paths a recursive read would reach")
            if kind == "write" and not env.inside(_rp(p)) and _rank(worst) < 1:
                worst = gry("gray:outside-write", f"{p} is outside the worktree or inside a git dir")
        return worst
    if call.tool == "oversized":
        return gry("gray:oversized", "payload over the size cap")
    return gry("gray:other", f"unrecognised tool {call.tool!r}")


def decide(call, ctx):
    env = _Env(ctx)
    return _shell(call, env) if call.kind == "shell" else _other(call, env)
