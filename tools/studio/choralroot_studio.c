/* SPDX-License-Identifier: GPL-3.0-only */
/* ChoralRoot FM-1 studio twin: the firmware of the FM-1 (ChoralRoot FM-1, unchanged sources) for the host, as
 * WebAssembly. It is ChoralRoot's own browser emulator (tools/emu/web/emu_web.c: the firmware side tools/emu/emu_fw.c
 * with the include list tools/emu/emu_firmware.h, the HAL tools/emu/emu_hal_fw.h, the flash image staged before
 * power-on), with what the emulator leaves out: the USB SysEx path (FELUCCA_OTA 1, as firmware/src/choralroot.c builds
 * the device: usb.c's frame buffer and SysEx ring; cr_backup.c's backup protocol INFO / LIST / GET / PUT / RESTART
 * answered through ota_wire_send, as on the device; ota.c answers the identity handshake) and the twin ABI below.
 * tests/cr_backup_test.c drives cr_backup.c on the same firmware build. CoralFlower's twin (coralflower_studio.c, a
 * fork of ChoralRoot on the same emulator) is the template.
 * The clock follows emu_web.c (the emulator's headless loop, one millisecond at a time):
 *   every millisecond   emu_fw_tick (the 1 ms timer: input edges, knobs, the key sensing), then a UI frame every
 *                       15 ms (emu_fw_frame: master_poll, cr_ui_input, cr_ui_frame, cr_ui_draw, cr_update_poll,
 *                       cr_settings_poll) or the input scan between frames (emu_fw_idle: GRAIN's render slice), the
 *                       SysEx service (main.c: ed_service, ota_service)
 *   every 128 frames    audio.c fm1_alnk0_irq, the real one (one I2S half: cr_engine.c's tick, the parts, fx.c's mix,
 *                       the click; a flash erase holds the audio 45 ms, as the emulator and the device do)
 * Built by FM-1 Companion's tools/build_sims.py (target choralroot-studio) from ChoralRoot's sources at a release tag;
 * nothing of the firmware is modified. Host stand-ins (documented in the NOTICE) are the emulator's: keys / LEDs / LCD,
 * the key sensing fed from the panel (full velocity), the MASTER pot, NOR flash = a 1 MiB RAM image, the second core's
 * job (the bass part) run by the stub core inline (cr_cpu1.h), USB = always configured (SysEx in as USB-MIDI packets,
 * SysEx and MIDI out drained from usb.c's rings); no USB audio, no TRS MIDI, no Bluetooth, no serial console, no
 * updater (ota.c answers the identity handshake, an update request is dropped), no USB boot (OCT- + OCT+ + HOME held
 * 5 s does nothing here). The device restarts after a restore (RESTART, 72): the twin's host does the same by a power
 * cycle (a new instance on the flash).
 * The twin ABI (st_*: boot, flash, MIDI in/out, panel, LEDs, screen, render, transport) is what
 * fm1companion/sims/_studio/twin-core.js expects, with the optional st_transport_set (the looper: PLAY) and
 * st_track_mute_set (the stems: the chord part and the bass part; ChoralRoot has no editor TRACK_MIX). ChoralRoot has
 * no project slots the twin loads (its ten loop slots are backup objects 40..49, loaded on the panel): st_project_*
 * answer "none". */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define getenv(name) ((char *)0)         /* (WASI: no environment; the emulator's EMU_* switches stay off) */
#define printf(...) ((void)0)            /* (the emulator's diagnostics: nobody reads stdout here) */
#define exit(code) ((void)(code))        /* (the emulator ends at the update gesture; the twin carries on) */

#define FELUCCA_OTA 1                    /* usb.c's SysEx frame and ring, cr_backup.c's replies through them */
#ifndef FELUCCA_VERSION
#define FELUCCA_VERSION "ChoralRoot 0.39"   /* build_sims.py passes the release's (tools/build.py --release X.Y) */
#endif
#ifndef FELUCCA_ID
#define FELUCCA_ID "FM-1_920"            /* build_sims.py passes the package identity (tools/build.py PRODUCT) */
#endif

#define CRB_REBOOT() ((void)0)            /* (RESTART, 72: the reply only; the host power-cycles the twin) */

