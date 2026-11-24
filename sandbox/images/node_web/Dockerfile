# DevTeam AI sandbox image: node_web (Part E plan §3.5; D-08).
# Built only by scripts/build_sandbox_images.py (the only step with network access). The exact dependency
# tree from package-lock.json (generated on Linux) is installed into /node_modules at the image root, so a
# project copied to /work/<dir> resolves it by Node's standard parent-folder lookup. Install scripts are
# disabled. At run time: no network, read-only root filesystem, UID 10001, no npm or npx.
FROM node:22-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee

COPY package.json package-lock.json /opt/build/
RUN cd /opt/build \
 && npm ci --ignore-scripts --no-audit --no-fund --loglevel=error \
 && mv /opt/build/node_modules /node_modules \
 && rm -rf /opt/build /root/.npm \
 && groupadd -g 10001 sandbox \
 && useradd -u 10001 -g 10001 -M -d /work/.devteam/home -s /usr/sbin/nologin sandbox \
 && install -d -o 10001 -g 10001 -m 0755 /work

ENV NODE_ENV=production \
    NPM_CONFIG_OFFLINE=true \
    NPM_CONFIG_UPDATE_NOTIFIER=false

COPY measure /opt/devteam/measure

USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/opt/devteam/measure"]
CMD ["node", "--version"]
