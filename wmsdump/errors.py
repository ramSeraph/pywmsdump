import os
import re

from pathlib import Path

import xmltodict

SORT_KEY_ERR_MSGS = [ 'Cannot do natural order without a primary key, ' + \
                      'please add it or specify a manual sort over existing attributes' ]
INVALID_PROP_NAME_ERR_MSGS = [ 'Illegal property name',
                               'Sort property \'[\\w:]+\' not available in [\\w:]+' ]
WFS_DISABLED_ERR_MSGS = [ 'Service WFS is disabled',
                          'WFS request not enabled' ]
SERVICE_DISABLED_ERR_MSGS = [ 'Can\'t recognize service requested.' ]
ZERO_AREA_ERR_MSGS = [ 'The request bounding box has zero area' ]
KML_NOT_SUPPORTED_MSGS = [ 'There is no support for creating maps in kml format',
                           'There is no support for creating maps in '
                           'application/vnd.google-earth.kml%2Bxml format',
                           'There is no support for creating maps in '
                           'application/vnd.google-earth.kml+xml format' ]
GEORSS_NOT_SUPPORTED_MSGS = [ 'Creating maps using application/atom xml is not allowed' ]
LAYER_MISSING_MSGS = [ 'Could not find layer' ]

class KnownException(Exception):
    pass

class SortKeyRequiredException(KnownException):
    pass

class InvalidSortKeyException(KnownException):
    pass

class WFSUnsupportedException(KnownException):
    pass

class ZeroAreaException(KnownException):
    pass

class KMLUnsupportedException(KnownException):
    pass

class ServiceUnsupportedException(KnownException):
    pass

class GeoRSSUnsupportedException(KnownException):
    pass

class LayerMissingException(KnownException):
    pass

ERROR_MAPPINGS = [
    (SORT_KEY_ERR_MSGS, SortKeyRequiredException),
    (INVALID_PROP_NAME_ERR_MSGS, InvalidSortKeyException),
    (WFS_DISABLED_ERR_MSGS, WFSUnsupportedException),
    (SERVICE_DISABLED_ERR_MSGS, ServiceUnsupportedException),
    (ZERO_AREA_ERR_MSGS, ZeroAreaException),
    (KML_NOT_SUPPORTED_MSGS, KMLUnsupportedException),
    (GEORSS_NOT_SUPPORTED_MSGS, GeoRSSUnsupportedException),
    (LAYER_MISSING_MSGS, LayerMissingException),
]


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


def check_error_msg(err_msg):
    for msg_patterns, ExceptopnClass in ERROR_MAPPINGS:
        for msg_pattern in msg_patterns:
            matches = re.search(rf'{msg_pattern}', err_msg)
            if matches is not None:
                raise ExceptopnClass()

    raise Exception(err_msg)


def handle_error(data):
    err_msg = get_error_msg(data)

    if err_msg is not None:
        check_error_msg(err_msg)


def handle_error_xml(xml_text):
    data = xmltodict.parse(xml_text)
    handle_error(data)


def optionally_save_to_file(txt):
    to_file = os.environ.get('WMSDUMP_SAVE_RESPONSE_TO_FILE', None)
    if to_file is None or to_file.strip() == '':
        return
    p = Path(to_file)
    try:
        p.write_text(txt)
    except Exception:
        pass


