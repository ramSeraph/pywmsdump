import json
import logging

from enum import Enum
from pathlib import Path

import jsonschema

logger = logging.getLogger(__name__)

COMMON_PROPS = {
    "url": {
        "description": "the wms endpoint url",
        "type": "string",
        "format": "uri"
    },
    "layername": {
        "description": "name of the layer being downloaded",
        "type": "string"
    },
    "service": {
        "description": "the ogc service used.. one of WMS or WFS",
        "type": "string",
        "pattern": "WFS|WMS"
    },
    "version": {
        "description": "version of WMS or WFS protocol in use",
        "type": "string",
        "pattern": "[0-9]+\\.[0-9]+\\.[0-9]+",
    },
    "operation": {
        "description": "the ogc operation used.. one of GetMap, GetFeatureInfo or GetFeature",
        "type": "string",
        "pattern": "GetMap|GetFeatureInfo|GetFeature"
    }
}

COMMON_REQUIRED = [ 'url', 'layername', 'service', 'version', 'operation', 'mode' ]

OFFSET_SCHEMA = {
    "type" : "object",
    "required": COMMON_REQUIRED + [ "sort_key", "index_done_till", "downloaded_count" ],
    "properties": COMMON_PROPS | {
        "sort_key": {
            "descrption": "field to sort on",
            "type": ["string", "null"],
        },
        "index_done_till": {
            "descrption": "offset till which the index has been explored",
            "type": "integer",
            "minimum": 0
        },
        "downloaded_count": {
            "descrption": "count of downloaded records",
            "type": "integer",
            "minimum": 0
        }
    }
}

EXTENT_SCHEMA = {
    "type" : "object",
    "required": COMMON_REQUIRED + [ "explored_tree" ],
    "properties": COMMON_PROPS | {
        "explored_tree": {
            "type": "object",
            "description": "bounds tree and their exploration status",
            "patternProperties": {
                "^[0-3]+$": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 3
                }
            }
        }
    }
}


def validate_state_dict(state_data):
    mode = state_data.get('mode', None)
    schema = None
    if mode == 'EXTENT':
        schema = EXTENT_SCHEMA
    elif mode == 'OFFSET':
        schema = OFFSET_SCHEMA
    else:
        return False, f'{mode=} not supported'

    try:
        jsonschema.validate(state_data, schema)
    except jsonschema.exceptions.ValidationError as ex:
        return False, ex.message

    return True, None


class Extent(Enum):
    NOT_PRESENT = 0
    OPEN = 1
    EXPLORED = 2

class State:
    def __init__(self, 
                 url=None,
                 layername=None,
                 service=None,
                 version=None,
                 operation=None,
                 **params):
        self.url = url
        self.layername = layername
        self.service = service
        self.version = version
        self.operation = operation
        self.updatecb = None
        self.mode = None

    def from_dict(**params):
        mode = params.pop('mode', None)
        if mode == 'EXTENT':
            return ExtentState(**params)
        if mode == 'OFFSET':
            return OffsetState(**params)

        raise Exception(f'Unsupported mode: {mode}')


    def is_in_sync(self,
                   url=None,
                   layername=None,
                   service=None,
                   version=None,
                   operation=None,
                   mode=None):
        if self.url != url:
            return False, f'state url:{self.url} not the same as invocation url:{url}'

        if self.layername != layername:
            return False, f'state layername:{self.layername} not the same as invocation layername:{layername}'

        if self.service != service:
            return False, f'state service:{self.service} not the same as invocation service:{service}'
        
        if self.version != version:
            return False, f'state version:{self.version} not the same as invocation version:{version}'

        if self.operation != operation:
            return False, f'state operation:{self.operation} not the same as invocation operation:{operation}'

        if self.mode != mode:
            return False, f'state mode:{self.mode} not the same as invocation mode:{mode}'

        return True, None

    def get_dict(self):
        return {
            "url": self.url,
            "layername": self.layername,
            "service": self.service,
            "version": self.version,
            "operation": self.operation,
            "mode": self.mode
        }

