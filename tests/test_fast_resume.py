import builtins
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from wmsdump.cli import FileWriter, main
from wmsdump.dumper import OGCServiceDumper
from wmsdump.enums import Operation, RetrievalMode, Service, WMSVersion
from wmsdump.state import get_state_from_files, recover_output

PARAMS = dict(url='https://example.com/wms', layername='test', service=Service.WMS,
              version=WMSVersion.V1_1_1, operation=Operation.GetMap,
              mode=RetrievalMode.OFFSET, sort_key='id')


def checkpoint(tmp_path):
    output = tmp_path / 'out.geojsonl'
    state_file = Path(str(output) + '.state')
    writer = FileWriter(output, keep_idx=False)
    state = get_state_from_files(state_file, output, flush_output=writer.flush, **PARAMS)
    writer.write({'type': 'Feature', 'geometry': None, 'properties': {'id': 1}})
    # Skipped offsets need not equal the number of downloaded records.
    state.update(8, 1)
    writer.close()
    return output, state_file, writer


def no_output_scan(output):
    def guarded_open(name, mode='r', *args, **kwargs):
        if Path(name) == output and mode in ('r', 'rt'):
            raise AssertionError('Byte checkpoint recovery must not scan the output')
        return builtins.open(name, mode, *args, **kwargs)
    return patch('wmsdump.state.open', side_effect=guarded_open, create=True)


def test_byte_checkpoint_recovers_tail_without_counting(tmp_path):
    output, state_file, writer = checkpoint(tmp_path)
    committed = output.read_bytes()
    saved = state_file.read_bytes()
    with output.open('ab') as stream:
        stream.write(b'{"uncommitted": true}\n{"partial":')
    with no_output_scan(output):
        state = get_state_from_files(state_file, output, flush_output=writer.flush, **PARAMS)
    assert state.index_done_till == 8
    assert state.downloaded_count == 1
    assert output.read_bytes() == committed
    assert state_file.read_bytes() == saved


def test_explicit_verification_detects_count_mismatch(tmp_path):
    output, state_file, writer = checkpoint(tmp_path)
    saved = json.loads(state_file.read_text())
    saved['downloaded_count'] = 2
    state_file.write_text(json.dumps(saved))
    before = output.read_bytes()
    state = get_state_from_files(state_file, output, flush_output=writer.flush,
                                verify_record_count=True, **PARAMS)
    assert state is None
    assert output.read_bytes() == before
    assert json.loads(state_file.read_text()) == saved


def test_explicit_verification_counts_after_trimming_uncommitted_tail(tmp_path):
    output, state_file, writer = checkpoint(tmp_path)
    committed = output.read_bytes()
    with output.open('ab') as stream:
        stream.write(b'{"uncommitted": true}\n{"partial":')
    state = get_state_from_files(state_file, output, flush_output=writer.flush,
                                verify_record_count=True, **PARAMS)
    assert state.downloaded_count == 1
    assert output.read_bytes() == committed


@pytest.mark.parametrize('mode', list(RetrievalMode))
@pytest.mark.parametrize('verify', [False, True])
def test_old_format_is_rejected_before_output_access(tmp_path, caplog, mode, verify):
    output, state_file, writer = checkpoint(tmp_path)
    saved = json.loads(state_file.read_text())
    del saved['output_bytes']
    if mode != RetrievalMode.OFFSET:
        saved = {key: value for key, value in saved.items()
                 if key not in ('sort_key', 'index_done_till', 'downloaded_count')}
        saved['mode'] = mode.value
        saved['explored_tree'] = {'0': 1}
    state_file.write_text(json.dumps(saved))
    before = output.read_bytes()
    before_state = state_file.read_bytes()
    with patch('wmsdump.state.open', side_effect=AssertionError('Output must not be opened'), create=True):
        state = get_state_from_files(state_file, output, flush_output=writer.flush,
                                    verify_record_count=verify, **dict(PARAMS, mode=mode))
    assert state is None
    assert 'Unsupported checkpoint format: missing output_bytes' in caplog.text
    assert output.read_bytes() == before
    assert state_file.read_bytes() == before_state


def test_direct_recovery_rejects_missing_byte_offset_without_modifying_output(tmp_path):
    output, state_file, writer = checkpoint(tmp_path)
    saved = json.loads(state_file.read_text())
    del saved['output_bytes']
    state_file.write_text(json.dumps(saved))
    before = output.read_bytes()
    with patch('wmsdump.state.open', side_effect=AssertionError('Output must not be opened'), create=True):
        with pytest.raises(ValueError, match='Unsupported checkpoint format: missing output_bytes'):
            recover_output(state_file, output)
    assert output.read_bytes() == before


@pytest.mark.parametrize('bad_offset', [None, -1, True, '12', 1.5, 5])
def test_fast_resume_still_rejects_invalid_byte_checkpoints(tmp_path, bad_offset):
    output, state_file, writer = checkpoint(tmp_path)
    saved = json.loads(state_file.read_text())
    saved['output_bytes'] = bad_offset
    state_file.write_text(json.dumps(saved))
    before = output.read_bytes()
    with no_output_scan(output):
        assert get_state_from_files(state_file, output, flush_output=writer.flush, **PARAMS) is None
    assert output.read_bytes() == before


def test_cli_uses_fast_recovery_by_default(tmp_path):
    output, state_file, writer = checkpoint(tmp_path)
    with no_output_scan(output), patch.object(OGCServiceDumper, 'get_features', return_value=[]) as get:
        result = CliRunner().invoke(main, ['extract', 'test', str(output), '-u', PARAMS['url'],
                                          '-s', 'WMS', '-f', 'GEORSS', '-k', 'id'])
    assert result.exit_code == 0, result.output
    get.assert_called_once()
    assert not state_file.exists()


def test_cli_verification_rejects_mismatched_count_before_request(tmp_path, caplog):
    output, state_file, writer = checkpoint(tmp_path)
    saved = json.loads(state_file.read_text())
    saved['downloaded_count'] = 2
    state_file.write_text(json.dumps(saved))
    with patch.object(OGCServiceDumper, 'get_features') as get:
        result = CliRunner().invoke(main, ['extract', 'test', str(output), '-u', PARAMS['url'],
                                          '-s', 'WMS', '-f', 'GEORSS', '-k', 'id',
                                          '--verify-record-count'])
    get.assert_not_called()
    assert 'doesn\'t match existing count' in caplog.text
    assert not result.exception
    assert json.loads(state_file.read_text()) == saved
