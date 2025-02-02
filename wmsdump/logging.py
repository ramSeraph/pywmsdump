import logging
from colorlog import ColoredFormatter

def setup_logging(log_level):
    formatter = ColoredFormatter("%(log_color)s%(asctime)s [%(levelname)-5s] %(message)s",
                                 datefmt='%Y-%m-%d %H:%M:%S',
                                 reset=True,
                                 log_colors={
                                     'DEBUG':    'cyan',
                                     'INFO':     'green',
                                     'WARNING':  'yellow',
                                     'ERROR':    'red',
                                     'CRITICAL': 'red',
                                     },
                                 secondary_log_colors={},
                                 style='%')
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logging.basicConfig(level=log_level, handlers=[handler])


