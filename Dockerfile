FROM python:3.11-slim

# Pasang zona waktu Asia/Jakarta (WIB) & tools esensial
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    git \
    ca-certificates \
    procps \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Jakarta
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Salin dependencies & pasang via pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Salin script bot
COPY bot.py .

# Buat folder workspace kerja
RUN mkdir -p /workspace

CMD ["python", "bot.py"]
