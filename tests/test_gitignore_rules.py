"""What git ignores, asked of git.

Not a privacy control — the scanner that was one is gone. This guards what the
repo itself creates and must not commit.

`.env` is the case worth stating: the bootstrap in the README has the operator
write one at the repo root for Compose to read, so the file does get created
here, and the rule is what keeps it out of a commit. Property access facts do
live in pages, deliberately; a service token never does, and this is what keeps
that line.

Every rule here fails silently in one direction or the other, which is why they
are pinned behaviourally rather than as text. Tightening the secrets block drops
vault config from a fresh checkout with no error; widening the negation that
carves it back out commits the token.

Asserting that a line appears in `.gitignore` cannot see any of that. It reads
the same whether or not the rule still has the effect it was written for: a
later pattern re-shadowing an earlier negation leaves both lines present, and a
negation under an excluded directory is inert because git cannot re-include a
file whose parent is excluded. So the question is put to `git check-ignore`,
which is the thing that actually decides.

`-q`, not `-v`: `-v` prints the matching pattern whether it ignores or negates,
so it exits 0 for a path that is *not* ignored and cannot answer this at all.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        # The raw conversation cache: regenerable, and noise in a diff.
        ("vault/_raw/0-123.md", True),
        # Runtime state, rewritten by the gateway on every boot.
        ("channel_directory.json", True),
        # Written at the repo root by the README's bootstrap, for Compose.
        (".env", True),
        ("auth.json", True),
        # Created in-tree by `just test-wiki`, every run, and holding a full
        # copy of the staged conversations.
        (".e2e-vault/_raw/hostex/0-1.md", True),
        # Vault seed config — path, categories, page limits. No credentials,
        # and the deploy installs it into the runtime vault. It is named `.env`
        # only because that is how the obsidian-wiki tools find it, which puts
        # it under the rule above; a negation carves it back out.
        ("runtime/vault-seed/.env", False),
        # The `.env.*` half of the secrets block, which had no row either.
        (".env.local", True),
    ],
)
def test_the_ignore_rules_bite_where_they_should(path, ignored):
    decided = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=REPO)
    assert (decided.returncode == 0) is ignored


def test_nothing_tracked_is_also_ignored():
    """The rules above are silent on a file already in the index.

    `check-ignore --no-index` answers about a *pattern*; .gitignore has no
    effect on a tracked file, so anything force-added, renamed in, or added
    before its rule stays tracked forever while every assertion above keeps
    passing.

    Asked of git rather than restated in Python. Three consecutive rounds went
    into correcting a hand-maintained approximation of the ignore rules here --
    deleted as a duplicate, restored matching `.env`, widened to `.env*` -- and
    each shape was wrong in both directions at once: `.envrc` matched a filter
    the secrets block never claimed, while `*.token`, `*.secret` and `auth.json`
    were invisible to any dotenv filter. `-c -i --exclude-standard` lists
    tracked files the ignore rules match, which is the whole class, with no
    expected-survivor list to drift and nothing to re-edit when .gitignore
    changes.

    `check=True` is load-bearing: the expected value is the empty list, which
    is also what a FAILED git call produces. Without it, a git version that
    rejects the flag combination, or a run from a checkout without `.git`,
    leaves stdout empty and the suite green while nothing was checked. Verifying
    non-vacuity by force-adding a `.env.probe` cannot see that, because it only
    exercises the path where git succeeds.
    """
    tracked_but_ignored = subprocess.run(
        ["git", "ls-files", "-c", "-i", "--exclude-standard"],
        cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    assert tracked_but_ignored == []
