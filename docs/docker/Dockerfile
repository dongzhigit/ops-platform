# The web bundle is architecture-independent. Build it on the native builder
# platform instead of emulating the legacy linux/amd64 runtime on ARM hosts.
FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS web-builder

WORKDIR /build/spug_web
RUN corepack enable
COPY spug_web/package.json spug_web/yarn.lock ./
RUN yarn install --frozen-lockfile --non-interactive
COPY spug_web/ ./
# react-scripts 3 uses webpack 4, which needs the legacy OpenSSL provider on
# current Node releases. This affects only the build stage, not the runtime.
RUN NODE_OPTIONS=--openssl-legacy-provider yarn build


FROM centos:7.9.2009

ENV TZ=Asia/Shanghai

# CentOS 7 and EPEL 7 are end-of-life, so their normal mirror lists no longer
# resolve. Keep the legacy runtime buildable during M0 while the application is
# migrated to a supported Python and operating-system baseline.
RUN sed -i \
    -e 's|^mirrorlist=|#mirrorlist=|' \
    -e 's|^#baseurl=http://mirror.centos.org/centos/$releasever|baseurl=https://vault.centos.org/7.9.2009|' \
    /etc/yum.repos.d/CentOS-*.repo \
    && yum install -y epel-release
COPY docs/docker/epel-archive.repo /etc/yum.repos.d/epel.repo
RUN yum update -y --setopt=tsflags=nodocs \
    && yum install -y --setopt=tsflags=nodocs nginx redis mariadb-devel python36 python36-devel openldap-devel supervisor git gcc curl wget unzip net-tools sshpass rsync sshfs \
    && yum -y clean all

RUN pip3 install --no-cache-dir --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
RUN pip3 install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    gunicorn \
    mysqlclient \
    cryptography==36.0.2 \
    apscheduler==3.7.0 \
    asgiref==3.2.10 \
    Django==2.2.28 \
    channels==2.3.1 \
    channels_redis==2.4.1 \
    paramiko==2.11.0 \
    django-redis==4.10.0 \
    requests==2.22.0 \
    GitPython==3.0.8 \
    python-ldap==3.4.0 \
    openpyxl==3.0.3 \
    user_agents==2.2.0

RUN localedef -c -i en_US -f UTF-8 en_US.UTF-8
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8
RUN echo -e '\n# Source definitions\n. /etc/profile\n' >> /root/.bashrc
RUN mkdir -p /data/repos /data/spug/spug_api/logs /data/spug/spug_api/storage
COPY spug_api/ /data/spug/spug_api/
COPY --from=web-builder /build/spug_web/build/ /data/spug/spug_web/build/
COPY docs/docker/init_spug /usr/bin/
COPY docs/docker/nginx.conf /etc/nginx/
COPY docs/docker/ssh_config /etc/ssh/
COPY docs/docker/spug.ini /etc/supervisord.d/
COPY docs/docker/redis.conf /etc/
COPY docs/docker/entrypoint.sh /

ARG OPS_PLATFORM_VERSION=0.1.0-alpha.14
LABEL org.opencontainers.image.title="Spug Ops Platform" \
      org.opencontainers.image.version="${OPS_PLATFORM_VERSION}"
ENV OPS_PLATFORM_VERSION=${OPS_PLATFORM_VERSION}

EXPOSE 80
ENTRYPOINT ["/entrypoint.sh"]
