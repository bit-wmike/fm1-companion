# Simulator workstream – notes

## What ships (`fm1companion/sims/`)
| name | source | toolchain | wasm | realtime factor (x86 container, node 22) |
|---|---|---|---|---|
| `fm1-open` | m-vave/fm1_open @ 4c7496d (V14) | WASI SDK 27 (clang 20.1.8), flags of `tools/Build-Web.ps1` | 296 KB | ~58× |
| `melodee` | keremimo/melodee tag v1.6.0-rc.7 | WASI SDK 27 instead of Emscripten (emsdk LLVM host blocked) | 1.1 MB | ~19× |

Pi 5 (Cortex-A76 @ 2.4 GHz) is roughly 3–5× slower per core than this container for WASM; both builds
should keep a comfortable margin (Melodee ≈ 4–6×, FM1 Open ≈ 12–20× realtime, estimated, not measured).
The *page* (canvas drawing, 30–60 fps frame posts) costs more than the DSP on the Pi; Firefox is faster there.

Rebuild: `python3 tools/build_sims.py --repo fm1-open=/path/to/fm1_open --repo melodee=/path/to/melodee`.

## Decisions
* Official workbench shipped whole (play/edit, projects & voices, samples, manual) with `FM1_PACKAGE = null`
  (it cannot flash anything) and the update tab hidden. Projects & voices tab talks only the FM1 Open library
  protocol (`F0 7D 46 4D …`); Melodee/Felucca ignore it.
* Simulator/online pages may only send channel messages and `F0 7D 46 4D` SysEx to real ports
  (`sims.sim_out_allowed`, enforced in `MidiBridge.out_filter`): exactly one well-formed message per send, no SysEx except the FM1-Open library header; online editors (`kind="editor"`) get `sims.editor_out_allowed`, which additionally passes `F0 7D/7E/7F …`. Update key `F0 22 24 35 7F`, soft key `F0 22 24 35 7D` (ROM boot), handshake `F0 00 32 45` and the packed update stream are always dropped; the page shows how many messages were held back.
* `_host/fm1sim-host.js` (bundled pages load it; online pages get it injected at document creation):
  MIDI adapter (worklet messages for fm1-open/melodee, computer-key fallback), recording tap → WAV download,
  `?fm1latency=` latencyHint override, SSE MIDI relay when opened in Firefox, and a workaround for
  Chromium 102 (Qt 6.4) which forbids synchronous `WebAssembly.Module/Instance` > 4 KB on the main thread.
* Online pages get MIDI via their own Web-MIDI port list ("FM-1 (keys)", chosen system inputs).
* Additional sources (time-boxed): **fm1-emu** (Joakim Eriksson, MIT; CPU emulator running real .fwsc)
  is listed online, labelled slower, Firefox recommended – not bundled (Rust/cargo pages build with a firmware
  catalog step, out of time box). **FM-1 Workbench** (thegiantsnail) online entry. **RemoteFM1** (Quixotic7)
  link only (opens the GitHub page). QEMU fm1-emulator skipped (x86-64 only).
