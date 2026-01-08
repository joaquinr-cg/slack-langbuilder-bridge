FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code
COPY src/ ./src/

# Create data directory for SQLite
RUN mkdir -p /app/data

# Default environment variables
ENV DATABASE_PATH=/app/data/sessions.db
ENV LOG_LEVEL=INFO

# Run the bot
CMD ["python", "-m", "src.main"]
