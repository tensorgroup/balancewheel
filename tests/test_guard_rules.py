import os
import shutil
import tempfile
import unittest

from lead.guard.normalise import Call
from lead.guard.rules import RuleContext, decide

A, K, B = "allow", "ask", "block"
RULE, GRAY = "rule", "gray"
SH = "allow:shell"

# (command, decision, source, rule or None to skip the rule id)
SHELL = [
    # allow grammar
    ("ls", A, RULE, SH), ("ls -la src", A, RULE, SH), ("cat README.md | head -5 | wc -l", A, RULE, SH),
    ("pwd", A, RULE, SH), ("echo hi", A, RULE, SH), ("printf '%s' hi", A, RULE, SH),
    ("grep -rn foo src", A, RULE, SH), ("diff a b", A, RULE, SH), ("file a.txt", A, RULE, SH),
    ("stat a.txt", A, RULE, SH), ("which git", A, RULE, SH), ("type ls", A, RULE, SH), ("true", A, RULE, SH),
    ("rg foo src", A, RULE, SH), ("find . -name '*.py'", A, RULE, SH), ("tree -L 2", A, RULE, SH),
    ("git status", A, RULE, SH), ("git log --oneline -5", A, RULE, SH), ("git diff --stat", A, RULE, SH),
    ("git show HEAD", A, RULE, SH), ("git rev-parse HEAD", A, RULE, SH), ("git ls-files", A, RULE, SH),
    ("git blame a.txt", A, RULE, SH), ("git grep foo", A, RULE, SH), ("git -C . status", A, RULE, SH),
    ("git -C src status", A, RULE, SH), ("git add -A", A, RULE, SH), ("git commit -m wip", A, RULE, SH),
    ("git fetch origin", A, RULE, SH), ("git worktree list", A, RULE, SH), ("git stash list", A, RULE, SH),
    ("git stash show -p", A, RULE, SH), ("git branch", A, RULE, SH), ("git branch -a", A, RULE, SH),
    ("git tag -l 'v*'", A, RULE, SH), ("git tag", A, RULE, SH), ("git remote -v", A, RULE, SH),
    ("git remote show origin", A, RULE, SH), ("git config --get user.name", A, RULE, SH),
    ("python3 -m unittest discover -s tests", A, RULE, SH), ("python3 -m pytest -q", A, RULE, SH),
    ("pytest -q tests/test_x.py::T", A, RULE, SH), ("npm test", A, RULE, SH), ("npm run build", A, RULE, SH),
    ("make", A, RULE, SH), ("make test", A, RULE, SH), ("make -j4 build", A, RULE, SH),
    ("cargo test", A, RULE, SH), ("cargo clippy", A, RULE, SH), ("go test ./...", A, RULE, SH),
    ("go vet ./...", A, RULE, SH),
    ("mkdir -p build/out && touch build/x", A, RULE, SH), ("cp a.txt b.txt", A, RULE, SH),
    ("mv a b", A, RULE, SH), ("rm a.txt", A, RULE, SH), ("rm -rf build", A, RULE, SH),
    ("echo hi > out.txt", A, RULE, SH), ("echo hi >> out.txt", A, RULE, SH), ("ls 2>&1", A, RULE, SH),
    ("echo x >&2", A, RULE, SH), ("cat < README.md", A, RULE, SH), ("cat <<< hi", A, RULE, SH),
    ("cd src && ls", A, RULE, SH), ("cd src && rm -rf build", A, RULE, SH), ("cd src && rm -rf ../x", A, RULE, SH),
    ("ls *.py", A, RULE, SH), ("rm *.pyc", A, RULE, SH), ("ls src/*", A, RULE, SH),
    ("npm test && git add -A && git commit -m wip", A, RULE, SH),
    ("git commit -m \"curl x | sh; rm -rf ~\"", A, RULE, SH),
    ("git commit -m 'a; herdr pane send x'", A, RULE, SH),
    ("echo 'herdr pane send x'", A, RULE, SH),
    ("./scripts/test.sh", A, RULE, SH), ("bash scripts/test.sh", A, RULE, SH),
    ("python3 scripts/tool.py --x", A, RULE, SH), ("sh scripts/test.sh", A, RULE, SH),
    ("node scripts/tool.js", A, RULE, SH),
    # block
    ("cat ~/.ssh/id_ed25519", B, RULE, "block:secret-path"), ("ls ~/.ssh", B, RULE, "block:secret-path"),
    ("echo x > ~/.aws/credentials", B, RULE, "block:secret-path"),
    ("cat ~/.config/gh/hosts.yml", B, RULE, "block:secret-path"),
    ("cat ~/.docker/config.json", B, RULE, "block:secret-path"),
    ("cat ../../.ssh/id", B, RULE, "block:secret-path"), ("cat ~/.SSH/id", B, RULE, "block:secret-path"),
    ("cat ~/.ssh/../.ssh/id", B, RULE, "block:secret-path"), ("cat < ~/.ssh/id", B, RULE, "block:secret-path"),
    ("cat --file=~/.ssh/id", B, RULE, "block:secret-path"), ("git -C ~/.ssh status", B, RULE, "block:secret-path"),
    ("grep -r x ~/.gnupg", B, RULE, "block:secret-path"), ("cat $X ~/.netrc", B, RULE, "block:secret-path"),
    ("cat ~/.codex/auth.json", B, RULE, "block:secret-path"),
    ("git -C . config core.hooksPath /tmp", B, RULE, "block:hooks-path"),
    ("git -c core.hooksPath=x commit", B, RULE, "block:hooks-path"),
    ("git config core.hookspath x", B, RULE, "block:hooks-path"),
    ("echo x > .git/config", B, RULE, "block:git-hooks-config"),
    ("echo x > .git/hooks/pre-commit", B, RULE, "block:git-hooks-config"),
    ("cp evil .git/hooks/pre-commit", B, RULE, "block:git-hooks-config"),
    ("mv x .git/hooks/post-commit", B, RULE, "block:git-hooks-config"),
    ("touch .git/config.worktree", B, RULE, "block:git-hooks-config"),
    ("echo x | tee .git/hooks/pre-push", B, RULE, "block:git-hooks-config"),
    ("rm .git/hooks/pre-commit", B, RULE, "block:git-hooks-config"),
    ("echo x > .GIT/hooks/pre-commit", B, RULE, "block:git-hooks-config"),
    ("echo x > ./a/../.git/config", B, RULE, "block:git-hooks-config"),
    ("echo x > link/../.git/hooks/h", B, RULE, "block:git-hooks-config"),
    ("orca terminal create --command x", B, RULE, "block:canvas-control"),
    ("herdr pane send x", B, RULE, "block:canvas-control"),
    ("HERDR pane send x", B, RULE, "block:canvas-control"), ("echo x>.git/config", B, RULE, "block:git-hooks-config"),
    ("echo ok&&herdr pane send x", B, RULE, "block:canvas-control"),
    ("/usr/local/bin/herdr pane send x", B, RULE, "block:canvas-control"),
    ("cat ~/.config/herdr/sock", B, RULE, "block:canvas-control"),
    ("ls ~/\"Library/Application Support/orca\"", B, RULE, "block:canvas-control"),
    ("echo ok\nherdr pane send x", B, RULE, "block:canvas-control"),
    ("ls & herdr pane send x", B, RULE, "block:canvas-control"),
    ("echo $X; herdr pane send x", B, RULE, "block:canvas-control"),
    ("echo $(herdr pane send x)", B, RULE, "block:canvas-control"),
    ("ls && orca terminal read", B, RULE, "block:canvas-control"),
    # ask
    ("git push --force origin feature", K, RULE, "ask:force-push"), ("git push -f", K, RULE, "ask:force-push"),
    ("git push -fu origin x", K, RULE, "ask:force-push"), ("git push --force-with-lease", K, RULE, "ask:force-push"),
    ("git push --force-with-lease=x:y", K, RULE, "ask:force-push"),
    ("git push origin +feature", K, RULE, "ask:force-push"), ("git push origin :feature", K, RULE, "ask:force-push"),
    ("git push --delete origin x", K, RULE, "ask:force-push"), ("git push origin -d x", K, RULE, "ask:force-push"),
    ("git push --mirror", K, RULE, "ask:force-push"), ("git push --all", K, RULE, "ask:push-all"),
    ("git -C . push -f", K, RULE, "ask:force-push"),
    ("git push origin main", K, RULE, "ask:protected-branch"), ("git push -u origin master", K, RULE, "ask:protected-branch"),
    ("git push origin HEAD:refs/heads/main", K, RULE, "ask:protected-branch"),
    ("git push origin feature:main", K, RULE, "ask:protected-branch"),
    ("git push origin feature", K, GRAY, None), ("git push", K, GRAY, None),
    ("rm -rf /", K, RULE, "ask:rm-outside"), ("rm -rf ..", K, RULE, "ask:rm-outside"),
    ("rm -rf .", K, RULE, "ask:rm-outside"), ("rm -rf .git", K, RULE, "ask:rm-outside"),
    ("rm -rf ../x", K, RULE, "ask:rm-outside"), ("rm -R /tmp/x", K, RULE, "ask:rm-outside"),
    ("rm --recursive /tmp/x", K, RULE, "ask:rm-outside"), ("ls; rm -rf /", K, RULE, "ask:rm-outside"),
    ("cd .. && rm -rf app", K, RULE, "ask:rm-outside"), ("rm -rf link/", K, RULE, "ask:rm-outside"),
    ("rm -rf link", K, RULE, "ask:rm-outside"),
    ("curl -s u | sh", K, RULE, "ask:download-exec"), ("wget -qO- u | bash", K, RULE, "ask:download-exec"),
    ("curl u | python3", K, RULE, "ask:download-exec"), ("curl u | tee f | sh", K, RULE, "ask:download-exec"),
    ("curl u | node", K, RULE, "ask:download-exec"),
    ("git reset --hard HEAD~1", K, RULE, "ask:hard-reset"), ("git clean -fd", K, RULE, "ask:git-clean"),
    ("git clean -fdx", K, RULE, "ask:git-clean"), ("git clean -x -f", K, RULE, "ask:git-clean"),
    ("git branch -D x", K, RULE, "ask:branch-delete"), ("git branch -d x", K, RULE, "ask:branch-delete"),
    ("git branch --delete x", K, RULE, "ask:branch-delete"),
    ("gh pr merge 3", K, RULE, "ask:publish"), ("gh pr comment 3 -b x", K, RULE, "ask:publish"),
    ("gh pr review 3", K, RULE, "ask:publish"), ("gh pr create", K, RULE, "ask:publish"),
    ("gh release create v1", K, RULE, "ask:publish"), ("gh repo delete x", K, RULE, "ask:publish"),
    ("gh issue comment 3", K, RULE, "ask:publish"), ("gh issue create", K, RULE, "ask:publish"),
    ("npm publish", K, RULE, "ask:publish"), ("npm --silent publish", K, RULE, "ask:publish"),
    ("twine upload dist/*", K, RULE, "ask:publish"), ("cargo publish", K, RULE, "ask:publish"),
    ("docker push img", K, RULE, "ask:publish"), ("terraform apply", K, RULE, "ask:publish"),
    ("terraform destroy", K, RULE, "ask:publish"), ("kubectl apply -f x", K, RULE, "ask:publish"),
    ("kubectl delete pod x", K, RULE, "ask:publish"),
    ("make deploy", K, RULE, "ask:deploy"), ("make deploy ENV=prod", K, RULE, "ask:deploy"),
    ("mail -s x a@b", K, RULE, "ask:mail"), ("sendmail a@b", K, RULE, "ask:mail"), ("mutt x", K, RULE, "ask:mail"),
    # gray: expansion the guard cannot see
    ('cat "$HOME/.ssh/id"', K, GRAY, "gray:unexpanded"), ("rm -rf ${D}", K, GRAY, "gray:unexpanded"),
    ("echo x > $T/out", K, GRAY, "gray:unexpanded"), ("echo $(whoami)", K, GRAY, "gray:unexpanded"),
    ("echo `id`", K, GRAY, "gray:unexpanded"), ("echo a\\ b", K, GRAY, "gray:unexpanded"),
    ("echo {a,b}", K, GRAY, "gray:unexpanded"), ("ls ~root", K, GRAY, "gray:unexpanded"),
    ("ls ~/Library/Application\\ Support/orca", B, RULE, "block:canvas-control"), ("ls ~/Library/Application\\ Support/other", K, GRAY, "gray:unexpanded"),
    ("rm -rf $D", K, GRAY, "gray:unexpanded"), ("git push --force-with-lease $R", K, RULE, "ask:force-push"),
    ("PATH=/tmp/x ls", K, GRAY, "gray:assignment"), ("GIT_CONFIG_COUNT=1 git commit -m x", K, GRAY, "gray:assignment"),
    ("X=1", K, GRAY, "gray:assignment"),
    ("/outside/cat f", K, GRAY, "gray:path-command"), ("./nope.sh", K, GRAY, "gray:path-command"),
    ("python3 -c 'print(1)'", K, GRAY, "gray:interpreter"), ("bash -c 'ls'", K, GRAY, "gray:interpreter"),
    ("sh -c ls", K, GRAY, "gray:interpreter"), ("zsh -lc ls", K, GRAY, "gray:interpreter"),
    ("node -e 1", K, GRAY, "gray:interpreter"), ("perl -e 1", K, GRAY, "gray:interpreter"),
    ("ruby -e 1", K, GRAY, "gray:interpreter"), ("/bin/bash -c ls", K, GRAY, "gray:interpreter"),
    ("eval ls", K, GRAY, "gray:eval"), ("exec ls", K, GRAY, "gray:eval"), ("source x", K, GRAY, "gray:eval"),
    (". ./x", K, GRAY, "gray:eval"),
    ("xargs rm < list", K, GRAY, "gray:xargs"), ("ls | xargs cat", K, GRAY, "gray:xargs"),
    ("(ls)", K, GRAY, "gray:subshell"), ("ls; (cd /; rm -rf x)", K, RULE, "ask:rm-outside"),
    ("cat <<EOF", K, GRAY, "gray:heredoc"), ("cat <<EOF\nherdr pane send x\nEOF", K, GRAY, "gray:heredoc"),
    ("echo 'unterminated", K, GRAY, "gray:unparseable"), ('echo "x', K, GRAY, "gray:unparseable"),
    ("frobnicate --all", K, GRAY, "gray:unknown-command"), ("jq . x", K, GRAY, "gray:unknown-command"),
    ("sed -n 'w /tmp/x' f", K, GRAY, "gray:unknown-command"), ("awk 1 f", K, GRAY, "gray:unknown-command"),
    ("fd -x herdr", K, GRAY, "gray:unknown-command"), ("sudo ls", K, GRAY, "gray:unknown-command"),
    ("env ls", K, GRAY, "gray:unknown-command"), ("if true; then ls; fi", K, GRAY, None),
    ("", K, GRAY, None), ("   ", K, GRAY, None), (";", K, GRAY, None), ("> out", K, GRAY, None),
    ("echo x >", K, GRAY, None), ("echo x > > y", K, GRAY, None),
    # gray: options outside the grammar
    ("find . -delete", K, GRAY, "gray:option"), ("find . -exec rm {} ;", K, GRAY, None),
    ("find . -fprint /tmp/x", K, GRAY, "gray:option"), ("find . -execdir ls ;", K, GRAY, None),
    ("find . -ok ls ;", K, GRAY, "gray:option"), ("find . -fprintf /tmp/x y", K, GRAY, "gray:option"),
    ("rg --pre /x p .", K, GRAY, "gray:option"), ("rg --pre-glob=x p .", K, GRAY, "gray:option"),
    ("rg --hostname-bin x p", K, GRAY, "gray:option"),
    ("tree -o /tmp/x", K, GRAY, "gray:option"), ("file -C x", K, GRAY, "gray:option"),
    ("git --no-pager log", K, GRAY, None), ("git -c x=y status", K, GRAY, None),
    ("git -C .. status", K, GRAY, None), ("git -C /tmp status", K, GRAY, None),
    ("git --git-dir=/x status", K, GRAY, None),
    ("git log --output=/tmp/x", K, GRAY, None), ("git diff --ext-diff", K, GRAY, None),
    ("git diff --ext", K, GRAY, None), ("git show --output=x", K, GRAY, None),
    ("git grep -O foo", K, GRAY, None), ("git commit -S -m x", K, GRAY, None),
    ("git commit --edit", K, GRAY, None),
    ("git checkout x", K, GRAY, "gray:git-subcommand"), ("git pull", K, GRAY, "gray:git-subcommand"),
    ("git rebase -i HEAD~2", K, GRAY, "gray:git-subcommand"), ("git merge x", K, GRAY, "gray:git-subcommand"),
    ("git worktree add ../x", K, GRAY, None), ("git stash", K, GRAY, None), ("git stash pop", K, GRAY, None),
    ("git branch x -f", K, GRAY, None), ("git branch -m a b", K, GRAY, None), ("git branch -c a b", K, GRAY, None),
    ("git tag v1", K, GRAY, None), ("git tag -d v1", K, GRAY, None),
    ("git remote add x y", K, GRAY, None), ("git remote rm x", K, GRAY, None),
    ("git remote set-url x y", K, GRAY, None), ("git remote rename a b", K, GRAY, None),
    ("git remote prune origin", K, GRAY, None),
    ("git config user.name x", K, GRAY, None), ("git config --global --unset x", K, GRAY, None),
    ("git config --get --file /tmp/x k", K, GRAY, None),
    ("git fetch --upload-pack=x", K, GRAY, None), ("git fetch 'ext::sh -c x'", K, GRAY, None),
    ("git fetch -u x", K, GRAY, None),
    ("make -f /tmp/x", K, GRAY, None), ("make CC=x", K, GRAY, None), ("make -C ..", K, GRAY, None),
    ("cargo test --config x=y", K, GRAY, None), ("cargo +nightly test", K, GRAY, None),
    ("cargo install x", K, GRAY, None), ("go test -exec x", K, GRAY, None), ("go build -o /tmp/x", K, GRAY, None),
    ("go run x.go", K, GRAY, None),
    ("npm test --script-shell=/x", K, GRAY, None), ("npm install", K, GRAY, None), ("npm --prefix .. test", K, GRAY, None),
    ("npm run", K, GRAY, None),
    ("pytest --junitxml=/tmp/x", K, GRAY, None), ("python3 -m pytest --basetemp=/tmp/x", K, GRAY, None),
    ("python3 -m http.server", K, GRAY, None), ("python3 script.py", K, GRAY, None),
    ("python3 -X dev -m unittest", K, GRAY, None), ("bash", K, GRAY, None), ("bash -x scripts/test.sh", K, GRAY, None),
    ("bash scripts/new.sh", K, GRAY, None), ("python3 ../x.py", K, GRAY, None),
    # gray: writes outside the worktree, globs, cwd
    ("cp a /tmp/x", K, GRAY, "gray:outside-write"), ("mv a /tmp/x", K, GRAY, "gray:outside-write"),
    ("cp /etc/hosts .", K, GRAY, "gray:outside-write"), ("rm /tmp/x", K, GRAY, "gray:outside-write"),
    ("mkdir /tmp/d", K, GRAY, "gray:outside-write"), ("touch ../x", K, GRAY, "gray:outside-write"),
    ("echo x > ../out", K, GRAY, "gray:outside-write"), ("echo x > /tmp/out", K, GRAY, "gray:outside-write"),
    ("echo x >> /tmp/out", K, GRAY, "gray:outside-write"), ("ls 2> /tmp/e", K, GRAY, "gray:outside-write"),
    ("ls &> /tmp/e", K, GRAY, "gray:outside-write"), ("echo x > /dev/disk0", K, GRAY, "gray:outside-write"),
    ("ls > /dev/null", A, RULE, SH), ("ls > /dev/null 2>&1", A, RULE, SH), ("npm test 2>/dev/null", A, RULE, SH),
    ("echo x >/dev/stderr", A, RULE, SH), ("echo x > /dev/stdout", A, RULE, SH), ("echo x >&2", A, RULE, SH),
    ("cat se*/id_rsa", B, RULE, "block:secret-path"), ("cat se?/id_rsa", B, RULE, "block:secret-path"),
    ("cat [s]ec/id_rsa", B, RULE, "block:secret-path"), ("echo x > r*", K, GRAY, "gray:outside-write"),
    ("echo x > sub/.git/hooks/pre-commit", B, RULE, "block:git-hooks-config"),
    ("cp evil sub/.git/hooks/pre-commit", B, RULE, "block:git-hooks-config"),
    ("echo x > sub/.git/config", B, RULE, "block:git-hooks-config"),
    ("echo x > sub/.git/info/exclude", K, GRAY, "gray:outside-write"),
    ("git -C sub commit -m x", K, GRAY, None), ("cd sub && git commit -m x", K, GRAY, None),
    ("git -C sub status", K, GRAY, None),
    ("echo x > link/f", K, GRAY, "gray:outside-write"), ("echo x > .git/info/exclude", K, GRAY, "gray:outside-write"),
    ("rm .git/index", K, GRAY, "gray:outside-write"), ("cp a .git/x", K, GRAY, "gray:outside-write"),
    ("mv . x", K, GRAY, "gray:outside-write"),
    ("rm -rf", K, GRAY, None), ("rm -x a", K, GRAY, "gray:option"), ("rm --no-preserve-root -rf a", K, GRAY, "gray:option"),
    ("cp --parents a b", K, GRAY, "gray:option"), ("mkdir --mode=777 d", K, GRAY, "gray:option"),
    ("rm -rf ../*", K, GRAY, "gray:glob"), ("rm -rf .*", K, GRAY, "gray:glob"), ("rm -rf .g*", K, GRAY, "gray:glob"),
    ("cat /etc/*", K, GRAY, "gray:glob"), ("cat ~/.s?h/id", K, GRAY, "gray:glob"),
    ("cat */../../x", K, GRAY, "gray:glob"), ("cat ../*", K, GRAY, "gray:glob"),
    ("cat /tmp/x[ab]", K, GRAY, "gray:glob"), ("echo x > ../o*", K, GRAY, None),
    ("cat .git/*", K, GRAY, "gray:glob"),
    ("grep -r x ~", K, GRAY, "gray:secret-ancestor"), ("find / -name x", K, GRAY, "gray:secret-ancestor"),
    ("ls ~", K, GRAY, "gray:secret-ancestor"), ("ls ~/.config", K, GRAY, "gray:secret-ancestor"),
    ("cd /nonexistent; rm -rf x", K, GRAY, "gray:cwd-unknown"),
    ("cd src & rm -rf build", K, GRAY, "gray:cwd-unknown"),
    ("true && cd src; rm -rf ../x", K, GRAY, "gray:cwd-unknown"),
    ("cd src || rm -rf build", K, GRAY, "gray:cwd-unknown"),
    ("echo | cd src; rm -rf build", K, GRAY, "gray:cwd-unknown"),
    ("cd -; ls", K, GRAY, "gray:cwd-unknown"), ("cd link; ls ..", K, GRAY, "gray:cwd-unknown"),
    ("cd; rm -rf x", K, RULE, "ask:rm-outside"),
]


class GuardRulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp())
        cls.home = os.path.join(cls.tmp, "home")
        cls.wt = os.path.join(cls.home, "code", "wt")
        cls.outside = os.path.join(cls.tmp, "outside")
        for d in (".git/hooks", "src", "scripts"):
            os.makedirs(os.path.join(cls.wt, d))
        os.makedirs(cls.outside)
        os.symlink(cls.outside, os.path.join(cls.wt, "link"))
        os.makedirs(os.path.join(cls.home, ".ssh"))
        for f in (".ssh/id_rsa", ".bashrc"):
            open(os.path.join(cls.home, f), "w").close()
        os.symlink(os.path.join(cls.home, ".ssh"), os.path.join(cls.wt, "sec"))
        os.symlink(os.path.join(cls.home, ".bashrc"), os.path.join(cls.wt, "rc"))
        os.makedirs(os.path.join(cls.wt, "sub", ".git", "hooks"))
        for f in ("scripts/test.sh", "scripts/tool.py", "scripts/tool.js", "scripts/new.sh"):
            open(os.path.join(cls.wt, f), "w").close()
        cls.git = os.path.join(cls.wt, ".git")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.dest = None
        self.pushes = []
        self.pristine = {os.path.join(self.wt, "scripts", n) for n in ("test.sh", "tool.py", "tool.js")}
        self.ctx = self.make_ctx()

    def make_ctx(self, **kw):
        base = dict(worktree=self.wt, git_dir=self.git, common_dir=self.git, home=self.home,
                    protected_branches=["main", "master"], deploy_commands=["make deploy"],
                    push_dest=self.push_dest, is_pristine=lambda p: p in self.pristine)
        base.update(kw)
        return RuleContext(**base)

    def push_dest(self, remote, refspec):
        self.pushes.append((remote, refspec))
        return self.dest

    def shell(self, cmd, cwd=None, ctx=None):
        return decide(Call("pi", "bash", "shell", cmd, (), None, None, cwd or self.wt, self.wt), ctx or self.ctx)

    def other(self, kind, tool="t", paths=(), **kw):
        return decide(Call("pi", tool, kind, None, tuple(paths), kw.get("server"), None, self.wt, self.wt), self.ctx)

    def test_table(self):
        for cmd, decision, source, rule in SHELL:
            d = self.shell(cmd)
            self.assertEqual((d.decision, d.source), (decision, source), f"{cmd!r} -> {d}")
            if rule:
                self.assertEqual(d.rule, rule, f"{cmd!r} -> {d}")
            self.assertTrue(d.reason and "\n" not in d.reason, cmd)

    def test_table_is_big_enough_to_mean_something(self):
        self.assertGreater(len(SHELL), 300)

    def test_nothing_unknown_reaches_allow(self):
        for cmd in ("frobnicate", "git frob", "make deploy2 CC=x", "ls $(true)", "ls <(true)", "ls {a,b}"):
            self.assertNotEqual(self.shell(cmd).decision, "allow", cmd)

    def test_cd_back_and_forth_keeps_a_known_cwd(self):
        self.assertEqual(self.shell("cd src; cd ..; rm -rf build").decision, "allow")
        self.assertEqual(self.shell("cd src; cd ../..; rm -rf build").decision, "ask")

    def test_cd_inside_a_pipe_does_not_persist(self):
        self.assertEqual(self.shell("cd src | cat; rm -rf build").source, "gray")

    def test_cd_to_a_secret_is_blocked(self):
        self.assertEqual(self.shell("cd ~/.ssh").decision, "block")

    def test_block_beats_ask_beats_gray_beats_allow(self):
        self.assertEqual(self.shell("ls; echo $X; rm -rf /; herdr pane send x").decision, "block")
        self.assertEqual(self.shell("ls; echo $X; rm -rf /").source, "rule")
        self.assertEqual(self.shell("ls; echo $X").source, "gray")

    def test_script_pristine_or_gray_and_called_lazily(self):
        calls = []
        ctx = self.make_ctx(is_pristine=lambda p: calls.append(p) or False)
        self.assertEqual(self.shell("ls; echo x", ctx=ctx).decision, "allow")
        self.assertEqual(calls, [])
        d = self.shell("./scripts/test.sh", ctx=ctx)
        self.assertEqual((d.decision, d.source), ("ask", "gray"))
        self.assertEqual(calls, [os.path.join(self.wt, "scripts", "test.sh")])
        self.pristine.add(os.path.join(self.wt, "scripts", "new.sh"))
        self.assertEqual(self.shell("bash scripts/new.sh").decision, "allow")

    def test_script_outside_worktree_or_via_symlink_is_gray(self):
        open(os.path.join(self.outside, "s.sh"), "w").close()
        self.pristine.add(os.path.join(self.outside, "s.sh"))
        for cmd in ("./link/s.sh", "bash link/s.sh", "bash ../outside/s.sh"):
            self.assertEqual(self.shell(cmd).source, "gray", cmd)

    def test_bare_push_asks_on_a_protected_destination(self):
        self.dest = "main"
        d = self.shell("git push")
        self.assertEqual((d.decision, d.rule), ("ask", "ask:protected-branch"))
        self.dest = "origin/master"
        self.assertEqual(self.shell("git push origin").rule, "ask:protected-branch")
        self.dest = "feature"
        self.assertEqual(self.shell("git push").source, "gray")
        self.dest = None
        self.assertEqual(self.shell("git push").source, "gray")

    def test_push_dest_is_not_called_for_a_literal_protected_branch_or_unrelated_commands(self):
        self.shell("git push origin main")
        self.shell("git status")
        self.assertEqual(self.pushes, [])
        self.shell("git push origin HEAD")
        self.assertEqual(self.pushes, [("origin", "HEAD")])

    def test_push_with_unexpanded_branch_never_calls_the_broker_and_is_not_allowed(self):
        self.assertEqual(self.shell("git push origin $B").source, "gray")
        self.assertEqual(self.pushes, [])

    def test_custom_protected_branches_and_deploy_commands(self):
        ctx = self.make_ctx(protected_branches=["release"], deploy_commands=["./scripts/ship", "npm run deploy"])
        self.assertEqual(self.shell("git push origin main", ctx=ctx).source, "gray")
        self.assertEqual(self.shell("git push origin release", ctx=ctx).rule, "ask:protected-branch")
        self.assertEqual(self.shell("npm run deploy -- prod", ctx=ctx).rule, "ask:deploy")
        self.assertEqual(self.shell("./scripts/ship now", ctx=ctx).rule, "ask:deploy")
        self.assertEqual(self.shell("npm run build", ctx=ctx).decision, "allow")

    def test_git_dir_outside_the_worktree_for_a_linked_worktree(self):
        common = os.path.join(self.tmp, "main", ".git")
        gd = os.path.join(common, "worktrees", "wt")
        os.makedirs(os.path.join(common, "hooks"))
        os.makedirs(gd)
        ctx = self.make_ctx(git_dir=gd, common_dir=common)
        self.assertEqual(self.shell("echo x > %s/hooks/pre-commit" % common, ctx=ctx).rule, "block:git-hooks-config")
        self.assertEqual(self.shell("echo x > %s/config.worktree" % gd, ctx=ctx).rule, "block:git-hooks-config")
        self.assertEqual(self.shell("rm -rf %s" % common, ctx=ctx).rule, "ask:rm-outside")

    def test_dot_git_file_of_a_linked_worktree_is_guarded(self):
        wt2 = os.path.join(self.tmp, "wt2")
        os.makedirs(wt2)
        with open(os.path.join(wt2, ".git"), "w") as f:
            f.write("gitdir: /elsewhere\n")
        gd = os.path.join(self.tmp, "main2", ".git", "worktrees", "wt2")
        ctx = self.make_ctx(worktree=wt2, git_dir=gd, common_dir=os.path.dirname(os.path.dirname(gd)))
        d = decide(Call("pi", "bash", "shell", "echo 'gitdir: /x' > .git", (), None, None, wt2, wt2), ctx)
        self.assertEqual(d.decision, "block")
        d = decide(Call("pi", "write", "write", None, (os.path.join(wt2, ".git"),), None, None, wt2, wt2), ctx)
        self.assertEqual(d.decision, "block")
        d = decide(Call("pi", "bash", "shell", "echo hi > ok.txt", (), None, None, wt2, wt2), ctx)
        self.assertEqual(d.decision, "allow")

    def test_symlinked_secret_dir_is_still_blocked(self):
        os.makedirs(self.home, exist_ok=True)
        real = os.path.join(self.tmp, "realssh")
        os.makedirs(real)
        link = os.path.join(self.home, ".gnupg")
        os.symlink(real, link)
        self.addCleanup(os.unlink, link)
        self.assertEqual(self.shell("cat %s/k" % real).rule, "block:secret-path")

    def test_firmlink_paths_are_the_same_paths(self):
        fl = "/System/Volumes/Data"
        for cmd in (f"cat {fl}{self.home}/.ssh/id_rsa", f"ls {fl}{self.home}/.ssh", f"cat < {fl}{self.home}/.ssh/id_rsa"):
            self.assertEqual(self.shell(cmd).rule, "block:secret-path", cmd)
        self.assertEqual(self.shell(f"ls {fl}{self.home}/.config/herdr").rule, "block:canvas-control")
        self.assertEqual(self.shell(f"ls {fl}{self.home}").source, "gray")
        self.assertEqual(self.shell(f"echo x > {fl}{self.wt}/ok.txt").decision, "allow")
        self.assertEqual(self.shell(f"echo x > {fl}{self.git}/hooks/h").rule, "block:git-hooks-config")
        d = self.other("read", paths=[f"{fl}{self.home}/.ssh/id_rsa"])
        self.assertEqual(d.rule, "block:secret-path")
        self.assertEqual(self.other("write", paths=[f"{fl}{self.wt}/b.txt"]).decision, "allow")
        self.assertEqual(self.other("write", paths=[f"{fl}{self.git}/config"]).decision, "block")

    # non-shell
    def test_non_shell_reads(self):
        d = self.other("read", paths=[os.path.join(self.wt, "a.txt")])
        self.assertEqual((d.decision, d.source, d.rule), ("allow", "rule", "allow:read"))
        self.assertEqual(self.other("read", paths=["/etc/hosts"]).decision, "allow")
        d = self.other("read", paths=[os.path.join(self.home, ".ssh", "id")])
        self.assertEqual((d.decision, d.rule), ("block", "block:secret-path"))
        d = self.other("read", paths=[os.path.join(self.home, ".config", "herdr", "x")])
        self.assertEqual(d.rule, "block:canvas-control")
        self.assertEqual(self.other("read", paths=[self.home]).source, "gray")
        self.assertEqual(self.other("read", paths=[]).source, "gray")
        self.assertEqual(self.other("read", paths=[self.wt, os.path.join(self.home, ".aws")]).decision, "block")

    def test_non_shell_writes(self):
        d = self.other("write", paths=[os.path.join(self.wt, "b.txt")])
        self.assertEqual((d.decision, d.source, d.rule), ("allow", "rule", "allow:write"))
        self.assertEqual(self.other("write", paths=["/tmp/x"]).source, "gray")
        self.assertEqual(self.other("write", paths=[]).source, "gray")
        self.assertEqual(self.other("write", paths=[os.path.join(self.git, "hooks", "pre-commit")]).rule,
                         "block:git-hooks-config")
        self.assertEqual(self.other("write", paths=[os.path.join(self.git, "config")]).rule, "block:git-hooks-config")
        self.assertEqual(self.other("write", paths=[os.path.join(self.git, "info", "exclude")]).source, "gray")
        self.assertEqual(self.other("write", paths=[os.path.join(self.home, ".ssh", "authorized_keys")]).rule,
                         "block:secret-path")
        self.assertEqual(self.other("write", paths=[os.path.join(self.wt, "b.txt"), "/tmp/y"]).source, "gray")
        self.assertEqual(self.other("write", paths=[os.path.join(self.wt, "b"), os.path.join(self.git, "config")]).decision,
                         "block")
        self.assertEqual(self.other("write", paths=[self.wt]).source, "gray")

    def test_other_and_mcp(self):
        self.assertEqual(self.other("other", tool="frobnicate").rule, "gray:other")
        self.assertEqual(self.other("other", tool="oversized").rule, "gray:oversized")
        for name in ("send_email", "slack_post", "add_comment", "Message_user", "PUBLISH_page"):
            d = self.other("mcp", tool=name, server="s")
            self.assertEqual((d.decision, d.source, d.rule), ("ask", "rule", "ask:mcp-send"), name)
        d = self.other("mcp", tool="list_issues", server="s")
        self.assertEqual((d.decision, d.source, d.rule), ("ask", "gray", "gray:mcp"))
        self.assertEqual(self.other("bogus").source, "gray")


if __name__ == "__main__":
    unittest.main()
