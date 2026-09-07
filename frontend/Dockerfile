# HR Manager frontend: build with Node, serve with nginx (SPA + API proxy).
# Node version must stay in sync with .github/workflows/ci.yml (NODE_VERSION)
# and frontend/package.json ("engines.node").
FROM node:22.22.3-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
# The postinstall hook references scripts/ci-upgrade-npm.mjs, so the scripts
# directory must be present before `npm ci` runs.
COPY scripts ./scripts
RUN npm ci
COPY . .
# VITE_API_BASE_URL defaults to "/api", which nginx proxies to the backend.
RUN npm run build

# nginx-unprivileged runs as a non-root user (uid 101) and listens on 8080.
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
