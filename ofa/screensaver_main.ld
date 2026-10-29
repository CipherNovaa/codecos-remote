/* Appended after the installed spare-keys-v2 image end (VA 0x82792c), inside
 * its existing final 4 KiB APP flash sector. */
config_reply = 0x819cb4;
rx_dispatch = 0x81aaf4;
SECTIONS {
    .text 0x82792c : { *(.bridge) *(.text*) *(.rodata*) }
    /DISCARD/ : { *(.ARM.attributes) *(.comment) *(.ARM.exidx*) *(.data*) *(.bss*) }
}
