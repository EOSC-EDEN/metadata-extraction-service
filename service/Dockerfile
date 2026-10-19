FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=web.settings.prod \
    PROMETHEUS_MULTIPROC_DIR=/tmp/mes-metrics \
    MES_JOB_UPLOAD_DIR=/var/lib/mes/uploads

# Extraction tools are optional build args: enable with --build-arg INSTALL_<TOOL>=true,
# disable with =false. ExifTool and MediaInfo are on by default; the rest are opt-in.
# Apache Tika runs as a separate container (see docker-compose.yml, profile "tika").
# Enable each opt-in tool with MES_<TOOL>_ENABLED=true.
ARG INSTALL_EXIFTOOL=true
ARG INSTALL_MEDIAINFO=true
ARG INSTALL_FFPROBE=false
ARG INSTALL_IMAGEMAGICK=false
ARG INSTALL_METAFLAC=false
# File Scraper (CLI `scraper`): adds several hundred MB (JRE, JHove, LibreOffice, ...).
ARG INSTALL_FILE_SCRAPER=false
# Pinned sources for File Scraper; override with --build-arg. FILE_SCRAPER_REF and the
# DPRES_*_REF values are git refs (use tags or commit hashes for reproducible builds).
ARG FILE_SCRAPER_REF=master
ARG DPRES_REF=master
ARG JHOVE_JAR_URL=https://software.openpreservation.org/rel/jhove-latest.jar
ARG VERAPDF_VERSION=1.30.3
ARG VNU_VERSION=latest
ARG DBPTK_VERSION=4.4.1
ARG WARCHAEOLOGY_VERSION=5.0.0
ARG SCHEMATRON_REF=master

RUN set -eu; \
    packages=""; \
    [ "$INSTALL_EXIFTOOL" = "true" ] && packages="$packages libimage-exiftool-perl"; \
    [ "$INSTALL_MEDIAINFO" = "true" ] && packages="$packages mediainfo"; \
    [ "$INSTALL_FFPROBE" = "true" ] && packages="$packages ffmpeg"; \
    [ "$INSTALL_IMAGEMAGICK" = "true" ] && packages="$packages imagemagick"; \
    [ "$INSTALL_METAFLAC" = "true" ] && packages="$packages flac"; \
    [ "$INSTALL_FILE_SCRAPER" = "true" ] && packages="$packages git libmagic1 imagemagick libmagickwand-dev \
        libimage-exiftool-perl mediainfo ffmpeg pngcheck ghostscript libreoffice-core libreoffice-writer \
        libreoffice-calc libreoffice-impress libreoffice-java-common default-jre-headless curl gcc pkg-config \
        file pspp xsltproc libxml2-utils unzip ca-certificates"; \
    if [ -n "$packages" ]; then \
        apt-get update; \
        apt-get install -y --no-install-recommends $packages; \
        rm -rf /var/lib/apt/lists/*; \
    fi

# File Scraper installs into its own virtualenv so its dependencies stay out of the Django
# process. Upstream's extra Digital Preservation Finland repositories are needed (the PyPI
# package alone fails to import), as is a config file for libmagic. JHove has no apt package:
# its IzPack console installer is fed canned answers and installed system-wide (the container
# runs as the non-root `mes` user). Every other tool File Scraper looks up must be on PATH:
# veraPDF (IzPack auto-install), v.Nu and DBPTK (jars behind wrapper scripts), warchaeology
# (`warc`, a .deb), warctools (`warcvalid`) and the schematron XSLT used by xsltproc.
RUN set -eu; \
    if [ "$INSTALL_FILE_SCRAPER" = "true" ]; then \
        python -m venv /opt/file-scraper; \
        /opt/file-scraper/bin/pip install --no-cache-dir \
            "git+https://github.com/Digital-Preservation-Finland/dpres-file-formats@${DPRES_REF}" \
            "git+https://github.com/Digital-Preservation-Finland/fido@${DPRES_REF}" \
            "git+https://github.com/Digital-Preservation-Finland/dpx-validator@${DPRES_REF}" \
            "git+https://github.com/Digital-Preservation-Finland/xml-helpers@${DPRES_REF}" \
            "git+https://github.com/Digital-Preservation-Finland/ffmpeg-python@${DPRES_REF}" \
            "git+https://github.com/Digital-Preservation-Finland/file-scraper@${FILE_SCRAPER_REF}"; \
        ln -s /opt/file-scraper/bin/scraper /usr/local/bin/scraper; \
        mkdir -p /etc/file-scraper; \
        printf '[PATHS]\nmagiclib = %s\n' "$(ls /usr/lib/*/libmagic.so.1 | head -n 1)" > /etc/file-scraper/file-scraper.conf; \
        curl -fsSL -o /tmp/jhove.jar "$JHOVE_JAR_URL"; \
        printf '1\n/opt/jhove\nO\n1\nY\n1\n1\n1\n1\n1\n' | java -jar /tmp/jhove.jar -console; \
        ln -s /opt/jhove/jhove /usr/local/bin/jhove; \
        rm -f /tmp/jhove.jar; \
        /usr/local/bin/scraper --help > /dev/null; \
    fi

