FROM docker:cli AS docker_cli
FROM python:3.14-slim
COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker
COPY requirements-lock.txt /tmp/requirements-lock.txt
RUN docker --version && python -m pip install --no-cache-dir -r /tmp/requirements-lock.txt
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/codefix
CMD ["python", "-B", "-m", "app.execution_server", "serve", "--host", "0.0.0.0", "--port", "8780"]
