/* SPDX-License-Identifier: GPL-3.0-only */
/* Skiff studio twin: the firmware of the FM-1 (Skiff, unchanged sources) for the host, as WebAssembly, with what the
 * browser emulator (web/emu/felucca_web.c) leaves out: the USB SysEx path with the editor protocol (usb.c sysex_byte ->
 * editor.c ed_service -> ota_wire_send, as tests/editor_test.c drives it) and the MIDI out ring. The NOR flash is
 * the emulator's: a 1 MiB RAM image behind storage.c's hooks (settings, projects, user presets, the pattern bank,
 * the autosave and the user sample slots, read in place as XIP). The boot follows firmware/src/main.c fm1_main as
 * felucca_web.c does (web_power_on), the clock and the audio ISR follow felucca_web.c:
 *   every millisecond   TIMER4 / fm1_ms, ui_input (main.c's wait loop), ed_service, MIDI out due (usb.c mo_take)
 *   every 15 ms         main.c's loop body: master_poll, ui_input, settings_poll, autosave_poll, ui_leds, ui_draw
 *   every 128 frames    audio.c fm1_alnk0_irq, the real one (one I2S half)
 * Built by FM-1 Companion's tools/build_sims.py (target skiff-studio) from Skiff's tagged sources; nothing of the
 * firmware is modified. Host stand-ins (documented in the NOTICE): the HAL (keys, LEDs, LCD into a frame buffer,
 * ADC = the MASTER value given here, battery full), NOR flash = nor[] (JEDEC id of the expected part, no erase /
 * program timing), USB = always configured, SysEx in as USB-MIDI packets, SysEx and MIDI out drained from usb.c's
 * rings; one core (MENU > CPU 2 CORES is accepted but core 1 never answers, as felucca_web.c), no 320 MHz switch;
 * ota.c answers the identity handshake (FELUCCA_ID) but an update request is dropped and its flash hooks refuse;
 * no USB audio, no TRS MIDI, no serial console (as a release build: FELUCCA_CDC 0).
 * The twin ABI (st_*: boot, flash, MIDI in/out, panel, LEDs, screen, render, transport, projects) is what
 * fm1companion/sims/_studio/twin-core.js expects, the same set as tools/studio/melodee_studio.c exports, plus the
 * optional st_transport_set (Skiff's editor TRANSPORT command differs from Melodee's: the host uses this export when
 * a twin has it). */
#include <stdint.h>

#define FELUCCA_WEB 1                    /* (the host builds' branches, as the emulator) */
#define FELUCCA_CDC 0                    /* as a release (build.py --release): no console, no USB SERIAL row */
#define FELUCCA_FLASH 1
#define FELUCCA_OTA 1                    /* the editor SysEx plumbing in usb.c, ota.c's handshake, editor.c */
#define FELUCCA_UAC 0
#define FELUCCA_UART 0
#ifndef FELUCCA_VERSION
#define FELUCCA_VERSION "v2.2"           /* build_sims.py passes the tag's (felucca.c) */
#endif
#ifndef FELUCCA_ID
#define FELUCCA_ID "FM-1_700"            /* build_sims.py passes the release identity (build.py --release: FM-1_7XY) */
#endif

static uint8_t nor[0x100000];            /* the SPI NOR (erased: 0xFF) */
#define SMP_USER_XIP(k) ((const uint8_t *)nor + SMP_USER_BASE + (k) * SMP_USER_SIZE)   /* eng_sample.c */
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

/* ------------------------------------------------------------ the panel (as felucca_web.c) --- */
#define FM1_NCOL 11u
static const int8_t FM1_KEYMAP[6][FM1_NCOL] = {      /* hal/fm1_input.h: key id at (column, row bit) */
    {-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1},
    { 5, 11,  4, 10,  3,  9,  2,  8, -1, -1, -1},
    {34, 35, 36, 37, 38, 40, 39, 13,  7,  6, 12},
    {23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33},
    { 0,  1, 15, 14, 17, 16, 19, 18, 20, 21, 22},
    {-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1},
};
static uint8_t fm1_led[FM1_NCOL], fm1_led_dim[FM1_NCOL], fm1_led_breath[FM1_NCOL], fm1_led_mid[FM1_NCOL];
static uint32_t web_dim_lo;
static void fm1_led_dim_level(uint32_t lo) { web_dim_lo = lo; }
/* the power-on LED sweep (hal/fm1_led_anim.h): run by the clock as the scan does; at its end, or on a key or a button
 * down, the last picture and the UI's LEDs from the next frame (felucca_web.c) */
