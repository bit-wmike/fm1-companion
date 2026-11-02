/* SPDX-License-Identifier: GPL-3.0-only */
/* Melodee studio twin: the firmware of the FM-1 (Melodee, unchanged sources) for the host, as WebAssembly,
 * with what the browser emulator (web/emu/melodee_web.c) leaves out: the NOR flash (settings, projects, user
 * presets: storage.c on a 1 MiB RAM image, as tests/backup_test.c does it) and the USB SysEx path with the editor
 * protocol (usb.c sysex_byte -> editor.c ed_service -> ota_wire_send, as tests/editor_test.c drives it). The boot
 * follows firmware/src/main.c fm1_main, the clock and the audio ISR follow melodee_web.c / audio.c:
 *   every millisecond   ui_input (main.c's busy loop) and ed_service
 *   every 15 ms         main.c's loop body: pattern_poll, cz / fm6 / editor service, ui_input, glo_poll,
 *                       settings_poll, ui_leds, ui_draw
 *   every 128 frames    audio.c's half buffer: four CTL blocks of mix_block + click_render (the codec gets Q15 << 7)
 * Built by FM-1 Companion's tools/build_sims.py (target melodee-studio) from Melodee's tagged sources; nothing of
 * the firmware is modified. Host stand-ins (documented in the NOTICE): the HAL (keys, LEDs, LCD into a frame buffer,
 * ADC = the MASTER value given here), NOR flash = nor[] (JEDEC id of the expected part, no erase / program
 * timing), USB = always configured, SysEx in as USB-MIDI packets, SysEx and MIDI out drained from the USB rings;
 * CPU1 = online, its jobs run serially (the same kernels; float sums such as FM6's paired voices may differ in
 * the last bits); project.c compiled as for the target (FM1_IRQ_TARGET: none of the RAM-flash tests' 4-slot
 * mirrors); ota.c answers the identity handshake (MELODEE_ID) but an update request is dropped and its flash hooks
 * refuse; no BLE, no USB host, no overload shedding (the host never overloads).
 * The twin ABI (st_*: boot, flash, MIDI in/out, panel, LEDs, screen, render, transport, projects) is what
 * fm1companion/sims/_studio/twin-core.js expects; a glue for another firmware exports the same set. */
#include <stdint.h>

/* the production profile (performance_options.h / build.py): resident audio with CPU1 online (voice budget 18) */
#define MELODEE_RAM_AUDIO 1
#define MELODEE_DUAL_CORE 1
static int audio_worker_online = 1;
static int audio_worker_submit(void (*fn)(void *), void *ctx) { (void)fn; (void)ctx; return 0; }   /* serial */
static int audio_worker_join(void) { return 1; }
#define MELODEE_USB_AUDIO 1                     /* as the release: the USB audio menu, stems setting, descriptors */
#define MELODEE_OTA 1                           /* the editor SysEx plumbing in usb.c */
#ifndef MELODEE_VERSION
#define MELODEE_VERSION "v1.6.0-rc.7"           /* build_sims.py passes the tag's (melodee.c) */
#endif
#ifndef MELODEE_ID
#define MELODEE_ID "FM-1_900"                    /* build_sims.py passes the release identity (fm1pkg_make.py) */
#endif
#define main hostsim_main
#include "hostsim.c"
#undef main
#define NPART_DIAG NPART

