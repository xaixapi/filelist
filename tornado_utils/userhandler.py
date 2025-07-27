# cython: language_level=3
import datetime
import hashlib
import random
import re
import uuid

import pymongo
import tornado.web
from bson import ObjectId
from utils import Dict, awaitable

from .application import Blueprint
from .basehandler import BaseHandler

bp = Blueprint(__name__)


class BaseHandler(BaseHandler):

    def set_default_headers(self):
        self.set_header('Access-Control-Allow-Origin', '*')
        self.set_header('Access-Control-Allow-Headers', '*')
        self.set_header('Access-Control-Allow-Methods', 'POST, GET, PUT, DELETE, HEAD, OPTIONS')
        self.set_header('Access-Control-Allow-Credentials', 'true')
        self.set_header('Access-Control-Max-Age', '3600')

    def options(self):
        self.set_status(204)
        self.finish()

    def encrypt(self, password):
        return hashlib.md5(f'ywgx_{password}'.encode()).hexdigest()

    async def gen_code(self, email):
        code = ''.join(random.sample('0123456789', 4))
        key = f'{self.prefix}:code:{email}'
        await self.rd.lpush(f'{self.prefix}:SIGNUP:LIST', f'{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")} {email} {code}')
        await self.rd.expire(f'{self.prefix}:SIGNUP:LIST', 86400)
        with open("/tmp/"+code+"_"+email+".txt",'w') as f: f.write(code)
        await awaitable(self.rd.setex(key, 810, code))
        return code

    async def get_user(self, email):
        if email.find('@') >= 0:
            return await awaitable(self.db.users.find_one({'email': email}))
        else:
            return await awaitable(self.db.users.find_one({'username': email}))

    async def check_code(self):
        email = self.get_argument('email', None)
        code = self.get_argument('code', None)
        if email and code:
            key = f'{self.prefix}:code:{email}'
            if code == await awaitable(self.rd.get(key)):
                return Dict({'err': 0})
        return Dict({'err': 1, 'msg': '验证码无效'})

    async def check_username(self):
        username = self.get_argument('username', None)
        if not username:
            return Dict({'err': 1, 'msg': '请输入用户名'})
        if len(username) < 4:
            return Dict({'err': 1, 'msg': '用户名至少4个字符'})
        if len(username) > 20:
            return Dict({'err': 1, 'msg': '用户名至多20个字符'})
        if username.find('@') >= 0:
            return Dict({'err': 1, 'msg': '用户名禁止特殊字符'})
        if await awaitable(self.db.users.find_one({'username': username})):
            return Dict({'err': 1, 'msg': '该名称已被占用'})
        return Dict({'err': 0})

    async def check_email(self):
        email = self.get_argument('email', None)
        if not email:
            return Dict({'err': 1, 'msg': '请输入Email'})
        if len(email) > 64:
            return Dict({'err': 1, 'msg': 'Email地址太长'})
        if (not email.find('@')) or email.find(',') >= 0 or (not re.match(r'.*@(.*)\.(\w+)$', email)):
            return Dict({'err': 1, 'msg': '请填写正确的Email'})
        EmailSuffix = email.split('@')[-1]
        if await awaitable(self.rd.exists(f'{self.prefix}:Email_Blacklist')) and EmailSuffix in await awaitable (self.rd.smembers(f'{self.prefix}:Email_Blacklist')):
            return Dict({'err': 1, 'msg': '验证码已经发送,请耐心等待'})
        if await awaitable(self.rd.exists(f'{self.prefix}:Email_Whitelist')) and EmailSuffix not in await awaitable (self.rd.smembers(f'{self.prefix}:Email_Whitelist')):
            return Dict({'err': 1, 'msg': '非信任邮箱,请联系管理员'})
        return Dict({'err': 0})

    async def check_scene(self):
        scene = self.get_argument('scene', None)
        if not scene:
            return Dict({'err': 1, 'msg': f'scene is not defined: {scene}'})

        resp = await self.http.get(scene)
        if not resp.code == 200:
            return Dict({'err': 1, 'msg': f'get scene failed: {scene}'})

        ret = resp.json()
        if not ret.err:
            user = await awaitable(self.db.users.find_one({'openId': ret.openId}))
            if user:
                ret = user
            else:
                if self.current_user:
                    ret.active = True
                    ret.token = self.current_user.token
                    keys = ['active', 'openId', 'avatarUrl', 'city', 'country', 'gender', 'language', 'nickName', 'province']
                    update = {k: v for k, v in ret.items() if k in keys if v}
                    await awaitable(self.db.users.update_one({'_id': self.current_user._id}, {'$set': update}))
                else:
                    ret.id = await awaitable(self.db.users.seq_id)
                    ret.token = uuid.uuid4().hex
                    ret.active = True
                    ret.created_at = datetime.datetime.now().replace(microsecond=0)
                    await awaitable(self.db.users.update_one({'openId': ret.openId}, {'$set': ret}, upsert=True))
            expires = datetime.datetime.now() + datetime.timedelta(days=30)
            self.set_cookie('token', ret.token, expires=expires)
            ret.err = 0

        return ret


