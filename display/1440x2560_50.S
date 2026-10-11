/* Waveshare 5.5" 1440x2560 HDMI LCD at 50 Hz.
 * Timings come from Waveshare's hdmi_timings line for the Raspberry Pi 4:
 *   hdmi_timings=1440 0 10 10 140 2560 0 11 2 2 0 0 0 50 0 206000000 3
 * Build with https://github.com/akatrevorjay/edid-generator :
 *   cp 1440x2560_50.S edid-generator/ && cd edid-generator && make 1440x2560_50.bin
 * Then add digital input + audio with scripts/edid-add-audio.py.
 */
#define VERSION 1
#define REVISION 3

#define CLOCK 206000 /* kHz */
#define XPIX 1440
#define YPIX 2560
#define XY_RATIO XY_RATIO_16_9   /* EDID has no 9:16 code; only affects a legacy field */
#define XBLANK 160               /* 10 front porch + 10 sync + 140 back porch */
#define YBLANK 15                /* 11 front porch + 2 sync + 2 back porch */
#define XOFFSET 10
#define XPULSE 10
#define YOFFSET 11
#define YPULSE 2
#define DPI 96
#define VFREQ 50 /* Hz */
#define TIMING_NAME "Waveshare55"
#define ESTABLISHED_TIMING_BITS 0x00
#define ESTABLISHED_TIMING1_BITS 0x00
#define ESTABLISHED_TIMING2_BITS 0x00
#define ESTABLISHED_TIMING3_BITS 0x00
#define HSYNC_POL 1
#define VSYNC_POL 1

#include "edid.S"
