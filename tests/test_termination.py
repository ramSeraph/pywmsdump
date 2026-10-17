import json
import os
import signal
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from wmsdump.cli import FileWriter, main
from wmsdump.dumper import OGCServiceDumper
from wmsdump.enums import Extent, Operation, RetrievalMode, Service, WMSVersion
from wmsdump.errors import InvalidSortKeyException, SortKeyRequiredException
from wmsdump.state import get_state_from_files, recover_output
from wmsdump.termination import ExtractionStopped, GracefulStop

PARAMS = dict(url='https://example.com/wms', layername='test', service=Service.WMS,
              version=WMSVersion.V1_1_1, operation=Operation.GetMap,
              mode=RetrievalMode.OFFSET, sort_key='id')


def feature(key):
    return {'type': 'Feature', 'geometry': None, 'properties': {'id': key}}


def setup_checkpoint(tmp_path, mode=RetrievalMode.OFFSET):
    output = tmp_path / 'out.geojsonl'
    state_file = tmp_path / 'out.geojsonl.state'
    writer = FileWriter(output, keep_idx=False)
    params = dict(PARAMS, mode=mode)
    state = get_state_from_files(state_file, output, flush_output=writer.flush, **params)
    state.updatecb(state.get_dict())
    return output, state_file, writer, state


@pytest.mark.parametrize('has_temporary_state', [False, True])
def test_cli_restarts_after_interrupted_initial_checkpoint(tmp_path, has_temporary_state):
    output = tmp_path / 'out.geojsonl'
    state_file = tmp_path / 'out.geojsonl.state'
    temporary = tmp_path / 'out.geojsonl.state.tmp'
    args = ['extract', 'test', str(output), '-u', PARAMS['url'],
            '-s', 'WMS', '-f', 'GEORSS', '-k', 'id', '-b', '2']

    with patch('wmsdump.state.os.replace', side_effect=OSError('interrupted')):
        result = CliRunner().invoke(main, args)
    assert isinstance(result.exception, OSError)
    assert output.read_bytes() == b''
    assert not state_file.exists()
    assert temporary.exists()
    if has_temporary_state:
        # An unfinished temporary file must not supply progress on restart.
        temporary.write_text('{"index_done_till": 100')
    else:
        temporary.unlink()

    starts = []

    def fetch(dumper, count):
        starts.append(dumper.state.index_done_till)
        return [feature(1)]

    with patch.object(OGCServiceDumper, 'get_features', fetch):
        result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert starts == [0]
    assert [json.loads(line) for line in output.read_text().splitlines()] == [feature(1)]
    assert not state_file.exists()
    assert not temporary.exists()


@pytest.mark.parametrize('contents', [b'\n', b'{"partial":', b'{"type": "Feature"}\n'])
def test_nonempty_output_without_state_is_rejected(tmp_path, contents):
    output = tmp_path / 'out.geojsonl'
    state_file = tmp_path / 'out.geojsonl.state'
    output.write_bytes(contents)
    writer = FileWriter(output, keep_idx=False)

    assert get_state_from_files(state_file, output, flush_output=writer.flush, **PARAMS) is None
    assert output.read_bytes() == contents
    assert not state_file.exists()


@pytest.mark.parametrize('callbacks', [{}, {'flush_output': None}, {'flush_output': False}])
def test_flush_callback_required_before_recovery(tmp_path, callbacks):
    output, state_file, writer, state = setup_checkpoint(tmp_path)
    writer.write(feature(1))
    writer.close()
    before_output = output.read_bytes()
    before_state = state_file.read_bytes()

    with pytest.raises(TypeError, match='flush_output'):
        get_state_from_files(state_file, output, **callbacks, **PARAMS)

    assert output.read_bytes() == before_output
    assert state_file.read_bytes() == before_state


def test_failed_flush_does_not_advance_checkpoint(tmp_path):
    output, state_file, writer, state = setup_checkpoint(tmp_path)
    before = state_file.read_bytes()
    with patch.object(writer, 'flush', side_effect=OSError('flush failed')):
        state = get_state_from_files(state_file, output, flush_output=writer.flush, **PARAMS)
        writer.write(feature(1))
        with pytest.raises(OSError, match='flush failed'):
            state.update(1, 1)
    writer.close()

    assert state_file.read_bytes() == before
    assert not state_file.with_suffix('.state.tmp').exists()


def test_flush_precedes_atomic_state_and_no_per_batch_fsync(tmp_path):
    output, state_file, writer, state = setup_checkpoint(tmp_path)
    original = os.replace

    def inspect(source, destination):
        saved = json.loads(source.read_text())
        assert output.read_text().count('\n') == saved['downloaded_count'] == 1
        assert output.stat().st_size == saved['output_bytes']
        original(source, destination)

    with patch('wmsdump.state.os.replace', side_effect=inspect), patch('os.fsync') as sync:
        writer.write(feature(1))
        state.update(1, 1)
        sync.assert_not_called()
    writer.close()
    assert json.loads(state_file.read_text())['downloaded_count'] == 1


