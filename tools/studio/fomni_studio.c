/* SPDX-License-Identifier: GPL-3.0-only */
/* FoMni studio twin: the firmware of the FM-1 (FoMni-1 by Charles Vestal, unchanged sources) for the host, as WebAssembly.
 * It is FoMni's own browser emulator (web/emu/omni_web.c: host/omni_host.c, the whole app – UI, project, the instrument
 * – against a simulated FM-1, with the emulator's OM_WEB hooks; from X0X's), with what the emulator leaves out:
 *   - the NOR flash: storage.c (the device's A/B objects, plat.h's map) on a 1 MiB RAM image. The emulator keeps its
 *     objects in RAM (plat_store_*); every object it saves is written through storage.c to the image (st_save), and at
 *     power-on they are read from it (st_load)
 *   - the USB SysEx path in main_fm1.c's order: the screen reader (announce.c ann_frame), ota.c's identity handshake
 *     (FoMni has no editor and no backup over USB: other SysEx is dropped, as on the device)
 *   - USB MIDI out: what FoMni sends (the emulator's MIDI log line, taken as bytes; the clock it counts)
 * Built by FM-1 Companion's tools/build_sims.py (target fomni-studio) from FoMni's sources at a release tag; nothing of
 * the firmware is modified. Host stand-ins (documented in the NOTICE) are the emulator's: keys / LEDs / LCD, the
 * MASTER pot, USB = always configured; no USB audio, no TRS MIDI, no updater (ota.c answers the identity handshake, an
 * update request is dropped), no USB boot, no hardware calibration (the default panel map). The MIDI clock goes out at
 * the end of the millisecond it falls in (the emulator counts its clock bytes, it logs the rest).
 * The twin ABI (st_*) is what fm1companion/sims/_studio/twin-core.js expects, with the optional st_transport_set (the
 * rhythm: om_play) and st_track_mute_set (the stems: harp voice 1, harp voice 2, chord and bass, rhythm – their levels
 * held at 0 in the engine, the project keeps its own). FoMni has one project: st_project_* answer "none". */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <time.h>
/* (WASI: no clock to measure the render by; the emulator's CPU figures stay 0) */
#define clock_gettime(c, t) ((t)->tv_sec = 0, (t)->tv_nsec = 0, 0)
/* the emulator logs each MIDI message it sends to a file ("%u ms: %02X %02X %02X"): the twin takes it as bytes */
static int tw_fprintf(FILE *f, const char *fmt, ...);
#define fprintf tw_fprintf

#define OM_WEB 1                         /* the browser build's hooks: web_audio, web_said */
static void web_audio(const int32_t *blk, uint32_t n);
#include "../host/omni_host.c"
#undef fprintf
#undef __attribute__                     /* (omni_host.c drops attributes for the firmware files) */
#define EXPORT(name) __attribute__((used, export_name(#name)))

#ifndef FELUCCA_ID
#define FELUCCA_ID "FM-1_8000401"        /* build_sims.py passes the release's identity (tools/build.py --release) */
#endif

/* ------------------------------------------------------------ the NOR flash: storage.c --- */
#define TW_FLASH 0x100000u
static uint8_t nor[TW_FLASH];
static uint8_t flash_ok = 1;             /* a known flash part */
static int st_read(uint32_t off, void *dst, uint32_t n)
{
    if (off > TW_FLASH || n > TW_FLASH - off)
        return -1;
    memcpy(dst, nor + off, n);
    return 0;
}
static int st_erase(uint32_t off)
{
    if (off & 0xFFFu || off >= TW_FLASH)
        return -8;
    memset(nor + off, 0xFF, 0x1000u);
    return 0;
}
static int st_prog(uint32_t off, const void *src, uint32_t n)   /* NOR: a program only clears bits */
{
    const uint8_t *s = src;
    uint32_t i;
    if (off > TW_FLASH || n > TW_FLASH - off)
        return -8;
    for (i = 0; i < n; i++)
        nor[off + i] &= s[i];
    return 0;
}
#include "../firmware/src/storage.c"

