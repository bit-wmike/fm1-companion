/* SPDX-License-Identifier: GPL-3.0-only */
/* KARMA studio twin: the firmware of the FM-1 (KARMA, unchanged sources) for the host, as WebAssembly, with what the
 * browser emulator (web/emu/sloop_web.c, which runs the UI and the mix on the host tests' doubles) leaves out: the NOR
 * flash with storage.c, project.c and upreset.c (settings, the song's parts A..D, the song order, the three saved
 * songs, the user presets, the HEX kits, the working project's autosave, the user sample slots read in place as XIP),
 * the USB SysEx path with the editor protocol (usb.c sysex_byte -> editor.c ed_service -> ota_wire_send; editor.c's
 * backup LIST / GET / PUT, protocol v6) and the MIDI out ring. The composition follows firmware/src/felucca.c (its
 * include order) on the host tests' base (tests/hostsim.c), the boot follows firmware/src/main.c fm1_main up to its
 * loop, the clock and the audio ISR follow the Felucca family's browser emulators:
 *   every millisecond   TIMER4 / fm1_ms, ui_input and ed_service (main.c's wait loop), MIDI out drained (usb.c ep1_tx)
 *   every 15 ms         main.c's loop body: battery, MASTER, ed_service, ota_service, ui_input, ui_leds, ui_draw,
 *                       the screen reader's speak / ann_tick (3.1), autosave_tick, sections_flush
 *   every 128 frames    audio.c fm1_alnk0_irq, the real one (one I2S half)
 * Built by FM-1 Companion's tools/build_sims.py (target karma-studio) from KARMA's tagged sources; nothing of the
 * firmware is modified. Plaits' and Elements' C++ (firmware/mi, Mutable Instruments, MIT) is compiled as the firmware's
 * own tools/plx_host.sh does for the host tests and linked in. Host stand-ins (documented in the NOTICE): the HAL (keys,
 * LEDs, LCD into a frame buffer, ADC = the MASTER value given here, battery full), NOR flash = nor[] (JEDEC id of the
 * FM-1's part, no erase / program timing), USB = always configured, SysEx in as USB-MIDI packets, SysEx and MIDI out
 * drained from usb.c's rings; one core (KARMA_DUAL_CORE 0, KARMA's own single-core build: fx.c renders every track
 * itself), no USB audio (FELUCCA_UAC 0: no STEMS over USB), no TRS MIDI, no serial console, no update loader (ota.c
 * answers the identity handshake, FELUCCA_ID, but an update request is dropped and its flash hooks refuse), no USB
 * rescue (recovery.c). The twin ABI (st_*: boot, flash, MIDI in/out, panel, LEDs, screen, render, transport, projects)
 * is what fm1companion/sims/_studio/twin-core.js expects, the same set as tools/studio/skiff_studio.c exports, with the
 * optional st_transport_set (KARMA's editor has no transport command). Projects 0..3 are the song's parts A..D
 * (KARMA's SAVE + 5..8, its sections). */
#include <stdint.h>

#define FELUCCA_WEB 1                    /* (the host builds' branches, as the emulator) */
#define FELUCCA_CDC 0                    /* no serial console (MENU > USB SERIAL stays a setting, nothing behind it) */
#define FELUCCA_FLASH 1
#define FELUCCA_OTA 1                    /* the editor SysEx plumbing in usb.c, ota.c's handshake, editor.c */
#define FELUCCA_UAC 0                    /* no USB audio (MENU > USB AUDIO stays a setting: no STEMS over USB) */
#define FELUCCA_UART 0
#define FELUCCA_ARRANGER 1               /* (felucca.c: the song arranger, always on) */
#define KARMA_DUAL_CORE 0                /* one core: KARMA's single-core build (KARMA_DUAL_CORE=0 build.sh) */
#define FM1_AUDIO_RX_CLOCK 0
#define PR_WAVES_FROM_PLX 1              /* (felucca.c: PLAITS WAVE reads Plaits' own wavetable, plx.cc) */
#if __has_include("../firmware/src/announce.c")
#define KARMA_ANN 1                      /* (3.1) the screen reader (announce.c, ui_speak.c), as felucca.c */
#else
#define KARMA_ANN 0
#endif
#ifndef FELUCCA_VERSION
#define FELUCCA_VERSION "KARMA 3.1"      /* build_sims.py passes the tag's (ui.c) */
#endif
#ifndef FELUCCA_ID
#define FELUCCA_ID "FM-1_926"            /* build_sims.py passes the package identity (tools/build.py PRODUCT) */
#endif

