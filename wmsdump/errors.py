import xmltodict

SORT_KEY_ERR_MSG = 'Cannot do natural order without a primary key, ' + \
                   'please add it or specify a manual sort over existing attributes'
INVALID_PROP_NAME_ERR_MSG = 'Illegal property name'
WFS_DISABLED_ERR_MSG = 'Service WFS is disabled'
ZERO_AREA_ERR_MSG = 'The request bounding box has zero area'
KML_NOT_SUPPORTED_MSG = 'There is no support for creating maps in kml format'


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


def handle_error(data):
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
        if err_msg.find(KML_NOT_SUPPORTED_MSG) != -1:
            raise KMLUnsupportedException()

        raise Exception(err_msg)

def handle_error_xml(xml_text):
    data = xmltodict.parse(xml_text)
    handle_error(data)