/* the emulator's objects <-> storage.c: what it saves is written to the flash, power-on reads it back */
static uint8_t tw_shadow[OBJ_NOBJ][PLAT_STORE_MAX];
static int tw_shadow_len[OBJ_NOBJ];
static uint32_t tw_writes_seen;
static void tw_from_flash(void)          /* power-on: the flash is what counts */
{
    uint32_t o;
    for (o = 0; o < OBJ_NOBJ; o++) {
        store_len[o] = st_load(o, store[o], PLAT_STORE_MAX);
        tw_shadow_len[o] = store_len[o];
        if (store_len[o] > 0)
            memcpy(tw_shadow[o], store[o], (size_t)store_len[o]);
    }
    tw_writes_seen = store_writes;
}
static void tw_to_flash(void)            /* each object the app saved since: st_save, as plat_fm1.c's plat_store_save */
{
    uint32_t o;
    if (store_writes == tw_writes_seen)
        return;
    tw_writes_seen = store_writes;
    for (o = 0; o < OBJ_NOBJ; o++) {
        if (store_len[o] < 0 || (store_len[o] == tw_shadow_len[o] &&
                                 !memcmp(store[o], tw_shadow[o], (size_t)store_len[o])))
            continue;
        if (st_save(o, store[o], (uint32_t)store_len[o]) == 0) {
            memcpy(tw_shadow[o], store[o], (size_t)store_len[o]);
            tw_shadow_len[o] = store_len[o];
        }
    }
}

/* ------------------------------------------------------------ USB: SysEx and MIDI out --- */
static uint8_t out_buf[131072];          /* raw MIDI bytes the device sent (SysEx and channel messages) */
static uint32_t out_n, out_lost;
static void out_put(const uint8_t *p, uint32_t n)
{
    if (out_n + n > sizeof out_buf) {
        out_lost++;
        return;
    }
    memcpy(out_buf + out_n, p, n);
    out_n += n;
}
static void *const tw_midi_log = (void *)&out_n;   /* (the emulator's MIDI log "file": any pointer but 0) */
static int tw_fprintf(FILE *f, const char *fmt, ...)
{
    va_list ap;
    unsigned ms, st, d1, d2;
    uint8_t b[3];
    if ((void *)f != tw_midi_log || strcmp(fmt, "%u ms: %02X %02X %02X\n"))
        return 0;
    va_start(ap, fmt);
    ms = va_arg(ap, unsigned);
    st = va_arg(ap, unsigned);
    d1 = va_arg(ap, unsigned);
    d2 = va_arg(ap, unsigned);
    va_end(ap);
    (void)ms;
    b[0] = (uint8_t)st;
    b[1] = (uint8_t)d1;
    b[2] = (uint8_t)d2;
    out_put(b, st >= 0xF8u || st == 0xF6u ? 1u : (st & 0xF0u) == 0xC0u || (st & 0xF0u) == 0xD0u ||
                                                     st == 0xF1u || st == 0xF3u ? 2u : 3u);
    return 0;
}
static uint32_t tw_clocks_seen;
static void tw_clock_out(void)           /* the clock bytes the emulator counted (it logs everything else) */
{
    static const uint8_t F8 = 0xF8u;
    while (tw_clocks_seen != clock_out_count) {
        tw_clocks_seen++;
        out_put(&F8, 1);
    }
}
static void web_said(const uint8_t *p, uint32_t n) { out_put(p, n); }   /* the screen reader's SysEx to the host */

/* the SysEx frame received (usb.c: one buffer, the 7-bit bytes between F0 and F7) */
static uint8_t fr_buf[1024];
static uint32_t fr_n, fr_pending;
static int ota_frame_get(const uint8_t **p, uint32_t *n)
{
    if (!fr_pending)
        return 0;
    *p = fr_buf;
    *n = fr_n;
    return 1;
}
static void ota_frame_done(void) { fr_pending = 0; }
static int ota_wire_send(const uint8_t *p, uint32_t n)   /* one F0..F7 message to the host */
{
    out_put(p, n);
    return 0;
}

