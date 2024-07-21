FROM python:3.10-slim-buster

COPY app /app

WORKDIR /app

RUN pip install --no-cache-dir -r /app/requirements.txt

CMD ["python", "main.py"]
