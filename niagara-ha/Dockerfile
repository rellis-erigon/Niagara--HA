ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip py3-requests

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

COPY src /app/src
COPY run.sh /app/run.sh
RUN chmod a+x /app/run.sh

WORKDIR /app
CMD ["/app/run.sh"]
