# cython: language_level=3
import asyncio
import os
import re
from urllib.parse import parse_qs, quote_plus, unquote_plus

import pymongo
import redis
from bson.son import SON
from motor import core
from motor.docstrings import get_database_doc
from motor.frameworks import asyncio as asyncio_framework
from motor.metaprogramming import (AsyncCommand, DelegateMethod,
                                   coroutine_annotation,
                                   create_class_with_framework,
                                   unwrap_args_session, unwrap_kwargs_session)
from redis import asyncio as aioredis

from .base_utils import Dict

__all__ = ['Mongo', 'MongoClient', 'Redis', 'AioRedis', 'Motor', 'MotorClient', 'parse_uri']


def parse_uri(uri):
    pattern = re.compile(r'''
        (?P<schema>[\w\+]+)://
        (?:
            (?P<user>[^:/]*)
            (?::(?P<password>[^/]*))?
        @)?
        (?:
            (?P<host>[^/:]*)
            (?::(?P<port>[^/]*))?
        )?
        (?:/(?P<db>\w*))?
        (?:\?(?P<extra>.*))?
        ''', re.X)

    m = pattern.match(uri)
    assert m is not None, 'Could not parse rfc1738 URL'
    kwargs = Dict()
    for k, v in m.groupdict().items():
        if v is None:
            continue
        if k == 'extra':
            ret = parse_qs(v)
            ret = {_k: _v[0] if len(_v) == 1 else _v for _k, _v in ret.items()}
            kwargs.update(ret)
        elif k == 'port':
            kwargs[k] = int(v)
        else:
            kwargs[k] = unquote_plus(v)

    if kwargs.schema == 'redis' and kwargs.db:
        kwargs.db = int(kwargs.db)
    return kwargs


class Cursor(pymongo.cursor.Cursor):

    def count(self, with_limit_and_skip=False):
        cmd = SON([("count", self.__collection.name),
                   ("query", self.__spec)])
        if self.__max_time_ms is not None:
            cmd["maxTimeMS"] = self.__max_time_ms
        if self.__comment:
            cmd["comment"] = self.__comment

        if self.__hint is not None:
            cmd["hint"] = self.__hint

        if with_limit_and_skip:
            if self.__limit:
                cmd["limit"] = self.__limit
            if self.__skip:
                cmd["skip"] = self.__skip

        return self.__collection._count(
            cmd, self.__collation, session=self.__session)


class Collection(pymongo.collection.Collection):

    def find(self, *args, **kwargs):
        # kwargs.update({'no_cursor_timeout': True})
        return Cursor(self, *args, **kwargs)

    def _count(self, cmd, collation=None, session=None):
        """Internal count helper."""
        # XXX: "ns missing" checks can be removed when we drop support for
        # MongoDB 3.0, see SERVER-17051.
        def _cmd(session, server, sock_info, secondary_ok):
            return self._count_cmd(
                session, sock_info, secondary_ok, cmd, collation)

        return self.__database.client._retryable_read(
            _cmd, self._read_preference_for(session), session)

    @property
    def seq_id(self):
        ret = self.database.ids.find_one_and_update({'_id': self.name},
                                                    {'$inc': {'seq': 1}},
                                                    upsert=True,
                                                    projection={'seq': True, '_id': False},
                                                    return_document=True)
        return ret['seq']


class Database(pymongo.database.Database):

    def __getitem__(self, name):
        return Collection(self, name)


class MongoClient(pymongo.MongoClient):

    def __init__(self, **kwargs):
        if kwargs.get('uri'):
            uri = kwargs.pop('uri')
        elif os.environ.get('MONGO_URI'):
            uri = os.environ.get('MONGO_URI')
        else:
            host = os.environ.get('MONGO_HOST', 'localhost')
            port = os.environ.get('MONGO_PORT', 27017)
            user = os.environ.get('MONGO_USER', None)
            password = os.environ.get('MONGO_PWD', None)
            uri = f"mongodb://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}" if user and password else f"mongodb://{host}:{port}"

        kwargs.pop('uri', None)
        kwargs.setdefault('document_class', Dict)
        super(MongoClient, self).__init__(uri, **kwargs)

    def __getitem__(self, name):
        return Database(self, name)

    def __getattr__(self, name):
        return Database(self, name)


class Mongo(Database):

    def __init__(self, db=None, **kwargs):
        client = MongoClient(**kwargs)
        name = db or client.get_default_database().name
        super(Mongo, self).__init__(client, name)


class AgnosticCursor(core.AgnosticCursor):
    __delegate_class__ = Cursor

    @coroutine_annotation
    def to_list(self, length=None):
        return super(core.AgnosticCursor, self).to_list(length)


