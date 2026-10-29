"""Drive the main 0x43 handler (from 0x81ad61) in v16 vs 407c86b7 with byte8=1/0. Modeled after check_touch_digits.execute_screen_key."""
import struct
from pathlib import Path
from unicorn import *; from unicorn.arm_const import *
ROOT=Path(__file__).resolve().parents[1]   # the ofa/ toolkit dir
V16=(ROOT/'artifacts/codecos-v16/main-codecos-v16-DO-NOT-FLASH.bin').read_bytes()
PATCH=(ROOT/'artifacts/touchscreen-hold-main/main-touchscreen-hold-inplace-DO-NOT-FLASH.bin').read_bytes()
def execute(data, key, byte8, *, slot=1, profile=4, ble=True, connected=True, act_flag=0, act=0):
    cpu=Uc(UC_ARCH_ARM,UC_MODE_THUMB)
    cpu.mem_map(0,0x80000); cpu.mem_map(0x800000,0x40000); cpu.mem_write(0x80DE00,data); cpu.mem_map(0x200000,0x10000)
    def byte(a,v): cpu.mem_write(a,bytes([v]))
    def word(a,v): cpu.mem_write(a,struct.pack('<I',v))
    byte(0x20A301,12); byte(0x20A302,12)
    byte(0x20A2A0, slot | (act<<4)); byte(0x20A2A0+4*slot+0x17, profile<<4)
    word(0x20A2A4, slot<<16); word(0x20A2A8,(1<<(slot-1)) if ble else 0)
    word(0x20B448,(1<<15) if connected else 0); byte(0x20B451,act_flag)
    byte(0x20B0B0+0x15,4); byte(0x20B0B0+1,1); byte(0x20B0B0+4,1)
    # activity table: activity 'act' routes to slot 2
    byte(0x20B4C4+act*4-1, 2)
    frame=bytes([0xA5,0x5A,0x43,0,3,0,slot,key,byte8,0]); cpu.mem_write(0x20E000,frame)
    cpu.reg_write(UC_ARM_REG_SP,0x20F000); cpu.reg_write(UC_ARM_REG_LR,0x830001); cpu.reg_write(UC_ARM_REG_R0,0x20E000)
    zero_stubs={0x7A5E,0x816EEE,0x81B960,0x81CF64,0x815EDA,0x81BC28,0x81BB9A,0x81BBBA,0x81BBDA,0x817938,0x817984,0xB918,0x26c64,0x26c3e,0x816012,0x81744c,0x81bee6}
    ev=[]; fin=[False]
    def hook(cpu,a,sz,_):
        if a==0x81B1FC: fin[0]=True; cpu.emu_stop(); return
        if a==0x81cb0c: ev.append(('0x81cb0c(idx=%#x)'%cpu.reg_read(UC_ARM_REG_R0)))
        if a==0x81d136: ev.append('->0x81d136(idx=%#x) r7=%d'%(cpu.reg_read(UC_ARM_REG_R0),cpu.reg_read(UC_ARM_REG_R7)))
        if a==0x81d082: ev.append('->IR 0x81d082(slot=%d,key=%#x)'%(cpu.reg_read(UC_ARM_REG_R0),cpu.reg_read(UC_ARM_REG_R1)))
        if a==0x81cdca: ev.append('->remap 0x81cdca(act=%d,key=%#x)'%(cpu.reg_read(UC_ARM_REG_R0),cpu.reg_read(UC_ARM_REG_R1)))
        if a==0x4F7A4:
            rep=bytes(cpu.mem_read(cpu.reg_read(UC_ARM_REG_R3),8)); ev.append('HID report id=%#x %s'%(cpu.reg_read(UC_ARM_REG_R2),rep.hex(' ')))
        elif a in (0x817938,0x817984): ev.append('queue %#x'%a)
        if a in zero_stubs or a in (0x4F7A4,0x81d082):
            if a==0x81d082: cpu.reg_write(UC_ARM_REG_R0,1)  # pretend IR handled -> exercise byte8 branch at 0x81b00a
            else: cpu.reg_write(UC_ARM_REG_R0,0)
            cpu.reg_write(UC_ARM_REG_PC,cpu.reg_read(UC_ARM_REG_LR)); return
        if a<0x800000: raise AssertionError('external %#x'%a)
    cpu.hook_add(UC_HOOK_CODE,hook)
    try: cpu.emu_start(0x81AD61,0x830000,count=6000)
    except UcError as e: ev.append('ERR '+str(e)+'@%#x'%cpu.reg_read(UC_ARM_REG_PC))
    return ev, fin[0], cpu.mem_read(0x20B448+2,1)[0]
for name,img in (('v16',V16),('PATCH 407c86b7',PATCH)):
    print('=====',name)
    for key,desc in ((0x0d,'digit 0'),(0x2f,'BLUE/CAST'),(0xf0,'spare-1/RESUME'),(0x22,'unmapped table key 0x22'),(0x36,'table key 0x36'),(0x00,'key 0 (device/activity record)')):
        for b8 in (1,0):
            ev,fin,r5_2=execute(img,key,b8)
            print(f'  key={key:#04x} {desc:28} byte8={b8}: {ev} done={fin} [0x20b44a]={r5_2}')
    print('  -- activity mode: [0x20b451]=1, current activity 1 -> routes to slot 2; frame slot=1 (BLE, connected)')
    for b8 in (1,0):
        ev,fin,_=execute(img,0x2f,b8,act_flag=1,act=1)
        print(f'  BLUE byte8={b8}: {ev}')
