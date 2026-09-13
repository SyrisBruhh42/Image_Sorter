"""POSIX endpoint preservation and admission failures keep journal identities intact."""
import os
import socket
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from imagesorter import mutation_client
from imagesorter.mutation_client import MutationClient
from imagesorter.mutation_service import _remove_stale_socket
from imagesorter.queue_worker import QueueWorker
from imagesorter.settings_manager import SettingsManager

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Pathname sockets apply to supported POSIX mutation service')


def test_stale_socket_cleanup_preserves_regular_and_symlink_entries(tmp_path):
    original = tmp_path / 'original'
    original.write_bytes(b'unrelated user content')
    endpoint = tmp_path / 'mutation.sock'
    endpoint.write_bytes(b'another file')
    with pytest.raises(RuntimeError, match='preserved'):
        _remove_stale_socket(str(endpoint))
    assert endpoint.read_bytes() == b'another file'
    endpoint.unlink()
    endpoint.symlink_to(original)
    with pytest.raises(RuntimeError, match='preserved'):
        _remove_stale_socket(str(endpoint))
    assert endpoint.is_symlink() and original.read_bytes() == b'unrelated user content'


def test_stale_socket_cleanup_only_removes_current_user_socket(tmp_path, monkeypatch):
    # Keep the native pathname short enough even on macOS temporary paths.
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix='is-sock-', dir='/tmp') as short:
        endpoint = Path(short) / 's'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(endpoint))
        actual = os.lstat(endpoint)
        with monkeypatch.context() as patch:
            patch.setattr('imagesorter.mutation_service.os.lstat', lambda path: SimpleNamespace(st_mode=actual.st_mode, st_uid=os.getuid() + 1))
            with pytest.raises(RuntimeError, match='preserved'):
                _remove_stale_socket(str(endpoint))
        assert endpoint.exists()
        _remove_stale_socket(str(endpoint))
        assert not endpoint.exists()
        _remove_stale_socket(str(endpoint))
        _remove_stale_socket('\0unchanged-linux-abstract')


def test_socket_preparation_refuses_before_client_admission(qtbot, tmp_path, monkeypatch):
    client = MutationClient(journal_path=tmp_path / 'journal.db')
    monkeypatch.setattr(mutation_client, 'service_address', Mock(side_effect=PermissionError('unsafe directory')))
    try:
        with pytest.raises(PermissionError, match='unsafe directory'):
            client.submit({'operation_id': 'unadmitted'})
        assert not client.pending and client.process is None
        messages = []
        client.status.connect(messages.append)
        monkeypatch.setattr(mutation_client.socket, 'socket', Mock(side_effect=AssertionError('Unsafe preparation must precede socket allocation')))
        client.poll()
        assert len(messages) == 1 and 'unsafe directory' in messages[0]
        assert client.connection is None and client.process is None and not client.pending
    finally:
        client.disconnect()


def test_poll_reports_startup_failure_without_dropping_uncertain_operation(qtbot, tmp_path, monkeypatch):
    client = MutationClient(journal_path=tmp_path / 'journal.db')
    request = {'operation_id': 'possibly-accepted'}
    client.pending['possibly-accepted'] = request
    messages, results = [], []
    client.status.connect(messages.append)
    client.result.connect(results.append)
    monkeypatch.setattr(client, '_connect', Mock(side_effect=OSError('socket preparation failed')))
    try:
        client.poll()
        client.poll()
        assert len(messages) == 1 and 'Pending operation IDs are preserved' in messages[0]
        assert client.pending == {'possibly-accepted': request} and results == []
        monkeypatch.setattr(client, '_connect', lambda: None)
        client.process = SimpleNamespace(poll=lambda: 2, returncode=2)
        client.poll()
        assert 'exited (2)' in messages[-1] and 'mutation-service.log' in messages[-1]
        assert client.pending == {'possibly-accepted': request} and results == []
    finally:
        client.process = None
        client.disconnect()


@pytest.mark.parametrize('action', ['move', 'undo_move', 'recover'])
def test_rejected_submission_preserves_request_map_and_enrichment(qtbot, tmp_path, monkeypatch, action):
    worker = QueueWorker(SettingsManager(filepath=str(tmp_path / 'settings.json')))
    monkeypatch.setattr(worker.client, 'submit', Mock(side_effect=PermissionError('unsafe socket directory')))
    worker._enrichment_tokens['old-token'] = 'old-primary'
    queued = ('old-primary', 'image.jpg', {})
    worker._enrichment_queue = deque([queued])
    try:
        with pytest.raises(PermissionError, match='unsafe socket'):
            if action == 'recover':
                worker.add_recovery_task({'operation_id': 'old-primary', 'state': 'recovery_required', 'source_path': 'image.jpg', 'manifest': {'version': 2, 'mode': 'move'}})
            else:
                worker.add_task(action, 'image.jpg', 'destination', undo_token={'token_id': 'old-token'})
        assert not worker._requests and not worker.client.pending
        assert list(worker._enrichment_queue) == [queued] and not worker._cancelled_enrichments
    finally:
        worker.shutdown()


@pytest.mark.parametrize('ready', [False, True], ids=['disconnected', 'ready'])
@pytest.mark.parametrize('invalid_kind', ['oversized', 'not-json'])
@pytest.mark.parametrize('action', ['move', 'undo_move', 'recover'])
def test_encoding_rejection_is_atomic_before_admission(qtbot, tmp_path, ready, invalid_kind, action):
    from imagesorter.worker_protocol import MAX_MESSAGE_BYTES

    worker = QueueWorker(SettingsManager(filepath=str(tmp_path / 'settings.json')))
    # Keep the real submit/encode path but prevent any helper or filesystem work.
    worker.client._timer.stop()
    worker.client._ready = ready
    worker.client._outgoing = b'previously queued bytes'
    worker._enrichment_tokens['old-token'] = 'old-primary'
    queued = ('old-primary', 'image.jpg', {})
    worker._enrichment_queue = deque([queued])
    existing_request = {'operation_id': 'old-primary', 'destination_path': 'image.jpg'}
    worker._requests['old-primary'] = existing_request
    payload = 'x' * MAX_MESSAGE_BYTES if invalid_kind == 'oversized' else object()
    expected_error = ValueError if invalid_kind == 'oversized' else TypeError
    try:
        with pytest.raises(expected_error):
            if action == 'recover':
                worker.add_recovery_task(
                    {'operation_id': 'old-primary', 'state': 'recovery_required',
                     'source_path': 'image.jpg', 'manifest': {'version': 2, 'mode': 'move'}},
                    task_options={'review_payload': payload},
                )
            else:
                worker.add_task(
                    action, 'image.jpg', 'destination', undo_token={'token_id': 'old-token'},
                    task_options={'review_payload': payload},
                )
        assert worker.client.pending == {}
        assert worker._requests == {'old-primary': existing_request}
        assert worker.client._outgoing == b'previously queued bytes'
        assert list(worker._enrichment_queue) == [queued]
        assert worker._enrichment_tokens == {'old-token': 'old-primary'}
        assert not worker._cancelled_enrichments
        assert worker.client.process is None and worker.client.connection is None
    finally:
        worker.shutdown()
