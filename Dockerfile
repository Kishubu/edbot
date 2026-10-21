FROM python:3.13-slim
WORKDIR /app
COPY edbot.py edsync.py edmail.py ed2fa.py ./
RUN chmod 644 *.py
CMD ["python3", "-u", "edbot.py"]
