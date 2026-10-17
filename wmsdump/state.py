import json
import logging
import os
from pathlib import Path

import jsonschema

from .enums import Extent, Operation, RetrievalMode, Service, parse_version

logger = logging.getLogger(__name__)

# Build patterns from enum values
SERVICE_PATTERN = '|'.join(s.value for s in [Service.WFS, Service.WMS])
OPERATION_PATTERN = '|'.join(o.value for o in Operation)

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
        "pattern": SERVICE_PATTERN
    },
    "version": {
        "description": "version of WMS or WFS protocol in use",
        "type": "string",
        "pattern": "[0-9]+\\.[0-9]+\\.[0-9]+",
    },
    "operation": {
        "description": "the ogc operation used.. one of GetMap, GetFeatureInfo or GetFeature",
        "type": "string",
        "pattern": OPERATION_PATTERN
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

EXTENT_FIXED_BUFFER_SCHEMA = {
    "type" : "object",
    "required": COMMON_REQUIRED + [ "explored_tree" ],
    "properties": COMMON_PROPS | {
        "explored_tree": {
            "type": "object",
            "description": "bounds tree and their exploration status (pipe-separated keys)",
            "patternProperties": {
                "^[0-9]+(\\|[0-9]+)*$": {
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
    if mode == RetrievalMode.EXTENT.value:
        schema = EXTENT_SCHEMA
    elif mode == RetrievalMode.EXTENT_FIXED_BUFFER.value:
        schema = EXTENT_FIXED_BUFFER_SCHEMA
    elif mode == RetrievalMode.OFFSET.value:
        schema = OFFSET_SCHEMA
    else:
        return False, f'{mode=} not supported'

    try:
        jsonschema.validate(state_data, schema)
    except jsonschema.exceptions.ValidationError as ex:
        return False, ex.message

    return True, None


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
        if mode == RetrievalMode.EXTENT:
            return ExtentState(**params)
        if mode == RetrievalMode.EXTENT_FIXED_BUFFER:
            return ExtentFixedBufferState(**params)
        if mode == RetrievalMode.OFFSET:
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
            "service": self.service.value,
            "version": self.version.value,
            "operation": self.operation.value,
            "mode": self.mode.value
        }

class ExtentState(State):
    def __init__(self, explored_tree={}, **params):
        super().__init__(**params)
        self.mode = RetrievalMode.EXTENT
        # Convert integer values to Extent enums when loading
        self.explored_tree = {k: Extent(v) for k, v in explored_tree.items()}
        self.done = {}
        self.current_count = 0
        self.get_nth = None

    def update_coverage(self, key, status):
        self.explored_tree[key] = status
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

        # Convert Extent enums to integers for JSON serialization
        d.update({
            'explored_tree': {k: v.value for k, v in self.explored_tree.items()}
        })
        return d


class ExtentFixedBufferState(ExtentState):
    """State for EXTENT_FIXED_BUFFER mode - uses pipe-separated keys."""
    def __init__(self, **params):
        super().__init__(**params)
        self.mode = RetrievalMode.EXTENT_FIXED_BUFFER


class OffsetState(State):
    def __init__(self,
                 sort_key=None,
                 downloaded_count=0,
                 index_done_till=0,
                 **params):

        super().__init__(**params)
        self.mode = RetrievalMode.OFFSET

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
            return False, (
                f'sort_key in state({self.sort_key}) does not match invocation sort_key({sort_key}). '
                'Delete the output and state files to restart, or use --force-sort-key-change '
                'to continue (may duplicate or skip records)'
            )
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


def convert_state_data_to_enums(state_data):
    """Convert string/int values in state data to enums."""
    if 'service' in state_data:
        state_data['service'] = Service(state_data['service'])
    if 'operation' in state_data:
        state_data['operation'] = Operation(state_data['operation'])
    if 'mode' in state_data:
        state_data['mode'] = RetrievalMode(state_data['mode'])
    if 'version' in state_data and 'service' in state_data:
        state_data['version'] = parse_version(state_data['service'], state_data['version'])
    return state_data


def load_state_from_json(state_file):
    """Load state data from JSON file and convert to enums."""
    state_data = json.loads(Path(state_file).read_text())
    return convert_state_data_to_enums(state_data)


def recover_output(state_file, output_file):
    """Roll back a process-crash tail to the last committed byte offset.

    Old states without output_bytes retain their existing count validation.
    Never infer a missing output prefix or advance a checkpoint to match a tail.
    Call only when this process is the sole writer and its buffers are closed.
    """
    state_data = json.loads(Path(state_file).read_text())
    committed = state_data.get('output_bytes')
    if committed is None:
        return
    if type(committed) is not int or committed < 0:
        raise ValueError('Invalid committed output byte offset')
    with open(output_file, 'r+b') as output:
        output.seek(0, os.SEEK_END)
        actual = output.tell()
        if actual < committed:
            raise ValueError('Output is shorter than the committed checkpoint; cannot safely resume')
        if committed:
            output.seek(committed - 1)
            if output.read(1) != b'\n':
                raise ValueError('Committed output byte offset is not a GeoJSONL line boundary')
        if actual > committed:
            logger.warning('Discarding %s uncommitted output bytes for replay', actual - committed)
            output.truncate(committed)


def sync_checkpoint(state_file, output_file):
    """Synchronize output before state at clean termination, not every batch."""
    for name in (output_file, state_file):
        with open(name, 'rb') as f:
            os.fsync(f.fileno())
    if os.name != 'nt':
        fd = os.open(Path(state_file).parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def can_restart_with_new_sort_key(state, output_file):
    """A changed sort key is safe only before any output or offset progress."""
    return (
        state.mode == RetrievalMode.OFFSET
        and state.index_done_till == 0
        and state.downloaded_count == 0
        and Path(output_file).stat().st_size == 0
    )


def get_state_from_files(state_file, output_file, *, flush_output, force_sort_key_change=False, **params):
    """Load resumable state, flushing the supplied writer before each checkpoint.

    flush_output is required and must flush any Python buffering owned by the
    output writer (for example, writer.flush).
    Normal checkpoints provide process-crash consistency while the OS is alive;
    they do not fsync each batch for power-loss durability.
    Changing sort keys after progress requires force_sort_key_change=True and
    may duplicate or skip records. All other resume checks still apply.
    """
    if not callable(flush_output):
        raise TypeError('flush_output must be a callable that flushes the output writer')

    output_file_exists = Path(output_file).exists()
    state_file_exists = Path(state_file).exists()

    if output_file_exists and not state_file_exists:
        if Path(output_file).is_file() and Path(output_file).stat().st_size == 0:
            logger.info(f'{output_file} is empty and {state_file} is missing; starting a fresh extraction')
        else:
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

        # Convert to enums after validation
        state_data = convert_state_data_to_enums(state_data)
        state = State.from_dict(**state_data)

        resume_params = dict(params)
        fresh_start = can_restart_with_new_sort_key(state, output_file)
        if state.mode == RetrievalMode.OFFSET and (fresh_start or force_sort_key_change):
            resume_params['sort_key'] = state.sort_key

        # Validate invocation identity before truncating any uncommitted tail.
        identity_params = dict(resume_params)
        if state.mode == RetrievalMode.OFFSET:
            identity_params['downloaded_count'] = state.downloaded_count
        else:
            identity_params.pop('sort_key', None)
        in_sync, reason = state.is_in_sync(**identity_params)
        if not in_sync:
            logger.error(f'state not in sync. Reason: {reason}')
            return None
        try:
            recover_output(state_file, output_file)
        except ValueError as e:
            logger.error(str(e))
            return None

        if state.mode == RetrievalMode.OFFSET:
            logger.info(f'Counting existing records in {output_file}')
            seen_count = 0
            with open(output_file) as f:
                for line in f:
                    seen_count += 1
            resume_params['downloaded_count'] = seen_count
        else:
            del resume_params['sort_key']
            logger.info(f'Reading existing records in {output_file}')
            with open(output_file) as f:
                for line in f:
                    state.add_raw_feature_no_dedup(line.strip('\n'))

        in_sync, reason = state.is_in_sync(**resume_params)
        if not in_sync:
            logger.error(f'state not in sync. Reason: {reason}')
            return None

        if state.mode == RetrievalMode.OFFSET and state.sort_key != params.get('sort_key'):
            new_sort_key = params.get('sort_key')
            if fresh_start:
                logger.info('No extraction progress saved; using sort key %r', new_sort_key)
            else:
                logger.warning('Forcing sort key change from %r to %r at offset %s; '
                               'records may be duplicated or skipped',
                               state.sort_key, new_sort_key, state.index_done_till)
            state.sort_key = new_sort_key
    else:
        state = State.from_dict(**params)

    def update_file(s):
        flush_output()
        if not Path(output_file).exists():
            Path(output_file).write_text('')
        checkpoint = dict(s, output_bytes=Path(output_file).stat().st_size)
        temporary = Path(str(state_file) + '.tmp')
        with temporary.open('w') as f:
            json.dump(checkpoint, f)
            f.flush()
        os.replace(temporary, state_file)
    state.updatecb = update_file

    return state

