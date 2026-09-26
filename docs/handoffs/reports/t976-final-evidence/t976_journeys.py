import json, os, pathlib, subprocess, sys, tempfile, time, signal, socket, urllib.request
ROOT=pathlib.Path(tempfile.mkdtemp(prefix='t976-journeys-'))
ATM='/tmp/atman-t976-0923-bin/atm'
ENV={k:v for k,v in os.environ.items() if not k.startswith(('TICKET','ATMAN','CLAUDE','CODEX','CURSOR'))}
ENV.update(PATH='/tmp/atman-t976-0923-bin:/usr/bin:/bin:/usr/sbin:/sbin',PYTHONDONTWRITEBYTECODE='1',TICKETS_USAGE_REFRESH='0',PYTEST_CURRENT_TEST='t976_disposable_stub_journeys',GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null')
ENV['HOME']='/tmp/atman-t976-0923-home'
LOG=[]
def run(args,cwd,agent='boss',ok=0,extra=None):
 e=dict(ENV,TICKET_AGENT=agent,TICKET_SESSION_ID='t976-'+agent)
 if extra:e.update(extra)
 p=subprocess.run(list(map(str,args)),cwd=cwd,env=e,text=True,capture_output=True,timeout=60)
 LOG.append(dict(args=list(map(str,args)),cwd=str(cwd),agent=agent,code=p.returncode,out=p.stdout,err=p.stderr))
 (ROOT/'commands.json').write_text(json.dumps(LOG,indent=2))
 if ok is not None and p.returncode!=ok:raise RuntimeError(str(args)+'\n'+p.stdout+p.stderr)
 return p

def atm(repo,*args,agent='boss',ok=0,extra=None):return run([ATM,*args],repo,agent,ok,extra)
def git(repo,*args):return run(['git','-c','user.name=T976 probe','-c','user.email=t976@example.invalid',*args],repo)
def ticket(repo,tid='T-001'):return json.loads((repo/'.tickets'/f'{tid}.json').read_text())
def setup(name):
 repo=ROOT/name;repo.mkdir();git(repo,'init','-q','-b','main');(repo/'README.md').write_text('Disposable T976 probe\n');git(repo,'add','.');git(repo,'commit','-qm','init')
 atm(repo,'quickstart','--agent','boss','--roles','backend');atm(repo,'quickstart','--remove');atm(repo,'master','take');atm(repo,'objective','Produce verified CSV statistics','--exit','exact row count accepted')
 git(repo,'add','.');git(repo,'commit','-qm','onboard')
 atm(repo,'join','alice','--roles','backend','--harness','custom','--cmd','/usr/bin/true {prompt_file}',agent='alice')
 atm(repo,'join','bob','--roles','backend','--harness','custom','--cmd','/usr/bin/true {prompt_file}',agent='bob')
 return repo

def worktree(repo,name):
 p=repo/'.worktrees'/name;git(repo,'worktree','add',str(p),'-b',name);return p

def create(repo,title,deps=None):
 args=['create',title,'--role','backend','--body','Cause: journey probe. Change: persisted artifact. Proof: independent assertion.']
 if deps:args+=['--deps',deps]
 return atm(repo,*args)

results={}
def normal():
 repo=setup('normal');create(repo,'Write CSV statistics');create(repo,'Consume verified statistics','T-001');w=worktree(repo,'alice')
 atm(w,'next',agent='alice');assert atm(repo,'claim','T-002',agent='bob',ok=None).returncode!=0
 atm(w,'msg','CSV artifact underway','--to','bob','--re','T-001',agent='alice');assert 'CSV artifact underway' in atm(repo,'inbox',agent='bob').stdout
 (w/'stats.json').write_text('{"rows":3,"columns":2}\n');git(w,'add','stats.json');git(w,'commit','-qm','CSV statistics')
 sha=git(w,'rev-parse','HEAD').stdout.strip();atm(w,'review','T-001','--notes','stats.json: 3 rows, 2 columns',agent='alice')
 assert atm(w,'accept','T-001','--sha',sha,'--notes','self',agent='alice',ok=None).returncode!=0
 assert atm(repo,'claim','T-002',agent='bob',ok=None).returncode!=0
 assert json.loads(git(repo,'show',sha+':stats.json').stdout)=={'rows':3,'columns':2}
 atm(repo,'accept','T-001','--sha',sha,'--notes','Independently read committed JSON; 3 rows and 2 columns',agent='bob')
 atm(repo,'done','T-001','--artifact',str(w),'--notes','stats.json verified')
 git(repo,'merge','--ff-only',sha)
 atm(repo,'next',agent='bob');assert ticket(repo,'T-002')['owner']=='bob'
 assert any(x.get('kind')=='accept' and x.get('sha')==sha and x.get('by')=='bob' for x in ticket(repo).get('review_events',[]))
 return {'status':'PASS CLI mechanics; stub identities, no provider certification','sha':sha,'repo':str(repo)}

def interrupted():
 repo=setup('interrupted');create(repo,'Resume interrupted artifact');w=worktree(repo,'alice');atm(w,'next',agent='alice')
 handoff='\n'.join('## '+k+'\n'+v for k,v in dict(completion_criteria='finished.txt has both stages',work_completed='mid.txt written',decisions='preserve first stage',open_questions='none',artifacts=str(w/'mid.txt'),worktree=str(w),checks='mid.txt equals stage-one',next_action='write finished.txt with stage-one and stage-two, commit and review',facts='mid.txt is on disk',assumptions='same worktree is available',context_budget='4000',missing_artifacts='none').items())
 (w/'handoff.md').write_text(handoff)
 worker=ROOT/'worker.py';worker.write_text("import pathlib,subprocess,time\npathlib.Path('mid.txt').write_text('stage-one\\n')\nsubprocess.run(['"+ATM+"','handover','T-001','--file','handoff.md'],check=True)\npathlib.Path('ready').write_text('ready')\ntime.sleep(120)\n")
 e=dict(ENV,TICKET_AGENT='alice',TICKET_SESSION_ID='t976-alice');p=subprocess.Popen([sys.executable,str(worker)],cwd=w,env=e,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
 try:
  deadline=time.monotonic()+15
  while not (w/'ready').exists() and time.monotonic()<deadline:time.sleep(.1)
  assert (w/'ready').exists();before=ticket(repo);os.killpg(p.pid,signal.SIGKILL);out,err=p.communicate(timeout=5)
 finally:
  if p.poll() is None:os.killpg(p.pid,signal.SIGKILL);p.wait()
 assert p.returncode==-9
 assert 'T-001' in atm(w,'mine',agent='alice').stdout
 assert atm(w,'next',agent='alice',ok=None).returncode!=0
 pulse=json.loads(atm(w,'pulse',agent='alice').stdout);assert any(r['ticket']=='T-001' and r['handoff']['next_action'] for r in pulse['active'])
 assert (w/'mid.txt').read_text()=='stage-one\n'
 (w/'finished.txt').write_text((w/'mid.txt').read_text()+'stage-two\n');(w/'ready').unlink();git(w,'add','.');git(w,'commit','-qm','Resume stage two');sha=git(w,'rev-parse','HEAD').stdout.strip()
 atm(w,'review','T-001','--notes','finished.txt persists both stages',agent='alice')
 assert git(repo,'show',sha+':finished.txt').stdout=='stage-one\nstage-two\n'
 atm(repo,'accept','T-001','--sha',sha,'--notes','Independent committed artifact read: both stages',agent='bob')
 after=ticket(repo);assert after['owner']=='alice' and after['owner_generation']==before['owner_generation']
 return {'status':'PASS killed subprocess / same custom harness mechanics only','sha':sha,'repo':str(repo),'killed_returncode':p.returncode,'handoff':after['handoff']['id']}

for name,fn in [('normal',normal),('interrupted',interrupted)]:
 try:results[name]=fn()
 except Exception as ex:results[name]={'status':'FAIL','error':str(ex)}
 (ROOT/'results.json').write_text(json.dumps(results,indent=2));print(name,results[name],flush=True)
print('ARTIFACTS',ROOT,flush=True)
