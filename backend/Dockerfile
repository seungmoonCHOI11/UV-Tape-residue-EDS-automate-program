FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2 MALLOC_TRIM_THRESHOLD_=131072 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 OPENCV_OPENCL_RUNTIME=disabled

RUN apt-get update && apt-get install -y \
    libglib2.0-0 libgl1 libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data
COPY .env.example ./

EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
