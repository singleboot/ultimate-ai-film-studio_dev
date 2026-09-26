#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Objective QA metrics for generated media (history drawer).

Pure PIL + ffmpeg metrics, no numpy:
- sharpness: mean edge energy on sampled frames (or the image itself)
- motion / jitter: mean and std-dev of inter-frame histogram distance
- audio: RMS/peak dB + silence percentage via ffmpeg astats/silencedetect

Results are cached per file (mtime+size keyed) so the history endpoint can
compute them lazily without re-processing unchanged files on every poll.
"""
import json
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

_lock = threading.Lock()
_cache: dict = {}
_cache_path = Path(__file__).parent / "gen_qa_cache.json"

try:
    _cache = json.loads(_cache_path.read_text(encoding="utf-8"))
    if not isinstance(_cache, dict):
        _cache = {}
except Exception:
    _cache = {}


def _save_cache():
    try:
        _cache_path.write_text(json.dumps(_cache), encoding="utf-8")
    except Exception:
        pass


def _gray(im):
    return im.convert("L")


def _edge_energy(im):
    g = _gray(im.resize((320, 180))).filter(ImageFilter.FIND_EDGES)
    h = g.histogram()
    total = sum(h) or 1
    return sum(i * c for i, c in enumerate(h)) / total


def _frame_diff(a, b):
    d = ImageChops.difference(_gray(a), _gray(b))
    h = d.histogram()
    total = sum(h) or 1
    return sum(i * c for i, c in enumerate(h)) / total


def _frames(path, times):
    out = []
    for t in times:
        f = os.path.join(tempfile.gettempdir(), "qa_frame.png")
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-ss", str(t), "-i", str(path), "-vframes", "1", f],
                capture_output=True, timeout=60)
        except Exception:
            return out
        if r.returncode == 0 and os.path.exists(f) and os.path.getsize(f) > 0:
            try:
                out.append(Image.open(f).convert("RGB"))
            except Exception:
                pass
    return out


def video_metrics(path: Path, dur: float, samples: int = 6) -> dict:
    if dur <= 0:
        return {}
    times = [min(max(0.1, dur - 0.3), 0.3 + i * (dur - 0.9) / max(1, samples - 1))
             for i in range(samples)]
    fr = _frames(path, times)
    if len(fr) < 2:
        return {}
    sharp = [_edge_energy(im) for im in fr]
    diffs = [_frame_diff(fr[i], fr[i + 1]) for i in range(len(fr) - 1)]
    mean_d = sum(diffs) / len(diffs)
    var_d = sum((d - mean_d) ** 2 for d in diffs) / len(diffs)
    return {
        "sharpness": round(sum(sharp) / len(sharp), 1),
        "motion": round(mean_d, 1),
        "jitter": round(var_d ** 0.5, 2),
    }


def image_metrics(path: Path) -> dict:
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return {}
    return {"sharpness": round(_edge_energy(im), 1)}


def audio_stats(path: Path) -> dict:
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", str(path), "-af",
             "astats=metadata=1:reset=0,silencedetect=noise=-45dB:d=0.8",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=120)
    except Exception:
        return {}
    err = r.stderr or ""
    rms = re.findall(r"RMS level dB: (-?[\d.]+)", err)
    peak = re.findall(r"Peak level dB: (-?[\d.]+)", err)
    sil = re.findall(r"silence_duration: ([\d.]+)", err)
    d = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = 0.0
    if d:
        dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
    out = {}
    if rms:
        out["rms"] = round(float(rms[-1]), 1)
    if peak:
        out["peak"] = round(float(peak[-1]), 1)
    if dur:
        out["silence_pct"] = round(100 * sum(float(x) for x in sil) / dur, 1)
    return out


def compute_qa(path_str: str, task: str) -> dict | None:
    """QA for one file. task in {image, video, audio}. Returns None if unavailable."""
    p = Path(path_str)
    if not p.exists() or p.stat().st_size == 0:
        return None
    key = f"{p}|{p.stat().st_mtime_ns}|{p.stat().st_size}"
    with _lock:
        if key in _cache:
            return _cache[key]
    qa = None
    try:
        if task == "video":
            dur = 0.0
            try:
                r = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "csv=p=0", str(p)], capture_output=True, text=True, timeout=30)
                dur = float(r.stdout.strip() or 0)
            except Exception:
                pass
            qa = video_metrics(p, dur)
            if qa:
                qa["duration"] = round(dur, 2)
        elif task == "image":
            qa = image_metrics(p)
        elif task == "audio":
            qa = audio_stats(p)
    except Exception:
        qa = None
    with _lock:
        if len(_cache) > 600:
            _cache.clear()
        _cache[key] = qa
        _save_cache()
    return qa
