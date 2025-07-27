# cython: language_level=3
import asyncio
import os
import smtplib
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import COMMASPACE, formataddr, formatdate, parseaddr
from pathlib import Path

import aiosmtplib

__all__ = ['Email', 'AioEmail']


class EmailBase:

    def __init__(self, sender=None, smtp=None, port=None, user=None, pwd=None, use_tls=None):
        self.sender = sender or os.environ.get('EMAIL_SENDER')
        self.smtp = smtp or os.environ.get('EMAIL_SMTP')
        self.port = port or os.environ.get('EMAIL_PORT')
        self.user = user or os.environ.get('EMAIL_USER')
        self.pwd = pwd or os.environ.get('EMAIL_PWD')
        self.use_tls = use_tls or os.environ.get('EMAIL_TLS')
        if isinstance(self.use_tls, str):
            self.use_tls = self.use_tls.lower() == 'true'

    @staticmethod
    def _format_addr(s):
        # format: username<email address>
        name, addr = parseaddr(s)
        return formataddr((Header(name, 'utf-8').encode(), addr)) if name else addr

    def pack(self, receivers, title=None, content=None, files=None, cc=None):
        msg = MIMEMultipart()
        msg.set_charset('utf8')

        if content:
            mime = MIMEText(content, 'html', 'utf-8')
            msg.attach(mime)

        if files:
            if not isinstance(files, (list, tuple)):
                files = [files]
            for i, fname in enumerate(files):
                fpath = Path(fname)
                att = MIMEApplication(fpath.read_bytes())
                # att.add_header('X-Attachment-Id', str(i))
                att.add_header('Content-ID', str(i))
                att.add_header('Content-Type', 'application/octet-stream')
                att.add_header('Content-Disposition', 'attachment', filename=fpath.name)
                msg.attach(att)

        if cc:
            if not isinstance(cc, (list, tuple)):
                cc = [cc]
            msg['cc'] = COMMASPACE.join([self._format_addr(c) for c in cc])

        msg['subject'] = title
        msg['date'] = formatdate(localtime=True)
        sender = f'{self.sender}<{self.user}>' if self.sender else self.user
        msg['from'] = self._format_addr(sender)
        if not isinstance(receivers, (list, tuple)):
            receivers = [receivers]
        msg['to'] = COMMASPACE.join([self._format_addr(r) for r in receivers])
        return msg


class Email(EmailBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.use_tls:
            self.client = smtplib.SMTP_SSL(self.smtp, self.port)
        else:
            self.client = smtplib.SMTP(self.smtp, self.port)

    def send(self, *args, **kwargs):
        msg = self.pack(*args, **kwargs)
        self.client.connect(self.smtp)
        self.client.ehlo()
        self.client.login(self.user, self.pwd)
        self.client.send_message(msg)
        self.client.quit()


class AioEmail(EmailBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        kwargs = {'port': self.port, 'use_tls': True} if self.use_tls else {}
        self.client = aiosmtplib.SMTP(hostname=self.smtp, **kwargs)

    async def send(self, *args, **kwargs):
        try:
            msg = self.pack(*args, **kwargs)
            await self.client.connect()
            await self.client.login(self.user, self.pwd)
            await self.client.send_message(msg)
        except:
            pass
        await self.client.quit()

if __name__ == '__main__':
    email = AioEmail()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(email.send('ywgx@xabcloud.com', '你好', '世界'))
