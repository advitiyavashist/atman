exec(open('/tmp/t976-final-probes/t976_journeys.py').read().split("for name,fn in [('normal'")[0])
import shutil
shutil.rmtree(ROOT);ROOT=pathlib.Path(pathlib.Path('/tmp/t976-live-location').read_text());loc=json.loads((ROOT/'location.json').read_text());repo=pathlib.Path(loc['repo']);w=pathlib.Path(loc['worktree'])
ENV.pop('PYTEST_CURRENT_TEST',None);ENV['PATH']='/tmp/atman-t976-0923-bin:/Users/kavana/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
body='''Cause: live interrupted-provider acceptance probe. Change: two-stage artifact. Proof: finished.txt must contain exactly stage-one newline stage-two newline. Use /tmp/atman-t976-0923-bin/atm for EVERY board command, including self (record its output). You have one ticket only. If mid.txt does not exist: write mid.txt containing stage-one newline, commit it, record an atm update explaining the exact artifact and next action to write finished.txt from mid.txt plus stage-two newline. Then run sleep 120 in the FOREGROUND; the evaluator will kill this real process once the progress note is durable. Do not finish before interruption. On a subsequent run when mid.txt exists: preserve it, write finished.txt, independently assert exact contents, commit, atm sync then atm review with paths and test. No new tests framework; no provider spawning. Do not edit .tickets by hand.'''
atm(repo,'create','Live two-stage continuation','--role','liveprobe','--body',body)
e=dict(ENV,TICKET_AGENT='live-codex');log=open(ROOT/'interrupt-watch.log','w')
p=subprocess.Popen([ATM,'watch','--agent','live-codex','--cwd',str(w),'--every','1','--max-runs','1','--run-timeout','4'],cwd=w,env=e,stdout=log,stderr=log,start_new_session=True)
try:
 deadline=time.monotonic()+180
 while time.monotonic()<deadline and p.poll() is None:
  t=ticket(repo);notes=str(t.get('notes',[]))
  if (w/'mid.txt').exists() and 'finished.txt' in notes:
   time.sleep(2);break
  time.sleep(1)
 else:raise RuntimeError('provider did not persist intermediate artifact and continuation note')
 before=ticket(repo);(ROOT/'before-interrupt.json').write_text(json.dumps(before,indent=2));os.killpg(p.pid,signal.SIGTERM);p.wait(timeout=20)
 assert (w/'mid.txt').read_text()=='stage-one\n'
 (ROOT/'interruption.json').write_text(json.dumps({'signal':'SIGTERM watcher terminates child','exit':p.returncode,'owner':before['owner'],'generation':before['owner_generation'],'mid':'stage-one\n'},indent=2))
 print('INTERRUPTED',p.returncode,flush=True)
finally:
 if p.poll() is None:os.killpg(p.pid,signal.SIGTERM);p.wait(timeout=20)
 log.close()
atm(w,'msg','Interruption completed. Continue your held ticket from mid.txt and the recorded next action; preserve the first stage. Use /tmp/atman-t976-0923-bin/atm for board operations.','--to','live-codex')
log=open(ROOT/'resume-watch.log','w');p=subprocess.Popen([ATM,'watch','--agent','live-codex','--cwd',str(w),'--every','1','--max-runs','1','--run-timeout','4'],cwd=w,env=e,stdout=log,stderr=log,start_new_session=True)
try:p.wait(timeout=220)
except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGTERM);p.wait(timeout=20)
log.close();print('RESUME',p.returncode,flush=True)
t=ticket(repo);(ROOT/'after-resume.json').write_text(json.dumps(t,indent=2));assert t['status']=='review',t['status']
sha=git(w,'rev-parse','HEAD').stdout.strip();assert git(repo,'show',sha+':finished.txt').stdout=='stage-one\nstage-two\n'
assert t['owner']==before['owner'] and t['owner_generation']==before['owner_generation']
atm(repo,'accept','T-001','--sha',sha,'--notes','Independent coordinator reads exact commit: both stages intact',agent='bob')
(ROOT/'live-result.json').write_text(json.dumps({'status':'PASS real Codex interrupted and continued','sha':sha,'ticket':ticket(repo)},indent=2));print('PASS',sha,flush=True)
