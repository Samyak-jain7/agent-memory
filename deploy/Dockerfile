FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY apps apps
COPY packages packages
COPY migrations migrations
COPY scripts scripts
COPY deploy deploy
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home memory
USER memory
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python","-m","scripts.serve","api","--host","0.0.0.0"]
