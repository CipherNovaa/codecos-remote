/* Touchscreen CAST/RESUME edge sender for RT150T (ARMv6-M).
 * The wrapper retains the original lv_event_t pointer so unhandled keys tail-call
 * the stock CLICKED-only callback byte-for-byte in behaviour. */
.syntax unified
.thumb
.cpu cortex-m0plus

.section .text,"ax",%progbits
.global ss_key
.thumb_func
ss_key:
    push {r4, lr}
    mov r4, r0
    bl ss_key_impl
    cmp r0, #0
    beq 1f
    pop {r4, pc}
1:
    mov r0, r4
    /* ARMv6-M cannot `pop {…, lr}`.  Recover the saved caller LR in a
     * temporary callee-saved register, restore that register, then tail-call
     * stock so its return goes directly to our caller with this frame removed. */
    mov r3, r5
    pop {r4, r5}
    mov lr, r5
    mov r5, r3
    ldr r3, =(stock_key_callback + 1)
    bx r3

/* Numeric keypad callback.  It has a different logical-key resolver and must
 * therefore tail-call its own stock callback for a non-key event. */
.global ss_key_num
.thumb_func
ss_key_num:
    push {r4, lr}
    mov r4, r0
    bl ss_key_num_impl
    cmp r0, #0
    beq 2f
    pop {r4, pc}
2:
    mov r0, r4
    mov r3, r5
    pop {r4, r5}
    mov lr, r5
    mov r5, r3
    ldr r3, =(stock_numeric_callback + 1)
    bx r3

.global ss_key_table
.thumb_func
ss_key_table:
    push {r4, lr}
    bl ss_key_table_impl
    pop {r4, pc}

.ltorg

.section .grp,"ax",%progbits
.global ss_key_grp
.thumb_func
ss_key_grp:
    push {r4, lr}
    bl ss_key_grp_impl
    pop {r4, pc}