#include "../tools/emu/web/emu_web.c"

#undef __attribute__                     /* (hostsim.c drops attributes for the firmware files) */
#define EXPORT(name) __attribute__((used, export_name(#name)))

/* ------------------------------------------------- the SysEx path (main.c's ed_service, ota_service) --- */
static void twin_drain(void);
static uint32_t ota_now_ms(void) { return fm1_ms; }
static void ota_idle(void) { twin_drain(); }       /* ota_wire_send waits for room in the SysEx ring */
static int ota_erase(uint32_t off) { (void)off; return -1; }
static int ota_prog(uint32_t off, const void *p, uint32_t n) { (void)off; (void)p; (void)n; return -1; }
static int ota_fread(uint32_t off, void *p, uint32_t n) { return st_read(off, p, n); }
static void ota_show(uint32_t step, int32_t code) { (void)step; (void)code; }
static void ota_commit(const uint8_t *parm) { (void)parm; }
#include "../firmware/src/ota.c"

/* cr_backup.c's ed_service (the device's: only our frames, 7D 46 4C, the rest stays for ota_service), without its
 * restart (crb_leave: the host power-cycles the twin itself). usb.c also keeps a short frame in usb.sysex, which the
 * emulator's frame (emu_fw_frame, its scripts' "sysex") would answer a second time: it is taken here */
static void twin_ed_service(void)
{
    const uint8_t *p;
    uint32_t n;
    crb.reboot = 0;
    crb.stub = 0;
    if (!usb.sx_on)
        usb.sx_len = 0;
    if (!ota_frame_get(&p, &n) || n < 4u || p[0] != CRB_HDR0 || p[1] != CRB_HDR1 || p[2] != CRB_HDR2)
        return;
    crb_handle(p, n);
    ota_frame_done();
}

static uint8_t out_buf[65536];                     /* raw MIDI bytes the device sent (SysEx and channel messages) */
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
static void twin_drain(void)                       /* usb.c ep1_tx: SysEx first, never split by MIDI out */
{
    while (so_r != so_w) {
        uint32_t pkt = sx_out_q[so_r++ % SXQ], cin = pkt & 15u;
        uint8_t b[3] = {(uint8_t)(pkt >> 8), (uint8_t)(pkt >> 16), (uint8_t)(pkt >> 24)};
        out_put(b, cin == 4u || cin == 7u ? 3u : cin == 6u ? 2u : 1u);
    }
    SX_TAKEN();
    if (sx_busy)
        return;
    while (mo_r != mo_w) {
        uint32_t pkt = midi_out_q[mo_r++ % MQ], cin = pkt & 15u;
        uint32_t len = cin == 0xCu || cin == 0xDu ? 2u : cin >= 8u ? 3u : cin == 0xFu || cin == 5u ? 1u : cin == 2u ? 2u : 3u;
        uint8_t b[3] = {(uint8_t)(pkt >> 8), (uint8_t)(pkt >> 16), (uint8_t)(pkt >> 24)};
        out_put(b, len);
    }
}
static void twin_service(void)
{
    twin_ed_service();
    ota_service();                                 /* M-UPGRADE handshake (identity); drops any other frame */
    usb.ota_req = 0;                               /* (the updater is not part of the twin) */
    usb.uboot_req = 0;
    twin_drain();
}

/* ------------------------------------------------------------ the clock and the sound --- */
#define TW_RING 8192u
static float tw_ring[TW_RING * 2u], tw_out_l[4096], tw_out_r[4096];
static uint32_t tw_w, tw_r, tw_ms, tw_frame, tw_booted, tw_mutes, tw_mute_on;
static uint64_t tw_frames;

