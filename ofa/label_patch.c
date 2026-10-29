/* Version-pinned, bounded custom-label command for the main RTL application.
 * Stock label records/persistence are reused; no learned IR data is created.
 * W 0B 00 -> 0B 01 "CT01". W 0B 01 slot alias label[16] sets a label.
 * W 0B 02 slot alias removes it; W 0B 03 slot alias reads one label.
 * Status 1=OK, 2=invalid/unsupported, 3=full/corrupt label table.
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
#define META ((volatile u8 *)0x20a2a0)
#define LABELS ((volatile u8 *)0x20be54)
#define SAVE ((void (*)(const volatile void *, u32, u32))0xb919)

__attribute__((section(".labels")))
u32 label_command(const u8 *cmd, u32 len, u8 *reply) {
    reply[0] = 0x0b;
    reply[1] = 2;
    if (len == 3 && cmd[2] == 0) {
        reply[1] = 1;
        reply[2] = 'C'; reply[3] = 'T'; reply[4] = '0'; reply[5] = '1';
        return 6;
    }
    if (len != 5 && len != 21) return 2;
    u32 op = cmd[2], slot = cmd[3], key = cmd[4];
    if (slot < 1 || slot > 12 || key < 0xf0 || op < 1 || op > 3) return 2;
    if ((op == 1 && len != 21) || (op != 1 && len != 5)) return 2;
    if (META[4 * slot + 0x17] >> 4 != 4) return 2;
    if (!(*(volatile u32 *)(META + 8) & (1u << (slot - 1)))) return 2;
    if (op == 1) {
        u32 ended = 0;
        // Screen label rendering calls strlen on this 16-byte field.
        if (!cmd[5] || cmd[20]) return 2;
        for (u32 i = 5; i < 21; ++i) {
            u8 ch = cmd[i];
            if (!ch) ended = 1;
            else if (ended || ch < 32 || ch > 126) return 2;
        }
    }
    u32 count = META[7];
    if (count > 128) { reply[1] = 3; return 2; }
    u32 index = count;
    for (u32 i = 0; i < count; ++i)
        if (LABELS[i * 18] == slot && LABELS[i * 18 + 1] == key) {
            if (index != count) { reply[1] = 3; return 2; }
            index = i;
        }
    if (op == 3) {
        reply[1] = 1;
        reply[2] = index != count;
        for (u32 i = 0; i < 16; ++i)
            reply[3 + i] = index == count ? 0 : LABELS[index * 18 + 2 + i];
        return 19;
    }
    if (op == 1) {
        if (index == count && count == 128) { reply[1] = 3; return 2; }
        LABELS[index * 18] = slot;
        LABELS[index * 18 + 1] = key;
        for (u32 i = 0; i < 16; ++i) LABELS[index * 18 + 2 + i] = cmd[5 + i];
        if (index == count) ++count;
    } else if (index != count) {
        for (u32 i = index * 18; i < (count - 1) * 18; ++i) LABELS[i] = LABELS[i + 18];
        --count;
        for (u32 i = count * 18; i < (count + 1) * 18; ++i) LABELS[i] = 0;
    } else { reply[1] = 1; return 2; }
    // Same two FTL objects as the stock label writer. Keep the bond/slot bytes
    // intact when persisting the shared metadata block.
    SAVE(LABELS, 0x2230, 0x900);
    META[7] = count;
    SAVE(META, 4, 0x60);
    reply[1] = 1;
    return 2;
}
