"""Emulate HOLD ss_key (0x29e54) and stock/v15 0x87c4 with REAL stock code for the type dispatch.
Stubs only: EVENT_CODE 0x12210, EVENT_TARGET 0x1225a, TARGET_INDEX 0x172fa, SEND_43 0x1bf50, op41 0x1be84, op42 0x1be24, release 0x1f428.
"""
from unicorn import *; from unicorn.arm_const import *
from fw import *
CALLER=0x2001
def run(img, entry, ev, shown, idx, key, slot=3, count=4, rec=None):
    uc=Uc(UC_ARCH_ARM,UC_MODE_THUMB)
    uc.mem_map(0,0x40000); uc.mem_write(0x1000,img); uc.mem_map(0x20000000,0x10000); uc.mem_map(0x40000000,0x1000)
    uc.mem_write(0x200003f4, (shown).to_bytes(4,'little'))
    uc.mem_write(0x20000005, bytes([slot])); uc.mem_write(0x20000006, b'\x55')
    uc.mem_write(0x20000000+0x259, bytes([count])); uc.mem_write(0x20000000+0x25a, bytes([0x22,0x23,key,0x36,0x2f,0,0,0]))
    if rec: uc.mem_write(0x20000000+0x1ae+idx*0x14, bytes(rec)+bytes(0x14-len(rec)))
    uc.reg_write(UC_ARM_REG_SP,0x2000f000); uc.reg_write(UC_ARM_REG_LR,CALLER); uc.reg_write(UC_ARM_REG_R0,0x40000000)
    r={'sends':[],'ops':[],'hits':[]}
    def hk(uc,a,sz,u):
        if a==(CALLER&~1): r['ret']=True; r['sp']=uc.reg_read(UC_ARM_REG_SP); uc.emu_stop(); return
        lr=uc.reg_read(UC_ARM_REG_LR); regs=tuple(uc.reg_read(x) for x in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3))
        if a==0x87c4: r['hits'].append('stock0x87c4'); return
        if a==0x882c: r['hits'].append('stock@0x882c(table path)'); return
        if a==0x8822: r['hits'].append('stock@0x8822(type2-4)'); return
        if a==0x885c: r['hits'].append('stock@0x885c(type0 device)'); return
        if a==0x8886: r['hits'].append('stock@0x8886(type1 activity)'); return
        if a==0x88ae: r['hits'].append('stock@0x88ae(type5)'); return
        if a==0x12210: uc.reg_write(UC_ARM_REG_R0,ev)
        elif a==0x1225a: uc.reg_write(UC_ARM_REG_R0,0x40000000)
        elif a==0x172fa: uc.reg_write(UC_ARM_REG_R0,idx)
        elif a==0x1bf50: r['sends'].append(('SEND_43 slot=%#x key=%#x r2=%d byte8=%d'%regs))
        elif a==0x1be84: r['ops'].append('op41(slot=%#x,%#x,%#x,%#x)'%regs)
        elif a==0x1be24: r['ops'].append('op42(act=%#x)'%regs[0])
        elif a==0x1f428: r['ops'].append('release0x1f428')
        else: return
        uc.reg_write(UC_ARM_REG_PC,lr|1)
    uc.hook_add(UC_HOOK_CODE,hk,begin=0,end=0x40000)
    try: uc.emu_start(entry|1,0,count=2000)
    except UcError as e: r['ERR']=str(e)+'@'+hex(uc.reg_read(UC_ARM_REG_PC))
    r['state6']=uc.mem_read(0x20000006,1)[0]; r['state5']=uc.mem_read(0x20000005,1)[0]
    return r
EV={1:'PRESSED',3:'PRESS_LOST',7:'CLICKED',8:'RELEASED'}
for shown in (0xb,0x15):
    print(f'===== SHOWN={shown:#x}  idx=2 -> key=[0x25a+2]=0x2f, count=4, slot [5]=3')
    for ev in (1,3,7,8):
        h=run(HOLD,0x29e54,ev,shown,2,0x2f); s=run(V15,0x87c4,ev,shown,2,0x2f)
        print(f'  {EV[ev]:11} HOLD ss_key: sends={h["sends"]} ops={h["ops"]} path={h["hits"]} ret={h.get("ret")} sp_ok={h.get("sp")==0x2000f000} {h.get("ERR","")}')
        print(f'  {"":11} v15  0x87c4: sends={s["sends"]} ops={s["ops"]} path={s["hits"]}')
print('===== SHOWN=0 favorites, idx=2, record types 0..5 (record=[type,slot=7,key=0x2f])')
for t in range(6):
    rec=[t,7,0x2f if t>=2 else 0]
    for ev in (1,7,8):
        h=run(HOLD,0x29e54,ev,0,2,0,rec=rec); s=run(V15,0x87c4,ev,0,2,0,rec=rec)
        print(f'  type{t} {EV[ev]:9} HOLD: sends={h["sends"]} ops={h["ops"]} [6]={h["state6"]:#x} [5]={h["state5"]:#x} path={h["hits"]} {h.get("ERR","")} | v15: sends={s["sends"]} ops={s["ops"]} [6]={s["state6"]:#x} [5]={s["state5"]:#x} path={s["hits"]}')
print('===== SHOWN=0xb count check: idx=5 >= count 4')
h=run(HOLD,0x29e54,7,0xb,5,0); print('  HOLD CLICKED idx5:',h['sends'],h['hits'])
print('===== SHOWN=0xc (not handled by 0x87c4)')
h=run(HOLD,0x29e54,7,0xc,2,0x2f); print('  HOLD CLICKED:',h['sends'],h['hits'],h.get('ret'))
