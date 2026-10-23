#!/usr/bin/env bash
# A 60 s 1080p30 H.264 clip from vtest.avi (a crowded scene, harder than a home camera).
set -euo pipefail
cd "$(dirname "$0")/.."
ffmpeg -loglevel error -y -stream_loop -1 -i data/samples/vtest.avi -t 60 \
  -vf "scale=1920:1080,fps=30" -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p -an \
  data/samples/speed_1080p.mp4
