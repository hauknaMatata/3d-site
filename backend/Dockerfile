# Backend + CAD engine (OpenCascade via CadQuery). Needs ~2 GB RAM for heavy CAD booleans.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends \
      assimp-utils libgl1 libglu1-mesa libxrender1 libxext6 libxi6 libxcursor1 libxft2 libxinerama1 \
      libfontconfig1 libfreetype6 libcairo2 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

RUN useradd -m -u 1000 studio && mkdir -p /data && chown studio /data
USER studio
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
