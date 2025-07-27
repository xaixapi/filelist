# cython: language_level=3
import asyncio
import copy
import gzip
import io
import json
import logging
import math
import mimetypes
import os
import random
import re
import socket
import string
import threading
import time
import urllib.parse
import zlib
from concurrent.futures import ThreadPoolExecutor
from functools import partial, reduce
from http.cookiejar import MozillaCookieJar
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import aiohttp
import chardet
import requests
from bs4 import BeautifulSoup
from lxml import etree
from requests.models import RequestEncodingMixin
from requests.structures import CaseInsensitiveDict
from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor
from tornado import httputil
from tornado.httpclient import HTTPClient, HTTPRequest, HTTPResponse

from .base_utils import Dict, DictWrapper, to_str, tqdm
from .cached_property import cached_property

logging.getLogger("requests").setLevel(logging.WARNING)


def patch_connection_pool(num_pools=100, maxsize=100):
    from urllib3 import connectionpool, poolmanager

    class MyHTTPSConnectionPool(connectionpool.HTTPSConnectionPool):
        def __init__(self, *args, **kwargs):
            kwargs.update(dict(maxsize=maxsize))
            super().__init__(*args, **kwargs)
    poolmanager.pool_classes_by_scheme['https'] = MyHTTPSConnectionPool

    class MyHTTPConnectionPool(connectionpool.HTTPConnectionPool):
        def __init__(self, *args, **kwargs):
            kwargs.update(dict(maxsize=maxsize))
            super().__init__(*args, **kwargs)
    poolmanager.pool_classes_by_scheme['http'] = MyHTTPConnectionPool


class ChunkedMultipartEncoderMonitor(MultipartEncoderMonitor):

    def __init__(self, encoder, callback=None, chunk_size=1048576):
        super().__init__(encoder, callback)
        self.len = None
        self.chunk_size = chunk_size
        self.chunks = int(math.ceil(self.encoder.len / chunk_size))

    def __iter__(self):
        for i in range(self.chunks):
            data = self.encoder.read(self.chunk_size)
            self.bytes_read += len(data)
            self.callback(self)
            yield data


class Response:

    def __init__(self, *args, decode_body=False, **kwargs):
        if args and isinstance(args[0], HTTPResponse):
            self.url = args[0].effective_url
            self.time = args[0].request_time
            for key in ['code', 'reason', 'headers', 'body', 'request']:
                setattr(self, key, getattr(args[0], key))
        else:
            kwargs.setdefault('body', b'')
            for k, v in kwargs.items():
                setattr(self, k, v)

        if hasattr(self, 'headers') and isinstance(self.headers, httputil.HTTPHeaders):
            self.cookies = Dict()
            if self.code == 200:
                sc = SimpleCookie()
                for cookie in self.headers.get_list('Set-Cookie'):
                    sc.load(cookie)
                self.cookies = Dict(map(lambda x: (x[0], x[1].value), sc.items()))
            self.headers = CaseInsensitiveDict(self.headers.items())
        elif not hasattr(self, 'headers'):
            self.headers = CaseInsensitiveDict()

        if decode_body:
            self.body = self._decompress(self.headers, self.body)

    @staticmethod
    def _decompress_gzip(body):
        gz = gzip.GzipFile(fileobj=io.BytesIO(body), mode='rb')
        return gz.read()

    @staticmethod
    def _decompress_zlib(body):
        try:
            return zlib.decompress(body, -zlib.MAX_WBITS)
        except Exception:
            return zlib.decompress(body)

    @staticmethod
    def _decompress(headers, body):
        try:
            encoding = headers.get('Content-Encoding')
            if encoding and encoding.lower().find('gzip') >= 0:
                return Response._decompress_gzip(body)
            elif encoding and encoding.lower().find('deflate') >= 0:
                return Response._decompress_zlib(body)
            else:
                try:
                    return Response._decompress_gzip(body)
                except Exception:
                    return Response._decompress_zlib(body)
        except Exception:
            return body

    @cached_property
    def encoding(self):
        encoding = None
        content_type = self.headers.get('content-type')
        if content_type and re.match('^text', content_type):
            content_type, params = self._parse_header(content_type)
            if 'charset' in params:
                encoding = params['charset'].strip("'\"")
            if encoding is None:
                encoding = chardet.detect(self.body)['encoding']
        return 'gbk' if encoding and encoding.lower() == 'gb2312' else encoding

    @staticmethod
    def _parse_header(line):
        parts = line.split(';')
        content_type = parts[0].strip()
        params = {}
        for param in parts[1:]:
            if '=' in param:
                key, value = param.split('=', 1)
                params[key.strip().lower()] = value.strip(' "')
        return content_type, params

    @cached_property
    def text(self):
        if self.encoding:
            return self.body.decode(self.encoding)
        else:
            return self.body.decode()

    def json(self, **kwargs):
        return DictWrapper(json.loads(self.text, **kwargs))

    def soup(self, features='html5lib', **kwargs):
        return BeautifulSoup(self.body, features=features, **kwargs)

    def html(self, **kwargs):
        return etree.HTML(self.soup().renderContents())

    def __repr__(self):
        return f'<Response({self.code}) [{self.reason}]>'


