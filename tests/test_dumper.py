import json
from pathlib import Path
from unittest.mock import Mock, call, patch

import pytest
import requests

from wmsdump.cli import FileWriter
from wmsdump.dumper import OGCServiceDumper
from wmsdump.enums import Extent, Flavor, Format, Operation, RetrievalMode, Service, WFSVersion
from wmsdump.errors import LayerMissingException, ZeroAreaException
from wmsdump.state import get_state_from_files
from wmsdump.termination import ExtractionStopped

BOUNDS = dict(xmin=0, ymin=0, xmax=8, ymax=8)
SAMPLES = Path(__file__).parent / 'samples'


def feature(key):
    return {'type': 'Feature', 'id': f'row.{key}', 'geometry': {
        'type': 'Point', 'coordinates': [1.23456, 2.34567]}, 'properties': {'key': key}}


def response(features):
    return Mock(ok=True, text=json.dumps({'type': 'FeatureCollection', 'features': features}))


def dumper(**kwargs):
    return OGCServiceDumper(**dict(dict(url='https://example.com/ows', layername='roads',
                                      service=Service.WFS, bounds=BOUNDS, batch_size=2,
                                      session=Mock(), pause_seconds=0), **kwargs))


@pytest.mark.parametrize('version', list(WFSVersion))
@pytest.mark.parametrize('bounded', [False, True])
def test_wfs_request_contract(version, bounded):
    obj = dumper(service_version=version, sort_key='id')
    obj.state.update(12, 9)
    params = obj.get_bounded_params(BOUNDS, 7) if bounded else obj.get_params(7, False, False)
    expected = dict(service='WFS', version=version.value, request='GetFeature', typeName='roads',
                    outputFormat='application/json', srsName='EPSG:4326')
    expected['count' if version == WFSVersion.V2_0_0 else 'maxFeatures'] = 7
    expected.update({'bbox': '0, 0, 8, 8, EPSG:4326'} if bounded else {'startIndex': 12, 'sortBy': 'id'})
    assert params == expected
    assert 'startIndex' not in obj.get_params(7, True, True)
    assert 'sortBy' not in obj.get_params(7, True, True)


@pytest.mark.parametrize('fmt,mime', [(Format.KML, 'application/vnd.google-earth.kml+xml'),
                                    (Format.GEORSS, 'application/atom xml')])
@pytest.mark.parametrize('bounded', [False, True])
def test_getmap_request_contract(fmt, mime, bounded):
    obj = dumper(service=Service.WMS, getmap_format=fmt, sort_key='id')
    obj.state.update(6, 5)
    expected = dict(service='WMS', version='1.1.1', request='GetMap', layers='roads', maxFeatures=7,
                    srs='EPSG:4326', format=mime, width=256, height=256, styles='', bbox='0, 0, 8, 8')
    if not bounded:
        expected.update(startIndex=6, sortBy='id')
    params = obj.get_bounded_params(BOUNDS, 7) if bounded else obj.get_params(7, False, False)
    assert params == expected
    assert 'startIndex' not in obj.get_params(7, True, True)
    assert 'sortBy' not in obj.get_params(7, True, True)


@pytest.mark.parametrize('flavor', list(Flavor))
def test_getfeatureinfo_request_contract(flavor):
    obj = dumper(service=Service.WMS, operation=Operation.GetFeatureInfo, flavor=flavor,
                 retrieval_mode=RetrievalMode.EXTENT, get_nth=Mock(), wms_map_size=100)
    params = obj.get_bounded_params(BOUNDS, 7)
    expected = dict(service='WMS', version='1.1.1', request='GetFeatureInfo', layers='roads',
                    query_layers='roads', feature_count=7, srs='EPSG:4326', info_format='application/json',
                    with_geometry='true', width=100, height=100, styles='', bbox='0, 0, 8, 8',
                    x=50, y=50, i=50, j=50)
    if flavor == Flavor.Geoserver:
        expected['buffer'] = 71
    else:
        expected.update(FI_LINE_TOLERANCE=71, FI_POINT_TOLERANCE=71, FI_POLYGON_TOLERANCE=71)
    assert params == expected