static uint8_t nor[0x100000];            /* the SPI NOR (erased: 0xFF) */
#define SMP_USER_XIP(k) ((const uint8_t *)nor + SMP_USER_OFF((uint32_t)(k)))   /* eng_sample.c */
#define main hostsim_main
#include "hostsim.c"
#undef main

/* ------------------------------------------------------------ the clock --- */
#define FM1_TICKS_PER_US 24u             /* TIMER4: 24 MHz */
static uint32_t web_ticks;
static uint32_t fm1_ticks(void) { return web_ticks; }

/* ------------------------------------------------------ audio (ALNK0) --- */
static uint32_t web_half;                /* the half the next render fills */
#define FM1_AUDIO_HALF 1u
static uint8_t fm1_audio_pending(void) { return FM1_AUDIO_HALF; }
static void fm1_audio_ack_aux(uint8_t p) { (void)p; }
static uint32_t fm1_audio_free_half(void) { return web_half; }
static void fm1_audio_ack_half(void) {}
static void fm1_audio_init(int32_t *buf, uint32_t words, void (*isr)(void), uint32_t prio, int32_t *rx)
{ (void)buf; (void)words; (void)isr; (void)prio; (void)rx; }
static void fm1_audio_stop(void) {}
void isr_alnk0(void) {}
#include "../firmware/src/audio.c"

/* ------------------------------------------------------------ the panel (hal/fm1_input.h, as sloop_web.c) --- */
#define FM1_NCOL 11u
static const int8_t FM1_KEYMAP[6][FM1_NCOL] = {      /* hal/fm1_input.h: key id at (column, row bit) */
    {-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1},
    { 5, 11,  4, 10,  3,  9,  2,  8, -1, -1, -1},
    {34, 35, 36, 37, 38, 40, 39, 13,  7,  6, 12},
    {23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33},
    { 0,  1, 15, 14, 17, 16, 19, 18, 20, 21, 22},
    {-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1},
};
static uint8_t fm1_led[FM1_NCOL], fm1_led_dim[FM1_NCOL], fm1_led_bg[FM1_NCOL];
static volatile uint16_t fm1_led_bg_ns;
static void fm1_led_key(uint32_t id, int on)      /* (panel.c's setup: one LED by its matrix id) */
{
    uint32_t c, r;
    for (c = 0; c < FM1_NCOL; c++)
        for (r = 1; r < 5u; r++)
            if (FM1_KEYMAP[r][c] == (int8_t)id) {
                if (on)
                    fm1_led[c] |= (uint8_t)(1u << r);
                else
                    fm1_led[c] &= (uint8_t)~(1u << r);
            }
}
static uint32_t host_pressed, host_released, host_notes;
static int32_t host_enc[7];
static uint32_t fm1_input_edges(uint32_t *released)
{
    uint32_t p = host_pressed;
    if (released)
        *released = host_released;
    host_pressed = host_released = 0;
    return p;
}
static uint32_t fm1_input_note_edges(void) { uint32_t n = host_notes; host_notes = 0; return n; }
static int32_t fm1_enc_take(uint32_t e) { int32_t s = host_enc[e % 7u]; host_enc[e % 7u] = 0; return s; }
static void fm1_input_init(void) {}
static void fm1_input_tick(void) {}
static void twin_wait_ms(void);
static void fm1_wdt_feed(void) { twin_wait_ms(); }   /* the firmware's waits: time and the audio ISR go on */
static uint32_t host_master_adc = 1023;           /* MASTER, 0..1023 as the ADC reads it */
enum { FM1_ADC_BATT = 3, FM1_ADC_MASTER = 4 };
static int32_t fm1_adc_read(uint32_t ch) { return ch == FM1_ADC_MASTER ? (int32_t)host_master_adc : 620; }
static void fm1_adc_init(void) {}

