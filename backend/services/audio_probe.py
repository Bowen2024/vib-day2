from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from errors import AppError

ALLOWED_CODECS = {"opus", "aac"}


@dataclass
class ProbeResult:
    container: str
    codec: str
    duration_sec: float


def _ffprobe_bin() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise AppError(
            500,
            "AUDIO_PROBE_MISSING",
            "服务器缺少音频探测工具 ffprobe，无法校验录音。请先安装 FFmpeg。",
            "upload",
        )
    return path


def _run_ffprobe(args: list[str], timeout: float) -> dict:
    completed = subprocess.run(
        [_ffprobe_bin(), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise AppError(
            415,
            "AUDIO_UNSUPPORTED",
            "当前录音格式不受支持，请使用最新版 Chrome 或 Edge 录制。",
            "upload",
        )
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AppError(
            415,
            "AUDIO_UNSUPPORTED",
            "当前录音格式不受支持，请使用最新版 Chrome 或 Edge 录制。",
            "upload",
        ) from exc


def _parse_seconds(value: object) -> float | None:
    if value in (None, "", "N/A", "n/a"):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds < 0 or seconds != seconds:  # NaN
        return None
    return seconds


def _duration_from_format_and_streams(payload: dict) -> float | None:
    fmt = payload.get("format") or {}
    for candidate in (
        fmt.get("duration"),
        (fmt.get("tags") or {}).get("DURATION"),
        (fmt.get("tags") or {}).get("duration"),
    ):
        parsed = _parse_seconds(candidate)
        if parsed is not None:
            return parsed

    for stream in payload.get("streams") or []:
        if stream.get("codec_type") != "audio":
            continue
        tags = stream.get("tags") or {}
        for candidate in (stream.get("duration"), tags.get("DURATION"), tags.get("duration")):
            parsed = _parse_seconds(candidate)
            if parsed is not None:
                return parsed
    return None


def _duration_from_packets(path: Path) -> float | None:
    payload = _run_ffprobe(
        [
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "packet=pts_time,dts_time",
            "-of",
            "json",
            str(path),
        ],
        timeout=10,
    )
    timestamps: list[float] = []
    for packet in payload.get("packets") or []:
        parsed = _parse_seconds(packet.get("pts_time"))
        if parsed is None:
            parsed = _parse_seconds(packet.get("dts_time"))
        if parsed is not None:
            timestamps.append(parsed)
    if not timestamps:
        return None
    return max(timestamps) - min(timestamps)


def probe_audio(path: Path) -> ProbeResult:
    payload = _run_ffprobe(
        [
            "-v",
            "error",
            "-show_entries",
            "format=format_name,duration:stream=codec_name,codec_type,duration:format_tags=DURATION:stream_tags=DURATION",
            "-of",
            "json",
            str(path),
        ],
        timeout=8,
    )

    format_name = str((payload.get("format") or {}).get("format_name") or "").lower()
    if "webm" not in format_name:
        raise AppError(
            415,
            "AUDIO_UNSUPPORTED",
            "当前录音格式不受支持，请使用最新版 Chrome 或 Edge 录制。",
            "upload",
        )

    audio_streams = [
        stream
        for stream in (payload.get("streams") or [])
        if stream.get("codec_type") == "audio"
    ]
    if not audio_streams:
        raise AppError(
            415,
            "AUDIO_UNSUPPORTED",
            "当前录音格式不受支持，请使用最新版 Chrome 或 Edge 录制。",
            "upload",
        )

    codec = str(audio_streams[0].get("codec_name") or "").lower()
    if codec not in ALLOWED_CODECS:
        raise AppError(
            415,
            "AUDIO_UNSUPPORTED",
            "当前录音格式不受支持，请使用最新版 Chrome 或 Edge 录制。",
            "upload",
        )

    duration_sec = _duration_from_format_and_streams(payload)
    if duration_sec is None:
        duration_sec = _duration_from_packets(path)
    if duration_sec is None:
        raise AppError(
            422,
            "AUDIO_DURATION_INVALID",
            "无法确认录音时长，请重新录制。",
            "upload",
        )

    return ProbeResult(container="webm", codec=codec, duration_sec=duration_sec)
