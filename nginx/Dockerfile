# Build context: repository root (see docker-compose.yml)
# Vite 7 needs Node 20.19+ or 22.12+
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
# Real API mode by default; VITE_USE_MOCK=true explicitly enables sample data
ARG VITE_API_BASE_URL=/api/v1
ARG VITE_USE_MOCK=false
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_USE_MOCK=$VITE_USE_MOCK
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
