# Dr.Tracker Outreach: one container runs the UI, the API and the background worker.
# Run exactly one container per SQLite database (see docs/DEPLOYMENT.md).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=root:root backend ./backend
COPY --chown=root:root frontend ./frontend
COPY --chown=root:root scripts ./scripts
COPY --chown=root:root samples ./samples
# Email templates per audience (writing guide + images); Setup → Email templates can override them in /data.
# Brands: brand_outreach_agent.md and the three product-flow screenshots at the repo root.
COPY --chown=root:root templates ./templates
COPY --chown=root:root ["brand_outreach_agent.md", "Step 1 – Asks in chat@2x.png", "Step 2 – Sees it first@2x.png", "Step 3 – Buys with a code@2x.png", "./"]

RUN mkdir -p /data && chown app:app /data && chmod 700 /data

ENV OUTREACH_DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8000

VOLUME /data
EXPOSE 8000
USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
