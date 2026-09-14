"""Compare the exact previously failed boundary with the repaired candidate."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPAIRED = Path(os.environ['T876_CAND'])
PRIOR = Path(os.environ['T876_PRIOR'])
scratch = Path(tempfile.mkdtemp(prefix='t847-prior-comparison-', dir=os.environ['T876_SCRATCH']))
print('SCRATCH', scratch, flush=True)
for name, root in [('prior-feca2f5', PRIOR), ('candidate-f3289b2', REPAIRED)]:
    repo = scratch / name
    repo.mkdir()
    board = repo / '.tickets'
    env = {'PATH': os.environ['PATH'], 'HOME': str(repo), 'TICKETS_DIR': str(board),
           'TICKETS_CACHE_DIR': str(repo / 'cache')}
    def run(args, agent, sid):
        result = subprocess.run([sys.executable, str(root / 'tickets.py')] + args,
                                cwd=str(repo), env=dict(env, TICKET_AGENT=agent, TICKET_SESSION_ID=sid),
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return result
    run(['init'], 'setup', 'setup')
    run(['join', 'parent-seat', '--roles', 'verification'], 'parent-seat', 'parent-sid')
    run(['msg', 'PARENT_PRIVATE', '--to', 'parent-seat'], 'sender', 'sender-sid')
    run(['msg', 'WORKER_TASK', '--to', 'worker-seat'], 'sender', 'sender-sid')
    wrapper = repo / 'wrapper'
    run(['hooks', 'remote', '--agent', 'worker-seat', '--wrapper', str(wrapper)], 'parent-seat', 'parent-sid')
    hostile = dict(env, TICKET_AGENT='parent-seat', TICKET_SEAT='parent-seat', TICKET_SESSION_ID='parent-sid', CURSOR_SESSION_ID='parent-sid', CODEX_SESSION_ID='parent-sid')
    inbox = subprocess.run([str(wrapper), 'inbox', '--keep'], cwd=str(repo), env=hostile, capture_output=True, text=True)
    post = subprocess.run([str(wrapper), 'msg', 'WRAPPER_POST'], cwd=str(repo), env=hostile, capture_output=True, text=True)
    records = [json.loads(line) for line in (board / 'messages.jsonl').read_text().splitlines()]
    sender = next(row['from'] for row in reversed(records) if row['text'] == 'WRAPPER_POST')
    leak = 'PARENT_PRIVATE' in inbox.stdout
    print(name, 'remote_parent_leak=', leak, 'worker_mail=', 'WORKER_TASK' in inbox.stdout, 'sender=', sender, flush=True)
    assert (leak, sender) == ((True, 'parent-seat') if name.startswith('prior') else (False, 'worker-seat'))
    parent, worker = repo / 'parent', repo / 'worker'
    parent.mkdir(); worker.mkdir()
    local = parent / '.claude/settings.local.json'
    run(['hooks', 'claude', '--agent', 'parent-seat', '--settings', str(local)], 'parent-seat', 'parent-sid')
    before = local.read_bytes()
    code = ('import importlib.util,sys; sys.path.insert(0,sys.argv[1]); '
            's=importlib.util.spec_from_file_location("probe",sys.argv[1]+"/tickets.py"); '
            'm=importlib.util.module_from_spec(s);s.loader.exec_module(m); '
            'm._inherit_settings(sys.argv[2],sys.argv[3]); '
            'assert m._pin_spawned_worker_hooks(sys.argv[4],"worker-seat",sys.argv[3],"claude")')
    result = subprocess.run([sys.executable, '-c', code, str(root), str(parent), str(worker), str(board)], env=hostile, cwd=str(repo), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    clean = json.loads((worker / '.claude/settings.local.json').read_text())
    commands = [h['command'] for entries in clean.get('hooks', {}).values() for entry in entries for h in entry.get('hooks', []) if 'command' in h]
    parent_hook = any('--agent parent-seat' in command for command in commands)
    print(name, 'claude_parent_hook_survives=', parent_hook, 'parent_bytes_unchanged=', local.read_bytes() == before, flush=True)
    assert parent_hook == name.startswith('prior')
    assert local.read_bytes() == before
