import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".ts", ".m2ts", ".webm"}


@dataclass
class MediaInfo:
    has_video: bool
    all_video_av1: bool
    audio_codecs: List[str]
    audio_streams: List[dict]
    subtitle_streams: int
    attachments: int
    colorspace: Optional[str]
    color_primaries: Optional[str]
    color_trc: Optional[str]
    pix_fmt: Optional[str]


FFPROBE_STREAMS = [
    "index,codec_type,codec_name,channels,channel_layout,bit_rate,disposition,"
    "tags,profile,color_space,color_transfer,color_primaries,pix_fmt"
]
FFPROBE_FORMAT = ["format_name,format_long_name,tags"]


def resolve_ffmpeg_dir(explicit: Optional[str] = None) -> Optional[Path]:
    """Try to locate a bundled ffmpeg directory if provided.

    Search order:
      1. explicit path (argument)
      2. environment AV1_ENCODER_FFMPEG_DIR
      3. ./ffmpeg relative to script or frozen executable
    Returns directory Path or None.
    """
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env_dir = os.environ.get("AV1_ENCODER_FFMPEG_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    # Frozen bundle base dir
    base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.parent))
    candidates.append(base_dir / "ffmpeg")
    for c in candidates:
        if c and c.exists() and (c / "ffmpeg.exe").exists() and (c / "ffprobe.exe").exists():
            return c
    return None


def ffmpeg_paths(ffmpeg_dir: Optional[Path]):
    """Return (ffmpeg_exe, ffprobe_exe) names or absolute paths."""
    if ffmpeg_dir:
        return str(ffmpeg_dir / "ffmpeg.exe"), str(ffmpeg_dir / "ffprobe.exe")
    return "ffmpeg", "ffprobe"


