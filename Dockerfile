# System Process Monitor - headless watchdog agent
#
# The desktop window needs a display, so the container runs the collector and
# the local watchdog agent in console mode and keeps everything in /data.
#
#   docker build -t system-process-monitor .
#   docker run -d --name spm-agent --pid=host -v spm-data:/data system-process-monitor

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# headless mode only needs psutil (no Tk, no customtkinter)
COPY requirements-agent.txt .
RUN pip install -r requirements-agent.txt

COPY main.py .
COPY sql ./sql
COPY spm ./spm

RUN useradd --create-home --uid 10001 spm \
    && mkdir -p /data \
    && chown spm:spm /data
USER spm

VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "spm.healthcheck", "--db", "/data/monitor.db", "--max-age", "30"]

ENTRYPOINT ["python", "main.py", "--headless", "--db", "/data/monitor.db"]
CMD ["--interval", "2", "--retention", "24"]
