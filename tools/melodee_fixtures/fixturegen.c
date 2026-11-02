/* SPDX-License-Identifier: GPL-3.0-only
 * FM-1 Companion: test fixtures written by Melodee's own firmware code.
 *
 * Compiled against a Melodee 1.6.0-rc.7 checkout (tests/backup_test.c pulls in the real firmware sources,
 * project.c, pattern_store.c, project_stream.c and editor_backup.c). It builds music in the firmware's RAM,
 * saves it with the device's own save path and reads the objects back through the real BACKUP_LIST/GET
 * handler: the bytes are exactly what a backup of a real FM-1 holds. Writes:
 *   <out>/obj_<id>.bin   object bytes (0 = laufende Musik, 2 = Projekt 1, 64 = Projekt 5)
 *   <out>/expected.json  what the firmware holds in RAM (steps per track/bank, timing, song, tempo)
 *   <out>/layout.json    sizes and offsets of the record (docs/melodee-project-format.md)
 *   <out>/tables.json    parameter/engine/preset tables of this firmware
 * Build: see tools/melodee_fixtures/build.sh
 */
#define BACKUP_TEST_NO_MAIN 1
#include "backup_test.c"
#include <assert.h>

static FILE *J;
static void jstr(const char *s) { fputc('"', J); for (; s && *s; s++) { if (*s == '"' || *s == '\\') fputc('\\', J); fputc(*s, J); } fputc('"', J); }

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

static void dump_expected(const char *name)
{
    fprintf(J, "{\"name\":"); jstr(name);
    fprintf(J, ",\"bpm\":%d,\"swing\":%d,\"ntrk\":%u,\"sel\":%u,\"tracks\":[", song.g[G_BPM], song.g[G_SWING], trk_n(), song.sel);
    for (uint32_t k = 0; k < trk_n(); k++) {
        track_t *t = &trk[k];
        pattern_commit(t);
        fprintf(J, "%s{\"engine\":%u,\"preset\":%u,\"active\":%u,\"level\":%d,\"pan\":%d,\"mute\":%d,\"banks\":[", k ? "," : "",
                t->eng_req, t->preset, t->pattern, t->p[P_LEVEL], t->p[P_PAN], t->p[P_MUTE]);
        for (uint32_t b = 0; b < trk_npat(k); b++) {
            pattern_t p; pattern_read(k, b, &p);
            fprintf(J, "%s{\"timing\":[%d,%d,%d,%d],\"steps\":{", b ? "," : "", p.timing[0], p.timing[1], p.timing[2], p.timing[3]);
            int first = 1;
            for (uint32_t i = 0; i < NSTEP; i++) {
                step_t *s = &p.step[i];
                if (s->time == ST_REST && !s->n && !s->hit && !s->vel && !s->flags && !s->probability) continue;
                fprintf(J, "%s\"%u\":{\"notes\":[%u,%u,%u,%u],\"n\":%u,\"time\":%u,\"flags\":%u,\"vel\":%u,\"hit\":%u,\"acc\":%u,\"prob\":%u}",
                        first ? "" : ",", i, s->note[0], s->note[1], s->note[2], s->note[3], s->n, s->time, s->flags, s->vel, s->hit, s->acc, s->probability);
                first = 0;
            }
            fprintf(J, "}}");
        }
        fprintf(J, "]}");
    }
    fprintf(J, "],\"song\":{\"count\":%u,\"loop\":%u,\"rows\":[", chain_config.count, chain_config.rsv[CHAIN_LOOP]);
    for (uint32_t r = 0; r < chain_config.count; r++) {
        fprintf(J, "%s{\"repeat\":%u,\"banks\":[", r ? "," : "", chain_config.row[r].repeat);
        for (uint32_t k = 0; k < NTRK; k++) fprintf(J, "%s%u", k ? "," : "", chain_patterns[r][k]);
        fprintf(J, "]}");
    }
    fprintf(J, "]},\"motion\":[");
    for (uint32_t i = 0; i < motion.count; i++)
        fprintf(J, "%s[%u,%u,%u,%d]", i ? "," : "", motion.event[i].place >> 6, motion.event[i].place & 63u, motion.event[i].param, motion.event[i].value);
    fprintf(J, "],\"recording\":[");
    int first = 1;
    for (uint32_t i = 0; i < RECORD_MAX; i++) {
        recorded_note_t r = recording[i];
        if (!r.vel) continue;
        fprintf(J, "%s{\"owner\":%u,\"step\":%u,\"note\":%u,\"vel\":%u,\"on\":%u,\"duration\":%u,\"exp\":%u}", first ? "" : ",",
                recorded_owner(&r), recorded_step(&r), recorded_note(&r), r.vel, r.on, r.duration, recorded_exponent(&r));
        first = 0;
    }
    fprintf(J, "]}");
}

