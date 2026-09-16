FROM node:22-alpine AS build
ARG VCS_REF=development
ARG BUILD_DATE=unknown
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web ./
RUN npm run build
RUN node -e 'require("node:fs").writeFileSync("dist/version.json",JSON.stringify({commit:process.env.VCS_REF,build:process.env.BUILD_DATE,component:"web"}))'

FROM nginx:1.28-alpine
ARG VCS_REF=development
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.source="https://github.com/SaitWors/campus-flow" \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.created=$BUILD_DATE
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
