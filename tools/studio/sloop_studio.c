/* SPDX-License-Identifier: GPL-3.0-only */
/* SLOOP studio twin: the firmware of the FM-1 (SLOOP, unchanged sources) for the host, as WebAssembly. SLOOP has no
 * browser emulator of its own (its web preview is the DSP and the sequencer only), so this glue follows SLOOP's own
 * host harness of the whole firmware, tests/editor_host.c (the editor's SysEx protocol with the real storage.c /
 * upreset.c / project.c on a RAM image of the 1 MiB NOR, the real usb.c SysEx assembly, the real UI, the audio between
 * frames), and adds what the harness leaves out: main.c's boot (felucca_init, the splash, the autosave resumed), the
 * real audio ISR (audio.c fm1_alnk0_irq), ota.c's identity handshake, the MIDI out ring, the LEDs and the screen:
 *   every millisecond   TIMER4 / fm1_ms, ui_input and ed_service (main.c's wait loop), MIDI out (usb.c's ring)
 *   every 15 ms         main.c's loop body: ed_service, ota_service, ui_input, ui_leds, ui_draw, autosave_tick,
 *                       sections_flush
 *   every 128 frames    audio.c fm1_alnk0_irq, the real one (one I2S half)
 * Built by FM-1 Companion's tools/build_sims.py (target sloop-studio) from SLOOP's tagged sources; nothing of the
 * firmware is modified. Host stand-ins (documented in the NOTICE): the HAL (keys, LEDs, LCD into a frame buffer,
 * ADC = the MASTER value given here, battery full), NOR flash = nor[] (the JEDEC id of the FM-1's part, the ranges of
 * hal/fm1_flash.h, no erase / program timing; the user sample slots, the FM6 bank and the user presets read in place
 * as the XIP window, as editor_host.c does), USB = always configured, SysEx in as USB-MIDI packets, SysEx and MIDI out
 * drained from usb.c's rings; one core (FELUCCA_CPU1 0, as the host tests: the render runs on this thread), no USB
 * audio, no TRS MIDI, no serial console (as the release: FELUCCA_CDC 0); ota.c answers the identity handshake
 * (FELUCCA_ID) but an update request is dropped and its flash hooks refuse; no USB rescue (recovery.c).
 * The twin ABI (st_*: boot, flash, MIDI in/out, panel, LEDs, screen, render, transport, projects) is what
 * fm1companion/sims/_studio/twin-core.js expects, the same set as tools/studio/skiff_studio.c exports, with the
 * optional st_transport_set. Projects 0..3 are SLOOP's sections A..D (SAVE > SECTION A..D). */
#include <stdint.h>

#define FELUCCA_ARRANGER 1               /* (felucca.c) the song order, the sections */
#define FELUCCA_FLASH 1
#define FELUCCA_OTA 1                    /* the editor SysEx plumbing in usb.c, ota.c's handshake, editor.c */
#define FELUCCA_CDC 0                    /* as the release (felucca.c's default since 2.7): no console */
#define FELUCCA_UAC 0
#ifndef FELUCCA_VERSION
#define FELUCCA_VERSION "SLOOP 2.8.5"    /* build_sims.py passes the tag's (ui.c) */
#endif
#ifndef FELUCCA_ID
#define FELUCCA_ID "FM-1_900"            /* build_sims.py passes the package identity (tools/build.py PRODUCT) */
#endif

/* the flash: a RAM image, read where it is (the XIP window), as tests/editor_host.c */
static uint8_t nor[0x100000] __attribute__((aligned(4096)));
#define SMP_USER_XIP(k) ((const uint8_t *)nor + SMP_USER_OFF((uint32_t)(k)))
#define FM6_BANK_XIP(copy) ((const uint8_t *)nor + st_sector(OBJ_FM6BANK, copy) + ST_PAYLOAD_OFF)
#define ST_XIP_PTR(off) ((const uint8_t *)nor + (off))
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
static void fm1_audio_init(int32_t *buf, uint32_t words, void (*isr)(void), uint32_t prio)
{ (void)buf; (void)words; (void)isr; (void)prio; }
static void fm1_audio_stop(void) {}
void isr_alnk0(void) {}
#include "../firmware/src/audio.c"