/* ----------------------------------------------------------- the screen (lcd.c) --- */
static uint16_t host_screen[240 * 240];  /* RGB565 as the panel gets it (high byte first) */
static void lcd_fill(uint32_t x, uint32_t y, uint32_t w, uint32_t h, uint16_t c)
{
    uint32_t i, j;
    uint16_t sw = (uint16_t)((c >> 8) | (c << 8));
    for (j = 0; j < h && y + j < 240u; j++)
        for (i = 0; i < w && x + i < 240u; i++)
            host_screen[(y + j) * 240u + x + i] = sw;
}
static void lcd_blit(uint32_t x, uint32_t y, uint32_t w, uint32_t h, const uint16_t *p)
{
    uint32_t i, j;
    for (j = 0; j < h && y + j < 240u; j++)
        for (i = 0; i < w && x + i < 240u; i++)
            host_screen[(y + j) * 240u + x + i] = p[j * w + i];
}
static void lcd_sync(void) {}
static void lcd_init(void) {}

#include "../firmware/src/gfx.c"
#include "../firmware/src/panel.c"
#include "../firmware/src/ui.c"
#if KARMA_ANN
#include "ann_words.h"                    /* (build/gen: tools/gen_words.py, firmware/words.tsv) */
#define ANN_NAME FELUCCA_VERSION
#define ANN_MAX 160u
#include "../firmware/src/announce.c"
#endif
#include "../firmware/src/ui_song.c"
#include "../firmware/src/ui_studio.c"
#include "../firmware/src/ui_op.c"
#include "../firmware/src/icons.c"
#include "../firmware/src/ui_draw.c"
#include "../firmware/src/ui_vis.c"
#include "../firmware/src/ui_layers.c"
#include "../firmware/src/ui_menu.c"
#include "../firmware/src/ui_input.c"
#if KARMA_ANN
#include "../firmware/src/ui_speak.c"
#endif

/* ------------------------------------------------------------ the flash (felucca.c, hal/fm1_flash.h) --- */
#include "../firmware/hal/fm1_flash_id.h"          /* FL_JEDEC_OK: the parts the firmware knows */
static uint8_t flash_ok;
static int st_read(uint32_t off, void *dst, uint32_t n)
{
    if (off >= sizeof nor || n > sizeof nor - off)
        return -1;
    memcpy(dst, nor + off, n);
    return 0;
}
static int nor_erase(uint32_t off)
{
    if (off >= sizeof nor || 4096u > sizeof nor - off || (off & 4095u))
        return -8;
    memset(nor + off, 0xFF, 4096);
    return 0;
}
static int nor_prog(uint32_t off, const void *src, uint32_t n)       /* NOR: a write only clears bits */
{
    const uint8_t *s = src;
    uint32_t i;
    if (off >= sizeof nor || n > sizeof nor - off)
        return -8;
    for (i = 0; i < n; i++)
        nor[off + i] &= s[i];
    return 0;
}
static int st_erase(uint32_t off) { return nor_erase(off); }
static int st_prog(uint32_t off, const void *src, uint32_t n) { return nor_prog(off, src, n); }
static uint32_t irq_save(void) { return 0; }
static void irq_restore(uint32_t f) { (void)f; }
static uint32_t fl_jedec_ram(void) { return 0x856014u; }             /* the FM-1's part (Puya P25Q80) */
static void fl_plain_window_init(void) {}
#define FL_FAR(fn) (fn)
static void audio_silence(void) {}
/* the editor's flash hooks (felucca.c / fm1_flash.h on the device): the user sample slots USR1..USR4 in nor[] */
static int twin_in_slots(uint32_t off, uint32_t n)
{
    return (off >= SMP_USER_BASE && off <= SMP_USER_BASE + 3u * SMP_USER_SIZE &&
            n <= SMP_USER_BASE + 3u * SMP_USER_SIZE - off) ||
           (off >= SMP_USER4_BASE && off <= SMP_USER4_BASE + SMP_USER_SIZE && n <= SMP_USER4_BASE + SMP_USER_SIZE - off);
}
static int fl_erase4k_quiet(uint32_t off, uint32_t *took)
{
    *took = 0;
    if (!twin_in_slots(off, 4096u))
        return -8;                                    /* (the editor erases only inside the sample slots) */
    return nor_erase(off);
}
static int fl_write(uint32_t off, const void *p, uint32_t n)
{
    if (!twin_in_slots(off, n))
        return -8;
    return nor_prog(off, p, n);
}
static void fl_inval(uint32_t off, uint32_t n) { (void)off; (void)n; }   /* (no XIP cache) */
#include "../firmware/src/storage.c"
#include "../firmware/src/upreset.c"
#include "../firmware/src/project.c"

