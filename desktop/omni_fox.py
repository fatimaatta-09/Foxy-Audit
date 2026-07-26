"""
Foxy Audit — Desktop Compliance Officer
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Animated fox that lives at the bottom of your screen as the mascot
for the Foxy Audit Trust-as-a-Service platform.  Monitors system
health, provides governance tips, and offers a chat interface.
"""

import sys
import os
import time
import random

import webbrowser

import psutil
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QMenu, QSystemTrayIcon
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, pyqtSignal, QThread
from PyQt6.QtGui import QPixmap, QTransform, QCursor, QPainter, QColor, QIcon
from pynput import keyboard, mouse

from clay_chat_popup import ChatPopup
from fox_settings import FoxSettings
from foxy_client import FoxyClient, shutdown_workers, spawn_worker
from foxy_tokens import matte_menu_qss as _matte_menu_qss, resource_path
from settings_dialog import SettingsDialog
import window_tracker
from eye_tracker import EyeOverlay
from security_overlay import SecurityOverlay
from sdk_bridge import SDKBridgeListener
from dashboard import DashboardWindow
from breach_poll import plan_reactions


# ── Spritesheet layout ─────────────────────────────────────────────────────
CELL_WIDTH        = 192
CELL_HEIGHT       = 208
COLUMNS           = 8
FRAMES_PER_ACTION = 24
ROWS_PER_ACTION   = 3

ROW_SLEEP      = 0  * ROWS_PER_ACTION
ROW_ALERT      = 1  * ROWS_PER_ACTION
ROW_CHORE      = 2  * ROWS_PER_ACTION
ROW_CRYING     = 3  * ROWS_PER_ACTION
ROW_LOVING     = 4  * ROWS_PER_ACTION
ROW_PAWING     = 5  * ROWS_PER_ACTION
ROW_WALK       = 6  * ROWS_PER_ACTION
ROW_SHY        = 7  * ROWS_PER_ACTION
ROW_CHEERING   = 8  * ROWS_PER_ACTION
ROW_CUDDLING   = 9  * ROWS_PER_ACTION
ROW_STANDING   = 10 * ROWS_PER_ACTION
ROW_THINKING   = 11 * ROWS_PER_ACTION
ROW_MOTIVATING = ROW_CHEERING

# ── Compliance officer speech bubbles ──────────────────────────────────────
COMPLIANCE_TIPS = [
    "✓ Systems nominal",
    "📋 Audit chain: intact",
    "🔒 Governance: active",
    "✓ All checks passing",
    "🔗 Hash chain: verified",
    "📊 Monitoring compliance…",
    "🛡️ Policies: up to date",
    "✓ Zero-knowledge: enforced",
    "📝 Ready for audit review",
    "✓ Tamper-evidence: active",
]


# ── Background sensor thread ───────────────────────────────────────────────
class GlobalSensors(QThread):
    typing_signal          = pyqtSignal()
    scrolling_signal       = pyqtSignal()
    hardware_update_signal = pyqtSignal(dict)

    def run(self):
        kb_listener    = keyboard.Listener(
            on_press=lambda k: self.typing_signal.emit())
        mouse_listener = mouse.Listener(
            on_scroll=lambda x, y, dx, dy: self.scrolling_signal.emit())
        kb_listener.start()
        mouse_listener.start()

        while not self.isInterruptionRequested():
            cpu  = psutil.cpu_percent(interval=None)
            ram  = psutil.virtual_memory().percent
            batt = psutil.sensors_battery()
            self.hardware_update_signal.emit({
                "cpu":     cpu,
                "ram":     ram,
                "battery": batt.percent       if batt else 100,
                "plugged": batt.power_plugged if batt else True,
            })
            time.sleep(3)

        kb_listener.stop()
        mouse_listener.stop()


# ── Backend breach poller ──────────────────────────────────────────────────
class BreachPollWorker(QThread):
    """Polls the backend so the fox reacts to a REAL, backend-detected policy
    breach.  Grading is asynchronous, and in production the backend is remote —
    it can never push a UDP datagram to the desktop's localhost:9999 — so the
    desktop pulls: GET /v1/logs/breaches?since_seq={cursor}.  The first poll is a
    baseline (existing breaches are absorbed, not fired) so the fox never floods
    with stale alerts on launch.  Pure cursor logic lives in breach_poll.py.

    HTTP goes through the shared FoxyClient, which reads the backend URL and
    org key from FoxSettings per request — a settings change applies on the
    next poll tick without restarting this thread."""
    breach_detected = pyqtSignal(dict)

    def __init__(self, client: FoxyClient, interval: float = 10.0, parent=None):
        super().__init__(parent)
        self._client = client
        self.interval = interval
        self._cursor = 0
        self._first_poll = True

    def run(self):
        while not self.isInterruptionRequested():
            self._poll_once()
            waited = 0.0
            while waited < self.interval and not self.isInterruptionRequested():
                self.msleep(200)
                waited += 0.2

    def _poll_once(self):
        try:
            breaches = self._client.request(
                "GET", f"/v1/logs/breaches?since_seq={self._cursor}", timeout=8)
        except Exception:
            return  # transient (offline / unreachable) — retry next tick
        if not isinstance(breaches, list):
            return
        to_fire, self._cursor = plan_reactions(
            breaches, self._cursor, self._first_poll)
        self._first_poll = False
        for b in to_fire:
            self.breach_detected.emit({
                "reason": b.get("reason", "Policy breach"),
                "risk_score": b.get("risk_score", 100),
                "policy": b.get("policy_tag", "default"),
            })