@bp.route("/check")
class CheckHandler(BaseHandler):

    async def get(self):
        for key, value in self.args.items():
            if hasattr(self, f'check_{key}'):
                ret = await getattr(self, f'check_{key}')()
                break
        else:
            ret = Dict({'err': 1, 'msg': 'not authorized'})
        self.finish(ret)


@bp.route("/logout")
class LogoutHandler(BaseHandler):

    def get(self):
        self.clear_cookie('token')
        self.redirect('/')


@bp.route("/signup")
class SignupHandler(BaseHandler):

    def get(self):
        self.next = self.get_argument('next', '/')
        self.render('signup.html')

    async def post(self):
        ret = await self.check_username()
        if ret.err:
            return self.finish(ret)
        ret = await self.check_email()
        if ret.err:
            return self.finish(ret)
        if self.args.code:
            ret = await self.check_code()
            if ret.err:
                return self.finish(ret)

        if not (self.args.username and self.args.password and self.args.email and self.args.code ):
            return self.finish({'err': 1, 'msg': '信息未填写完整'})

        token = uuid.uuid4().hex
        doc = self.args.copy()
        doc.update({
            'password': self.encrypt(self.args.password),
            'token': token,
            'created_at': datetime.datetime.now().replace(microsecond=0)
        })
        try:
            user = await awaitable(self.db.users.find_one_and_update({'username': doc['username']},
                                                                     {'$set': doc},
                                                                     upsert=True,
                                                                     return_document=True))
        except pymongo.errors.DuplicateKeyError:
            seq_id = await awaitable(self.db.users.seq_id)
            await awaitable(self.db.users.find_one_and_update({'id': {'$exists': 0}}, {'$set': {'id': seq_id}}))
            user = await awaitable(self.db.users.find_one_and_update({'username': doc['username']},
                                                                     {'$set': doc},
                                                                     upsert=True,
                                                                     return_document=True))

        update = {}
        if not user.id:
            update['id'] = await awaitable(self.db.users.seq_id)
            if update['id'] == 1:
                update['admin'] = True
        if update:
            await awaitable(self.db.users.update_one({'_id': user._id}, {'$set': update}))
            user.update(update)
        self.set_cookie('token', token, expires_days=30)
        self.finish({'err': 0, 'user': user})


@bp.route("/signin")
class SigninHandler(BaseHandler):

    def get(self):
        self.next = self.get_argument('next', '/')
        self.render('signin.html')

    async def post(self):
        username = self.get_argument('username', None)
        password = self.get_argument('password', None)
        remember = self.get_argument('remember', None)
        if username and password:
            if username.find('@') >= 0:
                query = {'email': username, 'password': self.encrypt(password)}
            else:
                query = {'username': username, 'password': self.encrypt(password)}
            user = await awaitable(self.db.users.find_one(query))
            if user:
                ret = {'err': 0, 'user': user}
                if remember == 'on':
                    self.set_cookie('token', user.token, expires_days=30)
                else:
                    self.set_cookie('token', user.token)
            else:
                ret = {'err': 1, 'msg': '用户名或密码错误'}
        else:
            ret = {'err': 1, 'msg': '请输入用户名和密码'}
        self.finish(ret)


