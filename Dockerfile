FROM python:alpine AS base

FROM base AS builder
COPY . /filelist
RUN apk add --no-cache gcc musl-dev && \
    pip install setuptools Cython && \
    rm -rf /filelist/.git /filelist/.github /filelist/Dockerfile /filelist/README.md /filelist/build.sh /filelist/docker-compose.yml /filelist/deployment.yml && \
    chmod +x /filelist/docker-build.sh && /filelist/docker-build.sh && \
    mv /filelist/docker-entrypoint.sh /usr/local/bin/ && chmod +x /usr/local/bin/docker-entrypoint.sh && \
    rm -rf /filelist/docker-build.sh /filelist/build

FROM base AS runner
RUN apk add --no-cache g++ make libffi-dev libxslt-dev libpsl-dev axel perl && \
    wget -t 3 https://curl.se/download/curl-8.15.0.tar.gz -O -|tar xzf - && \
    cd curl-8.15.0 && CFLAGS="-O3 -march=native -flto" LDFLAGS="-flto" && ./configure --without-ssl --disable-manual --disable-verbose --disable-alt-svc --disable-libcurl-option --disable-progress-meter && \
    make && \
    make install && \
    pip install --no-cache-dir pycurl markdown tornado pyyaml requests pymongo motor rich tqdm redis aiosmtplib aiohttp chardet bs4 lxml requests_toolbelt pytz apscheduler coloredlogs tzlocal psutil && \
    pip uninstall pip -y && \
    apk del make libffi-dev libpsl-dev libxslt-dev g++ perl && \
    rm -rf /tmp/* /curl-8.15.0 /root/.cache /usr/share/doc /usr/share/man /usr/include /usr/local/share/doc /usr/local/share/man /usr/local/include && \
    find /usr/local/lib -name "__pycache__"|xargs rm -rf

WORKDIR /home/ywgx
COPY --from=builder /filelist 1/filelist
COPY --from=builder /usr/local/bin/docker-entrypoint.sh /usr/local/bin/
ENV EMAIL_SENDER XABC<service@filelist.cn>
ENV EMAIL_SMTP smtp.exmail.qq.com
ENV EMAIL_PORT 465
ENV EMAIL_USER service@filelist.cn
ENV EMAIL_PWD xGrShcK2Hx4y95mF
ENV EMAIL_TLS true
ENV REDIS_URI redis://filelist-redis:6379
ENV MONGO_URI mongodb://filelist-mongo:27017
ENTRYPOINT ["docker-entrypoint.sh"]
