FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m pip install --no-cache-dir \
    beautifulsoup4==4.13.4 \
    httpx==0.28.1 \
    openpyxl==3.1.5 \
    pandas==2.3.1 \
    Pillow==11.3.0 \
    pypdf==5.9.0 \
    pytest==8.4.1 \
    python-docx==1.2.0 \
    python-pptx==1.0.2 \
    reportlab==4.4.3

COPY docker/skill-worker/runner.py /opt/eck-worker/runner.py
COPY docker/skill-worker/foundation_skill.py /opt/eck-worker/foundation_skill.py

ENTRYPOINT ["python", "/opt/eck-worker/runner.py"]
CMD ["self-check"]
