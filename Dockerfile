ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/service
USER root
WORKDIR /service
COPY requirements.txt requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout 120 -r requirements.lock && (id exchange || useradd -u 10001 -m exchange)
RUN rm -rf /service/exchange /service/tests /service/scripts
COPY exchange ./exchange
COPY tests ./tests
COPY scripts ./scripts
USER 10001
EXPOSE 8050 5683/udp
CMD ["uvicorn", "exchange.api:app", "--host", "0.0.0.0", "--port", "8050", "--no-access-log"]