class RequestMeta(type):

    _dnscache = {}

    @classmethod
    def _set_dnscache(cls):

        def _getaddrinfo(*args, **kwargs):
            if args in cls._dnscache:
                return cls._dnscache[args]
            else:
                cls._dnscache[args] = socket._getaddrinfo(*args, **kwargs)
                return cls._dnscache[args]

        if not hasattr(socket, '_getaddrinfo'):
            socket._getaddrinfo = socket.getaddrinfo
            socket.getaddrinfo = _getaddrinfo

    def __new__(cls, name, bases, attrs):
        cls._set_dnscache()
        return type.__new__(cls, name, bases, attrs)


class UserAgent:

    def __init__(self, config='user_agent.json'):
        if Path(config).exists():
            self._user_agents = json.load(open(config))
            self._total_agents = reduce(lambda x, y: x + y, self._user_agents.values())

    def __getattr__(self, key):
        if key in self._user_agents:
            return random.choice(self._user_agents[key])
        else:
            return random.choice(self._total_agents)


class BaseRequest(metaclass=RequestMeta):

    def __init__(self, **kwargs):
        '''
        files: dict, { key: filename or file descriptor }
        proxy: str, 'http://user:password@114.112.93.35:8080'
        '''
        self.proxy = kwargs.get('proxy', os.environ.get('http_proxy'))
        self.cookie = kwargs.get('cookie')
        self.cookies = kwargs.get('cookies', {})
        self.timeout = kwargs.get('timeout', 30)
        self.retry = kwargs.get('retry', 0)
        self.sleep = kwargs.get('sleep', 1)
        self.raise_error = kwargs.get('raise_error', False)
        self.progress = kwargs.get('progress', False)
        self.logger = logging.getLogger()

        self.headers = {
            'accept': '*/*',
            'connection': 'keep-alive',
            'accept-encoding': 'gzip,deflate,sdch',
            'accept-language': 'zh-CN,zh;q=0.8,en;q=0.6',
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.132 Safari/537.36'
        }
        self.headers.update(kwargs.get('headers', {}))

        if self.cookie:
            root = Path(self.cookie).parent
            if not root.exists():
                root.mkdir(parents=True, exist_ok=True)

    def add_headers(self, headers):
        self.headers.update(headers)

    def add_cookies(self, cookies):
        self.cookies.update(cookies)

    def set_spider_ua(self):
        self.headers['user-agent'] = 'Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)'

    def set_chrome_ua(self):
        self.headers['user-agent'] = 'Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/76.0.3809.87 Mobile Safari/537.36'

    def set_mobile_ua(self):
        self.headers['user-agent'] = 'Mozilla/5.0 (iPhone; CPU iPhone OS 12_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/12.0 Mobile/15E148 Safari/604.1'

    def _prepare(self, url, kwargs):
        url = urllib.parse.quote(to_str(url), safe=string.printable)
        kwargs = copy.copy(kwargs)
        headers = copy.copy(self.headers)
        headers.update(kwargs.get('headers', {}))
        cookies = copy.copy(self.cookies)
        cookies.update(kwargs.get('cookies', {}))

        kwargs.setdefault('progress', self.progress)
        kwargs.setdefault('proxy', self.proxy)
        kwargs.setdefault('timeout', self.timeout)
        kwargs.setdefault('data', {})
        kwargs.setdefault('files', {})
        kwargs.setdefault('method', 'post' if kwargs['data'] or kwargs['files'] else 'get')
        kwargs['method'] = kwargs['method'].upper()
        if kwargs['method'] == 'HEAD':
            kwargs['progress'] = False
            kwargs.setdefault('allow_redirects', False)
        else:
            kwargs.setdefault('allow_redirects', True)

        if kwargs.get('json'):
            headers['content-type'] = 'application/json; charset=utf-8'
            if isinstance(kwargs['json'], dict):
                kwargs['data'] = json.dumps(kwargs['json'], ensure_ascii=False).encode('utf-8')
            elif isinstance(kwargs['data'], dict):
                kwargs['data'] = json.dumps(kwargs['data'], ensure_ascii=False).encode('utf-8')

        if isinstance(kwargs['data'], dict):
            # headers['content-type'] = 'application/x-www-form-urlencoded'
            kwargs['data'] = list(kwargs['data'].items())

        if isinstance(kwargs['files'], dict):
            kwargs['files'] = list(kwargs['files'].items())

        for i, (key, value) in enumerate(kwargs['files']):
            if isinstance(value, (str, bytes, Path)):
                value = [str(value), open(value, 'rb')]
            elif isinstance(value, io.IOBase):
                value = [value.name if hasattr(value, 'name') else 'file', value]
            elif isinstance(value, tuple):
                value = list(value)
            if len(value) == 2:
                mtype = mimetypes.guess_type(value[0])[0] or 'application/octet-stream'
                value.append(mtype)
            value[0] = Path(value[0]).name
            kwargs['files'][i] = (key, tuple(value))

        if isinstance(kwargs.get('params'), dict):
            ret = urlparse(url)
            query = parse_qs(ret.query)
            query.update(kwargs['params'])
            url = urlunparse((ret.scheme, ret.netloc, ret.path, ret.params, urlencode(query, doseq=True), ret.fragment))
            kwargs.pop('params')

        if url.startswith('//'):
            url = 'http:' + url
        if kwargs.pop('autoreferer', False):
            ret = urllib.parse.urlparse(url)
            headers.update({'referer': f'{ret.scheme}://{ret.netloc}'})
        kwargs['headers'] = headers
        kwargs['cookies'] = cookies
        return url, kwargs

    def _finish(self, kwargs):
        if hasattr(self, '_pbar'):
            self._pbar.close()
        for key, value in kwargs.get('files', []):
            if not value[1].closed:
                value[1].close()

    def _request(self, url, **kwargs):
        raise NotImplementedError

    def _retry(self, url, kwargs, retry):
        reason = 'OK'
        for i in range(retry + 1):
            try:
                return self._request(url, **kwargs)
            except Exception as e:
                reason = str(e)
                self.logger.warning(f'url: {url}, retry: {i}, exception: {e}')
                time.sleep(self.sleep)

        return Response(url=url, code=599, reason=reason)

    def request(self, url, **kwargs):
        raise_error = kwargs.pop('raise_error', self.raise_error)
        retry = kwargs.pop('retry', self.retry)
        if raise_error:
            return self._request(url, **kwargs)
        else:
            return self._retry(url, kwargs, retry)

    def _download(self, url, kwargs, fp, lock, start, end):
        raise NotImplementedError

    def download(self, url, filename=None, shards=1, **kwargs):
        filename = Path(filename or Path(url).name)
        if shards > 1:
            url, kwargs = self._prepare(url, kwargs)
            kwargs['headers']['Range'] = 'bytes=0-0'
            resp = self.request(url, **kwargs)
            filesize = int(resp.headers['Content-Range'].split('/')[-1])
            block = int(math.ceil(filesize / shards))
            chunks = [(i * block, min(filesize, (i + 1) * block) - 1) for i in range(shards)]
            args = list(zip(*chunks))
            filename.parent.mkdir(parents=True, exist_ok=True)
            with open(filename, 'wb') as fp:
                fp.truncate(filesize)
            fp = open(filename, 'rb+')
            lock = threading.Lock()
            func = partial(self._download, url, kwargs, fp, lock)
            with ThreadPoolExecutor(shards) as executor:
                results = executor.map(func, *args)
            fp.close()
            ret = all(results)
            if not ret:
                filename.unlink()
            return ret
        else:
            resp = self.request(url, **kwargs)
            if resp.code == 200:
                filename.parent.mkdir(parents=True, exist_ok=True)
                filename.write_bytes(resp.body)
            return resp.code == 200

    def get(self, url, **kwargs):
        return self.request(url, method='get', **kwargs)

    def post(self, url, **kwargs):
        return self.request(url, method='post', **kwargs)

    def head(self, url, **kwargs):
        return self.request(url, method='head', **kwargs)

    def put(self, url, **kwargs):
        return self.request(url, method='put', **kwargs)

    def delete(self, url, **kwargs):
        return self.request(url, method='delete', **kwargs)

    def options(self, url, **kwargs):
        return self.request(url, method='options', **kwargs)

    def patch(self, url, **kwargs):
        return self.request(url, method='patch', **kwargs)

    @staticmethod
    def multipart(headers={}, data={}, files={}):
        body, content_type = RequestEncodingMixin._encode_files(files, data)
        headers['Content-Type'] = content_type
        return body