class AgnosticCollection(core.AgnosticCollection):
    __delegate_class__ = Collection

    def __init__(self, database, name, codec_options=None,
                 read_preference=None, write_concern=None, read_concern=None,
                 _delegate=None):
        db_class = create_class_with_framework(
            AgnosticDatabase, self._framework, self.__module__)

        if not isinstance(database, db_class):
            raise TypeError("First argument to MotorCollection must be "
                            "MotorDatabase, not %r" % database)

        delegate = _delegate or Collection(
            database.delegate, name, codec_options=codec_options,
            read_preference=read_preference, write_concern=write_concern,
            read_concern=read_concern)

        super(core.AgnosticBaseProperties, self).__init__(delegate)
        self.database = database

    def __getitem__(self, name):
        collection_class = create_class_with_framework(
            AgnosticCollection, self._framework, self.__module__)

        return collection_class(self.database, self.name + '.' + name,
                                _delegate=self.delegate[name])

    def find(self, *args, **kwargs):
        # kwargs.update({'no_cursor_timeout': True})
        cursor = self.delegate.find(*unwrap_args_session(args),
                                    **unwrap_kwargs_session(kwargs))
        cursor_class = create_class_with_framework(
            AgnosticCursor, self._framework, self.__module__)

        return cursor_class(cursor, self)

    @property
    async def seq_id(self):
        ret = await self.database.ids.find_one_and_update({'_id': self.name},
                                                          {'$inc': {'seq': 1}},
                                                          upsert=True,
                                                          projection={'seq': True, '_id': False},
                                                          return_document=True)
        return ret['seq']


class AgnosticDatabase(core.AgnosticDatabase):
    __delegate_class__ = Database

    create_collection = AsyncCommand().wrap(Collection)
    get_collection = DelegateMethod().wrap(Collection)

    def __init__(self, client, name, **kwargs):
        self._client = client
        delegate = kwargs.get('_delegate') or Database(
            client.delegate, name, **kwargs)

        super(core.AgnosticBaseProperties, self).__init__(delegate)

    def __getitem__(self, name):
        collection_class = create_class_with_framework(
            AgnosticCollection, self._framework, self.__module__)

        return collection_class(self, name)


class AgnosticClient(core.AgnosticClient):
    __delegate_class__ = MongoClient
    get_database = DelegateMethod(doc=get_database_doc).wrap(Database)

    def __getitem__(self, name):
        db_class = create_class_with_framework(
            AgnosticDatabase, self._framework, self.__module__)
        return db_class(self, name)


def create_asyncio_class(cls):
    asyncio_framework.CLASS_PREFIX = ''
    return create_class_with_framework(cls, asyncio_framework, 'db_utils')


MotorClient = create_asyncio_class(AgnosticClient)
MotorDatabase = create_asyncio_class(AgnosticDatabase)
MotorCollection = create_asyncio_class(AgnosticCollection)


class Motor(MotorDatabase):

    def __init__(self, db=None, **kwargs):
        client = MotorClient(**kwargs)
        name = db or client.get_default_database().name
        super(Motor, self).__init__(client, name)


class Redis(redis.StrictRedis):

    def __init__(self, **kwargs):
        if any([key in kwargs for key in ['host', 'port', 'password']]):
            host = kwargs.pop('host', 'localhost')
            port = kwargs.pop('port', 6379)
            password = kwargs.pop('password', None)
            db = kwargs.pop('db', 0)
            uri = f"redis://:{password}@{host}:{port}/{db}" if password else f"redis://{host}:{port}/{db}"
        elif kwargs.get('uri'):
            uri = kwargs.pop('uri')
        elif os.environ.get('REDIS_URI'):
            uri = os.environ['REDIS_URI']
        else:
            host = os.environ.get("REDIS_HOST", 'localhost')
            port = int(os.environ.get("REDIS_PORT", 6379))
            password = os.environ.get("REDIS_PWD", None)
            db = int(os.environ.get("REDIS_DB", 0))
            uri = f"redis://:{password}@{host}:{port}/{db}" if password else f"redis://{host}:{port}/{db}"

        kwargs.pop('uri', None)
        kwargs.setdefault('decode_responses', True)
        kwargs.setdefault('health_check_interval', 60)
        pool = redis.ConnectionPool.from_url(uri, **kwargs)
        super().__init__(connection_pool=pool)

    def clear(self, pattern='*'):
        if pattern == '*':
            self.flushdb()
        else:
            keys = [x for x in self.scan_iter(pattern)]
            if keys:
                self.delete(*keys)


class AioRedis(aioredis.StrictRedis):

    def __init__(self, **kwargs):
        if any([key in kwargs for key in ['host', 'port', 'password']]):
            host = kwargs.pop('host', 'localhost')
            port = kwargs.pop('port', 6379)
            password = kwargs.pop('password', None)
            db = kwargs.pop('db', 0)
            uri = f"redis://:{password}@{host}:{port}/{db}" if password else f"redis://{host}:{port}/{db}"
        elif kwargs.get('uri'):
            uri = kwargs.pop('uri')
        elif os.environ.get('REDIS_URI'):
            uri = os.environ['REDIS_URI']
        else:
            host = os.environ.get("REDIS_HOST", 'localhost')
            port = int(os.environ.get("REDIS_PORT", 6379))
            password = os.environ.get("REDIS_PWD", None)
            db = int(os.environ.get("REDIS_DB", 0))
            uri = f"redis://:{password}@{host}:{port}/{db}" if password else f"redis://{host}:{port}/{db}"

        kwargs.pop('uri', None)
        kwargs.setdefault('decode_responses', True)
        pool = aioredis.ConnectionPool.from_url(uri, **kwargs)
        super().__init__(connection_pool=pool)

    async def clear(self, pattern='*'):
        if pattern == '*':
            await self.flushdb()
        else:
            keys = [key async for key in self.scan_iter(pattern)]
            if keys:
                await self.delete(*keys)
