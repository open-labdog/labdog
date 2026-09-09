# Frontend build-only image (static export)
#
# NOT FOR DEPLOYMENT. Use the root Dockerfile, which builds frontend and
# backend together and serves the export from FastAPI. That path is the
# one that sends the security headers (CSP, frame-ancestors, HSTS) from
# SecurityHeadersMiddleware and serves /api on the same origin, so the
# session cookie and the CSRF double-submit work as designed. `serve`
# below sends none of them and knows nothing about /api.
#
# It exists for CI and for looking at the static export on its own.
FROM node:24-alpine@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build
# Output: /app/out/

# Serve with a lightweight static server (for standalone testing)
FROM node:24-alpine@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf
# Pinned: an unpinned `npm install -g serve` resolves to whatever is
# newest at build time, which is the same floating-dependency problem the
# release path was just fixed for.
RUN npm install -g serve@14.2.6
WORKDIR /app
COPY --from=builder /app/out ./out
# node:alpine ships an unprivileged `node` user (uid 1000). Nothing here
# writes, so read-only ownership is enough.
USER node
EXPOSE 3000
CMD ["serve", "out", "-l", "3000", "-s"]
