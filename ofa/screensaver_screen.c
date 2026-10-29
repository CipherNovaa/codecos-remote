/* OFA RT150T e-paper application extension v15: MULTI-SLOT image screensaver drawn by
 * a DIRECT FRAMEBUFFER BLIT at the sleep instant, with on-device readback, fitted
 * INSIDE the stock-size image (the bootloader only commits images <= 180608 B, and
 * only the OFA app's "E-paper - Select File" upload issues the commit).
 *
 * v15 = v14 + WAKE/RESTORE HARDENING (the v14 keypress FREEZE and pickup DOUBLE-SWITCH):
 *   * the v14 restore only set SHOWN := 0xff; the stock page dispatcher (0x1ec5c, tail
 *     0x1ef0a) then chose the refresh mode from its own policy 0xfa3c(state): PARTIAL
 *     (0x22/0xcf, the short LUT at 0x27f36) for the device pages 0x5b..0x5d and for any
 *     page after its first draw.  A partial LUT cannot erase a dense full-contrast saver
 *     image, so the panel visibly kept the saver while the framebuffer already held the
 *     page; every later wake was `ignored` (saver_active 0) and nothing re-rendered until
 *     the next sleep-draw's forced FULL blit ("un-sticks").  If main went quiet first,
 *     the sleep-draw found fb != saver, picked ANOTHER slot and drew it: the "switch".
 *   * fix 1: every saver blit (sleep-draw, SHOW) leaves the partial counter 0x20000a24 at
 *     0x1e, so the NEXT stock flush is a FULL update (0x1858: counter >= 0x1e -> 0xc030,
 *     counter := 0) whatever policy the dispatcher picks; the stock policy resumes after;
 *   * fix 2: a 5th hook on `bl 0x1ec5c` @0xe694 (ss_dispatch): before any page build,
 *     wait (<= 4 s, no RST) for a refresh still in flight; after a genuine restore it
 *     re-asserts the counter (belt and braces), records the policy the stock would have
 *     used (rst_policy / rst_sync) and sets 0x200008b8 so the restore's full refresh is
 *     NOT guarded by the stock wait 0x1e160 (1.5 s cap, then an RST pulse mid-refresh)
 *     but by our bounded waits (this hook, the sleep hook, SHOW);
 *   * fix 3: GRACE -- for 20 s of awake time after a genuine restore the sleep hook
 *     defers the stock sleep instead of drawing a NEW image (a quiet pickup would
 *     otherwise show the page for ~1 s and swap to another saver); the skip path and the
 *     saver-off paths now also pre-wait BUSY so the panel is never deep-slept mid-refresh;
 *   * readback regs 84..99: rst_pending, grace_armed, disp_calls, disp_forced,
 *     disp_busy_pre, disp_ms10, disp_timeouts, grace_defers, quiet_ms10 (awake time from
 *     the restore to the first sleep decision, x8 ms), wake_snap (bit0 rx-edge, 1 slept,
 *     2 LPTIM woke, 3 BUSY at wake, 4 PA2 event, 5 ARMED, 7:6 pre-wake mode), rst_policy
 *     (FULL_FLAG the dispatcher chose: 0 = the v14 freeze mode), rst_sync (0x200008b8 it
 *     left: 0 = stock 1.5 s RST-on-timeout wait), LIVE 0x20000a24/0x20000a25/0x200008b8,
 *     grace seconds.  ext_version 15, magic SSRF, ss_state 84 B.  reg_read is table-driven.
 *
 * v14 = v13 + a FORENSIC flash path (the v13 slot-0 "regression" was not in the code:
 * the flashed v13 erase/unlock/program/crc bytes for 0x2e000 are op-for-op the v12/v6
 * ones -- see SCREENSAVER-DEBUG-STATE.md v14).  What v14 adds, so the next failure
 * names itself instead of being inferred:
 *   * fl_unlock(): the stock single-bit SLOCK write, then RM 10.3.1 Step 7 -- read the
 *     SLOCK register back and retry until the bit is 1 (8 tries); a bit that never
 *     sticks fails the op with code 0xa (err_addr = the address);
 *   * fl_erase(): after each sector erase the 512 B are read back and must be 0xFF
 *     (a locked/absent sector erases "successfully" per the RM: the op is simply void)
 *     -> code 8 with err_addr; the driver's own failure -> 7;
 *   * fl_write(): every failure records err_addr + the driver code (1 = readback
 *     mismatch, 6 = OP mode refused, 9 = BUSY timeout, 0xa = unlock);
 *   * DATA keeps a running crc32 of the RECEIVED bytes (rx_crc), rx_frames, rx_bytes;
 *   * COMMIT computes both crcs and stores diag = 0x10 | rx_ok | flash_ok << 1.  If the
 *     flash crc is wrong it NAKs but LEAVES rx_active = 1, so the tool can re-send the
 *     DATA frames (re-programming identical bytes is a no-op; a dropped frame's 0xFF
 *     cells take the bytes) and COMMIT again without a new BEGIN/erase;
 *   * registers 61..83 expose rx_crc/rx_frames/rx_bytes/err_addr/err_code/diag/
 *     slock_tries, FLASH_IFR (IF1 = protection alarm), FLASH_CR[7:0], SLOCK2[31:24],
 *     SLOCK3[23:0] and the first DATA byte of the slot being written.
 *
 * Three free regions (zero padding the mgs-font tool left after glyph bitmaps,
 * verified unreferenced by every glyph descriptor):
 *   region A  .text VA 0x25e1c..0x263ac (1424 B): command handler + flash helpers
 *   region B  .b    VA 0x25290..0x256c0 (1072 B): entry glue + hooks + blit
 *   region C  .c    VA 0x25898..0x25c28 ( 912 B): readback + slot picker
 * Cortex-M0+ / Thumb-1, no libcalls, no '/' or '%'.
 *
 * v12 (WORKING on hardware, NOREARM) drew ONE slot at 0x2e000.  v13 keeps the v12
 * sleep path byte-for-byte in spirit -- same wrapper calls, same order, same waits,
 * same scrub, same NOREARM rule (never call 0x1848) -- and only makes the SOURCE
 * slot dynamic:
 *   * six 8 KiB slots at 0x2e000 + i*0x2000 (i = 0..5; 0x2e000..0x3a000, all past
 *     the app image end 0x2d180).  BEGIN <slot> erases exactly that slot, DATA /
 *     COMMIT program exactly that slot (every erase/program range-checked to it);
 *   * presence = the slot header 'SSAV' + len 8072, written by COMMIT only after
 *     the crc32 of the programmed bytes verified.  Register 0 is the 6-bit map;
 *   * the sleep-draw picks among the FILLED slots: MODE 0 (random, default) = LCG
 *     x = (x ^ ms_tick ^ ADC_raw<<20) * 1103515245 + 12345, index = (x>>16)*count>>16
 *     (no divide), never the slot already on the panel when another is filled;
 *     MODE 1 (sequential) = the next filled slot after the current one;
 *   * a sleep entry whose framebuffer already holds the CURRENT slot (after SHOW,
 *     or after a suppressed self-wake) sleeps silently, exactly like v12 (`skips`):
 *     the image only changes after a genuine wake re-dispatched the normal page;
 *   * CLEAR <slot|0xff> erases a slot's header sector (only slots that are present
 *     are touched); THRESHOLD 0 disables the saver (stock sleep), anything else
 *     enables it (the value is kept for readback; the draw is always at the sleep
 *     instant, as in v12).  0xFFii / 0xFEii stay the REG / CHEM aliases of main v1;
 *   * SHOW [slot] blits a slot while awake (no arg: the current one, else a pick).
 * A slot that fails to erase/program (dead or locked upper flash) never becomes
 * present and is simply never picked; nothing else depends on it.
 *
 * Sleep path (unchanged from v12): at the `bl 0xbe8c` decision inside 0xe5a8 (0xe632)
 *   1. bounded pre-wait for the panel BUSY line 0xea10, then the stock flush wrapper
 *      0x6598 (UART IRQ off, PA10 -> GPIO), PB0 := 1 (0xa1fe), 0x17a8 (touch INT masked);
 *   2. copy the slot's 8064 pixel bytes (past its 8-byte palette) into the EPD
 *      framebuffer 0x20004c3c INVERTED (fb 1 = white, blob 1 = black; same MSB-first
 *      order and 21-byte stride -- measured against the real set_px_cb 0xbff0);
 *   3. stock RAM write 0x19da + stock refresh 0x1858 with 0x20000a25 forced (full),
 *      then PB0 := 0, 0x5cfc, NVIC IRQ6 on;
 *   4. unconditional BUSY poll (<= 4 s) or a fixed 3 s fallback if BUSY never reads
 *      high; 100 ms settle; [SS_REARM only] 0x1848; 50 ms; scrub 0x20006e50/51 and
 *      any PA2 event; return 1 so the stock sleep entry 0xbe8c runs in the SAME pass.
 * A GENUINE wake (0xbe10 hook) sets SHOWN := 0xff so the loop re-dispatches the
 * current page; self-generated blips inside the window are counted, not acted on.
 *
 * Patches: cmd-0x50 dispatch entry 0x1c3a2 -> ss_cmd_stub; `bl 0xbe8c` @0xe632 ->
 * ss_sleep_hook; 0xbe12 -> ss_activity_hook; `bl 0xbeec` @0x1d67c -> ss_lptim_hook;
 * v15: `bl 0x1ec5c` @0xe694 -> ss_dispatch_hook.
 * The page table word for state 0x59 is left STOCK.  0xe00/app region never
 * addressed; 0x40/46/47/48 untouched.
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

#define B __attribute__((section(".b"), noinline))
#define C __attribute__((section(".c"), noinline))   /* region C: readback + picker */

