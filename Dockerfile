FROM python:3.11-slim

WORKDIR /app

COPY . .

RUN pip install -r Requirements.txt

CMD ["python", "Main.py- entry point"]