@pytest.mark.parametrize('fmt,stem', [(Format.KML, 'kml_points'), (Format.GEORSS, 'georss_point')])
@pytest.mark.parametrize('bounded', [False, True])
def test_getmap_download_parses_real_response(fmt, stem, bounded):
    obj = dumper(service=Service.WMS, getmap_format=fmt)
    obj.session.get.return_value = Mock(ok=True, text=(SAMPLES / f'{stem}.xml').read_text())
    actual = obj.get_bounded_features(BOUNDS, 10, '0') if bounded else obj.get_features(10)
    expected = [json.loads(line) for line in (SAMPLES / f'{stem}.geojsonl').read_text().splitlines() if line]
    assert actual == expected


@pytest.mark.parametrize('pages,expected_offsets', [([], [0]), ([2], [0, 2]), ([2, 1], [0, 2])])
def test_offset_download_through_http_boundary(pages, expected_offsets):
    obj = dumper(geometry_precision=2, drop_id=True, sort_key='key', req_params={'timeout': 3})
    responses, expected = [], []
    for size in pages:
        items = [feature(len(expected) + i) for i in range(size)]
        expected.extend(items)
        responses.append(response(items))
    responses.append(response([]))
    obj.session.get.side_effect = responses
    actual = list(obj)
    for item in expected:
        item.pop('id')
        item['geometry']['coordinates'] = [1.23, 2.35]
    assert actual == expected
    assert [c.kwargs['params']['startIndex'] for c in obj.session.get.call_args_list] == expected_offsets
    assert all(c.kwargs['timeout'] == 3 for c in obj.session.get.call_args_list)
    assert obj.state.downloaded_count == len(expected)
    assert obj.state.index_done_till == 2 * len(expected_offsets)


def test_offset_resume_and_checkpoint_only_after_consuming_batch():
    obj = dumper()
    obj.state.update(8, 3)
    obj.session.get.side_effect = [response([feature(1), feature(2)]), response([])]
    stream = iter(obj)
    assert next(stream) == feature(1)
    assert (obj.state.index_done_till, obj.state.downloaded_count) == (8, 3)
    assert next(stream) == feature(2)
    assert (obj.state.index_done_till, obj.state.downloaded_count) == (8, 3)
    assert list(stream) == []
    assert (obj.state.index_done_till, obj.state.downloaded_count) == (12, 5)
    assert [c.kwargs['params']['startIndex'] for c in obj.session.get.call_args_list] == [8, 10]


@pytest.mark.parametrize('failure', [requests.Timeout('timeout'), Mock(ok=False, status_code=503, text='busy')])
def test_request_retry_backoff_and_success(failure):
    obj = dumper(max_attempts=3, retry_delay=2)
    obj.session.get.side_effect = [failure, failure, response([])]
    with patch.object(obj, 'wait') as wait:
        assert json.loads(obj.make_request({'request': 'GetFeature'}))['features'] == []
    assert wait.call_args_list == [call(2), call(4)]
    assert obj.session.get.call_count == 3


def test_retry_exhaustion_does_not_advance_state():
    obj = dumper(max_attempts=2, retry_delay=3)
    obj.session.get.side_effect = requests.Timeout('timeout')
    with patch.object(obj, 'wait') as wait, pytest.raises(requests.Timeout):
        list(obj)
    wait.assert_called_once_with(3)
    assert obj.session.get.call_count == 2
    assert obj.state.index_done_till == obj.state.downloaded_count == 0


def test_xml_service_error_is_not_retried_or_checkpointed():
    obj = dumper()
    obj.session.get.return_value = Mock(ok=True, text=(SAMPLES / 'wfs_unknown_layer.xml').read_text())
    with pytest.raises(LayerMissingException):
        list(obj)
    assert obj.session.get.call_count == 1
    assert obj.state.index_done_till == obj.state.downloaded_count == 0


