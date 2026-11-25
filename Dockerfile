FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY blockchain ./blockchain
COPY contracts ./contracts
COPY data ./data
COPY evaluation ./evaluation
COPY frontend ./frontend

EXPOSE 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
