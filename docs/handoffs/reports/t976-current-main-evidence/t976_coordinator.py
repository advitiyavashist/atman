exec(open('/tmp/t976_journeys.py').read().split("for name,fn in [('normal'")[0])
repo=setup('coordinator');create(repo,'Coordinator interruption target');w=worktree(repo,'boss-probe')
worker=ROOT/'coordinator.py';worker.write_text("import pathlib,time\npathlib.Path('coordinator-mid.txt').write_text('durable coordinator artifact\\n')\ntime.sleep(120)\n")
cmd=f'{sys.executable} {worker} {{prompt_file}}'
e=dict(ENV,TICKET_AGENT='boss',TICKET_SESSION_ID='t976-boss')
log=open(ROOT/'watch.log','w')
p=subprocess.Popen([ATM,'watch','--agent','boss','--exec',cmd,'--prompt-kind','master','--cwd',str(w),'--force','--max-runs','1'],cwd=w,env=e,stdout=log,stderr=log,start_new_session=True)
try:
 deadline=time.monotonic()+20
 while not (w/'coordinator-mid.txt').exists() and p.poll() is None and time.monotonic()<deadline:time.sleep(.1)
 assert (w/'coordinator-mid.txt').exists(),(ROOT/'watch.log').read_text()
 os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=5)
 r=atm(w,'watch','--agent','boss','--exec','/usr/bin/true {prompt_file}','--prompt-kind','master','--cwd',str(w),'--force','--max-runs','1',ok=None)
 assert r.returncode==0,r.stdout+r.stderr
 assert (w/'coordinator-mid.txt').read_text()=='durable coordinator artifact\n'
 result={'status':'PASS custom master watcher killed/restarted','killed_returncode':p.returncode,'restart':r.stdout,'artifact':str(w/'coordinator-mid.txt')}
except Exception as ex:result={'status':'FAIL','error':str(ex)}
finally:
 if p.poll() is None:os.killpg(p.pid,signal.SIGKILL);p.wait()
 log.close()
(ROOT/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result));print('ARTIFACTS',ROOT)
