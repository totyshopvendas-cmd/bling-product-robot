FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install
COPY frontend/ ./
ENV REACT_APP_BACKEND_URL=
ENV CI=true
RUN rm -rf node_modules/fork-ts-checker-webpack-plugin/node_modules \
 && DISABLE_ESLINT_PLUGIN=true npm run build

FROM python:3.11-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND_BUILD=/app/frontend/build
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend /app/backend
COPY --from=frontend /frontend/build /app/frontend/build
WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