class AsyncRequest(BaseRequest):

    async def _retry(self, url, kwargs, retry):
        reason = 'OK'
        for i in range(retry + 1):
            try:
                return await self._request(url, **kwargs)
            except Exception as e:
                reason = str(e)
                self.logger.warning(f'url: {url}, retry: {i}, exception: {e}')
                await asyncio.sleep(self.sleep)
        return Response(url=url, code=599, reason=reason)

    async def _download(self, url, kwargs, fp, lock, start, end):
        kwargs = copy.deepcopy(kwargs)
        kwargs['headers']['Range'] = f'bytes={start}-{end}'
        resp = await self._request(url, **kwargs)
        if resp.code == 206:
            async with lock:
                fp.seek(start)
                fp.write(resp.body)
        return resp.code == 206

    async def download(self, url, filename=None, shards=1, **kwargs):
        filename = Path(filename or Path(url).name)
        filename.parent.mkdir(parents=True, exist_ok=True)
        if shards > 1:
            url, kwargs = self._prepare(url, kwargs)
            kwargs['headers']['Range'] = 'bytes=0-0'
            resp = await self.request(url, **kwargs)
            filesize = int(resp.headers['Content-Range'].split('/')[-1])
            block = int(math.ceil(filesize / shards))
            chunks = [(i * block, min(filesize, (i + 1) * block) - 1) for i in range(shards)]
            filename.parent.mkdir(parents=True, exist_ok=True)
            with open(filename, 'wb') as fp:
                fp.truncate(filesize)
            fp = open(filename, 'rb+')
            lock = asyncio.Lock()
            futures = [self._download(url, kwargs, fp, lock, start, end) for start, end in chunks]
            results = await asyncio.gather(*futures)
            fp.close()
            ret = all(results)
            if not ret:
                filename.unlink()
            return ret
        else:
            resp = await self.request(url, **kwargs)
            if resp.code == 200:
                filename.parent.mkdir(parents=True, exist_ok=True)
                filename.write_bytes(resp.body)
            return resp.code == 200


