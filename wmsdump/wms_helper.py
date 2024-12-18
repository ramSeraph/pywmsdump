import io
import logging

from pprint import pformat

import requests

from .xml_helper import extract_xml_elements

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
 

def fill_layer_list(layer_list, wms_info, url, wms_version, **req_args):
    xml_txt = get_capabilities(url, wms_version, **req_args)

    for request_type in ['GetMap', 'GetFeatureInfo']:
        req_xpath = f'//WMT_MS_Capabilities/Capability/Request/{request_type}/Format/text() | ' + \
                    f'//WMS_Capabilities/Capability/Request/{request_type}/Format/text()'
        results, errors = extract_xml_elements(xml_txt, req_xpath)
        if len(results) > 0:
            wms_info[request_type] = results

    result_xpath = "//WMT_MS_Capabilities/Capability/Layer/Layer/Name/text() | " + \
                   "//WMS_Capabilities/Capability/Layer/Layer/Name/text()"

    results, errors = extract_xml_elements(xml_txt, result_xpath)
    layer_list.extend(results)
    if len(errors) > 0:
        raise Exception(errors[0])

    #exception_xpath = '//ServiceExceptionReport | //ows:ExceptionReport'
    exception_xpath = '//ServiceExceptionReport'

    exceptions, errors = extract_xml_elements(xml_txt, exception_xpath)
    if len(exceptions) > 0:
        raise Exception(exceptions[0])
