FROM python:3.14-slim@sha256:1a3c6dbfd2173971abba880c3cc2ec4643690901f6ad6742d0827bae6cefc925

WORKDIR /app

# Install dependencies (pinned + hashed)
COPY requirements.lock .
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

# Copy application code
COPY app/ ./app/
COPY worker.py .

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# Expose API port
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

# Default command (can be overridden)
CMD ["python", "-m", "app.main"]
