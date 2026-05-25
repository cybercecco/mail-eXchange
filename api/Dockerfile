FROM python:3.12-slim

WORKDIR /app
COPY api/requirements.txt /app/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker-cli curl openssl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /usr/libexec/docker/cli-plugins \
    && curl -fsSL "https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-x86_64" \
        -o /usr/libexec/docker/cli-plugins/docker-compose \
    && chmod +x /usr/libexec/docker/cli-plugins/docker-compose \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY api/app /app/app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
