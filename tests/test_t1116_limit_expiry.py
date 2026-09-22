"""A stale quota observation cannot park an otherwise eligible seat forever."""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from ticket_board import route_headroom as rh
from test_t1022_provider_limits import cli, record
from test_t478_limit_outcome import _alice_on_docs
from test_wakeup import board, run  # noqa: F401
from test_t1041_route_headroom import TOOLS, TOOL_IDS, _pair, show, run as route_run


def old_limit(hours=6, **extra):
    return dict(at=(datetime.now(timezone.utc) - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                reset_at='', note='Individual quota reached', source='provider', **extra)


@pytest.mark.parametrize('reset', ['', 'bad timestamp', '2026-09-21'])
def test_unknown_or_malformed_reset_has_bounded_window(reset):
    lim = old_limit()
    lim['reset_at'] = reset
    assert rh.seat_limit({'limit': lim}) is None
    lim['at'] = datetime.now(timezone.utc).isoformat()
    assert rh.seat_limit({'limit': lim}) == lim


def test_explicit_weekly_and_future_reset_are_not_shortened():
    lim = old_limit(6)
    lim['note'] = "You've hit your weekly limit"
    assert rh.seat_limit({'limit': lim}) == lim
    lim = old_limit(169)
    lim['note'] = 'weekly quota reached'
    assert rh.seat_limit({'limit': lim}) is None
    lim['reset_at'] = '2099-01-01T00:00:00Z'
    assert rh.seat_limit({'limit': lim}) == lim


@pytest.mark.parametrize('text,expected', [
    ('back in 50m', '2026-09-15T12:50:00Z'),
    ('resets in 2 hours', '2026-09-15T14:00:00Z'),
    ('retry in 30 seconds', '2026-09-15T12:00:30Z'),
    ('resets 1:40am', '2026-09-15T17:40:00Z'),
    ('resets 7pm', '2026-09-16T11:00:00Z'),
    ('resets 13:99pm', ''),
])
def test_capture_clock_and_duration(monkeypatch, text, expected):
    old_tz = __import__('os').environ.get('TZ')
    monkeypatch.setenv('TZ', 'Asia/Singapore')
    time.tzset()
    try:
        assert rh.provider_reset_at(text, '2026-09-15T12:00:00Z') == expected
    finally:
        if old_tz is None:
            monkeypatch.delenv('TZ')
        else:
            monkeypatch.setenv('TZ', old_tz)
        time.tzset()


def test_capture_relative_reset_from_rejection(board, monkeypatch):
    _alice_on_docs(board, monkeypatch)
    monkeypatch.setattr(cli, 'now', lambda: '2026-09-15T12:00:00Z')
    cli._watch_note_limit_from_log(str(board), 'alice', "You've hit your session limit; back in 50m")
    assert record(board)['limit']['reset_at'] == '2026-09-15T12:50:00Z'


@pytest.mark.parametrize('tool', TOOLS, ids=TOOL_IDS)
def test_stale_resetless_seat_is_routable(tool, board):
    _pair(tool, board)
    path = board / 'agents/alice.json'
    rec = json.loads(path.read_text())
    rec['limit'] = old_limit()
    path.write_text(json.dumps(rec))
    result = route_run(tool, board, 'route', '--only', 'alice', agent='bob')
    assert result.returncode == 0, result.stdout + result.stderr
    assert show(tool, board, 'T-001', agent='bob')['suggested'] == 'alice'


def test_stale_limit_is_wakeable_and_reason_survives(board, monkeypatch):
    repo, tid = _alice_on_docs(board, monkeypatch)
    cli._agent_update(str(board), 'alice', lambda rec: rec.update(
        limit=old_limit(), adapter_failure={'state': 'failed', 'trigger': 'same'}))
    pending = cli.pending_work(str(board), 'alice')
    assert not pending.get('limited')
    assert any(tid in item for item in pending['holding'])
    marker = repo / 'resumed'
    result = run(board, 'watch', '--agent', 'alice', '--once', '--exec',
                 'touch ' + str(marker), '--cwd', str(repo), agent='alice', cwd=repo)
    assert marker.exists(), result.stdout + result.stderr
    rec = record(board)
    assert not rec.get('limit') and not rec.get('adapter_failure')
    assert '5h retry window elapsed' in rec['limit_expired_reason']


def test_stale_attention_and_limits_age_order(board, monkeypatch):
    repo, _ = _alice_on_docs(board, monkeypatch)
    cli._agent_update(str(board), 'alice', lambda rec: rec.update(limit=old_limit(6)))
    cli.checkin(str(board), 'older')
    cli._agent_update(str(board), 'older', lambda rec: rec.update(limit=old_limit(12)))
    snapshot = cli.board_snapshot(str(board))
    assert any('alice stale usage limit no longer blocks' in item['msg'] for item in snapshot['attention'])
    result = run(board, 'limits', agent='alice', cwd=repo)
    recorded = result.stdout.split('Probably limited')[0]
    assert '[STALE:' in recorded and 'no longer blocks' in recorded
    assert recorded.index('older') < recorded.index('alice')


def test_read_only_expiry_ignores_old_transcript_without_writing(board, monkeypatch):
    _alice_on_docs(board, monkeypatch)
    cli._agent_update(str(board), 'alice', lambda rec: rec.update(limit=old_limit()))
    before = (board / 'agents/alice.json').read_bytes()
    monkeypatch.setattr(cli, '_transcript_over_cwds', lambda cwds: ('limited', 7 * 3600, 'old rejection', str(board.parent)))
    monkeypatch.setattr(cli._WATCH_TABLE, 'read_only', True, raising=False)
    assert cli.agent_liveness(str(board), record(board))['state'] != 'limited'
    assert (board / 'agents/alice.json').read_bytes() == before


def test_malformed_observation_does_not_block_forever():
    assert rh.seat_limit({'limit': {'note': 'quota reached', 'at': 'invalid'}}) is None


def test_attention_clears_when_watcher_resumes(board, monkeypatch):
    _alice_on_docs(board, monkeypatch)
    cli._agent_update(str(board), 'alice', lambda rec: rec.update(limit=old_limit()))
    cli._active_seat_limit(str(board), 'alice')
    cli._run_begin(str(board), 'alice', 1, str(board.parent))
    assert not any('alice stale usage limit' in msg for _, msg, _ in cli.health(str(board), cli.load_all(str(board))))


def test_new_rejection_after_expiry_starts_new_hold(board, monkeypatch):
    _alice_on_docs(board, monkeypatch)
    cli._agent_update(str(board), 'alice', lambda rec: rec.update(limit=old_limit()))
    cli._watch_note_limit_from_log(str(board), 'alice', "You've hit your session limit")
    assert cli.pending_work(str(board), 'alice').get('limited')
    assert not record(board).get('expired_limit')


def test_cli_board_attention_names_stale_seat(board, monkeypatch):
    repo, _ = _alice_on_docs(board, monkeypatch)
    cli._agent_update(str(board), 'alice', lambda rec: rec.update(limit=old_limit()))
    result = run(board, 'board', agent='alice', cwd=repo)
    assert 'attention: alice stale usage limit no longer blocks' in result.stdout
