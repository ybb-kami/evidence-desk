FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ARG WITH_FORMULA=0
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    if [ -f /run/secrets/proxy_ca ]; then set -- -o Acquire::https::CaInfo=/run/secrets/proxy_ca; fi \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get "$@" update \
    && apt-get "$@" install -y --no-install-recommends libgomp1 tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
    && if [ "$WITH_FORMULA" = "1" ]; then apt-get "$@" install -y --no-install-recommends libgl1 libglib2.0-0; fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/evidence-desk
COPY scanpdf/requirements.txt scanpdf/requirements-rag.txt ./scanpdf/
COPY scanpdf/semantic/requirements.txt ./scanpdf/semantic/
COPY scanpdf/bilingual/requirements.txt ./scanpdf/bilingual/
COPY scanpdf/qa_reader/requirements.txt ./scanpdf/qa_reader/
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi \
    && python -m pip install --no-cache-dir -r scanpdf/requirements-rag.txt \
    && python -m pip check

COPY scanpdf/container/formula-requirements.txt /opt/requirements/formula.txt
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi \
    && if [ "$WITH_FORMULA" = "1" ]; then \
         python -m venv /opt/evidence-formula \
         && /opt/evidence-formula/bin/python -m pip install --no-cache-dir -r /opt/requirements/formula.txt \
         && /opt/evidence-formula/bin/python -m pip check; \
       fi

COPY app.py arxiv_tracking.py byline.py discover.py engine.py graph.py local_review.py ocr_local.py provider.py reader.py revisions.py ./
COPY csv-stats.js index.html reader.css reader.js review-panel.js sample.pdf tracking.js writing.js ./
COPY scanpdf/ ./scanpdf/

RUN groupadd -g 10001 evidence \
    && useradd -u 10001 -g evidence -M -d /nonexistent -s /usr/sbin/nologin evidence \
    && install -d -o evidence -g evidence /data \
    && mkdir /models

USER evidence
VOLUME ["/data"]
STOPSIGNAL SIGINT
ENTRYPOINT ["python", "scanpdf/workbench.py", "--data", "/data"]
CMD ["--port", "8765"]