static step_t note_step(uint8_t n0, uint8_t n1, uint8_t n2, uint8_t n3, uint8_t n, uint8_t vel)
{ step_t s = {{n0, n1, n2, n3}, n, ST_NOTE, 0, vel}; return s; }

static void build_music(void)
{
    reset();
    song.g[G_BPM] = 98; song.g[G_SWING] = 12;
    song.ntrk = NPART + 1u;                          /* the parts and one MIDI-only track */
    /* track 1: a bass line in bank 0, a variation in bank 2 */
    track_t *t = &trk[0];
    t->p[P_SLEN] = 16; t->p[P_SDIV] = 2; t->p[P_LEVEL] = 100; t->p[P_PAN] = -20;
    uint8_t bass[8] = {36, 0, 36, 43, 0, 41, 39, 0};
    for (uint32_t i = 0; i < 16; i++) {
        uint8_t n = bass[i % 8];
        if (n) { t->step[i] = note_step(n, 0, 0, 0, 1, (uint8_t)(80 + i)); }
    }
    t->step[1] = (step_t){{0}, 0, ST_TIE};
    t->step[3].flags = SF_ACCENT; t->step[5].flags = SF_SLIDE; t->step[6].probability = 50;
    pattern_request(t, 2);
    t->p[P_SLEN] = 32; t->p[P_SDIV] = 3;
    t->step[0] = note_step(48, 0, 0, 0, 1, 127); t->step[31] = note_step(55, 0, 0, 0, 1, 64);
    pattern_request(t, 0);
    /* track 2: chords (4 notes), length 8, 1/8 */
    t = &trk[1]; t->p[P_SLEN] = 8; t->p[P_SDIV] = 1; t->p[P_MUTE] = 1;
    t->step[0] = note_step(60, 64, 67, 71, 4, 100); t->step[1] = (step_t){{0}, 0, ST_TIE};
    t->step[4] = note_step(57, 60, 64, 0, 3, 90);
    /* track 3: drum grid lanes in bank 15 (the last part bank) */
    t = &trk[2]; pattern_request(t, 15); t->p[P_SLEN] = 16;
    for (uint32_t i = 0; i < 16; i++) {
        uint8_t hit = (i % 4 == 0 ? 1u : 0u) | (i % 8 == 4 ? 2u : 0u) | (i % 2 == 0 ? 8u : 0u) | (i == 15 ? 128u : 0u);
        if (hit) { t->step[i] = (step_t){{0}, 0, ST_NOTE, 0, 100, hit, (uint8_t)(i == 0 ? 1u : i == 15 ? 128u : 0u), 0}; }
    }
    /* track 4: one note at the last step, bank 7 */
    t = &trk[3]; pattern_request(t, 7); t->step[63] = note_step(72, 0, 0, 0, 1, 99); t->p[P_SLEN] = 64;
    /* track 5 (MIDI only): bank 3 */
    t = &trk[4]; pattern_request(t, 3); t->step[2] = note_step(50, 0, 0, 0, 1, 70); t->p[P_SLEN] = 4;
    pattern_request(t, 0);
    /* motion on track 1, a recorded note of track 1 bank 0 */
    motion_set_event(&trk[0], 4, P_PAN, 30);
    recorded_note_t r = {.on = 12345, .duration = 40000, .note7 = 62, .vel = 90};
    recorded_set_owner(&r, 0 * NPAT + 0); recorded_set_step(&r, 9, 0, 0);
    recording[0] = r; recording_reindex();
    /* song: 3 sections, a rest and a hold */
    chain_config.count = 3;
    chain_config.row[0].repeat = 2; chain_config.row[1].repeat = 1; chain_config.row[2].repeat = 4;
    uint8_t rows[3][NTRK] = {{0, 0, 15, 7, 3}, {2, CHAIN_REST, 15, 7, 3}, {0, CHAIN_REST, CHAIN_HOLD, 7, 0}};
    for (uint32_t i = 0; i < 3; i++) { memcpy(chain_patterns[i], rows[i], NTRK); chain_config.row[i].slot = rows[i][0]; }
}

