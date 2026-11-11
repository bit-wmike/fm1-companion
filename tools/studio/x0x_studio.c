/* SPDX-License-Identifier: GPL-3.0-only */
/* X0X studio twin: the firmware of the FM-1 (X0X by Charles Vestal, unchanged sources) for the host, as WebAssembly.
 * It is X0X's own browser emulator (web/emu/x0x_web.c: host/x0x_host.c, the whole app – UI, project, sequencer, every
 * engine – against a simulated FM-1, with the emulator's X0X_WEB hooks), with what the emulator leaves out:
 *   - the NOR flash: storage.c (the device's A/B objects, flash map plat.h / storage.c) on a 1 MiB RAM image. The
 *     emulator keeps its objects in RAM (plat_store_*); every object it saves is written through storage.c to the
 *     image (st_save), and at power-on the objects are read from it (st_load, st_load_prev), the sample slots read in
 *     place from it (slots.c smp_user_scan, the emulator's "slotimg" path: the device's format)
 *   - the USB SysEx path: one SysEx frame buffer (usb.c's ota_frame_get / ota_frame_done), replies through
 *     ota_wire_send; main_fm1.c's main loop order: the screen reader (announce.c ann_frame), the editor (app/editor.c:
 *     INFO, PING, the sample slots SMP_BEGIN .. SMP_READ), ota.c's identity handshake and backup.c's backup and
 *     restore (main_fm1.c backup_service: cmd 0x42 streamed by bu_step, cmd 0x41 pulled by restore_rx)
 *   - USB MIDI out: what X0X sends (the emulator's MIDI log line, taken as bytes; the clock it counts)
 * A restore pulls the backup from the host with read requests and waits for each answer (ota.c ota_read) and then
 * for SEL on the panel (main_fm1.c rs_ask). A twin cannot wait for its host inside a call, so the host puts the
 * backup on the twin's side of the cable first (frames of cmd 0x7E, below: the twin's own, never seen by the
 * firmware); the read requests are then answered from it at once, and the question is answered yes (the suite
 * asked the user before). The firmware checks, stages and commits it exactly as on the device (backup.c), the
 * report reads (RS_OK_ADDR, RS_FAIL_ADDR | code) go to the host as on the device.
 * Built by FM-1 Companion's tools/build_sims.py (target x0x-studio) from X0X's sources at a release tag; nothing of
 * the firmware is modified. Host stand-ins (documented in the NOTICE) are the emulator's: keys / LEDs / LCD, the
 * MASTER pot, one core (plat_cpu2_* run the job at once, as the emulator does), no overload guard (a browser's
 * timing is not the FM-1's: the emulator reports no load), USB = always configured; no USB audio, no TRS MIDI, no
 * updater (ota.c answers the identity handshake, an update request is dropped), no USB boot, no hardware calibration
 * (the default panel map). The MIDI clock goes out at the end of the millisecond it falls in (the emulator counts its
 * clock bytes, it logs the rest).
 * The twin ABI (st_*) is what fm1companion/sims/_studio/twin-core.js expects, with the optional st_transport_set and
 * st_track_mute_set (the stems: X0X has no editor TRACK_MIX). X0X has one project (its sound, 32 patterns, the song,
 * the motion): st_project_* answer "none". */
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

#define X0X_WEB 1                        /* the browser build's hooks: web_audio, web_said; no load measured */
static void web_audio(const int32_t *blk, uint32_t n);
#include "../host/x0x_host.c"
#undef fprintf
#undef __attribute__                     /* (x0x_host.c drops attributes for the firmware files) */
#define EXPORT(name) __attribute__((used, export_name(#name)))

#ifndef FELUCCA_ID
#define FELUCCA_ID "FM-1_9010201"        /* build_sims.py passes the release's identity (tools/build.py --release) */
#endif

/* ------------------------------------------------------------ the NOR flash: storage.c --- */
#define TW_FLASH 0x100000u
static uint8_t nor[TW_FLASH];
static uint8_t flash_ok = 1;             /* a known flash part (editor.c, backup_service) */
static int st_read(uint32_t off, void *dst, uint32_t n)
{
    if (off > TW_FLASH || n > TW_FLASH - off)
        return -1;
    memcpy(dst, nor + off, n);
    return 0;
}
static void tw_slot_sync(uint32_t off, uint32_t n);
static int st_erase(uint32_t off)
{
    if (off & 0xFFFu || off >= TW_FLASH)
        return -8;
    memset(nor + off, 0xFF, 0x1000u);
    tw_slot_sync(off, 0x1000u);
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
    tw_slot_sync(off, n);
    return 0;
}
#define st_crc32 st_crc32_storage        /* (the emulator has its own copy of the same CRC-32) */
#include "../firmware/src/storage.c"
#undef st_crc32

