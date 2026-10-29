/* Main RTL8762C application: additive config opcode W 0C = screensaver forwarder (v2)
 * plus a screen-reply relay.
 *
 * `W 0C <sub> ...` forwards its bytes verbatim to the e-paper MCU as a55a
 * command 0x50 through the stock frame sender (0x81a540), exactly the way the
 * stock label/page pushes do.  Replies: `0c 01` = forwarded, `0c 02` = refused
 * (bad framing; nothing sent).  `W 0C` alone answers `0c 01 'S' 'S' '0' '2'`.
 *
 * v2 relay: the screen's a55a reply to command 0x50 (header a6 6a, cmd 0x50,
 * status, len16, payload, checksum) is caught by ss_rx_relay(), which is spliced
 * in front of the stock response dispatcher 0x81aaf4 (the `b.w` at 0x81b938), and
 * re-sent on the config notify characteristic as `0c 03 <status> <payload...>`.
 * It reuses the stock config reply buffer 0x20d4d0 / length 0x20d2ae and the stock
 * notify sender 0x818382, the same way the stock screen-OTA progress events
 * (`a2 01/02`, 0x81843a) are emitted from this very context.  No new RAM.
 * The screen-OTA path (W A1/A2 -> 0x40/0x46/0x47/0x48) is untouched.
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

#define SEND ((void (*)(u32, u32, u32, u32, u32, const u8 *, u32))0x81a541u)
#define NOTIFY ((void (*)(u8 *, u32))0x818383u)     /* stock config notify sender */
#define REPLY_BUF ((u8 *)0x20d4d0u)                  /* stock config reply buffer (0x300 bytes) */
#define REPLY_LEN (*(volatile u16 *)0x20d2aeu)       /* its stock length field */
#define IMG_BYTES 8072u
#define NSLOTS 6u
#define MAX_DATA 128u
#define MAX_RELAY 40u

u32 ss_command(const u8 *cmd, u32 len, u8 *reply)
{
    reply[0] = 0x0c;
    reply[1] = 2;
    if (len == 2) {
        reply[1] = 1;
        reply[2] = 'S'; reply[3] = 'S'; reply[4] = '0'; reply[5] = '2';
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
    case 0x08: ok = n == 2; break;                       /* REG idx */
    case 0x09: ok = n == 2 && p[1] < 2; break;           /* CHEM 0|1 */
    default: break;
    }
    if (!ok)
        return 2;
    SEND(1, 1, 0xa55a, 0x50, 0, p, n);
    reply[1] = 1;
    return 2;
}

/* Called with every checksum-valid a6 6a response frame before the stock dispatcher. */
void ss_rx_relay(const u8 *frame)
{
    if (frame[2] != 0x50)
        return;
    u32 n = (u32)(frame[4] | (frame[5] << 8));
    if (n > MAX_RELAY)
        n = MAX_RELAY;
    u8 *b = REPLY_BUF;
    b[0] = 0x0c;
    b[1] = 0x03;
    b[2] = frame[3];                                     /* 0 = ack, 0x40 = nak */
    for (u32 i = 0; i < n; ++i)
        b[3 + i] = frame[6 + i];
    REPLY_LEN = (u16)(3 + n);
    NOTIFY(b, 3 + n);
}
