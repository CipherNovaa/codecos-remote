ir_dispatch = 0x81d082;
ir_continue = 0x81b006;
SECTIONS { .text 0x827416 : SUBALIGN(2) { *(.text*) } /DISCARD/ : { *(.ARM.attributes) *(.comment) *(.ARM.exidx*) *(.data*) *(.bss*) } }
