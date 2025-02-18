import re
import json
import logging

from pprint import pprint
from pathlib import Path

import click
import requests

from requests.packages.urllib3.exceptions import InsecureRequestWarning

from wmsdump.state import get_state_from_files
from wmsdump.geoserver import get_layer_list_from_page
from wmsdump.capabilities import fill_layer_list
from wmsdump.dumper import (
    OGCServiceDumper, DEFAULTS,
    bbox_to_str, get_global_bounds
)
from wmsdump.logging import setup_logging
from wmsdump.errors import (
    SortKeyRequiredException, InvalidSortKeyException,
    WFSUnsupportedException, KMLUnsupportedException,
    LayerMissingException, GeoRSSUnsupportedException,
    ServiceUnsupportedException
)

logger = logging.getLogger(__name__)

req_params = {}

def add_to_url(url, piece):
    if url.endswith('/'):
        return url + piece
    return url + '/' + piece

def print_service_info(service, info):
    for req_type, formats in info.items():
        print(f'{service}-{req_type}:')
        for fmt in formats:
            print(f'\t{fmt}')


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

class FileWriter:
    def __init__(self, fname, keep_idx):
        self.file = Path(fname)
        self.fh = None
        self.keep_idx = keep_idx
        self.count = 0
        self.idx_map = {}
        if self.keep_idx:
            self.init_idx()

    def init_idx(self):
        self.idx_map[self.count] = 0
        if not self.file.exists():
            return
        with open(self.file, 'r') as f:
            while True:
                line = f.readline()
                self.count += 1
                self.idx_map[self.count] = f.tell()
                if line == '':
                    break

    def get(self, n):
        if not self.keep_idx or \
           n >= self.count or \
           not self.file.exists():
            return None

        with open(self.file, 'r') as f:
            f.seek(self.idx_map[n])
            line = f.readline()
            line = line.strip('\n')
            return line

    def write(self, feat):
        if self.fh is None:
            self.fh = open(self.file, 'a')

        self.fh.write(json.dumps(feat))
        self.fh.write('\n')
        if self.keep_idx:
            self.count += 1
            self.idx_map[self.count] = self.fh.tell()

    def close(self):
        if self.fh is not None:
            self.fh.close()
            self.fh = None

EXPECTED_BOUNDS_FORMAT = '<xmin>,<ymin>,<xmax>,<ymax>'
EXPECTED_MAX_BOX_FORMAT = '<deltax>,<deltay>'

def get_box_dims(b_str):
    parts = b_str.split(',')
    if len(parts) != 2:
        raise Exception(f'does not have parts, expected: {EXPECTED_MAX_BOX_FORMAT}')

    nos = []
    for part in parts:
        try:
            n = float(part)
            if n <= 0:
                raise Exception('')
            nos.append(n)
        except Exception:
            raise Exception(f'not a number: {part}, expected postive non-zero floating point')
    b = { 'deltax': nos[0], 'deltay': nos[1] }

    return b


def get_bounds_from_str(b_str, crs_str):
    parts = b_str.split(',')
    if len(parts) != 4:
        raise Exception(f'does not have four parts, expected: {EXPECTED_BOUNDS_FORMAT}')

    nos = []
    for part in parts:
        try:
            n = float(part)
            nos.append(n)
        except Exception:
            raise Exception(f'not a number: {part}, expected floating point')
    b = { 'xmin': nos[0], 'ymin': nos[1], 'xmax': nos[2], 'ymax': nos[3] }

    g = get_global_bounds(crs_str)
            
    if b['xmin'] < g['xmin'] or b['xmin'] > g['xmax']:
        raise Exception(f'Invalid x: xmin: {b["xmin"]}, expected value between {g["xmin"]} and {g["xmax"]}')

    if b['xmax'] < g['xmin'] or b['xmax'] > g['xmax']:
        raise Exception(f'Invalid x: xmax: {b["xmax"]}, expected value between {g["xmin"]} and {g["xmax"]}')
            
    if b['ymin'] < g['ymin'] or b['ymin'] > g['ymax']:
        raise Exception(f'Invalid y: ymin: {b["ymin"]}, expected value between {g["ymin"]} and {g["ymax"]}')

    if b['ymax'] < g['ymin'] or b['ymax'] > g['ymax']:
        raise Exception(f'Invalid y: ymax: {b["ymax"]}, expected value between {g["ymin"]} and {g["ymax"]}')

    return b

