# Multi-stage production build for the recovered Omnipath baseline.
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim AS builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Final stage
FROM python:${PYTHON_VERSION}-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the matching Python environment from the builder without hard-coding a
# minor-version site-packages path.
COPY --from=builder /usr/local /usr/local

# Copy application code
COPY backend/ ./backend/
COPY VERSION ./VERSION
COPY .env.example .env

# Create non-root user
RUN useradd -m -u 1000 omnipath && chown -R omnipath:omnipath /app
USER omnipath

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
