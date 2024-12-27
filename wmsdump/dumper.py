import io
import math
import time
import json
import logging

from pprint import pformat

import requests
import kml2geojson

from .state import State, Extent
from .georss_helper import extract_features, get_props
from .errors import (
    handle_error_xml, KnownException, ZeroAreaException,
    optionally_save_to_file
)

logger = logging.getLogger(__name__)

GLOBAL_BOUNDS = { 'xmin': -180, 'ymin': -90, 'xmax': 180, 'ymax': 90 }

DEFAULTS = {
    'wms_version': '1.1.1',
    'wfs_version': '1.0.0',
    'batch_size': 1000,
    'requests_to_pause': 10,
    'pause_seconds': 2,
    'max_attempts': 5,
    'retry_delay': 5,
    'geometry_precision': -1,
    'out_srs': 'EPSG:4326',
    'getmap_format': 'GEORSS',
    'bounds': GLOBAL_BOUNDS
}

def truncate_nested_coordinates(coords, precision):
    if type(coords) is not list:
        return round(float(coords), precision)

    return [ truncate_nested_coordinates(c, precision) for c in coords ]

def truncate_geometry(geom, precision):
    if geom is None or precision == -1:
        return 
    if geom['type'] == 'GeometryCollection':
        for subgeom in geom['geometries']:
            truncate_geometry(subgeom, precision)
    else:
        geom['coordinates'] = truncate_nested_coordinates(geom['coordinates'], precision)

def bbox_to_str(bounds, crs):
    b = bounds

    b_str = f'{b["xmin"]}, {b["ymin"]}, {b["xmax"]}, {b["ymax"]}'
    if crs is not None:
        b_str += f', {crs}'

    return b_str

def convert_kml_props(feat):
    if 'properties' not in feat:
        return 

    props = feat['properties']
    if 'description' not in props:
        return

    new_props = get_props(props['description'])

    feat['properties'] = new_props

