ARG BUILD_FROM
FROM $BUILD_FROM

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

COPY src /app/src
COPY run.sh /app/run.sh
RUN chmod a+x /app/run.sh

WORKDIR /app
CMD ["/app/run.sh"]
