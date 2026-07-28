"""D14 — packaging prep: the version stamp and what the bundle carries.

The owner runs the actual builds, so what is testable here is the part that
has silently gone wrong before: a build that ships without its assets, or one
that reports a version nobody set.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_SPEC = (_HERE / "omni_fox.spec").read_text(encoding="utf-8")


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
    path = _ROOT / ".github" / "workflows" / "desktop-release.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_release_workflow_never_runs_on_a_plain_commit():
    """Three PyInstaller runners per push would swamp the queue the real gate
    depends on."""
    triggers = _workflow()[True]          # PyYAML parses bare `on:` as True
    assert set(triggers) <= {"push", "workflow_dispatch"}
    assert list(triggers["push"]) == ["tags"]
    assert triggers["push"]["tags"] == ["v*"]


def test_all_three_platforms_are_built():
    matrix = _workflow()["jobs"]["build"]["strategy"]["matrix"]["include"]
    assert {row["os"] for row in matrix} == {
        "windows-latest", "macos-latest", "ubuntu-latest"}


def test_one_platform_failing_still_yields_the_other_two():
    assert _workflow()["jobs"]["build"]["strategy"]["fail-fast"] is False


def test_the_tag_is_checked_against_the_version_before_anything_is_built():
    jobs = _workflow()["jobs"]
    assert "check-version" in jobs
    assert jobs["build"]["needs"] == "check-version"
