/* SPDX-License-Identifier: GPL-3.0-only
 * FM-1 Companion: a Melodee 1.6.0 song project written by Melodee's own firmware code.
 *
 * Compiled against a Melodee v1.6.0 checkout like fixturegen.c (tests/backup_test.c pulls in the real firmware:
 * project.c, pattern_store.c, project_stream.c, song_sound.c, editor_backup.c). It uses what 1.6 added: patterns
 * 9-16 on the four synth tracks, a 64-section song with rests and continued blocks, timed punch FX, song-block
 * presets and pattern presets (FBKQ). Read through the real BACKUP_LIST/GET handler as the running music
 * (object 0: the same project record a slot holds, HPJ1 around FBKQ). Writes:
 *   <out>/obj_0.bin      the running music
 *   <out>/expected.json  what the firmware holds (used patterns, song rows, punches, presets)
 * Build: tools/melodee_fixtures/build.sh <melodee-v1.6.0-checkout> song16
 */
#define BACKUP_TEST_NO_MAIN 1
#include "backup_test.c"
#include <assert.h>

static FILE *J;

static void dump_obj(const char *out, uint32_t id)
{
    uint32_t len = 0, crc = 0; char path[512];
    list(id, &len, &crc);
    static uint8_t buf[ED_BK_MAX];
    for (uint32_t off = 0; off < len; off += 256u) {
        uint32_t n = len - off > 256u ? 256u : len - off;
        assert(!get(id, off, n));
        assert(ed_unpack7(rep + 9, rep_n - 9, buf + off, n) == n);
    }
    snprintf(path, sizeof path, "%s/obj_%u.bin", out, id);
    FILE *f = fopen(path, "wb"); fwrite(buf, 1, len, f); fclose(f);
    printf("object %u: %u bytes, crc %08x, magic %.4s\n", id, len, crc, (const char *)buf);
}

static step_t note_step(uint8_t n0, uint8_t vel)
{ step_t s = {{n0, 0, 0, 0}, 1, ST_NOTE, 0, vel}; return s; }

static void build_music(void)
{
    reset();
    song.g[G_BPM] = 112;
    song.ntrk = NPART;
    /* every synth track: one note in each of its 16 patterns (pattern b: note 48 + b at step b) */
    for (uint32_t k = 0; k < NPART; k++) {
        track_t *t = &trk[k];
        for (uint32_t b = 0; b < NPAT; b++) {
            pattern_request(t, b);
            t->p[P_SLEN] = 16;
            t->step[b] = note_step((uint8_t)(36 + 12 * (k % 3) + b), (uint8_t)(70 + b));
        }
        pattern_request(t, 0);
    }
    /* song presets: track 1 sounds as a Prophet preset in block 2; pattern 12 of track 2 has its own sound */
    track_t saved = trk[0];
    engine_load_of(&trk[0], ENGI_PROPHET, 1);
    song_sound_t sound;
    song_sound_capture(&sound, &trk[0], "BLOCK SOUND");
    assert(song_sound_valid(&sound));
    uint32_t id = song_sound_store(&sound); assert(id);
    trk[0] = saved;
    /* 64 sections: tracks 1-4 walk through all 16 patterns, a rest on track 3 every fifth, a continued block */
    chain_config.count = CHAIN_ROWS;
    for (uint32_t r = 0; r < CHAIN_ROWS; r++) {
        chain_config.row[r].repeat = (uint8_t)(1u + r % 2u);
        for (uint32_t k = 0; k < NTRK; k++) {
            uint32_t v = k < NPART ? (r + 4u * k) % NPAT : CHAIN_REST;
            if (k == 2 && r % 5u == 4u) v = CHAIN_REST;
            chain_patterns[r][k] = (uint8_t)v;
        }
        chain_config.row[r].slot = chain_patterns[r][0];
    }
    chain_patterns[11][1] = CHAIN_HOLD;                      /* track 2: block of section 11 continues */
    chain_config.rsv[CHAIN_LOOP] = 1;
    chain_config.sound[2][0] = chain_config.sound[3][0] = (uint8_t)id;
    pattern_sound[1][11] = (uint8_t)id;
    /* timed punches: two in section 1, four in the last section */
    chain_config.fx[0][0] = (chain_fx_t){PX_REV + 1, 4, 4};
    chain_config.fx[0][1] = (chain_fx_t){PX_STUT + 1, 12, 2};
    for (uint32_t e = 0; e < CHAIN_FX_EVENTS; e++)
        chain_config.fx[63][e] = (chain_fx_t){(uint8_t)(PX_LOOP4 + 1 + e), (uint8_t)(e * 4u), 3};
}

static void dump_expected(void)
{
    fprintf(J, "{\"bpm\":%d,\"ntrk\":%u,\"patterns\":[", song.g[G_BPM], trk_n());
    for (uint32_t k = 0; k < NTRK; k++) {
        uint32_t used = 0;
        for (uint32_t b = 0; b < trk_npat(k); b++) {
            pattern_t p; pattern_read(k, b, &p);
            for (uint32_t i = 0; i < NSTEP; i++) if (p.step[i].time != ST_REST || p.step[i].hit) { used |= 1u << b; break; }
        }
        fprintf(J, "%s{\"npat\":%u,\"used\":%u}", k ? "," : "", trk_npat(k), used);
    }
    fprintf(J, "],\"song\":{\"count\":%u,\"loop\":%u,\"rows\":[", chain_config.count, chain_config.rsv[CHAIN_LOOP]);
    for (uint32_t r = 0; r < chain_config.count; r++) {
        fprintf(J, "%s{\"repeat\":%u,\"banks\":[", r ? "," : "", chain_config.row[r].repeat);
        for (uint32_t k = 0; k < NTRK; k++) fprintf(J, "%s%u", k ? "," : "", chain_patterns[r][k]);
        fprintf(J, "],\"sound\":[");
        for (uint32_t k = 0; k < NTRK; k++) fprintf(J, "%s%u", k ? "," : "", chain_config.sound[r][k]);
        fprintf(J, "],\"fx\":[");
        for (uint32_t e = 0; e < CHAIN_FX_EVENTS; e++)
            fprintf(J, "%s[%u,%u,%u]", e ? "," : "", chain_config.fx[r][e].fx, chain_config.fx[r][e].start,
                    chain_config.fx[r][e].length);
        fprintf(J, "]}");
    }
    fprintf(J, "]},\"pattern_sound\":[");
    for (uint32_t k = 0; k < NTRK; k++) {
        fprintf(J, "%s[", k ? "," : "");
        for (uint32_t b = 0; b < NPAT; b++) fprintf(J, "%s%u", b ? "," : "", pattern_sound[k][b]);
        fprintf(J, "]");
    }
    fprintf(J, "],\"sounds\":[");
    for (uint32_t i = 1; i <= song_sound_count; i++) {
        const song_sound_t *s = song_sound_get(i);
        fprintf(J, "%s{\"name\":\"%s\",\"engine\":%u,\"preset\":%u}", i > 1 ? "," : "", s->name, s->engine, s->preset);
    }
    fprintf(J, "]}");
}

int main(int argc, char **argv)
{
    const char *out = argc > 1 ? argv[1] : ".";
    char path[512];
    build_music();
    snprintf(path, sizeof path, "%s/expected.json", out);
    J = fopen(path, "w"); fprintf(J, "{\"version\":\"%s\",\"song16\":", FIXTURE_VERSION); dump_expected(); fprintf(J, "}\n"); fclose(J);
    dump_obj(out, 0);
    return 0;
}
