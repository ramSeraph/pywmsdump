import logging

from pprint import pformat

import requests
import xmltodict

from .errors import check_error_msg, optionally_save_to_file

logger = logging.getLogger(__name__)

def get_capabilities(url, service, service_version, namespace=None, **req_args):
    query_params = {
        "service": service,
        "version": service_version,
        "request": "GetCapabilities"
    }
    if namespace is not None:
        query_params['namespace'] = namespace

    logger.info(f'Getting capabilities from {url}')
    logger.debug(pformat(query_params))
    resp = requests.get(url, params=query_params, **req_args)
    if not resp.ok:
        raise Exception(f'Unable to get capabilities from {url}')

    return resp.text

def matches(path, expected):
    got = [ p[0] for p in path ]
    got = got[:len(expected)]
    return got == expected
 
def parse_capabilities(service, xml_txt, layer_list, service_info):
    exceptions = []

    def process_error_2(path, item):
        if matches(path, ['ServiceExceptionReport', 'ServiceException']):
            exceptions.append(item)
        return True

    def process_error_3(path, item):
        if matches(path, ['ows:ExceptionReport', 'ows:Exception', 'ows:ExceptionText']):
            exceptions.append(item)
        return True

    def process_wms_5(path, item):
        if matches(path, ['WMT_MS_Capabilities', 'Capability', 'Layer', 'Layer', 'Name']) or \
           matches(path, ['WMS_Capabilities', 'Capability', 'Layer', 'Layer', 'Name']):
            layer_list.append(item)
        for request_type in ['GetMap', 'GetFeatureInfo']:
            if matches(path, ['WMT_MS_Capabilities', 'Capability', 'Request', request_type, 'Format']) or \
               matches(path, ['WMS_Capabilities', 'Capability', 'Request', request_type, 'Format']):
                if request_type not in service_info:
                    service_info[request_type] = []
                service_info[request_type].append(item)
        return True

    def process_wfs_6(path, item):
        if matches(path, ['WFS_Capabilities', 'Capability', 'Request', 'GetFeature', 'ResultFormat']):
            if 'GetFeature' not in service_info:
                service_info['GetFeature'] = []
            service_info['GetFeature'].append(path[-1][0])
        return True

    def process_wfs_4(path, item):
        if matches(path, ['WFS_Capabilities', 'FeatureTypeList', 'FeatureType', 'Name']):
            layer_list.append(item)
        return True

    if service == 'WMS':
        xmltodict.parse(xml_txt, item_depth=5, item_callback=process_wms_5)
    else:
        xmltodict.parse(xml_txt, item_depth=6, item_callback=process_wfs_6)
        xmltodict.parse(xml_txt, item_depth=4, item_callback=process_wfs_4)

    xmltodict.parse(xml_txt, item_depth=2, item_callback=process_error_2)
    xmltodict.parse(xml_txt, item_depth=3, item_callback=process_error_3)

    if len(exceptions) > 0:
        check_error_msg(exceptions[0])


def fill_layer_list(layer_list, service_info, service_url, service, service_version, namespace=None, **req_args):
    xml_txt = get_capabilities(service_url, service, service_version, namespace=namespace, **req_args)
    optionally_save_to_file(xml_txt)

    logger.info('parsing capabilities')
    parse_capabilities(service, xml_txt, layer_list, service_info)

