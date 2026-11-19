FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt acmecse==2026.5.3
COPY rescuetwin ./rescuetwin
COPY tools ./tools
COPY config.example.json ./config.example.json
CMD ["python", "-m", "rescuetwin", "bridge"]