#define TARGET_STATE   (*(volatile u32 *)0x200003ecu)
#define SHOWN_STATE    (*(volatile u32 *)0x200003f4u)
#define SYSCLK_HZ      (*(volatile u32 *)0x200003b8u)
#define LP_MODE        (*(volatile u8 *)0x20006bccu)    /* 0 awake, 1 deep sleep, 2 LPTIM re-sleep */
#define BL_DONE        (*(volatile u8 *)0x20000871u)    /* set by the loop 100 ms after a flush (0x1d5d2), with 0x1848 */
#define FLUSH_DONE     (*(volatile u8 *)0x20006e46u)
#define ARMED          (*(volatile u8 *)0x20006e44u)    /* touch INT (PA2) armed; the ISR clears it when it fires */
#define PA2_EVENT      (*(volatile u8 *)0x20006e43u)    /* touch INT fired (0x5a98); mode-1 loop wakes on it (0x1d630) */
#define BATT_PCT       (*(volatile u8 *)0x200003d4u)
#define BATT_MV        (*(volatile u32 *)0x20000a74u)
#define BATT_RAW       (*(volatile u32 *)0x20000a70u)   /* HC32 ADC raw sample (12 bit): entropy for the picker */
#define SETTINGS_253   (*(volatile u8 *)0x20000253u)
#define REPLY_CMD      (*(volatile u8 *)0x20006c7au)    /* a55a rx/tx struct + 0x12: reply cmd byte */
#define RX_EDGE_FLAG   (*(volatile u8 *)0x2000088cu)    /* set at 0x1d52a iff the wake flag was the UART RX edge (0x20006e51) */
#define WAKE_TOUCH     (*(volatile u8 *)0x20006e50u)    /* pending wake flag: PA8 line (ISR 0xd4f4, loop-cleared at 0x1d518) */
#define WAKE_RXEDGE    (*(volatile u8 *)0x20006e51u)    /* pending wake flag: UART RX edge (PA10) */
#define PERIOD_BYTE    (*(volatile u8 *)0x20000254u)    /* settings byte 6: LPTIM wake period, 5..60 s */
#define PANEL_ABSENT   (*(volatile u8 *)0x20006bc4u)    /* stock: set after repeated BUSY timeouts; driver skips waits */
#define FULL_FLAG      (*(volatile u8 *)0x20000a25u)    /* 0x1858 does a full refresh (0xc030) when 1 */
#define PARTIAL_CNT    (*(volatile u8 *)0x20000a24u)    /* 0x1858 (0x186e): partial updates since the last full one; >= 0x1e -> ONE full (0xc030) and := 0 */
#define SYNC_SKIP      (*(volatile u8 *)0x200008b8u)    /* 0x1858 tail (0x1888): 1 = skip the stock wait 0x1e160 (1.5 s cap, RST pulse on timeout); set 1 by every dispatch, cleared by the state-0 page group (0x1ee5e) */
#define SLEPT_FLAG     (*(volatile u8 *)0x20000872u)    /* 0xbe8c sets it at every deep sleep; 0xe79c clears it once awake */
#define LPTIM_FLAG     (*(volatile u8 *)0x20000873u)    /* 0xbeec sets it at an LPTIM re-sleep; 0xbe10 re-inits the touch IC when set */
#define RX_COUNT       (*(volatile u16 *)0x20006c90u)   /* UART rx byte count (0x6454); zero after the loop rearm 0x1d36c */
#define FB             ((u8 *)0x20004c3cu)              /* EPD framebuffer: 21 B x 384 rows, 1 = white, MSB-first */
#define FB_BYTES       8064u

