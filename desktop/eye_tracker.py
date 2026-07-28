"""
Cursor-following eyes overlay for the OmniAware Fox.

The base spritesheet has eyes baked into the pixel art, so we can't move
them inside the existing frames. Instead this draws two small dark
pixel-square "pupils" on a transparent layer *on top of* the sprite, at
roughly where the fox's eyes sit, and nudges them a few pixels toward the
mouse cursor. Kept intentionally tiny/subtle to read as a pixel-art
detail rather than a cartoon eyeball.

Usage in omni_fox.py:

    from eye_tracker import EyeOverlay

    # after self.label is created and positioned:
    self.eyes = EyeOverlay(self)
    self.eyes.raise_()

    # call once per animation tick, passing which state/frame is showing
    # so the overlay knows whether eyes should be visible right now and
    # where to anchor them for the current pose:
    self.eyes.update_for_state(self.state, self.current_frame)

The overlay hides itself automatically during big expressive poses
(crying, alerting, cheering, etc.) where painted eyes would clash with
the art, and only shows during calm states (idle/sleep/walking/sitting)
where a fixed anchor point looks right.
"""
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QPainter, QColor, QCursor

# States where painted-on pupils make sense (character is facing roughly
# forward/calm and isn't already doing a big expressive eye animation).
EYE_VISIBLE_STATES = {"IDLE", "WALKING", "STANDING", "SPEAKING", "ALERTING"}

# Anchor point (within the 192x208 cell) where the two eyes sit on the
# default forward-facing pose. Tune these to match your art's eye position.
LEFT_EYE_ANCHOR = QPoint(78, 96)
RIGHT_EYE_ANCHOR = QPoint(112, 96)
PUPIL_SIZE = 4          # px, kept small/pixel-art scale
MAX_DRIFT = 3           # max pixels the pupil shifts toward the cursor
PUPIL_COLOR = QColor(40, 28, 20, 235)


class EyeOverlay(QWidget):
    """Transparent child widget drawn on top of the fox sprite label."""

    def __init__(self, fox_widget, cell_size=(192, 208)):
        super().__init__(fox_widget)
        self.fox_widget = fox_widget
        self.cell_w, self.cell_h = cell_size
        self.setFixedSize(self.cell_w, self.cell_h)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.move(0, 0)

        self._visible_now = True
        self._drift = QPoint(0, 0)

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_cursor)
        self._poll_timer.start(60)  # ~16fps is plenty for a subtle glance


    def set_cell_size(self, width: int, height: int):
        """Follow the fox when the size slider moves (D13). An overlay left at
        the old cell either clips the sprite or floats past its edge."""
        self.cell_w, self.cell_h = int(width), int(height)
        self.setFixedSize(self.cell_w, self.cell_h)
        self.update()

    def update_for_state(self, state: str, frame: int):
        """Call this once per animation tick from OmniAwareFox.update_frame()."""
        should_show = state in EYE_VISIBLE_STATES
        if should_show != self._visible_now:
            self._visible_now = should_show
            self.setVisible(should_show)

    def _poll_cursor(self):
        if not self._visible_now:
            return
        cursor = QCursor.pos()
        fox_center = self.fox_widget.mapToGlobal(
            QPoint(self.cell_w // 2, self.cell_h // 2)
        )
        dx = cursor.x() - fox_center.x()
        dy = cursor.y() - fox_center.y()
        # Normalize into a small fixed-radius drift so it stays pixel-art
        # subtle regardless of how far away the cursor actually is.
        dist = max(1, (dx * dx + dy * dy) ** 0.5)
        new_drift = QPoint(
            int(max(-MAX_DRIFT, min(MAX_DRIFT, (dx / dist) * MAX_DRIFT))),
            int(max(-MAX_DRIFT, min(MAX_DRIFT, (dy / dist) * MAX_DRIFT))),
        )
        if new_drift != self._drift:
            self._drift = new_drift
            self.update()  # trigger repaint only when the pupils actually move

    def paintEvent(self, event):
        if not self._visible_now:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)  # keep hard pixel edges
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(PUPIL_COLOR)
        for anchor in (LEFT_EYE_ANCHOR, RIGHT_EYE_ANCHOR):
            x = anchor.x() + self._drift.x() - PUPIL_SIZE // 2
            y = anchor.y() + self._drift.y() - PUPIL_SIZE // 2
            painter.drawRect(x, y, PUPIL_SIZE, PUPIL_SIZE)
        painter.end()
