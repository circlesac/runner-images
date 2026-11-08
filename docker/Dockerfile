ARG RUNNER_BASE
FROM ${RUNNER_BASE}
USER root
ARG TARGETARCH
ARG NODE_VERSION
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
# runscaler starts /home/runner/run.sh. Each real runner distribution and its
# workspace are copied into a unique daemon-visible shared-volume directory.
RUN mv /home/runner /opt/actions-runner && mkdir /home/runner && chown runner:docker /home/runner
COPY --chmod=755 docker/runner.sh /home/runner/run.sh
COPY --chmod=755 docker/verify-toolchain.sh /usr/local/bin/verify-runner-toolchain
COPY runner_images /opt/runner-inventory/runner_images
COPY scripts/inventory.py /opt/runner-inventory/scripts/inventory.py
USER runner
WORKDIR /home/runner
CMD ["/home/runner/run.sh"]