/* ------------------------------------------------------------ HAL stubs (as tests/ui_test.c) --- */
#define FM1_NCOL 11u
static const int8_t FM1_KEYMAP[6][FM1_NCOL] = {      /* hal/fm1_input.h, as melodee_web.c */
    {-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1},
    { 5, 11,  4, 10,  3,  9,  2,  8, -1, -1, -1},
    {34, 35, 36, 37, 38, 40, 39, 13,  7,  6, 12},
    {23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33},
    { 0,  1, 15, 14, 17, 16, 19, 18, 20, 21, 22},
    {-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1},
};
static uint8_t fm1_led[FM1_NCOL];
static uint8_t fm1_led_dim[3][FM1_NCOL], fm1_led_dim_mask[3];
#define FM1_TICKS_PER_US 1u
static uint32_t host_ticks, host_pressed, host_notes;
static int32_t host_enc[7];
static uint32_t fm1_ticks(void) { return host_ticks; }
static uint32_t fm1_input_edges(int x) { uint32_t p = host_pressed; (void)x; host_pressed = 0; return p; }
static uint32_t fm1_input_note_edges(void) { uint32_t n = host_notes; host_notes = 0; return n; }
static int32_t fm1_enc_take(uint32_t e)
{
    e %= 7u;
    int32_t s = host_enc[e]; host_enc[e] = 0;
    return s;
}
static void fm1_irq_off(void) {}
static void fm1_irq_on(void) {}
static uint16_t host_screen[240 * 240];
static void lcd_fill(uint32_t x, uint32_t y, uint32_t w, uint32_t h, uint16_t c)
{
    uint32_t i, j;
    for (j = 0; j < h && y + j < 240u; j++)
        for (i = 0; i < w && x + i < 240u; i++)
            host_screen[(y + j) * 240u + x + i] = (uint16_t)((c >> 8) | (c << 8));
}
static void lcd_sync(void) {}
#define SCOPE_N 512u                              /* audio.c: the HOME oscilloscope */
static int16_t scope_buf[SCOPE_N];
static uint32_t scope_w;
static void lcd_blit(uint32_t x, uint32_t y, uint32_t w, uint32_t h, const uint16_t *p)
{
    uint32_t i, j;
    for (j = 0; j < h && y + j < 240u; j++)
        for (i = 0; i < w && x + i < 240u; i++) host_screen[(y + j) * 240u + x + i] = p[j * w + i];
}
/* audio.c's diagnostics (editor AUDIO_STATS reads them; the twin has no ISR timing: zeros) */
static struct { uint32_t magic, halves, max_us, nested, in_audio, late, timer_irqs, ui_frames, last_us, cpu_q8, boots,
                stage, page, home; } melodee_dbg;
static uint32_t shed_count, shed_part[NPART_DIAG];
static uint32_t audio_irq_max_ticks, audio_timer5_max_ticks, audio_full_max_us, audio_full_last_us,
                audio_deadline_misses, audio_target_exceeded;
static struct { uint32_t jobs, max_job_ticks, max_wait_ticks, timeouts, rejected, misread; } audio_worker;
static struct { uint32_t count, cpu, dbg, pc, rets, emu; } fm1_core1_fault;
static struct { uint32_t status, con_before, data_before, instruction_before, con_after, data_after,
                instruction_after, failure_address, test_ticks; } fm1_cache;
static uint32_t in_tick;                          /* the clock is running (fm1_wdt_feed must not advance it) */
static void twin_wait_ms(void);
static void fm1_wdt_feed(void) { twin_wait_ms(); }   /* the firmware's waits: the audio ISR runs on meanwhile */

#define MELODEE_FLASH 1
#include "../firmware/src/gfx.c"
#include "../firmware/src/panel.c"
#include "../firmware/src/ui.c"
#include "../firmware/src/icons.c"
#include "../firmware/src/ui_graph.c"
#include "../firmware/src/ui_draw.c"
#include "../firmware/src/ui_menu.c"
#include "../firmware/src/ui_input.c"
#include "../firmware/src/ui_layer.c"

/* ------------------------------------------------------------ the NOR flash (as tests/backup_test.c) --- */
static uint8_t nor[0x100000];
static uint8_t flash_ok;
static int st_read(uint32_t off, void *dst, uint32_t n)
{
    if (off > sizeof nor || n > sizeof nor - off) return -1;
    memcpy(dst, nor + off, n); return 0;
}
static int st_erase(uint32_t off)
{
    if (off >= sizeof nor || (off & 4095u)) return -8;
    memset(nor + off, 0xFF, 4096); return 0;
}
static int st_prog(uint32_t off, const void *src, uint32_t n)   /* NOR: programming only clears bits */
{
    const uint8_t *s = src;
    if (off > sizeof nor || n > sizeof nor - off) return -8;
    for (uint32_t i = 0; i < n; i++) nor[off + i] &= s[i];
    return 0;
}
static uint32_t irq_save(void) { return 0; }
static void irq_restore(uint32_t f) { (void)f; }
static uint32_t fl_jedec_ram(void) { return 0x856014u; }   /* the expected 1 MiB part (project.c persist_boot) */
static void fl_plain_window_init(void) {}
static void audio_silence(void) {}
#define FL_FAR(fn) (fn)
#include "../firmware/src/storage.c"
#include "../firmware/src/upreset.c"
/* project.c as the device compiles it: its host-only mirrors of projects 1..4 (proj_slot / proj_bank_slot, four
 * entries, indexed by any of the 32 slots) belong to the RAM-flash tests, not to a build with MELODEE_FLASH; the
 * device's settings_poll also waits for silent voices and idle USB audio before a settings save */
