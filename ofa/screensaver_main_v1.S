/* Config-dispatch bridge for W 0C, v1 (FROZEN: the installed main v1 forwarder;
 * see screensaver_main_v1.c).  Same shape as label_bridge.S for W 0B:
 * r4 = command bytes, r8 = length, r9 = reply buffer, [r7+2] = reply length.
 * No response-relay splice (that is v2). */
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
