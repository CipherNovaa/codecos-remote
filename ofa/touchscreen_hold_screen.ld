/* The v15 NOREARM app has verified zero padding here.  The image may not grow. */
stock_key_callback = 0x87c4;
stock_numeric_callback = 0xa680;
SECTIONS {
    .text 0x29e54 : { *(.text*) *(.rodata*) }
    .grp  0x2a02c : { *(.grp) }
    .aux  0x2a198 : { *(.aux) }
    /DISCARD/ : { *(.ARM.attributes) *(.comment) *(.ARM.exidx*) *(.data*) *(.bss*) }
}
