# syntax=docker/dockerfile:1

# One recipe, two images. Every FROM opens a new *stage*:
#   base    - Python plus the runtime dependencies; shared by the two stages below
#   test    - base plus pytest and the tests; built only on request (--target test)
#   runtime - base plus the application; the image that ships
# Without --target, Docker builds the LAST stage, so runtime must stay last: that keeps
# `docker compose up --build` producing exactly the image it always did. BuildKit builds
# only the stages the requested target depends on, so a runtime build never runs the test stage.

# =============================================================================================
# Stage 1 - base
# =============================================================================================

# python:3.12-slim matches the Python the project is developed against. "slim" is the
# Debian-based image without the build toolchain and docs - roughly 10x smaller than the
# full "python:3.12" and still able to install every wheel this project needs.
FROM python:3.12-slim AS base

# PYTHONDONTWRITEBYTECODE - do not litter the image layer with .pyc files
#                           (because container is temporal and doesn't need the compiled binary pyc cache).
# PYTHONUNBUFFERED=1     - turns buffering OFF: every print goes to stdout immediately.
#                           Without it, Python's default is to hold output in RAM and flush it
#                           only when the buffer fills or the process exits - so
#                           `docker compose logs` would lag, and a crash could lose the last lines.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies are copied and installed BEFORE the application source, and that order is the
# whole point. Docker caches each instruction as a layer and invalidates every layer after the
# first one that changed. With this order, editing app/ reuses the cached pip layer; with the
# reverse order, every code change would reinstall all dependencies.
COPY requirements.txt .
# --no-cache-dir means that when we install libraries - we won't save them in cache.
# That's because there's no future use: The image is built once and remains static.
# No one will run pip install inside the live container, so there is no reason to keep temporary installation files.
RUN pip install --no-cache-dir -r requirements.txt

# =============================================================================================
# Stage 2 - test: run the suite in a fixed Python, whatever the host has installed
# =============================================================================================

FROM base AS test

# requirements-dev.txt starts with "-r requirements.txt"; those are already installed in base,
# so pip only adds pytest, pytest-asyncio and httpx on top.
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY pytest.ini conftest.py ./
COPY app/ ./app/
COPY tests/ ./tests/

# Runs as root on purpose: this container is thrown away after one run and is never exposed.
CMD ["pytest", "-v"]

# =============================================================================================
# Stage 3 - runtime: the image that ships (must stay the last stage, see the top of the file)
# =============================================================================================

FROM base AS runtime

# Only the application. No tests, no pytest: those exist only in the test stage above.
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
