"""
Foxy Audit — Security Overlay
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A transparent widget layered over the main fox sprite that draws
pulsing security indicators (green shield / red alert) based on
telemetry from the Foxy Audit SDK.
"""

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QPropertyAnimation, pyqtProperty, pyqtSignal, QTimer
from PyQt6.QtGui import QPainter, QColor, QRadialGradient


class SecurityOverlay(QWidget):
    """
    Transparent overlay that pulses coloured rings around the fox.
    Used for ambient telemetry (SDK hash confirmations) and active threats.
    """

    def __init__(self, fox_widget, cell_size=(192, 208)):
        super().__init__(fox_widget)
        self.fox_widget = fox_widget
        self.cell_w, self.cell_h = cell_size
        self.setFixedSize(self.cell_w, self.cell_h)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.move(0, 0)

        self._color = QColor(0, 255, 0, 0)
        self._opacity = 0.0

        # Animation for fading out the glow
        self._fade_anim = QPropertyAnimation(self, b"opacity_prop")
        self._fade_anim.setDuration(1500)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)

        # For long pulses (e.g. 5-second red alert)
        self._active_alert = False
        self._alert_timer = QTimer(self)
        self._alert_timer.setSingleShot(True)
        self._alert_timer.timeout.connect(self._end_alert)

    def set_cell_size(self, width: int, height: int):
        """Follow the fox when the size slider moves (D13). An overlay left at
        the old cell either clips the sprite or floats past its edge."""
        self.cell_w, self.cell_h = int(width), int(height)
        self.setFixedSize(self.cell_w, self.cell_h)
        self.update()

    # ── Qt Property for the animation ─────────────────────────────────────
    @pyqtProperty(float)
    def opacity_prop(self) -> float:
        return self._opacity

    @opacity_prop.setter
    def opacity_prop(self, val: float):
        self._opacity = val
        self.update()

    # ── Triggers ─────────────────────────────────────────────────────────
    def flash_green(self):
        """A quick pulse to indicate a successful hash or backend connection."""
        if self._active_alert:
            return  # Red alert overrides ambient green flashes

        self._color = QColor(100, 255, 120)  # Bright neon green
        self._fade_anim.stop()
        self._fade_anim.setDuration(1500)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()

    def flash_red(self, duration_ms: int = 5000):
        """A severe red alert for policy breaches."""
        self._color = QColor(255, 60, 60)   # Bright neon red
        self._active_alert = True
        
        self._fade_anim.stop()
        # Instead of a single fade, we'll keep it at a steady glow
        # and rely on the timeout to turn it off. For a pulsing effect
        # we'd need a loop, but a steady bright red works well for alerts.
        self.opacity_prop = 1.0

        self._alert_timer.start(duration_ms)

    def flash_amber(self):
        """Warning pulse (e.g. backend unreachable)."""
        if self._active_alert:
            return

        self._color = QColor(255, 180, 50)  # Amber
        self._fade_anim.stop()
        self._fade_anim.setDuration(3000)
        self._fade_anim.setStartValue(0.8)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()

    def _end_alert(self):
        self._active_alert = False
        self._fade_anim.stop()
        self._fade_anim.setDuration(1000)
        self._fade_anim.setStartValue(self._opacity)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()

    # ── Paint ─────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        if self._opacity <= 0.01:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cx, cy = self.cell_w / 2, self.cell_h / 2 + 10
        radius = min(self.cell_w, self.cell_h) / 2.2

        # Radial gradient for a nice soft glow behind/around the sprite
        grad = QRadialGradient(cx, cy, radius)
        
        # Calculate alpha based on opacity property
        base_alpha = self._color.alpha() if self._color.alpha() < 255 else 180
        current_alpha = int(base_alpha * self._opacity)
        
        center_col = QColor(self._color)
        center_col.setAlpha(0)  # transparent in the very middle
        
        edge_col = QColor(self._color)
        edge_col.setAlpha(current_alpha)
        
        fade_col = QColor(self._color)
        fade_col.setAlpha(0)

        grad.setColorAt(0.0, center_col)
        grad.setColorAt(0.7, edge_col)
        grad.setColorAt(1.0, fade_col)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawRect(0, 0, self.cell_w, self.cell_h)
        painter.end()
