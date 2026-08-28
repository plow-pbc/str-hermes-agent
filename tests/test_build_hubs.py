"""What a property hub lists, and where that list comes from.

The hub is how an agent starting from a property reaches the operational facts
for it. The generator writes `operations/` and `index.md`; the hub was
hand-authored in the seed and installed over the runtime vault on every deploy,
so a page created by a nightly was reachable from the index and invisible from
its own property until somebody noticed. Deriving the list from the pages that
exist removes the drift rather than detecting it.

Behavioural: these run the real script against a temporary vault and assert on
the files it produces.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD_HUBS = ROOT / "bin" / "build-hubs"

HUB = """---
type: Property
title: {title}
category: properties
---

# {title}

Operational knowledge for this property lives in `operations/{slug}-*` and links
back here.

## Operations

## Notes

Owner prefers text over calls.
"""

PAGE = """---
type: Operation
title: {title}
category: operations
---

# {title}
"""


# The hub every failure case leaves alone. It sorts before the one the cases
# break, so it is derived first — and under a generator that wrote as it went,
# it would already be on disk by the time the next hub aborted the run.
FIRST_HUB = HUB.format(title="Cedar Cabin", slug="cedar-cabin")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Two hubs, the way the seed ships them after this change."""
    (tmp_path / "properties").mkdir()
    (tmp_path / "operations").mkdir()
    (tmp_path / "properties" / "cedar-cabin.md").write_text(FIRST_HUB)
    (tmp_path / "properties" / "lake-house.md").write_text(
        HUB.format(title="Lake House", slug="lake-house")
    )
    for slug, title in (
        ("cedar-cabin-access-and-backup-codes", "Cedar Cabin access and backup codes"),
        (
            "lake-house-parking-and-snowplow",
            "Lake House parking and snowplow rules",
        ),
        ("lake-house-thermostat-and-hvac", "Lake House thermostat and HVAC"),
    ):
        (tmp_path / "operations" / f"{slug}.md").write_text(PAGE.format(title=title))
    return tmp_path


def build(vault: Path) -> subprocess.CompletedProcess:
    return subprocess.run([str(BUILD_HUBS), str(vault)], text=True, capture_output=True)


def hub_text(vault: Path) -> str:
    return (vault / "properties" / "lake-house.md").read_text()


def test_the_generated_hub_lists_every_page_and_keeps_the_prose(vault: Path) -> None:
    """One artifact, so one contract test rather than three of the same run.

    The live failure is the links: both pages are on disk and neither is in the
    hub the fixture ships, which is the state a deploy leaves behind. The label
    is the page's own `title:` minus the property — a reader scans the hub for
    the operation, and every bullet would otherwise open with the same three
    words. The prose is checked on both sides of the generated region, since a
    hub is free to carry a section after its list and replacing to end-of-file
    would eat it.
    """
    assert build(vault).returncode == 0
    text = hub_text(vault)
    assert (
        "- [Parking and snowplow rules]"
        "(../operations/lake-house-parking-and-snowplow.md)" in text
    )
    assert (
        "- [Thermostat and HVAC]"
        "(../operations/lake-house-thermostat-and-hvac.md)" in text
    )
    assert text.startswith("---\ntype: Property\ntitle: Lake House\n")
    assert "Operational knowledge for this property lives in" in text
    assert text.endswith("## Notes\n\nOwner prefers text over calls.\n")


def test_a_second_run_changes_nothing_unless_a_page_went_away(vault: Path) -> None:
    """Two callers write this file — the nightly and the deploy.

    A generator whose output drifts between runs would put them in a fight the
    operator sees as a hub that changes every night for no reason.

    The shrink case is the one the nightly actually produces: rename or remove
    an operations page and the new hub is SHORTER than the file on disk. The
    open no longer carries O_TRUNC, so an explicit truncate is the only thing
    clearing the old bytes — without it the hub keeps the tail of its previous
    self past the end of the write and still reads as well-formed to every
    other assertion here, which is the shape this repo blocks on.
    """
    build(vault)
    once = hub_text(vault)
    build(vault)
    assert hub_text(vault) == once

    (vault / "operations" / "lake-house-thermostat-and-hvac.md").unlink()
    build(vault)
    text = hub_text(vault)
    assert "thermostat-and-hvac" not in text
    assert text.endswith("## Notes\n\nOwner prefers text over calls.\n")


