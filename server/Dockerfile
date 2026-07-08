FROM python:3.13-slim AS build

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.1.3 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

RUN pip install "poetry==$POETRY_VERSION"

COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --only main

COPY src ./src

COPY llm-events.sql ./llm-events.sql

EXPOSE 8000

CMD [ "uvicorn", "server:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000" ]