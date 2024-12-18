import re
import json
import logging

from pprint import pprint
from pathlib import Path

import click
import xmltodict
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning


from wmsdump.state import get_state_from_files
from wmsdump.page_scraper import get_layer_list_from_page
from wmsdump.wms_helper import fill_layer_list
from wmsdump.dumper import (
    WMSDumper, SortKeyRequiredException,
    InvalidSortKeyException, WFSUnsupportedException
)

logger = logging.getLogger(__name__)

req_params = {}

def add_to_url(url, piece):
    if url.endswith('/'):
        return url + piece
    return url + '/' + piece


def handle_layer_list(layer_list, output_file):
    if output_file is None:
        print('layers:')
        for lname in layer_list:
            print(f'\t{lname}')
    else:
        logger.info(f'writing layer list to "{output_file}"')
        with open(output_file, 'w') as f:
            for lname in layer_list:
                f.write(lname)
                f.write('\n')


@click.group()
@click.option('--log-level', '-l',
              type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                                case_sensitive=False),
              default='INFO', show_default=True,
              help='set logging level')
@click.option('--no-ssl-verify',
              is_flag=True, default=False, show_default=True,
              help='switch off ssl verification') 
@click.option('--request-timeout', '-t', type=int,
              help='timeout for the http requests') 
def main(log_level, no_ssl_verify, request_timeout):
    logging.basicConfig(level=log_level)
    req_params['verify'] = not no_ssl_verify
    req_params['timeout'] = request_timeout
    if no_ssl_verify:
        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


@main.command()
@click.option('--geoserver-url', '-g',
              help='Url of the geoserver endpoint.'
                   ' wms endpoint is assumed to be <geoserver_url>/ows')
@click.option('--wms-url', '-w',
              help='Url of the wms endpoint from which we can probe for capabilities.'
                   'If not provided, will be derived from geoserver-url')
@click.option('--wms-version',
              default='1.1.1', show_default=True,
              help='set the wms api version to use')
@click.option('--output-file', '-o',
              type=click.Path(),
              help='file to write layer list to')
@click.option('--scrape-webpage', '-s',
              is_flag=True, default=False, show_default=True,
              help='scrape the geoserver web page instead of reading capabilities.'
                   ' Useful when capabilities is broken because of large number of layers')
def explore(geoserver_url, wms_url, wms_version, scrape_webpage, output_file):
    if geoserver_url is None and wms_url is None:
        logger.error('Invalid invocation: '
                     'One of "--wms-url" or "--geoserver-url" must be provided')
        return

    if scrape_webpage:
        if geoserver_url is None:
            logger.error('Invalid invocation: '
                         'if using "--scrape-webpage" then "--geoserver-url" must be provided')
            return

        layer_list = get_layer_list_from_page(geoserver_url, **req_params)
        handle_layer_list(layer_list, output_file)
        return

    if wms_url is None:
        wms_url = add_to_url(geoserver_url, './ows')
        logger.info(f'setting wms url to "{wms_url}"')

    layer_list = []
    try:
        fill_layer_list(layer_list, wms_url, wms_version, **req_params)
    except Exception:
        logger.exception('Unable to get layer list using "GetCapabilities" call')
        logger.info('Consider parsing the geoserver webpage using '
                    '"--geoserver-url" and "--scrape-webpage" if the url is known '
                    'and has a functioning webpage')
        if len(layer_list) > 0:
            logger.info('Could obtain some partial results.. dumping them')
            handle_layer_list(layer_list, output_file)
        return
    handle_layer_list(layer_list, output_file)


@main.command()
@click.argument('layername',
                required=True)
@click.argument('output-file',
                type=click.Path(), required=False)
@click.option('--geoserver-url', '-g',
              help='Url of the geoserver endpoint.'
                   ' wms endpoint is assumed to be <geoserver_url>/<layer_group>/ows')
@click.option('--wms-url', '-w',
              help='Url of the wms endpoint from which we can retrieve data.'
                   'If not provided, will be derived from geoserver-url')
@click.option('--wms-version',
              default='1.1.1', show_default=True,
              help='set the wms api version to use')
@click.option('--wfs-version',
              default='1.0.0', show_default=True,
              help='set the wfs api version to use')
