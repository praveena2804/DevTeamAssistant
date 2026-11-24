# Container image for running the generated backend yourself (delivered as a file; DevTeam AI does not
# build or run it). Build: docker build -t my-app .   Run: docker run -p 8000:8000 --env-file .env my-app
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.165.0 uvicorn==0.54.0 pydantic==2.14.3 sqlalchemy==2.1.9 alembic==1.20.1
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
RUN useradd -u 10001 -M app && mkdir -p /app/backend/data && chown -R app /app/backend/data
USER app
WORKDIR /app/backend
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
