ARG BUILD_FROM
FROM $BUILD_FROM

# Supervisor passes the version from config.yaml, so the running add-on reports
# the version it was actually built from rather than a hand-typed constant.
ARG BUILD_VERSION
ENV NIAGARA_HA_VERSION=${BUILD_VERSION}

RUN apk add --no-cache python3 py3-pip py3-requests py3-yaml

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

COPY src /app/src
COPY static /app/static
COPY rootfs /
