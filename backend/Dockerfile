FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1

RUN useradd --system --uid 10001 --create-home --home-dir /home/firmadoc firmadoc

WORKDIR /app/backend

COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=firmadoc:firmadoc backend/ ./
COPY --chown=firmadoc:firmadoc frontend/ /app/frontend/

RUN mkdir -p /tmp/firmadoc && chown firmadoc:firmadoc /tmp/firmadoc

USER firmadoc

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