#define FM1_IRQ_TARGET 1
#include "../firmware/src/project.c"
#undef FM1_IRQ_TARGET
#include "../firmware/src/cz_store.c"
#include "../firmware/src/fm6_store.c"
#define ED_BK_FLASH_PTR(off) (nor + (off))      /* fm1_xip_ptr: the flash read in place */
/* ota.c: its handshake answers the package identity (MELODEE_ID) as on the device; the update itself never runs
 * here (usb.ota_req is dropped) and its flash hooks refuse */
static uint32_t ota_now_ms(void) { return fm1_ms; }
static void twin_drain(void);
static void ota_idle(void) { twin_drain(); }      /* ota_wire_send waits for room in the SysEx ring */
static int ota_erase(uint32_t off) { (void)off; return -1; }
static int ota_prog(uint32_t off, const void *p, uint32_t n) { (void)off; (void)p; (void)n; return -1; }
static int ota_fread(uint32_t off, void *p, uint32_t n) { return st_read(off, p, n); }
static void ota_show(uint32_t step, int32_t code) { (void)step; (void)code; }
static void ota_commit(const uint8_t *parm) { (void)parm; }
#define OTA_SCRATCH proj_wire_borrow()          /* as melodee.c */
#include "../firmware/src/ota.c"
#include "../firmware/src/editor.c"

/* ------------------------------------------------------------ boot (main.c fm1_main / melodee_init) --- */
static void melodee_init(void)                    /* main.c, verbatim */
{
    uint32_t i;
    trk_voices_bind();
    midi_insert_init();
    punch_reset();
    chain_defaults(&chain_config);
    pattern_init();
    for (i = 0; i < G_COUNT; i++)
        song.g[i] = GP[i].def;
    undo_depth++;                             /* (no undo copy of the power-on loads) */
    fm6_init();                               /* every track's FM6 patch: the init voice */
    cz_init();
    for (i = 0; i < NTRK; i++) {
        track_t *t = &trk[i];
        track_defaults(t);
        set_engine_of(t, trk_def_engine(i));
        apply_preset_to(t, trk_def_preset(i));    /* with its sends */
        t->engine = t->eng_req;
        track_defaults_steps(t);              /* (a sound load never touches them) */
        pat_sig[i] = steps_sig(t);            /* (empty: not the user's) */
        pat_last[i] = 0;
    }
    undo_depth--;
    song.sel = 0;
    song.master_q12 = 2048;
    ui.home = 1;
    ui.force = 1;
}

#undef __attribute__                              /* (hostsim.c drops attributes for the firmware files) */
#define EXPORT(name) __attribute__((used, export_name(#name)))

static uint32_t now_ms, fb_gen, booted, master_knob = 1023u * 16u;
static uint16_t fb_seen[240 * 240];

EXPORT(st_flash) uint8_t *st_flash(void) { return nor; }          /* the whole NOR image (write it before st_boot) */
EXPORT(st_flash_size) uint32_t st_flash_size(void) { return sizeof nor; }
EXPORT(st_flash_erase) void st_flash_erase(void) { memset(nor, 0xFF, sizeof nor); }   /* a new, empty device */

