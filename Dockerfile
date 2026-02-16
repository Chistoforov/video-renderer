FROM python:3.11-slim

# FFmpeg + wget для скачивания шрифта
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg wget && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код
COPY . .

# Создаём папки
RUN mkdir -p output assets

# Скачиваем шрифт Montserrat Bold (поддерживает кириллицу)
RUN wget -q -O assets/font.ttf \
    "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf" && \
    apt-get purge -y wget && apt-get autoremove -y

EXPOSE 5000

# 1 воркер — экономим память на Free Tier (512MB)
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000", "--timeout", "120", "--workers", "1"]
