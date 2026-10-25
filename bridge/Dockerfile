# The camera bridge in a container: docker build -t camera-bridge . (works on x86 PCs and 64 bit Raspberry Pi OS)
FROM python:3.12-slim
RUN pip install --no-cache-dir opencv-python-headless==5.0.0.93 websockets==17.3 \
    && useradd --system --uid 10001 --no-create-home bridge
COPY pehra_bridge.py /app/pehra_bridge.py
USER bridge
ENTRYPOINT ["python", "/app/pehra_bridge.py"]