EXPORT(st_boot) uint32_t st_boot(uint32_t master)   /* fm1_main up to the main loop; 1 = flash found */
{
    if (booted)
        return flash_ok;
    booted = 1;
    master_knob = (master > 1023u ? 1023u : master) * 16u;
    usb.config = 1;                               /* plugged into the Suite: the editor protocol is live */
    resource_host_cache_enabled = 1;              /* MELODEE_CACHE_RAM: the cache arena is ready, as on the device */
    settings_init();
    dsp_tables_init();
    persist_boot();
    settings_init();
    panel_init();
    melodee_init();
    glo_restore();                                /* CLK TUNE MIDI ROUT as last used, */
    project_boot();                               /* then the BOOT project or the template */
    memset(host_screen, 0, sizeof host_screen);
    ui.force = 1;
    return flash_ok;
}

/* the panel: matrix ids (PANEL_DEFAULT's), as fm1_in and the scan would have them (melodee_web.c) */
EXPORT(st_keys) void st_keys(uint32_t m) { host_notes |= m & ~fm1_in.notes; fm1_in.notes = m; }
EXPORT(st_buttons) void st_buttons(uint32_t m) { host_pressed |= m & ~fm1_in.buttons; fm1_in.buttons = m; }
EXPORT(st_encoder) void st_encoder(uint32_t e, int32_t n) { if (e < 7u) host_enc[e] += n; }
EXPORT(st_master) void st_master(uint32_t v) { master_knob = (v > 1023u ? 1023u : v) * 16u; }   /* 0..1023 */

/* ---- USB MIDI: in as USB-MIDI event packets (usb.c midi_in_event), out from the SysEx and MIDI rings ---- */
static uint8_t out_buf[65536];                     /* raw MIDI bytes the device sent (SysEx and channel messages) */
static uint32_t out_n, out_lost;
static void out_put(const uint8_t *p, uint32_t n)
{
    if (out_n + n > sizeof out_buf) { out_lost++; return; }
    memcpy(out_buf + out_n, p, n);
    out_n += n;
}
static void twin_drain(void)                       /* ep1_tx: SysEx first, then the MIDI ring */
{
    while (so_r != so_w) {
        uint32_t p = sx_out_q[so_r++ % SXQ], cin = p & 15u;
        uint8_t b[3] = {(uint8_t)(p >> 8), (uint8_t)(p >> 16), (uint8_t)(p >> 24)};
        out_put(b, cin == 4u || cin == 7u ? 3u : cin == 6u ? 2u : 1u);
    }
    while (!sx_busy && mo_r != mo_w) {
        uint32_t p = midi_out_q[mo_r++ % MQ], cin = p & 15u, st = (p >> 8) & 0xFFu;
        uint8_t b[3] = {(uint8_t)st, (uint8_t)(p >> 16), (uint8_t)(p >> 24)};
        uint32_t n = cin == 0xCu || cin == 0xDu ? 2u : cin >= 8u ? 3u : st >= 0xF8u || st == 0xF6u ? 1u :
                     st == 0xF1u || st == 0xF3u ? 2u : 3u;
        out_put(b, n);
    }
}
static void twin_service(void)                     /* main loop: ed_service, then ota_service's share */
{
    ed_service();
    ota_service();                                 /* M-UPGRADE handshake (identity); drops any other frame */
    usb.ota_req = 0;                               /* (the updater is not part of the twin) */
    usb.uboot_req = 0;
    twin_drain();
}
EXPORT(st_out) uint8_t *st_out(void) { return out_buf; }
EXPORT(st_out_len) uint32_t st_out_len(void) { return out_n; }
EXPORT(st_out_lost) uint32_t st_out_lost(void) { return out_lost; }
EXPORT(st_out_clear) void st_out_clear(void) { out_n = 0; }

static uint8_t in_buf[65536];
EXPORT(st_in) uint8_t *st_in(void) { return in_buf; }
/* n bytes of in_buf: complete MIDI messages from the USB host (SysEx and/or channel / realtime messages), sent as
 * USB-MIDI packets; the editor answers at once (main.c serves the editor in its busy loop). -> bytes in st_out */