static void twin_block(void)                       /* emu_fw_audio for one I2S half, kept at the DAC's 24 bits */
{
    uint32_t k;
    const int32_t *h;
    if (tw_w - tw_r + HALF_FRAMES > TW_RING)
        tw_r += HALF_FRAMES;                       /* nobody listened: the oldest sound goes */
    if (emu_stall_blocks) {                        /* a flash erase on the device: IRQs off, the buffer zeroed */
        emu_stall_blocks--;
        emu_stall_blocks_all++;
        for (k = 0; k < HALF_FRAMES; k++) {
            uint32_t i = (tw_w++ % TW_RING) * 2u;
            tw_ring[i] = tw_ring[i + 1u] = 0.0f;
        }
        return;
    }
    emu_half ^= 1u;
    if (tw_mutes) {                                /* a stem: the metronome's click is no part (cr_out.c cr_click_mix
                                                    * strikes it at metro_vol: 0 for this block) */
        uint8_t vol = crl.metro_vol;
        crl.metro_vol = 0;
        fm1_alnk0_irq();
        crl.metro_vol = vol;
    } else {
        fm1_alnk0_irq();
    }
    h = &abuf[emu_half * HALF_WORDS];
    for (k = 0; k < HALF_FRAMES; k++) {            /* Q15 << OUT_SHIFT = 0 dBFS (emu_fw.c) */
        uint32_t i = (tw_w++ % TW_RING) * 2u;
        tw_ring[i] = (float)h[2u * k] / (float)(32768 << OUT_SHIFT);
        tw_ring[i + 1u] = (float)h[2u * k + 1u] / (float)(32768 << OUT_SHIFT);
    }
}

static void twin_ms(void)                          /* one millisecond of the device (emu_web.c run_ms) */
{
    uint32_t ms = tw_ms++, k;
    emu_fw_tick(ms);
    if (ms == 0 || ms - tw_frame >= 15u) {
        tw_frame = ms;
        twin_service();                            /* main.c: ed_service, ota_service before the frame */
        emu_fw_frame();
    } else {
        emu_fw_idle();
        twin_service();                            /* main.c's wait loop: cr_ui_input, ed_service */
    }
    if (tw_mute_on)                                /* st_track_mute_set: the parts held silent (stems) */
        for (k = 0; k < 2u; k++)
            trk[k].p[P_MUTE] = (int16_t)((tw_mutes >> k) & 1u);
    while (tw_frames + HALF_FRAMES <= (uint64_t)ms * EMU_FS / 1000u) {
        twin_block();
        tw_frames += HALF_FRAMES;
    }
    twin_drain();
}

/* ------------------------------------------------------------ power-on and the flash --- */
EXPORT(st_flash) uint8_t *st_flash(void) { return emu_flash; }    /* the whole NOR image (write it before st_boot) */
EXPORT(st_flash_size) uint32_t st_flash_size(void) { return EMU_FLASH_SIZE; }
EXPORT(st_flash_erase) void st_flash_erase(void) { memset(emu_flash, 0xFF, sizeof emu_flash); }   /* a new device */

/* the emulator's power-on (emu_web.c web_boot: emu_fw_init, the flash read from the stage), then the splash (cr_ui.c
 * CR_SPLASH_MS, 1500 ms: the stripes slide in, the version under them) and a frame more, its sound dropped, so the twin
 * answers its panel at once as the other twins do; 1 = flash found */
EXPORT(st_boot) uint32_t st_boot(uint32_t master)
{
    uint32_t i;
    if (tw_booted)
        return flash_ok;
    memcpy(web_stage, emu_flash, EMU_FLASH_SIZE);  /* (emu_flash_open reads the "file" into emu_flash) */
    web_flash_stage_len(EMU_FLASH_SIZE);
    emu_hal.master = (int32_t)(master > 1023u ? 1023u : master ? master : 1u);
    web_boot();
    tw_booted = 1;
    for (i = 0; i < 1520u; i++)
        twin_ms();
    tw_r = tw_w;
    out_n = 0;
    return flash_ok;
}

/* ---------------------------------------------------------------------- the panel --- */
/* keys: bit n = note key n (0 = F3 .. 26 = G5); buttons: matrix ids (panel.c PANEL_DEFAULT's); a key or a button newly
 * down also taps (emu_web.c web_keys: a press shorter than 1 ms counts) */
