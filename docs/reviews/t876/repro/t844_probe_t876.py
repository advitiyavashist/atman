"""Independent disposable-board probes; never touches the production board."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(os.environ['T876_CAND'])
WHEEL = Path(os.environ['T876_WHEEL'])
scratch = Path(tempfile.mkdtemp(prefix='atman-t844-probes-', dir=os.environ['T876_SCRATCH']))
print('ARTIFACT_ROOT', scratch, flush=True)

def run(entry, board, args, sid='fresh', agent=None, seat=None, cwd=None, stdin=None):
    env = dict(os.environ)
    for var in ('TICKET_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CODEX_SESSION_ID',
                'CURSOR_SESSION_ID', 'TERM_SESSION_ID', 'CURSOR_CONVERSATION_ID',
                'TICKET_AGENT', 'TICKET_SEAT', 'TICKETS_DIR', 'PYTHONPATH'):
        env.pop(var, None)
    env.update(TICKETS_DIR=str(board / '.tickets'), TICKETS_CACHE_DIR=str(scratch / 'cache'),
               TICKET_SESSION_ID=sid)
    if agent:
        env['TICKET_AGENT'] = agent
    if seat:
        env['TICKET_SEAT'] = seat
    if entry == 'baseline':
        cmd = [sys.executable, os.environ['T876_BASE'] + '/tickets.py']
    else:
        cmd = [sys.executable, str(ROOT / 'tickets.py')] if entry == 'root' else [str(WHEEL)]
    return subprocess.run(cmd + args, cwd=str(cwd or board), env=env, input=stdin,
                          text=True, capture_output=True, timeout=30)

for entry in ('root', 'wheel'):
    board = scratch / entry
    board.mkdir()
    for args, sid, agent in [(['init'], 'setup', 'setup'),
                             (['create', 'probe', '--role', 'verification'], 'setup', 'setup'),
                             (['join', 'parent-seat', '--roles', 'verification'], 'parent-sid', 'parent-seat'),
                             (['msg', 'SECRET_FOR_PARENT', '--to', 'parent-seat'], 'sender-sid', 'sender'),
                             (['msg', 'ONLY_FOR_WORKER', '--to', 'worker-seat'], 'sender-sid', 'sender')]:
        result = run(entry, board, args, sid=sid, agent=agent)
        assert result.returncode == 0, result.stdout + result.stderr
    safe = run(entry, board, ['inbox', '--keep', '--quiet-if-unidentified'],
               sid='parent-sid', agent='worker-seat', seat='worker-seat')
    assert safe.returncode == 0 and 'ONLY_FOR_WORKER' in safe.stdout and 'SECRET_FOR_PARENT' not in safe.stdout, safe.stdout + safe.stderr
    print(entry, 'EXPLICIT_SEAT_OVERRIDES_FORGED_SID=PASS', flush=True)
    forged = run(entry, board, ['inbox', '--keep', '--quiet-if-unidentified'],
                 sid='parent-sid', agent='worker-seat')
    print(entry, 'FORGED_SID_WITHOUT_ASSIGNED_SEAT_LEAK=', 'SECRET_FOR_PARENT' in forged.stdout, flush=True)
    owned = run(entry, board, ['board', '-q'], sid='parent-sid', agent='wrong-ambient')
    assert 'you: parent-seat' in owned.stdout and 'UNCONFIRMED' not in owned.stdout, owned.stdout
    print(entry, 'GENUINE_RECORDED_SESSION_PRECEDENCE=PASS', flush=True)

board = scratch / 'root'
launcher = scratch / 'launcher'
target = scratch / 'worker-tree'
launcher.mkdir()
target.mkdir()
installed = run('root', board, ['hooks', 'cursor', '--agent', 'test-role-seat', '--worktree', str(target)],
                sid='parent-sid', agent='parent-seat')
assert installed.returncode == 0, installed.stdout + installed.stderr
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('t844_candidate', ROOT / 'tickets.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
saved = dict(os.environ)
try:
    os.environ['TICKETS_CACHE_DIR'] = str(scratch / 'cache')
    os.environ['TICKET_SESSION_ID'] = 'parent-sid'
    os.environ['CURSOR_CONVERSATION_ID'] = 'parent-conversation'
    os.environ['TICKET_AGENT'] = 'parent-seat'
    env = module._supervisor_launch_env(str(board / '.tickets'), 'worker-seat')
    assert env['TICKET_SEAT'] == env['TICKET_AGENT'] == 'worker-seat'
    assert env['TICKET_SESSION_ID'].startswith('launch:worker-seat:')
    assert env.get('CURSOR_CONVERSATION_ID') is None
    assert module._pin_spawned_worker_hooks(str(board / '.tickets'), 'worker-seat', str(target), 'cursor')
finally:
    os.environ.clear()
    os.environ.update(saved)
cfg = json.loads((target / '.cursor' / 'hooks.json').read_text())
command = cfg['hooks']['sessionStart'][0]['command']
hook_env = dict(os.environ, TICKET_AGENT='parent-seat', TICKET_SEAT='parent-seat',
                TICKET_SESSION_ID='parent-sid', TICKETS_CACHE_DIR=str(scratch / 'cache'))
hook = subprocess.run(command, shell=True, cwd=str(launcher), env=hook_env,
                      input=json.dumps({'session_id': 'parent-sid', 'workspace_roots': [str(target)]}),
                      text=True, capture_output=True, timeout=30)
assert hook.returncode == 0, hook.stdout + hook.stderr
hook_output = hook.stdout
assert 'SECRET_FOR_PARENT' not in hook_output, hook_output
assert "AGENT = 'worker-seat'" in (target / '.cursor' / 'hooks' / 'tickets-board.py').read_text()
print('ROOT_GENERATED_WORKER_HOOK_IDENTITY=PASS', 'output=', hook_output[:250], flush=True)
spawned = run('root', board, ['spawn', 'spawn-worker', '--harness', 'cursor', '--roles', 'verification',
                            '--exec', 'true', '--every', '3600', '--max-runs', '1', '--worktree', str(target)],
              sid='parent-sid', agent='parent-seat', cwd=launcher)
try:
    assert spawned.returncode == 0, spawned.stdout + spawned.stderr
    rec = json.loads((board / '.tickets' / 'agents' / 'spawn-worker.json').read_text())
    assert Path(rec['cwd']).resolve() == target.resolve(), rec
    assert Path(rec.get('worktree') or rec['cwd']).resolve() == target.resolve(), rec
    print('ROOT_REAL_SPAWN_WORKTREE_REGISTRATION=PASS', rec['cwd'], flush=True)
finally:
    stopped = run('root', board, ['spawn', 'spawn-worker', '--stop'], agent='parent-seat', cwd=launcher)
    print('DISPOSABLE_WATCHER_STOP', stopped.returncode, stopped.stdout.strip(), flush=True)
print('NO_CURSOR_MODEL_OR_LIVE_WAKE_EXECUTED', flush=True)

for entry in ('baseline', 'root'):
    b = scratch / ('remote-' + entry)
    b.mkdir()
    for args, sid, agent in [(['init'], 'setup', 'setup'),
                             (['join', 'parent-seat', '--roles', 'verification'], 'parent-sid', 'parent-seat'),
                             (['msg', 'REMOTE_SECRET_FOR_PARENT', '--to', 'parent-seat'], 'sender-sid', 'sender'),
                             (['msg', 'REMOTE_ONLY_FOR_WORKER', '--to', 'worker-seat'], 'sender-sid', 'sender')]:
        r = run(entry, b, args, sid=sid, agent=agent)
        assert r.returncode == 0, r.stdout + r.stderr
    wrapper = b / 'worker-wrapper'
    r = run(entry, b, ['hooks', 'remote', '--agent', 'worker-seat', '--wrapper', str(wrapper)],
            sid='parent-sid', agent='parent-seat')
    assert r.returncode == 0, r.stdout + r.stderr
    env = dict(os.environ, TICKET_SESSION_ID='parent-sid', TICKET_AGENT='parent-seat',
               TICKETS_DIR=str(b / '.tickets'), TICKETS_CACHE_DIR=str(scratch / 'cache'))
    env.pop('TICKET_SEAT', None)
    r = subprocess.run([str(wrapper), 'inbox', '--keep'], cwd=str(launcher), env=env,
                       capture_output=True, text=True, timeout=30)
    print(entry, 'REMOTE_WRAPPER_INHERITED_SID', r.returncode,
          'PARENT_SECRET_LEAK=', 'REMOTE_SECRET_FOR_PARENT' in r.stdout,
          'WORKER_MAIL_VISIBLE=', 'REMOTE_ONLY_FOR_WORKER' in r.stdout, flush=True)
    if entry == 'root':
        assert 'REMOTE_SECRET_FOR_PARENT' not in r.stdout and 'REMOTE_ONLY_FOR_WORKER' in r.stdout, r.stdout + r.stderr
    env['TICKET_SEAT'] = 'parent-seat'
    r = subprocess.run([str(wrapper), 'msg', 'WRAPPER_ATTRIBUTION_PROBE'], cwd=str(launcher), env=env,
                       capture_output=True, text=True, timeout=30)
    records = [json.loads(line) for line in (b / '.tickets' / 'messages.jsonl').read_text().splitlines()]
    post = next(m for m in reversed(records) if m['text'] == 'WRAPPER_ATTRIBUTION_PROBE')
    print(entry, 'REMOTE_WRAPPER_INHERITED_ASSIGNED_SEAT_SENDER=', post['from'], flush=True)
    if entry == 'root':
        assert post['from'] == 'worker-seat', post

claude_parent = scratch / 'claude-parent'
claude_worker = scratch / 'claude-worker'
claude_parent.mkdir()
claude_worker.mkdir()
local = claude_parent / '.claude' / 'settings.local.json'
r = run('root', board, ['hooks', 'claude', '--agent', 'parent-seat', '--settings', str(local)],
        sid='parent-sid', agent='parent-seat')
assert r.returncode == 0, r.stdout + r.stderr
parent_local_before = local.read_text()
copied = module._inherit_settings(str(claude_parent), str(claude_worker))
assert module._pin_spawned_worker_hooks(str(board / '.tickets'), 'worker-seat', str(claude_worker), 'claude')
worker_local = claude_worker / '.claude' / 'settings.local.json'
clean = json.loads(worker_local.read_text())
commands = [h['command'] for entries in clean.get('hooks', {}).values()
            for entry in entries for h in entry.get('hooks', []) if 'command' in h]
from ticket_board.session_boundary import is_board_hook
assert not any(is_board_hook(c) for c in commands), commands
assert local.read_text() == parent_local_before, 'parent settings mutated'
print('CLAUDE_SETTINGS_LOCAL_CANONICAL_HOOK_SURVIVES_WORKER_PIN=False PASS', 'copied=', copied, flush=True)