#define F_PAYLOAD      ((const u8 *(*)(void))0x1d205u)
#define F_LEN          ((u32 (*)(void))0x1d219u)
#define F_ACK          ((void (*)(void))0x1d393u)
#define F_NAK          ((void (*)(void))0x1d379u)
#define F_REPLY        ((void (*)(const void *, u32))0x1d387u)
#define F_CLKDIV       ((u32 (*)(u32, u32))0x13e9u)
#define F_FL_INIT      ((u32 (*)(u32, u32))0x4eddu)
#define F_FL_UNLOCK    ((void (*)(u32))0x1afbdu)                /* bank 2 (0x2xxxx) -> SLOCK reg 4, bank 3 (0x3xxxx) -> reg 5 */
#define F_FL_MODE      ((u32 (*)(u32))0x4fe5u)
#define F_FL_ERASE     ((u32 (*)(u32))0x5035u)                  /* stock bound: sector < 0x40000 */
#define F_FL_PROG      ((u32 (*)(u32, const void *, u32))0x50c1u) /* self-verifying byte programmer, 0 = ok */
#define F_FLUSH_BEGIN  ((void (*)(void))0x6599u)                /* flush_cb prologue: NVIC IRQ6 off, PA10 -> GPIO */
#define F_PB0          ((void (*)(u32, u32))0xa1ffu)            /* 0xa1fe(1, v): PB0 := v around the EPD transaction */
#define F_TOUCH_MASK   ((void (*)(void))0x17a9u)                /* Gpio_ClearIrq(PA2) + Gpio_DisableIrq(PA2, falling) */
#define F_EPD_WRITE    ((void (*)(const u8 *))0x19dbu)          /* cmd 0x24 + 8064 B from the framebuffer */
#define F_EPD_REFRESH  ((void (*)(void))0x1859u)                /* full (0xc030) if FULL_FLAG else partial (0xc090) */
#define F_FLUSH_END    ((void (*)(void))0x5cfdu)                /* PA10 -> UART, Gpio_DisableIrq(PA10, falling) */
#define F_NVIC         ((void (*)(u32, u32, u32))0x17fdu)       /* (irq, prio, enable) */
#define F_TOUCH_ARM    ((void (*)(void))0x1849u)                /* Gpio_EnableIrq(PA2, falling): what the loop does 100 ms after a flush */
#define F_BUSY         ((u32 (*)(void))0xea11u)                 /* EPD BUSY line (PB3): nonzero while refreshing */
#define F_TICK         ((u32 (*)(void))0x5151u)                 /* ms tick (SysTick) */
#define F_DELAY        ((void (*)(u32))0xa4c5u)                 /* busy delay, ms */
#define F_DISPATCH     ((void (*)(u32))0x1ec5du)                /* stock page dispatcher: builds the LVGL page for a state; sets SYNC_SKIP := 1 and FULL_FLAG per 0xfa3c */
#define BUSY_CAP_MS    4000u
#define FALLBACK_MS    3000u
#define FULL_AT        0x1eu                                    /* PARTIAL_CNT value at which 0x1858 does a full update */
#define GRACE_MS       20000u                                   /* awake time after a genuine restore during which no NEW saver image is drawn */

#define SLOT0       0x2e000u
#define SLOT_SIZE   0x2000u
#define NSLOTS      6u
#define NO_SLOT     0xffu
#define SECTOR      512u
#define IMG_BYTES   8072u
#define SLOT_MAGIC  0x56415353u         /* 'SSAV' */
#define RAM_MAGIC   0x46525353u         /* 'SSRF' (v15: new layout) */
#define EXT_VERSION 15u
#define FLASH_SLOCK0 ((volatile u32 *)0x40020030u)   /* SLOCK0/1 at +0/+4, SLOCK2/3 at +0x10/+0x14 (RM 10.8.13-16) */

struct ss_state {
    u32 magic;
    u8 rx_active, draw_target, last_err, inits;
    u8 cmd_calls, sleep_hits, sleep_draws, show_blits;
    u8 act_calls, restores, act_mode, deferred;
    u8 act_caller, restore_caller, ignored_caller, ignored;
    u8 skips, ign_src, rst_src, busy_pre;
    u8 busy_ms10, busy_timeouts, saver_active, lptim_since_draw;
    u8 suppressed, lptim_wakes, scrubbed, busy_first;
    u8 pre_ms10, busy_fallbacks, pa2_scrubbed, rearms;
    u8 fb_lo, fb_hi, last_path, defers;
    u8 cur_slot, mode, thr_lo, thr_hi;      /* v13: slot on the panel (0xff none), 0 random / 1 sequential, threshold */
    u32 seed;                               /* v13: picker LCG state */
    u8 picks, map_at_draw, disabled, rx_slot; /* v13: picks made, slot map at the last sleep, saver off, slot being written */
    u32 rx_crc;                             /* v14 (+52): running crc32 STATE of the received DATA bytes (~state = crc) */
    u16 rx_frames, rx_bytes;                /* v14 (+56, +58): DATA frames programmed / bytes since BEGIN */
    u32 err_addr;                           /* v14 (+60): flash address of the last failed unlock/erase/program */
    u8 err_code, diag, slock_tries, pad;    /* v14 (+64..): driver code, COMMIT diag 0x10|rx_ok|flash_ok<<1, unlock tries (0 = never stuck) */
    u32 rst_tick;                           /* v15 (+68): ms tick at the last genuine restore (the grace clock) */
    u8 rst_pending, grace_armed, disp_calls, disp_forced;     /* +72: restore awaiting its dispatch; grace running; dispatches; restore dispatches forced FULL */
    u8 disp_busy_pre, disp_ms10, disp_timeouts, grace_defers; /* +76: BUSY high at the last dispatch; that wait x8 ms; capped waits; sleep decisions deferred by the grace */
    u8 quiet_ms10, wake_snap, rst_policy, rst_sync;           /* +80: awake ms/8 from the restore to the first sleep decision; wake flags; FULL_FLAG / SYNC_SKIP as the stock left them at the restore dispatch */
};                                          /* 84 B: 0x20007f80..0x20007fd4 */
#define V15_OFF 72u                         /* regs 84.. read the 12 bytes above verbatim */
#define DIAG_OFF 52u                        /* regs 61.. read the 16 diagnostic bytes above verbatim */
#define ST ((struct ss_state *)0x20007f80u)     /* above the Keil ZI+stack end 0x20007e58 */

