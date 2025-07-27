# cython: language_level=3
import asyncio
import datetime
import functools
import hashlib
import io
import json
import math
import os
import re
import shutil
import string
import tarfile
import tempfile
import time
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import markdown
import tornado.web
import yaml
from bson import ObjectId
from tornado.concurrent import run_on_executor
from tornado_utils import BaseHandler, Blueprint
from tornado.web import HTTPError
from utils import Dict

bp = Blueprint(__name__)

def check_auth(method):
    @functools.wraps(method)
    async def wrapper(self, name, *args, **kwargs):
        auth_header = self.request.headers.get('Authorization')
        expected_api_key = os.environ.get('API_KEY','sk-xaixapi')
        if auth_header:
            parts = auth_header.split()
            if len(parts) >=2:
                api_key = parts[-1]
                if api_key == expected_api_key:
                    return await method(self, name, *args, **kwargs)
                else:
                    raise HTTPError(403, reason="Unauthorized")
            else:
                raise HTTPError(400, reason="Unauthorized")
        else:
            if await self.check(name):
                await method(self, name, *args, **kwargs)
            elif self.request.method in ['GET', 'HEAD']:
                self.redirect(self.get_login_url())
            else:
                self.finish({'err': 1, 'msg': 'Unauthorized'})

    return wrapper


class BaseHandler(BaseHandler):

    executor = ThreadPoolExecutor(10)

    default = {
        'ppt.png': ['.ppt', '.pptx'],
        'word.png': ['.doc', '.docx'],
        'excel.png': ['.xls', '.xlsx'],
        'pdf.png': ['.pdf'],
        'txt.png': ['.txt','.log','.m3u','.lrc','.tsx','.pem', '.key'],
        'vue.png': ['.vue'],
        'exe.png': ['.exe'],
        'mac.png': ['.dmg', '.pkg'],
        'apk.png': ['.apk'],
        'iso.png': ['.iso'],
        'json.png': ['.json', '.yml', '.yaml'],
        'ini.png': ['.ini'],
        'markdown.png': ['.md','.markdown'],
        'kindle.png': ['.azw3', '.epub'],
        'database.png': ['.db', '.sql'],
        'image.png': ['.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.svg', '.ai','.ico'],
        'audio.png': ['.amr', '.ogg', '.wav', '.mp3', '.flac', '.ape', '.m4a'],
        'video.png': ['.rmvb', '.rm', '.mkv', '.mp4', '.m4v', '.avi', '.wmv', '.flv', '.m3u8','.mov'],
        'zip.png': ['.rar', '.tar', '.tgz', '.gz', '.bz2', '.xz', '.zip', '.7z', '.z'],
        'c.png': ['.c', '.h'],
        'cpp.png': ['.cpp'],
        'csharp.png': ['.cs'],
        'python.png': ['.py', '.pyc'],
        'bash.png': ['.sh'],
        'go.png': ['.go'],
        'java.png': ['.java', '.javac', '.class', '.jar'],
        'javascript.png': ['.js','.ts'],
        'html.png': ['.html'],
        'css.png': ['.css', '.less', '.sass', '.scss'],
    }
    icon = {}
    for key, value in default.items():
        for v in value:
            icon[v] = key

    @staticmethod
    def convert_size(size):
        if size / (1024 * 1024 * 1024.0) >= 1:
            return '%.1f GB' % (size / (1024 * 1024 * 1024.0))
        elif size / (1024 * 1024.0) >= 1:
            return '%.1f MB' % (size / (1024 * 1024.0))
        else:
            return '%.1f KB' % (size / 1024.0)

    @staticmethod
    def convert_time(mtime):
        if isinstance(mtime, (int, float)):
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
        elif isinstance(mtime, datetime.datetime):
            return mtime.strftime("%Y-%m-%d %H:%M:%S")
        else:
            return mtime

@bp.route('/')
class IndexHandler(BaseHandler):

    def get(self):
        if self.app.options.auth:
            if self.current_user:
                self.redirect(f'/disk/{self.current_user.id}')
            else:
                self.redirect('/disk/0')
        else:
            self.redirect('/disk')


@bp.route('/set')
class AdminHandler(BaseHandler):

    @tornado.web.authenticated
    async def get(self):
        self.app.get_email_list()
        self.app.get_system_info()
        await self.app.get_db_info()
        self.render('set.html')

    @tornado.web.authenticated
    async def post(self):
        if self.args.kindle and self.args.kindle != self.current_user.kindle:
            code = await self.rd.get(f'{self.prefix}:code:{self.args.kindle}')
            if self.args.kindle and not (code and code == self.args.code):
                return self.finish({'err': 1, 'msg': '验证码不正确'})

        update = {
            'public': self.args.public == 'on'
        }
        if self.args.kindle:
            update['kindle'] = self.args.kindle
        await self.db.users.update_one({'_id': self.current_user._id}, {'$set': update})

        if self.args.whitelist:
            await self.rd.delete(f'{self.prefix}:Email_Whitelist')
            await self.rd.sadd(f'{self.prefix}:Email_Whitelist',*(self.args.whitelist.split(',')))
        else:
            await self.rd.delete(f'{self.prefix}:Email_Whitelist')
        if self.args.blacklist:
            await self.rd.delete(f'{self.prefix}:Email_Blacklist')
            await self.rd.sadd(f'{self.prefix}:Email_Blacklist',*(self.args.blacklist.split(',')))
        else:
            await self.rd.delete(f'{self.prefix}:Email_Blacklist')

        await self.rd.save()

        self.finish({'err': 0})

