# SPDX-License-Identifier: GPL-3.0-only
"""FM-1: alles, was das Gerät über sich verrät – Verbindung, Live-Last, Speicher, User-Presets.

Solange die Seite offen ist, reserviert die App das Gerät (der Editor pausiert und verbindet
sich beim Zurückwechseln von selbst neu). So stören sich die Live-Abfragen und der Editor nicht.
Alle Abfragen sind rein lesend.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QProgressBar, QPushButton, QScrollArea, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                             QWidget)

from .. import health
from ..devices import alsa_card_for
from .common import fmt_size, heading, icon, run_job

LIVE_MS = 2000


def _bar(fmt: str = "") -> QProgressBar:
    b = QProgressBar()
    b.setRange(0, 1000)
    b.setFormat(fmt)
    b.setTextVisible(True)
    return b


def _set_bar(b: QProgressBar, v, total, text: str):
    b.setValue(int(min(1.0, v / total) * 1000) if total else 0)
    b.setFormat(text)


def _lab(text="–") -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


class FM1Page(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.active = False
        self.held_link = None
        self.live_busy = False
        self.presets: list = []
        self.engines: list[str] = []

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.addWidget(heading("FM-1", "Was das angeschlossene Gerät über sich meldet. Solange diese Seite offen "
                                      "ist, pausiert der Editor; beim Zurückwechseln verbindet er sich von selbst."))
        self.none = QLabel("Kein FM-1 verbunden.")
        self.none.setWordWrap(True)
        self.none.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.none)
        self.content = QWidget()
        grid = QGridLayout(self.content)
        grid.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.content)

        # --- Gerät
        g = QGroupBox("Gerät")
        self.dev = QFormLayout(g)
        self.f = {}
        for k in ("Profil", "Firmware", "Engines", "Spuren", "Seriennummer"):
            self.f[k] = _lab()
            self.dev.addRow(k + ":", self.f[k])
        grid.addWidget(g, 0, 0)

        # --- Verbindung
        c = QGroupBox("Verbindung")
        self.con = QFormLayout(c)
        for k in ("USB-Gerät", "USB-ID", "Tempo", "Anschluss", "MIDI-Port", "Soundkarte", "USB-Audio"):
            self.f[k] = _lab()
            self.con.addRow(k + ":", self.f[k])
        grid.addWidget(c, 0, 1)

        # --- Live
        lv = QGroupBox("Live")
        ll = QVBoxLayout(lv)
        top = QHBoxLayout()
        self.cb_live = QCheckBox(f"alle {LIVE_MS // 1000} s aktualisieren")
        self.cb_live.setChecked(True)
        self.cb_live.toggled.connect(self._live_toggled)
        top.addWidget(self.cb_live)
        top.addStretch(1)
        self.live_state = QLabel("")
        top.addWidget(self.live_state)
        ll.addLayout(top)
        lf = QFormLayout()
        ll.addLayout(lf)
        self.cpu = _bar()
        lf.addRow("Audio-CPU:", self.cpu)
        self.ram = _bar()
        lf.addRow("Arbeitsspeicher:", self.ram)
        self.cache = _bar()
        lf.addRow("Cache-RAM:", self.cache)
        for k in ("Stimmen", "Überlast", "Pro Spur", "Zweiter Kern", "Rechenzeit", "USB-Audio-Fehler", "Speichertest"):
            self.f["L" + k] = _lab()
            lf.addRow(k + ":", self.f["L" + k])
        grid.addWidget(lv, 1, 0, 1, 2)

        # --- Speicher
        sp = QGroupBox("Speicher")
        sl = QVBoxLayout(sp)
        sf = QFormLayout()
        sl.addLayout(sf)
        self.pool = _bar()
        sf.addRow("Speicher-Pool:", self.pool)
        for k in ("Projekte", "User-Presets", "Laufende Musik", "Sample-Slots", "Komplett-Backup"):
            self.f["S" + k] = _lab()
            sf.addRow(k + ":", self.f["S" + k])
        h = QHBoxLayout()
        b = QPushButton(icon("view-refresh"), "Neu einlesen")
        b.clicked.connect(self.refresh)
        h.addWidget(b)
        h.addStretch(1)
        sl.addLayout(h)
        grid.addWidget(sp, 2, 0)

        # --- User-Presets
        up = QGroupBox("User-Presets")
        ul = QVBoxLayout(up)
        fl = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Name oder Engine suchen …")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._fill_presets)
        fl.addWidget(self.filter, 1)
        self.cb_used = QCheckBox("nur belegte")
        self.cb_used.setChecked(True)
        self.cb_used.toggled.connect(self._fill_presets)
        fl.addWidget(self.cb_used)
        ul.addLayout(fl)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Platz", "Engine", "Name", "★"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.setMinimumHeight(260)
        ul.addWidget(self.tree, 1)
        self.preset_note = QLabel("")
        self.preset_note.setWordWrap(True)
        ul.addWidget(self.preset_note)
        grid.addWidget(up, 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addStretch(1)

        self.timer = QTimer(self, interval=LIVE_MS)
        self.timer.timeout.connect(self._live_tick)
        ctx.changed.connect(self._dev_changed)
        self._dev_changed()

    # ------------------------------------------------------------ Seite
    def activate(self):
        self.active = True
        self._dev_changed()

    def deactivate(self):
        self.active = False
        self.timer.stop()
        self._release()

    def _hold(self):
        link = self.ctx.link
        if self.held_link is link or link is None or link.closed:
            return
        self._release()
        link.hold(settle=False)
        self.held_link = link

    def _release(self):
        if self.held_link is not None:
            if not self.held_link.closed:
                self.held_link.release()
            self.held_link = None

    def _generic_text(self) -> str:
        """was über ein FM-1 ohne Editor-Protokoll (oder im Update-Modus) bekannt ist"""
        c = self.ctx
        p, prof = c.port, c.profile
        from .device import firmware_name
        if getattr(c, "firmware_ambiguous", False):
            fw = "unbekannt – bitte wählen (Kennung wird von mehreren Firmwares benutzt)"
        else:
            fw = firmware_name(c.firmware_key) or "unbekannt"
            if c.firmware_key and not (prof and prof.firmware_choice.get(c.identity)) and not c.info:
                fw += " (geraten aus der Kennung)" if c.firmware_guess else ""
        usb = " · ".join(x for x in (" ".join(y for y in (p.manufacturer, p.product) if y), p.usb_id,
                                      f"USB-Port {p.usb_path}" if p.usb_path else "",
                                      f"{p.speed} Mbit/s" if p.speed else "") if x) or "–"
        mode = getattr(c, "protocol", "")
        rows = [("Profil", prof.name if prof else "–"),
                ("Zustand", c.status_text() if hasattr(c, "status_text") else "verbunden"),
                ("Kennung", c.identity or "noch nicht gelesen"),
                ("Firmware", fw),
                ("Seriennummer", p.serial or "keine"),
                ("USB", usb),
                ("MIDI-Port", p.out_name)]
        tail = ("Das FM-1 ist im Update-Modus – die Seite „Rettung“ schließt die Installation ab."
                if mode == "update" else
                "Live-Werte, Speicher und User-Presets liefert nur Melodee bzw. M-VAVE FM1 Open (Editor-Protokoll). "
                "Diese Firmware kann die App installieren und per MIDI ansprechen, aber nicht auslesen.")
        return ("<b>FM-1 verbunden – ohne Editor-Protokoll</b><table cellspacing=4>" +
                "".join(f"<tr><td>{k}:</td><td>{v}</td></tr>" for k, v in rows) + f"</table><p>{tail}</p>")

    def _dev_changed(self):
        ok = self.ctx.connected
        self.none.setVisible(not ok)
        self.content.setVisible(ok)
        if not ok:
            self.none.setText(self._generic_text() if self.ctx.port is not None else "Kein FM-1 verbunden.")
            self.timer.stop()
            self._release()
            self.presets = []
            return
        self._static()
        key = self.ctx.port.key if self.ctx.port else ""
        if key != getattr(self, "_shown_key", ""):        # anderes Gerät: keine Werte des vorigen zeigen
            self._shown_key = key
            self._clear_dynamic()
        if self.active:
            self._hold()
            self.refresh()

    def _clear_dynamic(self):
        self.presets = []
        self._fill_presets()
        self._show_live(None, "noch nicht gelesen")
        for k in ("SProjekte", "SUser-Presets", "SLaufende Musik", "SSample-Slots", "SKomplett-Backup"):
            self.f[k].setText("–")
        _set_bar(self.pool, 0, 0, "noch nicht gelesen")
        self.preset_note.setText("")
        self.live_state.setText("")

    # ------------------------------------------------------------ fest
    def _static(self):
        p, info, prof = self.ctx.port, self.ctx.info, self.ctx.profile
        self.engines = list(info.engines) if info else []
        self.f["Profil"].setText(prof.name if prof else "–")
        self.f["Firmware"].setText(info.version if info else "–")
        self.f["Engines"].setText(", ".join(self.engines) or "–")
        self.f["Spuren"].setText(f"{info.ntrk} Spuren × {info.nstep} Steps" if info else "–")
        self.f["Seriennummer"].setText(p.serial or "keine")
        self.f["USB-Gerät"].setText(" ".join(x for x in (p.manufacturer, p.product) if x) or p.label)
        self.f["USB-ID"].setText(p.usb_id or "–")
        self.f["Tempo"].setText(f"{p.speed} Mbit/s" + (" (Full Speed)" if p.speed == "12" else "") if p.speed else "–")
        self.f["Anschluss"].setText(f"USB-Port {p.usb_path}" if p.usb_path else "–")
        self.f["MIDI-Port"].setText(p.out_name)
        card = alsa_card_for(p)
        self.f["Soundkarte"].setText(f"{card} (Karte {p.card})" if card and p.card is not None else (card or "–"))

    # ------------------------------------------------------------ Abfragen
    def refresh(self):
        if not self.ctx.connected:
            return
        self._hold()
        link = self.ctx.link
        self.live_state.setText("lese …")

        def work(w):
            rep = health.fm1_report(link)       # sendet AUDIO_STATS (72) nur an Melodee / FM1 Open
            try:
                rep["preset_list"] = health.fm1_presets(link)
            except Exception:
                rep["preset_list"] = None
            return rep
        run_job(self, work, on_done=self._report, on_fail=lambda msg, tb: self.live_state.setText(f"Fehler: {msg}"))

    def _report(self, r: dict):
        import time as _t
        self.live_state.setText(f"eingelesen um {_t.strftime('%H:%M:%S')} (nur gelesen, nichts verändert)")
        if r.get("error"):
            self.live_state.setText(f"Fehler: {r['error']}")
            return
        self._show_live(r.get("audio"))
        if r.get("pool_total"):
            _set_bar(self.pool, r["pool_used"], r["pool_total"],
                     f"{fmt_size(r['pool_used'])} von {fmt_size(r['pool_total'])} (Projekte + Presets)")
        else:
            _set_bar(self.pool, 0, 0, "bei dieser Firmware nicht bekannt")
        if r.get("projects_total"):
            self.f["SProjekte"].setText(f"{r['projects_used']} von {r['projects_total']} Plätzen belegt "
                                       f"({fmt_size(r['projects_bytes'])})")
            self.f["SLaufende Musik"].setText(fmt_size(r.get("live_bytes")))
        else:
            self.f["SProjekte"].setText("–")
            self.f["SLaufende Musik"].setText("–")
        self.f["SKomplett-Backup"].setText(f"{fmt_size(r['backup_bytes'])} in {r['objects']} Objekten"
                                          if r.get("backup_bytes") else "–")
        slots = r.get("sample_slots")
        self.f["SSample-Slots"].setText(
            "keine (bei Melodee ab 1.5 entfernt)" if not slots else
            ", ".join(f"USR{s.index + 1}: {'leer' if s.empty else s.name}" for s in slots))
        self.presets = r.get("preset_list") or []
        if r.get("preset_list") is None:
            self.f["SUser-Presets"].setText("–")
            self.preset_note.setText("Diese Firmware hat keine gemeinsame Preset-Bibliothek (gibt es ab Melodee 1.5).")
        else:
            used = sum(s.used for s in self.presets)
            fav = sum(s.favorite for s in self.presets)
            self.f["SUser-Presets"].setText(f"{used} von {len(self.presets)} belegt" + (f", {fav} Favoriten" if fav else ""))
            self.preset_note.setText("Bearbeiten, laden und speichern: im Editor unter „Library“.")
        self._fill_presets()
        if self.active and self.cb_live.isChecked() and r.get("audio_supported", True):
            self.timer.start()

    def _live_toggled(self, on: bool):
        if on and self.active and self.ctx.connected:
            self.timer.start()
            self._live_tick()
        else:
            self.timer.stop()

    def _live_tick(self):
        if self.live_busy or not self.ctx.connected or not self.active:
            return
        version = self.ctx.info.version if self.ctx.info else ""
        if not health.audio_stats_supported(version):
            self._show_live(None)               # Befehl 72 bedeutet bei anderen Firmwares etwas anderes
            self.timer.stop()
            return
        self.live_busy = True
        link = self.ctx.link

        def done(v):
            self.live_busy = False
            self._show_live(v)

        def fail(msg, tb):
            self.live_busy = False
            self.live_state.setText("keine Live-Werte")
        run_job(self, lambda w: health.fm1_live(link, version), on_done=done, on_fail=fail)

    def _show_live(self, a: dict | None, empty_text: str = "keine Audio-Werte bei dieser Firmware"):
        if not a:
            for k in ("Stimmen", "Überlast", "Pro Spur", "Zweiter Kern", "Rechenzeit", "USB-Audio-Fehler", "Speichertest"):
                self.f["L" + k].setText("–")
            _set_bar(self.cpu, 0, 0, empty_text)
            self.f["USB-Audio"].setText("–")
            return
        cpu = a.get("cpu_percent", 0)
        _set_bar(self.cpu, cpu, 100, f"{cpu:.0f} %")
        self.f["USB-Audio"].setText(
            ("Wiedergabe " + (f"{a['play_rate']} Hz" if a.get("play_alt") else "aus")) + " · " +
            ("Aufnahme " + (f"{a['capture_rate']} Hz" if a.get("capture_alt") else "aus")))
        self.f["LUSB-Audio-Fehler"].setText(
            f"Aussetzer Wiedergabe {a.get('play_underruns', 0)} / Aufnahme {a.get('capture_overruns', 0)} · "
            f"fehlerhafte Pakete {a.get('bad_packets', 0)} · verpasste USB-Frames {a.get('missed_usb_frames', 0)}")
        self.f["LRechenzeit"].setText(
            f"längster Audio-Block {a.get('longest_render_us', 0)} µs · verspätete Blöcke {a.get('late_renders', 0)}")
        if "voices_active" in a:
            self.f["LStimmen"].setText(f"{a['voices_active']} aktiv, {a['voices_held']} gehalten")
            self.f["LÜberlast"].setText(f"{a['voices_shed']} Stimmen abgeschnitten (CPU), "
                                        f"{max(0, a['voices_given_up'] - a['voices_shed'])} an das Stimmenbudget abgegeben")
        else:
            self.f["LStimmen"].setText("–")
            self.f["LÜberlast"].setText("–")
        if "track_1_voices_shed" in a:
            self.f["LPro Spur"].setText(" · ".join(
                f"Spur {i}: {a[f'track_{i}_voices_shed']}/{a[f'track_{i}_voices_given_up']}" for i in range(1, 5))
                + "  (abgeschnitten/abgegeben)")
        else:
            self.f["LPro Spur"].setText("–")
        if "core1_online" in a:
            faults = a.get("core1_faults", 0) + a.get("core1_rejected", 0)
            self.f["LZweiter Kern"].setText(
                ("aktiv" if a["core1_online"] else "aus") + f" · {a['core1_jobs']} Aufgaben, längste "
                f"{a['core1_max_job_us']} µs, Timeouts {a['core1_timeouts']}" + (f" · {faults} Fehler!" if faults else ""))
        else:
            self.f["LZweiter Kern"].setText("–")
        if a.get("resource_capacity"):
            _set_bar(self.ram, a["resource_used"], a["resource_capacity"],
                     f"{fmt_size(a['resource_used'])} von {fmt_size(a['resource_capacity'])} "
                     f"(Spitze {fmt_size(a['resource_peak'])})" + (f", {a['resource_failures']} Engpässe"
                                                                     if a.get("resource_failures") else ""))
            _set_bar(self.cache, a.get("cache_used", 0), a.get("cache_capacity", 0),
                     f"{fmt_size(a.get('cache_used'))} von {fmt_size(a.get('cache_capacity'))}")
            st = a.get("cache_status", 0)
            self.f["LSpeichertest"].setText("beim Start bestanden" if st == 1 else
                                            ("nicht gelaufen" if st == 0 else f"Status {st} – auffällig"))
        else:
            _set_bar(self.ram, 0, 0, "–")
            _set_bar(self.cache, 0, 0, "–")
            self.f["LSpeichertest"].setText("–")
        if self.timer.isActive():
            self.live_state.setText("live")

    # ------------------------------------------------------------ Presets
    def _engine(self, e: int) -> str:
        from .. import presetlib
        return presetlib.engine_display_name(e, self.engines)

    def _fill_presets(self):
        q = self.filter.text().strip().lower()
        only = self.cb_used.isChecked()
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for s in self.presets:
            if only and not s.used:
                continue
            eng = self._engine(s.engine) if s.used else ""
            if q and q not in s.name.lower() and q not in eng.lower() and q not in s.label.lower():
                continue
            it = QTreeWidgetItem([s.label, eng, s.name if s.used else "(leer)", "★" if s.favorite else ""])
            self.tree.addTopLevelItem(it)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        for c in range(4):
            self.tree.resizeColumnToContents(c)