/* ---------------- region A: slots, flash, command handler ---------------- */
static u32 slot_base(u32 i) { return SLOT0 + (i << 13); }
#define SLOT_PIXELS(i) ((const u8 *)(slot_base(i) + 8 + 8))   /* slot header (SSAV, len) + the blob's 8-byte palette */

static int present(u32 i)
{
    const u8 *h = (const u8 *)slot_base(i);
    return *(const u32 *)h == SLOT_MAGIC && (u32)(h[4] | (h[5] << 8)) == IMG_BYTES;
}

static u32 slot_map(void)
{
    u32 m = 0;
    for (u32 i = 0; i < NSLOTS; ++i)
        if (present(i))
            m |= 1u << i;
    return m;
}

/* Every program lands inside the slot BEGIN selected: never the app image, never another slot. */
static int fl_ok(u32 a, u32 n)
{
    if (ST->rx_slot >= NSLOTS)
        return 0;
    u32 lo = slot_base(ST->rx_slot), hi = lo + SLOT_SIZE;
    return a >= lo && a < hi && n != 0 && n <= hi - a;
}

static u32 fl_prepare(void)
{
    return F_FL_INIT(F_CLKDIV(SYSCLK_HZ, 0x3d0900u) & 0xffu, 1);
}

static u32 fl_mode(u32 m)
{
    for (u32 i = 0; i < 1000; ++i)
        if (F_FL_MODE(m) == 0)
            return 0;
    return 6;
}

/* Record where and why a flash step failed (read back as regs 69..73). */
static u32 fl_fail(u32 a, u32 code)
{
    ST->err_addr = a;
    ST->err_code = (u8)code;
    return code;
}

/* The stock unlock (one SLOCK bit for the 2 KiB block holding `a`), then RM 10.3.1 Step 7:
 * read the bit back and retry until it is 1.  bank = a[19:16] -> SLOCK0/1 at +0/+4, SLOCK2/3
 * at +0x10/+0x14 (the stock table {1,4,5} at 0x1afe4), bit = a[15:11]. */
static u32 fl_unlock(u32 a)
{
    u32 bank = (a >> 16) & 0xfu;
    volatile u32 *reg = FLASH_SLOCK0 + (bank < 2 ? bank : bank + 2);
    u32 bit = 1u << ((a & 0xffffu) >> 11);
    for (u32 i = 1; i <= 8; ++i) {
        F_FL_UNLOCK(a);
        if (*reg & bit) {
            ST->slock_tries = (u8)i;
            return 0;
        }
    }
    ST->slock_tries = 0;
    return fl_fail(a, 0xa);
}

static u32 fl_write(u32 a, const u8 *src, u32 n)
{
    if (!fl_ok(a, n))
        return 0xfe;
    while (n) {
        u32 piece = 0x800u - (a & 0x7ffu);
        if (piece > n)
            piece = n;
        u32 r = fl_unlock(a);
        if (r)
            return r;
        r = fl_mode(1);
        if (r == 0)
            r = F_FL_PROG(a, src, piece);
        if (r)
            return fl_fail(a, r);
        a += piece; src += piece; n -= piece;
    }
    return 0;
}

/* Erase `nsect` 512-B sectors from `a` (a slot base: 16 for BEGIN, 1 for CLEAR), each read
 * back as all-0xFF (a protected/absent sector "erases" without effect, RM 10.6.1). 0 = ok. */
static u32 fl_erase(u32 a, u32 nsect)
{
    if (a < SLOT0 || a + nsect * SECTOR > SLOT0 + NSLOTS * SLOT_SIZE)
        return 0xfd;
    if (fl_prepare())
        return 0xfc;
    for (; nsect; --nsect, a += SECTOR) {
        u32 r = fl_unlock(a);
        if (r)
            return r;
        r = fl_mode(2);
        if (r == 0 && F_FL_ERASE(a))
            r = 7;
        if (r == 0) {
            const u32 *w = (const u32 *)a;
            for (u32 i = 0; i < SECTOR / 4; ++i)
                if (w[i] != 0xffffffffu) {
                    r = 8;
                    break;
                }
        }
        if (r)
            return fl_fail(a, r);
    }
    return 0;
}

/* crc32 (zlib polynomial, reflected) as a resumable state: init ~0, finish with ~c. */
static u32 crc32_step(u32 c, const u8 *p, u32 n)
{
    while (n--) {
        c ^= *p++;
        for (u32 k = 0; k < 8; ++k)
            c = (c >> 1) ^ (0xedb88320u & (0u - (c & 1u)));
    }
    return c;
}

static u32 crc32_flash(u32 a, u32 n)
{
    return ~crc32_step(~0u, (const u8 *)a, n);
}

static void ss_init(void)
{
    if (ST->magic == RAM_MAGIC)
        return;
    volatile u8 *z = (volatile u8 *)ST;
    for (u32 i = 4; i < sizeof(struct ss_state); ++i)
        z[i] = 0;
    ST->magic = RAM_MAGIC;
    ST->inits = 1;
    ST->cur_slot = NO_SLOT;                          /* mode 0 = random, disabled 0 = saver ON by default */
}

/* ---------------- region C: the picker ---------------- */
/* Choose a slot among the filled ones (`map` != 0).  Sequential: the next filled slot after the
 * current one.  Random: an LCG stirred with the ms tick (how long the user was active) and the
 * battery ADC raw sample, index = (x >> 16) * count >> 16 (no divide); the slot already on the
 * panel is excluded whenever another one is filled. */