def test_a_hub_swapped_for_a_symlink_is_not_written_through(vault: Path) -> None:
    """The deploy runs this on the host, as the operator, over a live vault.

    The gateway stays up during a restore, so a terminal-enabled turn can swap
    a hub for a relative symlink between the read and the write. It resolves to
    nothing inside the container and to a real host file outside it —
    `../.ssh/authorized_keys` being the one that locks the operator out. The
    decoy is a valid hub, so the run gets all the way to the write with nothing
    to complain about; only the open refuses.
    """
    outside = vault.parent / "outside-the-vault.md"
    outside.write_text(HUB.format(title="Lake House", slug="lake-house"))
    hub = vault / "properties" / "lake-house.md"
    hub.unlink()
    hub.symlink_to(f"../../{outside.name}")

    result = build(vault)

    assert result.returncode == 1
    assert "lake-house.md" in result.stderr
    assert outside.read_text() == HUB.format(
        title="Lake House", slug="lake-house"
    ), "wrote through the link, out of the vault"
    # The same all-or-nothing line every failure row carries: the refusal is
    # still ahead of the first write, not inside the loop.
    assert (vault / "properties" / "cedar-cabin.md").read_text() == FIRST_HUB


@pytest.mark.parametrize(
    "break_it, expected",
    [
        pytest.param(
            lambda v: (v / "properties" / "lake-house.md").write_text(
                "---\ntype: Property\ntitle: Lake House\n---\n\n# Lake House\n"
            ),
            "lake-house.md",
            id="hub-without-an-operations-heading",
        ),
        pytest.param(
            lambda v: (
                v / "operations" / "lake-house-parking-and-snowplow.md"
            ).write_text(PAGE.format(title="Parking and snowplow rules")),
            "lake-house-parking-and-snowplow.md",
            id="page-title-missing-the-property-prefix",
        ),
        pytest.param(
            lambda v: (
                v / "operations" / "lake-house-parking-and-snowplow.md"
            ).write_text(PAGE.format(title="Lake House")),
            "lake-house-parking-and-snowplow.md",
            id="page-title-leaving-no-label-behind",
        ),
        pytest.param(
            lambda v: (
                v / "operations" / "lake-house-parking-and-snowplow.md"
            ).write_text(PAGE.format(title="Lake Housed parking")),
            "lake-house-parking-and-snowplow.md",
            id="page-title-whose-prefix-stops-mid-word",
        ),
        pytest.param(
            lambda v: shutil.rmtree(v / "operations"),
            "operations/",
            id="vault-with-no-operations-directory",
        ),
        pytest.param(
            lambda v: (shutil.move(v / "properties", v.parent / "outside"),
                       (v / "properties").symlink_to("../outside")),
            "properties/",
            id="properties-directory-swapped-for-a-symlink",
        ),
    ],
)
def test_a_hub_it_cannot_derive_fails_loudly(vault, break_it, expected) -> None:
    """No fallback label, no skipped hub, no empty list written on a bad path.

    Each shape means the vault is not what the caller thinks it is, and each
    one has a silent form: a guessed label, or — for a wrong vault path, which
    both callers take from outside — every hub rewritten with nothing in it,
    which is the drift this script exists to remove arriving by its own hand.
    """
    break_it(vault)
    result = build(vault)
    assert result.returncode == 1
    assert expected in result.stderr
    # Nothing written, not even the hub that derived cleanly before the failure.
    # The deploy caller reaches this script with the lists already emptied by
    # the seed overlay, so a run that wrote as it went would leave the operator
    # with some hubs rebuilt and the rest linking nothing.
    assert (vault / "properties" / "cedar-cabin.md").read_text() == FIRST_HUB
