#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Writes tests/fixtures/musik/ with Melodee's own firmware code (see fixturegen.c).
#   tools/melodee_fixtures/build.sh /path/to/melodee-checkout   (needs python3 + Pillow + fontTools, clang)
# With "song16" (a Melodee v1.6.0 checkout): tests/fixtures/musik16/ from fixturegen16.c – patterns 9-16, a
# 64-section song, timed punches, song/pattern presets.
set -e
MEL=${1:?Melodee-Checkout angeben}
HERE=$(cd "$(dirname "$0")" && pwd)
GEN=fixturegen
OUT=$HERE/../../tests/fixtures/musik
if [ "${2:-}" = song16 ]; then GEN=fixturegen16; OUT=$HERE/../../tests/fixtures/musik16; fi
mkdir -p "$MEL/build/gen" "$OUT"
cd "$MEL"
for g in "gen_tables.py build/gen/melodee_tables.h" "gen_fm6_patches.py build/gen/melodee_fm6.h" \
         "gen_cz1_factory.py build/gen/melodee_cz1.h" "gen_ui_palettes.py build/gen/ui_palettes.h" \
         "gen_aa_keycaps.py build/gen/ui_keycaps.h" "gen_icons.py build/gen/ui_icons.h" \
         "gen_909_tables.py build/gen/x0x_drum_tables.h"; do
    set -- $g; [ -f "$2" ] || python3 "tools/$1" "$2"
done
[ -f build/gen/melodee_prophet_factory.h ] || python3 tools/gen_prophet_factory.py build/gen/melodee_prophet_factory.h --web check
[ -f build/gen/ui_fonts.h ] || python3 tools/gen_aa_font.py build/gen/ui_fonts.h --preset rubik
cp "$HERE/$GEN.c" tests/fm1c_fixturegen.c
VER=$(sed -n 's/^#define MELODEE_VERSION "\([^"]*\)".*/\1/p' firmware/src/melodee.c)
clang -O1 -ffp-contract=off -w -Ibuild/gen -Ifirmware/src -DFIXTURE_VERSION="\"$VER\"" -o build/fm1c_fixturegen \
      tests/fm1c_fixturegen.c -lm
./build/fm1c_fixturegen "$OUT"