def parse_header(ctx, param, values):
    """Convert header strings into a dictionary."""
    headers = {}

    if not values:
        return headers

    for value in values:
        try:
            key, val = value.split(':', 1)
            headers[key.strip()] = val.strip()
        except ValueError:
            raise click.BadParameter(
                f'Invalid header format: {value}. Use Key:Value format'
            )

    return headers

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
@click.option('--header', '-H',
              multiple=True, callback=parse_header,
              help='Header in the format "Key:Value". Can be used multiple times.')
def main(log_level, no_ssl_verify, request_timeout, header):
    setup_logging(log_level)
    req_params['verify'] = not no_ssl_verify
    req_params['timeout'] = request_timeout
    if no_ssl_verify:
        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    if header:
        req_params['headers'] = header



@main.command()
@click.option('--geoserver-url', '-g',
              help='Url of the geoserver endpoint.'
                   ' wms endpoint is assumed to be <geoserver_url>/ows')
@click.option('--service-url', '-u',
              help='Url of the wms/wfs endpoint from which we can probe for capabilities.'
                   'If not provided, will be derived from geoserver-url')
@click.option('--service', '-s',
              type=click.Choice(['WMS', 'WFS'], case_sensitive=False),
              default='WFS', show_default=True,
              help='service to use for extracting data, one of WFS or WMS')
@click.option('--service-version', '-v',
              help='the protocol version to use. defaults to'
                   f' \'{DEFAULTS["wms_version"]}\' for WMS and \'{DEFAULTS["wfs_version"]}\' for WFS')
@click.option('--namespace', '-n',
              help='only look for layers in a given namespace at the server,'
                   'only supported on geoserver implementations of wms')
@click.option('--output-file', '-o',
              type=click.Path(),
              help='file to write layer list to')
@click.option('--scrape-webpage', '-w',
              is_flag=True, default=False, show_default=True,
              help='scrape the geoserver web page instead of reading capabilities.'
                   ' Useful when capabilities is broken because of large number of layers')
def explore(geoserver_url, service_url, service, service_version, namespace, scrape_webpage, output_file):
    if geoserver_url is None and service_url is None:
        logger.error('Invalid invocation: '
                     'One of "--service-url" or "--geoserver-url" must be provided')
        return

    if scrape_webpage:
        if geoserver_url is None:
            logger.error('Invalid invocation: '
                         'if using "--scrape-webpage" then "--geoserver-url" must be provided')
            return

        layer_list = get_layer_list_from_page(geoserver_url, **req_params)
        handle_layer_list(layer_list, output_file)
        return

    if service_url is None:
        service_url = add_to_url(geoserver_url, 'ows')
        logger.info(f'setting wms url to "{service_url}"')

    if service_version is None:
        service_version = DEFAULTS['wms_version'] if service == 'WMS' else DEFAULTS['wfs_version']

    layer_list = []
    service_info = {}
    try:
        fill_layer_list(layer_list, service_info, service_url,
                        service, service_version,
                        namespace=namespace, **req_params)
    except WFSUnsupportedException:
        logger.error('WFS not supported on server. Try exploring WMS with --service/-s WMS')
    except Exception:
        logger.exception('Unable to get layer list using "GetCapabilities" call')
        logger.info('Consider parsing the geoserver webpage using '
                    '"--geoserver-url" and "--scrape-webpage" if the url is known '
                    'and has a functioning webpage')

        if len(layer_list) == 0:
            return
        logger.info('Could obtain some partial results.. dumping them')

    print_service_info(service, service_info)
    handle_layer_list(layer_list, output_file)


@main.command()
@click.argument('layername',
                required=True)
@click.argument('output-file',
                type=click.Path(), required=False)
@click.option('--output-dir', '-d',
              type=click.Path(file_okay=False), default='.',
              help='directory to write output files in. Only used when "output-file" is not given')
@click.option('--geoserver-url', '-g',
              help='Url of the geoserver endpoint.'
                   ' service-url is assumed to be <geoserver_url>/[<layer_namespace>/]ows')
@click.option('--service-url', '-u',
              help='Url of the wms/wfs endpoint from which we can retrieve data. '
                   'If not provided, will be derived from geoserver-url')
@click.option('--service', '-s',
              type=click.Choice(['WMS', 'WFS'], case_sensitive=False),
              default='WFS', show_default=True,
              help='service to use for extracting data, one of WFS or WMS')
