#!/usr/bin/env bash
# Download the small English Vosk model used by voice_input.py.
# The model is about 40 MB and is gitignored under hci/models/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="${ROOT}/models/vosk-model-small-en-us-0.15"
URL="https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"

if [[ -d "${DEST}" ]]; then
  echo "already present: ${DEST}"
  exit 0
fi

if ! command -v curl >/dev/null || ! command -v unzip >/dev/null; then
  echo "curl and unzip are required" >&2
  exit 1
fi

mkdir -p "${ROOT}/models"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
echo "downloading ${URL}"
curl -fL --retry 3 -o "${tmp}/model.zip" "${URL}"
unzip -q "${tmp}/model.zip" -d "${ROOT}/models"
if [[ ! -d "${DEST}" ]]; then
  echo "zip did not contain ${DEST}" >&2
  exit 1
fi
echo "installed ${DEST}"