C static u32 pick(u32 map)
{
    u32 cur = ST->cur_slot;
    if (ST->mode == 1) {
        u32 s = cur < NSLOTS ? cur : NSLOTS - 1;
        for (u32 k = 0; k < NSLOTS; ++k) {
            if (++s >= NSLOTS)
                s = 0;
            if ((map >> s) & 1u)
                return s;
        }
        return NO_SLOT;
    }
    u32 cand = map;
    if (cur < NSLOTS)
        cand &= ~(1u << cur);
    if (!cand)
        cand = map;
    u32 count = 0;
    for (u32 i = 0; i < NSLOTS; ++i)
        count += (cand >> i) & 1u;
    u32 x = ST->seed ^ F_TICK() ^ (BATT_RAW << 20);
    x = x * 1103515245u + 12345u;
    ST->seed = x;
    u32 r = ((x >> 16) * count) >> 16;
    for (u32 i = 0; i < NSLOTS; ++i)
        if ((cand >> i) & 1u) {
            if (r == 0)
                return i;
            --r;
        }
    return NO_SLOT;
}

/* ---------------- region B: the blit ---------------- */
/* 1 iff the framebuffer already holds slot `slot`'s (inverted) image. */
C static int fb_matches(u32 slot)          /* region C since v15 (region B budget) */
{
    const u8 *s = SLOT_PIXELS(slot);
    for (u32 i = 0; i < FB_BYTES; ++i)
        if (FB[i] != (u8)~s[i])
            return 0;
    return 1;
}

/* Poll the driver's BUSY reader (PB3) until idle, <= BUSY_CAP_MS.  Returns the FIRST
 * reading (1 = the panel was busy when we looked).  The stock wait 0x1e160 is not used:
 * its timeout action 0x1e11c pulses the panel RST, and it is skipped when PANEL_ABSENT. */
B static u32 wait_busy(volatile u8 *ms10_out, volatile u8 *timeouts)
{
    u32 t0 = F_TICK(), dt = 0;
    u32 first = F_BUSY(), busy = first;
    while (busy) {
        F_DELAY(5);
        dt = F_TICK() - t0;
        if (dt >= BUSY_CAP_MS) {
            (*timeouts)++;
            break;
        }
        busy = F_BUSY();
    }
    dt = dt >> 3;                                   /* ~ /10 without a divide (x0.8) */
    *ms10_out = dt > 255 ? 255 : (u8)dt;
    return first;
}

/* The bounded pre-wait every path that is about to touch the panel (or deep-sleep it) runs:
 * a stock refresh may still be in flight (records busy_pre / pre_ms10). */
B static void pre_wait(void)
{
    ST->busy_pre = (u8)wait_busy(&ST->pre_ms10, &ST->busy_timeouts);
}

/* Copy + invert the slot's image into the framebuffer and push it exactly the way the
 * stock flush callback 0xbf80 does (same wrapper calls in the same order), as a FULL refresh.
 * Callers pre_wait() first.  Leaves PARTIAL_CNT at FULL_AT so the NEXT stock flush -- the
 * page that replaces this full-contrast image -- is a FULL update whatever the stock
 * policy for that page says (v15: a partial LUT cannot erase a dense saver). */
B static void ss_push(u32 slot)
{
    const u8 *s = SLOT_PIXELS(slot);
    F_FLUSH_BEGIN();
    F_PB0(1, 1);
    F_TOUCH_MASK();
    for (u32 i = 0; i < FB_BYTES; ++i)
        FB[i] = (u8)~s[i];
    ST->fb_lo = (u8)FB_BYTES;
    ST->fb_hi = (u8)(FB_BYTES >> 8);
    F_EPD_WRITE(FB);
    u8 save = FULL_FLAG;
    FULL_FLAG = 1;
    F_EPD_REFRESH();
    FULL_FLAG = save;
    F_PB0(1, 0);
    F_FLUSH_END();
    F_NVIC(6, 3, 1);
    PARTIAL_CNT = FULL_AT;                          /* 0x1858 just zeroed it (full update); re-arm: next stock flush = FULL */
}

/* Called in place of `bl 0xbe8c` at 0xe632.  Returns 1 = run the stock sleep entry now,
 * 0 = skip it this pass. */
B u32 ss_sleep_draw(void)
{
    ss_init();
    pre_wait();                                     /* v15: never deep-sleep (or overwrite) the panel mid-refresh, on ANY path */
    if (ST->disabled) {                             /* THRESHOLD 0: stock sleep, nothing drawn */
        ST->sleep_hits++;
        return 1;
    }
    u32 map = slot_map();
    ST->map_at_draw = (u8)map;
    if (!map) {
        ST->sleep_hits++;
        return 1;
    }
    u32 cur = ST->cur_slot;
    u32 held = cur < NSLOTS && ((map >> cur) & 1u) && fb_matches(cur);
    if (ST->grace_armed) {                          /* v15: a genuine restore happened `dt` ms of awake time ago */
        u32 dt = F_TICK() - ST->rst_tick;
        if (ST->quiet_ms10 == 0) {                  /* first sleep decision after the restore: how long main kept us awake */
            u32 q = dt >> 3;
            ST->quiet_ms10 = q > 255 ? 255 : (q ? (u8)q : 1);
        }
        if (dt < GRACE_MS) {
            if (!held) {                            /* the restored page is on the panel: keep it there, decide again next pass */
                if (ST->grace_defers < 255)
                    ST->grace_defers++;
                return 0;
            }
        } else
            ST->grace_armed = 0;
    }
    ST->sleep_hits++;
    if (held) {                                     /* already on the panel: sleep silently */
        ST->skips++;
        ST->last_path = 3;
        ST->saver_active = 1;
        ST->deferred = 0;
        return 1;
    }
    ST->rst_pending = 0;                            /* a saver draw supersedes any restore that never got its dispatch */
    u32 s = pick(map);
    ST->cur_slot = (u8)s;
    ST->picks++;
    ST->draw_target = (u8)TARGET_STATE;
    ss_push(s);
    ST->last_path = 1;
    F_DELAY(5);                                     /* let BUSY assert after the 0x20 activation */
    u32 first = wait_busy(&ST->busy_ms10, &ST->busy_timeouts);
    ST->busy_first = (u8)first;
    if (!first) {                                   /* BUSY never seen: give the panel a fixed refresh time */
        F_DELAY(FALLBACK_MS);
        ST->busy_fallbacks++;
    }
    F_DELAY(100);                                   /* the loop's own spacing after a flush */
#if SS_REARM
    F_TOUCH_ARM();                                  /* what 0x1d5d6 does 100 ms after every flush */
    ST->rearms++;
#endif
    F_DELAY(50);                                    /* let any blip the tail of the refresh raises land ... */
    if (WAKE_TOUCH | WAKE_RXEDGE) {                 /* ... then drop the flags (the loop clears these same bytes at 0x1d518) */
        WAKE_TOUCH = 0;
        WAKE_RXEDGE = 0;
        ST->scrubbed++;
    }
    for (u32 i = 0; i < 3 && PA2_EVENT; ++i) {      /* a late touch-INT blip: consume it and re-arm, bounded */
        PA2_EVENT = 0;
        ST->pa2_scrubbed++;
#if SS_REARM
        ARMED = 1;
        F_TOUCH_ARM();
        F_DELAY(50);
#endif
    }
#if !SS_REARM
    ARMED = 0;                                      /* touch INT stays masked while asleep; the loop re-arms on the next awake pass (0xe7f0) */
#endif
    ST->saver_active = 1;                           /* suppression window: see ss_activity */
    ST->lptim_since_draw = 0;
    ST->sleep_draws++;
    if (RX_COUNT != 0 && !ST->deferred) {           /* main talked during the blit: let the loop see it first, once */
        ST->deferred = 1;
        ST->defers++;
        return 0;
    }
    ST->deferred = 0;
    return 1;
}

