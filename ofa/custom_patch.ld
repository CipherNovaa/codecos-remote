lookup_done = 0x81afe2;
config_reply = 0x819cb4;
SECTIONS {
    .hook 0x81afcc : { *(.hook) }
    .text 0x8276cc : { *(.text) *(.bridge) *(.labels) *(.rodata*) }
    /DISCARD/ : { *(.ARM.attributes) *(.comment) *(.ARM.exidx*) }
}
