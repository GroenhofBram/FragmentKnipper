import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import streamlit as st

# Check for yt-dlp import with friendly message
try:
    from yt_dlp import YoutubeDL
except ModuleNotFoundError:
    st.error(
        "Python package 'yt-dlp' is not installed.\n\n"
        "If running locally: run `pip install yt-dlp`.\n"
        "If on Streamlit Community Cloud: add `yt-dlp` to requirements.txt and redeploy."
    )
    st.stop()

# Check ffmpeg binary is available
if shutil.which("ffmpeg") is None:
    st.error(
        "ffmpeg binary not found on PATH.\n\n"
        "On Streamlit Community Cloud: add 'ffmpeg' to packages.txt and redeploy.\n"
        "Locally: install ffmpeg (e.g., apt, brew, choco or download from ffmpeg.org)."
    )
    st.stop()

# ---------- Time parsing utilities ----------
def parse_timepart(t: str) -> float:
    t = t.strip()
    if not t:
        raise ValueError("Empty time string")
    if ':' in t:
        parts = [p for p in t.split(':') if p != '']
        parts_f = []
        for p in parts:
            if '.' in p:
                parts_f.append(float(p))
            else:
                parts_f.append(int(p))
        if len(parts_f) == 3:
            h, m, s = parts_f
        elif len(parts_f) == 2:
            h = 0
            m, s = parts_f
        elif len(parts_f) == 1:
            h = 0
            m = 0
            s = parts_f[0]
        else:
            raise ValueError("Bad time format: " + t)
        return float(h) * 3600 + float(m) * 60 + float(s)
    if '.' in t:
        parts = t.split('.')
        if len(parts) == 2:
            m = int(parts[0]) if parts[0] != '' else 0
            s = float(parts[1])
            return float(m) * 60 + float(s)
        else:
            return float(t)
    return float(t)

def parse_ranges(range_string: str):
    raw_pieces = []
    for line in range_string.replace('\n', ',').split(','):
        piece = line.strip()
        if piece:
            raw_pieces.append(piece)
    ranges = []
    for piece in raw_pieces:
        if '-' not in piece:
            raise ValueError("Each range must use '-' to separate start and end: " + piece)
        a, b = piece.split('-', 1)
        start = parse_timepart(a)
        end = parse_timepart(b)
        if end <= start:
            raise ValueError(f"End time must be greater than start time in range {piece}")
        ranges.append((start, end))
    return ranges

# ---------- yt_dlp download ----------
def download_video(url: str, outdir: str, logger=None) -> str:
    opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': os.path.join(outdir, 'video.%(ext)s'),
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4',
    }
    if logger:
        logger("Downloading video...")
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    ext = info.get('ext', 'mp4')
    fn = os.path.join(outdir, 'video.' + ext)
    if not os.path.exists(fn):
        for f in os.listdir(outdir):
            if f.startswith('video.'):
                fn = os.path.join(outdir, f)
                break
    if not os.path.exists(fn):
        raise FileNotFoundError("Downloaded video file not found in " + outdir)
    if logger:
        logger(f"Downloaded to: {fn}")
    return fn

# ---------- ffmpeg operations ----------
def cut_segments_with_ffmpeg(input_file: str, ranges, tempdir: str, logger=None):
    seg_files = []
    for i, (start, end) in enumerate(ranges):
        seg_path = os.path.join(tempdir, f"seg_{i:03d}.mp4")
        duration = end - start
        cmd = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-i', input_file,
            '-ss', str(start),
            '-t', str(duration),
            '-c', 'copy',
            seg_path
        ]
        if logger:
            logger(f"Cutting segment {i+1}/{len(ranges)}: {start} -> {end}")
            logger(" ".join(shlex.quote(x) for x in cmd))
        subprocess.check_call(cmd)
        if not os.path.exists(seg_path):
            raise RuntimeError(f"Expected segment output missing: {seg_path}")
        seg_files.append(seg_path)
    return seg_files