/* ota.c's hooks: no updater in the twin */
static uint32_t ota_now_ms(void) { return now_ms; }
static void tw_isr_ms(void);
static void ota_idle(void) { tw_isr_ms(); }
static int ota_erase(uint32_t off) { (void)off; return -8; }
static int ota_prog(uint32_t off, const void *p, uint32_t n) { (void)off; (void)p; (void)n; return -8; }
static int ota_fread(uint32_t off, void *p, uint32_t n) { return st_read(off, p, n); }
static void ota_show(uint32_t step, int32_t code) { (void)step; (void)code; }
static void ota_commit(const uint8_t *parm) { (void)parm; }
#include "../firmware/src/ota.c"

/* ------------------------------------------------------------ the clock --- */
#define TW_RING 16384u
static float tw_ring[TW_RING * 2u], tw_out_l[4096], tw_out_r[4096];
static uint32_t tw_w, tw_r, tw_booted, tw_mutes, tw_mute_on;
static void web_audio(const int32_t *blk, uint32_t n)   /* the emulator's output, 24 bits = 0 dBFS */
{
    uint32_t k;
    if (tw_w - tw_r + n > TW_RING)
        tw_r = tw_w + n - TW_RING;       /* nobody listened: the oldest sound goes */
    for (k = 0; k < n; k++) {
        uint32_t i = (tw_w++ % TW_RING) * 2u;
        tw_ring[i] = (float)blk[2 * k] / 8388608.0f;
        tw_ring[i + 1u] = (float)blk[2 * k + 1] / 8388608.0f;
    }
}

static const uint8_t TW_LEVEL[4] = {P_VOICE1, P_VOICE2, P_CHORD, P_RHYVOL};   /* the stems' levels (omni.h) */
static int16_t tw_level[4] = {-1, -1, -1, -1};
static void tw_mute_apply(void)          /* a part held silent: its level 0 to the engine (the project keeps its own) */
{
    uint32_t k;
    if (!tw_mute_on)
        return;
    for (k = 0; k < 4u; k++) {
        int want = (tw_mutes >> k) & 1u ? 0 : proj.par[TW_LEVEL[k]];
        if (tw_level[k] != want) {
            om_set(TW_LEVEL[k], want);
            tw_level[k] = (int16_t)want;
        }
    }
}

/* the audio ISR alone (the main loop waits for its host): the device's time and its sound */
static void tw_isr_ms(void)
{
    static int32_t blk[512];
    now_ms++;
    while (audio_due_ms <= (double)now_ms) {
        tw_mute_apply();
        om_render(blk, 256, master);
        web_audio(blk, 256);
        audio_due_ms += 256.0 * 1000.0 / 44100.0;
    }
    tw_clock_out();
}

static void twin_service(void)           /* main_fm1.c's main loop: the SysEx received, in its order */
{
    const uint8_t *p;
    uint32_t n;
    if (ota_frame_get(&p, &n) && ann_frame(p, n, now_ms))
        ota_frame_done();
    ota_service();                       /* M-UPGRADE handshake (other SysEx is dropped) */
    fr_pending = 0;
}

static void twin_ms(void)                /* one millisecond of the device */
{
    tw_mute_apply();
    run_ms(1);                           /* the emulator's: the audio blocks due, the UI frame or the input scan */
    tw_clock_out();
    twin_service();
    tw_to_flash();
}

/* ------------------------------------------------------------ power-on and the flash --- */
EXPORT(st_flash) uint8_t *st_flash(void) { return nor; }        /* the whole NOR image (write it before st_boot) */
EXPORT(st_flash_size) uint32_t st_flash_size(void) { return TW_FLASH; }
EXPORT(st_flash_erase) void st_flash_erase(void) { memset(nor, 0xFF, sizeof nor); }   /* a new device */

/* main_fm1.c's power-on: the objects read from the flash, then the emulator's boot (omni_host.c boot: the
 * instrument, the project, the UI), and the splash's 330 ms run, so the twin answers
 * its panel at once as the other twins do; 1 = flash found */
EXPORT(st_boot) uint32_t st_boot(uint32_t master_v)
{
    uint32_t i, k10;
    if (tw_booted)
        return flash_ok;
    midi_log = (FILE *)tw_midi_log;
    k10 = master_v > 1023u ? 1023u : master_v;
    master = (k10 * k10) >> 8;           /* main_fm1.c: the knob, squared, Q12 */
    tw_from_flash();
    boot();
    tw_booted = 1;
    for (i = 0; i < 330u; i++)
        twin_ms();
    tw_r = tw_w;
    out_n = 0;
    tw_clocks_seen = clock_out_count;
    return flash_ok;
}

