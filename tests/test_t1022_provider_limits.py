"""Provider rejections pause seats without losing their held work or mail."""
import importlib.util
import json
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_t478_limit_outcome import _alice_on_docs
from test_wakeup import board, run  # noqa: F401
from test_trajectories import events

TOOL = Path(__file__).resolve().parents[1] / 'tickets.py'
spec = importlib.util.spec_from_file_location('limits_cli', TOOL)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def record(board):
    return json.loads((board / 'agents/alice.json').read_text())


@pytest.mark.parametrize('with_reset', [True, False])
def test_ticket_update_then_exit_zero_rejection_records_limit(board, monkeypatch, with_reset):
    repo, tid = _alice_on_docs(board, monkeypatch)
    reset = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    rejection = "You've hit your session limit" + (' · resets ' + reset if with_reset else '')
    command = shlex.join([sys.executable, str(TOOL), 'update', tid, 'work before rejection'])
    command += ' && printf "%s\\n" ' + shlex.quote(rejection) + '; exit 0'
    result = run(board, 'watch', '--agent', 'alice', '--once', '--exec', command,
                 '--cwd', str(repo), agent='alice', cwd=repo)
    assert result.returncode == 0, result.stdout + result.stderr
    ended = events(board, kind='run_end')[-1]
    assert ended['exit'] == 0 and ended.get('bound_write') is True
    history = run(board, 'show', tid, agent='alice', cwd=repo).stdout
    assert 'work before rejection' in history
    lim = record(board)['limit']
    assert lim['source'] == 'provider'
    assert lim['reset_at'] == (reset if with_reset else '')
    assert 'LIMITED: alice' in history and rejection in history
    assert 'Automatic retrigger paused' in history
    assert cli.pending_work(str(board), 'alice').get('limited')
    marker = repo / 'must-not-retrigger'
    denied = run(board, 'watch', '--agent', 'alice', '--once', '--force',
                 '--exec', shlex.join(['touch', str(marker)]), '--cwd', str(repo),
                 agent='alice', cwd=repo)
    assert denied.returncode == 1, denied.stdout + denied.stderr
    assert not marker.exists()
    assert record(board)['ticket'] == tid


@pytest.mark.parametrize('rc', [0, 1])
def test_real_claude_rejection_is_visible_and_force_cannot_retry(board, monkeypatch, rc):
    repo, tid = _alice_on_docs(board, monkeypatch)
    result = run(board, 'watch', '--agent', 'alice', '--once', '--exec',
                 'echo "You\'ve hit your session limit · resets 7pm (Asia/Singapore)"; exit %d' % rc,
                 '--cwd', str(repo), agent='alice', cwd=repo)
    assert result.returncode == 0, result.stdout + result.stderr
    lim = record(board)['limit']
    assert lim['source'] == 'provider'
    assert lim['until'] == '7pm (Asia/Singapore)'
    assert lim['reset_at'].endswith('T11:00:00Z')
    shown = run(board, 'who', agent='alice', cwd=repo).stdout
    assert 'limited' in shown and '7pm (Asia/Singapore)' in shown
    history = run(board, 'show', tid, agent='alice', cwd=repo).stdout
    assert 'LIMITED: alice' in history and 'Automatic retrigger paused' in history
    marker = repo / 'must-not-run'
    denied = run(board, 'watch', '--agent', 'alice', '--once', '--force',
                 '--exec', 'touch ' + str(marker), '--cwd', str(repo), agent='alice', cwd=repo)
    assert not marker.exists(), denied.stdout + denied.stderr
    assert cli.pending_work(str(board), 'alice').get('limited')
    assert record(board)['ticket'] == tid