@bp.route('/manage')
class ManageHandler(BaseHandler):

    @tornado.web.authenticated
    async def get(self):
        if not self.current_user.admin:
            raise tornado.web.HTTPError(403)

        query = self.get_args()
        if query.q:
            if query.q.isdigit():
                query = Dict({'id': query.q})
            else:
                if query.q == '管理员':
                    query = Dict({'admin': True})
                elif query.q == '公开':
                    query = Dict({'public': True})
                else:
                    query = Dict({'email': {'$regex': re.compile(query.q)}}) if query.q.find('@') >= 0 else Dict({'username': {'$regex': re.compile(query.q)}})
        entries = await self.query('users', query, schema={'id': int})
        self.render('manage.html', entries=entries)

    @tornado.web.authenticated
    async def post(self):
        if not self.current_user.admin:
            return self.finish({'err': 1, 'msg': '当前用户无权限'})

        _id = self.get_argument('id', None)
        if not _id:
            return self.finish({'err': 1, 'msg': '用户未指定'})
        user = await self.db.users.find_one({'_id': ObjectId(_id)})
        if not user:
            return self.finish({'err': 1, 'msg': '用户不存在'})
        if user._id == self.current_user._id:
            return self.finish({'err': 1, 'msg': '禁止执行'})
        if self.args.action == 'delete':
            await self.db.users.delete_one({'_id': user._id})
        elif self.args.action == 'deny':
            await self.db.users.update_one({'_id': user._id}, {'$set': {'deny': True}})
        elif self.args.action == 'toggle':
            if user.admin:
                await self.db.users.update_one({'_id': user._id}, {'$unset': {'admin': 1}})
            else:
                await self.db.users.update_one({'_id': user._id}, {'$set': {'admin': True}})

        self.finish({'err': 0})

@bp.route('/share/?(.*)')
class ShareHandler(BaseHandler):

    async def get(self, name):
        if self.app.options.auth:
            if not name:
                if self.current_user:
                    return self.redirect(f'/share/{self.current_user.id}')
                else:
                    return self.redirect('/disk')

            if self.current_user:
                token = self.args.token if self.current_user.admin and self.args.token else self.current_user.token
                query = self.get_args()
                docs = await self.query('share', {'token': token,'name':{'$regex': re.compile(query.q)}}) if query.q else await self.query('share', {'token': token})
                entries = []
                for doc in docs:
                    path = self.root / doc.name
                    if doc.expired_at and doc.expired_at < datetime.datetime.now() or not path.exists():
                        Id = str(doc._id)
                        link = await self.rd.get(f'{self.prefix}:LINK:{Id}')
                        await self.rd.delete(f'{self.prefix}:LINK:{link}')
                        await self.rd.delete(f'{self.prefix}:LINK:{Id}')
                        await self.db.share.delete_one({'_id': doc._id})
                    else:
                        entries.append(Dict({
                            'path': Path(doc.name),
                            'mtime': doc.mtime,
                            'size': doc.size,
                            'is_dir': doc.is_dir,
                            'key': doc._id,
                            'expired_at': doc.expired_at,
                            'shared': True,
                            'link': (await self.rd.get(f'{self.prefix}:LINK:{str(doc._id)}')),
                            'num': int((await self.rd.get(f'{self.prefix}:NUM:{doc.name}')).encode()) if await self.rd.exists(f'{self.prefix}:NUM:{doc.name}') else 0
                        }))

                if self.args.sort == 'time':
                    entries.sort(key=lambda x: x.mtime, reverse=(self.args.order == 1))
                elif self.args.sort == 'size':
                    entries.sort(key=lambda x: x.size, reverse=(self.args.order == -1))
                elif self.args.sort == 'num':
                    entries.sort(key=lambda x: x.num, reverse=(self.args.order == -1))
                else:
                    entries.sort(key=lambda x: x.mtime,reverse=(self.args.order == -1))

                self.render('index.html', entries=entries, absolute=True)
            else:
                self.redirect(self.get_login_url())
        else:
            self.redirect('/disk')

