FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir requests pytz
COPY sports_bot.py .
RUN mkdir -p data
CMD ["python", "sports_bot.py"]
