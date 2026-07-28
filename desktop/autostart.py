"""Foxy Audit desktop — "Start Foxy when the PC starts" (D13, plan §9.2).

Three platforms, three completely different mechanisms, one boolean:

    Windows   HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
    macOS     ~/Library/LaunchAgents/tech.foxyaudit.desktop.plist
    Linux     ~/.config/autostart/foxy-audit.desktop

**The state lives in the OS, not in QSettings.** That is the whole design.
A user can revoke autostart from Task Manager, `launchctl`, or GNOME Tweaks
without the app ever running, and a copy of the answer in our own settings
would then be a stale claim about someone else's machine. `is_enabled()`
always asks the real store — which is why the dialog re-reads it on open
rather than trusting what it wrote last time ("state drift", per the plan).

**The backend is injectable, and the suite injects.** `Autostart(backend)` and
`SettingsDialog(autostart=…)` both take one; `MemoryBackend` is what the tests
use, and `test_d13_companion_settings.py` asserts no test constructs a real
one. The seam is the constructor rather than an afterthought because the D3
round found the shell tests writing the developer's actual
`HKCU\\Software\\OmniAwareFox` — and this module edits login items, which is
further outside the app than QSettings ever was. Note what the seam does NOT
cover: the default `Autostart()` reads the real store, so any code path that
skips the argument is talking to the live machine. That is correct for the
app and wrong for a test, which is why the guard test exists.

Every operation returns a bool rather than raising: a refused registry write
or a read-only home directory is a thing to TELL the user about, in the one
place they asked for it, not a crash on the way into a settings dialog.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

#: The name the entry carries in whichever store the platform uses.
APP_NAME = "FoxyAudit"
#: Matches `omni_fox.spec`'s BUNDLE identifier, so macOS sees one app.
BUNDLE_ID = "tech.foxyaudit.desktop"
LINUX_DESKTOP_FILE = "foxy-audit.desktop"


def launch_command() -> list[str]:
    """How to start Foxy again from a cold boot.

    Frozen (PyInstaller), `sys.executable` IS the app, so it is the whole
    command. From source it is a Python interpreter, which on its own would
    start an interactive REPL at login — the script has to come with it.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable]
    entry = Path(__file__).resolve().parent / "omni_fox.py"
    return [sys.executable, str(entry)]


def _quote(parts: list[str]) -> str:
    """A single command line. Windows wants double quotes around paths with
    spaces (`C:\\Program Files\\…`); `shlex.quote` would emit POSIX single
    quotes, which `cmd` treats as part of the path."""
    if sys.platform.startswith("win"):
        return " ".join(f'"{p}"' if " " in p else p for p in parts)
    return " ".join(shlex.quote(p) for p in parts)


# ── backends ────────────────────────────────────────────────────────────────
class MemoryBackend:
    """The test store. Also what an unsupported platform gets, so callers
    never have to special-case `None`."""

    supported = False
    location = "not available on this platform"

    def __init__(self, enabled: bool = False, writable: bool = True):
        self._enabled = enabled
        self._writable = writable
        self.command = ""

    def read(self) -> bool:
        return self._enabled

    def write(self, command: str) -> bool:
        if not self._writable:
            return False
        self._enabled, self.command = True, command
        return True

    def remove(self) -> bool:
        if not self._writable:
            return False
        self._enabled = False
        return True


class WindowsRegistryBackend:
    """`HKCU\\…\\Run` — per-user, needs no elevation, and is exactly what the
    Startup tab of Task Manager lists, so a user who turns it off there and
    the toggle here can never disagree for long."""

    supported = True
    KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    location = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"

    def __init__(self, root=None, key: str | None = None):
        import winreg
        self._winreg = winreg
        self._root = root if root is not None else winreg.HKEY_CURRENT_USER
        self._key = key or self.KEY

    def read(self) -> bool:
        try:
            with self._winreg.OpenKey(self._root, self._key) as handle:
                value, _type = self._winreg.QueryValueEx(handle, APP_NAME)
                return bool(value)
        except OSError:
            return False

    def write(self, command: str) -> bool:
        try:
            with self._winreg.CreateKey(self._root, self._key) as handle:
                self._winreg.SetValueEx(handle, APP_NAME, 0,
                                        self._winreg.REG_SZ, command)
            return True
        except OSError:
            return False

    def remove(self) -> bool:
        try:
            with self._winreg.OpenKey(self._root, self._key, 0,
                                      self._winreg.KEY_SET_VALUE) as handle:
                self._winreg.DeleteValue(handle, APP_NAME)
            return True
        except FileNotFoundError:
            return True             # already gone is the state we wanted
        except OSError:
            return False


