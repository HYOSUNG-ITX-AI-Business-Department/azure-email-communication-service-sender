FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY worker.py .

# Expose API port
EXPOSE 8000

# Default command (can be overridden)
CMD ["python", "-m", "app.main"]
