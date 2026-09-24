FROM python:3.12-slim
WORKDIR /app
# Unbuffered stdout so docker logs show [loop]/[tg]/[email] lines immediately
ENV PYTHONUNBUFFERED=1
COPY bot.py event_reminder.py ./
# data/ is bind-mounted by docker-compose.yml (CSV/state/.env persist on host)
CMD ["python", "-u", "bot.py"]
