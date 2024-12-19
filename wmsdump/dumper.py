import re
import time
import json
import logging
from pprint import pformat

import xmltodict
import requests

from .state import State
from .georss_helper import extract_feature

logger = logging.getLogger(__name__)

SORT_KEY_ERR_MSG = 'Cannot do natural order without a primary key, ' + \
                   'please add it or specify a manual sort over existing attributes'
INVALID_PROP_NAME_ERR_MSG = 'Illegal property name'
WFS_DISABLED_ERR_MSG = 'Service WFS is disabled'

DEFAULTS = {
    'wms_version': '1.1.1',
    'wfs_version': '1.0.0',
    'batch_size': 1000,
    'requests_to_pause': 10,
    'pause_seconds': 2,
    'max_attempts': 5,
    'retry_delay': 5,
    'geometry_precision': 7,
    'out_srs': 'EPSG:4326',
}

class SortKeyRequiredException(Exception):
    pass

class InvalidSortKeyException(Exception):
    pass

class WFSUnsupportedException(Exception):
    pass

def get_error_msg(data):
    if 'ServiceExceptionReport' in data and 'ServiceException' in data['ServiceExceptionReport']:
        val = data['ServiceExceptionReport']['ServiceException']
        if isinstance(val, str):
            return val
        if isinstance(val, dict) and '#text' in val:
            return val['#text']

    if 'ows:ExceptionReport' in data and 'ows:Exception' in data['ows:ExceptionReport']:
        val = data['ows:ExceptionReport']['ows:Exception']
        if isinstance(val, str):
            return val
        if isinstance(val, dict) and 'ows:ExceptionText' in val:
            return val['ows:ExceptionText']
    return None

def handle_non_json_response(resp_text):
    try:
        data = xmltodict.parse(resp_text)
    except Exception:
        return None, None
    err_msg = get_error_msg(data)
    if err_msg is None:
        return data, None
    if err_msg.find(SORT_KEY_ERR_MSG) != -1:
        raise SortKeyRequiredException()
    if err_msg.find(INVALID_PROP_NAME_ERR_MSG) != -1:
        raise InvalidSortKeyException()
    if err_msg.find(WFS_DISABLED_ERR_MSG) != -1:
        raise WFSUnsupportedException()

    return data, err_msg

class ServiceDumper:

    def __init__(self, url, layername, service,
                 service_version=None,
                 batch_size=DEFAULTS['batch_size'],
                 out_srs=DEFAULTS['out_srs'],
                 sort_key=None,
                 state=None,
                 requests_to_pause=DEFAULTS['requests_to_pause'],
                 pause_seconds=DEFAULTS['pause_seconds'],
                 retry_delay=DEFAULTS['retry_delay'],
                 max_attempts=DEFAULTS['max_attempts'],
                 geometry_precision=DEFAULTS['geometry_precision'],
                 session=None,
                 req_params={}):

        if service not in ['WMS', 'WFS']:
            raise Exception('Service expected to be one of WMS or WFS')
        self.service = service

        if service_version is None:
            service_version = DEFAULTS['wms_version'] if service == 'WMS' else DEFAULTS['wfs_version']
        self.service_version = service_version

        self.url = url
        self.layername = layername
        self.geometry_precision = geometry_precision
        self.batch_size = batch_size
        self.sort_key = sort_key
        self.state = state
        self.out_srs = out_srs
        self.requests_to_pause = requests_to_pause
        self.pause_seconds = pause_seconds
        self.max_attempts = max_attempts
        self.req_params = req_params
        self.state = state
        if self.state is None:
            self.state = State(url=url, service=service, sort_key=self.sort_key)
        self.session = session
        if self.session is None:
            self.session = requests.session()

        self.req_count = 0

    def get_params_WFS(self, count, no_index, no_sort):

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

        fc_key = 'count' if self.wfs_version == '2.0.0' else 'maxFeatures'
        params[fc_key] = count

        if not no_sort and self.sort_key is not None:
            params['sortBy'] = self.sort_key

        return params

    def get_params_WMS(self, count, no_index, no_sort):
        bbox_str =  '-180,-90,180,90'
        params = {
            'service': 'WMS',
            'version': self.service_version,
            'request': 'GetMap',
            'layers': self.layername,
            'maxFeatures': count,
            'srs': self.out_srs,
            'format': 'application/atom xml',
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


    def get_params(self, count, no_index, no_sort):
        if self.service == 'WFS':
            return self.get_params_WFS(count, no_index, no_sort)

        return self.get_params_WMS(count, no_index, no_sort)


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
                logger.info(f'request failed - attempt:{attempt}/{self.max_attempts}.. retrying in {self.retry_delay*attempt} secs') 
                if attempt >= self.max_attempts:
                    raise
                time.sleep(self.retry_delay * attempt)
                continue

            return resp.text

    def parse_response_WFS(self, resp_text):
        try:
            data = json.loads(resp_text)
        except ValueError:
            handle_non_json_response(resp_text)
            logger.info(f'resp: {resp_text}')
            raise

        for feat in data['features']:
            self.truncate_geometry(feat.get('geometry', None))
        return data['features']

    def parse_response_WMS(self, resp_text):
        xml = re.sub(r'&#([a-zA-Z0-9]+);?', r'[#\1;]', resp_text)

        data, err_msg = handle_non_json_response(xml)

        if data is None:
            logger.error(xml)
            raise Exception('Unable to parse server response as xml')

        if err_msg is not None:
            logger.error(pformat(err_msg))
            raise Exception('got error when requesting features')

        if 'feed' not in data:
            logger.error(pformat(data))
            raise Exception('no feed in data')

        feed = data['feed']
        if 'entry' not in feed:
            return []
        entries = feed['entry']

        feats = []
        for entry in entries:
            feat = extract_feature(entry)
            self.truncate_geometry(feat.get('geometry', None))
            feats.append(feat)

        return feats

    def truncate_nested_coordinates(self, coords):
        if type(coords) is not list:
            return round(float(coords), self.geometry_precision)

        return [ self.truncate_nested_coordinates(c) for c in coords ]

    def truncate_geometry(self, geom):
        if geom['type'] == 'MultiGeometry':
            for subgeom in geom['geometries']:
                self.truncate_geometry(subgeom)

        geom['coordinates'] = self.truncate_nested_coordinates(geom['coordinates'])

    def parse_response(self, resp_text):
        if self.service == 'WFS':
            return self.parse_response_WFS(resp_text)

        return self.parse_response_WMS(resp_text)

    def get_features(self, count, no_index=False, no_sort=False):
        params = self.get_params(count, no_index, no_sort)

        resp_text = self.make_request(params)

        return self.parse_response(resp_text)


    def __iter__(self):
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