@bp.route('/s/(.*)')
@tornado.web.stream_request_body
class XabcHandler(tornado.web.StaticFileHandler, BaseHandler):

    def __init__(self, application, request, **kwargs):
        tornado.web.StaticFileHandler.__init__(self, application, request, path=self.app.root)
        BaseHandler.__init__(self, application, request, path=self.app.root)

    def compute_etag(self):
        if hasattr(self, 'absolute_path'):
            return super().compute_etag()

    async def prepare(self):
        path = self.root / self.request.path[6:]
        if self.request.method in ['POST'] and path in self.cache:
            self.cache.pop(path)
        elif self.request.method in ['PUT', 'DELETE', 'HEAD'] and path.parent in self.cache:
            self.cache.pop(path.parent)

        if self.request.method == 'PUT':
            self.received = 0
            self.process = 0
            self.request.headers.pop('Content-Type')
            self.length = int(self.request.headers['Content-Length'])
            if str(path).find('..') >= 0:
                return self.finish('target is forbidden\n')
            if path.is_dir():
                return self.finish('target is directory\n')
            path.parent.mkdir(parents=True, exist_ok=True)
            self.fp = open(path, 'wb')
        else:
            XabcHandler._stream_request_body = False
        await super().prepare()

    @run_on_executor
    def download(self, root):
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        filename = urllib.parse.quote(root.name)
        try:
            stream = io.BytesIO()
            zf = zipfile.ZipFile(stream, 'a', zipfile.ZIP_DEFLATED, True)
            for f in root.rglob('*'):
                if f.is_file():
                    zf.writestr(f'{root.name}/{f.relative_to(root)}', f.read_bytes())
            for zfile in zf.filelist:
                zfile.create_system = 0
            zf.close()
            data = stream.getvalue()
            stream.close()
            self.set_header('Content-Disposition', f'attachment;filename={filename}.zip')
            self.finish(data)
        except:
            self.finish("")

    async def send(self, name, include_body=True):
        if include_body and self.app.options.auth:
            doc = await self.db.files.find_one({'name': name})
            if doc:
                return self.redirect(doc.url)

        zh = re.compile(u'[\u4e00-\u9fa5]+')
        filename = os.path.basename(name)
        if zh.search(filename):
            self.set_header('Content-Disposition', f"inline;filename*=UTF-8''{urllib.parse.quote(filename.encode('UTF-8'))}")
        else:
            self.set_header('Content-Disposition', f'inline;filename={urllib.parse.quote(filename)}')

        await super().get(name, include_body)
        await self.rd.incr(f'{self.prefix}:NUM:{name}')
        await self.rd.incr(f'{self.prefix}:SEND:TOTAL')

    async def get(self, name, include_body=True):
        lenth = len(name)
        if self.request.method not in ['GET', 'HEAD'] or lenth not in (6,8,10,12,24):
            return False

        if include_body and self.app.options.auth:
            if lenth in (6,8,10,12):
                Id = await self.rd.get(f'{self.prefix}:LINK:{name}')
                doc = await self.db.share.find_one({'_id': ObjectId(Id)})
            else:
                doc = await self.db.share.find_one({'_id': ObjectId(name)})

            if not doc:
                return False
            if doc.expired_at and doc.expired_at < datetime.datetime.now():
                return await self.db.share.delete_one({'_id': doc._id})

            path = self.root / doc.name
            if not path.exists():
                return await self.db.share.delete_one({'_id': doc._id})

            if path.is_file():
                await self.send(doc.name, include_body)
            else:
                await self.download(path)