/* the HAL calls of editor.c's sample-slot commands, on the image */
static void fm1_irq_off(void) {}
static void fm1_irq_on(void) {}
static void fm1_wdt_feed(void) {}
static void audio_silence(void) {}
static void fl_inval(uint32_t off, uint32_t n) { (void)off; (void)n; }
static int fl_erase4k(uint32_t off, uint32_t *took) { *took = 0; return st_erase(off); }
static int fl_write(uint32_t off, const void *p, uint32_t n) { return st_prog(off, p, n); }

/* the sample slots are read in place: the emulator's slot images (slotimg) hold what the flash holds */
static void tw_slot_sync(uint32_t off, uint32_t n)
{
    uint32_t k;
    for (k = 0; k < SMP_USER_SLOTS; k++) {
        uint32_t b = SMP_USER_BASE + k * SMP_USER_SIZE, lo = off > b ? off : b,
                 hi = off + n < b + SMP_USER_SIZE ? off + n : b + SMP_USER_SIZE;
        if (lo < hi)
            memcpy(slot_img[k] + (lo - b), nor + lo, hi - lo);
    }
}

/* the emulator's objects <-> storage.c: what it saves is written to the flash, power-on reads it back */
static uint8_t tw_shadow[OBJ_NOBJ][PLAT_STORE_MAX];
static int tw_shadow_len[OBJ_NOBJ];
static uint32_t tw_writes_seen;
static void tw_from_flash(void)          /* power-on, after a restore: the flash is what counts */
{
    uint32_t o, k;
    for (o = 0; o < OBJ_NOBJ; o++) {
        store_len[o] = st_load(o, store[o], PLAT_STORE_MAX);
        store_prev_len[o] = st_load_prev(o, store_prev[o], PLAT_STORE_MAX);
        tw_shadow_len[o] = store_len[o];
        if (store_len[o] > 0)
            memcpy(tw_shadow[o], store[o], (size_t)store_len[o]);
    }
    for (k = 0; k < SMP_USER_SLOTS; k++) {
        memcpy(slot_img[k], nor + SMP_USER_BASE + k * SMP_USER_SIZE, SMP_USER_SIZE);
        slot_is_img[k] = 1;              /* (the device's path: what the flash holds is what plays) */
        smp_user_scan(k);
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
static void tw_host_reply(const uint8_t *p, uint32_t n);
static int ota_wire_send(const uint8_t *p, uint32_t n)   /* one F0..F7 message to the host */
{
    tw_host_reply(p, n);
    return 0;
}

/* ota.c's hooks: no updater in the twin; the journal record of a restore (RS_REC, in the update's staging area) */
#define RS_REC_SECTOR 0x000E1000u        /* (backup.c RS_REC: the journal record's sector) */
static uint32_t ota_now_ms(void) { return now_ms; }
static void tw_isr_ms(void);
static void ota_idle(void) { tw_isr_ms(); }    /* waiting for the host: the device's time and its audio go on */
static int ota_erase(uint32_t off) { return off == RS_REC_SECTOR ? st_erase(off) : -8; }
static int ota_prog(uint32_t off, const void *p, uint32_t n)
{
    return off >= RS_REC_SECTOR && off + n <= RS_REC_SECTOR + 0x1000u ? st_prog(off, p, n) : -8;
}
static int ota_fread(uint32_t off, void *p, uint32_t n) { return st_read(off, p, n); }
static void ota_show(uint32_t step, int32_t code) { (void)step; (void)code; }
static void ota_commit(const uint8_t *parm) { (void)parm; }
#include "../firmware/src/ota.c"

#include "../firmware/src/app/editor.c"

/* backup.c's hooks (main_fm1.c's, on the twin's USB) */
static uint8_t tw_stage[24576];          /* the host's backup on the twin's side of the cable (cmd 0x7E) */
static uint32_t tw_stage_len, tw_staged;
static int rs_fetch(uint32_t addr, uint8_t *dst, uint32_t n) { return ota_read(addr, dst, n); }
static int rs_note(uint32_t addr)
{
    uint8_t b[8];
    ota_deadline = 0;
    return ota_read(addr, b, 8);
}
static int rs_playing(void) { return seq.playing || seq.req_stop; }
static void rs_begin(void)
{
    ota_try_ms = 5000u;
    ota_deadline = ota_now_ms() + 120000u;
}
static void rs_show(uint32_t pct)        /* main_fm1.c's progress */
{
    char b[8];
    uint32_t i = 0;
    if (pct >= 100u)
        b[i++] = '1';
    if (pct >= 10u)
        b[i++] = (char)('0' + pct / 10u % 10u);
    b[i++] = (char)('0' + pct % 10u);
    b[i++] = '%';
    b[i] = 0;
    if (!pct)
        lcd_fill(0, 0, 240, 240, C_BLACK);
    draw_text_box(0, 92, 240, &FONT_B, "RESTORE FROM USB", C_WHITE, 1);
    draw_text_box(0, 124, 240, &FONT_S, b, C_HI, 1);
}
/* main_fm1.c's question, SEL yes / HOME no: the suite asked its user before it put the backup on the cable, so the
 * twin answers yes; a restore without it never gets here (its reads go unanswered) */
static int rs_ask(void)
{
    lcd_fill(0, 0, 240, 240, C_BLACK);
    draw_text_box(0, 64, 240, &FONT_B, "RESTORE FROM USB?", C_WHITE, 1);
    draw_text_box(0, 92, 240, &FONT_S, "REPLACES THE PROJECT:", C_GRAY, 1);
    draw_text_box(0, 112, 240, &FONT_S, "SOUND, PATTERNS, SONG", C_GRAY, 1);
    draw_text_box(0, 150, 240, &FONT_B, "SEL  YES      HOME  NO", C_HI, 1);
    tw_isr_ms();
    return tw_staged && !rs_playing();
}
static void rs_reload(void)              /* the restored objects from the flash, then the app as at power-on */
{
    tw_from_flash();
    app_restored();
}
static int rs_rec_erase(void) { return ota_erase(RS_REC_SECTOR); }
static int rs_rec_prog(uint32_t off, const void *src, uint32_t n) { return ota_prog(RS_REC_SECTOR + off, src, n); }
static int rs_rec_read(uint32_t off, void *dst, uint32_t n) { return ota_fread(RS_REC_SECTOR + off, dst, n); }
static int bu_room(void) { return out_n + 512u <= sizeof out_buf; }   /* the host takes it all */
static int bu_send(const uint8_t *p, uint32_t n) { return ota_wire_send(p, n); }
static void bu_flush(void)
{
    ui_flush();
    tw_to_flash();                       /* (the app's save, on the flash before the backup reads it) */
}
static int bu_unsaved(void) { return ui_dirty(); }
static void bu_hold(int on) { ui_hold_saves(on); }
#include "../firmware/src/backup.c"

/* the host side of the cable for a restore: read requests (0x30) answered from the staged backup at once; the
 * report reads (RS_OK_ADDR, RS_ASK_ADDR, RS_FAIL_ADDR | code) also go to the host, as on the device */
static uint8_t hr_dec[64], hr_msg[1024], hr_wire[1200];
static void tw_host_reply(const uint8_t *p, uint32_t n)
{
    uint32_t d, addr, len, i, s = 0, w;
    if (!tw_staged || n < 3u || p[0] != 0xF0u || p[n - 1u] != 0xF7u) {
        out_put(p, n);
        return;
    }
    d = ota_unpack7(p + 1, n - 2u, hr_dec, sizeof hr_dec);
    if (d != 15u || hr_dec[0] != 0 || hr_dec[1] != 0x59u || hr_dec[2] != 0x30u) {
        out_put(p, n);
        return;
    }
    addr = ota_rd32(hr_dec + 7);
    len = ota_rd16(hr_dec + 11);
    if (addr >= RS_OK_ADDR)
        out_put(p, n);                   /* (the device's report: the host sees it) */
    if (len > 512u)
        return;
    hr_msg[0] = 0;
    hr_msg[1] = 0x59;
    hr_msg[2] = 0x30;
    hr_msg[3] = (uint8_t)(len + 8u);
    hr_msg[4] = (uint8_t)((len + 8u) >> 8);
    hr_msg[5] = 0;
    hr_msg[6] = 0;
    ota_wr32(hr_msg + 7, addr);
    hr_msg[11] = (uint8_t)len;
    hr_msg[12] = (uint8_t)(len >> 8);
    hr_msg[13] = 0;
    for (i = 0; i < len; i++)
        hr_msg[14 + i] = addr < tw_stage_len && i < tw_stage_len - addr ? tw_stage[addr + i] : 0;
    for (i = 6; i < 14u + len; i++)
        s += hr_msg[i];
    hr_msg[14 + len] = (uint8_t)~s;
    w = ota_pack7(hr_msg, 15u + len, hr_wire);
    if (w <= sizeof fr_buf) {            /* (the answer is in the receive buffer when ota_read looks) */
        memcpy(fr_buf, hr_wire, w);
        fr_n = w;
        fr_pending = 1;
    }
}

/* main_fm1.c's backup_service: the host's backup and restore requests (ota.c's framing) */
static void backup_service(uint32_t req)
{
    if (req == 11u && ota_dec[2] == BU_REQ && bu_rd32(ota_dec + 6) == BU_MAGIC && flash_ok)
        bu_start();
    bu_step();
    if (req == 15u && ota_dec[2] == RS_REQ && flash_ok) {
        uint32_t len = ota_rd32(ota_dec + 6), crc = ota_rd32(ota_dec + 10);
        int begun = 0, rc;
        if (bu.on)
            bu_stop();
        ota_try_ms = 300u;
        ota_deadline = ota_now_ms() + 3000u;
        rc = restore_rx(len, crc, &begun);
        ota_try_ms = 5000u;
        ota_deadline = 0;
        if (begun && rc)
            ui_redraw();
        (rc ? ui_warn : ui_say)(!rc ? "RESTORED FROM USB" : rc == RS_E_PLAYING ? "STOP FIRST, THEN RESTORE"
                                : rc == RS_E_NO ? "NOT RESTORED" : "RESTORE FAILED", 0);
        tw_staged = 0;                   /* (used once) */
        tw_stage_len = 0;
        tw_to_flash();
        tw_writes_seen = store_writes;
    }
}

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

static void tw_mute_apply(void)          /* st_track_mute_set: the parts held silent (stems) */
{
    uint32_t p;
    if (!tw_mute_on)
        return;
    for (p = 0; p < NTRACKS; p++)
        if ((tw_mutes >> p) & 1u)
            seq.mute |= seq_part_mask((int)p);
}

/* the audio ISR alone (the main loop waits: a restore's reads, its question): the device's time and its sound */
static void tw_isr_ms(void)
{
    static int32_t blk[512];
    now_ms++;
    while (audio_due_ms <= (double)now_ms) {
        tw_mute_apply();
        engine_render(blk, 256);
        engine_volume(blk, 256);
        web_audio(blk, 256);
        engine_load(0u, 256);
        audio_due_ms += 256.0 * 1000.0 / 44100.0;
    }
    if (!midi_rate)
        host_moq = 0;
    tw_clock_out();
}

static void twin_service(void)           /* main_fm1.c's main loop: the SysEx received, in its order */
{
    const uint8_t *p;
    uint32_t n, i;
    if (ota_frame_get(&p, &n) && ann_frame(p, n, now_ms))
        ota_frame_done();
    ed_service();
    backup_service(ota_service());
    for (i = 0; bu.on && i < 400u; i++) {   /* (a backup streams one frame a pass; the host takes them at once) */
        bu_step();
        tw_isr_ms();
    }
    fr_pending = 0;                      /* (a frame nobody took: dropped) */
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

/* main_fm1.c's power-on: a restore's commit cut short finished (rs_boot), the objects and the slots read, then the
 * emulator's boot (x0x_host.c boot: the project, its sound, the UI), and the splash's 330 ms run, so the twin answers
 * its panel at once as the other twins do; 1 = flash found */
EXPORT(st_boot) uint32_t st_boot(uint32_t master_v)
{
    uint32_t i, k10;
    if (tw_booted)
        return flash_ok;
    midi_log = (FILE *)tw_midi_log;
    cc_last_clear();
    k10 = master_v > 1023u ? 1023u : master_v;
    master = (k10 * k10) >> 8;           /* main_fm1.c: the knob, squared, Q12 */
    rs_boot();
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
 * PRESETS 6) <-> X0X's labels and roles (plat.h B_*, EN_*), by panel.c's PANEL_DEFAULT */
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

/* the twin's own frame (cmd 0x7E, ota.c's framing: F0 pack7(00 59 7E len24 body ~sum) F7, body u32 offset + bytes,
 * offset 0 starts over): the host's backup for the next restore, put on the twin's side of the cable. -> 1 taken */
static uint8_t st_dec[1100];
static int tw_stage_frame(const uint8_t *p, uint32_t n)
{
    uint32_t d = ota_unpack7(p, n, st_dec, sizeof st_dec), i, s = 0, off, k;
    if (d < 11u || d > sizeof st_dec || st_dec[0] != 0 || st_dec[1] != 0x59u || st_dec[2] != 0x7Eu ||
        ota_rd24(st_dec + 3) + 7u != d)
        return 0;
    for (i = 6; i + 1u < d; i++)
        s += st_dec[i];
    if ((uint8_t)~s != st_dec[d - 1u])
        return 1;
    off = ota_rd32(st_dec + 6);
    k = d - 11u;
    if (!off)
        tw_stage_len = 0;
    if (off + k > sizeof tw_stage)
        return 1;
    memcpy(tw_stage + off, st_dec + 10, k);
    if (off + k > tw_stage_len)
        tw_stage_len = off + k;
    tw_staged = 1;
    return 1;
}

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
            if (e < n && e - i - 1u <= sizeof fr_buf && !tw_stage_frame(in_buf + i + 1, e - i - 1u)) {
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
EXPORT(st_version) const char *st_version(void) { return "X0X " X0X_VERSION; }
EXPORT(st_fs) uint32_t st_fs(void) { return FS; }

/* ------------------------------------------------------------ the transport --- */
/* bit 0 playing, 1 the song plays, 2 busy (playing or a start pending), 3 recording */
EXPORT(st_transport) uint32_t st_transport(void)
{
    return (seq.playing ? 1u : 0u) | (seq.playing && seq.song_on ? 2u : 0u) |
           (seq.playing || seq.req_start ? 4u : 0u) | (ui.rec && seq.playing ? 8u : 0u);
}
/* (optional export) 0 stop, 1 play as PLAY does (ui.c: the song on SONG, else the patterns), 2 the song from its first
 * bar (no song: the patterns), 3 restart. The audio side takes it at its next block. -> st_transport() */
EXPORT(st_transport_set) uint32_t st_transport_set(uint32_t how)
{
    uint32_t i;
    if (how == 0u || how == 3u || seq.playing || seq.req_start) {
        seq_stop(&seq);
        for (i = 0; i < 50u && (seq.playing || seq.req_stop || seq.req_start); i++)
            tw_isr_ms();                     /* (taken at the next block: a backup right after sees it stopped) */
        if (how == 0u)
            return st_transport();
    }
    if (how == 2u && song()->len) {
        seq.song_on = 1;
        seq.song_start = 0;
    } else if (how == 2u) {
        seq.song_on = 0;
    } else {
        seq.song_start = ui.song_sel < song()->len ? ui.song_sel : 0;
    }
    seq_start(&seq);
    for (i = 0; i < 20u && !seq.playing; i++)
        tw_isr_ms();                     /* (started before the next UI frame looks at the view) */
    return st_transport();
}
/* (optional export) part k (0 the 909, 1 the 808, 2 303 A, 3 303 B, 4 BREAK) held silent (the sequencer's part mute)
 * until set again; for the stems of a render */
EXPORT(st_track_mute_set) void st_track_mute_set(uint32_t k, uint32_t on)
{
    if (k >= NTRACKS)
        return;
    if (!on && ((tw_mutes >> k) & 1u))
        seq.mute &= ~seq_part_mask((int)k);
    tw_mutes = on ? tw_mutes | 1u << k : tw_mutes & ~(1u << k);
    tw_mute_on = 1;
}
EXPORT(st_track_mute) uint32_t st_track_mute(uint32_t k) { return k < NTRACKS ? (uint32_t)seq_part_muted(&seq, (int)k) : 0u; }
/* one project in X0X (its patterns and song are in it): 1 = empty */
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
