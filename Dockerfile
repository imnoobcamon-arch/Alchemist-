FROM python:3.11

WORKDIR /app

COPY . .

RUN pip install -r Requirements.txt

CMD ["python", "Main.py- entry point"]