@click.option('--service-version', '-v',
              help='the protocol version to use. defaults to'
                   f' \'{DEFAULTS["wms_version"]}\' for WMS and \'{DEFAULTS["wfs_version"]}\' for WFS')
@click.option('--retrieval-mode', '-m',
              type=click.Choice(['OFFSET', 'EXTENT'], case_sensitive=False),
              default=DEFAULTS['retrieval_mode'], show_default=True,
              help='which method to use to batch record retrieval, OFFSET uses record offset paging, '
                   'EXTENT uses bbox splitting and drilling down by spatial extent, '
                   'when using GetFeatureInfo this will be overriden to EXTENT')
@click.option('--operation', '-o',
              type=click.Choice(['GetMap', 'GetFeatureInfo'], case_sensitive=False),
              default=DEFAULTS['operation'], show_default=True,
              help='which operation to use for querying a WMS endpoint')
@click.option('--flavor', 
              type=click.Choice(['Geoserver', 'QGISserver'], case_sensitive=False),
              default=DEFAULTS['flavor'], show_default=True,
              help='vendor of the WMS services, useful to specify for GetFeatureInfo based retrieval')
@click.option('--sort-key', '-k',
              help='key to use to do paged retrieval')
@click.option('--batch-size', '-b',
              type=int, default=DEFAULTS['batch_size'], show_default=True,
              help='batch size to use for retrieval')
@click.option('--pause-seconds', '-p',
              type=int, default=DEFAULTS['pause_seconds'], show_default=True,
              help='amount of time to pause between a batch of requests')
@click.option('--requests-to-pause', 
              type=int, default=DEFAULTS['requests_to_pause'], show_default=True,
              help='number of requests to make before pausing for --pause-seconds')
@click.option('--max-attempts', 
              type=int, default=DEFAULTS['max_attempts'], show_default=True,
              help='number of times to attempt a request before giving up')
@click.option('--retry-delay', '-r',
              type=int, default=DEFAULTS['retry_delay'], show_default=True,
              help='number of secs to wait before retrying on failure.. '
                   'for each failure the delay is incremented by the same number')
@click.option('--geometry-precision', '-g',
              type=int, default=DEFAULTS['geometry_precision'], show_default=True,
              help='decimal point precision of geometry to be returned, '
                   'truncation is done on client side. -1 means no truncation')
@click.option('--getmap-format', '-f',
              type=click.Choice(['KML', 'GEORSS'], case_sensitive=False),
              default=DEFAULTS['getmap_format'], show_default=True,
              help='which format to use while pulling using WMS GetMap')
@click.option('--kml-strip-point/--no-kml-strip-point',
              default=DEFAULTS['kml_strip_point'], show_default=True,
              help='Whether to strip the points in polygons and linestring geomcollections')
@click.option('--kml-keep-original-props/--no-kml-keep-original-props',
              default=DEFAULTS['kml_keep_original_props'], show_default=True,
              help='Whether to keep the original style related props in kml conversion')
@click.option('--out-srs', 
              default=DEFAULTS['out_srs'], show_default=True,
              help='CRS to ask the server to return data in.. '
                   'will be written out in the same CRS')
@click.option('--bounds', 
              help='bounds to restrict query to. defaults to CRS bounds. '
                   f'format: "{EXPECTED_BOUNDS_FORMAT}"')
@click.option('--max-box-dims',
              help='when querying using EXTENT mode, maximum size of the bounding box to use.'
                   f' format: {EXPECTED_MAX_BOX_FORMAT}')
@click.option('--skip-index', 
              type=int, default=0, show_default=True,
              help='skip n elements in index.. useful to skip records causing failure. '
                   'only applicable when using OFFSET based retrieval') 
