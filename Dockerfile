FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY meemee ./meemee
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 meemee && mkdir -p /home/meemee/.meemee && chown -R meemee:meemee /home/meemee
USER 10001
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/ready', timeout=2)"
CMD ["meemee", "serve", "--host", "0.0.0.0", "--port", "8787"]
