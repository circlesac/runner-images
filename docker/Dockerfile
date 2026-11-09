ARG RUNNER_BASE=ghcr.io/actions/actions-runner:latest
FROM golang:1.27 AS patched-tools
ARG SECURITY_INPUTS
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
COPY docker/security_inputs.py docker/build-security-tools.py /build/
RUN python3 /build/build-security-tools.py "$SECURITY_INPUTS" /out
FROM ${RUNNER_BASE}
USER root
ARG TARGETARCH
ARG SECURITY_INPUTS
ARG NODE_VERSION
ARG NPM_VERSION
ARG NODE_SHA256
ARG ANDROID_URL
ARG ANDROID_SHA1
ENV ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-arm64 \
    PATH=/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/platform-tools:/usr/local/bin:${PATH}
RUN test "$TARGETARCH" = arm64 \
    && sed -i '/^Types: deb$/a Architectures: arm64' /etc/apt/sources.list.d/ubuntu.sources \
    && printf '%s\n' 'Types: deb' 'URIs: http://archive.ubuntu.com/ubuntu/' \
       'Suites: noble noble-updates noble-backports' 'Components: main universe restricted multiverse' \
       'Architectures: amd64' 'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' '' \
       'Types: deb' 'URIs: http://security.ubuntu.com/ubuntu/' 'Suites: noble-security' \
       'Components: main universe restricted multiverse' 'Architectures: amd64' \
       'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' > /etc/apt/sources.list.d/amd64.sources \
    && dpkg --add-architecture amd64 && apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends libc6:amd64 libstdc++6:amd64 zlib1g:amd64 \
       openjdk-17-jdk-headless python3 rsync shellcheck make g++ unzip zip xz-utils \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL --retry 5 "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-arm64.tar.xz" -o /tmp/node.tar.xz \
    && printf '%s  /tmp/node.tar.xz\n' "$NODE_SHA256" | sha256sum -c - \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 && rm /tmp/node.tar.xz \
    && curl -fsSL --retry 5 "$ANDROID_URL" -o /tmp/android.zip \
    && printf '%s  /tmp/android.zip\n' "$ANDROID_SHA1" | sha1sum -c - \
    && mkdir -p "$ANDROID_HOME/cmdline-tools" \
    && unzip -q /tmp/android.zip -d /tmp/android \
    && mv /tmp/android/cmdline-tools "$ANDROID_HOME/cmdline-tools/latest" \
    && rm -rf /tmp/android /tmp/android.zip \
    && { yes || true; } | sdkmanager --licenses >/dev/null
RUN sdkmanager 'platform-tools' 'platforms;android-36' 'build-tools;36.0.0' 'ndk;27.1.12297006' 'cmake;3.22.1' \
    && chown -R runner:docker "$ANDROID_HOME"
# Apple Container runs a private Docker daemon inside each Linux VM for
# container jobs; the Docker execution mode continues using its host socket.
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
    && printf '%s\n' 'deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable' > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*
# Update the toolchain's own npm independently of bundled upstream runtimes.
RUN npm install --global "npm@${NPM_VERSION}" && npm cache clean --force
# runscaler starts /home/runner/run.sh. Each real runner distribution and its
# workspace are copied into a unique daemon-visible shared-volume directory.
RUN mv /home/runner /opt/actions-runner && mkdir /home/runner && chown runner:docker /home/runner
COPY --from=patched-tools /out/ /opt/runner-security-tools/
COPY docker/security_inputs.py docker/harden.py /opt/runner-hardening/
RUN --mount=type=bind,source=docker/security-assets,target=/opt/runner-security-assets \
    python3 /opt/runner-hardening/harden.py "$SECURITY_INPUTS"
COPY --chmod=755 docker/runner.sh /home/runner/run.sh
COPY --chmod=755 docker/apple-runner.sh /usr/local/bin/apple-runner
COPY --chmod=755 docker/verify-toolchain.sh /usr/local/bin/verify-runner-toolchain
COPY runner_images /opt/runner-inventory/runner_images
COPY scripts/inventory.py /opt/runner-inventory/scripts/inventory.py
USER runner
WORKDIR /home/runner
CMD ["/home/runner/run.sh"]
