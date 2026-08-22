# syntax=docker/dockerfile:1

# python:3.12-slim matches the Python the project is developed against. "slim" is the
# Debian-based image without the build toolchain and docs - roughly 10x smaller than the
# full "python:3.12" and still able to install every wheel this project needs.
FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE - do not litter the image layer with .pyc files 
#                           (because container is temporal and doesn't need the compiled binary pyc cache).
# PYTHONUNBUFFERED       - send logs straight to stdout instead of buffering them in RAM 
#                           (and only when it's filled / docker is closed send them to stdout)
#                            - `docker compose logs` shows output as it happens.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies are copied and installed BEFORE the application source, and that order is the
# whole point. Docker caches each instruction as a layer and invalidates every layer after the
# first one that changed. With this order, editing app/ reuses the cached pip layer; with the
# reverse order, every code change would reinstall all dependencies.
# Only the runtime set is installed - pytest and httpx stay out of the shipped image.
COPY requirements.txt .
# --no-cache-dir means that when we install libraries - we won't save them in cache.
# That's because there's no future use: The image is built once and remains static.
# No one will run pip install inside the live container, so there is no reason to keep temporary installation files.
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Containers run as root by default, which means a process that escapes the container starts
# with root privileges on whatever it landed in. An unprivileged user costs one line.
# chown pass ownership to appuser 
# (so it could write and read, without it USER appuser will try to r/w/x code and it will fail)
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Documentation only - EXPOSE publishes nothing by itself; docker-compose.yml maps the port.
EXPOSE 8000

# --host 0.0.0.0 is mandatory. uvicorn defaults to 127.0.0.1, which inside a container is the
# container's own loopback interface: the port mapping would resolve to a socket nothing is
# listening on, and every request from the host would be refused.
# That is because the container's external network cards (for requests from our computer, e.g)
# IP is diff than 127.0.0.1 - so it won't listen.
# 0.0.0.0 listens to both internal and external network cards
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
