# cython: language_level=3
import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import datetime
import collections
import hashlib
import secrets
import string
import psutil
import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from handler import bp as bp_disk
from tornado.options import define, options
from tornado_utils import Application, bp_user
from utils import AioEmail, AioRedis, Dict, Motor, Request, Redis

define('root', default=os.path.abspath(os.path.dirname(__file__))+'/files', type=str)
define('auth', default=True if os.environ.get('FILELIST_AUTH') else False, type=bool)
define('tools', default=False, type=bool)
define('upload', default=True, type=bool)
define('delete', default=True, type=bool)
define('db', default='filelist', type=str)

class Application(Application):

    def initialize(self):
        logging.getLogger('apscheduler').setLevel(logging.ERROR)
        self.root = Path(options.root).expanduser().absolute()
        self.http = Request(lib='aiohttp')
        self.cache = collections.defaultdict(list)
        self.mtime = {}
        self.sched = BackgroundScheduler()
        self.sched.add_job(self.scan, 'cron', minute=0, hour='*')
        self.sched.add_job(self.scan, 'date', run_date=datetime.datetime.now() + datetime.timedelta(seconds=30))
        if options.auth:
            self.sched.add_job(self.count,'interval',seconds=3600)
        self.sched.start()

        if options.auth:
            self.db = Motor(options.db)
            self.email = AioEmail()
            self.rd = AioRedis()
            self.redis = Redis()

        now = datetime.datetime.now()
        self.boot_time = "System boot time: {}".format(now.strftime("%Y-%m-%d %H:%M:%S"))

    def count_files(self,path):
        return sum(1 for entry in os.scandir(path) if entry.is_file())

    def count(self):
        if self.redis.exists(f'{self.prefix}:UPLOAD_FLAG') and self.redis.ttl(f'{self.prefix}:UPLOAD_FLAG') < 3600:
            if not self.redis.exists(f'{self.prefix}:FILE_COUNT'):
                self.redis.set(f'{self.prefix}:FILE_COUNT',0)
            with ThreadPoolExecutor() as executor:
                tasks = [executor.submit(self.count_files, os.path.join(root, subdir))
                        for root, dirs, files in os.walk(self.root) for subdir in dirs]
                file_count = sum(task.result() for task in as_completed(tasks))
                difference = file_count - int(self.redis.get(f'{self.prefix}:FILE_COUNT').split()[0])
                self.redis.set(f'{self.prefix}:FILE_COUNT',f'{file_count}   ( {difference:+} )')
                self.redis.set(f'{self.prefix}:COUNT_UPDATE',datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                self.redis.delete(f'{self.prefix}:UPLOAD_FLAG')

    async def shutdown(self):
        if options.auth:
            await self.redis.save()
        await super().shutdown()
        os._exit(0)

    async def get_db_info(self):
        self.share_documents = await self.db.share.estimated_document_count()
        self.file_count = await self.rd.get(f'{self.prefix}:FILE_COUNT')
        self.count_update = await self.rd.get(f'{self.prefix}:COUNT_UPDATE')
        self.redis_mem = (await self.rd.info())['used_memory_human']
        self.mongo_mem = (await self.db.command('serverStatus'))['mem']['resident']
        self.signup_list = await self.rd.lrange(f'{self.prefix}:SIGNUP:LIST',0,100)
        self.upload_list = await self.rd.lrange(f'{self.prefix}:UPLOAD:LIST',0,100)
        self.send_total = await self.rd.get(f'{self.prefix}:SEND:TOTAL')

    def get_email_list(self):
        if options.auth:
            self.email_blacklist = ','.join(self.redis.smembers(f'{self.prefix}:Email_Blacklist'))
            self.email_whitelist = ','.join(self.redis.smembers(f'{self.prefix}:Email_Whitelist'))

    def get_system_info(self):
        disk_info_list = []
        for partition in psutil.disk_partitions():
            disk_usage = psutil.disk_usage(partition.mountpoint)
            disk_info = (f"Device: {partition.device}\n"
                 f"File system type: {partition.fstype}\n"
                 f"Total: {disk_usage.total / (1024 ** 3):.2f} GB\n"
                 f"Used: {disk_usage.used / (1024 ** 3):.2f} GB\n"
                 f"Free: {disk_usage.free / (1024 ** 3):.2f} GB\n"
                 f"Percent: {disk_usage.percent}%\n")
            disk_info_list.append(disk_info)
        load_avg_1, load_avg_5, load_avg_15 = psutil.getloadavg()
        load_info_str = f"System Load Average: {load_avg_1:.2f} {load_avg_5:.2f} {load_avg_15:.2f}"
        mem_info = psutil.virtual_memory()
        mem_info_str = (f"Memory Usage: Total: {mem_info.total / (1024 ** 3):.2f} GB Available: {mem_info.available / (1024 ** 3):.2f} GB Used: {mem_info.used / (1024 ** 3):.2f} GB Percent: {mem_info.percent}%")
        self.disk_info_set = set(disk_info_list)
        self.load_info_str = load_info_str
        self.mem_info_str = mem_info_str

    def generate_short_link(self,id_str):
        salt = secrets.token_urlsafe(6)
        md = hashlib.blake2s((id_str + salt).encode(), digest_size=6)
        short_code = ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(4))
        return md.hexdigest()[:8] + short_code if random.choice([True, False]) else short_code + md.hexdigest()[:8]

    def scan_dir(self, root):
        if not root.exists():
            return []
        st_mtime = root.stat().st_mtime
        if root in self.cache and st_mtime == self.cache[root][0]:
            entries = self.cache[root][1]
        else:
            entries = []
            for item in root.iterdir():
                if not item.exists():
                    continue
                if item.name.startswith('.'):
                    continue
                path = item.relative_to(self.root)
                entries.append(Dict({
                    'path': path,
                    'mtime': int(item.stat().st_mtime),
                    'size': item.stat().st_size,
                    'is_dir': item.is_dir(),
                    'num': int(self.redis.get(f'{self.prefix}:NUM:{str(path)}')) if options.auth and self.redis.exists(f'{self.prefix}:NUM:{str(path)}') else 0
                }))
            entries.sort(key=lambda x: str(x.path).lower())
            self.cache[root] = [st_mtime, entries]

        return entries

    def scan(self):
        dirs = [self.root] + [f for f in self.root.rglob('*') if f.is_dir()]
        with ThreadPoolExecutor(min(20, len(dirs))) as executor:
            executor.map(self.scan_dir, dirs)

def main():
    kwargs = dict(
        static_path=Path(__file__).parent.absolute() / 'static',
        template_path=Path(__file__).parent.absolute() / 'templates'
    )
    app = Application(**kwargs)
    app.register(bp_disk, bp_user)
    app.run(port = options.port, max_buffer_size = 1024000 * 1024 * 1024)

if __name__ == '__main__':
    main()