@bp.route("/user")
class UserHandler(BaseHandler):

    async def get(self):
        if self.current_user:
            user = {k: v for k, v in self.current_user.items() if k not in ['_id', 'password']}
            self.finish(user)
        else:
            self.finish({'err': 1, 'msg': '用户未登录'})

    @tornado.web.authenticated
    async def post(self):
        user = self.current_user
        old_password = self.get_argument('old_password', None)
        password = self.get_argument('password', None)
        if not (old_password and self.encrypt(old_password) == user.password):
            return self.finish({'err': 1, 'msg': '原密码错误'})
        if not password:
            return self.finish({'err': 1, 'msg': '请输入新密码'})
        await awaitable(self.db.users.update_one({'_id': user._id}, {'$set': {'password': self.encrypt(password)}}))
        self.finish({'err': 0})

    @tornado.web.authenticated
    async def delete(self):
        if not self.current_user.admin:
            return self.finish({'err': 1, 'msg': 'unauthorized'})

        await awaitable(self.db.users.delete_one({'_id': ObjectId(self.args._id)}))
        self.finish({'err': 0})


@bp.route("/reset")
class ResetHandler(BaseHandler):

    def get(self):
        self.render('reset.html')

    async def post(self):
        ret = await self.check_code()
        if ret.err:
            return self.finish(ret)

        email = self.get_argument('email', None)
        password = self.get_argument('password', None)
        if email and password:
            user = await self.get_user(email)
            if user:
                await awaitable(self.db.users.update_one({'_id': user._id}, {'$set': {'password': self.encrypt(password)}}))
                self.finish({'err': 0,'msg': '重置密码成功'})
            else:
                self.finish({'err': 1, 'msg': '用户不存在'})
        else:
            self.finish({'err': 1, 'msg': '缺少关键信息'})


@bp.route(r'/active/(\w+)')
class ActiveHandler(BaseHandler):

    async def get(self, code):
        email = await awaitable(self.rd.get(f'{self.prefix}:active:{code}'))
        if email:
            await awaitable(self.db.users.update_one({'email': email}, {'$set': {'active': True}}))
        self.redirect('/set')

    async def post(self, _id):
        await awaitable(self.db.users.update_one({'_id': ObjectId(_id)}, {'$set': {'active': True}}))
        self.finish({'err': 0})


@bp.route(r'/email/(\w+)')
class EmailHandler(BaseHandler):

    async def get(self, action):
        email = self.args.email
        ret = await self.check_email()

        if ret.err:
            return self.finish(ret)
        if await awaitable(self.rd.get(f'{self.prefix}:code:{email}')):
            ttl = await awaitable(self.rd.ttl(f'{self.prefix}:code:{email}'))
            return self.finish({'err': 1, 'msg': f'请等待 {ttl} 秒后再发送邮件'})

        user = await self.get_user(email)
        if action == 'check':
            referer = self.referer.split('/')[-1]
            if not user and referer == 'signup' and not self.current_user:
                code = await self.gen_code(email)
                title = f'{code} 验证码来自 [ {self.request.host} ]'
                content = f'{code}'
            elif not user and referer == 'reset' and not self.current_user:
                return self.finish({'err': 1, 'msg': '该邮箱未注册'})
            elif user and referer == 'signup' and not self.current_user:
                return self.finish({'err': 1, 'msg': '该邮箱已注册'})
            elif user and referer == 'reset' and not self.current_user:
                code = await self.gen_code(email)
                title = f'{code} 验证码来自 [ {self.request.host} ] 重置密码'
                content = f'{code}'
            elif not user and referer == 'set' and self.current_user:
                code = await self.gen_code(email)
                title = f'{code} 验证码来自 [ {self.request.host} ] Kindle 邮箱设定验证'
                content = f'{code}'
            else:
                return self.finish({'err': 1, 'msg': '非法操作'})
        elif action == 'active':
            code = uuid.uuid4().hex
            title = f'{self.request.host} 激活邮件'
            key = f'{self.prefix}:active:{code}'
            await awaitable(self.rd.setex(key, 81, email))
            url = f'{self.scheme}://{self.host}/active/{code}'
            content = f'请点击链接或将其复制到地址栏打开: <br><a href="{url}">{url}</a>'
        else:
            return self.finish({'err': 1, 'msg': '非法操作'})

        if email.split('@')[-1] in {'kindle.com'}:
            title = f'[ {self.request.host} ] Kindle 邮箱验证码'
            await self.app.email.send(email, title, content, files="/tmp/"+code+"_"+email+".txt")
        else:
            await self.app.email.send(email, title, content)

        self.finish({'err': 0})

    post = get
