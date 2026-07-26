FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ core/
COPY feeds/ feeds/
COPY dashboard/ dashboard/
COPY alerts/ alerts/
COPY main.py server.py config.json ./

RUN mkdir -p /app/logs /app/data

EXPOSE 8080

CMD ["python3", "server.py"]
