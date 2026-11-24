FROM python:3.11-slim
# git is not needed; the launchers only shell out to the kaggle and colab CLIs.
RUN pip install --no-cache-dir fastapi==0.115.6 uvicorn==0.34.0 kaggle google-colab-cli \
 && useradd -m -u 1000 app
ENV PYTHONUNBUFFERED=1 WEBUI_IN_DOCKER=1 \
    WEBUI_HOST=0.0.0.0 WEBUI_PORT=8765 \
    LAUNCHER_DIR=/repo/tools/launchers \
    VISOSWAP_HOME=/data/visoswap \
    KAGGLE_CONFIG_DIR=/data/kaggle \
    KAGGLE_BIN=/usr/local/bin/kaggle \
    COLAB_BIN=/usr/local/bin/colab \
    HOME=/home/app
USER app
WORKDIR /repo/tools/launchers/webui
EXPOSE 8765
HEALTHCHECK CMD python -c "import urllib.request as u;u.urlopen('http://127.0.0.1:8765/healthz',timeout=3)"
CMD ["python", "app.py"]
