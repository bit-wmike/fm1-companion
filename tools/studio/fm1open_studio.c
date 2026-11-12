/* SPDX-License-Identifier: GPL-3.0-only */
/* FM1 Open studio twin: M-VAVE's open FM-1 firmware "FM1 Open" (app/ with the open web engine web/, unchanged
 * sources) for the host, as WebAssembly. It is FM1 Open's own browser simulator (web/engine/Simulator.c, which
 * tools/Build-Web.ps1 builds with -DFM1_HOST_TEST -DFM1_OPEN_WEB: the APP's screen, sequencer, projects, DX7 library,
 * sample bank and file transfer protocol on the open engine; the user flash partition as a RAM array), included whole,
 * plus what the simulator leaves to its page: the twin ABI below.
 *   USB MIDI in    channel / realtime messages and SysEx go through Simulator.c's sim_midi_from (USB port): the APP's
 *                  own fm1_app_midi_received and fm1_app_sysex, so the file transfer protocol (app/transfer.c:
 *                  F0 7D 'F' 'M' 02 ...: projects, slots, DX7 library banks, the sample bank) answers as on the device;
 *                  the SDK's identity request (F0 + 7-bit stream of 00 59 11 ..., the update handshake) answers the
 *                  release's package identity (app/version.h FM1_UPDATE_PRODUCT); every other update command is
 *                  dropped (no updater in the twin).
 *   USB MIDI out   everything the APP sends to the USB port (fm1_midi_send_port FM1_MIDI_USB: transfer replies, the
 *                  tracks' MIDI out) is collected for st_out; Simulator.c's own test capture is replaced.
 *   the clock      Simulator.c's sim_render: one 128-frame block of the engine, the APP's control work every 10 ms.
 *   the flash      st_flash is a 1 MiB image like the other twins; FM1 Open's user partition (536576 bytes:
 *                  projects, settings, the sample bank, the DX7 library, the tunings) is its first part. On the device
 *                  the SDK places that partition in the NOR; its address is not part of the open sources.
 * Built by FM-1 Companion's tools/build_sims.py (target fm1-open-studio) from FM1 Open's sources at a commit; nothing of
 * the firmware is modified. Not in the twin (as in M-VAVE's simulator): the device engine of the binary SDK
 * (libfm1_engine.a; the open web engine stands in for it), Bluetooth, USB audio, TRS MIDI, the updater. */
#include <stdint.h>
#include <string.h>
#include "fm1.h"

/* Simulator.c's MIDI out goes to its optional test capture; the twin takes the APP's output itself (below) */
#define fm1_midi_send_port fm1c_sim_send_port
#define fm1_midi_send fm1c_sim_send
#include "Simulator.c"
#undef fm1_midi_send_port
#undef fm1_midi_send
#undef EXPORT

#ifndef FM1C_VERSION
#define FM1C_VERSION "V14"                 /* build_sims.py passes app/version.h FM1_RELEASE_VERSION */
#endif
#ifndef FM1C_ID
#define FM1C_ID "FM-1_8000014"             /* build_sims.py passes app/version.h FM1_UPDATE_PRODUCT */
#endif

#define ST_EXPORT(name) __attribute__((used, export_name(#name)))

/* ------------------------------------------------------------------ USB MIDI out */
static uint8_t out_buf[65536];
static uint32_t out_n, out_lost;

int fm1_midi_send_port(unsigned port, const uint8_t *data, size_t bytes)
{
    if (port >= 3 || !data || !bytes || bytes > 128)
        return -1;
    if (port != FM1_MIDI_USB)
        return 0;                                  /* (DIN / BLE: no cable in the twin) */
    if (out_n + bytes > sizeof out_buf) {
        out_lost++;
        return -2;                                 /* full: the APP retries (transfer.c) */
    }
    memcpy(out_buf + out_n, data, bytes);
    out_n += (uint32_t)bytes;
    return 0;
}
int fm1_midi_send(const uint8_t *data, size_t bytes) { return fm1_midi_send_port(FM1_MIDI_USB, data, bytes); }

