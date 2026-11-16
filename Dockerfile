FROM python:3.10-slim

WORKDIR /app

# Install system dependencies including Node.js for .js bot support
RUN apt-get update && apt-get install -y \
    nodejs \
    npm \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt --break-system-packages

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]
