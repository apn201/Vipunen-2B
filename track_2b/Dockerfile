FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY src ./src
COPY config ./config
COPY data ./data
COPY tests ./tests
COPY tools ./tools
RUN pip install --no-cache-dir -e ".[dev]"
EXPOSE 8000
CMD ["vipunen", "run", "--demo"]