/* ---------------------------------------------------------------------- the panel --- */
/* the matrix ids (Felucca's panel: OCT- 0, OCT+ 1, FX 2 .. REC 13; encoders SELECT 0, ALGORITHM 1, KNOB 1..4 2..5,
 * PRESETS 6) <-> FoMni's labels and roles (plat.h B_*, EN_*), by panel.c's PANEL_DEFAULT */
static const uint8_t TW_BTN[NB] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 0, 1};
static const uint8_t TW_ENC[NE] = {0, 1, 6, 2, 3, 4, 5};
EXPORT(st_keys) void st_keys(uint32_t m) { held_keys = m & ((1u << NKEYS) - 1u); }   /* bit n = key n (0 = F3) */
EXPORT(st_buttons) void st_buttons(uint32_t m)
{
    uint32_t i, b = 0;
    for (i = 0; i < NB; i++)
        if ((m >> TW_BTN[i]) & 1u)
            b |= 1u << i;
    held_btn = b;
}
EXPORT(st_encoder) void st_encoder(uint32_t e, int32_t n)   /* + = CW */
{
    uint32_t r;
    for (r = 0; r < NE; r++)
        if (TW_ENC[r] == e)
            enc_acc[r] += n;
}
EXPORT(st_master) void st_master(uint32_t v)        /* 0..1023 */
{
    uint32_t k10 = v > 1023u ? 1023u : v;
    master = (k10 * k10) >> 8;
}
/* the matrix id of a panel label (TwinCore.B: FX 0 .. REC 11, as plat.h's B_*) */
EXPORT(st_panel_button) uint32_t st_panel_button(uint32_t label) { return label < NB ? TW_BTN[label] : 0u; }

/* ---- USB MIDI: SysEx frames to the main loop, channel messages as USB-MIDI packets (engine.c reads them) ---- */
EXPORT(st_out) uint8_t *st_out(void) { return out_buf; }
EXPORT(st_out_len) uint32_t st_out_len(void) { return out_n; }
EXPORT(st_out_lost) uint32_t st_out_lost(void) { return out_lost; }
EXPORT(st_out_clear) void st_out_clear(void) { out_n = 0; }

static uint8_t in_buf[65536];
EXPORT(st_in) uint8_t *st_in(void) { return in_buf; }
/* n bytes of in_buf: complete MIDI messages from the USB host (SysEx and/or channel / realtime messages); a SysEx
 * frame is served at once (the main loop's order). -> bytes in st_out */
EXPORT(st_midi_in) uint32_t st_midi_in(uint32_t n)
{
    uint32_t i = 0;
    if (n > sizeof in_buf)
        n = sizeof in_buf;
    while (i < n) {
        uint8_t b = in_buf[i];
        if (b == 0xF0u) {
            uint32_t e = i + 1u;
            while (e < n && in_buf[e] != 0xF7u)
                e++;
            if (e < n && e - i - 1u <= sizeof fr_buf) {
                memcpy(fr_buf, in_buf + i + 1, e - i - 1u);
                fr_n = e - i - 1u;
                fr_pending = 1;
                if (tw_booted)
                    twin_service();
                fr_pending = 0;
            }
            i = e + 1u;
            continue;
        }
        if (b >= 0x80u && b != 0xF7u) {
            uint32_t len = b >= 0xF8u || b == 0xF6u ? 1u : (b & 0xF0u) == 0xC0u || (b & 0xF0u) == 0xD0u ||
                           b == 0xF1u || b == 0xF3u ? 2u : 3u;
            uint32_t cin = b >= 0xF0u ? 0x0Fu : b >> 4;   /* (as the emulator's "midi" command) */
            uint32_t pkt = cin | (uint32_t)b << 8, k;
            for (k = 1; k < len && i + k < n; k++)
                pkt |= (uint32_t)(in_buf[i + k] & 0x7Fu) << (8u * (k + 1u));
            while (tw_booted && mi_w - mi_r >= MQ - 8u)   /* (the ring full: the host waits) */
                twin_ms();
            min_q[mi_w++ % MQ] = pkt;
            i += len;
            continue;
        }
        i++;
    }
    return out_n;
}

