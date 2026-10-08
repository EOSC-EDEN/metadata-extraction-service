FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=web.settings.prod

# Extraction tools are optional build args: enable with --build-arg INSTALL_<TOOL>=true,
# disable with =false. ExifTool and MediaInfo are on by default; the rest are opt-in.
# (Apache Tika and File Scraper adapters don't exist yet; they are added in a later step.)
ARG INSTALL_EXIFTOOL=true
ARG INSTALL_MEDIAINFO=true
ARG INSTALL_FFPROBE=false
ARG INSTALL_IMAGEMAGICK=false

RUN set -eu; \
    packages=""; \
    [ "$INSTALL_EXIFTOOL" = "true" ] && packages="$packages libimage-exiftool-perl"; \
    [ "$INSTALL_MEDIAINFO" = "true" ] && packages="$packages mediainfo"; \
    [ "$INSTALL_FFPROBE" = "true" ] && packages="$packages ffmpeg"; \
    [ "$INSTALL_IMAGEMAGICK" = "true" ] && packages="$packages imagemagick"; \
    if [ -n "$packages" ]; then \
        apt-get update; \
        apt-get install -y --no-install-recommends $packages; \
        rm -rf /var/lib/apt/lists/*; \
    fi

RUN useradd --system --create-home --uid 10001 mes

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=mes:mes . .
USER mes

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).status == 200 else 1)"

CMD ["gunicorn", "web.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