@bp.route('/disk/?(.*)')
@bp.route('/file/?(.*)')
@tornado.web.stream_request_body
class DiskHandler(tornado.web.StaticFileHandler, BaseHandler):

    def __init__(self, application, request, **kwargs):
        tornado.web.StaticFileHandler.__init__(self, application, request, path=self.app.root)
        BaseHandler.__init__(self, application, request, path=self.app.root)

    def compute_etag(self):
        if hasattr(self, 'absolute_path'):
            return super().compute_etag()

    def set_extra_headers(self, path):
        if path.endswith('.webp'):
            self.set_header('content-type', 'image/webp')
        elif path.endswith('.ts'):
            self.set_header('content-type', 'application/octet-stream')
    async def prepare(self):
        path = self.root / self.request.path[6:]
        if self.request.method in ['POST'] and path in self.cache:
            self.cache.pop(path)
        elif self.request.method in ['PUT', 'DELETE', 'HEAD'] and path.parent in self.cache:
            self.cache.pop(path.parent)

        if self.request.method == 'PUT':
            self.received = 0
            self.process = 0
            self.request.headers.pop('Content-Type')
            self.length = int(self.request.headers['Content-Length'])
            if str(path).find('..') >= 0:
                return self.finish('target is forbidden\n')
            if path.is_dir():
                return self.finish('target is directory\n')
            path.parent.mkdir(parents=True, exist_ok=True)
            self.fp = open(path, 'wb')
        else:
            DiskHandler._stream_request_body = False
        await super().prepare()

    def data_received(self, chunk):
        self.received += len(chunk)
        process = int(self.received / self.length * 100)
        if process > self.process + 5:
            self.process = process
            self.write(f'uploading process {process}%\n')
            self.flush()
        self.fp.write(chunk)

    async def put(self, name):
        self.fp.close()
        self.finish('upload succeed\n')

    @run_on_executor
    def search(self, name):
        entries = []
        q = self.args.q.lower()
        tmp = self.app.cache.copy()
        for key, files in tmp.items():
            for doc in files[1]:
                if doc.path.name.lower().find(q) >= 0:
                    if self.app.options.auth and not str(doc.path).startswith(name):
                        continue
                    entries.append(doc)
        doc = self.get_args(page=1, size=50)
        self.args.total = len(entries)
        self.args.pages = int(math.ceil(len(entries) / doc.size))
        entries = entries[(doc.page - 1) * doc.size:doc.page * doc.size]
        return entries

    @run_on_executor
    def listdir(self, root):
        entries = self.app.scan_dir(root)
        doc = self.get_args(page=1, size=50, order=1)
        if self.args.sort == 'time':
            entries.sort(key=lambda x: x.mtime, reverse=(self.args.order == - 1))
        elif self.args.sort == 'size':
            entries.sort(key=lambda x: x.size, reverse=(self.args.order == - 1))
        elif self.args.sort == 'num':
            entries.sort(key=lambda x: x.num, reverse=(self.args.order == - 1))
        else:
            entries.sort(key=lambda x: x.mtime,reverse=(self.args.order == 1))
        self.args.total = len(entries)
        self.args.pages = int(math.ceil(len(entries) / doc.size))
        entries = entries[(doc.page - 1) * doc.size:doc.page * doc.size]
        return entries

    @run_on_executor
    def download(self, root):
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        filename = urllib.parse.quote(root.name)
        try:
            stream = io.BytesIO()
            zf = zipfile.ZipFile(stream, 'a', zipfile.ZIP_DEFLATED, True)
            for f in root.rglob('*'):
                if f.is_file():
                    zf.writestr(f'{root.name}/{f.relative_to(root)}', f.read_bytes())
            for zfile in zf.filelist:
                zfile.create_system = 0
            zf.close()
            data = stream.getvalue()
            stream.close()
            self.set_header('Content-Disposition', f'attachment;filename={filename}.zip')
            self.finish(data)
        except:
            self.finish("")

    def get_nodes(self, root):
        nodes = []
        key = self.app.root / root
        if key in self.app.cache:
            entries = self.app.cache[key][1]
            for doc in entries:
                if doc.is_dir:
                    nodes.append({'title': doc.path.name, 'href': f'/disk/{doc.path}', 'children': self.get_nodes(doc.path)})
                else:
                    nodes.append({'title': doc.path.name, 'href': f'/disk/{doc.path}'})
        return nodes

    async def get_info(self, name):
        info = Dict()
        if self.app.options.auth and not name.startswith('0'):
            doc = await self.db.share.find_one({'name': name, 'token': self.current_user.token})
            info.share = True if doc else False
        if self.current_user.admin:
            doc = await self.db.files.find_one({'name': name})
        return info

    def set_default_headers(self):
        self.set_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.set_header('Pragma', 'no-cache')
        self.set_header('Expires', '0')

    def preview_html(self, html, padding=0, background='#23241f'):
        lines = [
            '<html><head>',
            '<link rel="stylesheet" href="/static/src/css/monokai_sublime.css">',
            '<style>img{margin:10px auto;max-width:100%}</style>',
            f'</head><body style="padding:{padding}px;margin:0;background:{background};">',
            html,
            '<script src="/static/src/js/highlight.min.js"></script>',
            '<script>hljs.initHighlightingOnLoad()</script>',
            '</body></html>'
        ]
        self.finish(''.join(lines))

    def preview_video(self):
        url = self.request.path
        if self.app.options.auth:
            url += '?token=' + (self.args.key or self.current_user.token)
        html = [
            '<html><head>',
            '<link rel="stylesheet" href="/static/src/css/DPlayer.min.css">',
            '</head><body>',
            '<div id="video"></div>',
            '<script src="/static/src/js/flv.min.js"></script>',
            '<script src="/static/src/js/hls.min.js"></script>',
            '<script src="/static/src/js/DPlayer.min.js"></script>',
            '<script>new DPlayer({container: document.getElementById("video"), autoplay: true, video: { type: "auto", url: "' + url + '" } })</script>',
            '</body></html>'
        ]
        self.finish(''.join(html))

    def preview_audio(self):
        url = self.request.path
        if self.app.options.auth:
            url += '?token=' + (self.args.key or self.current_user.token)
        html = [
            '<html><head>',
            '</head><body>',
            f'<audio style="display:block;margin:20px auto" controls="controls"><source src="{url}"></audio>'
            '</body></html>'
        ]
        self.finish(''.join(html))
    def preview_zip(self, path):
        zf = zipfile.ZipFile(path)
        items = zf.infolist()
        entries = []
        for item in sorted(items, key=lambda x: x.filename):
            try:
                item.filename = item.filename.encode('cp437').decode('gbk')
            except:
                pass
            entries.append(Dict({
                'path': Path(item.filename),
                'mtime': datetime.datetime(*item.date_time).strftime("%Y-%m-%d %H:%M:%S"),
                'size': item.file_size,
                'is_dir': item.is_dir(),
            }))
        self.render('index.html', entries=entries, absolute=True)

    def preview_tar(self, path):
        tf = tarfile.open(path)
        items = tf.getmembers()
        entries = []
        for item in sorted(items, key=lambda x: x.name):
            entries.append(Dict({
                'path': Path(item.name),
                'mtime': item.mtime,
                'size': item.size,
                'is_dir': item.isdir(),
            }))
        self.render('index.html', entries=entries, absolute=True)

    async def check(self, name):
        if not self.app.options.auth or self.current_user.admin:
            return True
        key = name.split('/')[0]
        if key == str(self.current_user.id):
            return True
        if self.request.method not in ['GET', 'HEAD']:
            return False
        if key == '0':
            return True
        if not key.isdigit():
            return False
        user = await self.db.users.find_one({'id': int(key)})
        if user and user.public:
            return True
        if not self.args.key or len(self.args.key) != 24:
            return False
        doc = await self.db.share.find_one({'_id': ObjectId(self.args.key)})
        if not doc:
            return False
        if doc.expired_at and doc.expired_at < datetime.datetime.now():
            return await self.db.share.delete_one({'_id': doc._id})
        return name.startswith(doc.name)

    async def send(self, name, include_body=True):
        if include_body and self.app.options.auth:
            doc = await self.db.files.find_one({'name': name})
            if doc:
                return self.redirect(doc.url)
        await super().get(name, include_body)

    @check_auth
    async def get(self, name, include_body=True):
        path = self.root / name
        if self.args.q:
            entries = await self.search(name)
            self.render('index.html', entries=entries, absolute=True)
        elif self.args.f == 'tree':
            nodes = self.get_nodes(path)
            self.finish({'nodes': nodes})
        elif self.args.f == 'info':
            info = await self.get_info(name)
            self.finish(info)
        elif self.request.path.startswith('/file/') or self.args.f == 'download' or re.match('wget|curl|axel', self.ua.lower()):
            self.app.options.auth and await self.rd.incr(f'{self.prefix}:NUM:{name}')
            self.set_header('Content-Type', 'application/octet-stream')
            self.set_header('Content-Disposition', f"attachment;filename*=UTF-8''{urllib.parse.quote(path.name.encode('UTF-8'))}")
            if path.is_file():
                await self.send(name, include_body)
            else:
                await self.download(path)
        elif path.is_file() and self.args.f == 'preview':
            self.app.options.auth and await self.rd.incr(f'{self.prefix}:NUM:{name}')
            suffix = path.suffix.lower()[1:]
            if suffix == 'zip':
                self.preview_zip(path)
            elif re.match('(tar|gz|bz2|tgz|z)$', suffix) and tarfile.is_tarfile(path):
                self.preview_tar(path)
            elif re.match('(mp4|mkv|flv|mov)$', suffix):
                self.preview_video()
            elif re.match('(mp3|flac|ogg|amr|wav|m4a)$', suffix):
                self.preview_audio()
            elif suffix == 'json':
                self.finish(json.load(open(path)))
            elif re.match('(md|markdown)$', suffix):
                exts = ['markdown.extensions.extra', 'markdown.extensions.codehilite', 'markdown.extensions.tables', 'markdown.extensions.toc']
                html = markdown.markdown(path.read_text(), extensions=exts)
                self.preview_html(html, padding=20, background='#fff')
            elif re.match('(py|sh|cu|h|hpp|c|cpp|vue|php|js|ts|tsx|css|html|less|scss|pig|java|go|ini|conf|txt|toml|vim|lrc|m3u|cfg|log|lua|rb|yml|yaml|pem|key|json|xml|repo)$', suffix):
                code = {
                    'py': 'python',
                    'sh': 'bash',
                    'h': 'c',
                    'js': 'javascript',
                    'vue': 'javascript',
                    'conf': 'txt',
                    'cfg': 'txt',
                    'log': 'txt',
                    'm3u': 'txt',
                    'repo': 'txt',
                    'yml': 'txt',
                    'xml': 'txt',
                    'vim': 'txt',
                    'yaml': 'txt',
                    'toml': 'txt',
                    'ts': 'txt',
                    'tsx': 'txt',
                    'pem': 'txt',
                    'key': 'txt',
                    'json': 'txt'
                }.get(suffix, suffix)
                try:
                    text = path.read_text()
                except:
                    text = path.read_text(encoding='unicode_escape')
                self.preview_html(f'<pre><code class="{code}">{ tornado.escape.xhtml_escape(text) }</code></pre>')
            elif re.match('(jpeg|jpg|png|gif|bmp|webp|svg|tif|webp|ai|ico|exif|pdf|xml)$', suffix):
                await self.send(name, include_body)
            else:
                self.finish('该格式不支持预览')
        elif path.is_file():
            self.app.options.auth and await self.rd.incr(f'{self.prefix}:NUM:{name}')
            await self.send(name, include_body)
        else:
            entries = await self.listdir(path)
            self.render('index.html', entries=entries, absolute=False)

    async def merge(self, path):
        dirname = Path(f'/tmp/upload/{self.args.guid}-{self.args.id}')
        filename = path / urllib.parse.unquote(self.args.name)
        filename.parent.mkdir(parents=True, exist_ok=True)
        chunks = int(list(dirname.glob("*"))[0].name.split('_')[0])
        md5 = hashlib.md5()
        with filename.open('wb') as fp:
            for i in range(int(chunks)):
                chunk = dirname / f'{chunks}_{i}'
                if not chunk.exists():
                    return self.finish({'err': 1, 'msg': f'缺少分片: {i}'})
                data = chunk.read_bytes()
                md5.update(data)
                fp.write(data)
        md5 = md5.hexdigest()
        if self.args.md5 and self.args.md5 != 'undefined' and self.args.md5 != md5:
            self.finish({'err': 1, 'msg': 'md5校验失败'})
        else:
            shutil.rmtree(dirname)
            self.finish({'err': 0, 'path': filename.relative_to(self.app.root)})
    async def upload(self, path):
        if not self.app.options.upload:
            raise tornado.web.HTTPError(403)

        if self.args.action == 'merge':
            await self.merge(path)
        elif self.args.chunks and self.args.chunk:
            filename = Path(f'/tmp/upload/{self.args.guid}-{self.args.id}/{self.args.chunks}_{self.args.chunk}')
            filename.parent.mkdir(parents=True, exist_ok=True)
            filename.write_bytes(self.request.files['file'][0].body)
            if self.app.options.auth and int(self.args.chunk) == 0:
                await self.rd.setex(f'{self.prefix}:UPLOAD_FLAG',4900,1)
                await self.rd.lpush(f'{self.prefix}:UPLOAD:LIST',f'{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")} {str(path).split(str(self.root))[1][1:]}/{self.args.name}')
                await self.rd.expire(f'{self.prefix}:UPLOAD:LIST', 43200)
            self.finish({'err': 0})
        elif self.request.files:
            path.mkdir(parents=True, exist_ok=True)
            urls = []

            for items in self.request.files.values():
                for item in items:
                    cleaned_filename = re.sub(r'[\s%]+', lambda m: ' ' if m.group().isspace() else '',item.filename)
                    cleaned_path_name = path / urllib.parse.unquote(Path(cleaned_filename).name)
                    cleaned_path_name.parent.mkdir(parents=True, exist_ok=True)
                    cleaned_path_name.write_bytes(item.body)
                    urls.append(cleaned_path_name.relative_to(self.app.root))

            ret = {'err': 0, 'path': urls[0]}

            if self.app.options.auth:
                await self.rd.setex(f'{self.prefix}:UPLOAD_FLAG',4900,1)
                await self.rd.lpush(f'{self.prefix}:UPLOAD:LIST',f'{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")} {urls[0]}')
                await self.rd.expire(f'{self.prefix}:UPLOAD:LIST', 43200)

            if len(urls) > 1:
                ret['paths'] = urls
            self.finish(ret)
        else:
            self.finish({'err': 1, 'msg': 'files not found'})

    @check_auth
    async def post(self, name):
        path = self.root / name
        if self.args.action == 'delete' and self.request.headers.get('referer', '').find('/share') >= 0:
            self.logger.info('change action from delete to unshare')
            self.args.action = 'unshare'
        if not path.exists() and self.args.action and self.args.action not in ['unshare', 'merge']:
            self.finish({'err': 1, 'msg': f'{name} not exists'})
        elif self.args.action == 'folder':
            folder = path / self.args.name.strip('./')
            folder.mkdir(parents=True, exist_ok=True)
            self.finish({'err': 0})
        elif self.args.action == 'kindle':
            if not self.current_user.kindle:
                self.finish({'err': 1, 'msg': '未设置 Kindle 推送邮箱'})
            elif path.is_dir():
                self.finish({'err': 1, 'msg': '不可推送文件夹'})
            elif path.stat().st_size > 52428800:
                self.finish({'err': 1, 'msg': '文件大小不可大于50MB'})
            elif not re.match('.(zip|pdf|txt|epub|azw3|doc|docx|html|htm|rtf|jpeg|jpg|webp|png|gif|bmp)$', path.suffix.lower()):
                self.finish({'err': 1, 'msg': '文件类型不支持推送至Kindle'})
            else:
                await self.app.email.send(self.current_user.kindle, 'convert', files=str(path))
                self.finish({'err': 0, 'msg': '推送成功'})
        elif self.args.action == 'rename':
            if self.args.filename.find('/') >= 0:
                return self.finish({'err': 1, 'msg': '文件名不可包含/'})
            new_path = path.parent / self.args.filename
            if new_path.exists():
                self.finish({'err': 1, 'msg': '文件名重复'})
            else:
                path.rename(new_path)
                self.finish({'err': 0, 'msg': '重命名成功'})
        elif self.args.action == 'move':
            if self.args.dirname.startswith('/'):
                dirpath = '/'.join(self.request.path.split('/')[2:(3 if self.app.options.auth else 2)])
            else:
                dirpath = '/'.join(self.request.path.split('/')[2:- 1])
            new_path = self.root / dirpath / self.args.dirname.strip('/') / path.name
            self.logger.info(f'move {path} to {new_path}')
            if new_path.exists():
                return self.finish({'err': 1, 'msg': '目标文件已存在'})
            if new_path.parent.is_file():
                return self.finish({'err': 1, 'msg': '目标文件夹为文件'})
            new_path.parent.mkdir(parents=True, exist_ok=True)
            path.rename(new_path)
            self.finish({'err': 0, 'msg': '已移动至目标文件夹'})
        elif self.args.action == 'public':
            filename = self.root / '0' / path.name
            if not self.current_user.admin:
                self.finish({'err': 1, 'msg': '无权限'})
            elif filename.exists():
                self.finish({'err': 1, 'msg': f'{name} 已存在于公共空间'})
            else:
                filename.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(path, filename)
                self.finish({'err': 0, 'msg': f'{name}已分享公共空间'})
        elif self.args.action == 'share':
            url = f'/disk/{name}'
            if not self.app.options.auth:
                return self.finish({'err': 0, 'url': url})
            doc = await self.db.share.find_one({'token': self.current_user.token, 'name': name})
            if doc and self.args.batch:
                await self.db.share.delete_one({'_id': doc._id})
                self.finish({'err': 0, 'msg': f'{name}已取消分享'})
            else:
                doc = {
                    'token': self.current_user.token,
                    'name': name,
                    'mtime': int(path.stat().st_mtime),
                    'size': path.stat().st_size,
                    'is_dir': path.is_dir(),
                    'created_at': datetime.datetime.now().replace(microsecond=0)
                }
                if self.args.days and self.args.days != '0':
                    doc['expired_at'] = doc['created_at'] + datetime.timedelta(days=int(self.args.days))
                doc = await self.db.share.find_one_and_update({'token': self.current_user.token, 'name': name}, {'$set': doc}, upsert=True, return_document=True)

                Id = str(doc._id)
                expire = int((datetime.timedelta(days=int(self.args.days or 36000))).total_seconds())
                if not await self.rd.exists(f'{self.prefix}:LINK:{Id}'):
                    link = self.generate_short_link(Id)
                else:
                    link = await self.rd.get(f'{self.prefix}:LINK:{Id}')
                await self.rd.setex(f'{self.prefix}:LINK:{link}',expire,Id)
                await self.rd.setex(f'{self.prefix}:LINK:{Id}',expire,link)

                self.finish({'err': 0, 'msg': f'{name}分享成功'})
        elif self.args.action == 'unshare':
            if not self.app.options.auth:
                self.finish({'err': 0})
            else:
                await self.db.share.delete_one({'token': self.current_user.token, 'name': name})
                self.finish({'err': 0, 'msg': f'{name}已取消分享'})
        elif self.args.action == 'download':
            for url in re.split('[,;\n\t]',self.args.src):
                filename = urllib.parse.urlparse(url).path.split('/')[-1]
                filename = path / filename
                command = f''' axel -U "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.1.1 Safari/605.1.15" -n5 '{url}' -o '{filename}' '''
                p = await asyncio.create_subprocess_shell(command)
                await p.wait()
                if p.returncode != 0:
                    command = f''' wget --no-check-certificate -U "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.1.1 Safari/605.1.15" '{url}' -O '{filename}' '''
                    p = await asyncio.create_subprocess_shell(command)
                    await p.wait()
                self.logger.info(f'download result: {p.returncode}, {url}')
            self.finish({'err': p.returncode, 'msg': '下载成功' if p.returncode == 0 else '下载失败'})
        elif self.args.action == 'delete':
            await self.delete(name)
        else:
            await self.upload(path)

    @check_auth
    async def delete(self, name):
        if self.app.options.auth:
            doc = await self.db.share.find_one({'name': name, 'token': self.current_user.token})
            if doc:
                Id = str(doc._id)
                link = await self.rd.get(f'{self.prefix}:LINK:{Id}')
                await self.rd.delete(f'{self.prefix}:LINK:{link}')
                await self.rd.delete(f'{self.prefix}:LINK:{Id}')
                await self.rd.setex(f'{self.prefix}:UPLOAD_FLAG',4900,1)

        if not self.app.options.delete:
            raise tornado.web.HTTPError(403)

        path = self.root / name
        if not path.exists():
            return self.finish({'err': 1, 'msg': f'{name} not exists'})

        if self.app.options.auth:
            await self.rd.delete(f'{self.prefix}:NUM:{name}')
            await self.db.share.delete_many({'name': name})
            if not name.startswith('0'):
                for f in (self.root / '0').rglob('*'):
                    if f.resolve() == path.absolute():
                        f.unlink()

        if path.is_file() or path.is_symlink():
            path.unlink()
        else:
            shutil.rmtree(path)
            self.app.cache.pop(path, None)
            if self.app.options.auth:
                p = await self.rd.pipeline()
                for i in await self.rd.keys(f'{self.prefix}:*:{name}*'):
                    await p.delete(i)
                await p.execute()
        self.app.cache.pop(path.parent, None)
        self.finish({'err': 0, 'msg': f'{name} 删除成功'})


