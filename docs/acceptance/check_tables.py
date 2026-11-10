import io, re, sys, glob, os
# 回归：流程层四份文件的 markdown 表格不得有列数错位、不得被空行截断
BS = chr(92)
def pipes(l): return l.count('|') - l.count(BS + '|')
def check(path):
    lines = io.open(path, encoding='utf-8').read().split('\n')
    sec=None; hdr=None; bad=[]; ntab=0
    for i,l in enumerate(lines,1):
        if l.startswith('#'): sec=l.strip(); hdr=None; continue
        if l.startswith('|'):
            n=pipes(l)
            if re.fullmatch(r'\|[ :\-|]+\|', l):
                if hdr is None: bad.append((i,sec,'sep-without-header',n))
                elif hdr[1]!=n: bad.append((i,sec,'sep-cols',n,hdr[1]))
                continue
            if hdr is None:
                ntab+=1; hdr=(sec,n); continue
            if n!=hdr[1]: bad.append((i,sec,'row-cols',n,hdr[1]))
        elif l=='':
            hdr=None      # 空行结束一张表；紧接又出现表行而缺表头会被 sep/首行逻辑捕获
    return ntab, bad
files = sys.argv[1:] or sorted(glob.glob('docs/acceptance/*.md'))
rc=0
for f in files:
    n,bad = check(f)
    print('%-34s tables=%d %s' % (f, n, 'OK' if not bad else 'BROKEN '+str(bad)))
    if bad: rc=1
sys.exit(rc)
