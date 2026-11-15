# syntax=docker/dockerfile:1
# trading-desk 웹 배포 이미지 (docs/deploy.md) — React 빌드 결과를 nginx 가 서빙하고 /api/ 를 백엔드로 넘긴다.
#
# 멀티 스테이지다. 배포 호스트에서 바로 빌드한다. 산출물은 정적 파일뿐이라 네이티브 바이너리 · 아키텍처 문제가 없다.
# 베이스 태그는 정확한 버전으로 고정한다.
ARG NODE_IMAGE=node:22.21.1-bookworm-slim
ARG NGINX_IMAGE=nginx:1.28.0-alpine

FROM ${NODE_IMAGE} AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
# #377 public/(sw.js · 매니페스트 · 아이콘)은 Vite 가 dist 루트로 그대로 복사한다
COPY public ./public
RUN npm run build

FROM ${NGINX_IMAGE}
# nginx 공식 이미지는 /etc/nginx/templates/*.template 을 기동 시 envsubst 해 conf.d 에 둔다.
# 백엔드 주소(BACKEND_UPSTREAM)를 compose 가 넘긴다 — 컨테이너 이름이 환경마다 달라도 이미지는 하나다
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 80
