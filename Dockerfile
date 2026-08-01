# ប្រើប្រាស់ Python ជំនាន់ចុងក្រោយ
FROM python:3.11-slim

# ដំឡើងកម្មវិធី ffmpeg ដែល yt-dlp ត្រូវការជាចាំបាច់
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# កំណត់ទីតាំងធ្វើការងារក្នុង Container
WORKDIR /app

# ចម្លងឯកសារ requirements ចូលនិងដំឡើង
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ចម្លងកូដទាំងអស់ចូល
COPY . .

# បញ្ជាឲ្យដំណើរការកូដ
CMD ["python", "botx.py"]