@bp.route(r"/chart/?(.*)")
class ChartHandler(BaseHandler):

    async def get(self, name):
        if not name:
            docs = await self.query('charts')
            self.render('chart.html', docs=docs)
        else:
            chart = await self.db.charts.find_one({'name': name})
            if not chart:
                raise tornado.web.HTTPError(404)

            if self.args.f == 'json':
                self.finish({'containers': json.loads(chart.containers)})
            else:
                self.render('chart.html')

    async def delete(self, name):
        await self.db.charts.delete_one({'name': name})
        self.finish({'err': 0})

    async def post(self, name):
        charts = json.loads(self.request.body)
        if isinstance(charts, dict):
            charts = [charts]
        containers = []
        for chart in charts:
            chart = Dict(chart)
            if chart.chart:
                chart.credits = {'enabled': False}
                containers.append(chart)
            else:
                chart.setdefault('xAxis', [])
                data = {
                    'chart': {
                        'type': chart.type,
                        'zoomType': 'x',
                    },
                    'credits': {
                        'enabled': False
                    },
                    'title': {
                        'text': chart.title,
                        'x': -20
                    },
                    'xAxis': {
                        'tickInterval': int(math.ceil(len(chart.xAxis) / 20.0)),
                        'labels': {
                            'rotation': 45 if len(chart.xAxis) > 20 else 0,
                            'style': {
                                'fontSize': 12,
                                'fontWeight': 'normal'
                            }
                        },
                        'categories': chart.xAxis
                    },
                    'yAxis': {
                        'title': {
                            'text': ''
                        },
                        'plotLines': [{
                            'value': 0,
                            'width': 1,
                            'color': '#808080'
                        }]
                    },
                    'tooltip': {
                        'headerFormat': '<span style="font-size:10px">{point.key}</span><table>',
                        'pointFormat': '<tr><td style="color:{series.color};padding:0">{series.name}: </td><td style="padding:0"><b>{point.y:.2f}</b></td></tr>',
                        'footerFormat': '</table>',
                        'shared': True,
                        'useHTML': True
                    },
                    'legend': {
                        'layout': 'horizontal',
                        'align': 'center',
                        'verticalAlign': 'bottom',
                        'borderWidth': 0,
                        'y': 0,
                        'x': 0
                    },
                    'plotOptions': {
                        'series': {
                            'marker': {
                                'radius': 1,
                                'symbol': 'diamond'
                            }
                        },
                        'pie': {
                            'allowPointSelect': True,
                            'cursor': 'pointer',
                            'dataLabels': {
                                'enabled': True,
                                'color': '#000000',
                                'connectorColor': '#000000',
                                'format': '<b>{point.name}</b>: {point.percentage:.3f} %'
                            }
                        }
                    },
                    'series': chart.series
                }
                containers.append(data)

        if containers:
            doc = {
                'name': name,
                'containers': json.dumps(containers, ensure_ascii=False),
                'charts': json.dumps(charts, ensure_ascii=False),
                'date': datetime.datetime.now().replace(microsecond=0)
            }
            await self.db.charts.update_one({'name': name}, {'$set': doc}, upsert=True)
            self.finish({'err': 0})
        else:
            self.finish({'err': 1, 'msg': '未获取到必需参数'})

