# Imagem do backend. Uma imagem serve todos os ambientes: o que muda vem do
# ambiente (Secret/ConfigMap), nunca da imagem.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Camada de dependencias separada: so reinstala quando requirements.txt muda.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY app ./app

# Sem root: mesma postura da imagem do front (nginx-unprivileged, uid 101).
RUN useradd --uid 1001 --create-home app && chown -R app:app /app
USER 1001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
