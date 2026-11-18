FROM python:3.13-alpine
WORKDIR /app
COPY server.py .
COPY static ./static
ENV PYTHONUNBUFFERED=1
CMD ["python", "server.py"]
