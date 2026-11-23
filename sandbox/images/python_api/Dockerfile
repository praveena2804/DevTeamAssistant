# DevTeam AI sandbox image: python_api (Part E plan §3.5; D-08).
# Built only by scripts/build_sandbox_images.py (the only step with network access). At run time the
# container has no network, a read-only root filesystem and runs as UID 10001; nothing is installed then.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/opt/devteam/tools

COPY requirements.lock /opt/devteam/requirements.lock
# Hash-pinned, no dependency resolution at build time beyond the lock, no source builds.
RUN pip install --no-cache-dir --require-hashes --no-deps --only-binary=:all: -r /opt/devteam/requirements.lock \
 && groupadd -g 10001 sandbox \
 && useradd -u 10001 -g 10001 -M -d /work/.devteam/home -s /usr/sbin/nologin sandbox \
 && install -d -o 10001 -g 10001 -m 0755 /work

COPY tools/ /opt/devteam/tools/

COPY measure /opt/devteam/measure

USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/opt/devteam/measure"]
CMD ["python", "--version"]