@pytest.mark.parametrize('mode', [RetrievalMode.OFFSET, RetrievalMode.EXTENT,
                                 RetrievalMode.EXTENT_FIXED_BUFFER])
def test_crash_tail_is_replayed_for_all_modes(tmp_path, mode):
    output, state_file, writer, state = setup_checkpoint(tmp_path, mode)
    writer.write(feature(1))
    if mode == RetrievalMode.OFFSET:
        state.update(1, 1)
    else:
        state.update_coverage('0', Extent.OPEN)
    committed = output.read_bytes()
    writer.write(feature(2))
    with patch('wmsdump.state.os.replace', side_effect=OSError('crash')):
        with pytest.raises(OSError):
            state.updatecb(state.get_dict())
    writer.close()
    with output.open('ab') as f:
        f.write(b'{"partial":')
    resumed = get_state_from_files(state_file, output, flush_output=writer.flush, **dict(PARAMS, mode=mode))
    assert resumed is not None
    assert output.read_bytes() == committed
    assert json.loads(state_file.read_text())['output_bytes'] == len(committed)


@pytest.mark.parametrize('force_sort_key_change', [False, True])
def test_missing_committed_output_is_not_skipped(tmp_path, force_sort_key_change):
    output, state_file, writer, state = setup_checkpoint(tmp_path)
    writer.write(feature(1))
    state.update(1, 1)
    writer.close()
    output.write_bytes(b'')
    assert get_state_from_files(state_file, output, flush_output=writer.flush,
                                force_sort_key_change=force_sort_key_change, **PARAMS) is None


@pytest.mark.parametrize('force_sort_key_change', [False, True])
def test_wrong_invocation_does_not_truncate_output(tmp_path, force_sort_key_change):
    output, state_file, writer, state = setup_checkpoint(tmp_path)
    writer.write(feature(1))
    writer.close()
    before = output.read_bytes()
    assert get_state_from_files(state_file, output, flush_output=writer.flush,
                                force_sort_key_change=force_sort_key_change,
                                **dict(PARAMS, layername='wrong', sort_key='name')) is None
    assert output.read_bytes() == before


@pytest.mark.parametrize('force_sort_key_change', [False, True])
def test_legacy_state_still_validated_without_inventing_offset(tmp_path, force_sort_key_change):
    output, state_file, writer, state = setup_checkpoint(tmp_path)
    state_file.write_text(json.dumps(state.get_dict()))
    writer.write(feature(1))
    writer.close()
    assert get_state_from_files(state_file, output, flush_output=writer.flush,
                                force_sort_key_change=force_sort_key_change, **PARAMS) is None
    assert output.read_text().count('\n') == 1


@pytest.mark.parametrize('old_key,error', [(None, SortKeyRequiredException), ('invalid', InvalidSortKeyException)])
def test_cli_corrects_sort_key_before_progress(tmp_path, caplog, old_key, error):
    output = tmp_path / 'out.geojsonl'
    state_file = tmp_path / 'out.geojsonl.state'
    args = ['extract', 'test', str(output), '-u', PARAMS['url'], '-s', 'WMS', '-f', 'GEORSS', '-b', '2']

    def fetch(dumper, count, no_index=False, no_sort=False):
        if not no_sort:
            if dumper.sort_key != 'id':
                raise error()
            assert dumper.state.index_done_till == 0
            assert json.loads(state_file.read_text())['sort_key'] == 'id'
        return [feature(1)]

    with caplog.at_level('INFO'), patch.object(OGCServiceDumper, 'get_features', fetch):
        result = CliRunner().invoke(main, args + (['-k', old_key] if old_key else []))
        assert result.exit_code == 0, result.output
        assert output.read_bytes() == b''
        assert json.loads(state_file.read_text())['sort_key'] == old_key
        assert 'No extraction progress saved; rerun with a valid --sort-key/-k' in caplog.text
        assert '--force-sort-key-change' not in caplog.text

        result = CliRunner().invoke(main, args + ['-k', 'id'])
    assert result.exit_code == 0, result.output
    assert [json.loads(line) for line in output.read_text().splitlines()] == [feature(1)]
    assert not state_file.exists()


