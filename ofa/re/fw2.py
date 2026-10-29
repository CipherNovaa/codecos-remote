from fw import *
def adis(img, va, n=0x400, count=None, stop=None):
    """disasm with pc-relative ldr resolved."""
    data = img[off(va): off(va)+n]
    out=[]
    for i,ins in enumerate(md.disasm(data, va)):
        s=f'{ins.address:#07x}  {ins.bytes.hex():<10} {ins.mnemonic:<8} {ins.op_str}'
        if ins.mnemonic in ('ldr','adr') and '[pc' in ins.op_str or ins.mnemonic=='adr':
            try:
                imm=int(ins.op_str.split('#')[1].rstrip(']'),0)
                p=((ins.address+4)&~3)+imm
                s+=f'   ; ={u32(img,p):#x} (pool {p:#x})'
            except Exception as e: pass
        out.append(s)
        if count and i+1>=count: break
        if stop and ins.address>=stop: break
    return '\n'.join(out)
