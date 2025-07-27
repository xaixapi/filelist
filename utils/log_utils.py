# cython: language_level=3

import logging
from functools import lru_cache
from pathlib import Path

from coloredlogs import BasicFormatter, ColoredFormatter


class WatchedFileHandler(logging.handlers.WatchedFileHandler):
    def emit(self, record):
        if record.levelno == self.level:
            super(WatchedFileHandler, self).emit(record)


class CustomAdapter(logging.LoggerAdapter):

    def process(self, msg, kwargs):
        return f"{self.extra} {msg}", kwargs


FIELD_STYLES = dict(
    asctime=dict(color='cyan'),
    hostname=dict(color='magenta'),
    levelname=dict(color='black', bold=True),
    filename=dict(color='magenta'),
    name=dict(color='blue'),
    threadName=dict(color='green'),
    module=dict(color='magenta'),
)

LEVEL_STYLES = dict(
    debug=dict(color='blue'),
    info=dict(color='green'),
    warning=dict(color='yellow'),
    error=dict(color='red'),
    critical=dict(color='red', bold=True),
)


@lru_cache()
def Logger(filename=None, stream=True, name=None, level='INFO', prefix=None, split=False):
    logger = logging.getLogger(name)
    if logger.handlers and logger.handlers[0].name == 'Logger':
        return logger

    logger.setLevel(level.upper())
    logger.propagate = False
    logger.handlers = []
    logfmt = '[%(levelname)1.1s %(asctime)s %(module)s:%(lineno)d] %(message)s'

    if stream:
        handler = logging.StreamHandler()
        handler.setFormatter(ColoredFormatter(logfmt, level_styles=LEVEL_STYLES, field_styles=FIELD_STYLES))
        handler.name = 'Logger'
        logger.addHandler(handler)

    if filename and split:
        level_map = logging.getLevelNamesMapping()
        for x in ['DEBUG', 'INFO', 'WARN', 'ERROR']:
            if level_map[x] >= level_map[level.upper()]:
                file = Path(filename).parent / f"{Path(filename).stem}.{x.lower()}{Path(filename).suffix}"
                handler = WatchedFileHandler(filename=str(file), mode='a', encoding='utf-8')
                handler.setFormatter(BasicFormatter(logfmt))
                handler.setLevel(x)
                handler.name = 'Logger'
                logger.addHandler(handler)
    elif filename:
        handler = logging.handlers.WatchedFileHandler(filename=filename, mode='a', encoding='utf-8')
        handler.setFormatter(BasicFormatter(logfmt))
        handler.setLevel(level.upper())
        handler.name = 'Logger'
        logger.addHandler(handler)

    if prefix:
        logger = CustomAdapter(logger, prefix)

    return logger
