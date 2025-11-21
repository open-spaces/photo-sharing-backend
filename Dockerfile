# syntax=docker/dockerfile:1.4
# FastAPI Backend Dockerfile - Optimized for faster builds

# ============================================================================
# Stage 1: Build dependencies
# ============================================================================
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies with cache mount (speeds up apt-get)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    libfreetype6-dev \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    libopencv-dev \
    python3-opencv

# Copy only requirements first (better layer caching - only rebuilds if requirements change)
COPY requirements.txt .

# Install Python dependencies with pip cache mount (reuses downloads across builds)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# ============================================================================
# Stage 2: Runtime image (smaller, no build tools)
# ============================================================================
FROM python:3.11-slim

WORKDIR /app

# Install only runtime dependencies (not build tools like gcc)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    libpng16-16 \
<<<<<<< HEAD
    libtiff6 \
=======
    libtiff5 \
>>>>>>> bf0e47add6117223dddc9b0bda6457e882c75293
    libfreetype6 \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    python3-opencv \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code (use .dockerignore to exclude unnecessary files)
COPY . .

# Create uploads directory with proper permissions
RUN mkdir -p uploads data && chmod 755 uploads data

# Set environment variables with defaults
ENV SECRET_KEY=${SECRET_KEY:-default_secret_key}
ENV GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
ENV ALGORITHM=${ALGORITHM:-HS256}
ENV ACCESS_TOKEN_EXPIRE_MINUTES=${ACCESS_TOKEN_EXPIRE_MINUTES:-30}
ENV SERVER_HOST=${SERVER_HOST:-0.0.0.0}
ENV SERVER_PORT=${SERVER_PORT:-8000}
ENV PUBLIC_URL=${PUBLIC_URL:-http://localhost:8000}
ENV UPLOAD_DIR=${UPLOAD_DIR:-uploads}
ENV MAX_FILE_SIZE=${MAX_FILE_SIZE:-5242880}
ENV DB_URL=${DB_URL:-sqlite:///./data/app.db}

# Python optimizations
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose the port that will be used
EXPOSE ${SERVER_PORT:-8000}

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:${SERVER_PORT:-8000}/guest')" || exit 1

# Run the FastAPI application using config values
CMD uvicorn main:app --host ${SERVER_HOST:-0.0.0.0} --port ${SERVER_PORT:-8000}