/* ------------------------------------------------------------ LEDs, the screen --- */
EXPORT(st_led_keys) uint32_t st_led_keys(void) { return lit_keys; }
EXPORT(st_led_buttons) uint32_t st_led_buttons(void)   /* by matrix id */
{
    uint32_t i, m = 0;
    for (i = 0; i < NB; i++)
        if ((lit_btn >> i) & 1u)
            m |= 1u << TW_BTN[i];
    return m;
}
static uint16_t fb_seen[240 * 240];
static uint32_t fb_gen;
EXPORT(st_fb) uint16_t *st_fb(void) { return fb; }   /* RGB565, the panel's byte order */
EXPORT(st_fb_gen) uint32_t st_fb_gen(void)
{
    if (memcmp(fb_seen, fb, sizeof fb_seen)) {
        memcpy(fb_seen, fb, sizeof fb_seen);
        fb_gen++;
    }
    return fb_gen;
}
EXPORT(st_now) uint32_t st_now(void) { return now_ms; }
EXPORT(st_version) const char *st_version(void) { return "FoMni-1 " OM_VERSION; }
EXPORT(st_fs) uint32_t st_fs(void) { return 44100u; }

/* ------------------------------------------------------------ the transport --- */
/* bit 0 playing, 2 busy: the rhythm runs (om_playing) */
EXPORT(st_transport) uint32_t st_transport(void) { return om_playing ? 5u : 0u; }
/* (optional export) 0 stop, 1 and 2 the rhythm from its first step (FoMni has no song), 3 restart. The audio side
 * takes it at its next block. -> st_transport() */
EXPORT(st_transport_set) uint32_t st_transport_set(uint32_t how)
{
    uint32_t i;
    if (how == 0u || how == 3u)
        om_play(0);
    if (how)
        om_play(1);
    for (i = 0; i < 20u; i++)
        tw_isr_ms();
    return st_transport();
}
/* (optional export) part k (0 harp voice 1, 1 harp voice 2, 2 chord and bass, 3 rhythm) held silent, until set again */
EXPORT(st_track_mute_set) void st_track_mute_set(uint32_t k, uint32_t on)
{
    if (k >= 4u)
        return;
    tw_mutes = on ? tw_mutes | 1u << k : tw_mutes & ~(1u << k);
    tw_mute_on = 1;
    tw_mute_apply();
}
EXPORT(st_track_mute) uint32_t st_track_mute(uint32_t k) { return (tw_mutes >> k) & 1u; }
/* one project in FoMni: 1 = empty */
EXPORT(st_project_load) uint32_t twin_project_load(uint32_t slot) { (void)slot; return 1u; }
EXPORT(st_project_used) uint32_t twin_project_used(uint32_t slot) { (void)slot; return 0u; }
EXPORT(st_project_current) uint32_t twin_project_current(void) { return 255u; }

/* ------------------------------------------------------------ render --- */
EXPORT(st_out_l) float *st_out_l(void) { return tw_out_l; }
EXPORT(st_out_r) float *st_out_r(void) { return tw_out_r; }
EXPORT(st_render) void st_render(uint32_t n)         /* n frames (<= 4096) into st_out_l / st_out_r */
{
    uint32_t k;
    if (n > 4096u)
        n = 4096u;
    while (tw_w - tw_r < n)
        twin_ms();
    for (k = 0; k < n; k++) {
        uint32_t i = (tw_r++ % TW_RING) * 2u;
        tw_out_l[k] = tw_ring[i];
        tw_out_r[k] = tw_ring[i + 1u];
    }
}
/* run the clock ms milliseconds without taking the sound (it is dropped): settle after a boot or a command */
EXPORT(st_run_ms) void st_run_ms(uint32_t ms)
{
    while (ms--) {
        twin_ms();
        if (tw_w - tw_r > TW_RING / 2u)
            tw_r = tw_w;
    }
}
