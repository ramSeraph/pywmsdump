"""Issue #17: file indices must remain aligned after reopening and appending."""

import json
from unittest.mock import Mock, patch

import pytest
import requests
from click.testing import CliRunner

from wmsdump.cli import FileWriter, main
from wmsdump.state import ExtentState


def feature(key):
    return {'type': 'Feature', 'id': f'row.{key}', 'properties': {},
            'geometry': {'type': 'Point', 'coordinates': [key, 0]}}


@pytest.mark.parametrize('existing_count', [0, 1, 3])
def test_reopened_writer_deduplicates_multiple_appended_records(tmp_path, existing_count):
    output = tmp_path / 'out.geojsonl'
    writer = FileWriter(output, keep_idx=True)
    for key in range(existing_count):
        writer.write(feature(key))
    writer.close()
    output.touch(exist_ok=True)

    writer = FileWriter(output, keep_idx=True)
    state = ExtentState()
    for line in output.read_text().splitlines():
        state.add_raw_feature_no_dedup(line)
    state.get_nth = writer.get
    accepted = []
    # Before 77c837d, EOF occupied an index and the second new record was duplicated.
    for key in [existing_count, existing_count + 1, existing_count, existing_count + 1]:
        accepted.append(state.add_feature(feature(key)))
        if accepted[-1]:
            writer.write(feature(key))
    assert accepted == [True, True, False, False]
    assert writer.count == state.current_count == existing_count + 2
    for index in range(writer.count):
        assert json.loads(writer.get(index)) == feature(index)
    assert writer.get(writer.count) is None
    writer.close()
    assert [json.loads(line) for line in output.read_text().splitlines()] == [
        feature(key) for key in range(existing_count + 2)]


def server_response(keys, response_format):
    if response_format == 'JSON':
        text = json.dumps({'type': 'FeatureCollection', 'features': [feature(key) for key in keys]})
    elif response_format == 'GEORSS':
        entries = ''.join(f'<entry><title>row.{key}</title><georss:where>'
                          f'<georss:point>0 {key}</georss:point></georss:where></entry>' for key in keys)
        text = ('<feed xmlns="http://www.w3.org/2005/Atom" '
                f'xmlns:georss="http://www.georss.org/georss"><title>roads</title>{entries}</feed>')
    else:
        entries = ''.join(f'<Placemark id="row.{key}"><Point><coordinates>{key},0</coordinates>'
                          '</Point></Placemark>' for key in keys)
        text = f'<kml xmlns="http://www.opengis.net/kml/2.2"><Document>{entries}</Document></kml>'
    return Mock(ok=True, text=text)


@pytest.mark.parametrize('operation,response_format', [
    ('GetFeatureInfo', 'JSON'), ('GetMap', 'GEORSS'), ('GetMap', 'KML'),
])
@pytest.mark.parametrize('crash_tail', [False, True])
def test_cli_extent_resume_rejects_old_and_new_duplicates(tmp_path, operation, response_format, crash_tail):
    output = tmp_path / 'out.geojsonl'
    checkpoint = tmp_path / 'out.geojsonl.state'
    args = ['extract', 'roads', str(output), '-u', 'https://example.com/wms', '-s', 'WMS',
            '-o', operation, '-m', 'EXTENT', '-b', '2', '--max-attempts', '1', '-p', '0',
            '--bounds', '0,0,8,8']
    if operation == 'GetMap':
        args += ['-f', response_format]

    first = Mock()
    first.get.side_effect = [server_response([1, 2], response_format),
                             server_response([1], response_format), requests.Timeout('interrupted')]
    with patch('wmsdump.dumper.requests.session', return_value=first):
        result = CliRunner().invoke(main, args)
    assert isinstance(result.exception, requests.Timeout)
    saved = json.loads(checkpoint.read_text())
    assert saved['explored_tree'] == {'0': 1, '00': 2}
    assert saved['output_bytes'] == output.stat().st_size

    if crash_tail:
        with output.open('a') as stream:
            stream.write(json.dumps(feature(999)) + '\n{"partial":')

    resumed = Mock()
    # Resume at 01, append two records, then revisit each in its child extents.
    resumed.get.side_effect = [server_response(keys, response_format) for keys in
                               [[3, 4], [3], [4], [1], [], [2], []]]
    with patch('wmsdump.dumper.requests.session', return_value=resumed):
        result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert [item['geometry']['coordinates'][0] for item in records] == [1, 2, 3, 4]
    assert not checkpoint.exists()
    assert resumed.get.call_count == 7
    # Root and explored child 00 must not be downloaded again.
    assert resumed.get.call_args_list[0].kwargs['params']['bbox'] == '4.0, 4.0, 8.0, 8.0'
