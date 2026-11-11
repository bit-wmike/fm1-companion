/* SPDX-License-Identifier: MPL-2.0 */
/* Studio twin of an fm1-base firmware (charlesvestal/fm1-base: AMB-1, LOOM-8): the firmware of the FM-1 (the app's and
 * fm1-base's unchanged sources) for the host, as WebAssembly. It is fm1-base's own browser emulator (web/emu/emu_web.c:
 * host/plat_host.c with PLAT_WEB, the whole app – UI, engine, saved state – against a simulated FM-1), with what the
 * emulator leaves out:
 *   - the NOR flash: device/storage.c (the device's A/B objects at the app's APP_STORE_MAP) on a 1 MiB RAM image. The
 *     emulator keeps its objects in RAM (plat_store_*); every object it saves is written through storage.c to the
 *     image (st_save), and at power-on they are read from it (st_load). The data windows (plat.h plat_data: samples,
 *     resources) are the image itself (plat_host.c's PLAT_DATA_AREA, as host/rfm1.c embeds it)
 *   - the USB SysEx path, in device/main_fm1.c's order: the screen reader (announce.c), the crash history and the
 *     debug stats (crashlog.c, dbgstats.c, the emulator's), the app's own frames (APP_SYSEX), ota.c's identity
 *     handshake (felucca-bsp chip/ota.c) and, with fm1.toml backup = true (FM1B_BACKUP), device/backup.c's backup and
 *     restore (main_fm1.c's usb_cmd and hooks)
 *   - USB MIDI out (the emulator's out log: notes, controllers, the clock) and the app's SysEx (plat_sysex_send)
 * A restore pulls the backup from the host with read requests and waits for each answer (ota.c ota_read) and then for
 * SEL on the panel. A twin cannot wait for its host inside a call, so the host puts the backup on the twin's side of
 * the cable first (frames of cmd 0x7E, below: the twin's own, never seen by the firmware); the read requests are then
 * answered from it at once and the question is answered yes (the suite asked the user before). backup.c checks,
 * stages and commits it exactly as on the device; its report reads (RS_OK_ADDR, RS_FAIL_ADDR | code) go to the host.
 * Built by FM-1 Companion's tools/build_sims.py (targets amb-1-studio, loom-studio) from the firmware's sources at a
 * release tag with its fm1-base and felucca-bsp at the commits the tag pins; nothing of them is modified. Host
 * stand-ins (documented in the NOTICE) are the emulator's: keys / LEDs / LCD, the MASTER pot, one core (plat_cpu2_*
 * run the job at once), Bluetooth, WiFi and the USB host as the emulator stands them in (nothing connects), USB =
 * always configured; no USB audio, no TRS MIDI, no updater (ota.c answers the identity handshake, an update request is
 * dropped), no USB boot, no hardware calibration (the default panel map).
 * The twin ABI (st_*) is what fm1companion/sims/_studio/twin-core.js expects, with the optional st_transport_set and
 * st_track_mute_set; the app's part of them (its transport, its parts for the stems) is the TW_APP_* block below.
 * MPL-2.0 like fm1-base, whose files it includes (FM-1 Companion's other files are GPL-3.0-only). */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
/* (WASI: no clock to measure the render by; the emulator's CPU figures stay 0) */
#define clock_gettime(c, t) ((t)->tv_sec = 0, (t)->tv_nsec = 0, 0)
#define getenv(name) ((char *)0)         /* (WASI: no environment; the simulator's switches stay off) */

#define TW_FLASH 0x100000u
static uint8_t nor[TW_FLASH];
#define PLAT_DATA_AREA (nor + DATA_BASE)          /* the emulator's data windows: the flash image itself */

static void web_audio(const int32_t *blk, uint32_t n);
#include "plat_host.c"
#undef __attribute__                     /* (plat_host.c drops attributes for the firmware files) */
#define EXPORT(name) __attribute__((used, export_name(#name)))

#ifndef FELUCCA_ID
#define FELUCCA_ID "FM-1_700"            /* build_sims.py passes the release's identity (fm1-base tools/build.py) */
#endif
#ifndef APP_VERSION
#define APP_VERSION "DEV"
#endif

/* ------------------------------------------------------------ the NOR flash: storage.c --- */
static uint8_t flash_ok = 1;
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
/* (storage.c's names, apart from the app's: LOOM-8's host part has a st_load of its own) */
#define st_load tw_st_load
#define st_load_prev tw_st_load_prev
#define st_size tw_st_size
#include "storage.c"