EXPORT(st_keys) void st_keys(uint32_t m)
{
    m &= (1u << EMU_NKEY) - 1u;
    emu_hal.keys_tap |= m & ~emu_hal.keys;
    emu_hal.keys = m;
}
EXPORT(st_buttons) void st_buttons(uint32_t m)
{
    m &= (1u << EMU_NBTN) - 1u;
    emu_hal.buttons_tap |= m & ~emu_hal.buttons;
    emu_hal.buttons = m;
}
EXPORT(st_encoder) void st_encoder(uint32_t e, int32_t n) { if (e < EMU_NENC) emu_hal.enc[e] += n; }   /* + = CW */
EXPORT(st_master) void st_master(uint32_t v) { emu_hal.master = (int32_t)(v > 1023u ? 1023u : v); }   /* 0..1023 */
/* the matrix id of a panel label (panel.c B_*: B_PLAY 10, B_REC 11 …) */
EXPORT(st_panel_button) uint32_t st_panel_button(uint32_t label) { return label < NB ? panel.btn[label] : 0u; }

/* ---- USB MIDI: in as USB-MIDI event packets (usb.c midi_in_event), out from the SysEx and MIDI OUT rings ---- */
EXPORT(st_out) uint8_t *st_out(void) { return out_buf; }
EXPORT(st_out_len) uint32_t st_out_len(void) { return out_n; }
EXPORT(st_out_lost) uint32_t st_out_lost(void) { return out_lost; }
EXPORT(st_out_clear) void st_out_clear(void) { out_n = 0; }

static uint8_t in_buf[65536];
EXPORT(st_in) uint8_t *st_in(void) { return in_buf; }
/* n bytes of in_buf: complete MIDI messages from the USB host (SysEx and/or channel / realtime messages), sent as
 * USB-MIDI packets; a SysEx frame is answered at once (main.c serves it in its wait loop). -> bytes in st_out */
EXPORT(st_midi_in) uint32_t st_midi_in(uint32_t n)
{
    uint32_t i = 0;
    if (n > sizeof in_buf)
        n = sizeof in_buf;
    while (i < n) {
        uint8_t b = in_buf[i];
        if (b == 0xF0u) {                          /* SysEx: CIN 4 (three bytes on), CIN 5..7 (the end, 1..3) */
            uint32_t e = i, end;
            while (e < n && in_buf[e] != 0xF7u)
                e++;
            end = e < n ? e + 1u : n;              /* (an unterminated one goes as it is: aborted later) */
            while (i < end) {
                uint32_t k = end - i >= 3u ? 3u : end - i, pkt = 0, j;
                for (j = 0; j < k; j++)
                    pkt |= (uint32_t)in_buf[i + j] << (8u * (j + 1u));
                i += k;
                pkt |= i == end && in_buf[end - 1u] == 0xF7u ? 4u + k : 4u;
                midi_in_event(pkt);
            }
            twin_service();                        /* one frame buffer: serve each frame as it completes */
            continue;
        }
        if (b >= 0x80u && b != 0xF7u) {
            uint32_t len = b >= 0xF8u || b == 0xF6u ? 1u : (b & 0xF0u) == 0xC0u || (b & 0xF0u) == 0xD0u ||
                           b == 0xF1u || b == 0xF3u ? 2u : 3u;
            uint32_t cin = b >= 0xF0u ? (len == 1u ? 0xFu : len == 2u ? 2u : 3u) : b >> 4;
            uint32_t pkt = cin | (uint32_t)b << 8, k;
            for (k = 1; k < len && i + k < n; k++)
                pkt |= (uint32_t)(in_buf[i + k] & 0x7Fu) << (8u * (k + 1u));
            while (tw_booted && MQ - (mi_w - mi_r) < 8u)   /* (usb.c ep1_take: the ring full, the host waits) */
                twin_ms();
            midi_in_event(pkt);
            i += len;
            continue;
        }
        i++;                                       /* stray data byte (no running status on USB) */
    }
    twin_service();
    return out_n;
}

