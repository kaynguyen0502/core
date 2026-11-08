FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY smartlab ./smartlab
COPY tools ./tools
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "smartlab.web:app", "--host", "0.0.0.0", "--port", "8000"]
