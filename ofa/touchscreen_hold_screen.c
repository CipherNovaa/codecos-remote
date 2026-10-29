/* This code only consumes press/release edges for the two hold-aware keys.
 * All other widgets/events are delegated by the assembly wrapper to 0x87c4. */
typedef unsigned char u8;
typedef unsigned int u32;

#define EVENT_CODE ((u32 (*)(void *))0x12211u)
#define EVENT_TARGET ((void *(*)(void *))0x1225bu)
#define EVENT_NUMERIC_TARGET ((u32 (*)(void *))0x1225fu)
#define GROUP_KEY ((u8 (*)(u32))0x9b59u)
#define TARGET_INDEX ((u32 (*)(void *))0x172fbu)
#define SEND_43 ((void (*)(u32, u32, u32, u32))0x1bf51u)

#define KEY_STATE (*(volatile u32 *)0x200003f4u)
#define SCREEN_STATE ((volatile u8 *)0x20000000u)

enum { LV_PRESSED = 1, LV_PRESS_LOST = 3, LV_CLICKED = 7, LV_RELEASED = 8 };

/* Return 1 only when the edge was fully consumed.  The stock callback's phase
 * gate is deliberately duplicated: an unexpected screen state falls through to
 * stock rather than inventing a frame. */
static u32 send_edge(u32 edge, u8 key)
{
    if (edge != LV_PRESSED && edge != LV_RELEASED && edge != LV_PRESS_LOST)
        return 1;
    SEND_43(SCREEN_STATE[5], key, 1, edge == LV_PRESSED ? 1 : 0);
    return 1;
}

static u32 input_event(u32 edge)
{
    return edge == LV_PRESSED || edge == LV_PRESS_LOST || edge == LV_CLICKED || edge == LV_RELEASED;
}

__attribute__((section(".aux"))) u32 ss_key_impl(void *event)
{
    u32 edge = EVENT_CODE(event);
    if (!input_event(edge))
        return 0;
    u32 index = TARGET_INDEX(EVENT_TARGET(event));
    u32 phase = KEY_STATE;
    if (phase == 0x0b || phase == 0x15) {
        if (index >= SCREEN_STATE[0x259])
            return 0;
        if (edge == LV_CLICKED)
            return 1;
        return send_edge(edge, SCREEN_STATE[0x25a + index]);
    }
    if (phase != 0 || index > 7)
        return 0;
    const volatile u8 *source = SCREEN_STATE + index * 0x14 + 0x1ae;
    u8 type = source[0];
    u8 slot = source[1];
    u8 key = source[2];
    if (type < 2 || type > 5)
        return 0;
    SCREEN_STATE[5] = type == 5 ? 0 : slot;
    if (edge == LV_CLICKED) {
        SCREEN_STATE[6] = 0;
        return 1;
    }
    return send_edge(edge, key);
}

/* Stock 0xa681 obtains the keypad index via 0x1225e then adds 0x0d; its
 * ten numeric buttons are logical 0x0d..0x16.  It has no other click-side
 * state mutation, so consuming CLICKED here is sufficient. */
u32 ss_key_num_impl(void *event)
{
    u32 edge = EVENT_CODE(event);
    if (!input_event(edge))
        return 0;
    if (edge == LV_CLICKED)
        return 1;
    u32 index = EVENT_NUMERIC_TARGET(event);
    if (index > 9)
        return 0;
    return send_edge(edge, (u8)(index + 0x0d));
}

/* 0xc234's live wrapper supplies a descriptor whose callback is this table
 * sender.  Stock reads key = state[0x25a + target_index], slot = state[5]. */
u32 ss_key_table_impl(void *event)
{
    u32 edge = EVENT_CODE(event);
    if (!input_event(edge))
        return 0;
    if (edge == LV_CLICKED)
        return 1;
    u32 index = TARGET_INDEX(EVENT_TARGET(event));
    return send_edge(edge, SCREEN_STATE[0x25a + (u8)index]);
}

__attribute__((section(".grp"))) u32 ss_key_grp_impl(void *event)
{
    u32 edge = EVENT_CODE(event);
    if (!input_event(edge))
        return 0;
    if (edge == LV_CLICKED)
        return 1;
    return send_edge(edge, GROUP_KEY(EVENT_NUMERIC_TARGET(event)));
}