def has_ffprobe(ffprobe_bin: str) -> bool:
    try:
        proc = subprocess.run([ffprobe_bin, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.returncode == 0
    except Exception:
        return False


def has_av1_nvenc(ffmpeg_bin: str) -> bool:
    try:
        proc = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-h", "encoder=av1_nvenc"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return proc.returncode == 0
    except Exception:
        return False


# Cache of encoder options reported by ffmpeg -h encoder=av1_nvenc
_AV1_NVENC_OPTIONS: Optional[set] = None


def get_av1_nvenc_options(ffmpeg_bin: str) -> set:
    """Return a set of av1_nvenc option names supported by this ffmpeg build."""
    global _AV1_NVENC_OPTIONS
    if _AV1_NVENC_OPTIONS is not None:
        return _AV1_NVENC_OPTIONS
    opts: set = set()
    try:
        res = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-h", "encoder=av1_nvenc"],
            capture_output=True,
            text=True,
        )
        out = (res.stdout or "") + "\n" + (res.stderr or "")
        for line in out.splitlines():
            s = line.strip()
            # Lines typically look like: "-spatial_aq        <boolean>..."
            if s.startswith("-") and not s.startswith("--"):
                name = s.split()[0].lstrip("-")
                # Normalize common variants
                name = name.replace("-", "_")
                if name:
                    opts.add(name)
    except Exception:
        # If anything goes wrong, just keep empty set; we'll skip conditional options
        pass
    _AV1_NVENC_OPTIONS = opts
    return opts


def run_ffprobe(path: Path, ffprobe_bin: str) -> Optional[dict]:
    try:
        # Use a single -show_entries specifying both sections
        entries = f"stream={FFPROBE_STREAMS[0].split(':')[-1]}:format={FFPROBE_FORMAT[0]}"
        cmd = [
            ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_entries",
            entries,
            "-show_streams",
            "-show_format",
            str(path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            stderr = (res.stderr or "").strip().splitlines()[-1:]  # last line for brevity
            raise RuntimeError("ffprobe failed: " + "; ".join(stderr))
        return json.loads(res.stdout or "{}")
    except Exception as e:
        print(f"[ffprobe] error on {path}: {e}")
        return None


def probe_media(path: Path, ffprobe_bin: str) -> Optional[MediaInfo]:
    data = run_ffprobe(path, ffprobe_bin)
    if not data:
        return None

    has_video = False
    all_video_av1 = True
    audio_codecs: List[str] = []
    audio_streams: List[dict] = []
    subtitle_streams = 0
    attachments = 0
    colorspace = None
    color_primaries = None
    color_trc = None
    pix_fmt = None

    for st in data.get("streams", []):
        ctype = st.get("codec_type")
        if ctype == "video":
            has_video = True
            vcodec = st.get("codec_name")
            if vcodec != "av1":
                all_video_av1 = False
            colorspace = colorspace or st.get("color_space")
            color_primaries = color_primaries or st.get("color_primaries")
            color_trc = color_trc or st.get("color_transfer")
            pix_fmt = pix_fmt or st.get("pix_fmt")
        elif ctype == "audio":
            acodec = st.get("codec_name") or ""
            audio_codecs.append(acodec)
            audio_streams.append(
                {
                    "index": st.get("index"),
                    "channels": st.get("channels"),
                    "channel_layout": st.get("channel_layout") or st.get("channel_layouts"),
                }
            )
        elif ctype == "subtitle":
            subtitle_streams += 1
        elif ctype == "attachment":
            attachments += 1

    return MediaInfo(
        has_video=has_video,
        all_video_av1=all_video_av1 if has_video else False,
        audio_codecs=audio_codecs,
    audio_streams=audio_streams,
        subtitle_streams=subtitle_streams,
        attachments=attachments,
        colorspace=colorspace,
        color_primaries=color_primaries,
        color_trc=color_trc,
        pix_fmt=pix_fmt,
    )


def is_media_file(path: Path, allowed_exts: Optional[set] = None) -> bool:
    exts = allowed_exts or VIDEO_EXTS
    return path.is_file() and path.suffix.lower() in exts


def build_ffmpeg_cmd(
    src: Path,
    dst: Path,
    info: MediaInfo,
    cq: Optional[int],
    target_bitrate: Optional[str],
    audio_bitrate: str,
    extra_video_args: List[str],
    ffmpeg_bin: str,
) -> List[str]:
    # Base command
    cmd = [
    ffmpeg_bin,
        "-hide_banner",
        "-y",
        "-nostdin",
        "-i",
        str(src),
    ]

    # Global metadata copy + chapters
    cmd += [
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
    ]

    # Map all streams from input
    cmd += ["-map", "0:v?", "-map", "0:a?", "-map", "0:s?", "-map", "0:t?"]

    # Video: AV1 NVENC settings
    v_pix = info.pix_fmt or "yuv420p"
    hdr = False
    if info.color_primaries or info.color_trc or info.colorspace:
        # Simple heuristic: if BT.2020 + PQ/HLG assume HDR10/HLG
        if (info.color_primaries or "").startswith("bt2020") or (
            info.colorspace or ""
        ).startswith("bt2020"):
            hdr = True
    if hdr:
        # Use 10-bit if source seems HDR
        v_pix = "p010le"

    v_args = ["-c:v", "av1_nvenc"]
    # Default to slowest/highest-quality preset (p7) unless user overrides
    if not any(a == "-preset" for a in extra_video_args):
        v_args += ["-preset", "p7"]
    # Prefer bitrate-based VBR if target_bitrate provided, else CQ-based VBR
    if target_bitrate:
        v_args += [
            "-rc",
            "vbr",
            "-b:v",
            target_bitrate,
            "-maxrate",
            target_bitrate,
            "-b_ref_mode",
            "middle",
        ]
    else:
        # Fallback to quality mode (previous default)
        # cq should always be set in this branch
        eff_cq = cq if cq is not None else 30
        v_args += [
            "-cq",
            str(eff_cq),
            "-rc",
            "vbr",
            "-b_ref_mode",
            "middle",
        ]

    # Conditionally enable Spatial/Temporal AQ if supported by this ffmpeg build
    nvenc_opts = get_av1_nvenc_options(ffmpeg_bin)
    if "spatial_aq" in nvenc_opts:
        v_args += ["-spatial_aq", "1"]
    if "temporal_aq" in nvenc_opts:
        v_args += ["-temporal_aq", "1"]

    v_args += [
        "-bf",
        "3",
        "-pix_fmt",
        v_pix,
    ]

    # Color metadata passthrough if present
    if info.color_primaries:
        v_args += ["-color_primaries", info.color_primaries]
    if info.color_trc:
        v_args += ["-color_trc", info.color_trc]
    if info.colorspace:
        v_args += ["-colorspace", info.colorspace]

    v_args += extra_video_args

    cmd += v_args

    # Audio: convert each stream to Opus (copy if already opus)
    # ffmpeg can't do conditional per-stream easily without filters; set codec by stream type
    # We use -c:a libopus and -b:a; ffmpeg will re-encode non-Opus and copy Opus by -c:a copy? No.
    # So we re-encode all audio to Opus to keep it simple and consistent.
    cmd += [
        "-c:a",
        "libopus",
        "-b:a",
        audio_bitrate,
        "-vbr",
        "on",
        "-application",
        "audio",
    ]

    # Subtitles and attachments: copy
    cmd += ["-c:s", "copy", "-c:t", "copy"]

    # Per-audio stream fixes for Opus
    # - Set mapping_family 0 for mono/stereo; 1 for multichannel
    # - Remap 5.1(side) -> 5.1(back) to satisfy Opus mapping using channelmap
    # - For any multichannel, ensure layout is Opus-compatible; fallback to stereo if not
    for a_idx, a in enumerate(info.audio_streams or []):
        ch = int(a.get("channels") or 0)
        layout = (a.get("channel_layout") or "").lower()
        codec = (info.audio_codecs[a_idx] if a_idx < len(info.audio_codecs) else "").lower()
        # Default: mapping_family 0 for mono/stereo, 1 for multichannel
        if ch <= 2:
            cmd += [f"-mapping_family:a:{a_idx}", "0"]
        else:
            # Handle common surround layouts
            # 5.1(side): remap to 5.1(back)
            if ch == 6 and layout == "5.1(side)":
                cmd += [f"-mapping_family:a:{a_idx}", "1"]
                cm = "channelmap=map=FL-FL|FR-FR|FC-FC|LFE-LFE|SL-BL|SR-BR"
                cmd += [f"-filter:a:{a_idx}", cm]
            # 5.1(back) or 5.1: direct mapping
            elif ch == 6 and layout in {"5.1", "5.1(back)"}:
                cmd += [f"-mapping_family:a:{a_idx}", "1"]
            # 7.1: Opus supports 7.1 (8ch) with mapping_family 1
            elif ch == 8 and layout in {"7.1", "7.1(wide)", "7.1(wide-side)", "7.1(rear)"}:
                cmd += [f"-mapping_family:a:{a_idx}", "1"]
            # Quad/4.0/4ch: Opus supports quad layout
            elif ch == 4 and layout in {"quad", "4.0"}:
                cmd += [f"-mapping_family:a:{a_idx}", "1"]
            # 2.1, 3.1, 4.1, 6.1, 7.1(ch) etc: try mapping_family 1, but warn if not standard
            elif ch in {3, 4, 5, 7}:
                cmd += [f"-mapping_family:a:{a_idx}", "1"]
                print(f"[WARN] Audio stream {a_idx}: {ch}ch layout '{layout}' may not be Opus standard; check output.")
            # Unknown/ambiguous layout: downmix to stereo to avoid mute
            else:
                print(f"[WARN] Audio stream {a_idx}: {ch}ch layout '{layout}' not recognized, downmixing to stereo.")
                cmd += [f"-ac:a:{a_idx}", "2"]
                cmd += [f"-mapping_family:a:{a_idx}", "0"]

    cmd.append(str(dst))
    return cmd


def replace_file(src: Path, tmp_out: Path, dest: Path):
    """Atomically replace src with tmp_out, potentially changing extension to dest.

    If dest == src, this is a classic atomic replace with a .bak backup.
    If dest != src, the original is backed up, tmp_out is moved to dest, then the
    backup of src is deleted. On failure, attempts to restore the original.
    """
    backup = src.with_suffix(src.suffix + ".bak")
    if backup.exists():
        backup.unlink()
    # Backup original
    if src.exists():
        src.replace(backup)
    try:
        if dest.exists():
            dest.unlink()
        tmp_out.replace(dest)
        # Success: remove backup of original
        if backup.exists():
            backup.unlink()
    except Exception:
        # Try to restore original if needed
        try:
            if (not src.exists()) and backup.exists():
                backup.replace(src)
        finally:
            raise


def already_compliant(info: Optional[MediaInfo]) -> bool:
    if not info or not info.has_video:
        return False
    if not info.all_video_av1:
        return False
    # If all audio already opus, skip
    if all(ac == "opus" for ac in info.audio_codecs if ac):
        return True
    return False


def process_file(
    path: Path,
    cq: Optional[int],
    target_bitrate: Optional[str],
    audio_bitrate: str,
    dry_run: bool,
    no_replace: bool,
    extra_video_args: List[str],
    force: bool,
    temp_dir: Optional[Path],
    ffmpeg_bin: str,
    ffprobe_bin: str,
) -> Tuple[bool, str]:
    info = probe_media(path, ffprobe_bin)
    if info is None:
        return False, f"Probe failed: {path}"

    if already_compliant(info) and not force:
        return True, f"Skip (already AV1 + Opus): {path}"

    # Choose output container: prefer MKV when converting to Opus to ensure compatibility
    def choose_ext(p: Path) -> str:
        src_ext = p.suffix.lower()
        # Prefer to keep mkv/webm; otherwise switch to mkv for Opus support
        return src_ext if src_ext in {".mkv", ".webm"} else ".mkv"

    chosen_ext = choose_ext(path)
    # Unique temp file to avoid collisions and ensure per-file isolation
    tmp_name = f"{path.stem}.av1.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp{chosen_ext}"
    # Place temp file in same drive/folder as destination for atomic rename, unless a temp_dir on the
    # same drive is provided.
    dest_dir = path.parent
    chosen_tmp_dir = dest_dir
    if temp_dir:
        try:
            # On Windows, compare drive letters; otherwise, use anchor/root comparison
            same_drive = (getattr(temp_dir, "drive", "") or getattr(temp_dir, "anchor", "")) == (
                getattr(dest_dir, "drive", "") or getattr(dest_dir, "anchor", "")
            )
            if same_drive:
                chosen_tmp_dir = temp_dir
        except Exception:
            # Fallback to dest_dir
            chosen_tmp_dir = dest_dir
    chosen_tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_out = chosen_tmp_dir / tmp_name

    cmd = build_ffmpeg_cmd(path, tmp_out, info, cq, target_bitrate, audio_bitrate, extra_video_args, ffmpeg_bin)

    if dry_run:
        return True, "DRY-RUN ffmpeg " + " ".join(cmd[1:])

    # Run ffmpeg
    try:
        print("Running:", " ".join(cmd))
        proc = subprocess.run(cmd, text=True)
        if proc.returncode != 0:
            # Cleanup partial temp file
            try:
                if tmp_out.exists():
                    tmp_out.unlink()
            finally:
                return False, f"ffmpeg failed: {path}"
    except FileNotFoundError:
        # Cleanup partial temp file
        if tmp_out.exists():
            tmp_out.unlink()
        return False, "ffmpeg not found; ensure ffmpeg.exe is in PATH"
    except Exception as e:
        if tmp_out.exists():
            tmp_out.unlink()
        return False, f"Error: {e}"

    if no_replace:
        # Move tmp next to original with chosen container extension
        final_out = path.with_suffix(chosen_ext)
        try:
            if final_out.exists():
                final_out.unlink()
            shutil.move(str(tmp_out), str(final_out))
        except Exception as e:
            # Ensure temp is removed on failure
            if tmp_out.exists():
                try:
                    tmp_out.unlink()
                except Exception:
                    pass
            return False, f"Move failed: {e}"
        return True, f"Wrote: {final_out}"

    # Replace in place atomically; if extension changes, the destination will be different
    dest_path = path if chosen_ext == path.suffix.lower() else path.with_suffix(chosen_ext)
    try:
        replace_file(path, tmp_out, dest_path)
    except Exception as e:
        # Ensure temp is removed on failure
        if tmp_out.exists():
            try:
                tmp_out.unlink()
            except Exception:
                pass
        return False, f"Replace failed: {e}"
    # If extension changed, optionally remove stray original filename (already removed via backup)
    return True, f"Replaced: {dest_path}"


def walk_and_process(
    root: Path,
    exts: Optional[List[str]],
    cq: Optional[int],
    target_bitrate: Optional[str],
    audio_bitrate: str,
    dry_run: bool,
    no_replace: bool,
    extra_video_args: List[str],
    force: bool,
    temp_dir: Optional[Path],
    ffmpeg_bin: str,
    ffprobe_bin: str,
) -> List[Tuple[Path, bool, str]]:
    allowed = {e.lower() if e.startswith(".") else "." + e.lower() for e in exts} if exts else None
    results = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            if is_media_file(p, allowed):
                ok, msg = process_file(
                    p,
                    cq,
                    target_bitrate,
                    audio_bitrate,
                    dry_run,
                    no_replace,
                    extra_video_args,
                    force,
                    temp_dir,
                    ffmpeg_bin,
                    ffprobe_bin,
                )
                results.append((p, ok, msg))
    return results


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Batch convert media to AV1 (NVENC) + Opus audio")
    p.add_argument("--input", "-i", required=True, help="Root folder to scan recursively")
    p.add_argument("--ext", nargs="*", default=None, help="Whitelist of file extensions (e.g. mkv mp4)")
    p.add_argument("--bitrate", default="3000k", help="Target average video bitrate (e.g. 3000k). Default 3000k")
    p.add_argument(
        "--cq",
        type=int,
        default=None,
        help="Optional constant quality mode (overrides --bitrate when set). Lower = higher quality.",
    )
    p.add_argument("--audio-bitrate", default="128k", help="Audio bitrate for Opus per stream")
    p.add_argument("--dry-run", action="store_true", help="Only print planned actions")
    p.add_argument("--no-replace", action="store_true", help="Do not replace originals; write alongside as .mkv")
    p.add_argument("--extra-video-args", nargs=argparse.REMAINDER, help="Extra args passed to ffmpeg after video opts")
    p.add_argument("--force", action="store_true", help="Re-encode even if already AV1 + Opus")
    p.add_argument("--temp-dir", default=None, help="Directory for temp files (must be on same drive as input to allow atomic replace; falls back to input folder if different drive)")
    p.add_argument("--ffmpeg-dir", default=None, help="Directory containing ffmpeg.exe & ffprobe.exe (used first if provided or bundled)")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None):
    args = parse_args(argv)
    root = Path(args.input)
    if not root.exists():
        print(f"Input not found: {root}")
        sys.exit(2)

    extra_video_args = args.extra_video_args or []

    ffmpeg_dir = resolve_ffmpeg_dir(args.ffmpeg_dir)
    ffmpeg_bin, ffprobe_bin = ffmpeg_paths(ffmpeg_dir)

    if not has_ffprobe(ffprobe_bin):
        print("ffprobe not found (looked for). Provide --ffmpeg-dir with ffmpeg.exe & ffprobe.exe or add to PATH.")
        sys.exit(3)

    if not args.dry_run and not has_av1_nvenc(ffmpeg_bin):
        print(
            "av1_nvenc encoder not available. Ensure you have an FFmpeg build with NVIDIA NVENC support and an RTX 40-series (or newer) GPU."
        )
        sys.exit(4)

    # Determine which mode: bitrate (default) or cq override
    target_bitrate = args.bitrate if args.cq is None else None
    results = walk_and_process(
        root=root,
        exts=args.ext,
        cq=args.cq,
        target_bitrate=target_bitrate,
        audio_bitrate=args.audio_bitrate,
        dry_run=args.dry_run,
        no_replace=args.no_replace,
        extra_video_args=extra_video_args,
        force=args.force,
        temp_dir=Path(args.temp_dir) if args.temp_dir else None,
        ffmpeg_bin=ffmpeg_bin,
        ffprobe_bin=ffprobe_bin,
    )

    # Summary
    ok_count = sum(1 for _p, ok, _m in results if ok)
    fail_count = sum(1 for _p, ok, _m in results if not ok)

    for p, ok, m in results:
        print(("OK", p.name, m) if ok else ("FAIL", p.name, m))

    print(f"Done: {ok_count} OK, {fail_count} failed")


if __name__ == "__main__":
    main()
