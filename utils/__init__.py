import warnings

from .base_utils import (DefaultDict, Dict, DictUnwrapper,
                         DictWrapper, JSONEncoder, Singleton,
                         awaitable, ceil, connect, floor, get_ip, int2ip,
                         int2str, ip2int, str2int, to_bytes, to_str,
                         tqdm, yaml)
from .cached_property import cached_property
from .db_utils import (AioRedis, Mongo, MongoClient, Motor,
                       MotorClient, Redis, parse_uri)
from .decorator import aioretry, retry, smart_decorator, synchronize, timeit
from .email_utils import AioEmail, Email
from .http_utils import Response, patch_connection_pool
from .log_utils import Logger, WatchedFileHandler

try:
    import pycurl  # noqa

    from .curl_utils import Request
except:
    from .http_utils import Request

warnings.filterwarnings("ignore")

__all__ = [
    'awaitable', 'floor', 'ceil', 'to_str', 'to_bytes', 'tqdm', 'yaml',
    'timeit', 'retry', 'aioretry', 'smart_decorator', 'synchronize', 'cached_property',
    'get_ip', 'connect', 'ip2int', 'int2ip', 'int2str', 'str2int', 'patch_connection_pool', 'parse_uri',
    'Singleton', 'JSONEncoder', 'Dict', 'DefaultDict', 'DictWrapper', 'DictUnwrapper',
    'Email', 'AioEmail', 'Logger', 'WatchedFileHandler',
    'Mongo', 'MongoClient', 'Redis', 'AioRedis', 'Motor', 'MotorClient',
    'Request', 'Response'
]
