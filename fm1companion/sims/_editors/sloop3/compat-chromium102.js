// SPDX-License-Identifier: GPL-3.0-only
// FM-1 Companion's own addition – NOT part of SLOOP 3's editor (editor.html beside this file is upstream's,
// unchanged). The app runs this once after the editor has loaded.
//
// Why: Raspberry Pi OS Bookworm has Qt 6.4, whose web view is Chromium 102. SLOOP 3's editor uses one thing newer
// than that: CSS color-mix() (Chromium 111) for the tint of a pattern pad that holds notes
// (web/src/editor.css: .pad.has{background:color-mix(in srgb,var(--c,#888) 30%,var(--panel2))}).
// An older Chromium drops that one declaration: everything works, but used and empty pattern pads look the same.
// The rule below gives the same colour without color-mix(): the track's colour under a 70 % layer of the panel
// colour (#1f1b2b = --panel2, B3 = 70 %). It sits BEFORE the editor's own style sheet, so wherever the editor's
// own rule is understood, that one wins, and the editor's later rules (.pad.cur, .pad.lit) win as they do upstream.
(function () {
  "use strict";
  if (document.getElementById("fm1c-compat")) return;
  var s = document.createElement("style");
  s.id = "fm1c-compat";
  s.textContent = ".pad.has{background:linear-gradient(#1f1b2bb3,#1f1b2bb3),var(--c,#888)}";
  document.head.insertBefore(s, document.head.firstChild);
})();