@bp.route(r"/table/?(.*)")
class TableHandler(BaseHandler):

    async def get(self, name):
        if not name:
            docs = await self.query('tables')
            self.render('table.html', docs=docs)
        else:
            meta = await self.db.tables.find_one({'name': name})
            if not meta:
                raise tornado.web.HTTPError(404)

            schema = dict(map(lambda x: x.split(':'), meta['fields']))
            entries = await self.query(f'table_{name}', self.args, schema=schema)

            self.args.fields = list(map(lambda item: item.split(':')[0], meta['fields']))
            self.args.searchs = meta.get('search', [])
            self.args.marks = meta.get('mark', [])
            self.args.options = {
                'sort': self.args.fields,
                'order': ['1:asc', '-1:desc'],
            }
            self.render('table.html', entries=entries)

    async def delete(self, name):
        table = f'table_{name}'
        await self.db[table].drop()
        await self.db.tables.delete_one({'name': name})
        self.finish({'err': 0})

    async def post(self, name):
        table = f'table_{name}'
        doc = json.loads(self.request.body)
        await self.db[table].drop()
        for key in doc.get('search', []):
            await self.db[table].create_index(key)

        fields = dict(map(lambda x: x.split(':'), doc['fields']))
        if doc.get('docs'):
            dts = dict(filter(lambda x: x[1] == 'datetime', fields.items()))
            for k in dts:
                for item in doc['docs']:
                    item[k] = datetime.datetime.strptime(item[k], '%Y-%m-%d %H:%M:%S')
            await self.db[table].insert_many(doc['docs'])

        meta = {'name': name, 'date': datetime.datetime.now().replace(microsecond=0)}
        meta.update(dict(filter(lambda x: x[0] in ['fields', 'search', 'mark'], doc.items())))
        await self.db.tables.update_one({'name': name}, {'$set': meta}, upsert=True)
        self.finish({'err': 0})

    async def put(self, name):
        table = f'table_{name}'
        doc = json.loads(self.request.body)
        meta = await self.db.tables.find_one({'name': name})
        type_dict = dict(map(lambda x: x.split(':'), meta['fields']))
        if type_dict[doc['key']] == 'int':
            doc['value'] = int(doc['value'])
        elif type_dict[doc['key']] == 'float':
            doc['value'] = float(doc['value'])
        elif type_dict[doc['key']] == 'datetime':
            doc['value'] = datetime.datetime.strptime(doc['value'], '%Y-%m-%d %H:%M:%S')

        if doc['action'] == 'add':
            op = '$set' if doc.get('unique') else '$addToSet'
        else:
            op = '$unset' if doc.get('unique') else '$pull'
        await self.db[table].update_one({'_id': ObjectId(doc['_id'])}, {op: {doc['key']: doc['value']}})
        self.finish({'err': 0})
