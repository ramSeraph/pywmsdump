import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class State:
    def __init__(self,
                 url=None,
                 service=None,
                 layername=None,
                 sort_key=None, 
                 index_done_till=0,
                 downloaded_count=0,
                 updatecb=None):
        self.url = url
        self.service = service
        self.layername = layername
        self.sort_key = sort_key
        self.index_done_till = index_done_till
        self.downloaded_count = downloaded_count
        self.updatecb = updatecb



    def validate(self):
        if self.service not in ['WMS', 'WFS']:
            return False, f'Invalid value for service - {self.service}.' + \
                           'Should be one of "WFS", "WMS"'

        if not isinstance(self.index_done_till, int) or self.index_done_till < 0:
            return False, f'Invalid value for index_done_till - {self.index_done_till}' + \
                           ' Should be a whole number'  

        if not isinstance(self.downloaded_count, int) or self.downloaded_count < 0:
            return False, f'Invalid value for downloaded_count - {self.downloaded_count}' + \
                           ' Should be a whole number'  

        return True, None

    def is_in_sync(self, url, service, layername, sort_key, seen_count):

        if self.url != url:
            return False, f'url in state({self.url}) doesn\'t match service in use({url})'

        if self.service != service:
            return False, f'service in state({self.service}) doesn\'t match service in use({service})'

        if self.layername != layername:
            return False, f'layername in state({self.layername}) doesn\'t match layername in use({layername})'

        if self.sort_key != sort_key:
            return False, f'sort key in state({self.sort_key}) doesn\'t match sort key in use({sort_key})'

        if self.downloaded_count != seen_count:
            return False, f'downloaded records count in state({self.downloaded_count}) ' + \
                          f'doesn\'t match existing count({seen_count})'

        return True, None

    def update(self, index_delta, downloaded_count_delta):
        self.index_done_till += index_delta
        self.downloaded_count += downloaded_count_delta
        if self.updatecb is not None:
            self.updatecb(self.get_dict())

    def get_dict(self):
        return {
            'url': self.url,
            'service': self.service,
            'layername': self.layername,
            'sort_key': self.sort_key, 
            'index_done_till': self.index_done_till,
            'downloaded_count': self.downloaded_count,
        }

def get_state_from_files(state_file, output_file, url, service, sort_key, layername):
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

    state = State(url=url, service=service, sort_key=sort_key, layername=layername)
    if state_file_exists and output_file_exists:
        logger.info('Both the output file and state file exists.. trying to resume extraction')
        try:
            state_data = json.loads(Path(state_file).read_text())
        except Exception:
            logger.exception('Unable to read {state_file}')
            return None

        state = State(**state_data)

        valid, reason = state.validate()

        if not valid:
            logger.error(f'state in file is invalid. Reason: {reason}')
            return None

        logger.info(f'Counting existing records in {output_file}')
        seen_count = 0
        with open(output_file, 'r') as f:
            for line in f:
                seen_count += 1

        in_sync, reason = state.is_in_sync(url, service, layername, sort_key, seen_count)
        if not in_sync:
            logger.error(f'state not in sync. Reason: {reason}')
            return None

    def update_state_file(s):
        Path(state_file).write_text(json.dumps(s))

    state.updatecb = update_state_file

    return state

