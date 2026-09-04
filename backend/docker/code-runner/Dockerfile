ARG PYTHON_BASE=python:3.12-slim
FROM ${PYTHON_BASE}

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin runner
COPY --chown=10001:10001 code_runner.py /runner/code_runner.py

USER 10001
WORKDIR /home/runner

ENTRYPOINT ["python", "/runner/code_runner.py"]
