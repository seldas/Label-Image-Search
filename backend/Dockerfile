FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# System deps for lxml, Pillow, and healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    libjpeg62-turbo-dev \
    zlib1g-dev \
    libwebp-dev \
    && rm -rf /var/lib/apt/lists/*

# Install CPU-only PyTorch first to avoid pulling CUDA (~2GB+ saved)
RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /app/data/db /app/data/images /app/data/downloads /app/models

EXPOSE 8000