@pytest.mark.parametrize('downloaded_count', [0, 1])
def test_cli_sort_key_change_after_progress_requires_force(tmp_path, caplog, downloaded_count):
    output, state_file, writer, state = setup_checkpoint(tmp_path)
    if downloaded_count:
        writer.write(feature(1))
    state.update(1, downloaded_count)
    if downloaded_count:
        writer.write(feature(999))  # Uncommitted tail must only be recovered once resuming is allowed.
    writer.close()
    before_output = output.read_bytes()
    before_state = state_file.read_bytes()
    args = ['extract', 'test', str(output), '-u', PARAMS['url'],
            '-s', 'WMS', '-f', 'GEORSS', '-k', 'name', '-b', '2']

    with patch.object(OGCServiceDumper, 'get_features') as get:
        CliRunner().invoke(main, args)
    get.assert_not_called()
    assert output.read_bytes() == before_output
    assert state_file.read_bytes() == before_state
    assert '--force-sort-key-change' in caplog.text

    def fetch_and_stop(dumper, count):
        assert dumper.sort_key == dumper.state.sort_key == 'name'
        assert dumper.state.index_done_till == 1
        assert json.loads(state_file.read_text())['sort_key'] == 'name'
        os.kill(os.getpid(), signal.SIGTERM)
        return [feature(2)]

    with patch.object(OGCServiceDumper, 'get_features', fetch_and_stop):
        result = CliRunner().invoke(main, args + ['--force-sort-key-change'])
    assert result.exit_code == 0, result.output
    saved = json.loads(state_file.read_text())
    assert saved['sort_key'] == 'name'
    assert saved['index_done_till'] == 3
    assert saved['downloaded_count'] == downloaded_count + 1
    assert saved['output_bytes'] == output.stat().st_size
    assert 'records may be duplicated or skipped' in caplog.text

    # Once saved, the new key resumes normally without the force flag.
    with patch.object(OGCServiceDumper, 'get_features', return_value=[]) as get:
        result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    get.assert_called_once()
    assert not state_file.exists()
    expected = ([feature(1)] if downloaded_count else []) + [feature(2)]
    assert [json.loads(line) for line in output.read_text().splitlines()] == expected


def test_sort_error_after_progress_explains_force_option(tmp_path, caplog):
    output, state_file, writer, state = setup_checkpoint(tmp_path)
    state.update(1, 0)  # Skipped offsets are progress even without output records.

    def fetch(dumper, count, no_index=False, no_sort=False):
        if not no_sort:
            raise InvalidSortKeyException()
        return [feature(1)]

    with caplog.at_level('INFO'), patch.object(OGCServiceDumper, 'get_features', fetch):
        result = CliRunner().invoke(main, ['extract', 'test', str(output), '-u', PARAMS['url'],
                                          '-s', 'WMS', '-f', 'GEORSS', '-k', 'id'])
    assert result.exit_code == 0, result.output
    assert 'or use --force-sort-key-change to continue' in caplog.text
    assert 'No extraction progress saved' not in caplog.text
    assert json.loads(state_file.read_text())['index_done_till'] == 1


@pytest.mark.parametrize('signum', [signal.SIGTERM, signal.SIGINT])
def test_cli_signal_during_batch_commits_and_restores_handlers(tmp_path, signum):
    output = tmp_path / 'out.geojsonl'
    previous = signal.getsignal(signum)
    original = FileWriter.write
    calls = []

    def fetch(dumper, count, *args):
        calls.append((count, dumper.state.index_done_till))
        return [feature(1), feature(2)]

    def write_and_signal(writer, feat):
        original(writer, feat)
        if feat['properties']['id'] == 1:
            os.kill(os.getpid(), signum)

    with patch.object(OGCServiceDumper, 'get_features', fetch), \
         patch.object(FileWriter, 'write', write_and_signal), patch('os.fsync') as sync:
        result = CliRunner().invoke(main, ['extract', 'test', str(output), '-u', PARAMS['url'],
                                          '-s', 'WMS', '-f', 'GEORSS', '-k', 'id', '-b', '2'])
    assert result.exit_code == 0, result.output
    assert signal.getsignal(signum) == previous
    assert calls == [(2, 0)]
    saved = json.loads(output.with_suffix('.geojsonl.state').read_text())
    assert saved['downloaded_count'] == saved['index_done_till'] == 2
    assert saved['output_bytes'] == output.stat().st_size
    assert output.read_text().count('\n') == 2
    assert sync.call_count >= 2


def test_stop_during_failed_request_does_not_retry():
    with GracefulStop() as stopping:
        dumper = OGCServiceDumper(PARAMS['url'], 'test', Service.WMS,
                                  stop_requested=lambda: stopping.requested)

        def fail(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGTERM)
            raise TimeoutError('timed out')

        with patch.object(dumper.session, 'get', side_effect=fail) as get:
            with pytest.raises(ExtractionStopped):
                dumper.make_request({})
            assert get.call_count == 1


def test_stop_before_request_and_during_wait():
    dumper = OGCServiceDumper(PARAMS['url'], 'test', Service.WMS, stop_requested=lambda: True)
    with patch.object(dumper.session, 'get') as get:
        with pytest.raises(ExtractionStopped):
            dumper.get_features(1000)
        get.assert_not_called()
    with pytest.raises(ExtractionStopped):
        dumper.wait(60)


def test_bad_byte_offset_refuses_recovery(tmp_path):
    output, state_file, writer, state = setup_checkpoint(tmp_path)
    writer.write(feature(1))
    writer.close()
    saved = json.loads(state_file.read_text())
    saved['output_bytes'] = 5
    state_file.write_text(json.dumps(saved))
    before = output.read_bytes()
    with pytest.raises(ValueError, match='line boundary'):
        recover_output(state_file, output)
    assert output.read_bytes() == before