/* Activity hook (0xbe12, before 0xbe10 zeroes LP_MODE).  `caller` is 0xbe10's own return
 * address (its pushed lr), i.e. WHICH main-loop path woke us:
 *   0x1d473 first run at boot            -> 1  never restore
 *   0x1d48f UART frame while asleep      -> 2  main sent a command: restore
 *   0x1d531 PA8 line / UART RX-edge flags-> 3  input: restore, except the PA8 blips our own
 *                                              refresh raises (window rule below)
 *   0x1d635 PA2 (touch INT) in low power -> 4  a touch on the panel: restore */
C static u32 caller_code(u32 lr)           /* region C since v15 */
{
    switch (lr) {
    case 0x1d473u: return 1;
    case 0x1d48fu: return 2;
    case 0x1d531u: return 3;
    case 0x1d635u: return 4;
    default: return 5;
    }
}

/* LPTIM periodic wake (mode 1 -> 0xbeec re-sleep), hooked at 0x1d67c: the only clock that keeps
 * counting while the MCU sleeps (the ms tick stops in WFI).  It expires the suppression window. */
C void ss_lptim(void)                    /* region C since v15 */
{
    ss_init();
    ST->lptim_wakes++;
    if (ST->lptim_since_draw < 255)
        ST->lptim_since_draw++;
}

/* While the saver is on the panel (saver_active), a wake restores the normal page ONLY for
 * genuine input: a UART frame while asleep (2), the touch INT (4), or the flags path (3) with
 * the UART RX-edge source or, for the PA8 line, once at least one LPTIM wake has passed since
 * the draw.  Restoring = SHOWN := 0xff, so the loop's `target != shown` path (0xe65c)
 * re-dispatches the current page itself.  Everything else is counted as suppressed. */
B void ss_activity(u32 caller)
{
    ss_init();
    ST->act_calls++;
    u32 mode = LP_MODE;
    u32 code = caller_code(caller);
    u32 src = RX_EDGE_FLAG;
    ST->act_mode = (u8)mode;
    ST->act_caller = (u8)code;
    ST->wake_snap = (u8)((src & 1u) | (SLEPT_FLAG ? 2u : 0u) | (LPTIM_FLAG ? 4u : 0u) | (F_BUSY() ? 8u : 0u)
                         | (PA2_EVENT ? 16u : 0u) | (ARMED ? 32u : 0u) | (mode << 6));   /* v15: raw wake-source snapshot, before 0xbe10 pulses RST */
    if (!ST->saver_active) {
        ST->ignored++;
        ST->ignored_caller = (u8)code;
        ST->ign_src = (u8)src;
        return;
    }
    u32 genuine = (code == 2) || (code == 4) || (code == 3 && (src != 0 || ST->lptim_since_draw != 0));
    if (mode != 0 && genuine) {
        ST->saver_active = 0;
        SHOWN_STATE = 0xffu;                        /* main loop re-dispatches TARGET through ss_dispatch */
        ST->rst_pending = 1;                        /* v15: that dispatch waits for the panel and forces ONE full update */
        ST->grace_armed = 1;                        /* v15: no NEW saver image for GRACE_MS of awake time */
        ST->rst_tick = F_TICK();
        ST->quiet_ms10 = 0;
        ST->restores++;
        ST->restore_caller = (u8)code;
        ST->rst_src = (u8)src;
        return;
    }
    ST->suppressed++;
    ST->ignored_caller = (u8)code;
    ST->ign_src = (u8)src;
}

/* ---------------- region C: the page-dispatch hook (v15) ---------------- */
/* Called in place of `bl 0x1ec5c` at 0xe694 (0xe65c: shown := target; dispatch(target)), i.e. for
 * EVERY page build.  A refresh still in flight (the saver's, or a restore's own full update) is
 * waited out first -- bounded, never the stock 0x1e160 whose 1.5 s timeout pulses RST -- so the
 * flush that follows this build can never collide with it.  After a genuine restore the page
 * the stock built is forced to ONE full update (PARTIAL_CNT lever, re-asserted here in case an
 * LVGL flush consumed the one ss_push left) and its completion is left to our bounded waits
 * (SYNC_SKIP := 1) instead of the stock RST-on-timeout wait.  What the stock would have done is
 * recorded (rst_policy = FULL_FLAG it chose, rst_sync = SYNC_SKIP it left). */
C void ss_dispatch(u32 state)
{
    ss_init();
    ST->disp_calls++;
    u32 first = F_BUSY();
    ST->disp_busy_pre = (u8)first;
    if (first)
        wait_busy(&ST->disp_ms10, &ST->disp_timeouts);
    F_DISPATCH(state);
    if (ST->rst_pending) {
        ST->rst_policy = FULL_FLAG;                 /* 0 = the stock would have used the partial LUT (the v14 freeze) */
        ST->rst_sync = SYNC_SKIP;                   /* 0 = the stock would have waited <= 1.5 s in 0x1858 and pulsed RST on timeout */
        PARTIAL_CNT = FULL_AT;
        SYNC_SKIP = 1;
        ST->rst_pending = 0;
        ST->disp_forced++;
    }
}