class ExtentState(State):
    def __init__(self, explored_tree={}, **params):
        super().__init__(**params)
        self.mode = 'EXTENT'
        self.explored_tree = explored_tree
        self.done = {}
        self.current_count = 0
        self.get_nth = None

    def update_coverage(self, key, status):
        self.explored_tree[key] = status.value
        if self.updatecb is not None:
            self.updatecb(self.get_dict())

    def add_raw_feature_no_dedup(self, f_str):
        hashed = hash(f_str)
        if hashed not in self.done:
            self.done[hashed] = []
        self.done[hashed].append(self.current_count)
        self.current_count += 1

    def add_feature(self, feature):
        f_str = json.dumps(feature)
        hashed = hash(f_str)
        if hashed in self.done:
            for idx in self.done[hashed]:
                existing = self.get_nth(idx)
                if existing == f_str:
                    return False
            self.done[hashed].append(self.current_count)
            self.current_count += 1
            return True

        self.done[hashed] = [ self.current_count ]
        self.current_count += 1
        return True

    def get_dict(self):
        d = super().get_dict()

        d.update({
            'explored_tree': self.explored_tree 
        })
        return d


class OffsetState(State):
    def __init__(self,
                 sort_key=None,
                 downloaded_count=0,
                 index_done_till=0,
                 **params):

        super().__init__(**params)
        self.mode = 'OFFSET'

        self.sort_key = sort_key
        self.downloaded_count = downloaded_count
        self.index_done_till = index_done_till

    def is_in_sync(self,
                   downloaded_count=None,
                   sort_key=None,
                   **invocation_params):
        valid, reason = super().is_in_sync(**invocation_params)
        if not valid:
            return valid, reason

        if self.downloaded_count != downloaded_count:
            return False, f'downloaded records count in state({self.downloaded_count}) ' + \
                          f'doesn\'t match existing count({downloaded_count})'

        if self.sort_key != sort_key:
            return False, f'sort_key in state({self.sort_key}) ' + \
                          f'doesn\'t match existing sort_key({sort_key})'
        return True, None

    def update(self, index_delta, downloaded_count_delta):
        self.index_done_till += index_delta
        self.downloaded_count += downloaded_count_delta
        if self.updatecb is not None:
            self.updatecb(self.get_dict())

    def get_dict(self):
        d = super().get_dict()

        d.update({
            'sort_key': self.sort_key, 
            'index_done_till': self.index_done_till,
            'downloaded_count': self.downloaded_count,
        })
        return d


def get_state_from_files(state_file, output_file, **params):
    output_file_exists = Path(output_file).exists()
    state_file_exists = Path(state_file).exists()

    if output_file_exists and not state_file_exists:
        logger.error(f'{output_file} exists already.. but {state_file} does not. '
                      'Can\'t continue.. delete the existing file to proceed')
        return None

    if state_file_exists and not output_file_exists:
        logger.error(f'{state_file} exists already.. but {output_file} does not. '
                      'Can\'t continue.. delete the existing file to proceed')
        return None


    if state_file_exists and output_file_exists:
        logger.info('Both the output file and state file exists..'
                    'trying to resume extraction')
        try:
            state_data = json.loads(Path(state_file).read_text())
        except Exception:
            logger.exception(f'Unable to read {state_file}')
            return None

        valid, reason = validate_state_dict(state_data)
        if not valid:
            logger.error(f'state in file is invalid. Reason: {reason}')
            return None

        state = State.from_dict(**state_data)

        if state.mode == 'OFFSET':
            logger.info(f'Counting existing records in {output_file}')
            seen_count = 0
            with open(output_file, 'r') as f:
                for line in f:
                    seen_count += 1
            params['downloaded_count'] = seen_count
        else:
            del params['sort_key']
            logger.info(f'Reading existing records in {output_file}')
            with open(output_file, 'r') as f:
                for line in f:
                    state.add_raw_feature_no_dedup(line.strip('\n'))

        in_sync, reason = state.is_in_sync(**params)
        if not in_sync:
            logger.error(f'state not in sync. Reason: {reason}')
            return None
    else:
        state = State.from_dict(**params)

    state.updatecb = lambda s: Path(state_file).write_text(json.dumps(s))

    return state

