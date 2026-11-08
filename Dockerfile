FROM python:3.12-slim

WORKDIR /app

# Minimal OS deps for pygame / chromadb wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsdl2-2.0-0 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt itsdangerous

COPY ailive2d ./ailive2d
COPY web ./web
COPY run.py config.yaml ./

RUN mkdir -p /app/data/chroma

ENV AILIVE2D_HOST=0.0.0.0 \
    AILIVE2D_PORT=8787 \
    PYTHONUNBUFFERED=1

EXPOSE 8787
CMD ["python", "run.py"]
