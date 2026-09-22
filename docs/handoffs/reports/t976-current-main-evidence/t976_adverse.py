exec(open('/tmp/t976_journeys.py').read().split("for name,fn in [('normal'")[0])
from concurrent.futures import ThreadPoolExecutor
results={}
def record(name,fn):
 try:results[name]=fn()
 except Exception as e:results[name]={'status':'FAIL','error':str(e)}
 (ROOT/'results.json').write_text(json.dumps(results,indent=2));print(name,results[name],flush=True)

def gate(mode):
 cwd=ROOT/mode;cwd.mkdir();bins=cwd/'bin';bins.mkdir();tmp=cwd/'tmp';tmp.mkdir()
 if mode!='missing-binary':
  stub=bins/'claude'
  stub.write_text('#!/bin/sh\nif [ "$1" = auth ]; then '+('echo "Not logged in. Please run /login"; exit 1' if mode=='missing-auth' else 'echo "Login: stub@example.invalid (Pro)"; exit 0')+'; fi\nprintf "stub artifact\\n" > demo.txt\ngit add demo.txt\ngit -c user.name=stub -c user.email=stub@example.invalid commit -qm artifact\n')
  stub.chmod(0o755)
 r=atm(cwd,'quickstart','--gate',extra={'PATH':str(bins)+':'+ENV['PATH'],'TMPDIR':str(tmp)})
 text=r.stdout+r.stderr
 if mode.startswith('missing'):
  assert 'WALKTHROUGH ONLY' in text and not list(tmp.iterdir())
 else:
  assert '8/8 released' in text and 'author alice != evaluator bob' in text
 return {'status':'PASS','output':text}

def race():
 repo=setup('race');create(repo,'Concurrent claim target')
 with ThreadPoolExecutor(2) as pool:
  ps=list(pool.map(lambda a:atm(repo,'claim','T-001',agent=a,ok=None),['alice','bob']))
 assert sorted(p.returncode for p in ps)==[0,1]
 t=ticket(repo);assert t['owner'] in ('alice','bob')
 winner=t['owner'];lease=t['owner_generation'];assert atm(repo,'claim','T-001',agent=winner,ok=None).returncode!=0
 assert ticket(repo)['owner_generation']==lease
 return {'status':'PASS','owner':winner,'codes':[p.returncode for p in ps]}

def failed_check():
 repo=setup('failed-check');create(repo,'Invalid result');w=worktree(repo,'alice');atm(w,'next',agent='alice')
 (w/'stats.json').write_text('{"rows":999}\n');git(w,'add','.');git(w,'commit','-qm','bad result');sha=git(w,'rev-parse','HEAD').stdout.strip();atm(w,'review','T-001','--notes','Deliberately wrong fixture',agent='alice')
 assert json.loads(git(repo,'show',sha+':stats.json').stdout)['rows']!=3
 atm(repo,'reject','T-001','--sha',sha,'--notes','Check failed: expected rows 3, got 999',agent='bob')
 t=ticket(repo);assert any(e.get('kind')=='reject' for e in t['review_events'])
 return {'status':'PASS reject records failed check','ticket_status':t['status'],'sha':sha}

def transfer():
 repo=setup('transfer');create(repo,'Transfer harness identity');w=worktree(repo,'alice');atm(w,'next',agent='alice');(w/'mid.txt').write_text('intact\n');git(w,'add','.');git(w,'commit','-qm','intermediate')
 atm(repo,'join','bob','--roles','backend','--harness','codex',agent='bob');before=ticket(repo);atm(repo,'assign','T-001','--owner','bob');after=ticket(repo)
 assert after['owner']=='bob' and after['owner_generation']==before['owner_generation']+1
 assert atm(w,'review','T-001','--notes','revoked owner',agent='alice',ok=None).returncode!=0
 assert (w/'mid.txt').read_text()=='intact\n'
 return {'status':'PASS metadata fencing ONLY; supported second harness execution NOT RUN','generation':after['owner_generation']}

def stale():
 cwd=ROOT/'stale';cwd.mkdir();bins=cwd/'bin';bins.mkdir();(bins/'atm').write_text('#!/bin/sh\necho stale-foreign-cli\n');(bins/'atm').chmod(0o755)
 r=run(['/tmp/atman-t976-0922-source/install.sh','--prefix',bins],cwd,ok=None)
 assert r.returncode!=0 and 'refusing to overwrite' in r.stderr and 'stale-foreign-cli' in (bins/'atm').read_text()
 return {'status':'PASS foreign install refused; exact repair --prefix or --force printed','error':r.stderr}

for name,fn in [('stub-auth-dispatch',lambda:gate('stub-auth-dispatch')),('missing-binary',lambda:gate('missing-binary')),('missing-auth',lambda:gate('missing-auth')),('concurrent-and-repeated-claim',race),('failed-check',failed_check),('interchangeability-metadata',transfer),('stale-install',stale)]:record(name,fn)
print('ARTIFACTS',ROOT)
