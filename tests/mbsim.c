/* SPDX-License-Identifier: GPL-3.0-only */
/* A stand-in unit for tests: m-bootloader's own MIDI mode (src/midi_update.c, src/core.c, unchanged) on a flash
 * file, one MIDI message per line as hex on stdin/stdout (FM-1 Companion's SubprocessTransport).
 *   mbsim FLASH_IN FLASH_OUT KEY_HEX JEDEC_HEX VERSION */
#include "midi_update.h"
#include "core.h"
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define FLASH 0x100000u
static uint8_t flash[FLASH];
static const char *out_path, *version;
static uint32_t jedec;
static unsigned erases, programs;
static char line[8192]; static size_t ln;
static void save(void) { FILE *f = fopen(out_path, "wb"); if (f) { fwrite(flash, 1, FLASH, f); fclose(f); } }
void mu_send(const uint8_t *d, uint32_t n) { for (uint32_t i = 0; i < n; i++) printf("%02x", d[i]); printf("\n"); fflush(stdout); }
void mu_idle(void) {
    char c; ssize_t r;
    while ((r = read(0, &c, 1)) == 1) {
        if (c != '\n') { if (ln < sizeof line - 1) line[ln++] = c; continue; }
        line[ln] = 0; ln = 0;
        for (char *p = line; p[0] && p[1]; p += 2) { char b[3] = { p[0], p[1], 0 }; mu_rx((uint8_t)strtoul(b, 0, 16)); }
        return;                                        /* one message, then the unit looks at it */
    }
    if (r == 0) { save(); fprintf(stderr, "mbsim: host gone (erases %u, programs %u)\n", erases, programs); exit(0); }
    usleep(200);
}
bool mu_cancel(void) { return false; }
bool mu_read(uint32_t a, void *o, uint32_t n) { if (a > FLASH || n > FLASH - a) return false; memcpy(o, flash + a, n); return true; }
bool mu_erase(uint32_t a) { if (a % MU_SECTOR || a >= FLASH) return false; memset(flash + a, 0xff, MU_SECTOR); erases++; return true; }
bool mu_program(uint32_t a, const void *d, uint32_t n) { if (a > FLASH || n > FLASH - a) return false; const uint8_t *s = d; for (uint32_t i = 0; i < n; i++) flash[a + i] &= s[i]; programs++; return true; }
void mu_progress(bool w, unsigned p) { (void)w; (void)p; }
void mu_unit_info(struct mu_unit *u) { u->jedec = jedec; u->capacity = FLASH; for (int i = 0; i < 8; i++) u->uid[i] = (uint8_t)(0xA0 + i); u->kind = MU_BOOTLOADER; u->version = version; }
void mu_start_app(void) {}
int main(int argc, char **argv) {
    if (argc < 6) return 2;
    memset(flash, 0xff, FLASH);
    FILE *f = fopen(argv[1], "rb"); if (f) { size_t n = fread(flash, 1, FLASH, f); (void)n; fclose(f); }
    out_path = argv[2]; uint16_t key = (uint16_t)strtoul(argv[3], 0, 16); jedec = (uint32_t)strtoul(argv[4], 0, 16); version = argv[5];
    fcntl(0, F_SETFL, fcntl(0, F_GETFL) | O_NONBLOCK);
    static uint8_t sector[MU_SECTOR];
    printf("# mbsim ready\n"); fflush(stdout);
    for (;;) {
        bool written = false;
        enum mu_result r = mu_run(key, sector, &written);
        save();
        fprintf(stderr, "mbsim: result %d written %d erases %u programs %u\n", (int)r, (int)written, erases, programs);
        if (r == MU_DONE || r == MU_RESTART || r == MU_DOWNLOAD) return 0;
    }
}