static void tables(const char *path)
{
    J = fopen(path, "w");
    fprintf(J, "{\"P_COUNT\":%u,\"P_E0\":%u,\"G_COUNT\":%u,\"NENGINES\":%u,\"params\":[", P_COUNT, P_E0, G_COUNT, NENGINES);
    for (uint32_t i = 0; i < P_COUNT; i++) {
        const param_desc_t *d = &TP[i];
        fprintf(J, "%s{\"label\":", i ? "," : ""); jstr(d->label);
        fprintf(J, ",\"fmt\":%u,\"min\":%d,\"max\":%d,\"def\":%d,\"names\":[", d->fmt, d->min, d->max, d->def);
        if (d->fmt == F_ENUM && d->names) for (int v = d->min; v <= d->max; v++) { if (v > d->min) fputc(',', J); jstr(d->names[v - d->min]); }
        fprintf(J, "]}");
    }
    fprintf(J, "],\"globals\":[");
    for (uint32_t i = 0; i < G_COUNT; i++) {
        const param_desc_t *d = &GP[i];
        fprintf(J, "%s{\"label\":", i ? "," : ""); jstr(d->label);
        fprintf(J, ",\"fmt\":%u,\"min\":%d,\"max\":%d,\"def\":%d}", d->fmt, d->min, d->max, d->def);
    }
    fprintf(J, "],\"engines\":[");
    for (uint32_t e = 0; e < NENGINES; e++) {
        const engine_t *en = ENGINES[e];
        fprintf(J, "%s{\"name\":", e ? "," : ""); jstr(en->name);
        fprintf(J, ",\"edit\":[");
        for (uint32_t k = 0; k < 8; k++) { if (k) fputc(',', J); jstr(en->edit[k].label); }
        fprintf(J, "],\"presets\":[");
        for (uint32_t k = 0; k < en->npresets; k++) { if (k) fputc(',', J); jstr(eng_preset(en, k)->name); }
        fprintf(J, "]}");
    }
    fprintf(J, "]}\n");
    fclose(J);
}

static void layout(const char *path)
{
    J = fopen(path, "w");
#define L(x) fprintf(J, "\"%s\":%u,\n", #x, (unsigned)(x))
    fprintf(J, "{\n");
    L(NTRK); L(NPART); L(NPAT); L(NPAT_V1); L(NPAT_MIDI); L(NSTEP); L(P_COUNT); L(P_E0); L(G_COUNT); L(P_SLEN);
    L(P_LEVEL); L(P_PAN); L(P_MUTE); L(P_ROOT); L(P_SCALE);
    L(PROJ_STORE_SIZE); L(BANK_STORE_SIZE); L(BANK_ACTIVE_OFF); L(BANK_TAIL_SIZE); L(PJ_HEAD); L(PJ_TPAGES); L(PJ_SPAGES);
    L(PROJ_FX_WIRE_OFF); L(PROJ_MASTER_WIRE); L(PROJ_FX_MODE_OFF); L(PROJ_MPC_OCT_WIRE); L(PROJ_TONE_WIRE);
    L(PROJ_FM6_OFF); L(PROJ_CZ_OFF); L(PROJ_REC_OFF); L(PROJ_P5_OFF); L(PROJ_DRUM_OFF); L(PROJ_REC_DIR); L(PROJ_NAME_OFF);
    L(FM6_PACKED); L(CZ_BYTES); L(sizeof(p5_patch_t)); L(DRUM_WIRE_BYTES); L(FM6_NFN); L(RECORD_MAX);
    L(sizeof(chain_legacy_t)); L(sizeof(motion_store_t)); L(MOTION_MAX); L(CHAIN_ROWS); L(CHAIN_LEGACY_ROWS);
    L(BANK_EXTRA_OFF); L(BANK_TIMING_OFF); L(BANK_CHAIN_OFF); L(BANK_MOTION_OFF); L(BANK_FN_OFF);
    L(BANKL_TIMING_OFF); L(BANKL_CHAIN_OFF); L(BANKL_MOTION_OFF); L(BANKL_FN_OFF); L(BANKL_REC_OFF); L(BANKL_TRK_OFF); L(BANKL_STEPS_OFF);
    L(BANKP_CHAIN_OFF); L(BANKP_MOTION_OFF); L(BANKP_FN_OFF); L(BANKP_REC_OFF); L(BANKP_REC_BYTES); L(BANKP_TRK_OFF); L(BANKP_STEPS_OFF);
    L(BANKL_TRK_BYTES); L(BANKM_SONG_BYTES); L(BANKN_FX_BYTES); L(BANKO_HEAD_BYTES); L(sizeof(song_sound_t)); L(PROJ_REC_DIR_BYTES);
    fprintf(J, "\"end\":0}\n");
    fclose(J);
}

int main(int argc, char **argv)
{
    const char *out = argc > 1 ? argv[1] : ".";
    char path[512];
    snprintf(path, sizeof path, "%s/tables.json", out); tables(path);
    snprintf(path, sizeof path, "%s/layout.json", out); layout(path);
    build_music();
    assert(!project_save_as(0, "FIXTURE SONG"));       /* Projekt 1 = object 2 */
    trk[1].step[6] = note_step(77, 0, 0, 0, 1, 33);    /* the live music differs from slot 1 */
    assert(!project_save_as(4, "SLOT FIVE"));           /* Projekt 5 = object 64 */
    snprintf(path, sizeof path, "%s/expected.json", out);
    J = fopen(path, "w"); fprintf(J, "{\"live\":"); dump_expected("SLOT FIVE"); fprintf(J, "}\n"); fclose(J);
    dump_obj(out, 0); dump_obj(out, 2); dump_obj(out, 64);
    return 0;
}