# ── Main fox widget ────────────────────────────────────────────────────────
class OmniAwareFox(QWidget):

    # States that expire back to IDLE after a timeout
    _TIMED_STATES = frozenset((
        "TYPING", "SCROLLING", "ALERTING", "LOVING", "CHORE",
        "CRYING", "SHY", "CHEERING", "CUDDLING", "STANDING",
        "SPEAKING", "IDLE_BREAK",
    ))

    # Idle-break actions: (row, duration)
    _IDLE_ACTIONS = [
        (ROW_SHY,      3.0),
        (ROW_PAWING,   2.5),
        (ROW_STANDING, 3.0),
        (ROW_CHEERING, 2.5),
        (ROW_CUDDLING, 3.0),
    ]

    def __init__(self):
        super().__init__()
        self.settings = FoxSettings()
        self.client = FoxyClient(self.settings, parent=self)
        self._workers: set = set()   # live one-shot ApiWorkers (health probes)

        # ── Window flags ───────────────────────────────────────────────────
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setWindowTitle("Foxy Audit")

        # ── Fixed size = one sprite cell ──────────────────────────────────
        self.setFixedSize(CELL_WIDTH, CELL_HEIGHT)

        # ── Spritesheet ────────────────────────────────────────────────────
        sheet_path = resource_path("ultimate_fox_spritesheet.png")
        self.sprite_sheet = QPixmap(sheet_path)
        if self.sprite_sheet.isNull():
            print(f"WARNING: spritesheet not found at {sheet_path}",
                  file=sys.stderr)

        # Pre-cache frames for fast rendering
        self._frame_cache: dict[tuple, QPixmap] = {}

        # ── Sprite label ───────────────────────────────────────────────────
        self.label = QLabel(self)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.label.setAutoFillBackground(False)
        self.label.setStyleSheet("background: transparent; border: none;")
        self.label.setFixedSize(CELL_WIDTH, CELL_HEIGHT)

        # ── Security overlay ──────────────────────────────────────────────
        self.security_overlay = SecurityOverlay(self, cell_size=(CELL_WIDTH, CELL_HEIGHT))
        self.security_overlay.raise_()

        # ── Eye overlay ───────────────────────────────────────────────────
        self.eyes = EyeOverlay(self, cell_size=(CELL_WIDTH, CELL_HEIGHT))
        self.eyes.raise_()

        # ── Speech bubble (compliance tips + PC health) ───────────────────
        self.speech_bubble = QLabel(self)
        self.speech_bubble.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground)
        self.speech_bubble.setStyleSheet(
            "background-color: rgba(30, 30, 46, 220); "
            "border: 1px solid rgba(203, 166, 247, 150); "
            "border-radius: 12px; "
            "padding: 6px 10px; "
            "font-weight: 600; font-size: 11px; "
            "color: #cdd6f4; "
            "font-family: 'Segoe UI', sans-serif;"
        )
        self.speech_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.speech_bubble.hide()

        # ── State machine ──────────────────────────────────────────────────
        self.current_frame         = 0
        self.current_row           = ROW_SLEEP
        self.state                 = "IDLE"
        self.reaction_timeout      = 0.0
        self.last_interaction_time = time.time()
        self._last_state_change    = 0.0

        # Debounce counters
        self._last_typing_trigger  = 0.0
        self._last_scroll_trigger  = 0.0

        # ── Chat popup ────────────────────────────────────────────────────
        self.chat_popup   = None
        self._chat_open   = False
        self.dashboard    = None
        self.is_dragging  = False
        self.drag_offset  = QPoint()
        self._press_pos   = QPoint()
        self._last_move_pos = QPoint()
        self._pat_wiggle  = 0.0
        self._user_placed = False   # True after user drags fox somewhere
        self._is_hidden_in_tray = False

        # ── Debouncing for SDK events ─────────────────────────────────────
        self._last_evaluating = 0.0
        self._last_hash_ok = 0.0
        self.roam_target_x  = None
        self._roam_paused   = False
        self._roam_pause_until = 0.0
        self._facing_left   = False

        # ── Idle-break timer (random autonomous actions) ──────────────────
        # ~600-1200 ticks @ 100ms ≈ 60-120s between autonomous poses.  Kept
        # deliberately sparse so the fox feels calm/ambient rather than fidgety.
        self._idle_break_countdown = random.randint(600, 1200)  # ticks

        # ── Compliance tip timer ──────────────────────────────────────────
        self._tip_countdown = random.randint(600, 900)  # ticks (~60-90s)

        # ── Hardware cache ────────────────────────────────────────────────
        self.latest_hw = {"cpu": 0, "ram": 0, "battery": 100, "plugged": True}

        # ── Screen geometry ───────────────────────────────────────────────
        self.screen_geom = QApplication.primaryScreen().geometry()

        # ── Initial position: where the user last placed the fox, clamped to
        # the current screen; default bottom-right, above the taskbar ──────
        taskbar_margin = 50
        self._bottom_y = self.screen_geom.height() - CELL_HEIGHT - taskbar_margin
        saved_pos = self.settings.pet_pos()
        if saved_pos is not None:
            self.move(
                max(self.screen_geom.x(),
                    min(saved_pos.x(), self.screen_geom.right() - CELL_WIDTH)),
                max(self.screen_geom.y(),
                    min(saved_pos.y(), self.screen_geom.bottom() - CELL_HEIGHT)),
            )
            self._user_placed = True   # a remembered spot counts as user-placed
        else:
            self.move(
                self.screen_geom.width() - CELL_WIDTH - 80,
                self._bottom_y,
            )

        # ── Background sensors ────────────────────────────────────────────
        self.sensors = GlobalSensors()
        self.sensors.typing_signal.connect(self.trigger_typing)
        self.sensors.scrolling_signal.connect(self.trigger_scrolling)
        self.sensors.hardware_update_signal.connect(self.process_hardware)
        self.sensors.start()

        # ── SDK Bridge ────────────────────────────────────────────────────
        self.sdk_bridge = SDKBridgeListener()
        self.sdk_bridge.evaluating.connect(self._on_sdk_evaluating)
        self.sdk_bridge.hash_confirmed.connect(self._on_sdk_hash_ok)
        self.sdk_bridge.policy_breach.connect(self._on_policy_breach)
        self.sdk_bridge.start()

        # ── System Tray ───────────────────────────────────────────────────
        self._setup_tray()

        # ── First run: ask for the API key if none is stored yet ──────────
        if not self.settings.org_api_key():
            self._prompt_first_run_key()

        # ── Startup backend health check ──────────────────────────────────
        url = self.settings.backend_url()
        key = self.settings.org_api_key()
        if url and key:
            spawn_worker(self.client, "GET", "/v1/health", timeout=5, parent=self,
                         force_bearer=True,  # /v1/health is Bearer-only on the backend
                         on_ok=self._on_health_ok, on_err=self._on_health_fail,
                         track=self._workers)

            # Poll the backend for real graded breaches → fox reacts (5A.1b).
            self._breach_poller = BreachPollWorker(self.client, parent=self)
            self._breach_poller.breach_detected.connect(self._on_policy_breach)
            self._breach_poller.start()

        # ── Animation timer: 100 ms ≈ 10 fps ─────────────────────────────
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.update_frame)
        self.anim_timer.start(100)

        # ── Roaming timer: 150 ms ≈ ~7 Hz ────────────────────────────────
        self.roam_tick_timer = QTimer(self)
        self.roam_tick_timer.timeout.connect(self._roaming_tick)
        self.roam_tick_timer.start(150)

    # ── First-run onboarding ───────────────────────────────────────────────
    def _prompt_first_run_key(self):
        """First launch with no API key stored: a small BRANDED dialog (the app's
        clay palette + the fox logo) asking for the key, instead of the plain OS
        input box. Dismissable — the key can also be set later via tray → Settings."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                     QLineEdit, QPushButton)
        from PyQt6.QtGui import QPixmap, QIcon
        from PyQt6.QtCore import Qt

        dlg = QDialog(self)
        dlg.setWindowTitle("Welcome to Foxy Audit")
        dlg.setWindowIcon(QIcon(resource_path("logo.png")))
        dlg.setModal(True)
        dlg.setFixedWidth(432)
        dlg.setStyleSheet("""
            QDialog { background:#FBF6EE; }
            QLabel#title { color:#5A4A3C; font-size:17px; font-weight:800; }
            QLabel#msg   { color:#8A7A6C; font-size:12px; }
            QLineEdit { background:#FFFFFF; border:2px solid #F0E4D6; border-radius:12px;
                        padding:11px 13px; color:#5A4A3C; font-size:13px; }
            QLineEdit:focus { border:2px solid #FF9F66; }
            QPushButton#primary { background:#FF9F66; color:#FFFFFF; border:none;
                        border-radius:12px; padding:11px 22px; font-weight:700; }
            QPushButton#primary:hover { background:#F08A4B; }
            QPushButton#ghost { background:transparent; color:#8A7A6C; border:none;
                        border-radius:12px; padding:11px 16px; }
            QPushButton#ghost:hover { color:#5A4A3C; }
        """)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(30, 26, 30, 24)
        v.setSpacing(13)

        logo = QLabel()
        pm = QPixmap(resource_path("logo.png"))
        if not pm.isNull():
            logo.setPixmap(pm.scaledToHeight(72, Qt.TransformationMode.SmoothTransformation))
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(logo)

        title = QLabel("Welcome to Foxy Audit")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title)

        msg = QLabel("Paste your API key to connect the fox to your account. It's in "
                     "your welcome email or the dashboard — you can also set it later "
                     "from the tray menu → Settings.")
        msg.setObjectName("msg")
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(msg)

        field = QLineEdit()
        field.setPlaceholderText("foxy_sk_…")
        v.addWidget(field)

        row = QHBoxLayout()
        row.addStretch(1)
        later = QPushButton("Later")
        later.setObjectName("ghost")
        connect = QPushButton("Connect")
        connect.setObjectName("primary")
        row.addWidget(later)
        row.addWidget(connect)
        v.addLayout(row)

        later.clicked.connect(dlg.reject)
        connect.clicked.connect(dlg.accept)
        field.returnPressed.connect(dlg.accept)
        field.setFocus()

        if dlg.exec() == QDialog.DialogCode.Accepted and field.text().strip():
            self.settings.set_org_api_key(field.text().strip())

    # ── Frame cache helper ─────────────────────────────────────────────────
    def _get_frame(self, row: int, frame_idx: int,
                   flip: bool = False) -> QPixmap:
        key = (row, frame_idx, flip)
        cached = self._frame_cache.get(key)
        if cached is not None:
            return cached

        col        = frame_idx % COLUMNS
        row_offset = frame_idx // COLUMNS
        x = col * CELL_WIDTH
        y = (row + row_offset) * CELL_HEIGHT

        pix = self.sprite_sheet.copy(QRect(x, y, CELL_WIDTH, CELL_HEIGHT))
        if flip:
            pix = pix.transformed(QTransform().scale(-1, 1))

        self._frame_cache[key] = pix
        return pix

    # ── Animation tick ─────────────────────────────────────────────────────
    def update_frame(self):
        now = time.time()

        # Expire timed states back to IDLE
        if self.state in self._TIMED_STATES:
            if now > self.reaction_timeout:
                self.state       = "IDLE"
                self.current_row = ROW_SLEEP
                self.current_frame = 0

        # Advance frame counter
        if self.state == "IDLE":
            idle_secs = now - self.last_interaction_time
            if idle_secs > 120:
                # Dozing: cycle through last 6 frames of sleep
                start = FRAMES_PER_ACTION - 6
                if self.current_frame < start:
                    self.current_frame = start
                self.current_frame = start + (
                    (self.current_frame + 1 - start) % 6)
            else:
                # Gentle breathing: cycle first 6 frames of sleep slowly
                self.current_frame = (self.current_frame + 1) % 6

            # Autonomous idle-break
            self._idle_break_countdown -= 1
            if self._idle_break_countdown <= 0:
                row, dur = random.choice(self._IDLE_ACTIONS)
                self._set_state("IDLE_BREAK", row, dur)
                self._idle_break_countdown = random.randint(600, 1200)

        elif self.state == "WALKING":
            self.current_frame = (self.current_frame + 1) % FRAMES_PER_ACTION

        else:
            self.current_frame = (self.current_frame + 1) % FRAMES_PER_ACTION

        # Show compliance tip periodically
        self._tip_countdown -= 1
        if self._tip_countdown <= 0 and self.state == "IDLE":
            self._show_compliance_tip()
            self._tip_countdown = random.randint(600, 1200)

        # Render the current frame
        flip = self._facing_left
        frame_pix = self._get_frame(self.current_row, self.current_frame, flip)
        self.label.setPixmap(frame_pix)
        self.eyes.update_for_state(self.state, self.current_frame)

    # ── Bottom-of-screen roaming ───────────────────────────────────────────
    def _roaming_tick(self):
        """Gentle walk along the bottom of the screen."""
        if self.is_dragging or self._chat_open or self._user_placed:
            return
        if self.state not in ("IDLE", "WALKING"):
            return

        now = time.time()

        # Paused between walks
        if self._roam_paused:
            if now < self._roam_pause_until:
                return
            self._roam_paused = False

        # Pick a new target if we don't have one
        if self.roam_target_x is None:
            margin = 60
            low  = self.screen_geom.x() + margin
            high = self.screen_geom.width() - CELL_WIDTH - margin
            if high <= low:
                high = low + 1
            self.roam_target_x = random.randint(low, high)

        # Walk towards target
        cx = self.x()
        dist = abs(cx - self.roam_target_x)

        if dist <= 4:
            # Arrived — pause for a bit, go back to idle
            self.roam_target_x = None
            self._roam_paused = True
            self._roam_pause_until = now + random.uniform(4.0, 10.0)
            if self.state == "WALKING":
                self.state       = "IDLE"
                self.current_row = ROW_SLEEP
                self.current_frame = 0
        else:
            # Walk at a calm speed (2px per tick at 150ms ≈ 13px/s)
            self._facing_left = self.roam_target_x < cx
            direction = -1 if self._facing_left else 1
            speed = 2
            new_x = cx + direction * speed

            # Keep y locked to bottom
            new_y = self._bottom_y

            self.move(new_x, new_y)

            if self.state != "WALKING":
                self.state       = "WALKING"
                self.current_row = ROW_WALK
                self.current_frame = 0

    # ── Hardware events ────────────────────────────────────────────────────
    def process_hardware(self, hw: dict):
        self.latest_hw = hw
        if self.state in self._TIMED_STATES:
            return
        if hw["cpu"] > 90.0 or hw["ram"] > 92.0:
            self._set_state("CRYING", ROW_CRYING, 3.0)
        elif hw["battery"] < 15 and not hw["plugged"]:
            self._set_state("ALERTING", ROW_ALERT, 3.0)
        elif self.state == "CRYING":
            self.state = "IDLE"

    # ── State helper ───────────────────────────────────────────────────────
    def _set_state(self, name: str, row: int, duration: float):
        now = time.time()
        if self.state == name:
            self.reaction_timeout = now + duration
            return
        cooldown = self.settings.reaction_cooldown()
        if now - self._last_state_change < cooldown:
            return
        self.state              = name
        self.current_row        = row
        self.current_frame      = 0
        self.reaction_timeout   = now + duration
        self._last_state_change = now

    # ── Input listeners (heavily debounced) ────────────────────────────────
    def trigger_typing(self):
        now = time.time()
        if now - self._last_typing_trigger < 12.0:
            return
        self._last_typing_trigger  = now
        self.last_interaction_time = now
        if self.is_dragging or self._chat_open:
            return
        if self.state in self._TIMED_STATES:
            return
        self._set_state("TYPING", ROW_PAWING, 2.0)

    def trigger_scrolling(self):
        now = time.time()
        if now - self._last_scroll_trigger < 15.0:
            return
        self._last_scroll_trigger  = now
        self.last_interaction_time = now
        if self.is_dragging or self._chat_open:
            return
        if self.state in self._TIMED_STATES:
            return
        self._set_state("SCROLLING", ROW_CHORE, 1.5)

    # ── Compliance tips ────────────────────────────────────────────────────
    def _show_compliance_tip(self):
        tip = random.choice(COMPLIANCE_TIPS)
        self.speech_bubble.setText(tip)
        self.speech_bubble.adjustSize()
        # Position above the fox
        bw = self.speech_bubble.width()
        self.speech_bubble.move(
            max(0, (CELL_WIDTH - bw) // 2),
            -self.speech_bubble.height() - 6,
        )
        self.speech_bubble.show()
        QTimer.singleShot(4000, self._hide_speech)

    def _hide_speech(self):
        self.speech_bubble.hide()

    # ── PC health ──────────────────────────────────────────────────────────
    def ask_pc_health(self):
        self.state         = "THINKING"
        self.current_row   = ROW_THINKING
        self.current_frame = 0
        self.reaction_timeout = time.time() + 3.5
        QTimer.singleShot(2000, self._finish_pc_health)

    def _finish_pc_health(self):
        hw = self.latest_hw
        cpu_icon = "🟢" if hw["cpu"] < 70 else "🟡" if hw["cpu"] < 85 else "🔴"
        ram_icon = "🟢" if hw["ram"] < 70 else "🟡" if hw["ram"] < 85 else "🔴"
        batt = hw.get("battery", 100)
        msg = f"{cpu_icon} CPU {hw['cpu']:.0f}%  {ram_icon} RAM {hw['ram']:.0f}%  🔋{batt:.0f}%"
        self.speech_bubble.setText(msg)
        self.speech_bubble.adjustSize()
        bw = self.speech_bubble.width()
        self.speech_bubble.move(
            max(0, (CELL_WIDTH - bw) // 2),
            -self.speech_bubble.height() - 6,
        )
        self.speech_bubble.show()
        self._set_state("SPEAKING", ROW_STANDING, 5.0)
        QTimer.singleShot(5000, self._hide_speech)

    # ── Compliance check ───────────────────────────────────────────────────
    def show_compliance_status(self):
        self._set_state("STANDING", ROW_STANDING, 4.0)
        self.speech_bubble.setText("📋 Foxy Audit: All systems compliant ✓")
        self.speech_bubble.adjustSize()
        bw = self.speech_bubble.width()
        self.speech_bubble.move(
            max(0, (CELL_WIDTH - bw) // 2),
            -self.speech_bubble.height() - 6,
        )
        self.speech_bubble.show()
        QTimer.singleShot(4000, self._hide_speech)

    # ── Startup Health Check Callbacks ─────────────────────────────────────
    def _on_health_ok(self, _data=None):
        if self.dashboard is not None:
            self.dashboard.set_connected(True)
        self._set_state("CHEERING", ROW_CHEERING, 2.0)
        self.security_overlay.flash_green()
        self.speech_bubble.setText("✓ Connected to Foxy Audit")
        self.speech_bubble.adjustSize()
        bw = self.speech_bubble.width()
        self.speech_bubble.move(max(0, (CELL_WIDTH - bw) // 2), -self.speech_bubble.height() - 6)
        self.speech_bubble.show()
        QTimer.singleShot(3000, self._hide_speech)

    def _on_health_fail(self, err: str):
        if self.dashboard is not None:
            self.dashboard.set_connected(False)
        self._set_state("ALERTING", ROW_ALERT, 3.0)
        self.security_overlay.flash_amber()
        self.speech_bubble.setText("⚠ Backend unreachable")
        self.speech_bubble.adjustSize()
        bw = self.speech_bubble.width()
        self.speech_bubble.move(max(0, (CELL_WIDTH - bw) // 2), -self.speech_bubble.height() - 6)
        self.speech_bubble.show()
        QTimer.singleShot(3000, self._hide_speech)

    # ── SDK Bridge Callbacks ───────────────────────────────────────────────
    def _on_sdk_evaluating(self, payload: dict):
        """SDK logged a call; grading is PENDING. Show a neutral 'thinking' beat —
        NOT green (that would imply a verdict before the Judge has run). The breach
        poller turns the fox red on the real verdict; otherwise it settles to idle."""
        now = time.time()
        if now - self._last_evaluating < 5.0:
            return  # debounce without suppressing the following hash_ok
        self._last_evaluating = now
        if self._is_hidden_in_tray:
            return
        self._set_state("THINKING", ROW_THINKING, 1.5)

    def _on_sdk_hash_ok(self, payload: dict):
        now = time.time()
        if now - self._last_hash_ok < 5.0:
            return  # Debounce busy SDKs
        self._last_hash_ok = now
        
        # Don't interrupt if in tray, unless we want to glow in tray (we can't).
        if self._is_hidden_in_tray:
            return

        self._set_state("STANDING", ROW_STANDING, 1.5)
        self.security_overlay.flash_green()
        delivery = payload.get("delivery")
        status = "local hash queued" if delivery == "queued" else "local hash only"
        self.speech_bubble.setText(f"Foxy: {status}")
        self.speech_bubble.adjustSize()
        bw = self.speech_bubble.width()
        self.speech_bubble.move(max(0, (CELL_WIDTH - bw) // 2),
                                -self.speech_bubble.height() - 6)
        self.speech_bubble.show()
        QTimer.singleShot(2200, self._hide_speech)

    def _on_policy_breach(self, payload: dict):
        # Force pop-back if hidden
        if self._is_hidden_in_tray:
            self._show_from_tray()

        self._set_state("ALERTING", ROW_ALERT, 5.0)
        self.security_overlay.flash_red(5000)

        reason = payload.get("reason", "Unknown injection")
        score = payload.get("risk_score", 100)

        # 5L: audible cue + a native tray popup, and a tally for the weekly summary.
        QApplication.beep()
        try:
            self.tray_icon.showMessage(
                "🚨 Policy Breach", f"{reason} (risk {score}/100)",
                QSystemTrayIcon.MessageIcon.Critical, 6000)
        except Exception:
            pass
        self.settings.bump_weekly_breaches()

        if self.chat_popup is None:
            self.chat_popup = ChatPopup(self, settings=self.settings)
            self.chat_popup.popup_closed.connect(self._on_chat_closed)
            self.chat_popup._add_bubble(
                f"🚨 **POLICY BREACH DETECTED** 🚨\n\n**Reason:** {reason}\n**Risk Score:** {score}/100",
                is_user=False
            )
        else:
            self.chat_popup._add_bubble(
                f"🚨 **POLICY BREACH DETECTED** 🚨\n\n**Reason:** {reason}\n**Risk Score:** {score}/100",
                is_user=False
            )

        self.open_chat()

    # ── Mouse interaction ──────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging    = True
            self._press_pos     = event.globalPosition().toPoint()
            self._last_move_pos = self._press_pos
            self._pat_wiggle    = 0.0
            self.drag_offset    = (event.globalPosition().toPoint()
                                   - self.frameGeometry().topLeft())
            self.last_interaction_time = time.time()

    def mouseMoveEvent(self, event):
        if not self.is_dragging:
            return
        pos  = event.globalPosition().toPoint()
        step = (pos - self._last_move_pos).manhattanLength()
        self._last_move_pos = pos
        self._pat_wiggle   += step

        dist_from_press = (pos - self._press_pos).manhattanLength()
        if (dist_from_press < 40
                and self._pat_wiggle > self.settings.pat_sensitivity()):
            self._pat_wiggle = 0.0
            self._set_state("LOVING", ROW_LOVING, 1.5)
            self.last_interaction_time = time.time()
            return

        self.move(pos - self.drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            was_dragging = self.is_dragging
            self.is_dragging = False
            moved = (event.globalPosition().toPoint()
                     - self._press_pos).manhattanLength()

            if moved < 6:
                # Click — open chat
                self.open_chat()
            elif moved > 30:
                # Significant drag — user placed the fox, stop auto-roaming
                self._user_placed = True
                self.settings.set_pet_pos(self.pos())   # remember for next launch
                self.roam_target_x = None
                if self.state == "WALKING":
                    self.state       = "IDLE"
                    self.current_row = ROW_SLEEP
                    self.current_frame = 0

    # ── Chat ───────────────────────────────────────────────────────────────
    def open_chat(self):
        if self.chat_popup is None:
            self.chat_popup = ChatPopup(self, settings=self.settings)
            self.chat_popup.popup_closed.connect(self._on_chat_closed)
        self.chat_popup.popup_near(self)
        self.chat_popup.show_animated()
        self.chat_popup.raise_()
        self.chat_popup.activateWindow()
        self._chat_open = True
        # Re-raise the fox so it stays visible on top of the chat
        QTimer.singleShot(250, self._raise_fox)

    def _raise_fox(self):
        self.raise_()
        self.show()

    def _on_chat_closed(self):
        self._chat_open = False

    # ── Dashboard ──────────────────────────────────────────────────────────
    def open_dashboard(self):
        """Open (or re-focus) the Compliance Command Center, wiring it to the
        same live telemetry the fox reacts to."""
        if self.dashboard is None:
            self.dashboard = DashboardWindow(
                self,
                settings=self.settings,
                sprite_sheet_path=resource_path("ultimate_fox_spritesheet.png"),
                client=self.client,
            )
            # Live feeds (Qt signals support multiple receivers)
            self.sensors.hardware_update_signal.connect(self.dashboard.on_hardware)
            self.sdk_bridge.hash_confirmed.connect(self.dashboard.on_hash_ok)
            self.sdk_bridge.policy_breach.connect(self.dashboard.on_policy_breach)
            self.dashboard.refresh_requested.connect(self._recheck_backend)

        # Push the current snapshot so gauges aren't empty on first open
        self.dashboard.on_hardware(self.latest_hw)
        self.dashboard.show_animated()
        self.dashboard.raise_()
        self.dashboard.activateWindow()

    def open_web_dashboard(self):
        """Open the browser dashboard (site 2) in the default browser — the full web app,
        distinct from the local desktop console. Mints a one-time auto-login handoff token
        (POST /v1/auth/handoff with the org key) so the person lands ALREADY logged in;
        falls back to the bare URL if the backend/key isn't set or the mint fails."""
        url = self.settings.web_dashboard_url()
        backend = self.settings.backend_url()
        key = self.settings.org_api_key()
        if backend and key:
            try:
                data = self.client.request("POST", "/v1/auth/handoff", timeout=6)
                token = data.get("token") if isinstance(data, dict) else None
                if token:
                    sep = "&" if "?" in url else "?"
                    url = f"{url}{sep}handoff={token}"
            except Exception as exc:
                print(f"[foxy] handoff mint failed, opening bare dashboard: {exc}")
        try:
            webbrowser.open(url)
        except Exception as exc:
            print(f"[foxy] could not open web dashboard {url!r}: {exc}")
        self._recheck_backend()

    def _recheck_backend(self):
        """Re-run the startup health probe and reflect the result on the
        dashboard's connection pill (if the dashboard is open)."""
        url = self.settings.backend_url()
        key = self.settings.org_api_key()
        if not (url and key):
            if self.dashboard is not None:
                self.dashboard.set_connected(False)
            return
        spawn_worker(self.client, "GET", "/v1/health", timeout=5, parent=self,
                     force_bearer=True,  # /v1/health is Bearer-only on the backend
                     on_ok=self._on_health_ok, on_err=self._on_health_fail,
                     track=self._workers)

    # ── Context menu ───────────────────────────────────────────────────────
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(_matte_menu_qss())

        # ── Compliance actions ──
        menu.addAction("📊 Desktop Console",    self.open_dashboard)
        menu.addAction("🌐 Open Web Dashboard", self.open_web_dashboard)
        menu.addAction("📋 Compliance Status", self.show_compliance_status)
        menu.addAction("🖥️ PC Health",          self.ask_pc_health)
        menu.addAction("💬 Open Chat",          self.open_chat)
        menu.addSeparator()

        # ── Emotes submenu ──
        emotes = menu.addMenu("🎭 Emotes")
        emotes.setStyleSheet(menu.styleSheet())
        emotes.addAction("💕 Loving",
            lambda: self._set_state("LOVING", ROW_LOVING, 4.0))
        emotes.addAction("😳 Shy",
            lambda: self._set_state("SHY", ROW_SHY, 4.0))
        emotes.addAction("🎉 Cheer",
            lambda: self._set_state("CHEERING", ROW_CHEERING, 4.0))
        emotes.addAction("🫂 Cuddle",
            lambda: self._set_state("CUDDLING", ROW_CUDDLING, 4.0))
        emotes.addAction("😢 Cry",
            lambda: self._set_state("CRYING", ROW_CRYING, 4.0))
        emotes.addAction("🚨 Alert",
            lambda: self._set_state("ALERTING", ROW_ALERT, 4.0))

        menu.addSeparator()

        # ── System Tray ──
        menu.addAction("🔽 Hide to Taskbar", self._hide_to_tray)

        menu.addSeparator()

        # ── Roaming control ──
        if self._user_placed:
            menu.addAction("🦊 Resume Roaming", self._resume_roaming)
        else:
            menu.addAction("📌 Stay Here", self._stop_roaming)

        menu.addAction("⚙️ Settings",  self.open_settings)
        menu.addSeparator()
        menu.addAction("❌ Quit", self._quit_app)
        menu.exec(event.globalPos())

    def _stop_roaming(self):
        self._user_placed = True
        self.roam_target_x = None
        if self.state == "WALKING":
            self.state       = "IDLE"
            self.current_row = ROW_SLEEP
            self.current_frame = 0

    def _resume_roaming(self):
        self._user_placed = False
        self.settings.clear_pet_pos()   # roaming again — forget the pinned spot
        # Snap back to the bottom of the screen
        self.move(self.x(), self._bottom_y)

    # ── System Tray ────────────────────────────────────────────────────────
    def _setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        
        # Extract a single frame for the tray icon (e.g. frame 0 of SLEEP)
        pixmap = self._get_frame(ROW_SLEEP, 0)
        icon = QIcon(pixmap.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip("Foxy Audit — Compliance Officer")

        tray_menu = QMenu(self)
        tray_menu.setStyleSheet(_matte_menu_qss())
        
        tray_menu.addAction("🦊 Show Foxy", self._show_from_tray)
        tray_menu.addAction("📊 Desktop Console", self.open_dashboard)
        tray_menu.addAction("🌐 Open Web Dashboard", self.open_web_dashboard)
        tray_menu.addAction("⚙️ Settings", self.open_settings)
        tray_menu.addSeparator()
        tray_menu.addAction("❌ Quit", self._quit_app)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()
        # 5L: once a week, surface a short activity summary via the tray.
        QTimer.singleShot(3000, self._maybe_weekly_summary)

    def _maybe_weekly_summary(self):
        """Once every 7 days, show a tray summary of the week's breach activity (5L)."""
        from datetime import datetime, timedelta
        try:
            last = self.settings.weekly_last_summary()
            now = datetime.now()
            due = True
            if last:
                try:
                    due = (now - datetime.fromisoformat(last)) >= timedelta(days=7)
                except ValueError:
                    due = True
            if not due:
                return
            if last:   # skip the very first run — no full week has elapsed yet
                breaches = self.settings.weekly_breaches()
                self.tray_icon.showMessage(
                    "🦊 Foxy Audit — weekly summary",
                    f"This week: {breaches} policy breach(es) caught. Stay compliant!",
                    QSystemTrayIcon.MessageIcon.Information, 8000)
            self.settings.set_weekly_last_summary(now.isoformat())
            self.settings.reset_weekly_breaches()
        except Exception:
            pass

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_from_tray()

    def _hide_to_tray(self):
        self.hide()
        self._is_hidden_in_tray = True
        self.tray_icon.showMessage("Foxy Audit", "Hidden to taskbar. Monitoring active.", QSystemTrayIcon.MessageIcon.Information, 2000)

    def _show_from_tray(self):
        self.show()
        self.raise_()
        self._is_hidden_in_tray = False

    # ── Settings ───────────────────────────────────────────────────────────
    def open_settings(self):
        dlg = SettingsDialog(self.settings, self, client=self.client)
        dlg.settings_saved.connect(self._on_theme_changed)
        dlg.exec()

    def _on_theme_changed(self, *_):
        # The dashboard + chat use the fixed foxy_tokens skins (the argument is
        # ignored); re-apply so any saved setting change re-skins them cleanly.
        if self.chat_popup is not None:
            self.chat_popup.apply_theme(None)
        if self.dashboard is not None:
            self.dashboard.apply_theme(None)

    # ── Shutdown ───────────────────────────────────────────────────────────
    def _quit_app(self):
        """Quit via close() so closeEvent's thread teardown actually runs —
        QApplication.quit() alone would skip it."""
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if self._user_placed:
            self.settings.set_pet_pos(self.pos())   # remember the fox's spot

        shutdown_workers(self._workers, wait_ms=600)

        if hasattr(self, "_breach_poller") and self._breach_poller.isRunning():
            self._breach_poller.requestInterruption()
            self._breach_poller.quit()
            self._breach_poller.wait(1000)

        if self.dashboard is not None:
            self.dashboard.close()

        self.sdk_bridge.stop()

        self.sensors.requestInterruption()
        self.sensors.quit()
        self.sensors.wait(1000)

        self.tray_icon.hide()

        super().closeEvent(event)


# ── Entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    app = QApplication(sys.argv)
    app.setApplicationName("Foxy Audit")
    app.setOrganizationName("FoxyAudit")
    # Foxy lives in the tray: closing the console (or hiding the fox) must never
    # end the process — only the explicit Quit action does. Without this, Qt
    # quits when the last VISIBLE window closes, so "fox hidden to tray + close
    # console" would silently kill the app and its breach polling.
    app.setQuitOnLastWindowClosed(False)
    from PyQt6.QtGui import QIcon
    app.setWindowIcon(QIcon(resource_path("logo.png")))

    pet = OmniAwareFox()
    pet.show()
    sys.exit(app.exec())
