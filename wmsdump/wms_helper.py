import logging

from pprint import pformat

import requests
import xmltodict

logger = logging.getLogger(__name__)

def get_capabilities(url, wms_version='1.1.1', **req_args):
    query_params = {
        "service": "wms",
        "version": wms_version,
        "request": "GetCapabilities"
    }

    logger.info(f'Getting capabilities from {url}')
    resp = requests.get(url, params=query_params, **req_args)
    if not resp.ok:
        raise Exception(f'Unable to get capabilities from {url}')

    return resp.text
 

def get_layer_list(url, wms_version, **req_args):
    xml_text = get_capabilities(url, wms_version, **req_args)
    parsed = xmltodict.parse(xml_text)
    if 'ServiceExceptionReport' in parsed:
        logger.error(pformat(parsed))
        raise Exception('Unable to get capabailities')

    cap_key = 'WMT_MS_Capabilities'
    if cap_key not in parsed:
        cap_key = 'WMS_Capabilities'
    try:
        layers = parsed[cap_key]['Capability']['Layer']['Layer']
    except:
        logger.exception('Unable to parse capabilities')
        logger.error(pformat(parsed))
        raise
    lnames = [ l.get('Name', None) for l in layers ]
    return lnames
