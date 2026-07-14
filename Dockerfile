# ============================================================
# ATLAS — Production Dockerfile
# Multi-stage build for the LangGraph AI Chatbot
# ============================================================

FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies for FAISS and PDF processing
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------
# Dependencies stage
# -----------------------------------------------------------
FROM base AS dependencies

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# -----------------------------------------------------------
# Application stage
# -----------------------------------------------------------
FROM dependencies AS app

# Copy application source
COPY *.py ./

# Create directory for SQLite database persistence
RUN mkdir -p /app/data

# Expose Streamlit default port
EXPOSE 8501

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# Default entrypoint: run the RAG-enabled frontend (most feature-complete)
ENTRYPOINT ["streamlit", "run", "streamlit_rag_frontend.py", \
            "--server.port=8501", \
            "--server.address=0.0.0.0", \
            "--server.headless=true"]