/* the emulator's objects <-> storage.c: what it saves is written to the flash, power-on reads it back */
static uint8_t tw_shadow[OBJ_NOBJ][PLAT_STORE_MAX];
static int tw_shadow_len[OBJ_NOBJ];
static uint32_t tw_writes_seen;
static void tw_from_flash(void)
{
    uint32_t o;
    store_ready();
    for (o = 0; o < OBJ_NOBJ; o++) {
        store_len[o] = st_load(o, store[o], PLAT_STORE_MAX);
        tw_shadow_len[o] = store_len[o];
        if (store_len[o] > 0)
            memcpy(tw_shadow[o], store[o], (size_t)store_len[o]);
    }
    tw_writes_seen = store_writes;
}
static void tw_to_flash(void)
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
            if (store_len[o] > 0)
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
static void web_said(const uint8_t *p, uint32_t n) { out_put(p, n); }   /* the screen reader's SysEx to the host */
static uint32_t tw_hsx_taken;
static void tw_drain(void)               /* the emulator's out logs, in order: the app's SysEx, then MIDI out */
{
    uint32_t i;
    if (hsx_n > tw_hsx_taken)
        out_put(hsx_out + tw_hsx_taken, hsx_n - tw_hsx_taken);
    hsx_n = tw_hsx_taken = 0;
    for (i = 0; i < n_out; i++) {
        uint32_t pkt = out_pkt[i], st = pkt >> 8 & 0xFFu;
        uint8_t b[3] = {(uint8_t)st, (uint8_t)(pkt >> 16), (uint8_t)(pkt >> 24)};
        if (!(out_to[i] & TO_USB))
            continue;
        out_put(b, st >= 0xF8u || st == 0xF6u ? 1u : (st & 0xF0u) == 0xC0u || (st & 0xF0u) == 0xD0u ||
                                                      st == 0xF1u || st == 0xF3u ? 2u : 3u);
    }
    n_out = 0;
}

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
static int ota_wire_send(const uint8_t *p, uint32_t n)
{
    tw_host_reply(p, n);
    return 0;
}
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
#include "../bsp/chip/ota.c"

/* the host side of the cable for a restore: read requests (0x30) answered from the staged backup at once; the
 * report reads (0xC0000000 and up) also go to the host, as on the device */
static uint8_t tw_stage[65536];
static uint32_t tw_stage_len, tw_staged;
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
    if (addr >= 0xC0000000u)
        out_put(p, n);
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
    if (w <= sizeof fr_buf) {
        memcpy(fr_buf, hr_wire, w);
        fr_n = w;
        fr_pending = 1;
    }
}

#if FM1B_BACKUP
/* device/backup.c's hooks (main_fm1.c's, on the twin's USB) */
static int rs_fetch(uint32_t addr, uint8_t *dst, uint32_t n) { return ota_read(addr, dst, n); }
static int rs_note(uint32_t addr)
{
    uint8_t b[8];
    ota_deadline = 0;
    return ota_read(addr, b, 8);
}
static int rs_playing(void) { return app_backup_playing(); }
static void rs_begin(void)
{
    ota_try_ms = 5000u;
    ota_deadline = ota_now_ms() + 120000u;
}
static void rs_show(uint32_t pct) { (void)pct; }
/* the device's question (SEL yes, HOME no): the suite asked its user before it put the backup on the cable, so the
 * twin answers yes; a restore without it never gets here (its reads go unanswered) */
static int rs_ask(void)
{
    tw_isr_ms();
    return tw_staged && !rs_playing();
}
static void tw_reload(void);
static void rs_reload(void) { tw_reload(); }
static int rs_rec_erase(void) { return ota_erase(RS_REC_SECTOR); }
static int rs_rec_prog(uint32_t off, const void *src, uint32_t n) { return ota_prog(RS_REC_SECTOR + off, src, n); }
static int rs_rec_read(uint32_t off, void *dst, uint32_t n) { return ota_fread(RS_REC_SECTOR + off, dst, n); }
static int bu_room(void) { return out_n + 1024u <= sizeof out_buf; }   /* the host takes it all */
static int bu_send(const uint8_t *p, uint32_t n) { return ota_wire_send(p, n); }
static void bu_flush(void)
{
    app_backup_flush();
    tw_to_flash();                       /* (the app's save, on the flash before the backup reads it) */
}
static void bu_hold(int on) { app_backup_hold(on); }
#include "backup.c"
static void tw_reload(void)              /* the restored objects from the flash, then the app as main_fm1.c does */
{
    tw_from_flash();
    app_backup_restored();
}
#endif