class Requests(BaseRequest):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.session = requests.session()
        self.session.cookies = MozillaCookieJar(filename=self.cookie)
        adapter = requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.load_cookie()

    def __del__(self):
        try:
          asyncio.run(self.session.close())
        except RuntimeError:
          pass

    def load_cookie(self):
        if self.cookie and Path(self.cookie).exists():
            return self.session.cookies.load(ignore_discard=True, ignore_expires=True)

    def save_cookie(self):
        if self.cookie:
            return self.session.cookies.save(ignore_discard=True, ignore_expires=True)

    def load_proxy(self):
        if self.proxy:
            self.session.proxies = {'http': self.proxy, 'https': self.proxy}

    def _prepare(self, url, kwargs):
        url, kwargs = super()._prepare(url, kwargs)
        kwargs.setdefault('verify', False)
        chunked = kwargs.pop('chunked', False)
        chunk_size = kwargs.pop('chunk_size', 1048576)

        proxy = kwargs.pop('proxy')
        if proxy:
            kwargs['proxies'] = {'http': proxy, 'https': proxy}

        progress = kwargs.pop('progress')
        if progress:
            if kwargs['method'] == 'GET':
                kwargs['stream'] = True
            else:
                data, files = kwargs.pop('data'), kwargs.pop('files')
                encoder = MultipartEncoder(fields=data + files)
                self._pbar = tqdm(unit='B', unit_scale=True, total=encoder.len)
                if chunked:
                    monitor = ChunkedMultipartEncoderMonitor(encoder, lambda m: self._pbar.update(m.bytes_read), chunk_size=chunk_size)
                else:
                    monitor = MultipartEncoderMonitor(encoder, lambda m: self._pbar.update(m.bytes_read))
                kwargs['headers'].update({'Content-Type': encoder.content_type})
                kwargs['data'] = monitor

        return url, kwargs

    def _download(self, url, kwargs, fp, lock, start, end):
        kwargs = copy.copy(kwargs)
        kwargs['headers']['Range'] = f'bytes={start}-{end}'
        kwargs['stream'] = True
        method = kwargs.pop('method', 'GET').upper()
        resp = self.session.request(method, url, **kwargs)
        if resp.status_code == 206:
            pbar = tqdm(total=end - start + 1, unit='B', unit_scale=True, desc=f'{start}')
            for chunk in resp.iter_content(chunk_size=4096):
                if chunk:
                    with lock:
                        fp.seek(start)
                        fp.write(chunk)
                        start += len(chunk)
                        pbar.update(incr=len(chunk))
            pbar.close()
        return resp.status_code == 206

    def _request(self, url, **kwargs):
        url, kwargs = self._prepare(url, kwargs)

        cookies = requests.utils.dict_from_cookiejar(self.session.cookies)
        cookies.update(kwargs['cookies'])
        request = Dict(
            url=url,
            headers=kwargs['headers'],
            data=kwargs['data'],
            cookies=cookies,
        )

        method = kwargs.pop('method', 'GET')
        resp = self.session.request(method, url, **kwargs)
        if kwargs.get('stream'):
            self._pbar = tqdm(total=int(resp.headers.get('Content-Length', -1)), unit='B', unit_scale=True)
            content = b''
            for chunk in resp.iter_content(chunk_size=4096):
                if chunk:
                    content += chunk
                    self._pbar.update(incr=len(chunk))
        else:
            content = resp.content
        self._finish(kwargs)
        self.save_cookie()

        response = Response(
            url=resp.url,
            encoding=resp.encoding,
            headers=resp.headers,
            body=content,
            code=resp.status_code,
            cookies=requests.utils.dict_from_cookiejar(resp.cookies),
            reason=resp.reason,
            request=request,
            time=resp.elapsed.microseconds / 1e6,
        )
        return response