class _FileBackend:
    """Shared shape for the two platforms whose answer is "a file exists"."""

    supported = True

    def __init__(self, path: Path):
        self.path = Path(path)
        self.location = str(self.path)

    def read(self) -> bool:
        return self.path.is_file()

    def write(self, command: str) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(self._contents(command), encoding="utf-8")
            return True
        except OSError:
            return False

    def remove(self) -> bool:
        try:
            self.path.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def _contents(self, command: str) -> str:      # pragma: no cover - abstract
        raise NotImplementedError


class LaunchAgentBackend(_FileBackend):
    """A LaunchAgent plist. `RunAtLoad` is the login hook; `KeepAlive` is
    deliberately absent — relaunching a companion the user just quit would be
    a fox that cannot be dismissed."""

    def __init__(self, home: Path | None = None):
        base = Path(home) if home else Path.home()
        super().__init__(base / "Library" / "LaunchAgents"
                         / f"{BUNDLE_ID}.plist")

    def _contents(self, command: str) -> str:
        from xml.sax.saxutils import escape
        args = "".join(f"        <string>{escape(a)}</string>\n"
                       for a in _split(command))
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n'
                '<dict>\n'
                f'    <key>Label</key>\n    <string>{BUNDLE_ID}</string>\n'
                '    <key>ProgramArguments</key>\n'
                f'    <array>\n{args}    </array>\n'
                '    <key>RunAtLoad</key>\n    <true/>\n'
                '</dict>\n</plist>\n')


class XdgAutostartBackend(_FileBackend):
    """A freedesktop `.desktop` entry. Honoured by GNOME, KDE, XFCE and
    anything else that follows the autostart spec."""

    def __init__(self, home: Path | None = None):
        base = Path(home) if home else Path.home()
        config = os.environ.get("XDG_CONFIG_HOME")
        root = Path(config) if config and home is None else base / ".config"
        super().__init__(root / "autostart" / LINUX_DESKTOP_FILE)

    def _contents(self, command: str) -> str:
        return ("[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Foxy Audit\n"
                "Comment=Compliance companion for Foxy Audit\n"
                f"Exec={command}\n"
                "Terminal=false\n"
                "X-GNOME-Autostart-enabled=true\n")


def backend_for(platform: str | None = None, *, home: Path | None = None):
    """The right store for this OS, or `MemoryBackend` where there is none.

    An unrecognised platform gets the unsupported memory store rather than a
    guess: writing a `.desktop` file on an OS that does not read one would
    make the toggle claim to work while doing nothing.
    """
    name = platform if platform is not None else sys.platform
    if name.startswith("win"):
        try:
            return WindowsRegistryBackend()
        except ImportError:         # winreg is Windows-only
            return MemoryBackend()
    if name == "darwin":
        return LaunchAgentBackend(home)
    if name.startswith("linux") or name.startswith("freebsd"):
        return XdgAutostartBackend(home)
    return MemoryBackend()


def _split(command: str) -> list[str]:
    """Best-effort argv from a joined command line, for the plist array."""
    try:
        return shlex.split(command, posix=not sys.platform.startswith("win"))
    except ValueError:
        return [command]


# ── the one object the UI talks to ──────────────────────────────────────────
class Autostart:
    """Grant or revoke login-start, and report what the OS actually says."""

    def __init__(self, backend=None):
        self.backend = backend if backend is not None else backend_for()

    @property
    def supported(self) -> bool:
        return bool(getattr(self.backend, "supported", False))

    @property
    def location(self) -> str:
        return str(getattr(self.backend, "location", ""))

    def is_enabled(self) -> bool:
        """Asks the OS every time — see the module docstring on drift."""
        if not self.supported:
            return False
        try:
            return bool(self.backend.read())
        except Exception:           # noqa: BLE001 — a broken store is "off"
            return False

    def set_enabled(self, on: bool) -> bool:
        """True if the store now matches `on`. False means the OS refused and
        the caller must say so rather than leaving the switch flipped."""
        if not self.supported:
            return False
        try:
            if on:
                return bool(self.backend.write(_quote(launch_command())))
            return bool(self.backend.remove())
        except Exception:           # noqa: BLE001 — never crash a dialog
            return False


#: What the dialog says when the OS refuses. Named here so the message and the
#: behaviour cannot drift apart.
GRANT_FAILED = "Windows wouldn't let Foxy add itself to startup — not changed."
REVOKE_FAILED = "Couldn't remove Foxy from startup — not changed."
UNSUPPORTED = "Foxy can't manage startup on this platform."


def failure_message(on: bool, platform: str | None = None) -> str:
    name = platform if platform is not None else sys.platform
    if on and name.startswith("win"):
        return GRANT_FAILED
    if on:
        return "Couldn't write the startup entry — not changed."
    return REVOKE_FAILED


__all__ = ["APP_NAME", "Autostart", "BUNDLE_ID", "GRANT_FAILED",
           "LaunchAgentBackend", "MemoryBackend", "REVOKE_FAILED",
           "UNSUPPORTED", "WindowsRegistryBackend", "XdgAutostartBackend",
           "backend_for", "failure_message", "launch_command"]
