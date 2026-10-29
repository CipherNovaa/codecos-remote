.syntax unified
.thumb
.cpu cortex-m4
.section .text,"ax",%progbits
.global hold_ir_gate
.thumb_func
/* r7 is byte8 from the 0x43 frame.  Continue at the original post-BL site.
 * A skipped release returns non-zero, the same "handled" shape required by
 * its immediately following cmp r0,#0. */
hold_ir_gate:
    cbz r7, no_ir
    bl ir_dispatch
    b.w ir_continue
no_ir:
    movs r0, #1
    b.w ir_continue