/* ------------------------------------------------------------ the panel (hal/fm1_input.h) --- */
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
static void fm1_led_key(uint32_t id, int on)          /* (hal/fm1_input.h) the LED of key id */
{
    uint32_t p, r;
    for (p = 0; p < FM1_NCOL; p++)
        for (r = 1; r < 5u; r++)
            if (FM1_KEYMAP[r][p] == (int8_t)id) {
                if (on)
                    fm1_led[p] |= (uint8_t)(1u << r);
                else
                    fm1_led[p] &= (uint8_t)~(1u << r);
            }
}
/* the debounced state as the scan leaves it (hal/fm1_input.h): the host's keys and buttons, the buttons held over
 * MIDI (GLO > MIDI 3 > REMOTE, fm1_remote_btn) read as closed like the scan reads them */
static uint32_t host_buttons, host_pressed, host_released, host_notes;
static int32_t host_enc[7];
static void scan_buttons(void)
{
    uint32_t m = host_buttons | fm1_remote_btn;
    host_pressed |= m & ~fm1_in.buttons;
    host_released |= fm1_in.buttons & ~m;
    fm1_in.buttons = m;
}
static uint32_t fm1_input_edges(uint32_t *released)
{
    uint32_t p;
    scan_buttons();
    p = host_pressed;
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
static uint32_t host_master_adc = 1023;   /* MASTER, 0..1023 as the ADC reads it */
enum { FM1_ADC_BATT = 3, FM1_ADC_MASTER = 4 };
static int32_t fm1_adc_read(uint32_t ch) { return ch == FM1_ADC_MASTER ? (int32_t)host_master_adc : 620; }
static void fm1_adc_init(void) {}

/* ----------------------------------------------------------- the screen --- */
static uint16_t host_screen[240 * 240];  /* RGB565 as the panel gets it (high byte first) */
static void lcd_sync(void) {}
static void lcd_blit(uint32_t x, uint32_t y, uint32_t w, uint32_t h, const uint16_t *p)
{
    uint32_t i, j;
    for (j = 0; j < h && y + j < 240u; j++)
        for (i = 0; i < w && x + i < 240u; i++)
            host_screen[(y + j) * 240u + x + i] = p[j * w + i];
}
static void lcd_init(void) {}
#include "../firmware/src/gfx.c"
static void lcd_fill(uint32_t x, uint32_t y, uint32_t w, uint32_t h, uint16_t c)
{
    uint32_t i, j;
    uint16_t sw = swap16(c);
    for (j = 0; j < h && y + j < 240u; j++)
        for (i = 0; i < w && x + i < 240u; i++)
            host_screen[(y + j) * 240u + x + i] = sw;
}
#include "../firmware/src/panel.c"
#include "../firmware/src/ui.c"
#include "../firmware/src/ui_song.c"
#include "../firmware/src/ui_studio.c"
#include "../firmware/src/icons.c"
#include "../firmware/src/ui_draw.c"
#include "../firmware/src/ui_vis.c"
#include "../firmware/src/ui_layers.c"
#include "../firmware/src/ui_menu.c"
#include "../firmware/src/ui_input.c"

/* ---- the flash (felucca.c's part on the RAM image, the ranges of hal/fm1_flash.h, as tests/editor_host.c) ---- */
#define FL_DATA_LO 0x00097000u
#define FL_DATA_HI 0x000E0000u
#define FL_GLOB_LO 0x000FC000u
#define FL_GLOB_HI 0x000FF000u
#define FL_OTA_LO 0x000E0000u
#define FL_OTA_HI 0x000E5000u
#define FL_FM6_LO 0x000E5000u
#define FL_FM6_HI 0x000E7000u
#define FL_USR4_LO 0x000E7000u
#define FL_USR4_HI 0x000FB000u
#define FL_IN(off, n, lo, hi) ((uint32_t)(off) >= (lo) && (uint32_t)(off) <= (hi) && (uint32_t)(n) <= (hi) - (uint32_t)(off))
#define FL_STORE_OK(off, n) (FL_IN(off, n, FL_DATA_LO, FL_DATA_HI) || FL_IN(off, n, FL_GLOB_LO, FL_GLOB_HI) || \
                             FL_IN(off, n, FL_FM6_LO, FL_FM6_HI) || FL_IN(off, n, FL_USR4_LO, FL_USR4_HI))
