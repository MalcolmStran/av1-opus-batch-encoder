AV1 Encoder (NVENC) — Batch convert folders to AV1 + Opus

Requirements
- NVIDIA GPU with AV1 NVENC support (e.g. RTX 40‑series, some later Ada / Hopper based cards). Older GPUs without av1_nvenc will not work.
- Recent NVIDIA driver (ensure the driver exposes AV1 encode in supported apps).

For Python users (development/source)
- FFmpeg build including av1_nvenc and libopus (check with `ffmpeg -hide_banner -encoders | find "av1_nvenc"`).
- ffprobe (ships with FFmpeg) accessible in PATH.
- Python 3.9+ (tested on 3.11+).
- Sufficient free disk space on the same drive as the media (temporary output written there unless `--temp-dir` same drive).

Quick start
Download the standalone executable from GitHub Releases, then:
```cmd
av1-encoder.exe -i "D:\Media"
```

Or for Python users:
```powershell
python -m av1_encoder -i "D:\Media"
```

That command:
- Recursively scans D:\Media
- Re-encodes video to AV1 (NVENC) using preset p7 and ~3000k target bitrate VBR
- Re-encodes every audio track to Opus 128k
- Copies subs, attachments, metadata, chapters
- Replaces originals atomically (backup .bak during swap)
- Skips files already AV1 + all Opus

Flags (same for exe and Python)
- -i / --input <PATH>  (required) Root folder or single file.
- --bitrate 3000k      Target average video bitrate (default). Used unless --cq is set.
- --cq <N>             Constant quality mode (overrides --bitrate). Lower = higher quality.
- --audio-bitrate 128k Opus bitrate applied per audio stream.
- --ext mkv mp4 ...    Only process these extensions.
- --dry-run            Show planned ffmpeg commands; no changes.
- --no-replace         Write new .mkv next to source instead of replacing.
- --extra-video-args … Pass additional video encoder args (e.g. -preset p5 -tier high).
- --force              Re-encode even if already AV1 + Opus.
- --temp-dir <PATH>    Directory for temp files (must be same drive) else falls back to source.

Examples
Default encode (3 Mbps):
```cmd
av1-encoder.exe -i "D:\Shows"
```
Higher bitrate:
```cmd
av1-encoder.exe -i "D:\Shows" --bitrate 5000k
```
Constant quality:
```cmd
av1-encoder.exe -i "D:\Shows" --cq 28
```
Change preset (override p7):
```cmd
av1-encoder.exe -i "D:\Shows" --extra-video-args -preset p5
```
Dry run:
```cmd
av1-encoder.exe -i "D:\Shows" --dry-run
```
Keep originals:
```cmd
av1-encoder.exe -i "D:\Shows" --no-replace
```

Behavior details
- Video mode: bitrate VBR (`-rc vbr -b:v/-maxrate <bitrate> -b_ref_mode middle -bf 3 -preset p7`).
- CQ override: adds `-cq N` instead of bitrate options.
- Spatial/Temporal AQ: auto-added only if supported by your ffmpeg build.
- HDR heuristic: BT.2020 + PQ/HLG => 10‑bit p010le & color flags preserved.
- Audio: Opus 128k VBR, channel layout fixes (5.1(side) remap, 5.1/7.1/quad support, ambiguous layouts downmix to stereo with warning).
- Container: keeps mkv/webm, otherwise outputs mkv.
- Atomic replace: temp in same folder (or same-drive temp-dir) + .bak rollback on failure.

Python development
Clone and install in editable mode:
```powershell
git clone https://github.com/MalcolmStran/av1-opus-batch-encoder.git
cd av1-opus-batch-encoder
pip install -e .
```
Then run with: `python -m av1_encoder -i "path"`

Troubleshooting
- av1_nvenc missing: ensure FFmpeg with NVENC + recent NVIDIA driver.
- For exe users: download latest release; all dependencies included.
- Temp move cross-drive: specify --temp-dir on same drive or rely on default (source folder).
- Silent/mute multichannel: layout now auto-fixed or downmixed; report remaining cases.

Credits
- FFmpeg project (core transcoding tools) — https://ffmpeg.org/
- NVIDIA NVENC hardware encoder.
- Opus codec (Xiph.Org / RFC 6716) via libopus.
- Channel layout heuristics & remap logic inspired by common community guidance for Opus mappings.

License
Copyright (c) 2025

Released under the MIT License. See `LICENSE` file for full text.