class OGCServiceDumper:

    def __init__(self, url, layername, service,
                 service_version=None,
                 retrieval_mode='OFFSET',
                 operation='GetMap',
                 batch_size=DEFAULTS['batch_size'],
                 out_srs=DEFAULTS['out_srs'],
                 sort_key=None,
                 state=None,
                 requests_to_pause=DEFAULTS['requests_to_pause'],
                 pause_seconds=DEFAULTS['pause_seconds'],
                 retry_delay=DEFAULTS['retry_delay'],
                 max_attempts=DEFAULTS['max_attempts'],
                 geometry_precision=DEFAULTS['geometry_precision'],
                 getmap_format=DEFAULTS['getmap_format'],
                 bounds=DEFAULTS['bounds'],
                 session=None,
                 get_nth=None,
                 req_params={}):

        if service not in ['WMS', 'WFS']:
            raise Exception('Service expected to be one of WMS or WFS')
        self.service = service

        if service_version is None:
            service_version = DEFAULTS['wms_version'] if service == 'WMS' else DEFAULTS['wfs_version']
        self.service_version = service_version

        if self.service == 'WFS':
            operation = 'GetFeature'
        self.operation = operation

        if retrieval_mode not in ['OFFSET', 'EXTENT']:
            raise Exception('retrieval mode should be one of OFFSET or EXTENSION')

        if self.operation == 'GetFeatureInfo' and retrieval_mode != 'EXTENT':
            logger.info('Overriding retrieval mode to EXTENT for GetFeatureInfo call')
            retrieval_mode == 'EXTENT'


        self.retrieval_mode = retrieval_mode

        self.url = url
        self.layername = layername
        self.geometry_precision = geometry_precision
        self.getmap_format = getmap_format
        self.initial_bounds = bounds
        self.batch_size = batch_size
        self.sort_key = sort_key
        self.state = state
        self.out_srs = out_srs
        self.requests_to_pause = requests_to_pause
        self.pause_seconds = pause_seconds
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self.req_params = req_params
        self.state = state
        if self.state is None:
            self.state = State.from_dict(url=self.url,
                                         layername=self.layername,
                                         service=self.service,
                                         version=self.service_version,
                                         operation=self.operation, 
                                         mode=self.retrieval_mode,
                                         sort_key=self.sort_key)

        if retrieval_mode == 'EXTENT' and get_nth is None:
            raise Exception('get_nth func needs to be passed to help with deduplication if retrieval mode is EXTENT')

        self.state.get_nth = get_nth

        self.session = session
        if self.session is None:
            self.session = requests.session()

        self.req_count = 0

    def get_params_WFS(self, count, bounds, no_index, no_sort):

        params = {
            'service': 'WFS',
            'version': self.service_version,
            'request': 'GetFeature',
            'typeName': self.layername,
            'outputFormat': "application/json",
            'srsName': self.out_srs,
        }
        if not no_index:
            params['startIndex'] = self.state.index_done_till

        fc_key = 'count' if self.service_version == '2.0.0' else 'maxFeatures'
        params[fc_key] = count

        if not no_sort and self.sort_key is not None:
            params['sortBy'] = self.sort_key

        if bounds is not None:
            params['bbox'] = bbox_to_str(bounds, self.out_srs)

        return params

    def get_params_WMS_GetMap(self, count, bounds, no_index, no_sort):
        bbox_str =  bbox_to_str(bounds, None)
        fmt = 'application/atom xml' if self.getmap_format == 'GEORSS' else 'application/vnd.google-earth.kml+xml'
        params = {
            'service': 'WMS',
            'version': self.service_version,
            'request': 'GetMap',
            'layers': self.layername,
            'maxFeatures': count,
            'srs': self.out_srs,
            'format': fmt,
            'width': 256,
            'height': 256,
            'styles': '',
            'bbox': bbox_str
        }
        if not no_index:
            params['startIndex'] = self.state.index_done_till
        if not no_sort and self.sort_key is not None:
            params['sortBy'] = self.sort_key

        return params

    def get_params_WMS_GetFeatureInfo(self, count, bounds):
        bbox_str = bbox_to_str(bounds, None)

        WIDTH = 256

        x = WIDTH / 2
        y = WIDTH / 2
        r = math.ceil(WIDTH / ( 2.0 ** 0.5 ))

        params = {
            'service': 'WMS',
            'version': self.service_version,
            'request': 'GetFeatureInfo',
            'layers': self.layername,
            'query_layers': self.layername,
            'feature_count': count,
            'srs': self.out_srs,
            'info_format': 'application/json',
            'width': WIDTH,
            'height': WIDTH,
            'styles': '',
            'bbox': bbox_str,
            'x': x,
            'y': y,
            'buffer': r,
        }

        return params



    def get_params(self, count, no_index, no_sort):
        if self.service == 'WFS':
            return self.get_params_WFS(count, None, no_index, no_sort)

        return self.get_params_WMS_GetMap(count, self.initial_bounds, no_index, no_sort)


    def get_bounded_params(self, bounds, count):
        if self.operation == 'GetFeature':
            return self.get_params_WFS(count, bounds, True, True)

        if self.operation == 'GetMap':
            return self.get_params_WMS_GetMap(count, bounds, True, True)

        if self.operation == 'GetFeatureInfo':
            return self.get_params_WMS_GetFeatureInfo(count, bounds)

        raise Exception(f'Unexpected operation: {self.operation}')

    def make_request(self, params):
        logger.debug(pformat(params))
        attempt = 0

        while True:
            attempt += 1

            try:
                resp = self.session.get(self.url, params=params, **self.req_params)
                if not resp.ok:
                    raise Exception(f'Request failed - status: {resp.status_code}, text: {resp.text}')
            except Exception:
                logger.info(f'request failed - attempt:{attempt}/{self.max_attempts}.. '
                            f'retrying in {self.retry_delay*attempt} secs') 
                if attempt >= self.max_attempts:
                    raise
                time.sleep(self.retry_delay * attempt)
                continue

            return resp.text

    def parse_response_geojson(self, resp_text):
        try:
            data = json.loads(resp_text)
        except ValueError:
            handle_error_xml(resp_text)
            logger.info(f'resp: {resp_text}')
            raise

        for feat in data['features']:
            truncate_geometry(feat.get('geometry', None),
                              self.geometry_precision)
        return data['features']

    def parse_response_georss(self, resp_text):
        try:
            feats = extract_features(resp_text)
        except KnownException:
            raise
        except Exception:
            logger.error('Unable to process response')
            logger.error(f'response: {resp_text}')
            raise

        for feat in feats:
            truncate_geometry(feat.get('geometry', None),
                              self.geometry_precision)

        return feats

    def parse_response_kml(self, resp_text):
        handle_error_xml(resp_text)
        fh = io.StringIO(resp_text)
        feature_collections = kml2geojson.main.convert(fh)
        data = feature_collections[0]
        for feat in data['features']:
            truncate_geometry(feat.get('geometry', None),
                              self.geometry_precision)
            convert_kml_props(feat)
        return data['features']

    def parse_response_getmap(self, resp_text):
        if self.getmap_format == 'GEORSS':
            return self.parse_response_georss(resp_text)
        return self.parse_response_kml(resp_text)

    def parse_response(self, resp_text):
        optionally_save_to_file(resp_text)
        if self.service == 'WFS':
            return self.parse_response_geojson(resp_text)

        return self.parse_response_getmap(resp_text)

    def parse_bounded_response(self, resp_text):
        if self.operation == 'GetMap':
            return self.parse_response_getmap(resp_text)

        return self.parse_response_geojson(resp_text)

    def get_features(self, count, no_index=False, no_sort=False):
        params = self.get_params(count, no_index, no_sort)

        resp_text = self.make_request(params)

        return self.parse_response(resp_text)

    def get_bounded_features(self, bounds, count):
        params = self.get_bounded_params(bounds, count)

        resp_text = self.make_request(params)

        return self.parse_bounded_response(resp_text)

    def split_envelope(self, envelope):
        half_width = (envelope['xmax'] - envelope['xmin']) / 2.0
        half_height = (envelope['ymax'] - envelope['ymin']) / 2.0
        return [
            dict(
                xmin=envelope['xmin'],
                ymin=envelope['ymin'],
                xmax=envelope['xmin'] + half_width,
                ymax=envelope['ymin'] + half_height,
            ),
            dict(
                xmin=envelope['xmin'] + half_width,
                ymin=envelope['ymin'] + half_height,
                xmax=envelope['xmax'],
                ymax=envelope['ymax'],
            ),
            dict(
                xmin=envelope['xmin'] + half_width,
                ymin=envelope['ymin'],
                xmax=envelope['xmax'],
                ymax=envelope['ymin'] + half_height,
            ),
            dict(
                xmin=envelope['xmin'],
                ymin=envelope['ymin'] + half_height,
                xmax=envelope['xmin'] + half_width,
                ymax=envelope['ymax'],
            ),
        ]


    def scrape_an_envelope(self, envelope, key):
        status = self.state.explored_tree.get(key, Extent.NOT_PRESENT.value)
        status = Extent(status)
        if status == Extent.EXPLORED:
            return

        max_records = self.batch_size
        if status == Extent.NOT_PRESENT:
            self.req_count += 1

            if self.req_count == self.requests_to_pause:
                logger.info(f'pausing for {self.pause_seconds} secs')
                time.sleep(self.pause_seconds)
                self.req_count = 0

            logger.info(f'making a request for {self.batch_size} records with key={key} ')
            try:
                features = self.get_bounded_features(envelope, max_records)
            except ZeroAreaException:
                features = []
            logger.info(f'got {len(features)} records')

            for feature in features:
                yield feature
            num_features = len(features)
            if num_features < max_records:
                self.state.update_coverage(key, Extent.EXPLORED)
                return
            self.state.update_coverage(key, Extent.OPEN)
            status = Extent.OPEN

        if status == Extent.OPEN:
            envelopes = self.split_envelope(envelope)

            for i, child_envelope in enumerate(envelopes):
                new_key = f'{key}{i}'
                for feature in self.scrape_an_envelope(child_envelope, new_key):
                    yield feature

            self.state.update_coverage(key, Extent.EXPLORED)


    def __iter__(self):
        if self.retrieval_mode == 'OFFSET':
            while True:
                self.req_count += 1

                if self.req_count == self.requests_to_pause:
                    logger.info(f'pausing for {self.pause_seconds} secs')
                    time.sleep(self.pause_seconds)
                    self.req_count = 0

                logger.info(f'making a request for {self.batch_size} records with '
                            f'start_index: {self.state.index_done_till}, '
                            f'already_downloaded: {self.state.downloaded_count}')

                feats = self.get_features(self.batch_size)
                for feat in feats:
                    yield feat

                self.state.update(self.batch_size, len(feats))

                if len(feats) < self.batch_size:
                    break
        else:
            for feature in self.scrape_an_envelope(self.initial_bounds, "0"):
                if self.state.add_feature(feature):
                    yield feature