/* ---- ota.c: its handshake answers the package identity (FELUCCA_ID) as on the device; the update itself never runs
 * here (usb.ota_req is dropped) and its flash hooks refuse ---- */
#define OTA_IDENTITY FELUCCA_ID
static uint32_t ota_now_ms(void) { return fm1_ms; }
static void twin_drain(void);
static void ota_idle(void) { twin_drain(); }      /* ota_wire_send waits for room in the SysEx ring */
static int ota_erase(uint32_t off) { (void)off; return -1; }
static int ota_prog(uint32_t off, const void *p, uint32_t n) { (void)off; (void)p; (void)n; return -1; }
static int ota_fread(uint32_t off, void *p, uint32_t n) { return st_read(off, p, n); }
static void ota_show(uint32_t step, int32_t code) { (void)step; (void)code; }
static void ota_commit(const uint8_t *parm) { (void)parm; }
#include "../firmware/src/ota.c"
/* hal/fm1_irq.h's crash records (editor SysEx 93 reads them): none here, zero */
typedef struct {
    uint32_t magic, count, vec, pc, rets, emu, dbg, sp, psr, icfg, uptime_ms;
    uint32_t etm[4];
    uint32_t early;
} fm1_crash_t;
static fm1_crash_t fm1_crash;
typedef struct { uint32_t magic, count, cpu, dbg, emu, pc, rets, uptime_ms; } fm1_core1_last_t;
static fm1_core1_last_t fm1_core1_last;
#include "../firmware/src/editor.c"
#if KARMA_ANN
static int ann_wire_send(const uint8_t *p, uint32_t n)   /* felucca.c: never waits (no room: not sent) */
{
    if (!usb.config || usb.suspended || sx_busy || SXQ - (so_w - so_r) < (n + 2u) / 3u)
        return -1;
    return ota_wire_send(p, n);
}
#endif
#include "../firmware/src/splash.c"

/* ------------------------------------------------- main.c and its HAL --- */
static struct { uint8_t p3_rst, wdt_con; uint32_t rst_src; } fm1_boot;
uint32_t _data_start[1], _data_end[1], _data_load[1], _bss_start[1], _bss_end[1];
uint32_t _pool_start[1], _pool_end[1], _rt_start[1], _rt_end[1], _rt_load[1];
static void fm1_timer5_ack(void) {}
static void fm1_timer5_start(void (*isr)(void), uint32_t prio) { (void)isr; (void)prio; }
void isr_timer5(void) {}
static void fm1_reboot(void) {}
static void fm1_enter_uboot(void) {}
static void fm1_guard_lock_top(void) {}
static void fm1_irq_enable_all(void) {}
static void fm1_irq_init(void) {}
static void fm1_time_init(void) {}
static void fm1_core1_stop(void) {}
static void fm1_reset_reason(void) {}
static void fm1_wdt_arm(uint32_t v) { (void)v; }
static void fm1_mailbox_clear(void) {}
static void fm1_guard_enable(uint32_t m) { (void)m; }
enum { FM1_GUARD_STACK = 1, FM1_GUARD_WRITE = 2, FM1_GUARD_BUS = 4, FM1_GUARD_PC = 8 };
static int recovery_key(void) { return 0; }       /* (recovery.c, the USB rescue: not part of the twin) */
static void recovery_main(void) {}
/* main.c's calls into the USB driver (registers): nothing here (usb.c's own functions stay as they are) */
#define usb_poll() ((void)0)
#define usb_start() ((void)0)
#define usb_retry(ms) ((void)(ms))
#define usb_detach() ((void)0)
#include "../firmware/src/main.c"
#undef usb_poll
#undef usb_start
#undef usb_retry
#undef usb_detach

