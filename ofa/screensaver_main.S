/* Config-dispatch bridge for W 0C (same shape as label_bridge.S for W 0B):
 * r4 = command bytes, r8 = length, r9 = reply buffer, [r7+2] = reply length.
 * Plus the v2 response-relay splice in front of the stock dispatcher 0x81aaf4. */
.syntax unified
.thumb
.cpu cortex-m4
.section .bridge,"ax",%progbits
.global ss_bridge
.thumb_func
ss_bridge:
    mov r0, r4
    mov r1, r8
    mov r2, r9
    bl ss_command
    strh r0, [r7, #2]
    b.w config_reply

/* Replaces `b.w 0x81aaf4` at 0x81b938: a tail call made after the parser has
 * popped its frame, with r0 = the validated a6 6a response frame.  Relay a
 * cmd-0x50 reply, then continue into the stock dispatcher exactly as before. */
.global ss_rx_hook
.thumb_func
ss_rx_hook:
    push {r4, lr}
    mov r4, r0
    bl ss_rx_relay
    mov r0, r4
    pop {r4, lr}
    b.w rx_dispatch