#define FL_FAR(fn) fn
#if __has_include("../firmware/hal/fm1_flash_id.h")
#include "../firmware/hal/fm1_flash_id.h"        /* (2.8.5) FL_JEDEC_OK: the flash parts the FM-1 ships with */
#endif
static uint32_t fl_jedec_ram(void) { return 0x856014u; }   /* the FM-1's part (Puya P25Q80) */
static void fl_plain_window_init(void) {}
static uint32_t irq_save(void) { return 0; }
static void irq_restore(uint32_t f) { (void)f; }
static void fl_inval(uint32_t off, uint32_t n) { (void)off; (void)n; }   /* (no XIP cache) */
static int fl_erase4k_ram(uint32_t off, uint32_t *took)
{
    *took = 0;
    if ((off & 0xFFFu) || !FL_STORE_OK(off, 0x1000u))
        return -1;
    memset(nor + off, 0xFF, 0x1000u);
    return 0;
}
static int fl_prog_ram(uint32_t off, const uint8_t *src, uint32_t n, uint32_t *took)   /* NOR: a write only clears bits */
{
    uint32_t i;
    *took = 0;
    if (!FL_STORE_OK(off, n) || n == 0 || n > 256u || ((off & 0xFFu) + n) > 256u)
        return -1;
    for (i = 0; i < n; i++)
        nor[off + i] &= src[i];
    return 0;
}
static int fl_read_ram(uint32_t off, uint8_t *dst, uint32_t n)
{
    if (off >= sizeof nor || n > sizeof nor - off)
        return -1;
    memcpy(dst, nor + off, n);
    return 0;
}
static int fl_write(uint32_t off, const uint8_t *src_ram, uint32_t n)   /* (fm1_flash.h) page by page */
{
    uint32_t took;
    while (n) {
        uint32_t chunk = 256u - (off & 0xFFu);
        int rc;
        if (chunk > n)
            chunk = n;
        rc = fl_prog_ram(off, src_ram, chunk, &took);
        if (rc)
            return rc;
        off += chunk;
        src_ram += chunk;
        n -= chunk;
    }
    return 0;
}
static uint8_t flash_ok;                 /* JEDEC id matched at boot (persist_boot) */
static int st_read(uint32_t off, void *dst, uint32_t n) { return fl_read_ram(off, dst, n); }
static void audio_silence(void)          /* (felucca.c) */
{
    uint32_t i;
    for (i = 0; i < sizeof abuf / sizeof abuf[0]; i++)
        abuf[i] = 0;
}
static int fl_erase4k_quiet(uint32_t off, uint32_t *took) { audio_silence(); return fl_erase4k_ram(off, took); }
static int st_erase(uint32_t off) { uint32_t took; if (!FL_STORE_OK(off, 0x1000u)) return -8; return fl_erase4k_quiet(off, &took); }
static int st_prog(uint32_t off, const void *src, uint32_t n) { if (!FL_STORE_OK(off, n)) return -8; return fl_write(off, src, n); }
#include "../firmware/src/storage.c"
#include "../firmware/src/upreset.c"
#include "../firmware/src/project.c"

/* ---- ota.c: its handshake answers the package identity (FELUCCA_ID) as on the device; the update itself never runs
 * here (usb.ota_req is dropped) and its flash hooks refuse ---- */