@pytest.mark.parametrize('mode', list(RetrievalMode))
def test_request_rate_limiting_and_stop(mode):
    options = {} if mode == RetrievalMode.OFFSET else dict(
        service=Service.WMS, operation=Operation.GetFeatureInfo, get_nth=Mock(), fixed_buffer=128)
    obj = dumper(retrieval_mode=mode, requests_to_pause=2, pause_seconds=7, **options)
    obj.session.get.return_value = response([])

    def fetch():
        if mode == RetrievalMode.OFFSET:
            return obj.get_features(2)
        if mode == RetrievalMode.EXTENT:
            return obj.get_bounded_features(BOUNDS, 2, '0')
        return obj.get_cell_features(BOUNDS, 0, 2, 2, 2, '0|0')

    with patch.object(obj, 'wait') as wait:
        for _ in range(5):
            assert fetch() == []
        assert wait.call_args_list == [call(7), call(7)]
    obj.stop_requested = lambda: True
    with pytest.raises(ExtractionStopped):
        fetch()
    assert obj.session.get.call_count == 5


def consume(obj):
    stored = []
    obj.state.get_nth = lambda n: json.dumps(stored[n])
    for item in obj:
        stored.append(item)
    return stored


def test_extent_subdivision_deduplicates_and_finishes_tree():
    obj = dumper(retrieval_mode=RetrievalMode.EXTENT, get_nth=Mock())
    obj.session.get.side_effect = [response(items) for items in [
        [feature(1), feature(2)], [feature(1)], [feature(3)], [], []]]
    assert consume(obj) == [feature(1), feature(2), feature(3)]
    assert obj.state.explored_tree == dict.fromkeys(['0', '00', '01', '02', '03'], Extent.EXPLORED)
    boxes = [c.kwargs['params']['bbox'] for c in obj.session.get.call_args_list]
    assert boxes == ['0, 0, 8, 8, EPSG:4326', '0, 0, 4.0, 4.0, EPSG:4326',
                     '4.0, 4.0, 8, 8, EPSG:4326', '4.0, 0, 8, 4.0, EPSG:4326',
                     '0, 4.0, 4.0, 8, EPSG:4326']
    assert list(obj) == []
    assert obj.session.get.call_count == 5


def test_extent_skips_oversized_and_already_explored_boxes():
    obj = dumper(retrieval_mode=RetrievalMode.EXTENT, get_nth=Mock(), max_box_dims={'deltax': 4, 'deltay': 4})
    obj.state.update_coverage('00', Extent.EXPLORED)
    obj.session.get.return_value = response([])
    assert list(obj) == []
    assert obj.session.get.call_count == 3
    assert obj.state.explored_tree['0'] is Extent.EXPLORED
    assert not obj.is_envelope_size_allowed(dict(xmin=0, xmax=4, ymin=0, ymax=8))


@pytest.mark.parametrize('fixed', [False, True])
def test_zero_area_is_marked_explored(fixed):
    mode = RetrievalMode.EXTENT_FIXED_BUFFER if fixed else RetrievalMode.EXTENT
    obj = dumper(service=Service.WMS, operation=Operation.GetFeatureInfo, retrieval_mode=mode,
                 get_nth=Mock(), fixed_buffer=128)
    method = 'get_cell_features' if fixed else 'get_bounded_features'
    with patch.object(obj, method, side_effect=ZeroAreaException):
        assert list(obj) == []
    assert all(status is Extent.EXPLORED for status in obj.state.explored_tree.values())


def test_fixed_buffer_only_subdivides_saturated_cell():
    obj = dumper(service=Service.WMS, operation=Operation.GetFeatureInfo,
                 retrieval_mode=RetrievalMode.EXTENT_FIXED_BUFFER, get_nth=Mock(), fixed_buffer=128)
    obj.session.get.side_effect = [response(items) for items in [
        [feature(1), feature(2)], [feature(1)], [feature(3)], [], [], [], [], []]]
    assert consume(obj) == [feature(1), feature(2), feature(3)]
    assert set(obj.state.explored_tree) == {'0|0', '0|0|0', '0|0|1', '0|0|2', '0|0|3', '0|1', '0|2', '0|3'}
    assert all(status is Extent.EXPLORED for status in obj.state.explored_tree.values())
    params = [c.kwargs['params'] for c in obj.session.get.call_args_list]
    assert len(params) == 8
    assert params[0]['bbox'] == params[-1]['bbox'] == '0, 0, 8, 8'
    assert len({p['bbox'] for p in params[1:5]}) == 1
    assert params[1]['bbox'] != params[0]['bbox']
    assert all('buffer' not in p for p in params)
    assert list(obj) == []
    assert obj.session.get.call_count == 8


