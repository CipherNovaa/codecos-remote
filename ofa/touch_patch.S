/* Pinned OFA 0.5.7: replace only the screen logical-key lookup.
 * Entry: r6=device slot, r9=logical key. Exit: r4=pool index or zero.
 * Only r0/r1/r4/flags are scratch; all other registers and SP are preserved.
 * The caller already checked BLE enablement and key-mover/macro overrides.
 */
.syntax unified
.thumb
.cpu cortex-m4
.section .hook,"ax",%progbits
.global touch_hook
touch_hook:
    b.w touch_lookup

.section .text,"ax",%progbits
.global touch_lookup
.thumb_func
touch_lookup:
    sub.w r1, r9, #0x0d
    cmp r1, #9
    bhi extras
    ldr r0, digit_table
    ldrb r4, [r0, r1]
    b.w lookup_done
extras:
    movs r4, #0
    ldr r0, devices
    add.w r0, r0, r6, lsl #2
    ldrb r0, [r0, #0x17]
    lsrs r0, r0, #4
    cmp r0, #4
    bne done
#ifdef OFA_CUSTOM_LABELS
    cmp.w r9, #0xf0
    blo named_extras
    sub.w r1, r9, #0xf0
    adr r0, alias_table
    ldrb r4, [r0, r1]
    b done
named_extras:
#endif
    adr r0, extra_table
    movs r1, #6
next:
    ldrb r4, [r0]
    cmp r4, r9
    beq found
    adds r0, #2
    subs r1, #1
    bne next
    movs r4, #0
    b done
found:
    ldrb r4, [r0, #1]
done:
    b.w lookup_done
.balign 4
digit_table: .word 0x823f36
devices: .word 0x20a2a0
extra_table:
    .byte 0x2d, 13 /* Red: F14 */
    .byte 0x30, 18 /* Green: F15 */
    .byte 0x2e, 19 /* Yellow: F16 */
    .byte 0x2f, 30 /* Blue: F17 */
    .byte 0x1b, 31 /* Return/Last CH: F18 */
    .byte 0x1f, 32 /* Info: F19 */
#ifdef OFA_CUSTOM_LABELS
/* Indexed by logical key - 0xf0. The screen firmware renders custom text only
 * for logical IDs 0xf0..0xff, so this table is the whole renameable key space. */
.balign 4
alias_table:
#ifdef OFA_SPARE_KEYS
    .byte 2, 3, 4, 5, 6, 7, 9, 10, 11, 12 /* f0..f9: SPARE 1..10 (never on a page as digits) */
#else
    .byte 20, 21, 22, 23, 24, 25, 26, 27, 28, 29 /* f0..f9: legacy CT01 digit aliases */
#endif
    .byte 13, 18, 19, 30, 31, 32 /* fa..ff: Red Green Yellow Blue Return Info */
#endif
