# Container do pipeline (extração + transformação). Roda e termina.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pipeline/ pipeline/
COPY sql/ sql/
COPY tests/ tests/
RUN useradd -u 1000 -m pipeline
USER pipeline
ENTRYPOINT ["python", "-m", "pipeline"]
