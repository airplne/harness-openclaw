FROM ghcr.io/openclaw/openclaw:latest

USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY runner_common.py worker_loop.py review_loop.py ./
RUN chmod +x /app/worker_loop.py /app/review_loop.py
USER node
