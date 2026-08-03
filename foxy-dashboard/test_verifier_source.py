"""The dashboard must not tell a customer to run a path only staff can resolve.

Register #52. Until E2 (`56840d6`) there was no honest command to print: the
verifier existed only in the repository, so `python verifier/foxy_verify.py …`
was the best available and still resolved for nobody outside the team. E2 put the
verifier inside the export bundle, beside the ledger it checks, which makes the
relative repo path not merely unfollowable but wrong.
"""
from __future__ import annotations

import io
import pathlib

import pytest

HTML = pathlib.Path(__file__).with_name("foxy-audit-premium.html")


@pytest.fixture(scope="module")
def html() -> str:
    return io.open(HTML, encoding="utf-8").read()


def test_the_repo_path_is_never_offered_to_a_customer(html):
    """`verifier/` is a directory in this repository. A customer reading the
    dashboard has a browser and an export, not a checkout."""
    assert "verifier/foxy_verify.py" not in html, (
        "the dashboard is telling a customer to run a path that exists only "
        "inside a git checkout — the verifier ships in the export bundle now, "
        "so the command is `python foxy_verify.py foxy-audit-logs.json`")


def test_the_offline_check_names_a_command_that_works_from_the_bundle(html):
    """Both halves matter: the command, and where the file it names comes from.
    Naming the command without naming its source is how #27 stayed open."""
    assert "python foxy_verify.py foxy-audit-logs.json" in html
    assert "verification bundle" in html, (
        "the reader is given a command but not told which download provides it")
