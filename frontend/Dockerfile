# Build the bundle with node, then throw node away and serve the files with
# nginx. The runtime image carries no toolchain and no source.

FROM node:22-alpine AS build

WORKDIR /app

# Copied first so a dependency install is only redone when the lockfile moves.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build


FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# 127.0.0.1, not localhost: that resolves to ::1 first in here, and nginx
# listens on IPv4 only.
HEALTHCHECK --interval=60s --timeout=3s --start-period=10s --start-interval=2s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