static uint32_t ota_now_ms(void) { return fm1_ms; }
static void twin_drain(void);
static void ota_idle(void) { twin_drain(); }      /* ota_wire_send waits for room in the SysEx ring */
static int ota_erase(uint32_t off) { (void)off; return -1; }
static int ota_prog(uint32_t off, const void *p, uint32_t n) { (void)off; (void)p; (void)n; return -1; }
static int ota_fread(uint32_t off, void *p, uint32_t n) { return st_read(off, p, n); }
static void ota_show(uint32_t step, int32_t code) { (void)step; (void)code; }
static void ota_commit(const uint8_t *parm) { (void)parm; }
#include "../firmware/src/ota.c"
#include "../firmware/src/editor.c"
#include "../firmware/src/splash.c"

/* ------------------------------------------------- main.c and its HAL --- */
/* (fm1_main and fm1_cstart are never called here: twin_power_on below runs fm1_main's boot; what the rest of main.c
 * reaches of the hardware is stood in for so that it compiles, the HAL's own way) */
#define FM1_CRASH_MAGIC 0x43525348u
typedef struct {
    uint32_t magic, count, vec, pc, rets, emu, dbg, sp, psr, icfg, uptime_ms;
    uint32_t etm[4];
    uint32_t early;
} fm1_crash_t;                                    /* (hal/fm1_irq.h) */
static fm1_crash_t fm1_crash;
static struct { uint8_t p3_rst; uint32_t rst_src; uint8_t wdt_con; } fm1_boot;   /* (hal/fm1_sys.h) */
uint32_t _data_start[1], _data_end[1], _data_load[1], _bss_start[1], _bss_end[1];
uint32_t _pool_start[1], _pool_end[1], _rt_start[1], _rt_end[1], _rt_load[1];
uint32_t _c1_start[1], _c1_end[1], _c1_load[1], _lr_start[1], _lr_end[1];
char _ustack_lo[1], _ustack_top[1], _sstack_lo[1], _sstack_top[1];
static uint32_t fm1_sp(void) { return 0; }
static uint32_t fm1_ssp(void) { return 0; }
static uint32_t fm1_usp(void) { return 0; }
static void fm1_timer5_ack(void) {}
static void fm1_timer5_start(void (*isr)(void), uint32_t prio) { (void)isr; (void)prio; }
void isr_timer5(void) {}
static void fm1_reboot(void) {}
static void fm1_enter_uboot(void) {}
static void fm1_guard_lock_top(void) {}
static void fm1_irq_enable_all(void) {}
static void fm1_irq_init(void) {}
static void fm1_time_init(void) {}
static void fm1_supplies_stock(void) {}
static void fm1_reset_reason(void) {}
static void fm1_wdt_arm(uint32_t v) { (void)v; }
static void fm1_mailbox_clear(void) {}
static void fm1_guard_enable(uint32_t m) { (void)m; }
enum { FM1_GUARD_STACK = 1, FM1_GUARD_WRITE = 2, FM1_GUARD_BUS = 4, FM1_GUARD_PC = 8 };
static uint8_t fm1_c1_on;                         /* one core (FELUCCA_CPU1 0, as the host tests) */
static struct { uint32_t bad; } fm1_c1_mb;
static uint8_t cpu1_state;
static uint32_t cpu1_timeouts;
static void fm1_cpu1_bar(void) {}
static void fm1_cpu1_halt(void) {}
static void cpu1_boot(void) {}
static int recovery_key(void) { return 0; }       /* (no USB rescue in the twin: recovery.c is not part of it) */
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

static uint32_t booted, boot_ms, last_frame, fb_gen, in_tick, in_audio;
static uint16_t fb_seen[240 * 240];

