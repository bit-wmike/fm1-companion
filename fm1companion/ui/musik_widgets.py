# SPDX-License-Identifier: GPL-3.0-only
"""Shared widgets of the music area: piano roll with drum grid (view and edit) and a
small preview synthesizer (deliberately simple: doesn't sound like the FM-1)."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QWidget

from .. import projectfmt as PF
from ..i18n import _

KEY_W = 44
ROW_H = 12
LANE_H = 16


def vel_color(vel: int, base: QColor) -> QColor:
    """quiet = pale/cool, loud = strong/warm."""
    v = max(1, min(127, vel or 96)) / 127
    c = QColor.fromHsvF((0.62 - 0.62 * v) % 1.0, 0.35 + 0.6 * v, 0.55 + 0.4 * v)
    return c


def copy_steps(steps) -> list:
    return [PF.Step(list(s.notes), s.time, s.accent, s.slide, s.recorded, s.vel, s.hit, s.acc, s.chance) for s in steps]


class EditHistory:
    """Undo/redo for edits on the computer (piano roll, generate, fine-tuning …).
    A state is (steps, length); call push() BEFORE every change with the old state."""

    def __init__(self, limit: int = 200):
        self.undo_stack: list[tuple[list, int, str]] = []
        self.redo_stack: list[tuple[list, int, str]] = []
        self.limit = limit

    def push(self, steps, length: int, what: str = ""):
        state = (copy_steps(steps), int(length), what)
        if self.undo_stack and self._same(self.undo_stack[-1], state):
            return
        self.undo_stack.append(state)
        del self.undo_stack[:-self.limit]
        self.redo_stack.clear()

    @staticmethod
    def _same(a, b) -> bool:
        if a[1] != b[1] or len(a[0]) != len(b[0]):
            return False
        return all((x.notes, x.time, x.accent, x.slide, x.vel, x.hit, x.acc, x.chance) ==
                   (y.notes, y.time, y.accent, y.slide, y.vel, y.hit, y.acc, y.chance) for x, y in zip(a[0], b[0]))

    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def undo(self, cur_steps, cur_length: int):
        """-> (steps, length, what) of the previous state or None; the current one moves to the redo stack."""
        if not self.undo_stack:
            return None
        st = self.undo_stack.pop()
        self.redo_stack.append((copy_steps(cur_steps), int(cur_length), st[2]))
        return st

    def redo(self, cur_steps, cur_length: int):
        if not self.redo_stack:
            return None
        st = self.redo_stack.pop()
        self.undo_stack.append((copy_steps(cur_steps), int(cur_length), st[2]))
        return st

    def clear(self):
        self.undo_stack.clear()
        self.redo_stack.clear()


class StepRoll(QWidget):
    """Steps of a pattern: notes at the top (piano roll), the 8 drum lanes below (if used or editable)."""
    changed = pyqtSignal()
    about_to_change = pyqtSignal()           # before a mouse edit (for undo)

    def __init__(self, parent=None, editable: bool = False):
        super().__init__(parent)
        self.editable = editable
        self.steps: list[PF.Step] = [PF.Step() for _i in range(PF.NSTEP)]
        self.length = 16
        self.step_w = 22
        self.lo, self.hi = 48, 72
        self.show_lanes = editable
        self.edit_vel = 100
        self.setMouseTracking(True)
        self.setToolTip(_("Notes: click sets/removes a note, right-click clears the step. Drum lanes: click toggles "
                          "the hit, Shift+click sets an accent.") if editable else
                        _("Piano roll: colour = velocity, bar across several steps = held note (TIE)"))

    # ------------------------------------------------------------------ Data
    def set_steps(self, steps, length: int, keep_range: bool = False):
        self.steps = [PF.Step(list(s.notes), s.time, s.accent, s.slide, s.recorded, s.vel, s.hit, s.acc, s.chance)
                      for s in steps[:PF.NSTEP]]
        self.steps += [PF.Step() for _i in range(PF.NSTEP - len(self.steps))]
        self.length = max(1, min(PF.NSTEP, length))
        notes = [n for s in self.steps[:self.length] for n in s.notes if s.time == PF.ST_NOTE]
        if not keep_range:
            if notes:
                self.lo, self.hi = min(notes) - 3, max(notes) + 3
            if not notes and any(s.hit for s in self.steps[:self.length]) and not self.editable:
                self.lo, self.hi = 58, 62
            elif self.hi - self.lo < 24:
                mid = (self.lo + self.hi) // 2 if notes else 60
                self.lo, self.hi = mid - 12, mid + 12
        self.show_lanes = self.editable or any(s.hit for s in self.steps[:self.length])
        self.updateGeometry()
        self.update()

    def set_zoom(self, step_w: int):
        self.step_w = max(8, min(60, step_w))
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        return QSize(KEY_W + self.length * self.step_w + 2, self._roll_h() + (8 * LANE_H + 6 if self.show_lanes else 0) + 18)

    def minimumSizeHint(self):
        return QSize(200, 120)

    def _rows(self):
        return self.hi - self.lo + 1

    def _roll_h(self):
        return self._rows() * ROW_H

    # ------------------------------------------------------------------ Drawing
    def paintEvent(self, ev):
        # Only paint the visible/to-be-redrawn region: in the scroll area the roll is ~3900×1800 px at full
        # zoom, only a fraction is shown – otherwise every click costs > 100 ms on the Pi.
        p = QPainter(self)
        pal = self.palette()
        clip = QRectF(ev.rect()) if ev is not None else QRectF(self.rect())
        p.setClipRect(clip)
        p.fillRect(clip, pal.color(QPalette.ColorRole.Base))
        text = pal.color(QPalette.ColorRole.Text)
        grid = QColor(text)
        grid.setAlpha(28)
        strong = QColor(text)
        strong.setAlpha(70)
        sw, rh = self.step_w, ROW_H
        top = 16
        c0 = max(0, int((clip.left() - KEY_W) // sw) - 1)           # visible columns (steps) …
        c1 = min(self.length, int((clip.right() - KEY_W) // sw) + 2)
        r0 = max(0, int((clip.top() - top) // rh) - 1)              # … and rows (pitches)
        r1 = min(self._rows(), int((clip.bottom() - top) // rh) + 2)
        x0, x1 = KEY_W + c0 * sw, KEY_W + c1 * sw
        font = QFont(self.font())
        font.setPointSizeF(max(6.5, font.pointSizeF() - 2))
        p.setFont(font)
        # header: step numbers
        for i in range(c0 - c0 % 4, c1):
            if i % 4 == 0:
                p.setPen(text)
                p.drawText(QRectF(KEY_W + i * sw, 0, sw * 4, 14), Qt.AlignmentFlag.AlignLeft, str(i + 1))
        # keyboard + rows
        black = {1, 3, 6, 8, 10}
        shade = QColor(text)
        shade.setAlpha(14)
        for r in range(r0, r1):
            note = self.hi - r
            y = top + r * rh
            if note % 12 in black:
                p.fillRect(QRectF(x0, y, x1 - x0, rh), shade)
            if note % 12 == 0:
                if clip.left() < KEY_W:
                    p.setPen(text)
                    p.drawText(QRectF(2, y - 1, KEY_W - 4, rh + 2), Qt.AlignmentFlag.AlignVCenter, PF.note_name(note))
                p.setPen(QPen(strong, 1))
                p.drawLine(QPointF(x0, y + rh), QPointF(x1, y + rh))
        y_lo, y_hi = top + r0 * rh, top + r1 * rh
        for i in range(c0, min(c1, self.length) + 1):
            p.setPen(QPen(strong if i % 4 == 0 else grid, 1))
            p.drawLine(QPointF(KEY_W + i * sw, y_lo), QPointF(KEY_W + i * sw, min(y_hi, top + self._roll_h())))
        # notes (a note with TIE can start outside on the left – hence from the start of its tie chain)
        hl = pal.color(QPalette.ColorRole.Highlight)
        start = min(c0, self.length - 1)            # (area wider than the roll: c0 can lie past the end)
        while start > 0 and self.steps[start].time == PF.ST_TIE:
            start -= 1
        for i in range(start, c1):
            s = self.steps[i]
            if s.time != PF.ST_NOTE or not s.notes:
                continue
            span = 1
            while i + span < self.length and self.steps[i + span].time == PF.ST_TIE:
                span += 1
            for n in s.notes:
                if not self.lo <= n <= self.hi:
                    continue
                y = top + (self.hi - n) * rh
                c = vel_color(s.vel, hl)
                if s.chance < 100:
                    c.setAlpha(110 + int(s.chance * 1.4))
                rect = QRectF(KEY_W + i * sw + 1, y + 1, span * sw - 2, rh - 2)
                p.fillRect(rect, c)
                if s.accent:
                    p.setPen(QPen(text, 1.5))
                    p.drawRect(rect)
                if s.slide:
                    p.setPen(QPen(text, 1))
                    p.drawLine(rect.topRight(), QPointF(rect.right() + sw / 2, rect.top() - 3))
        # drum lanes
        if self.show_lanes and clip.bottom() >= top + self._roll_h():
            y0 = top + self._roll_h() + 6
            for lane in range(8):
                y = y0 + lane * LANE_H
                p.setPen(text)
                p.drawText(QRectF(2, y, KEY_W - 4, LANE_H), Qt.AlignmentFlag.AlignVCenter, PF.LANES[lane])
                for i in range(c0, c1):
                    rect = QRectF(KEY_W + i * sw + 1, y + 1, sw - 2, LANE_H - 2)
                    s = self.steps[i]
                    if s.hit >> lane & 1:
                        c = QColor(hl)
                        c.setAlpha(255 if s.acc >> lane & 1 else 150)
                        p.fillRect(rect, c)
                    else:
                        p.fillRect(rect, grid)
        p.end()

    # ------------------------------------------------------------------ Editing
    def _hit(self, pos):
        x, y = pos.x() - KEY_W, pos.y() - 16
        if x < 0:
            return None
        i = int(x // self.step_w)
        if not 0 <= i < self.length:
            return None
        if 0 <= y < self._roll_h():
            return ("note", i, self.hi - int(y // ROW_H))
        y2 = y - self._roll_h() - 6
        if self.show_lanes and 0 <= y2 < 8 * LANE_H:
            return ("lane", i, int(y2 // LANE_H))
        return None

    def mousePressEvent(self, e):
        if not self.editable:
            return
        h = self._hit(e.position())
        if not h:
            return
        kind, i, v = h
        self.about_to_change.emit()
        s = self.steps[i]
        if e.button() == Qt.MouseButton.RightButton:
            self.steps[i] = PF.Step()
        elif kind == "note":
            if s.time != PF.ST_NOTE:
                s.time, s.notes, s.vel = PF.ST_NOTE, [], self.edit_vel
            if v in s.notes:
                s.notes.remove(v)
                if not s.notes and not s.hit:
                    self.steps[i] = PF.Step()
            elif len(s.notes) < 4:
                s.notes.append(v)
                s.notes.sort()
        else:
            bit = 1 << v
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier and s.hit & bit:
                s.acc ^= bit
            elif s.hit & bit:
                s.hit &= ~bit
                s.acc &= ~bit
                if not s.hit and not s.notes:
                    self.steps[i] = PF.Step()
            else:
                if s.time != PF.ST_NOTE:
                    s.time, s.notes, s.vel = PF.ST_NOTE, [], self.edit_vel
                s.hit |= bit
        self.update()
        self.changed.emit()


# --------------------------------------------------------------------------- Preview ---
def render_preview(events, bpm: float, rate: int = 22050, max_secs: float = 30.0) -> list[int]:
    """NoteEvents (midiexport) -> 16-bit samples. Simple sawtooth with envelope; drums as noise/sine."""
    import numpy as np
    spb = 60.0 / max(20.0, bpm)
    if not events:
        return [0] * (rate // 4)
    end = min(max_secs, max((e.start + e.length) * spb for e in events) + 0.4)
    out = np.zeros(int(end * rate) + 1, dtype=np.float32)
    rng = np.random.default_rng(1)
    for e in events:
        t0 = int(e.start * spb * rate)
        if t0 >= len(out):
            continue
        amp = 0.18 * (e.vel / 127)
        if e.drum:
            n = int(0.18 * rate)
            t = np.arange(n) / rate
            if e.note in (35, 36):
                sig = np.sin(2 * np.pi * (50 + 120 * np.exp(-t * 30)) * t) * np.exp(-t * 18) * 2.2
            elif e.note in (42, 44, 46):
                sig = rng.standard_normal(n) * np.exp(-t * (12 if e.note == 46 else 45)) * 0.6
            else:
                sig = (rng.standard_normal(n) * 0.6 + np.sin(2 * np.pi * 190 * t)) * np.exp(-t * 22)
        else:
            dur = max(0.03, e.length * spb)
            n = int((dur + 0.08) * rate)
            t = np.arange(n) / rate
            f = 440.0 * 2 ** ((e.note - 69) / 12)
            saw = 2 * ((t * f) % 1.0) - 1
            env = np.minimum(1.0, t / 0.008) * np.where(t < dur, 1.0, np.exp(-(t - dur) * 40))
            sig = (0.6 * saw + 0.4 * np.sin(2 * np.pi * f * t)) * env
        seg = out[t0:t0 + len(sig)]
        seg += (sig[:len(seg)] * amp).astype(np.float32)
    peak = float(np.max(np.abs(out))) or 1.0
    out = out / max(1.0, peak) * 0.9
    return (out * 32767).astype(np.int16).tolist()
