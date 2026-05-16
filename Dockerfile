FROM python:3.11-slim

WORKDIR /app

# System deps for Playwright
RUN apt-get update && apt-get install -y \
    curl wget gnupg ca-certificates \
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
    libgbm1 libasound2 libxshmfence1 libgtk-3-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy source and install Python deps
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir ".[api]"

# Install Playwright browser
RUN playwright install chromium --with-deps

EXPOSE 8000

CMD ["uvicorn", "brazil_lmm.api:app", "--host", "0.0.0.0", "--port", "8000"]
