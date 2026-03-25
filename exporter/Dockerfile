FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY exporter.py .

# Run as non-root
RUN useradd -r -u 1000 cellsentry
USER cellsentry

EXPOSE 9101
CMD ["python", "exporter.py"]
