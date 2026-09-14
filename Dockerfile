FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY newsbot/ ./newsbot/

# sources.yml and data/ are mounted from the host so edits and the dedupe
# database survive a rebuild.
VOLUME ["/app/data"]

RUN useradd --create-home --uid 1000 newsbot && chown -R newsbot /app
USER newsbot

CMD ["python", "-m", "newsbot"]