RUN set -eu; \
    if [ "$INSTALL_FILE_SCRAPER" = "true" ]; then \
        /opt/file-scraper/bin/pip install --no-cache-dir ffmpeg-python warctools; \
        ln -s /opt/file-scraper/bin/warcvalid /usr/local/bin/warcvalid; \
        mkdir -p /opt/vnu /opt/dbptk; \
        curl -fsSL -o /opt/vnu/vnu.jar "https://github.com/validator/validator/releases/download/${VNU_VERSION}/vnu.jar"; \
        printf '#!/bin/sh\nexec java -jar /opt/vnu/vnu.jar "$@"\n' > /usr/local/bin/vnu; \
        curl -fsSL -o /opt/dbptk/dbptk-app.jar "https://github.com/keeps/dbptk-developer/releases/download/v${DBPTK_VERSION}/dbptk-app-${DBPTK_VERSION}.jar"; \
        printf '#!/bin/sh\nexec java -jar /opt/dbptk/dbptk-app.jar "$@"\n' > /usr/local/bin/dbptk; \
        chmod 755 /usr/local/bin/vnu /usr/local/bin/dbptk; \
        curl -fsSL -o /tmp/warc.deb "https://github.com/NationalLibraryOfNorway/warchaeology/releases/download/v${WARCHAEOLOGY_VERSION}/warchaeology_${WARCHAEOLOGY_VERSION}_amd64.deb"; \
        dpkg -i /tmp/warc.deb; rm -f /tmp/warc.deb; \
        git clone https://github.com/Digital-Preservation-Finland/iso-schematron-xslt1 /opt/iso-schematron-xslt1; \
        git -C /opt/iso-schematron-xslt1 checkout "$SCHEMATRON_REF"; \
        printf 'schematron_dir = /opt/iso-schematron-xslt1\n' >> /etc/file-scraper/file-scraper.conf; \
        curl -fsSL -o /tmp/verapdf.zip "https://software.verapdf.org/rel/1.30/verapdf-greenfield-${VERAPDF_VERSION}-installer.zip"; \
        unzip -q /tmp/verapdf.zip -d /tmp/verapdf; \
        printf '%s' '<AutomatedInstallation langpack="eng"><com.izforge.izpack.panels.htmlhello.HTMLHelloPanel id="welcome"/><com.izforge.izpack.panels.target.TargetPanel id="install_dir"><installpath>/opt/verapdf</installpath></com.izforge.izpack.panels.target.TargetPanel><com.izforge.izpack.panels.packs.PacksPanel id="sdk_pack_select"><pack index="0" name="veraPDF GUI" selected="true"/><pack index="1" name="veraPDF Mac and *nix Scripts" selected="true"/><pack index="2" name="veraPDF Validation model" selected="true"/><pack index="3" name="veraPDF Documentation" selected="false"/><pack index="4" name="veraPDF Sample Plugins" selected="false"/></com.izforge.izpack.panels.packs.PacksPanel><com.izforge.izpack.panels.install.InstallPanel id="install"/><com.izforge.izpack.panels.finish.FinishPanel id="finish"/></AutomatedInstallation>' > /tmp/verapdf-auto.xml; \
        java -jar /tmp/verapdf/verapdf-greenfield-*/verapdf-izpack-installer-*.jar /tmp/verapdf-auto.xml; \
        ln -s /opt/verapdf/verapdf /usr/local/bin/verapdf; \
        rm -rf /tmp/verapdf /tmp/verapdf.zip /tmp/verapdf-auto.xml; \
    fi

RUN useradd --system --create-home --uid 10001 mes \
    && mkdir -p /tmp/mes-metrics /var/lib/mes/uploads \
    && chown mes:mes /tmp/mes-metrics /var/lib/mes/uploads

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip uninstall -y pip

COPY --chown=mes:mes . .
USER mes

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).status == 200 else 1)"

CMD ["gunicorn", "web.wsgi:application", "-c", "gunicorn.conf.py"]
