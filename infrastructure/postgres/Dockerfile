FROM postgres:16-alpine
COPY init.sh /docker-entrypoint-initdb.d/init.sh
RUN chmod 644 /docker-entrypoint-initdb.d/init.sh