#include "../firmware/hal/fm1_led_anim.h"
static uint32_t web_anim_t0, web_anim_glow, web_anim_run;
static uint32_t web_anim_frame(void) { return (web_ticks - web_anim_t0) / (1100u * FM1_TICKS_PER_US); }
static void fm1_led_anim_start(uint32_t glow)
{
    memset(fm1_led, 0, sizeof fm1_led);
    memset(fm1_led_dim, 0, sizeof fm1_led_dim);
    memset(fm1_led_breath, 0, sizeof fm1_led_breath);
    memset(fm1_led_mid, 0, sizeof fm1_led_mid);
    web_anim_t0 = web_ticks;
    web_anim_glow = glow != 0u;
    web_anim_run = 1;
}
static int fm1_led_anim_on(void)
{
    if (web_anim_run && (web_anim_frame() >= FM1_ANIM_FRAMES || fm1_in.notes || fm1_in.buttons)) {
        uint32_t c, r;
        web_anim_run = 0;
        for (c = 0; c < FM1_NCOL; c++)
            for (r = 1; r < 5u; r++)
                if (web_anim_glow && FM1_KEYMAP[r][c] >= 0)
                    fm1_led_dim[c] |= (uint8_t)(1u << r);
    }
    return (int)web_anim_run;
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
static void fm1_irq_off(void) {}
static void fm1_irq_on(void) {}

/* ----------------------------------------------------------- the screen --- */
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
static void lcd_power(uint32_t s)        /* MENU > SCREEN OFF (lcd.c): off, the screen dark */
{
    if (!s)
        memset(host_screen, 0, sizeof host_screen);
}
static void lcd_wake_now(void) {}
static void lcd_init(void) {}

#include "../firmware/src/gfx.c"
#include "../firmware/src/panel.c"
#include "../firmware/src/ui.c"
#include "../firmware/src/menu_items.c"
#include "../firmware/src/icons.c"
#include "../firmware/src/ui_graph.c"
#include "../firmware/src/ui_draw.c"
#include "../firmware/src/ui_menu.c"
#include "../firmware/src/ui_input.c"
#include "../firmware/src/ui_layer.c"

/* ------------------------------------------------------------ the flash (as felucca_web.c) --- */
static uint8_t flash_ok;
static int st_read(uint32_t off, void *dst, uint32_t n)
{
    if (off >= sizeof nor || n > sizeof nor - off)
        return -1;
    memcpy(dst, nor + off, n);
    return 0;
}
static int st_erase(uint32_t off)
{
    if (off >= sizeof nor || 4096u > sizeof nor - off)
        return -8;
    memset(nor + (off & ~4095u), 0xFF, 4096);
    return 0;
}
static int st_prog(uint32_t off, const void *src, uint32_t n)       /* NOR: a write only clears bits */
{
    const uint8_t *s = src;
    uint32_t i;
    if (off >= sizeof nor || n > sizeof nor - off)
        return -8;
    for (i = 0; i < n; i++)
        nor[off + i] &= s[i];
    return 0;
}
static uint32_t irq_save(void) { return 0; }
static void irq_restore(uint32_t f) { (void)f; }
static uint32_t fl_jedec_ram(void) { return 0x856014u; }             /* the FM-1's part (project.c) */
static void fl_plain_window_init(void) {}
#define FL_FAR(fn) (fn)
#include "../firmware/src/storage.c"
#include "../firmware/src/upreset.c"
#include "../firmware/src/project.c"
#include "../firmware/src/bank.c"

/* ---- the editor's flash hooks (storage_hw.c / fm1_flash.h on the device): the user sample slots in nor[] ---- */
static void audio_silence(void) {}
static int fl_erase4k(uint32_t off, uint32_t *took)
{
    *took = 0;
    if (off < SMP_USER_BASE || off >= SMP_USER_BASE + SMP_USER_SLOTS * SMP_USER_SIZE || (off & 4095u))
        return -8;                                    /* (the editor erases only inside the sample slots) */
    return st_erase(off);
}
static int fl_write(uint32_t off, const void *p, uint32_t n)
{
    if (off < SMP_USER_BASE || n > SMP_USER_BASE + SMP_USER_SLOTS * SMP_USER_SIZE - off)
        return -8;
    return st_prog(off, p, n);
}
static void fl_inval(uint32_t off, uint32_t n) { (void)off; (void)n; }   /* (no XIP cache) */

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

/* ------------------------------------------------- main.c and its HAL (as felucca_web.c) --- */
#if FELUCCA_CPU2
typedef struct { uint32_t magic, count, vec, pc, emu, dbg, rets, core; } fm1_crash_t;
#else
typedef struct { uint32_t magic, count, vec, pc, emu, dbg, rets; } fm1_crash_t;
#endif
#define FM1_CRASH_MAGIC 0x43525348u
static fm1_crash_t fm1_crash;                     /* (none: main.c boot_leds runs the sweep) */
static struct { uint8_t p3_rst, wdt_con; uint32_t rst_src; } fm1_boot;
uint32_t _data_start[1], _data_end[1], _data_load[1], _bss_start[1], _bss_end[1];
uint32_t _pool_start[1], _pool_end[1], _rt_start[1], _rt_end[1], _rt_load[1], _ramx_start[1], _ramx_end[1];
static uint32_t host_master_adc = 1023;   /* MASTER, 0..1023 as the ADC reads it */
enum { FM1_ADC_BATT = 3, FM1_ADC_MASTER = 4 };
static int32_t fm1_adc_read(uint32_t ch) { return ch == FM1_ADC_MASTER ? (int32_t)host_master_adc : 620; }
static void fm1_adc_init(void) {}
static void fm1_timer5_ack(void) {}
static void fm1_timer5_start(void (*isr)(void), uint32_t prio) { (void)isr; (void)prio; }
void isr_timer5(void) {}
static void fm1_reboot(void) {}
static void fm1_enter_uboot(void) {}
static void fm1_guard_lock_top(void) {}
static void fm1_irq_enable_all(void) {}
static void fm1_irq_init(void) {}
static void fm1_time_init(void) {}
static void fm1_reset_reason(void) {}
static void fm1_wdt_arm(uint32_t v) { (void)v; }
static void fm1_mailbox_clear(void) {}
static void fm1_guard_enable(uint32_t m) { (void)m; }
enum { FM1_GUARD_STACK = 1, FM1_GUARD_WRITE = 2, FM1_GUARD_BUS = 4, FM1_GUARD_PC = 8 };
#if FELUCCA_CPU2
static uint8_t fm1_c1_on, fm1_c1_gave_up;         /* one core whatever MENU > CPU says (felucca_web.c) */
static void fm1_cpu1_halt(void) {}
static int fm1_cpu1_start(void) { return -1; }
static void fm1_guard_pc_open(void) {}
#endif
enum { FM1_CLK320_EALREADY = 1, FM1_CLK320_EMODE };  /* MENU > CPU CLOCK 320 MHZ: no PLL here, refused */
static int fm1_clk320_go(uint32_t *t) { t[0] = t[1] = 0; return FM1_CLK320_EMODE; }
/* main.c's calls into the USB driver (registers): nothing here (usb.c's own functions stay as they are) */
#define usb_poll() ((void)0)
#define usb_start() ((void)0)
#define usb_retry(ms) ((void)(ms))
#define usb_detach() ((void)0)
#define ep1_tx() ((void)0)                       /* (TIMER5's MIDI OUT sender: twin_drain below) */
#include "../firmware/src/master.c"
#include "../firmware/src/main.c"
#undef ep1_tx
#undef usb_poll
#undef usb_start
#undef usb_retry
#undef usb_detach

#undef __attribute__                              /* (hostsim.c drops attributes for the firmware files) */
#define EXPORT(name) __attribute__((used, export_name(#name)))

static uint32_t booted, boot_ms, last_frame, fb_gen, in_tick, in_audio;
static uint16_t fb_seen[240 * 240];

/* main.c fm1_main's boot, up to the main loop (felucca_web.c web_power_on, with USB configured and the OTA hook) */
static void twin_power_on(void)
{
    persist_boot();
    bank_boot();
    if (settings_init())                          /* a new unit: BPM LOCK on (main.c) */
        ui_prefs |= PREF_BPM_LOCK;
    usb_serial_apply();
    lcd_init();
    draw_splash();
    memset(&felucca_dbg, 0, sizeof felucca_dbg);
    felucca_dbg.magic = DBG_MAGIC;
    fm1_input_init();
    fm1_adc_init();
    panel_init();
    felucca_init();
    master_boot();
    kb_boot_hold = 1;                             /* main.c (#137): the keys silent under the splash */
    audio_init();
    usb.up = 1;                                   /* plugged into the Suite: the editor protocol is live */
    usb.config = 1;
    boot_leds();
    if (ui_set_rev < 1u) {                        /* main.c (Skiff U1) */
        ui_prefs |= PREF_BPM_LOCK;
        ui_set_rev = 1;
        settings_save();
    }
#if FELUCCA_LEGACY
    proj_migrate(boot_clean);
#endif
    unit_g_adopt = ui_set_rev < 2u;               /* main.c: (Skiff U3, R8) the unit's CLOCK MIDI IN TUNE */
    autosave_boot(boot_clean);                    /* main.c: the last session's music (1.2, project.c) */
    unit_g_boot();
    boot_ms = fm1_ms + 430u;                      /* main.c: 30 + 400 ms before the first frame */
    booted = 1;
}

EXPORT(st_flash) uint8_t *st_flash(void) { return nor; }          /* the whole NOR image (write it before st_boot) */
EXPORT(st_flash_size) uint32_t st_flash_size(void) { return sizeof nor; }
EXPORT(st_flash_erase) void st_flash_erase(void) { memset(nor, 0xFF, sizeof nor); }   /* a new, empty device */

static void tick(void);
static uint32_t ring_w, ring_r;
/* fm1_main up to the main loop, the splash's 430 ms included (run here, their sound dropped: the twin answers its
 * panel at once, as Melodee's does); 1 = flash found */
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
static void twin_drain(void)                       /* usb.c ep1_tx: SysEx first, then the MIDI OUT events due */
{
    uint8_t p[64];
    uint32_t n, i;
    while (so_r != so_w) {
        uint32_t pkt = sx_out_q[so_r++ % SXQ], cin = pkt & 15u;
        uint8_t b[3] = {(uint8_t)(pkt >> 8), (uint8_t)(pkt >> 16), (uint8_t)(pkt >> 24)};
        out_put(b, cin == 4u || cin == 7u ? 3u : cin == 6u ? 2u : 1u);
    }
    if (sx_busy)
        return;
#ifdef MQO                                         /* (2.2) the events whose audio plays by now */
    while ((n = mo_take(p, sizeof p, web_ticks)) != 0u)
#else                                              /* (up to 2.1.1) the ring as it is */
    for (n = 0; mo_r != mo_w && n + 4u <= sizeof p; n += 4u) {
        uint32_t pkt = midi_out_q[mo_r++ % MQ];
        p[n] = (uint8_t)pkt;
        p[n + 1u] = (uint8_t)(pkt >> 8);
        p[n + 2u] = (uint8_t)(pkt >> 16);
        p[n + 3u] = (uint8_t)(pkt >> 24);
    }
    if (n)
#endif
        for (i = 0; i < n; i += 4u) {
            uint32_t cin = p[i] & 15u;            /* the bytes of a USB-MIDI event by its CIN */
            uint32_t len = cin == 0xCu || cin == 0xDu ? 2u : cin >= 8u ? 3u : cin == 0xFu || cin == 5u ? 1u :
                           cin == 2u ? 2u : 3u;
            out_put(p + i + 1u, len);
        }
}
#ifdef MQO
#define TWIN_MO_NOW(t) (mo.now = (t))              /* TIMER5's tick: the main loop's events are due at once */
#else
#define TWIN_MO_NOW(t) ((void)(t))
#endif
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
    TWIN_MO_NOW(web_ticks);
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

/* ---- LEDs (felucca_web.c: lit, by matrix id; PLAY's green half lights PLAY), screen ---- */
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
    if ((fm1_led[LED_PLAY_GREEN >> 3] >> (LED_PLAY_GREEN & 7u)) & 1u)
        m |= 1u << panel.btn[B_PLAY];
    return m;
}
EXPORT(st_fb) uint16_t *st_fb(void) { return host_screen; }   /* RGB565, the panel's byte order */
EXPORT(st_fb_gen) uint32_t st_fb_gen(void) { return fb_gen; }
EXPORT(st_now) uint32_t st_now(void) { return fm1_ms; }
EXPORT(st_version) const char *st_version(void) { return FELUCCA_VERSION; }
EXPORT(st_fs) uint32_t st_fs(void) { return FS; }
/* bit 0 playing, 1 song running (SONG ON / ONCE), 2 transport busy (playing, count-in or start pending), 3 recording */
EXPORT(st_transport) uint32_t st_transport(void)
{
    return (song.playing ? 1u : 0u) | (song.playing && lists.song != SONG_OFF ? 2u : 0u) |
           (transport_busy() ? 4u : 0u) | (song.rec ? 8u : 0u);
}
/* (optional export, Skiff and later twins) the transport as the editor's TRANSPORT command moves it
 * (editor_project.c): 0 stop, 1 play as the panel's PLAY (SONG ONCE from the top, a chain from its first part),
 * 2 the song from the top (as 1), 3 restart from the top. -> st_transport() */