EXPORT(st_midi_in) uint32_t st_midi_in(uint32_t n)
{
    uint32_t i = 0;
    if (n > sizeof in_buf) n = sizeof in_buf;
    while (i < n) {
        uint8_t b = in_buf[i];
        if (b == 0xF0u) {                          /* SysEx: CIN 4 (three bytes on), CIN 5..7 (the end, 1..3) */
            uint32_t e = i;
            while (e < n && in_buf[e] != 0xF7u) e++;
            uint32_t end = e < n ? e + 1u : n;      /* (an unterminated one goes as it is: aborted later) */
            while (i < end) {
                uint32_t k = end - i >= 3u ? 3u : end - i, pkt = 0, j;
                for (j = 0; j < k; j++) pkt |= (uint32_t)in_buf[i + j] << (8u * (j + 1u));
                i += k;
                pkt |= i == end && in_buf[end - 1u] == 0xF7u ? 4u + k : 4u;
                midi_in_event(pkt);
            }
            twin_service();                         /* one frame buffer: serve each frame as it completes */
            continue;
        }
        if (b >= 0x80u && b != 0xF7u) {
            uint32_t len = b >= 0xF8u || b == 0xF6u ? 1u : (b & 0xF0u) == 0xC0u || (b & 0xF0u) == 0xD0u ||
                           b == 0xF1u || b == 0xF3u ? 2u : 3u;
            uint32_t cin = b >= 0xF0u ? (len == 1u ? 0xFu : len == 2u ? 2u : 3u) : b >> 4;
            uint32_t pkt = cin | (uint32_t)b << 8;
            for (uint32_t k = 1; k < len && i + k < n; k++) pkt |= (uint32_t)(in_buf[i + k] & 0x7Fu) << (8u * (k + 1u));
            midi_in_event(pkt);
            i += len;
            continue;
        }
        i++;                                        /* stray data byte (no running status on USB) */
    }
    twin_service();
    return out_n;
}

/* ---- LEDs (melodee_web.c), screen ---- */
static uint32_t led_lit(uint32_t id)
{
    uint32_t c, r;
    for (c = 0; c < FM1_NCOL; c++)
        for (r = 1; r < 5u; r++)
            if (FM1_KEYMAP[r][c] == (int8_t)id)
                return (fm1_led[c] >> r) & 1u;
    return 0;
}
EXPORT(st_led_keys) uint32_t st_led_keys(void)
{
    uint32_t k, m = 0;
    for (k = 0; k < 27u; k++) m |= led_lit(14u + k) << k;
    return m;
}
EXPORT(st_led_buttons) uint32_t st_led_buttons(void)
{
    uint32_t b, m = 0;
    for (b = 0; b < 14u; b++) m |= led_lit(b) << b;
    return m;
}
EXPORT(st_fb) uint16_t *st_fb(void) { return host_screen; }   /* RGB565, the panel's byte order */
EXPORT(st_fb_gen) uint32_t st_fb_gen(void) { return fb_gen; }
EXPORT(st_now) uint32_t st_now(void) { return now_ms; }
EXPORT(st_version) const char *st_version(void) { return MELODEE_VERSION; }
EXPORT(st_fs) uint32_t st_fs(void) { return FS; }
/* bit 0 playing, 1 song chain running, 2 transport busy (playing, count-in or chain), 3 recording armed */
EXPORT(st_transport) uint32_t st_transport(void)
{
    return (song.playing ? 1u : 0u) | (chain.running ? 2u : 0u) | (transport_busy() ? 4u : 0u) | (song.rec ? 8u : 0u);
}
/* the matrix id of a panel label (panel.c B_*: B_PLAY 10, B_REC 11 …), the learned panel included */
EXPORT(st_panel_button) uint32_t st_panel_button(uint32_t label) { return label < NB ? panel.btn[label] : 0u; }
/* projects 1..32 (slot 0..31) as the PROJECT page loads them (ui_input.c: project_load); the editor protocol's
 * PROJECT command reaches slots 0..3 only. -> 0 loaded, 1 empty or invalid, 2 refused while the transport runs */
EXPORT(st_project_load) uint32_t twin_project_load(uint32_t slot)
{
    if (slot >= PROJ_SLOTS || !project_used(slot)) return 1u;
    if (transport_busy()) return 2u;
    project_load(slot);
    return 0u;
}
EXPORT(st_project_used) uint32_t twin_project_used(uint32_t slot) { return slot < PROJ_SLOTS ? (uint32_t)(project_used(slot) != 0) : 0u; }
EXPORT(st_project_current) uint32_t twin_project_current(void) { return proj_cur; }
/* track k's level 0..127 and mute (the mixer as TRACK_MIX shows it), for the host's checks */
EXPORT(st_track_mute) uint32_t st_track_mute(uint32_t k) { return k < NTRK ? (uint32_t)(trk[k].p[P_MUTE] != 0) : 0u; }