ST_EXPORT(st_out) uint8_t *st_out(void) { return out_buf; }
ST_EXPORT(st_out_len) uint32_t st_out_len(void) { return out_n; }
ST_EXPORT(st_out_lost) uint32_t st_out_lost(void) { return out_lost; }
ST_EXPORT(st_out_clear) void st_out_clear(void) { out_n = 0; }

/* ------------------------------------------------------------------ the flash */
#define NOR_BYTES 0x100000u
static uint8_t nor[NOR_BYTES];
static uint32_t nor_seen;
static int nor_valid;
_Static_assert(sizeof flash <= NOR_BYTES, "user partition fits the image");

static void nor_pull(void)                         /* Simulator.c's partition -> the image (after it changed) */
{
    if (nor_valid && nor_seen == flash_revision)
        return;
    memcpy(nor, flash, sizeof flash);
    nor_seen = flash_revision;
    nor_valid = 1;
}
ST_EXPORT(st_flash) uint8_t *st_flash(void) { return nor; }        /* write it before st_boot */
ST_EXPORT(st_flash_size) uint32_t st_flash_size(void) { return NOR_BYTES; }
ST_EXPORT(st_flash_erase) void st_flash_erase(void) { memset(nor, 0xFF, sizeof nor); nor_valid = 0; }

/* ------------------------------------------------------------------ the frame */
static uint16_t fb[240 * 240];                     /* the panel's byte order (big-endian RGB565) */
static uint32_t fb_gen, fb_rev = ~0u;
ST_EXPORT(st_fb) uint16_t *st_fb(void) { return fb; }
ST_EXPORT(st_fb_gen) uint32_t st_fb_gen(void)
{
    if (fb_rev != frame_revision) {
        fb_rev = frame_revision;
        for (unsigned i = 0; i < 240u * 240u; ++i)
            fb[i] = (uint16_t)((pixels[i] << 8) | (pixels[i] >> 8));
        fb_gen++;
    }
    return fb_gen;
}

/* ------------------------------------------------------------------ audio and time */
static float out_l[4096], out_r[4096];
static const float *blk;
static unsigned blk_pos = FM1_BLOCK_FRAMES;
static uint32_t frac;                              /* run_ms: frames still owed (x1000) */

static void advance(uint32_t n, int keep)
{
    for (uint32_t i = 0; i < n; ++i) {
        if (blk_pos >= FM1_BLOCK_FRAMES) {
            blk = sim_render();
            blk_pos = 0;
        }
        if (keep) {
            out_l[i] = blk[2u * blk_pos];
            out_r[i] = blk[2u * blk_pos + 1u];
        }
        blk_pos++;
    }
    nor_pull();
}
ST_EXPORT(st_out_l) float *st_out_l(void) { return out_l; }
ST_EXPORT(st_out_r) float *st_out_r(void) { return out_r; }
ST_EXPORT(st_render) void st_render(uint32_t n) { advance(n > 4096u ? 4096u : n, 1); }
ST_EXPORT(st_run_ms) void st_run_ms(uint32_t ms)
{
    while (ms) {                                   /* in steps: a long run never overflows */
        uint32_t step = ms > 1000u ? 1000u : ms;
        uint32_t want = step * (uint32_t)FM1_SAMPLE_RATE + frac;
        advance(want / 1000u, 0);
        frac = want % 1000u;
        ms -= step;
    }
}
ST_EXPORT(st_now) uint32_t st_now(void) { return now_ms; }
ST_EXPORT(st_fs) uint32_t st_fs(void) { return FM1_SAMPLE_RATE; }
ST_EXPORT(st_version) const char *st_version(void) { return "FM1 OPEN " FM1C_VERSION; }

/* ------------------------------------------------------------------ power on */
ST_EXPORT(st_boot) uint32_t st_boot(uint32_t master)
{
    unsigned blank = 1;
    memcpy(flash, nor, sizeof flash);
    for (unsigned i = 0; i < sizeof flash && blank; ++i)
        blank = flash[i] == 0xFFu;
    out_n = out_lost = 0;
    blk_pos = FM1_BLOCK_FRAMES;
    frac = 0;
    sim_boot(blank ? 1u : 0u);                     /* a new device: the factory sample bank, as the workbench writes it */
    sim_control(3, 0, (int)(master > 1023u ? 1023u : master));
    nor_valid = 0;
    nor_pull();
    st_fb_gen();
    return 1;
}