/* main.c fm1_main's boot, up to the main loop (with USB configured and the editor live) */
static void twin_power_on(void)
{
    persist_boot();
    if (flash_ok)
        ota_boot_cleanup();                       /* (main.c) the staging area of an update */
    settings_init();
    lcd_init();
    sloop_splash();                               /* the SLOOP logo (splash.c) */
    memset(&felucca_dbg, 0, sizeof felucca_dbg);
    felucca_dbg.magic = DBG_MAGIC;
    felucca_dbg.boots++;
    fm1_input_init();
    fm1_adc_init();
    panel_init();
    felucca_init();                               /* main.c: the parts, the drum track, the project as it was left */
    cpu1_boot();
    audio_init();
    usb.config = 1;                               /* plugged into the Suite: the editor protocol is live */
    boot_ms = fm1_ms + 30u + 900u;                /* main.c: 30 ms, the panel setup check, the logo's 900 ms */
    booted = 1;
}

EXPORT(st_flash) uint8_t *st_flash(void) { return nor; }          /* the whole NOR image (write it before st_boot) */
EXPORT(st_flash_size) uint32_t st_flash_size(void) { return sizeof nor; }
EXPORT(st_flash_erase) void st_flash_erase(void) { memset(nor, 0xFF, sizeof nor); }   /* a new, empty device */

static void tick(void);
static uint32_t ring_w, ring_r;
/* fm1_main up to the main loop, the splash included (run here, its sound dropped: the twin answers its panel at once,
 * as the other twins do); 1 = flash found */
