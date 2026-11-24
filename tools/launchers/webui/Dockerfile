FROM python:3.11-slim
# The launchers shell out to the kaggle and colab CLIs. git is not needed.
RUN pip install --no-cache-dir fastapi==0.115.6 uvicorn==0.34.0 kaggle google-colab-cli \
 && useradd -m -u 1000 app
# Everything mutable lives under /data/visoswap (the persisted volume): env files, webui_token, Kaggle config, a writable HOME
# for the Colab token.json import. The image root stays read-only.
ENV PYTHONUNBUFFERED=1 WEBUI_IN_DOCKER=1 \
    WEBUI_HOST=0.0.0.0 WEBUI_PORT=8765 \
    LAUNCHER_DIR=/repo/tools/launchers \
    VISOSWAP_HOME=/data/visoswap \
    KAGGLE_CONFIG_DIR=/data/visoswap/kaggle \
    HOME=/data/visoswap/home \
    KAGGLE_BIN=/usr/local/bin/kaggle \
    COLAB_BIN=/usr/local/bin/colab \
    LAUNCHER_BRIDGE_URL=
USER app
WORKDIR /repo/tools/launchers/webui
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request as u;u.urlopen('http://127.0.0.1:8765/healthz',timeout=3)"
CMD ["python", "app.py"]