/* ------------------------------------------------------------------ the update handshake (the SDK's, 00 59) */
static unsigned unpack_stream(const uint8_t *m, unsigned n, uint8_t *raw, unsigned cap)
{
    unsigned bits = 0, value = 0, k = 0;
    for (unsigned i = 1; i + 1 < n; ++i) {
        value |= (unsigned)m[i] << bits;
        bits += 7;
        if (bits >= 8) {
            if (k == cap)
                return 0;
            raw[k++] = (uint8_t)value;
            value >>= 8;
            bits -= 8;
        }
    }
    return k;
}
static void identity_reply(void)                   /* WebUpdate.js frame(17, 27 bytes of identity) */
{
    uint8_t raw[6 + 27 + 1], msg[64];
    unsigned sum = 0, k = 0, bits = 0, value = 0;
    memset(raw, 0, sizeof raw);
    raw[1] = 89;
    raw[2] = 17;
    raw[3] = 27;
    memcpy(raw + 6, FM1C_ID, sizeof FM1C_ID - 1u);
    for (unsigned i = 6; i < 33; ++i)
        sum += raw[i];
    raw[33] = (uint8_t)~sum;
    msg[k++] = 0xF0;
    for (unsigned i = 0; i < sizeof raw; ++i) {
        value |= (unsigned)raw[i] << bits;
        bits += 8;
        while (bits >= 7) {
            msg[k++] = (uint8_t)(value & 127u);
            value >>= 7;
            bits -= 7;
        }
    }
    if (bits)
        msg[k++] = (uint8_t)(value & 127u);
    msg[k++] = 0xF7;
    (void)fm1_midi_send_port(FM1_MIDI_USB, msg, k);
}
static int update_frame(const uint8_t *m, unsigned n)   /* the SDK's update protocol: -> 1 handled here */
{
    uint8_t raw[16];
    if (n == 6 && m[1] == 0x22 && m[2] == 0x24 && m[3] == 0x35)
        return 1;                                  /* the update key F0 22 24 35 7F/7D F7: no updater in the twin */
    if (n < 10 || n > 24)
        return 0;
    unsigned k = unpack_stream(m, n, raw, sizeof raw);
    if (k < 7 || raw[0] != 0 || raw[1] != 89)
        return 0;
    if (raw[2] == 17)
        identity_reply();
    return 1;                                      /* (capability 49, 50, 48: not answered, as stock V15 does) */
}

/* ------------------------------------------------------------------ USB MIDI in */
static uint8_t in_buf[65536];
ST_EXPORT(st_in) uint8_t *st_in(void) { return in_buf; }
static unsigned short_len(uint8_t s)
{
    if (s >= 0xF8u || s == 0xF6u)
        return 1;
    if (s == 0xF1u || s == 0xF3u || (s & 0xE0u) == 0xC0u)
        return 2;
    return s == 0xF2u || s < 0xF0u ? 3 : 1;
}
/* n bytes of complete MIDI messages from the USB host -> bytes in st_out */
ST_EXPORT(st_midi_in) uint32_t st_midi_in(uint32_t n)
{
    uint32_t i = 0;
    if (n > sizeof in_buf)
        n = sizeof in_buf;
    while (i < n) {
        uint8_t b = in_buf[i];
        uint32_t len;
        if (b == 0xF0u) {
            uint32_t e = i;
            while (e < n && in_buf[e] != 0xF7u)
                e++;
            if (e == n)
                break;                             /* (an unterminated frame is dropped) */
            len = e + 1u - i;
            if (!update_frame(in_buf + i, len) && len <= 1024u) {
                memcpy(file_buffer, in_buf + i, len);
                (void)sim_midi_from(FM1_MIDI_USB, len);
            }
        } else if (b < 0x80u) {
            i++;
            continue;
        } else {
            len = short_len(b);
            if (i + len > n)
                break;
            memcpy(file_buffer, in_buf + i, len);
            (void)sim_midi_from(FM1_MIDI_USB, len);
        }
        i += len;
    }
    fm1_web_control();
    nor_pull();
    return out_n;
}

