# Personal HCI image. The NASA flight software is the base and is not rebuilt.
FROM ghcr.io/nasa/astrobee:latest-ubuntu20.04

ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=noetic

# The ROS apt key baked into the NASA image (F42ED6FBAB17C654) is expired.
# Refresh it before apt-get, or the update fails closed.
RUN python3 - <<'PY'
import urllib.request
urllib.request.urlretrieve(
    "https://raw.githubusercontent.com/ros/rosdistro/master/ros.key",
    "/usr/share/keyrings/ros1-latest-archive-keyring.gpg",
)
PY

RUN set -eux; \
    if ! apt-get update; then \
      sed -i \
        -e 's|http://archive.ubuntu.com/ubuntu|http://old-releases.ubuntu.com/ubuntu|g' \
        -e 's|http://security.ubuntu.com/ubuntu|http://old-releases.ubuntu.com/ubuntu|g' \
        /etc/apt/sources.list; \
      apt-get update; \
    fi; \
    apt-get install -y --no-install-recommends \
      xvfb \
      x11vnc \
      novnc \
      websockify \
      openbox \
      xterm \
      xfonts-base \
      x11-utils \
      ca-certificates \
      curl \
      ros-noetic-rosbridge-suite \
      ros-noetic-web-video-server \
      fonts-dejavu-core \
    ; \
    rm -rf /var/lib/apt/lists/*

# fonts-dejavu-core is in the install above so xterm -fa Monospace has a
# fontconfig family. xfonts-base alone is bitmap-only.
COPY crew_assist /opt/hci/crew_assist
COPY docker /opt/hci/docker

RUN chmod a+x /opt/hci/docker/scripts/*.sh /opt/hci/crew_assist/scripts/*.sh \
    && ln -sf /opt/hci/docker/scripts/hci-gui.sh /usr/local/bin/hci-gui

ENTRYPOINT ["/opt/hci/docker/scripts/entrypoint.sh"]
CMD ["sim"]
