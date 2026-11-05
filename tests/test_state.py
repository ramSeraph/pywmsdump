import json
from unittest.mock import Mock, patch

import pytest

from wmsdump.cli import FileWriter
from wmsdump.enums import Extent, Operation, RetrievalMode, Service, WFSVersion, WMSVersion
from wmsdump.state import State, get_state_from_files, load_state_from_json, validate_state_dict

PARAMS = dict(url='https://example.com/ows', layername='roads', service=Service.WMS,
              version=WMSVersion.V1_1_1, operation=Operation.GetMap)
EXTENT_MODES = [RetrievalMode.EXTENT, RetrievalMode.EXTENT_FIXED_BUFFER]


def feature(key):
    return {'type': 'Feature', 'geometry': None, 'properties': {'name': key}}


@pytest.mark.parametrize('mode', list(RetrievalMode))
@pytest.mark.parametrize('service,version', [
    (Service.WMS, WMSVersion.V1_1_1), (Service.WMS, WMSVersion.V1_3_0),
    *[(Service.WFS, version) for version in WFSVersion],
])
def test_state_json_round_trip(tmp_path, mode, service, version):
    state = State.from_dict(**dict(PARAMS, service=service, version=version), mode=mode, sort_key='id')
    saved = []
    state.updatecb = saved.append
    if mode == RetrievalMode.OFFSET:
        state.update(10, 2)
        state.update(10, 1)
        assert (state.index_done_till, state.downloaded_count) == (20, 3)
    else:
        key = '012' if mode == RetrievalMode.EXTENT else '0|12|3'
        state.update_coverage(key, Extent.OPEN)
        state.update_coverage(key, Extent.EXPLORED)
        assert saved[0]['explored_tree'][key] == Extent.OPEN.value
    data = dict(state.get_dict(), output_bytes=0)
    assert saved[-1] == state.get_dict()
    assert validate_state_dict(data) == (True, None)
    path = tmp_path / 'state.json'
    path.write_text(json.dumps(data))
    restored = State.from_dict(**load_state_from_json(path))
    assert type(restored) is type(state)
    assert restored.get_dict() == state.get_dict()
    if mode != RetrievalMode.OFFSET:
        assert restored.explored_tree[key] is Extent.EXPLORED


@pytest.mark.parametrize('field,value', [
    ('url', 'https://elsewhere.example/ows'), ('layername', 'other'),
    ('service', Service.WFS), ('version', WMSVersion.V1_3_0),
    ('operation', Operation.GetFeatureInfo), ('mode', RetrievalMode.EXTENT),
    ('sort_key', 'other'), ('downloaded_count', 2),
])
def test_invocation_mismatch(field, value):
    params = dict(PARAMS, mode=RetrievalMode.OFFSET, sort_key='id', downloaded_count=1)
    state = State.from_dict(**params)
    assert state.is_in_sync(**params) == (True, None)
    valid, reason = state.is_in_sync(**dict(params, **{field: value}))
    assert not valid
    assert reason


@pytest.mark.parametrize('field', [*PARAMS, 'mode', 'output_bytes', 'sort_key',
                                   'index_done_till', 'downloaded_count'])
def test_missing_checkpoint_fields(field):
    data = dict(State.from_dict(**PARAMS, mode=RetrievalMode.OFFSET).get_dict(), output_bytes=0)
    del data[field]
    valid, reason = validate_state_dict(data)
    assert not valid
    assert reason


@pytest.mark.parametrize('value', [None, 7, [], ['output_bytes']])
def test_checkpoint_must_be_object(value):
    valid, reason = validate_state_dict(value)
    assert not valid
    assert reason


@pytest.mark.parametrize('field,value', [
    ('service', 'prefixWMS'), ('operation', 'GetMapSuffix'), ('version', '9.9.9'),
    ('version', 'prefix1.1.1'), ('mode', 'unknown'), ('index_done_till', -1),
    ('downloaded_count', -1), ('output_bytes', True), ('output_bytes', -1),
])
def test_invalid_checkpoint_values(field, value):
    data = dict(State.from_dict(**PARAMS, mode=RetrievalMode.OFFSET).get_dict(), output_bytes=0)
    data[field] = value
    assert validate_state_dict(data)[0] is False


