FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home appuser

WORKDIR /app
COPY pyproject.toml ./
COPY desk ./desk
RUN pip install --no-cache-dir .

USER appuser
# ~/.tradingagents holds the ChatGPT sign-in, the memory log and reports: mount it.
EXPOSE 8000
CMD ["uvicorn", "desk.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