static void tw_usb_cmd(uint32_t d)       /* main_fm1.c's usb_cmd (its backup and restore) and bu_step */
{
#if FM1B_BACKUP
    if (d >= 7u && ota_dec[2] != 0x11u) {
        if (d == 11u && ota_dec[2] == BU_REQ && bu_rd32(ota_dec + 6) == BU_MAGIC && flash_ok)
            bu_start(app_backup_playing());
        else if (d == 15u && ota_dec[2] == RS_REQ && flash_ok) {
            uint32_t len = ota_rd32(ota_dec + 6), crc = ota_rd32(ota_dec + 10);
            int begun = 0, rc;
            if (bu.on)
                bu_stop();
            ota_try_ms = 300u;
            ota_deadline = ota_now_ms() + 3000u;
            rc = restore_rx(len, crc, &begun);
            ota_try_ms = 5000u;
            ota_deadline = 0;
            if (begun)
                app_ui_init();
            app_say(rc == RS_E_PLAYING ? "STOP FIRST" : "RESTORE FROM USB",
                    !rc ? "RESTORED" : rc == RS_E_PLAYING ? "then restore" : rc == RS_E_NO ? "NOTHING CHANGED"
                    : "NOT RESTORED");
            tw_staged = 0;               /* (used once) */
            tw_stage_len = 0;
            tw_to_flash();
            tw_writes_seen = store_writes;
        }
    }
    bu_step();
#else
    (void)d;
#endif
}

/* ------------------------------------------------------------ the app's part --- */
static uint32_t tw_mutes, tw_mute_on;
#if defined TW_APP_AMB                   /* AMB-1: it plays itself; its four layers (amb.h P_PAD .. P_PULSE) */
#define TW_TRACKS 4u
static int16_t tw_level[TW_TRACKS] = {-1, -1, -1, -1};
static void tw_mute_apply(void)          /* a layer held silent: its level 0 to the engine (the project keeps its own) */
{
    uint32_t k;
    if (!tw_mute_on)
        return;
    for (k = 0; k < TW_TRACKS; k++) {
        int want = (tw_mutes >> k) & 1u ? 0 : proj.par[P_PAD + k];
        if (tw_level[k] != want) {
            en_set(P_PAD + (int)k, want);
            tw_level[k] = (int16_t)want;
        }
    }
}
static uint32_t tw_playing(void) { return en_playing; }
static void tw_play(uint32_t how)
{
    if (how == 0u || how == 3u)
        en_play(0);
    if (how)
        en_play(1);
}
#elif defined TW_APP_LOOM                /* LOOM-8: the sequencer's eight tracks (seq.h: seq.mute bit t, its tracks muted) */
#define TW_TRACKS 8u
static void tw_mute_apply(void)          /* a track held silent: its mute bit set (a scene's or a throw's stay as they are) */
{
    if (tw_mute_on && (seq.mute & tw_mutes) != tw_mutes)
        seq.mute |= (uint16_t)tw_mutes;
}
static uint32_t tw_playing(void) { return seq.playing; }
static void tw_play(uint32_t how)        /* 0 stop, 1 and 2 PLAY from the top (the scene playing), 3 restart */
{
    if (how == 0u || how == 3u || seq.playing)
        seq_stop(&seq);
    if (how) {
        uint32_t i;
        for (i = 0; i < 20u && seq.playing; i++)
            tw_isr_ms();
        seq_start(&seq, 1);
    }
}
#else
#define TW_TRACKS 0u
static void tw_mute_apply(void) {}
static uint32_t tw_playing(void) { return 0; }
static void tw_play(uint32_t how) { (void)how; }
#endif

/* ------------------------------------------------------------ the clock --- */
#define TW_RING 16384u
static float tw_ring[TW_RING * 2u], tw_out_l[4096], tw_out_r[4096];
static uint32_t tw_w, tw_r, tw_booted;
static void web_audio(const int32_t *blk, uint32_t n)   /* the emulator's output, 24 bits = 0 dBFS */
{
    uint32_t k;
    if (tw_w - tw_r + n > TW_RING)
        tw_r = tw_w + n - TW_RING;
    for (k = 0; k < n; k++) {
        uint32_t i = (tw_w++ % TW_RING) * 2u;
        tw_ring[i] = (float)blk[2 * k] / 8388608.0f;
        tw_ring[i + 1u] = (float)blk[2 * k + 1] / 8388608.0f;
    }
}