EXPORT(st_transport_set) uint32_t st_transport_set(uint32_t how)
{
    if (how == 0u) {
        if (transport_busy())
            transport_stop_now();
    } else if (how == 3u) {
        transport_req = 3;
    } else if (!song.playing && !seq_counting()) {
        if (lists.song == SONG_ONCE)
            song_top();
        else {
            chain_top();
            transport_req = 1;
        }
    }
    return st_transport();
}
/* the matrix id of a panel label (panel.c B_*: B_PLAY 10, B_REC 11 …), the learned panel included */
EXPORT(st_panel_button) uint32_t st_panel_button(uint32_t label) { return label < NB ? panel.btn[label] : 0u; }
/* projects A..D (slot 0..3) as the PROJECT page loads them (project.c project_load).
 * -> 0 loaded, 1 empty or invalid, 2 refused while the transport runs */
EXPORT(st_project_load) uint32_t twin_project_load(uint32_t slot)
{
    if (slot >= 4u || !project_used(slot)) return 1u;
    if (transport_busy()) return 2u;
    project_load(slot);
    return 0u;
}
EXPORT(st_project_used) uint32_t twin_project_used(uint32_t slot) { return slot < 4u ? (uint32_t)(project_used(slot) != 0) : 0u; }
EXPORT(st_project_current) uint32_t twin_project_current(void) { return proj_cur; }
EXPORT(st_track_mute) uint32_t st_track_mute(uint32_t k) { return k < NTRK ? (uint32_t)(trk[k].p[P_MUTE] != 0) : 0u; }

