/* Screen extension v15: three regions INSIDE the stock-size image (zero padding the
 * mgs-font tool left after glyph bitmaps).
 *   region A .text  VA 0x25e1c..0x263ac (1424 B): slots + flash helpers + command handler
 *   region B .b     VA 0x25290..0x256c0 (1072 B): entry glue + hooks + direct blit + grace
 *   region C .c     VA 0x25898..0x25c28 ( 912 B): slot picker + dispatch hook + table-driven readback
 *                                                  (2nd face padding, cap end file 0x24c2b) */
dispatch_send = 0x1d40c;
sleep_stock = 0xbe8c;
lptim_stock = 0xbeec;
SECTIONS {
    .b    0x25290 : { *(.entry) *(.b) }
    .c    0x25898 : { *(.c) *(.ctab) }
    .text 0x25e1c : { *(.text*) *(.rodata*) }
    /DISCARD/ : { *(.ARM.attributes) *(.comment) *(.ARM.exidx*) *(.data*) *(.bss*) }
}