def test_reset_elapsed_releases_original_work_and_failed_trigger(board, monkeypatch):
    repo, tid = _alice_on_docs(board, monkeypatch)
    past = (datetime.now(timezone.utc) - timedelta(seconds=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
    cli._watch_note_limit_from_log(str(board), 'alice',
        "You've hit your session limit · resets " + past, rc=1)
    cli._agent_update(str(board), 'alice', lambda rec: rec.update(
        adapter_failure={'state': 'failed', 'trigger': 'same'}))
    pending = cli.pending_work(str(board), 'alice')
    assert not pending.get('limited')
    assert any(tid in item for item in pending['holding'])
    assert not record(board).get('limit')
    assert not record(board).get('adapter_failure')
    marker = repo / 'resumed'
    result = run(board, 'watch', '--agent', 'alice', '--once', '--exec',
                 'touch ' + str(marker), '--cwd', str(repo), agent='alice', cwd=repo)
    assert marker.exists(), result.stdout + result.stderr


@pytest.mark.parametrize('reset', ['', 'soon', '7pm', 'Sep 13 at 8am'])
def test_unknown_reset_stays_limited_without_inventing_timestamp(board, monkeypatch, reset):
    _alice_on_docs(board, monkeypatch)
    text = "You've hit your session limit" + (' · resets ' + reset if reset else '')
    cli._watch_note_limit_from_log(str(board), 'alice', text, rc=0)
    assert record(board)['limit']['reset_at'] == ''
    assert cli.pending_work(str(board), 'alice').get('limited')
    cli._watch_note_limit_from_log(str(board), 'alice', text, rc=0)
    notes = cli.load(str(board), 'T-001')['notes']
    assert sum('LIMITED: alice' in n['text'] for n in notes) == 1


@pytest.mark.parametrize('text', [
    'discussing the rate limit; session resets at 23:00Z',
    '{"type":"token_count","rate_limits":{"limit_id":"premium"}}',
    'ticket T-429 finished successfully',
])
def test_successful_prose_and_telemetry_do_not_pause(board, monkeypatch, text):
    _alice_on_docs(board, monkeypatch)
    assert not cli._watch_note_limit_from_log(str(board), 'alice', text, rc=0)
    assert not record(board).get('limit')


def test_structured_rate_limit_and_native_message_guard(board, monkeypatch):
    repo, _ = _alice_on_docs(board, monkeypatch)
    cli._watch_note_limit_from_log(str(board), 'alice',
        '{"type":"error","error":{"type":"rate_limit_error","message":"capacity exhausted"}}', rc=0)
    assert record(board)['limit']['reset_at'] == ''
    result = run(board, 'msg', 'resume your work', '--task', '--to', 'alice',
                 agent='planner', cwd=repo)
    assert 'wake: alice -> limited (reset unknown)' in result.stdout
    assert not cli._poke_persist_watch(str(board), 'alice')
    assert 'resume your work' in run(board, 'inbox', agent='alice', cwd=repo).stdout


def test_daily_reset_uses_provider_zone_and_rolls_midnight():
    assert cli._provider_reset_at('1:40am (Asia/Singapore)', '2026-09-15T10:00:00Z') == '2026-09-15T17:40:00Z'
    assert cli._provider_reset_at('7pm (Asia/Singapore)', '2026-09-15T12:00:00Z') == '2026-09-16T11:00:00Z'
    assert cli._provider_reset_at('7pm (Mars/Olympus)', '2026-09-15T12:00:00Z') == ''


def test_limited_overrides_fresh_work_and_team_snapshot(board, monkeypatch):
    _alice_on_docs(board, monkeypatch)
    cli._watch_note_limit_from_log(str(board), 'alice', "You've hit your session limit", rc=0)
    monkeypatch.setattr(cli, '_transcript_over_cwds', lambda cwds: ('working', 0, 'fresh work', str(board.parent)))
    live = cli.agent_liveness(str(board), record(board))
    assert live['state'] == 'limited' and live['source'] == 'provider'
    snap = cli.board_snapshot(str(board))
    seat = next(a for a in snap['agents'] if a['name'] == 'alice')
    assert seat['state'] == 'LIMITED'
    assert not seat['wake_pending']
    assert seat['limit_until'] == ''


def test_structured_rejection_after_bound_write_pauses(board, monkeypatch):
    _alice_on_docs(board, monkeypatch)
    cli._watch_note_limit_from_log(str(board), 'alice',
        '{"type":"error","error":{"type":"rate_limit_error"}}', rc=0, bound_write=True)
    assert record(board)['limit']['source'] == 'provider'
    assert record(board)['limit']['reset_at'] == ''


def test_elapsed_limit_does_not_reappear_from_old_transcript(board, monkeypatch):
    _alice_on_docs(board, monkeypatch)
    past = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    cli._watch_note_limit_from_log(str(board), 'alice', "You've hit your session limit · resets " + past)
    monkeypatch.setattr(cli, '_transcript_over_cwds', lambda cwds: ('limited', 120, 'old rejection', str(board.parent)))
    assert cli.agent_liveness(str(board), record(board))['state'] != 'limited'
    # A new failure remains evidence after the previous hold has expired.
    monkeypatch.setattr(cli, '_transcript_over_cwds', lambda cwds: ('limited', 0, 'new rejection', str(board.parent)))
    assert cli.agent_liveness(str(board), record(board))['state'] == 'limited'


def test_explicit_clear_releases_unknown_reset(board, monkeypatch):
    repo, _ = _alice_on_docs(board, monkeypatch)
    cli._watch_note_limit_from_log(str(board), 'alice', "You've hit your session limit")
    cli._agent_update(str(board), 'alice', lambda rec: rec.update(adapter_failure={'state': 'failed'}))
    result = run(board, 'limit', 'alice', '--clear', agent='alice', cwd=repo)
    assert result.returncode == 0, result.stderr
    assert not record(board).get('limit')
    assert not record(board).get('adapter_failure')


def test_structured_error_preserves_provider_reset(board, monkeypatch):
    _alice_on_docs(board, monkeypatch)
    cli._watch_note_limit_from_log(str(board), 'alice', json.dumps({
        'type': 'error', 'error': {'type': 'rate_limit_error',
                                 'message': 'Quota exhausted; resets 2026-09-16T11:00:00Z'}}), rc=1)
    assert record(board)['limit']['reset_at'] == '2026-09-16T11:00:00Z'