@pytest.mark.parametrize('mode', EXTENT_MODES)
@pytest.mark.parametrize('tree', [{'bad-key': 1}, {'0': 3}, {'0': -1}, {'0': True}, {'0': '1'}])
def test_invalid_extent_tree(mode, tree):
    data = dict(State.from_dict(**PARAMS, mode=mode).get_dict(), output_bytes=0, explored_tree=tree)
    assert validate_state_dict(data)[0] is False


@pytest.mark.parametrize('mode', EXTENT_MODES)
@pytest.mark.parametrize('collisions', [False, True])
def test_deduplication_preserves_distinct_records_and_indices(mode, collisions):
    state = State.from_dict(**PARAMS, mode=mode)
    stored = [json.dumps(feature('old')), json.dumps(feature('old'))]
    state.get_nth = stored.__getitem__
    # Rebuild from a file containing duplicates: physical line indices must survive.
    with patch('wmsdump.state.hash', return_value=1, create=True) if collisions else patch(
        'wmsdump.state.hash', wraps=hash, create=True
    ):
        for line in stored:
            state.add_raw_feature_no_dedup(line)
        assert not state.add_feature(feature('old'))
        assert state.add_feature(feature('new'))
        stored.append(json.dumps(feature('new')))
        assert not state.add_feature(feature('new'))
        assert not state.add_feature(feature('old'))
        assert state.current_count == len(stored) == 3


@pytest.mark.parametrize('mode', EXTENT_MODES)
def test_extent_resume_rebuilds_dedup_after_trimming_tail(tmp_path, mode):
    output, checkpoint = tmp_path / 'out.geojsonl', tmp_path / 'state.json'
    params = dict(PARAMS, mode=mode)  # Library callers need not supply an OFFSET sort key.
    writer = FileWriter(output, keep_idx=True)
    state = get_state_from_files(checkpoint, output, flush_output=writer.flush, **params)
    state.get_nth = writer.get
    for key in ['first', 'second']:
        item = feature(key)
        assert state.add_feature(item)
        writer.write(item)
    tree_key = '01' if mode == RetrievalMode.EXTENT else '0|1'
    state.update_coverage(tree_key, Extent.OPEN)
    committed = output.read_bytes()
    writer.write(feature('uncommitted'))
    writer.close()
    with output.open('ab') as stream:
        stream.write(b'{"partial":')
    reader = FileWriter(output, keep_idx=True)
    resumed = get_state_from_files(checkpoint, output, flush_output=reader.flush, **params)
    # The CLI constructs its indexed writer after recovery; do likewise here.
    reader = FileWriter(output, keep_idx=True)
    resumed.get_nth = reader.get
    assert output.read_bytes() == committed
    assert resumed.current_count == 2
    assert resumed.explored_tree == {tree_key: Extent.OPEN}
    assert not resumed.add_feature(feature('first'))
    assert not resumed.add_feature(feature('second'))
    assert resumed.add_feature(feature('uncommitted'))
    reader.write(feature('uncommitted'))
    reader.close()
    assert [json.loads(line) for line in output.read_text().splitlines()] == [
        feature('first'), feature('second'), feature('uncommitted')]


@pytest.mark.parametrize('contents', ['{', 'null', '["output_bytes"]', '{"output_bytes": 0}'])
def test_corrupt_state_does_not_modify_output(tmp_path, contents):
    output, checkpoint = tmp_path / 'out.geojsonl', tmp_path / 'state.json'
    output.write_text('committed\npartial')
    checkpoint.write_text(contents)
    assert get_state_from_files(checkpoint, output, flush_output=Mock(),
                                **PARAMS, mode=RetrievalMode.OFFSET) is None
    assert output.read_text() == 'committed\npartial'
    assert checkpoint.read_text() == contents


def test_missing_output_rejects_checkpoint(tmp_path):
    output, checkpoint = tmp_path / 'out.geojsonl', tmp_path / 'state.json'
    checkpoint.write_text('{}')
    assert get_state_from_files(checkpoint, output, flush_output=Mock(),
                                **PARAMS, mode=RetrievalMode.OFFSET) is None
    assert not output.exists()


def test_extent_instances_do_not_share_coverage():
    first = State.from_dict(**PARAMS, mode=RetrievalMode.EXTENT)
    second = State.from_dict(**PARAMS, mode=RetrievalMode.EXTENT)
    first.update_coverage('0', Extent.OPEN)
    assert second.explored_tree == {}
