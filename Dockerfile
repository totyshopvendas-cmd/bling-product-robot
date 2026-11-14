FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --legacy-peer-deps --no-audit --no-fund
COPY frontend/ ./
ENV REACT_APP_BACKEND_URL=
ENV CI=true
RUN rm -rf node_modules/fork-ts-checker-webpack-plugin/node_modules \
 && DISABLE_ESLINT_PLUGIN=true npm run build

FROM python:3.11-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND_BUILD=/app/frontend/build \
    PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000
# Instala as bibliotecas do navegador separadamente. O Chromium é baixado
# depois que o painel já iniciou (em segundo plano); isso evita que builders
# gerenciados encerrem uma imagem saudável durante o download grande.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
RUN python -m playwright install-deps chromium
COPY backend /app/backend
COPY --from=frontend /frontend/build /app/frontend/build
WORKDIR /app/backend
EXPOSE 8000
# Plataformas gerenciadas injetam a porta em $PORT; no Docker local mantém 8000.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