@click.option('--service', '-s',
              type=click.Choice(['WMS', 'WFS'], case_sensitive=False),
              default='WFS', show_default=True,
              help='service to use for extracting data, one of WFS or WMS')
@click.option('--sort-key', '-k',
              help='key to use to do paged retrieval')
@click.option('--batch-size', '-b',
              type=int, default=1000, show_default=True,
              help='batch size to use for retrieval')
@click.option('--pause-seconds', '-p',
              type=int, default=2, show_default=True,
              help='amount of time to pause between a batch of requests')
@click.option('--requests-to-pause', 
              type=int, default=10, show_default=True,
              help='number of requests to make before pausing for --pause-seconds')
@click.option('--max-attempts', 
              type=int, default=5, show_default=True,
              help='number of times to attempt a request before giving up')
@click.option('--geometry-precision', '-g',
              type=int, default=7, show_default=True,
              help='decimal point precision of geometry to be returned, '
                   'only applies to the wms mode. truncation done on client side')
@click.option('--output-dir', '-d',
              type=click.Path(file_okay=False), default='.',
              help='directory to write output files in. Only used when "output-file" is not given')
@click.option('--skip-index', 
              type=int, default=0, show_default=True,
              help='skip n elements in index.. useful to skip records causing failure')
def extract(layername, output_file, geoserver_url, wms_url,
            service, sort_key, batch_size, wms_version,
            wfs_version, geometry_precision, output_dir,
            requests_to_pause, pause_seconds, max_attempts,
            skip_index):
    if geoserver_url is None and wms_url is None:
        logger.error('Invalid invocation: '
                     'One of "--wms-url" or "--geoserver-url" must be provided')
        return

    if output_file is None:
        output_file = re.sub(r'[^\w\d-]','_', layername) + '.geojsonl'
        output_dir_p = Path(output_dir)
        output_dir_p.mkdir(exist_ok=True, parents=True)
        ouput_file_p = output_dir_p / output_file
        output_file = str(ouput_file_p)
        logger.info(f'output file not specified.. writing to {output_file}')

    if geoserver_url is not None:
        parts = layername.split(':')
        if len(parts) == 1:
            wms_url = add_to_url(geoserver_url, 'ows')
        elif len(parts) == 2:
            layername = parts[1]
            wms_url = add_to_url(geoserver_url, f'{parts[0]}/ows')
        else:
            logger.error(f'{layername} is of unexpected format.. has more than one ":"')
            return

    logger.info(f'working with {wms_url=} and {layername=}')

    state_file = output_file + '.state'

    state = get_state_from_files(state_file, output_file, wms_url, service, sort_key, layername)
    if state is None:
        return

    if skip_index < 0:
        logger.error('skip index can\'t be negative')
        return

    if skip_index > 0:
        state.update(skip_index, 0)

    dumper = WMSDumper(wms_url, layername, service,
                       wfs_version=wfs_version,
                       wms_version=wms_version,
                       batch_size=batch_size,
                       sort_key=sort_key,
                       state=state,
                       requests_to_pause=requests_to_pause,
                       pause_seconds=pause_seconds,
                       max_attempts=max_attempts,
                       geometry_precision=geometry_precision,
                       req_params=req_params)

    dump_samples = False
    f = None
    try:
        for feat in dumper:
            # only open if you are about to write
            if f is None:
                f = open(output_file, 'a')
            f.write(json.dumps(feat))
            f.write('\n')
        Path(state_file).unlink()
        logger.info('Done!!!')
    except SortKeyRequiredException:
        logger.error('failed to iterate over records as a sorting key is not specified using "--sort-key"')
        dump_samples = True
    except InvalidSortKeyException:
        logger.error('failed to iterate over records as a sorting key specified is invalid')
        dump_samples = True
    except WFSUnsupportedException:
        logger.error('WFS is not supported on this endpoint.. try using --service WMS')
    finally:
        if f is not None:
            f.close()

    if dump_samples:
        logger.info('dumping a couple of records to inspect and pick a sorting key')
        feats = dumper.get_features(2, no_index=True, no_sort=True)
        for feat in feats:
            pprint(feat['properties'])
        