/* the audio ISR alone (the main loop waits: a restore's reads, its question): the device's time and its sound */
static void tw_isr_ms(void)
{
    static int32_t blk[2 * APP_BLOCK];
    now_ms++;
    while (audio_due_ms <= (double)now_ms) {
        sim_render(blk);
        web_audio(blk, APP_BLOCK);
        audio_due_ms += APP_BLOCK * 1000.0 / 44100.0;
    }
    hsx_ms();
}

static void twin_service(void)           /* main_fm1.c's main loop: the SysEx received, in its order */
{
    const uint8_t *p;
    uint32_t n, i;
    if (ota_frame_get(&p, &n) && (ann_frame(p, n, now_ms) || cl_request(p, n) || ds_request(p, n)))
        ota_frame_done();
#ifdef APP_SYSEX
    if (ota_frame_get(&p, &n) && !(n >= 2u && p[0] == 0x00u && p[1] == 0x59u) && app_sysex(p, n))
        ota_frame_done();                /* (the app's own frames; the update's framing is ota.c's) */
#endif
    tw_usb_cmd(ota_service());
#if FM1B_BACKUP
    for (i = 0; bu.on && i < 2000u; i++) {   /* (a backup streams one frame a pass; the host takes them at once) */
        tw_usb_cmd(0);
        tw_isr_ms();
    }
#endif
    (void)i;
    fr_pending = 0;                      /* (a frame nobody took: dropped) */
    tw_drain();
}

static void twin_ms(void)                /* one millisecond of the device */
{
    tw_mute_apply();
    run_ms(1);                           /* the emulator's: the clock, the render blocks due, the UI frame or the input */
    twin_service();
    tw_to_flash();
}

/* ------------------------------------------------------------ power-on and the flash --- */
EXPORT(st_flash) uint8_t *st_flash(void) { return nor; }
EXPORT(st_flash_size) uint32_t st_flash_size(void) { return TW_FLASH; }
EXPORT(st_flash_erase) void st_flash_erase(void) { memset(nor, 0xFF, sizeof nor); }

/* power-on: the objects read from the flash (a restore's commit cut short finished first, as main_fm1.c), the data
 * windows are the flash, then the emulator's boot (web_boot) and its first 300 ms, so the twin answers its panel at
 * once as the other twins do; 1 = flash found */
EXPORT(st_boot) uint32_t st_boot(uint32_t master_v)
{
    uint32_t i, k10;
    if (tw_booted)
        return flash_ok;
    data_ready = 1;                      /* (the data windows hold what the flash holds: not erased) */
    k10 = master_v > 1023u ? 1023u : master_v;
    master = (k10 * k10) >> 8;
#ifdef APP_STORE_POOL
    st_pool_boot();                      /* (main_fm1.c: the pool's copies where the layout before left them) */
#endif
#if FM1B_BACKUP
    rs_boot();
#endif
    tw_from_flash();
    boot();
    tw_booted = 1;
    for (i = 0; i < 300u; i++)
        twin_ms();
    tw_r = tw_w;
    out_n = 0;
    return flash_ok;
}

/* ---------------------------------------------------------------------- the panel --- */
/* the matrix ids (Felucca's panel: OCT- 0, OCT+ 1, FX 2 .. REC 13; encoders SELECT 0, ALGORITHM 1, KNOB 1..4 2..5,
 * PRESETS 6) <-> plat.h's labels and roles (B_*, EN_*), by device/panel.c's PANEL_DEFAULT */
