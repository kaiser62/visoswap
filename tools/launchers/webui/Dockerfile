FROM python:3.13-slim
# Self-contained for a plain Linux Docker host: kaggle CLI, colab CLI, cloudflared (tunnel create via the launcher), and
# keyrings.alt so the colab CLI can keep its Google login in a FILE (no macOS Keychain / Secret Service in a container).
ARG TARGETARCH=amd64
COPY fetch_cloudflared.py /tmp/fetch_cloudflared.py
# six is pinned explicitly: kaggle 2.2.5 imports it at module scope but does not declare it, so a clean
# python:3.13-slim gets no transitive copy and every `kaggle` subcommand dies with ModuleNotFoundError.
RUN pip install --no-cache-dir fastapi==0.115.6 uvicorn==0.34.0 kaggle six==1.17.0 google-colab-cli keyrings.alt \
 && python /tmp/fetch_cloudflared.py ${TARGETARCH} && chmod 755 /usr/local/bin/cloudflared && /usr/local/bin/cloudflared --version \
 && kaggle --version && colab version \
 && useradd -m -u 1000 app && install -d -o 1000 -g 1000 -m 700 /data/visoswap /data/visoswap/home /data/visoswap/xdg /data/visoswap/home/.kaggle
# Everything mutable lives under /data/visoswap (the persisted volume): env files, webui_token, Kaggle config, a writable HOME
# for the Colab token.json import. The image root stays read-only.
# KAGGLE_CONFIG_DIR must sit at $HOME/.kaggle: kagglesdk hardcodes ~/.kaggle/credentials.json and ~/.kaggle/access_token
# (kaggle_creds.py:15, kaggle_env.py:106) and ignores KAGGLE_CONFIG_DIR for those two files, so a config dir anywhere else
# stores a login the CLI can never read back. Keeping both equal makes every kaggle subcommand see what the launcher saved.
ENV PYTHONUNBUFFERED=1 WEBUI_IN_DOCKER=1 \
    WEBUI_HOST=0.0.0.0 WEBUI_PORT=8765 \
    LAUNCHER_DIR=/repo/tools/launchers \
    VISOSWAP_HOME=/data/visoswap \
    KAGGLE_CONFIG_DIR=/data/visoswap/home/.kaggle \
    HOME=/data/visoswap/home \
    PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring \
    XDG_DATA_HOME=/data/visoswap/xdg \
    CLOUDFLARED_BIN=/usr/local/bin/cloudflared \
    KAGGLE_BIN=/usr/local/bin/kaggle \
    COLAB_BIN=/usr/local/bin/colab \
    LAUNCHER_BRIDGE_URL=
USER app
WORKDIR /repo/tools/launchers/webui
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request as u;u.urlopen('http://127.0.0.1:8765/healthz',timeout=3)"
CMD ["python", "app.py"]
