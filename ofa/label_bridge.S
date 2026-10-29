.syntax unified
.thumb
.cpu cortex-m4
.section .bridge,"ax",%progbits
.global label_bridge
.thumb_func
label_bridge:
    mov r0, r4
    mov r1, r8
    mov r2, r9
    bl label_command
    strh r0, [r7, #2]
    b.w config_reply
