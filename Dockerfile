FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY meemee ./meemee
RUN pip install --no-cache-dir .
RUN useradd --create-home meemee
USER meemee
EXPOSE 8787
CMD ["meemee", "serve", "--host", "0.0.0.0", "--port", "8787"]
