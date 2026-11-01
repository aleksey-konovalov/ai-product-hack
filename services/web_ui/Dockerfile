FROM nginx:alpine

COPY services/web_ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY services/web_ui/static/ /usr/share/nginx/html

EXPOSE 80

# /health проксируется в agent — проверяем связку web_ui + agent целиком
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -qO- http://localhost/health >/dev/null || exit 1