def extract(layername, output_file, output_dir,
            geoserver_url, service_url,
            service, service_version, flavor,
            retrieval_mode, operation, out_srs,
            sort_key, batch_size, geometry_precision, 
            requests_to_pause, pause_seconds, max_attempts,
            getmap_format, kml_strip_point,
            kml_keep_original_props,
            retry_delay, bounds,
            max_box_dims, skip_index):

    if service_version is None:
        service_version = DEFAULTS['wms_version'] if service == 'WMS' else DEFAULTS['wfs_version']

    if service == 'WMS' and operation == 'GetFeatureInfo':
        if retrieval_mode != 'EXTENT':
            logger.info('Using GetFeatureInfo for retrieval.. overriding mode to EXTENT')
            retrieval_mode = 'EXTENT'

    if service == 'WFS':
        operation = 'GetFeature'

    if geoserver_url is None and service_url is None:
        logger.error('Invalid invocation: '
                     'One of "--service-url" or "--geoserver-url" must be provided')
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
            service_url = add_to_url(geoserver_url, 'ows')
        elif len(parts) == 2:
            layername = parts[1]
            service_url = add_to_url(geoserver_url, f'{parts[0]}/ows')
        else:
            logger.error(f'{layername} is of unexpected format.. has more than one ":"')
            return

    if bounds is None:
        bounds = get_global_bounds(out_srs)
        logger.info(f'working with bounds: {bbox_to_str(bounds, None)}')
    else:
        try:
            bounds = get_bounds_from_str(bounds, out_srs)
        except Exception:
            logger.error(f'Invalid bounds string: "{bounds}"')
            return

    if retrieval_mode == 'EXTENT' and max_box_dims is not None:
        try:
            max_box_dims = get_box_dims(max_box_dims)
        except Exception:
            logger.error(f'Invalid max box dimensions string: "{max_box_dims}"')
            return


    logger.info(f'working with {service_url=} and {layername=}, '
                f'{service=} and {operation=}, mode={retrieval_mode}')

    state_file = output_file + '.state'

    state = get_state_from_files(state_file, output_file,
                                 url=service_url,
                                 layername=layername,
                                 service=service,
                                 version=service_version,
                                 operation=operation,
                                 mode=retrieval_mode,
                                 sort_key=sort_key)
    if state is None:
        return

    if skip_index != 0 and retrieval_mode != 'OFFSET':
        logger.error('skip-index can\'t be used for non OFFSET based retrieval')
        return

    if skip_index < 0:
        logger.error('skip index can\'t be negative')
        return

    if skip_index > 0:
        state.update(skip_index, 0)

    writer = FileWriter(output_file,
                        keep_idx=(retrieval_mode == 'EXTENT'))

    dumper = OGCServiceDumper(service_url, layername, service,
                              service_version=service_version,
                              operation=operation,
                              retrieval_mode=retrieval_mode,
                              flavor=flavor,
                              batch_size=batch_size,
                              sort_key=sort_key,
                              state=state,
                              requests_to_pause=requests_to_pause,
                              pause_seconds=pause_seconds,
                              retry_delay=retry_delay,
                              max_attempts=max_attempts,
                              geometry_precision=geometry_precision,
                              getmap_format=getmap_format,
                              kml_strip_point=kml_strip_point,
                              kml_keep_original_props=kml_keep_original_props,
                              out_srs=out_srs,
                              bounds=bounds,
                              max_box_dims=max_box_dims,
                              get_nth=writer.get,
                              req_params=req_params)

    dump_samples = False
    try:
        for feat in dumper:
            writer.write(feat)
        Path(state_file).unlink()
        logger.info('Done!!!')
    except SortKeyRequiredException:
        logger.error('failed to iterate over records as no sorting key is specified. '
                     'use "--sort-key/-k"?')
        dump_samples = True
    except InvalidSortKeyException:
        logger.error('failed to iterate over records as the sorting key specified is invalid')
        dump_samples = True
    except WFSUnsupportedException:
        logger.error('WFS is not supported on this endpoint.. '
                     'try using --service/-s WMS')
    except ServiceUnsupportedException:
        alt_service = 'WMS' if service == 'WFS' else 'WFS'
        logger.error(f'{service} is not supported on this endpoint.. '
                     f'try using --service/-s {alt_service}')
    except KMLUnsupportedException:
        logger.error('kml is not supported on this endpoint.. '
                     'try using --getmap-format/-f GEORSS')
    except GeoRSSUnsupportedException:
        logger.error('GeoRSS is not supported on this endpoint.. '
                     'try using --getmap-format/-f KML')
    except LayerMissingException:
        logger.error('the layer specified is not supported on this endpoint.. '
                     'check available layers using the "explore" command')
    finally:
        writer.close()

    if dump_samples:
        logger.info('dumping a couple of records to inspect and pick a sorting key')
        feats = dumper.get_features(2, no_index=True, no_sort=True)
        for feat in feats:
            pprint(feat['properties'])


       
