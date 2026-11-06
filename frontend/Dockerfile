# Base images can be redirected to an internal registry mirror (air-gapped plants), e.g.
#   docker compose build --build-arg NODE_IMAGE=registry.firma.local/node:22-alpine ...
ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginx:1.27-alpine

FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM ${NGINX_IMAGE}
COPY nginx/security-headers.conf nginx/app.conf nginx/http.conf nginx/tls.conf /etc/nginx/fp/
COPY nginx/entrypoint.sh /usr/local/bin/fp-nginx-entrypoint.sh
COPY --from=build /app/dist /usr/share/nginx/html
RUN chmod +x /usr/local/bin/fp-nginx-entrypoint.sh && rm -f /etc/nginx/conf.d/default.conf
EXPOSE 80 443
ENTRYPOINT ["/usr/local/bin/fp-nginx-entrypoint.sh"]
