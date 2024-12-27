import logging

from pprint import pformat

import requests

from .xml_helper import extract_xml_elements

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
 

def parse_capabilities(service, xml_txt, layer_list, service_info):
    if service == 'WMS':
        request_types = ['GetMap', 'GetFeatureInfo']
        for request_type in request_types:
            req_xpath = f'//WMT_MS_Capabilities/Capability/Request/{request_type}/Format/text() | ' + \
                        f'//WMS_Capabilities/Capability/Request/{request_type}/Format/text()'
            results, errors = extract_xml_elements(xml_txt, req_xpath)
            if len(results) > 0:
                service_info[request_type] = results
    else:
        req_xpath = '//WFS_Capabilities/Capability/Request/GetFeature/ResultFormat/*'
        results, errors = extract_xml_elements(xml_txt, req_xpath, return_elems=True)
        results = [ r.tag.split('}')[-1] for r in results ]
        if len(results) > 0:
            service_info['GetFeature'] = results

    if service == 'WMS':
        result_xpath = "//WMT_MS_Capabilities/Capability/Layer/Layer/Name/text() | " + \
                       "//WMS_Capabilities/Capability/Layer/Layer/Name/text()"
    else:
        result_xpath = "//WFS_Capabilities/FeatureTypeList/FeatureType/Name/text()"

    results, errors = extract_xml_elements(xml_txt, result_xpath)
    layer_list.extend(results)
    if len(errors) > 0:
        raise Exception(errors[0])

    exception_xpath = '//ServiceExceptionReport/ServiceException/text() | ' + \
                      '//ExceptionReport/Exception/text()'
    exceptions, errors = extract_xml_elements(xml_txt, exception_xpath)
    if len(exceptions) > 0:
        check_error_msg(exceptions[0])

def fill_layer_list(layer_list, service_info, service_url, service, service_version, namespace=None, **req_args):
    xml_txt = get_capabilities(service_url, service, service_version, namespace=namespace, **req_args)
    optionally_save_to_file(xml_txt)

    parse_capabilities(service, xml_txt, layer_list, service_info)