/* ---------------- region C: readback ---------------- */
#define NREGS 100u
/* Where each register's byte comes from: < 0x80 = that byte of ss_state verbatim; 0x80|k = the byte at
 * REG_LIVE[k]; 0xc0|k = computed case k.  Register meanings (unchanged numbering since v12..v14):
 *  0 slot map, 1 cmd_calls, 2 sleep_hits, 3 sleep_draws, 4 show_blits, 5 restores, 6 act_calls, 7 act_mode,
 *  8 SHOWN, 9 TARGET, 10 LP_MODE, 11 rx_active, 12 last_err, 13 inits, 14 draw_target, 15 'V', 16 ext_version,
 *  17 batt %, 18 BL_DONE, 19 FLUSH_DONE, 20 ARMED, 21/22 mV, 23 variant, 24 act_caller, 25 restore_caller,
 *  26 ignored_caller, 27 ignored, 28 fb_match (LIVE), 29 ign_src, 30 rst_src, 31 skips, 32 busy_ms10,
 *  33 busy_timeouts, 34 saver_active, 35 lptim_since_draw, 36 suppressed, 37 lptim_wakes, 38 scrubbed,
 *  39 LPTIM period s, 40 busy_first, 41 busy_pre, 42 pre_ms10, 43 busy_fallbacks, 44 pa2_scrubbed, 45 rearms,
 *  46/47 fb bytes, 48 last_path, 49 PANEL_ABSENT, 50 PA2_EVENT, 51 defers, 52 cur_slot, 53 mode, 54/55 threshold,
 *  56 picks, 57 map_at_draw, 58 disabled, 59 rx_slot, 60 seed lo, 61..76 the v14 diagnostic bytes (rx_crc state,
 *  rx_frames, rx_bytes, err_addr, err_code, diag, slock_tries, pad), 77 FLASH_IFR[7:0], 78 FLASH_CR[7:0],
 *  79 SLOCK2[31:24], 80/81/82 SLOCK3 b0/b1/b2, 83 first DATA byte of the slot being written,
 *  v15: 84 rst_pending, 85 grace_armed, 86 disp_calls, 87 disp_forced, 88 disp_busy_pre, 89 disp_ms10,
 *  90 disp_timeouts, 91 grace_defers, 92 quiet_ms10, 93 wake_snap, 94 rst_policy, 95 rst_sync,
 *  96 PARTIAL_CNT (LIVE), 97 FULL_FLAG (LIVE), 98 SYNC_SKIP (LIVE), 99 grace seconds. */
#define L(k) (0x80u | (k))
#define X(k) (0xc0u | (k))
static const u8 REG_SRC[NREGS] __attribute__((section(".ctab"))) = {   /* .ctab: read-only tables placed in region C by the linker script */
    X(0), 8, 9, 10, 11, 13, 12, 14, L(0), L(1), L(2), 4, 6, 7, 5, X(1), X(2), L(3), L(4), L(5),
    L(6), L(7), L(8), X(3), 16, 17, 18, 19, X(4), 21, 22, 20, 24, 25, 26, 27, 28, 29, 30, L(9),
    31, 23, 32, 33, 34, 35, 36, 37, 38, L(10), L(11), 39, 40, 41, 42, 43, 48, 49, 50, 51,
    44, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, X(5), X(6), X(7),
    X(8), X(9), X(10), X(11), 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, L(12), L(13), L(14), X(12),
};
static const u32 REG_LIVE[15] __attribute__((section(".ctab"))) = {
    0x200003f4u, 0x200003ecu, 0x20006bccu, 0x200003d4u, 0x20000871u, 0x20006e46u, 0x20006e44u,
    0x20000a74u, 0x20000a75u, 0x20000254u, 0x20006bc4u, 0x20006e43u, 0x20000a24u, 0x20000a25u, 0x200008b8u,
};

C static u32 reg_read(u32 i)                        /* i < NREGS (callers check) */
{
    u32 src = REG_SRC[i];
    if (src < 0x80u)
        return ((volatile u8 *)ST)[src];
    if (src < 0xc0u)
        return *(volatile u8 *)REG_LIVE[src & 0x3fu];
    switch (src & 0x3fu) {
    case 0:  return slot_map();                 /* 6-bit slot presence map */
    case 1:  return 'V';                        /* fixed sentinel */
    case 2:  return EXT_VERSION;
    case 3:  return SS_REARM ? 3u : 4u;          /* variant: 3 = direct blit + touch re-arm, 4 = no re-arm */
    case 4: {                                   /* LIVE: framebuffer currently holds the current slot's inverted image */
        u32 c = ST->cur_slot;
        return (c < NSLOTS && present(c) && fb_matches(c)) ? 1u : 0u;
    }
    case 11: return ST->rx_slot < NSLOTS ? *(const u8 *)(slot_base(ST->rx_slot) + 8) : 0u;
    case 12: return GRACE_MS / 1000u;
    default: {                                  /* 5..10 = regs 77..82: IFR, CR, SLOCK2 b3, SLOCK3 b0/b1/b2 */
        u32 k = (src & 0x3fu) - 4u;                                  /* 1..6 */
        volatile u8 *slock2 = (volatile u8 *)(FLASH_SLOCK0 + 4);     /* 0x40020040; IFR = -0x1c, CR = -0x20 */
        if (k == 1)
            return *(volatile u32 *)(slock2 - 0x1c);
        if (k == 2)
            return *(volatile u32 *)(slock2 - 0x20);
        /* k = 3..6 = byte index from SLOCK2: 3 -> SLOCK2 b3 (bits 31:24), 4/5/6 -> SLOCK3 b0/b1/b2 */
        return *(volatile u32 *)(slock2 + (k & ~3u)) >> ((k & 3u) << 3);
    }
    }
}

/* Reply exactly like the stock version query (cmd 0x02 handler 0x1cbd0: version(2),
 * pct(1), mV(2), raw(2), settings[5](1)) with the two version bytes replaced: main
 * caches them at 0x20b166/7 and appends them as the LAST TWO BYTES of every W 02
 * inventory reply -- a readback that needs NO main change.  b0 is never 00/ff (main
 * resets the e-ink MCU on a 00/00 or ff/ff version); idx 0xff restores 01 3f. */
