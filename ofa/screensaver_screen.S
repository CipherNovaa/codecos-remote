/* Entry glue for the RT150T screensaver extension v15 (Thumb-1, Cortex-M0+); v12 glue + the
 * v15 page-dispatch hook.  Lives INSIDE the stock-size image (region B, free glyph-bitmap
 * padding at 0x25290).
 * ARMv6-M has no `b.w`: every patch site is a `bl`, every branch here is 16-bit or `bl`. */
.syntax unified
.thumb
.cpu cortex-m0plus

.section .entry,"ax",%progbits

/* Dispatch-table target for a55a command 0x50 (entry 0x1c3a2).  The dispatcher
 * frame is push {r4, r5, r7, lr}; stock stubs are `bl handler; bl 0x1d40c; pop`. */
.global ss_cmd_stub
.thumb_func
ss_cmd_stub:
    bl ss_command
    ldr r3, =(dispatch_send + 1)
    blx r3
    pop {r4, r5, r7, pc}

/* Replaces `bl 0xbe8c` at 0xe632 (idle->deep-sleep decision inside 0xe5a8):
 * ss_sleep_draw() returns 1 = run the stock sleep entry now, 0 = skip this pass. */
.global ss_sleep_hook
.thumb_func
ss_sleep_hook:
    push {r7, lr}
    bl ss_sleep_draw
    cmp r0, #0
    beq 1f
    ldr r3, =(sleep_stock + 1)
    blx r3
1:
    pop {r7, pc}

/* Replaces `ldr r0, [pc, #0x108]; movs r4, #0` at 0xbe12 (after 0xbe10's own
 * push {r4, r5, r7, lr}); re-creates r0/r4.  Passes 0xbe10's pushed lr (its caller's
 * return address, at [sp+16] after our push) to ss_activity so it knows which wake path
 * it is on. */
.global ss_activity_hook
.thumb_func
ss_activity_hook:
    push {lr}
    ldr r0, [sp, #16]
    bl ss_activity
    movs r4, #0
    ldr r0, =0x20006bcc
    pop {pc}

/* Replaces `bl 0xbeec` at 0x1d67c (mode-1 loop: LPTIM wake -> re-sleep).  Count, then the
 * stock consumer exactly as before. */
.global ss_lptim_hook
.thumb_func
ss_lptim_hook:
    push {r7, lr}
    bl ss_lptim
    ldr r3, =(lptim_stock + 1)
    blx r3
    pop {r7, pc}

/* v15: replaces `bl 0x1ec5c` at 0xe694 (page redraw 0xe65c: shown := target; dispatch(target)).
 * ss_dispatch(state) pre-waits the panel, runs the stock dispatcher itself, then forces the
 * restore's flush to a full update.  0xe65c keeps &shown/&target in r4/r5 across the call:
 * both are callee-saved through the C function and the stock dispatcher. */
.global ss_dispatch_hook
.thumb_func
ss_dispatch_hook:
    push {r7, lr}
    bl ss_dispatch
    pop {r7, pc}

.ltorg
