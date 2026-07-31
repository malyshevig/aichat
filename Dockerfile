# 1. Используем легкий официальный образ Python (slim весит меньше, чем полный)
FROM python:3.12-slim

# 2. Настраиваем переменные окружения для корректной работы Python в контейнере
# PYTHONUNBUFFERED=1 заставляет Python выводить логи в консоль сразу, без буферизации
# PYTHONDONTWRITEBYTECODE=1 запрещает создание .pyc файлов (экономит место и I/O)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 3. Создаем виртуальное окружение внутри контейнера
RUN python -m venv /opt/venv

# 4. "Активируем" venv для всех последующих команд в Dockerfile
# Это правильный способ активации в Docker (вместо source venv/bin/activate)
ENV PATH="/opt/venv/bin:$PATH"
ENV HOME=/tmp

# 5. Устанавливаем рабочую директорию
WORKDIR /app

# 6. Копируем ТОЛЬКО файл зависимостей и устанавливаем их.
# Это делается ДО копирования всего кода, чтобы Docker кэшировал этот слой.
# Если код изменится, но requirements.txt останется прежним, pip install не будет запускаться заново.
COPY requirements.txt .

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/* # Очищаем кэш apt, чтобы не раздувать образ


RUN python -m pip install --no-cache-dir -r requirements.txt

# 7. Копируем весь остальной код приложения
COPY . .

# 8. Создаем непривилегированного пользователя для безопасности (Best Practice)
RUN useradd -r -s /bin/false appuser && \
    chown -R appuser:appuser /app

# 9. Переключаемся на непривилегированного пользователя
USER appuser

# 10. Указываем порт (опционально, для документации)
EXPOSE 8000

# 11. Команда запуска приложения
# Замените на свою команду (например, uvicorn, gunicorn, или python bot.py)
CMD ["streamlit","run", "chat.py"]