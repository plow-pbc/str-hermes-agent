"""Does the night's compiled corpus reach its remote?

bin/nightly.sh deliberately commits nothing -- it runs inside the container,
where ~/hermes-vault.git is not mounted and no git credential exists. So the
promote is a host-side step, and for 22 days there was no step at all: 18 pages
rewritten and 6 new ones sat on one disk with no copy anywhere.

Behavioural: these run the real script against a temporary vault, its own bare
git dir and a bare 'remote', and assert on what lands there.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROMOTE = ROOT / "scripts" / "promote-vault"


def git(git_dir, work_tree, *args):
    return subprocess.run(
        ["git", f"--git-dir={git_dir}", f"--work-tree={work_tree}", *args],
        capture_output=True, text=True, check=False,
    )


@pytest.fixture
def vault(tmp_path):
    """A vault whose history lives outside it, with a bare remote behind that."""
    work = tmp_path / "hermes-vault"
    (work / "operations").mkdir(parents=True)
    (work / "_raw" / "hostex").mkdir(parents=True)
    (work / "index.md").write_text("# index\n")
    (work / "operations" / "a-property.md").write_text("Garage keypad: 4821#\n")
    (work / "properties").mkdir()
    (work / "properties" / "a-property.md").write_text("# A property\n\n## Operations\n")
    (work / "_raw" / "hostex" / "0-1.md").write_text("guest thread\n")
    (work / ".gitignore").write_text("/.env\n/AGENTS.md\n")

    git_dir = tmp_path / "hermes-vault.git"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(git_dir)], check=True)
    git(git_dir, work, "symbolic-ref", "HEAD", "refs/heads/main")
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        git(git_dir, work, "config", k, v)
    git(git_dir, work, "remote", "add", "origin", str(remote))
    git(git_dir, work, "add", "-A")
    git(git_dir, work, "commit", "-q", "-m", "seed")
    git(git_dir, work, "push", "-q", "origin", "HEAD:main")
    return work, git_dir, remote


def run_promote(vault_dir):
    """The git dir is `$vault.git` by convention -- which is what the fixture
    builds, so there is nothing to inject."""
    return subprocess.run([str(PROMOTE), str(vault_dir)],
                          capture_output=True, text=True)


def remote_head_files(remote):
    """NUL-framed: ls-tree applies core.quotePath too, so a line-split helper
    reports an accented path as its escaped spelling and the assertion misses
    a file that did land."""
    out = subprocess.run(["git", f"--git-dir={remote}", "ls-tree", "-r",
                          "--name-only", "-z", "main"],
                         capture_output=True, text=True, check=True)
    return set(filter(None, out.stdout.split("\0")))


def remote_commits(remote):
    out = subprocess.run(["git", f"--git-dir={remote}", "log", "--oneline", "main"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip().splitlines()


def test_a_quiet_night_promotes_nothing_and_says_so(vault):
    """A nightly that reports every quiet night is a nightly nobody reads."""
    work, git_dir, remote = vault
    before = remote_commits(remote)
    r = run_promote(work)
    assert r.returncode == 0, r.stderr
    assert "nothing to promote" in r.stdout
    assert remote_commits(remote) == before


def test_rewritten_pages_and_new_ones_reach_the_remote(vault):
    """The whole gap: the nightly writes both, and nothing carried either."""
    work, git_dir, remote = vault
    (work / "operations" / "a-property.md").write_text("Garage keypad: 4821#\nNew fact.\n")
    (work / "operations" / "b-property.md").write_text("A page tonight created.\n")
    (work / "_raw" / "hostex" / "0-2.md").write_text("a new thread\n")
    # The hubs are corpus too: the seed ships none, so a fresh host's restore
    # finds them only if a night carried them.
    (work / "properties" / "b-property.md").write_text("# B property\n\n## Operations\n")

    r = run_promote(work)
    assert r.returncode == 0, r.stderr
    landed = remote_head_files(remote)
    assert "operations/b-property.md" in landed
    assert "properties/b-property.md" in landed
    assert "_raw/hostex/0-2.md" in landed
    assert len(remote_commits(remote)) == 2


def test_door_codes_are_the_corpus_and_are_not_mistaken_for_secrets(vault):
    """Keypad and lock codes are what this vault exists to hold. A scanner that
    refuses them refuses every night and gets switched off."""
    work, git_dir, remote = vault
    (work / "operations" / "b-property.md").write_text(
        "Front door code 8823. Lockbox 4471#. Wifi password: sunnyvale2024.\n")
    r = run_promote(work)
    assert r.returncode == 0, r.stderr
    assert "operations/b-property.md" in remote_head_files(remote)


def plant(work, git_dir, state, secret, nul):
    """Put `secret` into the vault in one of the states `push` would publish
    from. `add -A`/`commit` here are the fixture, not the thing under test --
    they reproduce a run that died between the two, or a night that committed
    and never reached origin."""
    page = work / ("_raw/hostex/0-2.md" if nul else "operations/b-property.md")
    if nul:
        # git calls a file binary on a NUL in its first 8000 bytes and emits no
        # content for it; grep then declines to match inside NUL-bearing input
        # at all on some implementations. _raw/hostex/*.md is guest-authored, so
        # a \u0000 in a message body decodes to a real NUL -- a guest could
        # otherwise make the page holding their own text invisible to the scan.
        page.write_bytes(b"guest wrote: \x00 and pasted " + secret.encode() + b"\n")
    else:
        page.write_text(f"The owner sent their key: {secret}\n")
    if state == "merged":
        # The secret exists only in the MERGE RESOLUTION -- in neither parent --
        # which is the shape `git log -p` prints no patch for at all.
        page.write_text("the version main had\n")
        git(git_dir, work, "add", "-A")
        git(git_dir, work, "commit", "-q", "-m", "a night on main")
        git(git_dir, work, "checkout", "-q", "-b", "side", "HEAD~1")
        page.write_text("the version the other side had\n")
        git(git_dir, work, "add", "-A")
        git(git_dir, work, "commit", "-q", "-m", "a night on the other side")
        git(git_dir, work, "checkout", "-q", "main")
        git(git_dir, work, "merge", "side")  # conflicts, by construction
        page.write_text(f"resolved by hand: {secret}\n")
        git(git_dir, work, "add", "-A")
        git(git_dir, work, "commit", "-q", "-m", "hand-resolved merge")
        # Without this the row degrades silently into the `committed` one: any
        # setup step here can fail (git() is check=False), or the merge can
        # complete cleanly and auto-commit, and the trailing commit is then an
        # ordinary single-parent one that plain `log -p` catches — green while
        # asserting nothing about merges, which is this repo's own bug class.
        parents = git(git_dir, work, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        assert len(parents) == 3, f"HEAD must be a merge or --cc is untested: {parents}"
        return
    if state != "worktree":
        git(git_dir, work, "add", "-A")
    if state == "committed" or state == "removed":
        git(git_dir, work, "commit", "-q", "-m", "committed locally, unscanned")
    if state == "removed":
        page.write_text("the key was taken back out\n")
        git(git_dir, work, "add", "-A")
        git(git_dir, work, "commit", "-q", "-m", "and removed again")


@pytest.mark.parametrize("state,secret,nul", [
    ("worktree",  "ghp_" + "a" * 36,        False),
    ("staged",    "ghp_" + "b" * 36,        False),
    ("committed", "github_pat_" + "c" * 22, False),
    ("worktree",  "ghp_" + "d" * 36,        True),
    ("removed",   "ghp_" + "e" * 36,        False),
    ("merged",    "ghp_" + "f" * 36,        False),
], ids=["worktree", "staged", "committed-fine-grained-pat", "nul-bearing",
        "introduced-then-removed", "merge-resolution"])
def test_a_credential_is_refused_from_every_state_push_publishes(
        vault, state, secret, nul):
    """The pages are LLM-authored from raw guest threads, so a token pasted into
    a conversation would otherwise be compiled into a page and pushed. One row
    per state the scan has to reach, because each one has been a fail-open:

    - `staged` -- a run that died between `add -A` and `commit` leaves an index
      the next night's worktree matches, so a worktree-vs-index scan is empty.
    - `committed` -- a local commit the remote never received, and a
      fine-grained `github_pat_`, which the classic-`ghp_`-only expression let
      straight through.
    - `removed` -- introduced in one commit and taken back out in the next: gone
      from every tree, still in the history `push` publishes.
    - `merged` -- present only in a hand-resolved merge, so it is in neither
      parent and `git log -p` prints no patch for the commit carrying it. That
      is where a `git pull` after a non-ff push rejection puts a conflict fix.
    """
    work, git_dir, remote = vault
    before = remote_commits(remote)
    plant(work, git_dir, state, secret, nul)

    r = run_promote(work)
    assert r.returncode != 0
    assert "credential" in r.stderr
    assert remote_commits(remote) == before, "it published anyway"


def test_a_new_top_level_path_is_refused_rather_than_swept_in(vault):
    """A new file under a tracked directory is what a nightly produces. A new
    top-level path is a human decision or a tool writing somewhere nobody
    expected, and staging it blind is how an unrelated tree lands in a repo."""
    work, git_dir, remote = vault
    before = remote_commits(remote)
    (work / "node_modules").mkdir()
    (work / "node_modules" / "junk.js").write_text("x\n")
    (work / "operations" / "b-property.md").write_text("a real page too\n")

    r = run_promote(work)
    assert r.returncode != 0
    assert "node_modules" in r.stderr
    assert remote_commits(remote) == before


def test_a_git_dir_inside_the_worktree_is_refused(vault):
    """History lives outside the worktree because an ingest turn once ran
    `git restore --source=HEAD` over pages it judged missing. A .git appearing
    inside means something re-attached it, and promoting would promote whatever
    it did."""
    work, git_dir, remote = vault
    before = remote_commits(remote)
    (work / "operations" / "b-property.md").write_text("a page\n")
    (work / ".git").mkdir()
    r = run_promote(work)
    assert r.returncode != 0
    assert "must not be a git repo" in r.stderr
    assert remote_commits(remote) == before


def test_a_night_whose_push_failed_is_recovered_by_the_next_one(vault):
    """'Nothing to promote' was defined against the worktree alone, so a failed
    push left a commit sitting locally and every night after it reported green.
    That is this script's own failure mode -- the only copy back on one disk --
    reported as success."""
    work, git_dir, remote = vault
    (work / "operations" / "b-property.md").write_text("tonight's page\n")

    # A night whose push cannot reach the remote: the work is committed locally
    # and the run fails loudly.
    git(git_dir, work, "remote", "set-url", "origin", "/nonexistent/remote.git")
    failed = run_promote(work)
    assert failed.returncode != 0
    assert "operations/b-property.md" not in remote_head_files(remote)

    # The next night, worktree clean, remote reachable again.
    git(git_dir, work, "remote", "set-url", "origin", str(remote))
    r = run_promote(work)
    assert r.returncode == 0, r.stderr
    assert "operations/b-property.md" in remote_head_files(remote), \
        "the stranded commit was never recovered"


def test_a_quiet_night_that_cannot_reach_the_remote_fails_rather_than_reporting_green(vault):
    """Silence must not mean success: a push that cannot reach the remote is the
    one thing 'nothing to promote' must never be printed over."""
    work, git_dir, remote = vault
    git(git_dir, work, "remote", "set-url", "origin", "/nonexistent/remote.git")
    r = run_promote(work)
    assert r.returncode != 0
    assert "nothing to promote" not in r.stdout


def test_an_accented_page_name_promotes_like_any_other(vault):
    """git applies core.quotePath, so a path holding any non-ASCII byte prints
    as "op\\303\\251rations/x.md". Read as lines, the top-level check saw a
    quoted fragment, missed, and refused every promote from that night on --
    naming a path that does not exist. The pages are LLM-authored from guest
    threads; an accented property name is an ordinary night."""
    work, git_dir, remote = vault
    (work / "operations" / "café-münchen-property.md").write_text("a page\n")
    r = run_promote(work)
    assert r.returncode == 0, r.stderr
    assert "operations/café-münchen-property.md" in remote_head_files(remote)


def test_a_staged_but_uncommitted_night_is_promoted_by_the_next_one(vault):
    """The other half of the same state: clean content left in the index must
    reach the remote rather than sitting there forever."""
    work, git_dir, remote = vault
    (work / "operations" / "b-property.md").write_text("an ordinary page\n")
    git(git_dir, work, "add", "-A")

    r = run_promote(work)
    assert r.returncode == 0, r.stderr
    assert "operations/b-property.md" in remote_head_files(remote)


def test_a_commit_the_remote_never_received_is_pushed_without_a_second_commit(vault):
    """One expression covers this too: worktree-vs-remote is non-empty even
    though the index matches HEAD. Committing again would fail with 'nothing to
    commit'; the push is what that night is missing."""
    work, git_dir, remote = vault
    (work / "operations" / "b-property.md").write_text("committed but never sent\n")
    git(git_dir, work, "add", "-A")
    git(git_dir, work, "commit", "-q", "-m", "a night that never reached origin")
    before = len(remote_commits(remote))

    r = run_promote(work)
    assert r.returncode == 0, r.stderr
    assert "operations/b-property.md" in remote_head_files(remote)
    assert len(remote_commits(remote)) == before + 1, \
        "it made a second, empty commit instead of pushing the one that existed"