class Aiohttp(AsyncRequest):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.session = None

    async def create_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()

    async def close_session(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def __aenter__(self):
        await self.create_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()

    def save_cookie(self):
        if self.cookie and self.session:
            self.session.cookie_jar.save(self.cookie)

    def load_cookie(self):
        if self.cookie and Path(self.cookie).exists():
            self.session.cookie_jar.load(self.cookie)

    async def _request(self, url, **kwargs):
        await self.create_session()
        url, kwargs = self._prepare(url, kwargs)
        kwargs.setdefault('ssl', kwargs.pop('verify_ssl', False))
        kwargs.setdefault('raise_for_status', True)
        kwargs.pop('progress', None)
        kwargs.pop('chunked', None)
        files = kwargs.pop('files', None)
        if files:
            data = aiohttp.FormData()
            for k, v in kwargs.get('data', []):
                data.add_field(k, v)
            for k, v in files:
                data.add_field(k, v[1], filename=v[0], content_type=v[2])
            kwargs['data'] = data

        cookies = self.session.cookie_jar.filter_cookies(url)
        cookies = dict(map(lambda x: (x[0], x[1].value), cookies.items()))
        cookies.update(kwargs.get('cookies', {}))
        request = Dict(
            url=url,
            headers=kwargs.get('headers'),
            data=kwargs.get('data'),
            cookies=cookies,
        )

        method = kwargs.pop('method', 'GET').upper()
        async with self.session.request(method, url, **kwargs) as resp:
            body = await resp.read()
            self._finish(kwargs)
            self.save_cookie()
            response = Response(
                url=str(resp.url),
                headers=resp.headers,
                body=body,
                code=resp.status,
                reason=resp.reason,
                cookies=dict(resp.cookies),
                request=request,
            )
        return response

class Fetcher(BaseRequest):

    def __init__(self, phantomjs_proxy='http://localhost:25555', **kwargs):
        super().__init__(**kwargs)
        self.phantomjs_proxy = phantomjs_proxy
        self.default_options = {
            'method': 'GET',
            'headers': self.headers,
            'follow_redirects': True,
            'use_gzip': True,
            'timeout': self.timeout,
        }
        self.http = HTTPClient(max_clients=20)

    def parse_option(self, url, **kwargs):
        fetch = copy.copy(self.default_options)
        fetch['method'] = kwargs['method'].upper()
        fetch['url'] = url
        fetch['data'] = kwargs['data']
        fetch['headers'] = kwargs['headers']
        fetch['timeout'] = kwargs['timeout']
        js_script = kwargs.get('js_script')
        if js_script:
            fetch['js_script'] = js_script
            fetch['js_run_at'] = kwargs.get('js_run_at', 'document-end')
        fetch['load_images'] = kwargs.get('load_images', False)
        return fetch

    def _request(self, url, **kwargs):
        url, kwargs = self._prepare(url, kwargs)
        body = self.parse_option(url, **kwargs)
        config = {
            'follow_redirects': False,
            'connect_timeout': body['timeout'],
            'request_timeout': body['timeout'] + 1,
        }
        request = HTTPRequest(self.phantomjs_proxy, method='POST', body=json.dumps(body), **config)
        resp = self.http.fetch(request)

        request = Dict(
            url=url,
            headers=kwargs.get('headers'),
            data=kwargs.get('data'),
        )
        doc = Dict(json.loads(resp.body))
        response = Response(
            url=url,
            headers=doc.headers,
            body=doc.content,
            code=doc.status_code,
            cookies=doc.cookies,
            reason=doc.error or 'OK',
        )
        return response


def Request(lib='requests', **kwargs):
    module = {
        'requests': Requests,
        'aiohttp': Aiohttp,
    }
    return module[lib](**kwargs)