EXPORT(st_boot) uint32_t st_boot(uint32_t master)
{
    if (booted)
        return flash_ok;
    host_master_adc = master > 1023u ? 1023u : master;
    twin_power_on();
    while (boot_ms) {
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
    host_buttons = m & ((1u << 14) - 1u);
    scan_buttons();
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
static void twin_drain(void)                       /* usb.c's sender: SysEx first, then the MIDI OUT events */
{
    while (so_r != so_w) {
        uint32_t pkt = sx_out_q[so_r++ % SXQ], cin = pkt & 15u;
        uint8_t b[3] = {(uint8_t)(pkt >> 8), (uint8_t)(pkt >> 16), (uint8_t)(pkt >> 24)};
        out_put(b, cin == 4u || cin == 7u ? 3u : cin == 6u ? 2u : 1u);
    }
    if (sx_busy)
        return;
    while (mo_r != mo_w) {
        uint32_t pkt = midi_out_q[mo_r++ % MQ], cin = pkt & 15u, len;
        uint8_t b[3];
        len = cin == 0xCu || cin == 0xDu ? 2u : cin >= 8u ? 3u : cin == 0xFu || cin == 5u ? 1u : cin == 2u ? 2u : 3u;
        b[0] = (uint8_t)(pkt >> 8);
        b[1] = (uint8_t)(pkt >> 16);
        b[2] = (uint8_t)(pkt >> 24);
        out_put(b, len);
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
/* bit 0 playing, 1 song running (song mode: the song order plays), 2 transport busy (playing, count-in or start /
 * stop pending), 3 recording */
EXPORT(st_transport) uint32_t st_transport(void)
{
    return (song.playing ? 1u : 0u) | (song.playing && arrangement_clock.running ? 2u : 0u) |
           (song.playing || transport_req || ci_on ? 4u : 0u) | (song.rec ? 8u : 0u);
}
/* (optional export) the transport as the panel and the editor's LIVE page move it: 0 stop (seq.c's STOP at the next
 * audio block, the twin's clock runs until then: the editor's flash writes need it stopped), 1 play as the PLAY button
 * in the mode the twin is in (loop or song mode), 2 the song from the top (SAVE layer > SONG MODE, then PLAY: the
 * song order of the settings; with an empty section in it: the loop, as 1), 3 restart from the top. -> st_transport() */
EXPORT(st_transport_set) uint32_t st_transport_set(uint32_t how)
{
    uint32_t i;
    if (how == 0u || how == 3u) {
        if (song.playing || transport_req || ci_on) {
            transport_req = 2;
            ci_on = 0;
            for (i = 0; i < 100u && (song.playing || transport_req); i++)
                twin_wait_ms();
        }
        if (how == 0u)
            return st_transport();
    }
    if (!song.playing && !transport_req) {
        if (how == 2u)
            arrangement_enabled = (uint8_t)arr_valid(&arrangement, arrangement_ready());
        if (!(arrangement_enabled && !arr_valid(&arrangement, arrangement_ready())))
            transport_req = 1;
    }
    return st_transport();
}
/* the matrix id of a panel label (panel.c B_*: B_PLAY 10, B_REC 11 …), the learned panel included */
EXPORT(st_panel_button) uint32_t st_panel_button(uint32_t label) { return label < NB ? panel.btn[label] : 0u; }
/* the sections A..D (slot 0..3) as SAVE > SECTION loads them (project.c project_load).
 * -> 0 loaded, 1 empty or invalid, 2 refused while the transport runs */
EXPORT(st_project_load) uint32_t twin_project_load(uint32_t slot)
{
    if (slot >= 4u || !project_used(slot)) return 1u;
    if (song.playing || transport_req) return 2u;
    project_load(slot);
    return 0u;
}
EXPORT(st_project_used) uint32_t twin_project_used(uint32_t slot) { return slot < 4u ? (uint32_t)(project_used(slot) != 0) : 0u; }
EXPORT(st_project_current) uint32_t twin_project_current(void) { return live_sec >= 0 ? (uint32_t)live_sec : 255u; }
EXPORT(st_track_mute) uint32_t st_track_mute(uint32_t k) { return k < NTRK ? (uint32_t)(trk[k].p[P_MUTE] != 0) : 0u; }

/* ------------------------------------------------------------ the clock and the sound --- */
#define RING 8192u
static float ring[RING * 2u], out_l[4096], out_r[4096];
static uint32_t samples_due;

static void main_frame(void)                       /* main.c's loop body, once per 15 ms */
{
    int32_t b = fm1_adc_read(FM1_ADC_BATT), a = fm1_adc_read(FM1_ADC_MASTER);
    static int32_t knob = 512 * 16;
    if (b > 0)
        song.batt_raw = song.batt_raw ? song.batt_raw + (b - song.batt_raw) / 32 : b;
    if (a >= 0) {                                  /* main.c: MASTER, or the LIVE page's level until the knob turns */
        uint32_t k10;
        knob += (a * 16 - knob) / 8;
        k10 = (uint32_t)(knob / 16);
        if (ed_master) {
            if (ed_master_k == 0xFFFFu)
                ed_master_k = (uint16_t)k10;
            if (k10 + 48u < ed_master_k || k10 > ed_master_k + 48u)
                ed_master = 0;
            else
                k10 = (ed_master - 1u) * 1023u / 126u;
        }
        song.master_q12 = (k10 * k10) >> 8;
    }
    twin_service();
    felucca_dbg.ui_frames++;
    felucca_dbg.page = ui.page;
    felucca_dbg.home = ui.home;
    ui_input();
    ui_leds();
    ui_draw();
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
    in_tick = 1;
    fm1_ms++;
    web_ticks += 1000u * FM1_TICKS_PER_US;
    audio_due();
    if (booted && (int32_t)(fm1_ms - boot_ms) >= 0) {
        if (boot_ms) {                             /* main.c: the logo goes, the loop starts */
            boot_ms = 0;
            lcd_fill(0, 0, 240, 240, C_BLACK);
            last_frame = fm1_ms - 15u;
        }
        if (fm1_ms - last_frame >= 15u) {          /* main.c: a frame, then ui_input until 15 ms are over */
            last_frame = fm1_ms;
            main_frame();
        } else {
            ui_input();                             /* main.c's wait loop: ui_input, ed_service */
            twin_service();
        }
    } else {
        twin_service();
    }
    twin_drain();
    in_tick = 0;
}

/* the firmware waits (ota_wire_send, the stores' loops, a STOP asked for): time goes on and the audio ISR runs, which
 * stops the transport at its next block (as on the device, where it preempts the main loop) */
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