#undef __attribute__                              /* (hostsim.c drops attributes for the firmware files) */
#define EXPORT(name) __attribute__((used, export_name(#name)))

static uint32_t booted, last_frame, fb_gen, in_tick, in_audio;
static int32_t master_knob = 512 * 16, master_applied = -100;
static uint16_t fb_seen[240 * 240];

/* main.c fm1_main's boot, up to its main loop (USB configured: plugged into the Suite) */
static void twin_power_on(void)
{
    persist_boot();
    if (flash_ok)
        ota_boot_cleanup();
    settings_init();
    lcd_init();
    lcd_fill(0, 0, 240, 240, C_BLACK);
    boot_t = 0;                                   /* the boot animation (ui_vis.c), the UI's first ~2.5 s */
    memset(&felucca_dbg, 0, sizeof felucca_dbg);
    felucca_dbg.magic = DBG_MAGIC;
    felucca_dbg.boots++;
    fm1_input_init();
    fm1_adc_init();
    panel_init();
    felucca_init();
    audio_init();
    usb.up = 1;                                   /* plugged into the Suite: the editor protocol is live */
    usb.config = 1;
    booted = 1;
    last_frame = fm1_ms - 15u;
}

EXPORT(st_flash) uint8_t *st_flash(void) { return nor; }          /* the whole NOR image (write it before st_boot) */
EXPORT(st_flash_size) uint32_t st_flash_size(void) { return sizeof nor; }
EXPORT(st_flash_erase) void st_flash_erase(void) { memset(nor, 0xFF, sizeof nor); }   /* a new, empty device */

static void tick(void);
static uint32_t ring_w, ring_r;
/* fm1_main up to the main loop, then its first frames until the boot animation is over (at most 3 s device time, the
 * sound dropped): the twin answers its panel at once, as the other twins do; 1 = flash found */
EXPORT(st_boot) uint32_t st_boot(uint32_t master)
{
    uint32_t t;
    if (booted)
        return flash_ok;
    host_master_adc = master > 1023u ? 1023u : master;
    twin_power_on();
    for (t = 0; t < 3000u && boot_t != 0xFFFFu; t++) {
        tick();
        ring_r = ring_w;
    }
    return flash_ok;
}

/* the panel: matrix ids (PANEL_DEFAULT's), as fm1_in and the scan would have them */
EXPORT(st_keys) void st_keys(uint32_t m)
{
    m &= (1u << 27) - 1u;
    host_notes |= m & ~fm1_in.notes;
    fm1_in.notes = m;
}
EXPORT(st_buttons) void st_buttons(uint32_t m)
{
    host_pressed |= m & ~fm1_in.buttons;
    host_released |= fm1_in.buttons & ~m;
    fm1_in.buttons = m;
}
EXPORT(st_encoder) void st_encoder(uint32_t e, int32_t n) { if (e < 7u) host_enc[e] += n; }
EXPORT(st_master) void st_master(uint32_t v) { host_master_adc = v > 1023u ? 1023u : v; }   /* 0..1023 */

