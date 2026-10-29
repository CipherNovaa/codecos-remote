/* Appending stays inside the stock APP image's final 4 KiB flash sector. */
lookup_done = 0x81afe2;
SECTIONS {
    .hook 0x81afcc : { *(.hook) }
    .text 0x8276cc : { *(.text) }
    /DISCARD/ : { *(.ARM.attributes) *(.comment) }
}