/* ------------------------------------------------------------ the clock and the sound --- */
#define RING 8192u
static float ring[RING * 2u], out_l[4096], out_r[4096];
static uint32_t samples_due;

static void main_frame(void)                       /* main.c's loop body, once per 15 ms */
{
    int32_t b = fm1_adc_read(FM1_ADC_BATT);
    if (b > 0)
        song.batt_raw = song.batt_raw ? song.batt_raw + (b - song.batt_raw) / 32 : b;
    master_poll();
    twin_service();
    felucca_dbg.ui_frames++;
    ui_input();
    settings_poll();
    autosave_poll();
    ui_leds();
    ui_draw();
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

static void tick(void)                             /* one millisecond of the device (felucca_web.c web_ms) */
{
    in_tick = 1;
    fm1_ms++;
    web_ticks += 1000u * FM1_TICKS_PER_US;
    TWIN_MO_NOW(web_ticks);
    audio_due();
    if (booted && (int32_t)(fm1_ms - boot_ms) >= 0) {
        if (boot_ms) {                             /* main.c: the splash goes, the loop starts */
            boot_ms = 0;
            kb_boot_hold = 0;
            lcd_fill(0, 0, 240, 240, T_BG);
            scrn.idle = fm1_ms;
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

/* the firmware waits (ed_flash_stop, ota_wire_send, the stores' loops): time goes on and the audio ISR runs, which
 * stops the transport at its next block (as on the device, where it preempts the main loop) */
static void twin_wait_ms(void)
{
    if (!booted || in_audio)
        return;
    fm1_ms++;
    web_ticks += 1000u * FM1_TICKS_PER_US;
    TWIN_MO_NOW(web_ticks);
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
