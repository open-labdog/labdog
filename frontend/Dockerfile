# Frontend build-only image (static export)
# For production, use the root Dockerfile which builds frontend + backend together.
# This file is useful for CI or standalone frontend builds.
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build
# Output: /app/out/

# Serve with a lightweight static server (for standalone testing)
FROM node:20-alpine
RUN npm install -g serve
WORKDIR /app
COPY --from=builder /app/out ./out
EXPOSE 3000
CMD ["serve", "out", "-l", "3000", "-s"]
