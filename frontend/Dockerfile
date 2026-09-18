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
FROM node:25-alpine@sha256:bdf2cca6fe3dabd014ea60163eca3f0f7015fbd5c7ee1b0e9ccb4ced6eb02ef4 AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build
# Output: /app/out/

# Serve with a lightweight static server (for standalone testing)
FROM node:25-alpine@sha256:bdf2cca6fe3dabd014ea60163eca3f0f7015fbd5c7ee1b0e9ccb4ced6eb02ef4
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