@pytest.mark.parametrize('index,expected', [
    (0, dict(xmin=0, ymin=4, xmax=4, ymax=8)),
    (1, dict(xmin=4, ymin=4, xmax=8, ymax=8)),
    (2, dict(xmin=0, ymin=0, xmax=4, ymax=4)),
    (3, dict(xmin=4, ymin=0, xmax=8, ymax=4)),
])
def test_fixed_buffer_subdivision_matches_queried_pixel(index, expected):
    # WMS pixels start at the upper left; a saturated top cell must recurse north.
    # https://cite.ogc.org/teamengine/about/wms11/1.1.1/site/OGCTestData/wms/1.1.1/spec/wms1.1.1.html
    obj = dumper(service=Service.WMS, operation=Operation.GetFeatureInfo,
                 retrieval_mode=RetrievalMode.EXTENT_FIXED_BUFFER, get_nth=Mock(), fixed_buffer=128)
    assert obj.get_grid_dimensions() == (2, 2)
    assert obj.get_cell_envelope(BOUNDS, index, 2, 2) == expected
    params = obj.get_params_WMS_GetFeatureInfo_FixedBuffer(2, BOUNDS, index, 2, 2)
    x = 8 * params['x'] / 256
    y = 8 - 8 * params['y'] / 256
    assert expected['xmin'] < x < expected['xmax']
    assert expected['ymin'] < y < expected['ymax']
    assert params['i'] == params['x'] and params['j'] == params['y']


@pytest.mark.parametrize('mode', [RetrievalMode.EXTENT, RetrievalMode.EXTENT_FIXED_BUFFER])
def test_extent_interruption_resumes_without_duplicates(tmp_path, mode):
    output, checkpoint = tmp_path / 'out.geojsonl', tmp_path / 'state.json'
    options = dict(service=Service.WMS, operation=Operation.GetFeatureInfo, retrieval_mode=mode,
                   fixed_buffer=128, max_attempts=1)
    params = dict(url='https://example.com/ows', layername='roads', service=Service.WMS,
                  version=dumper(service=Service.WMS).service_version,
                  operation=Operation.GetFeatureInfo, mode=mode, sort_key=None)
    writer = FileWriter(output, keep_idx=True)
    state = get_state_from_files(checkpoint, output, flush_output=writer.flush, **params)
    state.updatecb(state.get_dict())
    obj = dumper(state=state, get_nth=writer.get, **options)
    obj.session.get.side_effect = [response([feature(1), feature(2)]), response([feature(1)]),
                                   requests.Timeout('interrupted')]
    with pytest.raises(requests.Timeout):
        for item in obj:
            writer.write(item)
    writer.close()
    reader = FileWriter(output, keep_idx=True)
    state = get_state_from_files(checkpoint, output, flush_output=reader.flush, **params)
    resumed = dumper(state=state, get_nth=reader.get, **options)
    # Previously explored child is skipped. The remaining children overlap old output.
    pages = [[feature(2)], [feature(3)], []] + ([[], [], []] if mode == RetrievalMode.EXTENT_FIXED_BUFFER else [])
    resumed.session.get.side_effect = [response(items) for items in pages]
    for item in resumed:
        reader.write(item)
    reader.close()
    assert [json.loads(line) for line in output.read_text().splitlines()] == [feature(1), feature(2), feature(3)]
    assert resumed.session.get.call_count == len(pages)


@pytest.mark.parametrize('mode', [RetrievalMode.EXTENT, RetrievalMode.EXTENT_FIXED_BUFFER])
def test_extent_modes_require_dedup_lookup(mode):
    with pytest.raises(Exception, match='get_nth'):
        dumper(service=Service.WMS, operation=Operation.GetFeatureInfo,
               retrieval_mode=mode, fixed_buffer=128)