/* ------------------------------------------------------------------ the panel */
ST_EXPORT(st_keys) void st_keys(uint32_t m) { controls.keys = m & 0x7FFFFFFu; fm1_web_control(); nor_pull(); }
ST_EXPORT(st_buttons) void st_buttons(uint32_t m) { controls.buttons = m & 0x3FFFu; fm1_web_control(); nor_pull(); }
ST_EXPORT(st_encoder) void st_encoder(uint32_t e, int32_t n)
{
    if (e >= 7u)
        return;
    while (n) {                                    /* (sim_control takes at most 127 detents at a time) */
        int32_t k = n > 127 ? 127 : n < -127 ? -127 : n;
        sim_control(2, e, k);
        n -= k;
    }
    nor_pull();
}
ST_EXPORT(st_master) void st_master(uint32_t v) { sim_control(3, 0, (int)(v > 1023u ? 1023u : v)); }
/* TwinCore.B's labels (FX 0 … REC 11) -> the button's bit: screen.c's enum OCT_DOWN, OCT_UP, FX, SCL, … REC */
ST_EXPORT(st_panel_button) uint32_t st_panel_button(uint32_t label) { return label < 12u ? label + 2u : 0u; }
ST_EXPORT(st_led_keys) uint32_t st_led_keys(void)
{
    uint32_t m = 0;
    for (unsigned k = 0; k < 27u; ++k)
        m |= (uint32_t)(leds[14u + k] != 0) << k;
    return m;
}
ST_EXPORT(st_led_buttons) uint32_t st_led_buttons(void)
{
    uint32_t m = 0;
    for (unsigned b = 0; b < 14u; ++b)
        m |= (uint32_t)(leds[b] != 0) << b;
    return m;
}

/* ------------------------------------------------------------------ transport and projects */
static unsigned playing(void)
{
    struct groove_view v;
    memset(&v, 0, sizeof v);
    groove_get_view(0, 0, &v);
    return v.playing;
}
/* bit 0 playing, 1 the song chain (FM1 Open has none), 2 busy (playing or counting in), 3 recording */
ST_EXPORT(st_transport) uint32_t st_transport(void)
{
    unsigned p = playing();
    return (p ? 1u : 0u) | ((p || groove_sequence_count_in()) ? 4u : 0u) | (groove_edit_recording() ? 8u : 0u);
}
static void post(uint8_t status)
{
    (void)fm1_midi_post(&status, 1);
    advance(FM1_BLOCK_FRAMES, 0);                  /* the engine takes it at its next block */
}
/* how: 0 stop, 1 PLAY, 2 the song (FM1 Open: the pattern from the top), 3 restart (MIDI Start / Stop into the
 * instrument's own input, as the panel's transport does) */
ST_EXPORT(st_transport_set) uint32_t st_transport_set(uint32_t how)
{
    if (how == 0u || how == 3u)
        post(0xFC);
    if (how != 0u)
        post(0xFA);
    return st_transport();
}
/* the stems: track k muted (on) or not, as MUTE on the panel (groove_mix_toggle) */
ST_EXPORT(st_track_mute_set) void st_track_mute_set(uint32_t k, uint32_t on)
{
    struct groove_view v;
    if (k >= GROOVE_TRACKS)
        return;
    memset(&v, 0, sizeof v);
    groove_get_view(k, 0, &v);
    if (!v.muted != !on)
        groove_mix_toggle(k, 0);
}
ST_EXPORT(st_project_used) uint32_t st_project_used(uint32_t slot)
{
    char name[13];
    return slot < GROOVE_PROJECT_SLOTS && groove_project_info(slot, name) == 0;
}
/* 0 loaded, 1 empty (or unreadable), 2 stop first */
ST_EXPORT(st_project_load) uint32_t st_project_load(uint32_t slot)
{
    if (slot >= GROOVE_PROJECT_SLOTS)
        return 1;
    if (playing())
        return 2;
    int r = groove_project_load_slot(slot);
    fm1_web_control();
    nor_pull();
    return r == 0 ? 0u : 1u;
}
ST_EXPORT(st_project_current) uint32_t st_project_current(void) { return groove_project_slot(); }
