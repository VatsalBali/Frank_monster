# Sandbox image for agent-generated code. No credentials, non-root, run with --read-only + internal network.
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest==8.3.3 requests==2.32.3 httpx==0.27.2 beautifulsoup4==4.12.3 lxml==5.3.0 \
    pydantic==2.9.2 openpyxl==3.1.5 python-dateutil==2.9.0 pypdf==5.1.0
RUN useradd -m -u 1000 runner
USER runner
WORKDIR /work
