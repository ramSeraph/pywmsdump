import re
import time
import json
import logging
from pprint import pprint, pformat

import xmltodict
import requests

from bs4 import BeautifulSoup
from .state import State

logger = logging.getLogger(__name__)

SORT_KEY_ERR_MSG = 'Cannot do natural order without a primary key, please add it or specify a manual sort over existing attributes'
INVALID_PROP_NAME_ERR_MSG = 'Illegal property name'
WFS_DISABLED_ERR_MSG = 'Service WFS is disabled'

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

class WMSDumper:

    def __init__(self, url, layername, service,
                 wms_version='1.1.1',
                 geometry_precision=7,
                 wfs_version='1.0.0',
                 batch_size=1000,
                 outSRS='EPSG:4326',
                 sort_key=None,
                 state=None,
                 requests_to_pause=10,
                 pause_seconds=2,
                 max_attempts=5,
                 session=None,
                 req_params={}):
        # common
        self.url = url
        self.layername = layername
        self.service = service

        # wms specific
        self.wms_version = wms_version
        self.geometry_precision = geometry_precision

        # wfs specific
        self.wfs_version = wfs_version

        # common
        self.batch_size = batch_size
        self.sort_key = sort_key
        self.state = state
        self.outSRS = outSRS
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
            'version': self.wfs_version,
            'request': 'GetFeature',
            'typeName': self.layername,
            'outputFormat': "application/json",
            'srsName': self.outSRS,
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
            'version': self.wms_version,
            'request': 'GetMap',
            'layers': self.layername,
            'maxFeatures': count,
            'srs': self.outSRS,
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
        attempt = 0

        while True:
            attempt += 1
            if attempt > self.max_attempts:
                raise Exception('Request failed')

            try:
                resp = self.session.get(self.url, params=params, **self.req_params)
                if not resp.ok:
                    raise Exception('Request failed')
            except Exception:
                logger.info(f'request failed - attempt:{attempt}.. retrying in {self.pause_seconds*attempt}') 
                time.sleep(self.pause_seconds * attempt)
                continue

            return resp.text

    def get_geom(self, georss_polygon):
        points = []
        curr_p = []
        for c in georss_polygon.split(' '):
            if len(curr_p) == 2:
                curr_p.reverse()
                points.append(curr_p)
                curr_p = []
            curr_p.append(round(float(c), self.geometry_precision))
        if len(curr_p) == 2:
            curr_p.reverse()
            points.append(curr_p)
            curr_p = []

        return { 'type': 'Polygon', 'coordinates': [points] }

    def get_props(self, content):
        soup = BeautifulSoup(content, 'html.parser')
        lis = soup.find_all('li')
        props = {}
        for li in lis:
            txt = li.text.strip()
            parts = txt.split(':', 1)
            if len(parts) != 2:
                return { 'unparsed': content }
            props[parts[0]] = parts[1].strip()
        return props


    def extract_feature(self, entry):
        content = entry.get('content', {}).get('#text', '')
        props = self.get_props(content)
    
        geom = self.get_geom(entry['georss:where']['georss:polygon'])
    
        return { 'type': 'Feature', 'geometry': geom, 'properties': props }


    def parse_response_WFS(self, resp_text):
        try:
            data = json.loads(resp_text)
        except ValueError:
            handle_non_json_response(resp_text)
            logger.info(f'resp: {resp_text}')
            raise

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
            feat = self.extract_feature(entry)
            feats.append(feat)

        return feats


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
                logger.info(f'pausing for {self.pause_seconds}')
                time.sleep(self.pause_seconds)
                self.req_count = 0

            logger.info(f'making a request for {self.batch_size} records with '
                        f'start_index: {self.state.index_done_till}, '
                        f'already_dowmloaded: {self.state.downloaded_count}')

            feats = self.get_features(self.batch_size)
            for feat in feats:
                yield feat

            self.state.update(self.batch_size, len(feats))

            if len(feats) < self.batch_size:
                break
