# syntax=docker/dockerfile:1
# trading-desk 백엔드 배포 이미지 (docs/deploy.md).
#
# 멀티 스테이지다. 빌드 agent 가 따로 없어 배포 호스트(backend-server, x86_64)에서 이 파일로 바로 빌드한다.
#   build   — venv 에 의존성 설치. TradingAgents 를 git 커밋 SHA 로 받으므로 git 이 필요하다
#   runtime — venv 만 복사한다. git · 컴파일러 · pip 캐시가 남지 않는다
#
# 베이스 태그는 정확한 버전으로 고정한다(latest · 3.12-slim 금지). 두 스테이지를 같이 올린다.
ARG PYTHON_IMAGE=python:3.12.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS build
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /src
# 의존성 층을 소스와 갈라 둔다 — desk/ 만 바뀌면 TradingAgents · langchain 설치를 다시 하지 않는다
COPY pyproject.toml ./
RUN mkdir -p desk && touch desk/__init__.py && pip install . && pip uninstall -y trading-desk
COPY desk ./desk
RUN pip install --no-deps .

FROM ${PYTHON_IMAGE}
# root 로 돌리지 않는다. 쓰는 곳은 ~/.tradingagents(ChatGPT 로그인 · 리포트 · 캐시, compose 볼륨) 하나다.
# 디렉터리를 이미지에 미리 만들어 두면 빈 명명 볼륨이 처음 붙을 때 이 소유자(app)를 물려받는다
RUN groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
    && mkdir -p /home/app/.tradingagents && chown app:app /home/app/.tradingagents
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
USER app

# 컨테이너 안 포트. 호스트에는 열지 않는다 — 웹 컨테이너만 같은 docker 네트워크로 닿는다(docs/security.md §4)
EXPOSE 8000

# - exec 형식: uvicorn 이 PID 1 이라 docker stop 의 SIGTERM 으로 graceful shutdown 한다
# - --proxy-headers: 클라이언트 IP 는 X-Forwarded-For 에서 읽는다. 웹 nginx 가 그 값을 인그레스가 보증한 한 주소로 덮어쓴다
# - 기동 시 Alembic upgrade head 가 앱 안에서 돈다(desk.db.migrate)
CMD ["uvicorn", "desk.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "20"]
