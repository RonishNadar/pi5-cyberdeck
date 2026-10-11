#!/usr/bin/env python3
"""
edid-add-audio.py: turn a 128-byte video-only EDID into the 256-byte EDID the deck uses.

Two changes make HDMI audio work on the Raspberry Pi 5:
  1. Byte 20 (video input) is set to 0x80 = digital display. The kernel template
     from edid-generator declares an analog display, and Linux only looks for
     HDMI support on digital displays.
  2. A CEA-861 extension block is appended that declares basic audio
     (2-channel LPCM, 32/44.1/48 kHz, 16/20/24-bit), front left/right speakers
     and the HDMI vendor block, so the Pi drives the panel in HDMI mode (not DVI).

Usage:
  python3 edid-add-audio.py 1440x2560_50.bin 1440x2560_50_audio.bin
Check the result with:
  edid-decode 1440x2560_50_audio.bin
"""
import sys

if len(sys.argv) != 3:
    sys.exit(__doc__)

src, dst = sys.argv[1], sys.argv[2]
base = bytearray(open(src, "rb").read()[:128])
if len(base) != 128 or base[:8] != bytes([0, 255, 255, 255, 255, 255, 255, 0]):
    sys.exit(f"{src} is not a valid 128-byte EDID")

base[20] = 0x80                                  # digital input
base[126] = 1                                    # one extension block follows
base[127] = (-sum(base[:127])) & 0xFF            # base block checksum

data = bytes([
    0x23, 0x09, 0x07, 0x07,                      # audio: LPCM, 2 ch, 32/44.1/48 kHz, 16/20/24-bit
    0x83, 0x01, 0x00, 0x00,                      # speakers: front left/right
    0x65, 0x03, 0x0C, 0x00, 0x10, 0x00,          # HDMI vendor block (OUI 00-0C-03), address 1.0.0.0
])
ext = bytearray(128)
ext[0], ext[1], ext[2], ext[3] = 0x02, 0x03, 4 + len(data), 0x40   # CEA-861 rev 3, basic audio
ext[4:4 + len(data)] = data
ext[127] = (-sum(ext[:127])) & 0xFF              # extension checksum

open(dst, "wb").write(base + ext)
print(f"wrote {dst} ({len(base) + len(ext)} bytes)")