static const uint8_t TW_BTN[NB] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 0, 1};
static const uint8_t TW_ENC[NE] = {0, 1, 6, 2, 3, 4, 5};
EXPORT(st_keys) void st_keys(uint32_t m)    /* bit n = key n (0 = F3); with APP_KEY_SENSE each key at velocity 100 */
{
#ifdef APP_KEY_SENSE
    uint32_t k, ch = (m ^ held_keys) & ((1u << NKEYS) - 1u);
    for (k = 0; k < NKEYS; k++)
        if (ch >> k & 1u)
            app_host_key(k, m >> k & 1u ? 100u : 0u, 0);
#endif
    held_keys = m & ((1u << NKEYS) - 1u);
}
EXPORT(st_buttons) void st_buttons(uint32_t m)
{
    uint32_t i, b = 0;
    for (i = 0; i < NB; i++)
        if ((m >> TW_BTN[i]) & 1u)
            b |= 1u << i;
    held_btn = b;
}
EXPORT(st_encoder) void st_encoder(uint32_t e, int32_t n)
{
    uint32_t r;
    for (r = 0; r < NE; r++)
        if (TW_ENC[r] == e)
            enc_acc[r] += n;
}
EXPORT(st_master) void st_master(uint32_t v)
{
    uint32_t k10 = v > 1023u ? 1023u : v;
    master = (k10 * k10) >> 8;
}
EXPORT(st_panel_button) uint32_t st_panel_button(uint32_t label) { return label < NB ? TW_BTN[label] : 0u; }

/* ---- USB MIDI ---- */
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
            uint32_t cin = b >= 0xF0u ? (len == 1u ? 0xFu : len == 2u ? 2u : 3u) : b >> 4;
            uint32_t pkt = cin | (uint32_t)b << 8, k;
            for (k = 1; k < len && i + k < n; k++)
                pkt |= (uint32_t)(in_buf[i + k] & 0x7Fu) << (8u * (k + 1u));
            while (tw_booted && mi_w - mi_r >= MQ - 8u)
                twin_ms();
            min_port[mi_w % MQ] = PORT_USB;
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
EXPORT(st_led_buttons) uint32_t st_led_buttons(void)
{
    uint32_t i, m = 0;
    for (i = 0; i < NB; i++)
        if ((lit_btn >> i) & 1u)
            m |= 1u << TW_BTN[i];
    return m;
}
static uint16_t fb_seen[240 * 240];
static uint32_t fb_gen;
EXPORT(st_fb) uint16_t *st_fb(void) { return fb; }
EXPORT(st_fb_gen) uint32_t st_fb_gen(void)
{
    if (memcmp(fb_seen, fb, sizeof fb_seen)) {
        memcpy(fb_seen, fb, sizeof fb_seen);
        fb_gen++;
    }
    return fb_gen;
}
EXPORT(st_now) uint32_t st_now(void) { return now_ms; }
EXPORT(st_version) const char *st_version(void) { return APP_NAME " " APP_VERSION; }
EXPORT(st_fs) uint32_t st_fs(void) { return 44100u; }

/* ------------------------------------------------------------ the transport --- */
EXPORT(st_transport) uint32_t st_transport(void) { return tw_playing() ? 5u : 0u; }   /* playing, busy */
EXPORT(st_transport_set) uint32_t st_transport_set(uint32_t how)
{
    uint32_t i;
    tw_play(how);
    for (i = 0; i < 20u; i++)
        tw_isr_ms();                     /* (the audio side takes it at its next block) */
    return st_transport();
}
EXPORT(st_track_mute_set) void st_track_mute_set(uint32_t k, uint32_t on)
{
    if (k >= TW_TRACKS)
        return;
#if defined TW_APP_LOOM
    if (!on && ((tw_mutes >> k) & 1u))
        seq.mute &= (uint16_t)~(1u << k);
#endif
    tw_mutes = on ? tw_mutes | 1u << k : tw_mutes & ~(1u << k);
    tw_mute_on = 1;
    tw_mute_apply();
}
EXPORT(st_track_mute) uint32_t st_track_mute(uint32_t k) { return (tw_mutes >> k) & 1u; }
EXPORT(st_project_load) uint32_t twin_project_load(uint32_t slot) { (void)slot; return 1u; }
EXPORT(st_project_used) uint32_t twin_project_used(uint32_t slot) { (void)slot; return 0u; }
EXPORT(st_project_current) uint32_t twin_project_current(void) { return 255u; }

/* ------------------------------------------------------------ render --- */
EXPORT(st_out_l) float *st_out_l(void) { return tw_out_l; }
EXPORT(st_out_r) float *st_out_r(void) { return tw_out_r; }
EXPORT(st_render) void st_render(uint32_t n)
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
EXPORT(st_run_ms) void st_run_ms(uint32_t ms)
{
    while (ms--) {
        twin_ms();
        if (tw_w - tw_r > TW_RING / 2u)
            tw_r = tw_w;
    }
}
