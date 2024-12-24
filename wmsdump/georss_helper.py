import re

import xmltodict

from bs4 import BeautifulSoup

SORT_KEY_ERR_MSG = 'Cannot do natural order without a primary key, ' + \
                   'please add it or specify a manual sort over existing attributes'
INVALID_PROP_NAME_ERR_MSG = 'Illegal property name'
WFS_DISABLED_ERR_MSG = 'Service WFS is disabled'
ZERO_AREA_ERR_MSG = 'The request bounding box has zero area'


class ExpectedException(Exception):
    pass

class SortKeyRequiredException(ExpectedException):
    pass

class InvalidSortKeyException(ExpectedException):
    pass

class WFSUnsupportedException(ExpectedException):
    pass

class ZeroAreaException(ExpectedException):
    pass


def get_points(vals):
    points = []
    curr_p = []
    for c in vals.split(' '):
        if len(curr_p) == 2:
            curr_p.reverse()
            points.append(curr_p)
            curr_p = []
        curr_p.append(float(c))
    if len(curr_p) == 2:
        curr_p.reverse()
        points.append(curr_p)
        curr_p = []
    return points

def get_props(content):
    # TODO: check that the data is indeed in html
    soup = BeautifulSoup(content, 'lxml')
    lis = soup.find_all('li')
    props = {}
    for li in lis:
        txt = li.text.strip()
        parts = txt.split(':', 1)
        if len(parts) != 2:
            return { 'unparsed': content }
        props[parts[0]] = parts[1].strip()
    return props


def extract_feature(entry):
    title = entry.get('title', None)
    content = entry.get('content', {}).get('#text', '')
    props = get_props(content)

    where = entry['georss:where']
    if 'georss:polygon' in where:
        points = get_points(where['georss:polygon'])
        geom = { 'type': 'Polygon', 'coordinates': [points] }
    elif 'georss:line' in where:
        points = get_points(where['georss:line'])
        geom = { 'type': 'LineString', 'coordinates': points }
    elif 'georss:point' in where:
        points = get_points(where['georss:point'])
        geom = { 'type': 'Point', 'coordinates': points[0] }
    else:
        raise Exception(f'unexpected content in {where}')

    return { 'type': 'Feature', 'id': title, 'geometry': geom, 'properties': props }


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

    return data, err_msg


def extract_features(xml_text, only_error=False):
    # deal with some xml/unicode messups
    # TODO: add a test case for this?
    xml_text = re.sub(r'&#([a-zA-Z0-9]+);?', r'[#\1;]', xml_text)

    data = xmltodict.parse(xml_text)

    err_msg = get_error_msg(data)
    if err_msg is not None:
        if err_msg.find(SORT_KEY_ERR_MSG) != -1:
            raise SortKeyRequiredException()
        if err_msg.find(INVALID_PROP_NAME_ERR_MSG) != -1:
            raise InvalidSortKeyException()
        if err_msg.find(WFS_DISABLED_ERR_MSG) != -1:
            raise WFSUnsupportedException()
        if err_msg.find(ZERO_AREA_ERR_MSG) != -1:
            raise ZeroAreaException()

        raise Exception(err_msg)

    if only_error:
        return []

    if 'feed' not in data:
       raise Exception('no feed in data')

    feed = data['feed']
    if 'entry' not in feed:
        return []

    entries = feed['entry']
    if type(entries) is not list:
        entries = [ entries ]

    feats = []
    for entry in entries:
        feat = extract_feature(entry)
        feats.append(feat)
    return feats