/* ------------------------------------------------------------ the clock and the sound --- */
#define RING 8192u
static float ring[RING * 2u], out_l[4096], out_r[4096];
static uint32_t ring_w, ring_r, owed;

static void main_frame(void)                       /* main.c's loop body, once per 15 ms */
{
    wire_release();
    pattern_poll();
    {
        uint32_t k10 = master_knob / 16u;           /* the MASTER pot (ADC), then MIDI master attenuation */
        midi_master_physical((k10 * k10) >> 8);
    }
    cz_service();
    fm6_service();
    twin_service();
    ui_input();
    glo_poll();
    settings_poll();
    ui_leds();
    ui_draw();
    if (memcmp(fb_seen, host_screen, sizeof fb_seen)) {
        memcpy(fb_seen, host_screen, sizeof fb_seen);
        fb_gen++;
    }
}

static void audio_half(void)                       /* audio.c fm1_audio_irq: one half buffer, CTL blocks */
{
    int32_t o[2 * HALF_FRAMES];
    uint32_t b, i;
    for (b = 0; b < HALF_FRAMES; b += CTL) {
        int32_t *out = o + 2u * b;
        mix_block(out, CTL);
        click_render(out, CTL);
        for (i = 0; i < CTL; i++)
            if (i & 1u) scope_buf[scope_w++ & (SCOPE_N - 1u)] = (int16_t)out[2u * i];
    }
    for (i = 0; i < HALF_FRAMES; i++) {
        uint32_t r = (ring_w++ % RING) * 2u;
        ring[r] = (float)o[2u * i] / 65536.0f;     /* Q15 << 7 into a 24-bit codec word: -6 dBFS ceiling */
        ring[r + 1u] = (float)o[2u * i + 1u] / 65536.0f;
    }
    twin_drain();                                  /* MIDI out of the MIDI tracks (USB poll) */
}

static void tick(void)
{
    in_tick = 1;
    now_ms++;
    fm1_ms++;
    host_ticks += 1000u;
    ui_input();
    twin_service();
    if (now_ms % 15u == 0u)
        main_frame();
    owed += FS;
    while (owed >= 1000u * HALF_FRAMES) {
        owed -= 1000u * HALF_FRAMES;
        audio_half();
    }
    in_tick = 0;
}

/* the firmware waits (ed_flash_stop, ota_wire_send, backup loops): time goes on, the audio ISR stops the
 * transport at its next block; the sound of these milliseconds goes to the ring as on the device */
static void twin_wait_ms(void)
{
    if (in_tick || !booted)
        return;
    in_tick = 1;
    now_ms++;
    fm1_ms++;
    host_ticks += 1000u;
    owed += FS;
    while (owed >= 1000u * HALF_FRAMES) {
        owed -= 1000u * HALF_FRAMES;
        if (ring_w - ring_r + HALF_FRAMES > RING)
            ring_r += HALF_FRAMES;                 /* nobody listened: the oldest sound goes */
        audio_half();
    }
    in_tick = 0;
}

EXPORT(st_out_l) float *st_out_l(void) { return out_l; }
EXPORT(st_out_r) float *st_out_r(void) { return out_r; }
EXPORT(st_render) void st_render(uint32_t n)         /* n frames (<= 4096) into out_l / out_r */
{
    uint32_t k;
    if (n > 4096u) n = 4096u;
    while (ring_w - ring_r < n)
        tick();
    for (k = 0; k < n; k++) {
        uint32_t i = (ring_r++ % RING) * 2u;
        out_l[k] = ring[i];
        out_r[k] = ring[i + 1u];
    }
}
/* run the clock ms milliseconds without taking the sound (it is dropped): settle after a boot or a command */
EXPORT(st_run_ms) void st_run_ms(uint32_t ms)
{
    while (ms--) {
        tick();
        if (ring_w - ring_r > RING / 2u)
            ring_r = ring_w;
    }
}
