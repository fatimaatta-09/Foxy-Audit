"""
Cross-platform "what window is focused, and where is it" helper for the
OmniAware Fox desktop pet, plus a cross-platform "open this file/app"
helper for the chat assistant's file-opening capability.

New module. omni_fox.py previously had Windows-only ctypes calls inlined
directly in track_active_window(); that logic now lives here behind
get_active_window_rect(), with macOS and Linux implementations added.

Windows: native ctypes (same approach as before).
macOS:   Quartz (optional dep: pyobjc-framework-Quartz).
Linux:   python-xlib against EWMH _NET_ACTIVE_WINDOW (optional dep: python-xlib).

If the optional dependency for the current OS isn't installed, every
function here degrades gracefully — it returns None rather than raising,
so the pet just falls back to autonomous roaming instead of crashing.
"""
import sys
import os
import subprocess
import ctypes

PLATFORM = sys.platform  # 'win32', 'darwin', 'linux', ...


class WindowInfo:
    __slots__ = ("left", "top", "right", "bottom", "title")

    def __init__(self, left, top, right, bottom, title=""):
        self.left, self.top, self.right, self.bottom, self.title = left, top, right, bottom, title


# ---------------------------------------------------------------- Windows --
def _win_get_active():
    from ctypes.wintypes import RECT
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if hwnd == 0:
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value
    if title == "Program Manager" or hwnd == user32.FindWindowW("Shell_TrayWnd", None):
        return None  # desktop / taskbar -> treat as "nothing focused"
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    if rect.left == 0 and rect.top == 0 and rect.right == 0:
        return None
    return WindowInfo(rect.left, rect.top, rect.right, rect.bottom, title)


# ---------------------------------------------------------------- macOS ----
def _mac_get_active():
    try:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionOnScreenOnly,
            kCGNullWindowID,
        )
    except ImportError:
        return None
    try:
        info_list = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
        for win in info_list:
            if win.get("kCGWindowLayer", 0) != 0:
                continue  # skip menu bar / overlays; layer 0 = normal app windows
            owner = win.get("kCGWindowOwnerName", "")
            if owner in ("Window Server", "Dock", "Finder", ""):
                continue
            bounds = win.get("kCGWindowBounds")
            if not bounds:
                continue
            left, top = int(bounds["X"]), int(bounds["Y"])
            right, bottom = left + int(bounds["Width"]), top + int(bounds["Height"])
            return WindowInfo(left, top, right, bottom, owner)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------- Linux ----
_linux_display = None


def _linux_get_active():
    global _linux_display
    try:
        from Xlib import display, X
    except ImportError:
        return None
    try:
        if _linux_display is None:
            _linux_display = display.Display()
        d = _linux_display
        root = d.screen().root
        active_atom = d.intern_atom("_NET_ACTIVE_WINDOW")
        prop = root.get_full_property(active_atom, X.AnyPropertyType)
        if not prop or not prop.value:
            return None
        win_id = prop.value[0]
        window = d.create_resource_object("window", win_id)
        geom = window.get_geometry()
        coords = window.translate_coords(root, 0, 0)
        left, top = -coords.x, -coords.y
        right, bottom = left + geom.width, top + geom.height
        name = window.get_wm_name() or ""
        return WindowInfo(left, top, right, bottom, name)
    except Exception:
        return None


def get_active_window_rect():
    """Returns a WindowInfo for the current foreground app window, or
    None if it's unavailable (unsupported OS, missing optional dep,
    desktop/taskbar focused, etc). Treat None as 'fall back to roaming'."""
    if PLATFORM == "win32":
        return _win_get_active()
    if PLATFORM == "darwin":
        return _mac_get_active()
    if PLATFORM.startswith("linux"):
        return _linux_get_active()
    return None


def supports_window_tracking() -> bool:
    """Whether the current OS + installed packages can actually report
    the active window. Used by the Settings dialog to show real status
    instead of silently doing nothing."""
    if PLATFORM == "win32":
        return True
    if PLATFORM == "darwin":
        try:
            import Quartz  # noqa: F401
            return True
        except ImportError:
            return False
    if PLATFORM.startswith("linux"):
        try:
            import Xlib  # noqa: F401
            return True
        except ImportError:
            return False
    return False


def open_path(path: str):
    """Open a file/folder/app with the OS default handler — the
    cross-platform equivalent of double-clicking it. Used by the 'open
    <file>' chat command. Raises FileNotFoundError if the path doesn't
    exist, and lets any subprocess error from the OS opener bubble up."""
    expanded = os.path.expanduser(os.path.expandvars(path))
    if not os.path.exists(expanded):
        raise FileNotFoundError(expanded)
    if PLATFORM == "win32":
        os.startfile(expanded)  # type: ignore[attr-defined]
    elif PLATFORM == "darwin":
        subprocess.Popen(["open", expanded])
    else:
        subprocess.Popen(["xdg-open", expanded])


import time
import socket
import json
import threading

class ActiveWindowTracker:
    def __init__(self, udp_host="127.0.0.1", udp_port=5005, throttle_rate=1.0):
        self.udp_host = udp_host
        self.udp_port = udp_port
        self.throttle_rate = throttle_rate
        self.last_update_time = 0.0
        self.last_window_title = None
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._running = False

    def start(self):
        if not self._running:
            self._running = True
            threading.Thread(target=self.check_active_window_loop, daemon=True).start()

    def stop(self):
        self._running = False

    def check_active_window_loop(self):
        """
        Background loop to check the active window and push changes over UDP.
        Implements throttling and state tracking to prevent spamming.
        """
        while self._running:
            # Prevent 100% CPU utilization
            time.sleep(0.01)

            now = time.time()
            # Time-based debounce
            if now - self.last_update_time < self.throttle_rate:
                continue

            rect = get_active_window_rect()
            
            # If no rect, we might want to treat it as "no window" or just ignore.
            current_title = rect.title if rect else None
            
            # Only send a UDP message if the active window title has actually changed
            if current_title != self.last_window_title:
                self.last_window_title = current_title
                self.last_update_time = now
                
                if rect:
                    payload = {
                        "event": "window_changed",
                        "title": rect.title,
                        "rect": {"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom}
                    }
                    try:
                        self._sock.sendto(json.dumps(payload).encode("utf-8"), (self.udp_host, self.udp_port))
                    except Exception:
                        pass
