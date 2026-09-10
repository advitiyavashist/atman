#!/bin/sh
# Build a versioned wheel and (optionally) a tagged Docker image for the
# Python oracle. Does not replace tickets.py and does not start a Go rewrite.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
cd "$HERE"
VERSION=$(python3 -c "import re,pathlib; t=pathlib.Path('pyproject.toml').read_text(); print(re.search(r'^version = \"([^\"]+)\"', t, re.M).group(1))")
REVISION=$(git rev-parse HEAD)
mkdir -p dist
python3 -m pip install --quiet --upgrade pip build
python3 -m build --wheel --outdir dist
echo "wheel: dist/ticket_board-${VERSION}-py3-none-any.whl"
if command -v docker >/dev/null 2>&1; then
  IMAGE="atman-tickets:${VERSION}-${REVISION}"
  docker build \
    --build-arg ATMAN_VERSION="$VERSION" \
    --build-arg ATMAN_REVISION="$REVISION" \
    -t "$IMAGE" \
    -t "atman-tickets:${VERSION}" \
    .
  echo "image: $IMAGE"
fi
