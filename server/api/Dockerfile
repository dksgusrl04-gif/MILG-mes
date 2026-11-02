# MLNG MES API — FastAPI + uvicorn (+ 공정 통합 AI: ai/mlng_ai)
# 빌드 위치는 저장소 루트 (docker-compose: context ..) — ai/ 폴더를 같이 넣기 위해
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY server/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server/api/app ./app
COPY ai/mlng_ai ./mlng_ai
COPY ai/state/model.json ./ai_seed/model.json
# root 가 아닌 사용자로 실행 · AI 누적 상태는 /data/ai (docker 볼륨)
RUN useradd -r -u 10001 api && mkdir -p /data/ai && chown -R api /srv /data/ai
USER api
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
