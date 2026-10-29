from unicorn import *; from unicorn.arm_const import *
from fw import *
CALLER=0x2001
def run(img, entry, ev, shown, idx, rec, slot=3):
    uc=Uc(UC_ARCH_ARM,UC_MODE_THUMB)
    uc.mem_map(0,0x40000); uc.mem_write(0x1000,img); uc.mem_map(0x20000000,0x10000); uc.mem_map(0x40000000,0x1000)
    uc.mem_write(0x200003f4,(shown).to_bytes(4,'little')); uc.mem_write(0x20000005,bytes([slot])); uc.mem_write(0x20000006,b'\x55')
    uc.mem_write(0x20000000+0x1ae+idx*0x14, bytes(rec)+bytes(0x14-len(rec)))
    uc.reg_write(UC_ARM_REG_SP,0x2000f000); uc.reg_write(UC_ARM_REG_LR,CALLER); uc.reg_write(UC_ARM_REG_R0,0x40000000)
    r={'sends':[],'ops':[],'hits':[],'n':0}
    def hk(uc,a,sz,u):
        r['n']+=1
        if a==(CALLER&~1): r['ret']=True; r['sp']=uc.reg_read(UC_ARM_REG_SP); uc.emu_stop(); return
        lr=uc.reg_read(UC_ARM_REG_LR); regs=tuple(uc.reg_read(x) for x in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3))
        for tag,addr in (('stock',0x87c4),('@882c',0x882c),('@8822 type2-4',0x8822),('@885c type0',0x885c),('@8886 type1',0x8886),('@88ae type5',0x88ae),('@884e idx>7',0x884e),('impl',0x2a198),('send_edge',0x29e9c)):
            if a==addr: r['hits'].append(tag); return
        if a==0x12210: uc.reg_write(UC_ARM_REG_R0,ev)
        elif a==0x1225a: uc.reg_write(UC_ARM_REG_R0,0x40000000)
        elif a==0x172fa: uc.reg_write(UC_ARM_REG_R0,idx)
        elif a==0x11e4: uc.mem_write(regs[0], bytes(uc.mem_read(regs[1],0x14)))
        elif a==0x1bf50: r['sends'].append('SEND_43(slot=%#x,key=%#x,%d,byte8=%d)'%regs)
        elif a==0x1be84: r['ops'].append('op41(slot=%#x,%#x,%#x,%#x)'%regs)
        elif a==0x1be24: r['ops'].append('op42(act=%#x)'%regs[0])
        elif a==0x1f428: r['ops'].append('release0x1f428')
        elif a==0x1268: pass
        else: return
        uc.reg_write(UC_ARM_REG_PC,lr|1)
    uc.hook_add(UC_HOOK_CODE,hk,begin=0,end=0x40000)
    try: uc.emu_start(entry|1,0,count=20000)
    except UcError as e: r['ERR']=str(e)+'@'+hex(uc.reg_read(UC_ARM_REG_PC))
    r['s6']=uc.mem_read(0x20000006,1)[0]; r['s5']=uc.mem_read(0x20000005,1)[0]; return r
EV={1:'PRESSED',3:'PRESS_LOST',7:'CLICKED',8:'RELEASED'}
print('===== SHOWN=0 favorites, idx=2, record=[type, slot=7, key]; screen [5]=3 before')
for t in range(6):
    rec=[t,7,0x2f if t>=2 else 0]
    for ev in (1,3,7,8):
        h=run(HOLD,0x29e54,ev,0,2,rec); s=run(V15,0x87c4,ev,0,2,rec)
        print(f'  type{t} {EV[ev]:10} HOLD: {h["sends"]} {h["ops"]} [6]={h["s6"]:#x} [5]={h["s5"]:#x} {h["hits"]} ret={h.get("ret")} {h.get("ERR","")}')
        print(f'  {"":16} v15 : {s["sends"]} {s["ops"]} [6]={s["s6"]:#x} [5]={s["s5"]:#x} {s["hits"]}')
print('===== SHOWN=0 idx=8 (>7)'); h=run(HOLD,0x29e54,7,0,8,[2,7,0x2f]); s=run(V15,0x87c4,7,0,8,[2,7,0x2f]); print('  HOLD',h['sends'],h['ops'],h['hits'],'| v15',s['sends'],s['ops'],s['hits'])
print('===== non-input event codes on SHOWN=0 (firehose) e.g. 2,4,5,6,0x16'); 
for ev in (2,4,5,6,0x16):
    h=run(HOLD,0x29e54,ev,0,2,[2,7,0x2f]); print(f'  ev={ev}: sends={h["sends"]} hits={h["hits"]} [6]={h["s6"]:#x} ret={h.get("ret")}')