def concat_segments_with_ffmpeg(seg_files, output_file, tempdir: str, logger=None):
    list_txt = os.path.join(tempdir, "concat_list.txt")
    with open(list_txt, 'w', encoding='utf-8') as f:
        for s in seg_files:
            safe_path = s.replace("'", r"'\''")
            f.write(f"file '{safe_path}'\n")
    cmd_copy = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list_txt, '-c', 'copy', output_file]
    try:
        if logger:
            logger("Concatenating (fast copy)...")
            logger(" ".join(shlex.quote(x) for x in cmd_copy))
        subprocess.check_call(cmd_copy)
    except subprocess.CalledProcessError:
        if logger:
            logger("Fast concat failed; re-encoding...")
        cmd_re = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list_txt, '-c:v', 'libx264', '-c:a', 'aac', output_file]
        if logger:
            logger(" ".join(shlex.quote(x) for x in cmd_re))
        subprocess.check_call(cmd_re)
    if not os.path.exists(output_file):
        raise RuntimeError("Concatenated output not found: " + output_file)
    if logger:
        logger("Concatenation complete: " + output_file)
    return output_file

# ---------- Streamlit UI ----------
st.set_page_config(page_title="YouTube Segment Player", layout="centered")
st.title("YouTube Segment Player")

st.markdown(
    "Paste a YouTube link and time ranges (comma or newline separated). "
    "Example range formats: `0.02-0.05`, `1:03-1:20`, `12-15`, `90-95.5`."
)

url = st.text_input("YouTube URL")
ranges_input = st.text_area("Time ranges (comma or newline separated)", value="0.02-0.05,1.03-1.20", height=80)
keep_temp = st.checkbox("Keep temporary files (for debugging)", value=False)
run = st.button("Create & Play Segments")

if run:
    if not url.strip():
        st.error("Please enter a YouTube URL.")
    elif not ranges_input.strip():
        st.error("Please enter at least one time range.")
    else:
        status_box = st.empty()
        logs = []
        def log(msg):
            logs.append(msg)
            status_box.text("\n".join(logs[-20:]))
        tmpdir = tempfile.mkdtemp(prefix="st_ytseg_")
        try:
            try:
                ranges = parse_ranges(ranges_input)
            except Exception as e:
                st.error(f"Could not parse ranges: {e}")
                raise

            try:
                in_file = download_video(url, tmpdir, logger=log)
            except Exception as e:
                st.error(f"Download failed: {e}")
                raise

            try:
                segs = cut_segments_with_ffmpeg(in_file, ranges, tmpdir, logger=log)
            except subprocess.CalledProcessError as e:
                st.error(f"ffmpeg failed while cutting segments: {e}")
                raise
            except Exception as e:
                st.error(f"Error cutting segments: {e}")
                raise

            out_file = os.path.join(tmpdir, "out_segments.mp4")
            try:
                concat_segments_with_ffmpeg(segs, out_file, tmpdir, logger=log)
            except subprocess.CalledProcessError as e:
                st.error(f"ffmpeg failed while concatenating: {e}")
                raise
            except Exception as e:
                st.error(f"Error concatenating segments: {e}")
                raise

            log("Preparing playback...")
            st.success("Ready — playing below (and you can download the result).")
            try:
                st.video(out_file)
            except Exception:
                with open(out_file, "rb") as f:
                    data = f.read()
                st.video(data)

            try:
                with open(out_file, "rb") as f:
                    vid_bytes = f.read()
                st.download_button("Download concatenated clip", data=vid_bytes, file_name="segments.mp4", mime="video/mp4")
            except Exception as e:
                st.warning(f"Could not create download button: {e}")

        finally:
            if keep_temp:
                log(f"Kept temp dir: {tmpdir}")
                st.info(f"Temporary files kept at: {tmpdir}")
            else:
                try:
                    for root, dirs, files in os.walk(tmpdir, topdown=False):
                        for name in files:
                            try:
                                os.remove(os.path.join(root, name))
                            except Exception:
                                pass
                        for name in dirs:
                            try:
                                os.rmdir(os.path.join(root, name))
                            except Exception:
                                pass
                    os.rmdir(tmpdir)
                except Exception:
                    pass
