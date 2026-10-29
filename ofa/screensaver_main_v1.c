/* Main RTL8762C application: additive config opcode W 0C = screensaver forwarder, v1.
 *
 * FROZEN.  This is the forwarder that is INSTALLED on the remote (main v1, flashed
 * 2026-09-23, image SHA 3b2e3240..., identity `SS01`).  It is the v2 source
 * (screensaver_main.c) minus the 0x08/0x09 sub-ops and the reply relay, and it
 * assembles byte-for-byte to the 236-byte code recorded in
 * artifacts/screensaver-v1/manifest.json (appended_code_sha256 580d763b...).
 * blue_led_off_firmware.py rebuilds main v1 from this so the flasher can gate the
 * blue-light-bar candidate by reproduce-and-match.  Do not edit.
 *
 * `W 0C <sub> ...` forwards its bytes verbatim to the e-paper MCU as a55a
 * command 0x50 through the stock frame sender (0x81a540), exactly the way the
 * stock label/page pushes do.  Replies: `0c 01` = forwarded, `0c 02` = refused
 * (bad framing; nothing sent).  `W 0C` alone answers `0c 01 'S' 'S' '0' '1'`.
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

#define SEND ((void (*)(u32, u32, u32, u32, u32, const u8 *, u32))0x81a541u)
#define IMG_BYTES 8072u
#define NSLOTS 6u
#define MAX_DATA 128u

u32 ss_command(const u8 *cmd, u32 len, u8 *reply)
{
    reply[0] = 0x0c;
    reply[1] = 2;
    if (len == 2) {
        reply[1] = 1;
        reply[2] = 'S'; reply[3] = 'S'; reply[4] = '0'; reply[5] = '1';
        return 6;
    }
    if (len < 3 || len > 2 + 3 + MAX_DATA)
        return 2;
    const u8 *p = cmd + 2;
    u32 n = len - 2;
    u32 ok = 0;
    switch (p[0]) {
    case 0x00: ok = n == 4 && p[1] < NSLOTS && (u32)(p[2] | (p[3] << 8)) == IMG_BYTES; break;
    case 0x01: ok = n >= 4 && n - 3 <= MAX_DATA &&
                    (u32)(p[1] | (p[2] << 8)) + (n - 3) <= IMG_BYTES; break;
    case 0x02: ok = n == 5; break;
    case 0x03: ok = n == 1 || (n == 2 && p[1] < NSLOTS); break;
    case 0x04: ok = n == 3; break;
    case 0x05: ok = n == 2 && (p[1] < NSLOTS || p[1] == 0xff); break;
    case 0x06: ok = (n == 2 || n == 3) && p[1] < 3 && (n == 2 || p[2] < NSLOTS); break;
    case 0x07: ok = n == 1; break;
    default: break;
    }
    if (!ok)
        return 2;
    SEND(1, 1, 0xa55a, 0x50, 0, p, n);
    reply[1] = 1;
    return 2;
}
