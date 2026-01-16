FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

# 🔹 Timezone
ENV TZ=Asia/Kolkata

# 🔹 System dependencies (audio + timezone)
RUN apt-get update && apt-get install -y \
    tzdata \
    ffmpeg \
    libsndfile1 \
    libportaudio2 \
    libgl1 \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 🔹 Working directory
WORKDIR /app

# 🔹 Python dependencies (layer caching)
COPY requirements.txt /app/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 🔹 Application files (Outbound only)
COPY agent.py /app/
COPY prompts.py /app/
COPY make_call.py /app/

# 🔹 Start outbound agent
CMD ["python", "agent.py", "start"]