/* ------------------------------------------------------------ LEDs, the screen --- */
static uint32_t led_lit(uint32_t id)               /* lit (emu_web.c led_of 2), by matrix id */
{
    uint32_t c, r;
    for (c = 0; c < EMU_NCOL; c++)
        for (r = 1; r < 5u; r++)
            if (emu_keymap[r][c] == (int8_t)id)
                return (emu_hal.led[c] >> r) & 1u;
    return 0;
}
EXPORT(st_led_keys) uint32_t st_led_keys(void)
{
    uint32_t k, m = 0;
    for (k = 0; k < EMU_NKEY; k++)
        m |= led_lit(14u + k) << k;
    return m;
}
EXPORT(st_led_buttons) uint32_t st_led_buttons(void)
{
    uint32_t b, m = 0, q = emu_hal.led_play_green;
    for (b = 0; b < EMU_NBTN; b++)
        m |= led_lit(b) << b;
    if ((emu_hal.led[q >> 3] >> (q & 7u)) & 1u)    /* PLAY's green half lights PLAY */
        m |= 1u << panel.btn[B_PLAY];
    return m;
}
static uint16_t fb_seen[EMU_LCD_W * EMU_LCD_H];
static uint32_t fb_gen, fb_writes;
EXPORT(st_fb) uint16_t *st_fb(void) { return emu_hal.lcd; }      /* RGB565, the panel's byte order */
EXPORT(st_fb_gen) uint32_t st_fb_gen(void)
{
    if (fb_writes != emu_hal.lcd_writes) {
        fb_writes = emu_hal.lcd_writes;
        if (memcmp(fb_seen, emu_hal.lcd, sizeof fb_seen)) {
            memcpy(fb_seen, emu_hal.lcd, sizeof fb_seen);
            fb_gen++;
        }
    }
    return fb_gen;
}
EXPORT(st_now) uint32_t st_now(void) { return fm1_ms; }
EXPORT(st_version) const char *st_version(void) { return FELUCCA_VERSION; }
EXPORT(st_fs) uint32_t st_fs(void) { return FS; }

/* ------------------------------------------------------------ the transport --- */
/* the looper (cr_loop.c, as cr_ui.c's snapshot shows it): bit 0 the loop plays, 1 (no song), 2 busy (playing, a
 * count-in, a take), 3 recording (a take or an overdub) */
EXPORT(st_transport) uint32_t st_transport(void)
{
    uint32_t play = crl.state == CRL_PLAYING, rec = crl.cap == CRL_CAP_REC || crl.cap == CRL_CAP_OD ||
                                                    crl.cap == CRL_CAP_COUNTIN;
    return (play ? 1u : 0u) | (play || rec ? 4u : 0u) | (rec ? 8u : 0u);
}
/* (optional export) the transport as PLAY moves it (cr_ui.c BT_LOOP: the event LP_PLAY, play / stop, a take
 * committed): 0 stop, 1 play the loop, 2 (no song: as 1), 3 restart from the top. The audio side takes it at its next
 * block. -> st_transport() */
static void tw_loop_play(void)
{
    uint32_t i, was = crl.state;
    cr_post(CRE_LOOP, LP_PLAY, 0, 0);
    for (i = 0; i < 10u && crl.state == was; i++)
        twin_ms();                                 /* (the audio side takes it at its next block) */
}
EXPORT(st_transport_set) uint32_t st_transport_set(uint32_t how)
{
    if (crl.state == CRL_PLAYING && how != 1u && how != 2u)
        tw_loop_play();                            /* stop (3: then from the top) */
    if (how != 0u && crl.state == CRL_STOPPED)
        tw_loop_play();
    return st_transport();
}
/* (optional export) part k (0 the chord, 1 the bass) held silent: no new note of it starts (voice.c P_MUTE), until set
 * again; for the stems of a render. While a part is held silent the metronome's click is too (no stem carries it; the
 * mix, nothing muted, has it as the device plays it). Parts 3 and 4 are unused (cr_ui.c: silent) */
EXPORT(st_track_mute_set) void st_track_mute_set(uint32_t k, uint32_t on)
{
    if (k >= 2u)
        return;
    tw_mutes = on ? tw_mutes | 1u << k : tw_mutes & ~(1u << k);
    tw_mute_on = 1;
}
EXPORT(st_track_mute) uint32_t st_track_mute(uint32_t k) { return k < NTRK ? (uint32_t)(trk[k].p[P_MUTE] != 0) : 0u; }
/* no project slots the twin loads (the loop slots are the panel's): 1 = empty */
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
