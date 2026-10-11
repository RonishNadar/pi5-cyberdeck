#!/bin/bash
# Draws the RONAD tech. logo: League Gothic, slanted, inside a symmetric rounded dashed frame.
# Usage: make-logo.sh [output.png]    Colors: FG=white BG=black make-logo.sh
set -e
IM=$(command -v magick || command -v convert)
OUT=${1:-ronad_logo.png}
FG=${FG:-white}
BG=${BG:-black}
W=1280; H=360

FONT=$(fc-list | grep -i "league gothic" | head -1 | cut -d: -f1)
[ -z "$FONT" ] && { echo "League Gothic not found"; exit 1; }

tmp=$(mktemp -d)

# Frame geometry is generated in Python so every side is laid out evenly:
# one curved dash per corner, and a whole number of equal dashes per side,
# centered with equal gaps (all values in pixels).
python3 - "$tmp/frame.mvg" <<'PY'
import sys
x0, y0, x1, y1 = 78, 38, 1202, 322   # frame rectangle
R = 46                                # corner radius
STROKE = 14                           # line thickness (round caps add STROKE/2 at each end)
DASH = 62                             # target dash length
GAP = 44                              # gap between dash centers' ends (visible gap = GAP - STROKE)

out = []
# Corner arcs (one dash each)
out.append(f"path 'M {x0},{y0+R} A {R},{R} 0 0,1 {x0+R},{y0}'")
out.append(f"path 'M {x1-R},{y0} A {R},{R} 0 0,1 {x1},{y0+R}'")
out.append(f"path 'M {x1},{y1-R} A {R},{R} 0 0,1 {x1-R},{y1}'")
out.append(f"path 'M {x0+R},{y1} A {R},{R} 0 0,1 {x0},{y1-R}'")

def side(a, b):
    """Return (start, end) dash segments that evenly fill a straight side from a to b."""
    L = b - a
    n = max(1, round((L - GAP) / (DASH + GAP)))
    d = (L - (n + 1) * GAP) / n
    segs, pos = [], a + GAP
    for _ in range(n):
        segs.append((pos, pos + d))
        pos += d + GAP
    return segs

for s, e in side(x0 + R, x1 - R):        # top and bottom
    out.append(f"line {s:.1f},{y0} {e:.1f},{y0}")
    out.append(f"line {s:.1f},{y1} {e:.1f},{y1}")
for s, e in side(y0 + R, y1 - R):        # left and right
    out.append(f"line {x0},{s:.1f} {x0},{e:.1f}")
    out.append(f"line {x1},{s:.1f} {x1},{e:.1f}")

open(sys.argv[1], "w").write("\n".join(out) + "\n")
PY

$IM -size ${W}x${H} xc:"$BG" -fill none -stroke "$FG" -strokewidth 14 \
  -draw "stroke-linecap round $(tr '\n' ' ' < "$tmp/frame.mvg")" "$tmp/frame.png"

# Text, slanted to the right (italic look), trimmed tight
$IM -background none -fill "$FG" -font "$FONT" -pointsize 320 label:"RONAD tech." \
  -trim +repage -virtual-pixel transparent +distort AffineProjection '1,0,-0.2,1,0,0' \
  -trim +repage -resize 1010x250 "$tmp/text.png"

# Text centered inside the frame
$IM "$tmp/frame.png" "$tmp/text.png" -gravity center -composite "$OUT"
rm -rf "$tmp"
echo "Saved $OUT"