/* ---- USB MIDI: in as USB-MIDI event packets (usb.c midi_in_event), out from the SysEx and MIDI OUT rings ---- */
static uint8_t out_buf[65536];                     /* raw MIDI bytes the device sent (SysEx and channel messages) */
static uint32_t out_n, out_lost;
static void out_put(const uint8_t *p, uint32_t n)
{
    if (out_n + n > sizeof out_buf) { out_lost++; return; }
    memcpy(out_buf + out_n, p, n);
    out_n += n;
}
static void twin_drain(void)                       /* usb.c ep1_tx: SysEx first, never split by notes, then MIDI OUT */
{
    while (so_r != so_w) {
        uint32_t pkt = sx_out_q[so_r++ % SXQ], cin = pkt & 15u;
        uint8_t b[3] = {(uint8_t)(pkt >> 8), (uint8_t)(pkt >> 16), (uint8_t)(pkt >> 24)};
        out_put(b, cin == 4u || cin == 7u ? 3u : cin == 6u ? 2u : 1u);
    }
    if (sx_busy)
        return;
    while (mo_r != mo_w) {
        uint32_t pkt = midi_out_q[mo_r++ % MQ], cin = pkt & 15u;
        uint8_t b[3] = {(uint8_t)(pkt >> 8), (uint8_t)(pkt >> 16), (uint8_t)(pkt >> 24)};
        uint32_t len = cin == 0xCu || cin == 0xDu ? 2u : cin >= 8u ? 3u : cin == 0xFu || cin == 5u ? 1u :
                       cin == 2u ? 2u : 3u;          /* the bytes of a USB-MIDI event by its CIN */
        out_put(b, len);
    }
}
static void twin_service(void)                     /* main loop: the screen reader's frame, ed_service, ota_service */
{
#if KARMA_ANN
    const uint8_t *p;
    uint32_t n;
    if (ota_frame_get(&p, &n) && ann_frame(p, n, fm1_ms))
        ota_frame_done();
#endif
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
 * USB-MIDI packets; the editor answers at once (main.c serves the editor in its wait loop). -> bytes in st_out */
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

/* ---- LEDs (lit, by matrix id), screen ---- */
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
EXPORT(st_now) uint32_t st_now(void) { return fm1_ms; }
EXPORT(st_version) const char *st_version(void) { return FELUCCA_VERSION; }
EXPORT(st_fs) uint32_t st_fs(void) { return FS; }
static uint32_t twin_busy(void) { return song.playing || transport_req || ci_on; }
/* bit 0 playing, 1 the song playing (song mode), 2 transport busy (playing, count-in or start pending), 3 recording */
EXPORT(st_transport) uint32_t st_transport(void)
{
    return (song.playing ? 1u : 0u) | (song.playing && arrangement_enabled ? 2u : 0u) | (twin_busy() ? 4u : 0u) |
           (song.rec ? 8u : 0u);
}
/* (optional export, Skiff and later twins) the transport, as the panel moves it (KARMA's editor has no transport
 * command): 0 stop (PLAY while playing), 1 play as the panel's PLAY (the loop, or the song in song mode), 2 the song
 * from the top (song mode on when the song order has parts, then PLAY; otherwise as 1), 3 restart from the top.
 * -> st_transport() */
EXPORT(st_transport_set) uint32_t st_transport_set(uint32_t how)
{
    if (how == 0u) {
        if (twin_busy())
            transport_req = 2;
    } else if (how == 3u) {
        if (song.playing)
            seq_stop();
        transport_req = 1;
    } else if (!song.playing && !transport_req) {
        if (how == 2u && arr_valid(&arrangement, arrangement_ready()))
            arrangement_enabled = 1;
        if (!arrangement_enabled || arr_valid(&arrangement, arrangement_ready()))
            transport_req = 1;
    }
    for (how = 0; how < 20u && transport_req; how++)
        tick();                                    /* the audio ISR takes the request at its next block (<= 3 ms) */
    return st_transport();
}
/* the matrix id of a panel label (panel.c B_*: B_PLAY 10, B_REC 11 …), the learned panel included */
EXPORT(st_panel_button) uint32_t st_panel_button(uint32_t label) { return label < NB ? panel.btn[label] : 0u; }
/* the song's parts A..D (slot 0..3) as SAVE > PARTS loads them (project.c project_load).
 * -> 0 loaded, 1 empty or invalid, 2 refused while the transport runs */
static uint32_t twin_proj_cur = 255u;             /* the part loaded last through st_project_load (255: none) */
EXPORT(st_project_load) uint32_t twin_project_load(uint32_t slot)
{
    if (slot >= 4u) return 1u;
    if (twin_busy()) return 2u;
    if (flash_ok && !proj_ok(&proj_slot[slot]))
        proj_fetch(slot);
    if (!project_used(slot)) return 1u;
    project_load(slot);                            /* (project_apply asks the audio ISR to stop: let it, <= 3 ms) */
    twin_proj_cur = slot;
    for (slot = 0; slot < 20u && transport_req; slot++)
        tick();
    return 0u;
}
EXPORT(st_project_used) uint32_t twin_project_used(uint32_t slot) { return slot < 4u ? (uint32_t)(project_used(slot) != 0) : 0u; }
/* the part playing (SAVE + 5..8 picked it: live_sec), else the one loaded last, else 255 (none) */
EXPORT(st_project_current) uint32_t twin_project_current(void) { return live_sec >= 0 ? (uint32_t)live_sec : twin_proj_cur; }
EXPORT(st_track_mute) uint32_t st_track_mute(uint32_t k) { return k < NTRK ? (uint32_t)(trk[k].p[P_MUTE] != 0) : 0u; }

/* ------------------------------------------------------------ the clock and the sound --- */
#define RING 8192u
static float ring[RING * 2u], out_l[4096], out_r[4096];
static uint32_t samples_due;

static void main_frame(void)                       /* main.c's loop body, once per 15 ms */
{
    int32_t b = fm1_adc_read(FM1_ADC_BATT), a = fm1_adc_read(FM1_ADC_MASTER);
    if (b > 0)
        song.batt_raw = song.batt_raw ? song.batt_raw + (b - song.batt_raw) / 32 : b;
    if (a >= 0) {                                  /* main.c: MASTER, smoothed, applied when it moved */
        uint32_t k10;
        master_knob += (a * 16 - master_knob) / 8;
        k10 = (uint32_t)(master_knob / 16);
        if ((int32_t)k10 - master_applied > 6 || master_applied - (int32_t)k10 > 6) {
            master_applied = (int32_t)k10;
            song.master_q12 = (k10 * k10) >> 8;
        }
    }
    twin_service();
    felucca_dbg.ui_frames++;
    felucca_dbg.page = ui.page;
    felucca_dbg.home = ui.home;
    ui_input();
    ui_leds();
    ui_draw();
#if KARMA_ANN
    speak(fm1_ms);                                 /* the screen reader: what changed */
    ann_tick(fm1_ms);
#endif
    autosave_tick();                               /* the working project into flash, when quiet */
    sections_flush();                              /* live sections / the recorded song, when quiet */
    if (memcmp(fb_seen, host_screen, sizeof fb_seen)) {
        memcpy(fb_seen, host_screen, sizeof fb_seen);
        fb_gen++;
    }
}

static void audio_due(void)                        /* the I2S halves due by now: the audio ISR renders each */
{
    if (in_audio)
        return;
    in_audio = 1;
    samples_due += FS;
    while (samples_due >= 1000u * HALF_FRAMES) {
        uint32_t k;
        const int32_t *o = &abuf[web_half * HALF_WORDS];
        samples_due -= 1000u * HALF_FRAMES;
        if (ring_w - ring_r + HALF_FRAMES > RING)
            ring_r += HALF_FRAMES;                 /* nobody listened: the oldest sound goes */
        fm1_alnk0_irq();
        for (k = 0; k < HALF_FRAMES; k++) {
            uint32_t i = (ring_w++ % RING) * 2u;
            ring[i] = (float)o[2u * k] / 8388608.0f;
            ring[i + 1u] = (float)o[2u * k + 1u] / 8388608.0f;
        }
        web_half ^= 1u;
    }
    in_audio = 0;
}

static void tick(void)                             /* one millisecond of the device */
{
    if (in_tick)
        return;
    in_tick = 1;
    fm1_ms++;
    web_ticks += 1000u * FM1_TICKS_PER_US;
    audio_due();
    if (booted) {
        if (fm1_ms - last_frame >= 15u) {          /* main.c: a frame, then ui_input until 15 ms are over */
            last_frame = fm1_ms;
            main_frame();
        } else {
            ui_input();                             /* main.c's wait loop: ui_input, ed_service */
            twin_service();
        }
    }
    twin_drain();
    in_tick = 0;
}

/* the firmware waits (ota_wire_send, the stores' loops, panel setup): time goes on and the audio ISR runs, which stops
 * the transport at its next block (as on the device, where it preempts the main loop) */
static void twin_wait_ms(void)
{
    if (!booted || in_audio)
        return;
    fm1_ms++;
    web_ticks += 1000u * FM1_TICKS_PER_US;
    audio_due();
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