C static void masquerade(u32 b0, u32 b1)
{
    u8 b[8];
    b[0] = (u8)b0; b[1] = (u8)b1;
    b[2] = BATT_PCT;
    b[3] = (u8)BATT_MV; b[4] = (u8)(BATT_MV >> 8);
    b[5] = (u8)BATT_RAW; b[6] = (u8)(BATT_RAW >> 8);
    b[7] = SETTINGS_253;
    REPLY_CMD = 0x02;
    F_REPLY(b, 8);
}

C static int reg_reply(u32 idx)
{
    if (idx == 0xff) {
        masquerade(0x01, 0x3f);
        return 1;
    }
    if (idx >= NREGS)
        return 0;
    masquerade(0x80u | idx, reg_read(idx) & 0xffu);
    return 1;
}

/* ---------------- region A: a55a command 0x50 ---------------- */
/* 00 <slot 0..5> <len16=8072> BEGIN (erase that slot) | 01 <off16> data DATA | 02 <crc32> COMMIT |
 * 03 [slot] SHOW = blit a slot to the panel NOW (no arg: the current slot, else a pick) |
 * 04 <v16> THRESHOLD: 0 = saver off, else on; 0xFFii = REG ii alias (installed main v1), 0xFExx reserved |
 * 05 <slot|0xff> CLEAR (erase the header sector of a present slot / all of them) |
 * 06 <0 random|1 sequential> MODE | 07 STATUS (plain reply) | 08 <ii> REG (main v2 native) */
void ss_command(void)
{
    ss_init();
    ST->cmd_calls++;
    const u8 *p = F_PAYLOAD();
    u32 n = F_LEN();
    if (n == 0)
        goto nak;
    switch (p[0]) {
    case 0x00:
        ST->rx_active = 0;
        if (n != 4 || p[1] >= NSLOTS || (u32)(p[2] | (p[3] << 8)) != IMG_BYTES)
            goto nak;
        ST->rx_slot = p[1];
        {
            volatile u8 *z = (volatile u8 *)ST;             /* v14: fresh diagnostics for this transfer */
            for (u32 i = DIAG_OFF; i < sizeof(struct ss_state); ++i)
                z[i] = 0;
        }
        ST->rx_crc = ~0u;
        if (fl_erase(slot_base(p[1]), SLOT_SIZE / SECTOR))
            goto fail;
        ST->rx_active = 1;
        break;
    case 0x01: {
        if (!ST->rx_active || n < 4 || n > 3 + 200)
            goto nak;
        u32 off = (u32)(p[1] | (p[2] << 8)), cnt = n - 3;
        if (off > IMG_BYTES || cnt > IMG_BYTES - off || fl_prepare() || fl_write(slot_base(ST->rx_slot) + 8 + off, p + 3, cnt))
            goto fail;
        if (off == 0) {                                    /* v14: a pass restarting at offset 0 restarts the stream crc (resend) */
            ST->rx_crc = ~0u;
            ST->rx_frames = 0;
            ST->rx_bytes = 0;
        }
        ST->rx_crc = crc32_step(ST->rx_crc, p + 3, cnt);   /* v14: what actually arrived, in order */
        ST->rx_frames++;
        ST->rx_bytes += (u16)cnt;
        break;
    }
    case 0x02: {
        if (!ST->rx_active || n != 5)
            goto nak;
        u32 base = slot_base(ST->rx_slot);
        u32 want = (u32)p[1] | ((u32)p[2] << 8) | ((u32)p[3] << 16) | ((u32)p[4] << 24);
        u32 d = 0x10u;                                     /* v14: bit 0 = received stream matches, bit 1 = flash matches */
        if (~ST->rx_crc == want)
            d |= 1u;
        if (crc32_flash(base + 8, IMG_BYTES) == want)
            d |= 2u;
        ST->diag = (u8)d;
        if (!(d & 2u))
            goto nak;                                      /* rx_active stays 1: DATA may be re-sent and COMMIT retried */
        ST->rx_active = 0;
        u8 h[8] = {'S', 'S', 'A', 'V', (u8)IMG_BYTES, (u8)(IMG_BYTES >> 8), 0, 0};
        if (fl_prepare() || fl_write(base, h, 8) || !present(ST->rx_slot))
            goto fail;
        break;
    }
    case 0x03: {
        if (n > 2)
            goto nak;
        u32 s;
        if (n == 2) {
            s = p[1];
            if (s >= NSLOTS || !present(s))
                goto nak;
        } else {
            s = ST->cur_slot;
            if (s >= NSLOTS || !present(s))
                s = pick(slot_map());
            if (s == NO_SLOT)
                goto nak;
        }
        ST->cur_slot = (u8)s;
        pre_wait();
        ss_push(s);                                 /* awake blit: proves the slot on the panel */
        ST->show_blits++;
        ST->last_path = 2;
        ST->saver_active = 0;
        break;
    }
    case 0x04: {
        if (n != 3)
            goto nak;
        u32 v = (u32)(p[1] | (p[2] << 8));
        if ((v & 0xff00u) == 0xff00u) {              /* REG alias (main v1 path) */
            if (!reg_reply(v & 0xffu))
                goto nak;
            return;
        }
        if ((v & 0xff00u) == 0xfe00u)                /* CHEM alias: not in this build */
            goto nak;
        ST->thr_lo = (u8)v;
        ST->thr_hi = (u8)(v >> 8);
        ST->disabled = v == 0;
        break;
    }
    case 0x05: {
        if (n != 2)
            goto nak;
        ST->rx_active = 0;
        u32 s = p[1];
        if (s == 0xffu) {
            for (u32 i = 0; i < NSLOTS; ++i)
                if (present(i) && fl_erase(slot_base(i), 1))
                    goto fail;
        } else {
            if (s >= NSLOTS)
                goto nak;
            if (present(s) && fl_erase(slot_base(s), 1))
                goto fail;
        }
        break;
    }
    case 0x06:
        if (n < 2 || n > 3 || p[1] > 1)
            goto nak;
        ST->mode = p[1];
        break;
    case 0x07: {
        if (n != 1)
            goto nak;
        u8 b[16];
        b[0] = 'S'; b[1] = '7';
        for (u32 i = 0; i < 14; ++i)
            b[2 + i] = (u8)reg_read(i);
        F_REPLY(b, 16);
        return;
    }
    case 0x08:
        if (n != 2 || !reg_reply(p[1]))
            goto nak;
        return;
    default:
        goto nak;
    }
    F_ACK();
    return;
fail:
    ST->rx_active = 0;
    ST->last_err = (u8)p[0] + 1;
nak:
    F_NAK();
}
