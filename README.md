AV1 Encoder (NVENC) — Batch convert folders to AV1 + Opus

Quick start (default settings)
```powershell
python -m av1_encoder -i "D:\\Media"
```

That command:
- Recursively scans D:\Media
- Re-encodes video to AV1 (NVENC) using preset p7 and ~3000k target bitrate VBR
- Re-encodes every audio track to Opus 128k
- Copies subs, attachments, metadata, chapters
- Replaces originals atomically (backup .bak during swap)
- Skips files already AV1 + all Opus

Flags
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

Behavior details
- Video mode: bitrate VBR (`-rc vbr -b:v/-maxrate <bitrate> -b_ref_mode middle -bf 3 -preset p7`).
- CQ override: adds `-cq N` instead of bitrate options.
- Spatial/Temporal AQ: auto-added only if supported by your ffmpeg build.
- HDR heuristic: BT.2020 + PQ/HLG => 10‑bit p010le & color flags preserved.
- Audio: Opus 128k VBR, channel layout fixes (5.1(side) remap, 5.1/7.1/quad support, ambiguous layouts downmix to stereo with warning).
- Container: keeps mkv/webm, otherwise outputs mkv.
- Atomic replace: temp in same folder (or same-drive temp-dir) + .bak rollback on failure.

Examples
Bitrate (default 3 Mbps):
```powershell
python -m av1_encoder -i "D:\\Shows"
```
Higher bitrate:
```powershell
python -m av1_encoder -i "D:\\Shows" --bitrate 5000k
```
Constant quality:
```powershell
python -m av1_encoder -i "D:\\Shows" --cq 28
```
Change preset (override p7):
```powershell
python -m av1_encoder -i "D:\\Shows" --extra-video-args -preset p5
```
Dry run:
```powershell
python -m av1_encoder -i "D:\\Shows" --dry-run
```
Keep originals:
```powershell
python -m av1_encoder -i "D:\\Shows" --no-replace
```

Troubleshooting
- av1_nvenc missing: install an ffmpeg build with NVENC (and AV1 support) + recent NVIDIA driver.
- ffprobe missing: ensure ffmpeg/ffprobe in PATH.
- Temp move cross-drive: specify --temp-dir on same drive or rely on default (source folder).
- Silent/mute multichannel: layout now auto-fixed or downmixed; report remaining cases.

License
MIT
