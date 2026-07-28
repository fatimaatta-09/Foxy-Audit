"""D14 — packaging prep: the version stamp and what the bundle carries.

The owner runs the actual builds, so what is testable here is the part that
has silently gone wrong before: a build that ships without its assets, one
that reports a version nobody set, or — as this branch first shipped it — two
workflows racing to build the same release.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_SPEC = (_HERE / "omni_fox.spec").read_text(encoding="utf-8")
_WORKFLOWS = _ROOT / ".github" / "workflows"
_RELEASE = _WORKFLOWS / "release.yml"


@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ══ the version stamp ═══════════════════════════════════════════════════════
def test_there_is_exactly_one_source_of_the_version():
    version = (_ROOT / "VERSION")
    assert version.is_file(), "VERSION is the single source; it must exist"
    text = version.read_text(encoding="utf-8").strip()
    assert text and len(text) <= 32


def test_the_app_reports_the_committed_version(app):
    import foxy_tokens
    assert foxy_tokens.APP_VERSION == \
        (_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_the_settings_card_stops_saying_unknown(app):
    import foxy_tokens
    import settings_data as sd
    line = sd.desktop_version_line(foxy_tokens.APP_VERSION)
    assert "unknown" not in line and foxy_tokens.APP_VERSION in line


def test_a_missing_version_file_is_blank_not_invented(app, monkeypatch):
    """The brief was explicit: source it from the tag or a file, never compute
    one. A build that invents "0.0.0-dev" or a timestamp puts a number nobody
    can trace into a bug report — the same class of lie as invented data."""
    import foxy_tokens
    monkeypatch.setattr(foxy_tokens, "resource_path", lambda _p: "/nope/VERSION")
    monkeypatch.setattr(foxy_tokens.os.path, "dirname", lambda _p: "/nope")
    assert foxy_tokens._read_version() == ""


def test_the_version_reader_computes_nothing():
    """Structural: no clock, no git, no random in the version path."""
    tree = ast.parse((_HERE / "foxy_tokens.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_read_version")
    names = {getattr(c.func, "attr", None) or getattr(c.func, "id", None)
             for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert not names & {"now", "today", "time", "uuid4", "check_output",
                        "run", "getenv"}


# ══ what the bundle carries ═════════════════════════════════════════════════
@pytest.mark.parametrize("asset", ["ultimate_fox_spritesheet.png", "logo.png",
                                   "VERSION"])
def test_the_spec_bundles_every_runtime_asset(asset):
    assert asset in _SPEC, f"{asset} is resolved at runtime but never bundled"


def test_the_repo_root_fonts_are_findable_from_the_spec():
    """A past spec bug meant fonts/ was never bundled and the shipped build
    silently fell back to system fonts. The fonts live at the REPO ROOT, one
    level up from desktop/ — assert they are actually there, so the spec's
    `os.pardir` walk resolves to something."""
    fonts = _ROOT / "fonts"
    assert fonts.is_dir(), "the repo-root fonts/ dir moved"
    names = {p.name for p in fonts.glob("*.ttf")}
    assert {"Unbounded-700.ttf", "SpaceMono-400.ttf"} <= names
    assert "os.pardir" in _SPEC and '"fonts"' in _SPEC


def test_the_mac_bundle_id_matches_the_autostart_agent():
    """A LaunchAgent labelled differently from the .app is a second identity
    macOS will happily keep alongside the first."""
    import autostart
    assert f'"{autostart.BUNDLE_ID}"' in _SPEC


def test_the_bundle_is_only_built_on_macos():
    """BUNDLE() on Windows or Linux emits a stray dist/FoxyAudit.app that is
    not a runnable anything, and the workflow would upload it."""
    guarded = _SPEC.split('if sys.platform == "darwin":')
    assert len(guarded) == 2 and "BUNDLE(" in guarded[1]


def test_the_linux_desktop_entry_is_shipped_and_well_formed():
    entry = (_HERE / "foxy-audit.desktop").read_text(encoding="utf-8")
    assert entry.startswith("[Desktop Entry]")
    for key in ("Type=Application", "Name=", "Exec=", "Icon=",
                "Terminal=false"):
        assert key in entry, key


# ══ the release workflow ════════════════════════════════════════════════════
def _workflow() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(_RELEASE.read_text(encoding="utf-8"))


def test_exactly_one_workflow_builds_the_desktop_app():
    """D14 first shipped its own `desktop-release.yml` on `tags: ["v*"]` —
    the trigger `release.yml` was already running a three-OS PyInstaller
    matrix on. One tag push would have run two of them and raced for the same
    release assets. The check is the file list, not a comment, because the
    failure mode here was writing a new workflow without reading the old one.
    """
    builders = sorted(p.name for p in _WORKFLOWS.glob("*.y*ml")
                      if "pyinstaller" in p.read_text(encoding="utf-8").lower())
    assert builders == ["release.yml"]


def test_the_release_workflow_never_runs_on_a_plain_commit():
    """Three PyInstaller runners per push would swamp the queue the real gate
    depends on."""
    triggers = _workflow()[True]          # PyYAML parses bare `on:` as True
    assert set(triggers) <= {"push", "workflow_dispatch"}
    assert list(triggers["push"]) == ["tags"]
    assert triggers["push"]["tags"] == ["v*"]


def test_all_three_platforms_are_built():
    matrix = _workflow()["jobs"]["build-desktop"]["strategy"]["matrix"]
    assert set(matrix["os"]) == {
        "windows-latest", "macos-latest", "ubuntu-latest"}


def test_one_platform_failing_still_yields_the_other_two():
    assert _workflow()["jobs"]["build-desktop"]["strategy"]["fail-fast"] is False


def test_the_tag_is_checked_against_the_version_before_anything_is_built():
    jobs = _workflow()["jobs"]
    assert "check-version" in jobs
    for job in ("build-sdk", "build-desktop"):
        assert jobs[job]["needs"] == "check-version", job


def test_the_consolidation_kept_every_piece_that_only_existed_in_one_file():
    """The union the two workflows had to preserve between them. Pinned so a
    future tidy-up cannot quietly drop the half that was only in the other
    file — which is how the duplicate arose in the first place."""
    text = _RELEASE.read_text(encoding="utf-8")
    for needle, why in (
        ("contents: write", "needed to attach assets to a release"),
        ("desktop/installer.iss", "the Inno Setup installer"),
        ("WINDOWS_CERT_BASE64", "the optional Windows signing hook"),
        ("foxy-audit.desktop", "the Linux desktop entry"),
        ("Analysis-00.toc", "the bundle asset verification"),
    ):
        assert needle in text, why


def test_the_two_committed_versions_agree():
    """One tag namespace releases both artefacts, so `VERSION` (the desktop
    app) and `sdk/pyproject.toml` must carry the same number — `check-version`
    refuses the tag otherwise, and failing here is cheaper than failing there.
    """
    version = (_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyproject = (_ROOT / "sdk" / "pyproject.toml").read_text(encoding="utf-8")
    sdk = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    assert sdk and sdk.group(1) == version
