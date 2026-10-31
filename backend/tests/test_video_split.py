"""Cutting oversized videos into playable parts (app.video_split + worker)."""

import dataclasses
import subprocess

import pytest
from sqlalchemy import select

from app import transfers, video_split
from app.db import SessionLocal
from app.models import TransferJob
from app.rclone import DeleteResult, UploadResult

needs_ffmpeg = pytest.mark.skipif(
    not video_split.ffmpeg_available(), reason="ffmpeg/ffprobe not installed"
)


def test_part_names():
    assert video_split.part_name("DJI_0005_W.MP4", 3, 38) == "DJI_0005_W_teil03.MP4"
    assert video_split.part_name("a.mp4", 7, 120) == "a_teil007.mp4"
    assert video_split.part_folder("DJI_0005_W.MP4") == "DJI_0005_W"
    assert video_split.is_splittable("x.MOV") and not video_split.is_splittable("x.jpg")


def _make_video(path, seconds=12):
    # Noise compresses badly, so the file is big enough to need several parts;
    # one keyframe per second keeps the cut points close to the plan.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-vf", "noise=alls=60:allf=t", "-c:v", "libx264", "-g", "25",
         "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )


@needs_ffmpeg
def test_split_video_parts_fit_and_play(tmp_path):
    src = tmp_path / "clip.MP4"
    _make_video(src)
    size = src.stat().st_size
    limit = size // 4
    result = video_split.split_video(src, tmp_path / "parts", limit)
    assert result.ok, result.message
    assert len(result.parts) >= 4
    assert all(p.stat().st_size <= limit for p in result.parts)
    # Every part is a standalone, playable video with audio.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(result.parts[1])],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "video" in probe and "audio" in probe


def test_split_reports_missing_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(video_split, "ffmpeg_available", lambda: False)
    result = video_split.split_video(tmp_path / "x.mp4", tmp_path / "p", 10)
    assert not result.ok and "ffmpeg" in result.message


# --- Worker -----------------------------------------------------------------


def _split_provider(monkeypatch, limit):
    real = transfers.get_type

    def patched(key):
        return dataclasses.replace(real(key), split_limit=lambda cfg: limit)

    monkeypatch.setattr(transfers, "get_type", patched)


def _fake_split(n):
    def split(src, out_dir, limit):
        out_dir.mkdir(parents=True, exist_ok=True)
        parts = []
        for i in range(n):
            p = out_dir / f"seg{i:04d}.mp4"
            p.write_bytes(b"p" * 10)
            parts.append(p)
        return video_split.SplitResult(True, tuple(parts))
    return split


def test_worker_uploads_parts_into_sub_folder_and_deletes_them(client, admin_auth, monkeypatch):
    from tests.test_transfers import _folder, _provider, _upload

    client.put("/api/system", headers=admin_auth, json={"delete_remote_on_local_delete": True})
    prov = _provider(client, admin_auth, name="Split")
    folder = _folder(client, admin_auth, name="Split")
    client.post(
        f"/api/folders/{folder['id']}/providers", headers=admin_auth,
        json={"provider_id": prov["id"], "dest_path": "einsatz"},
    )
    media = _upload(client, admin_auth, folder["id"], "DJI_1.MP4", b"v" * 100)
    _split_provider(monkeypatch, 40)
    monkeypatch.setattr(video_split, "split_video", _fake_split(3))
    sent = []

    def upload(local, options, dest, bwlimit=0, on_progress=None):
        sent.append(dest)
        on_progress(10, 10, 5.0)
        return UploadResult(True, 10, "", 5.0)

    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        transfers.process_job(db, job, upload)
        assert job.status == transfers.TransferStatus.DONE
        assert job.split_parts == 3
        stored = job.media_id
    assert sent == [
        "einsatz/DJI_1/DJI_1_teil01.MP4",
        "einsatz/DJI_1/DJI_1_teil02.MP4",
        "einsatz/DJI_1/DJI_1_teil03.MP4",
    ]
    # Work folder is gone again.
    with SessionLocal() as db:
        from app.models import MediaItem
        path = db.get(MediaItem, stored).stored_path
    from pathlib import Path
    assert not any(p.name.startswith(".split-") for p in Path(path).parent.iterdir())

    deleted = []

    def spy(options, dest):
        deleted.append(dest)
        return DeleteResult(True, "ok")

    with SessionLocal() as db:
        summary = transfers.delete_media(db, media["id"], delete_fn=spy)
    assert summary["remote_deleted"] == 1
    assert deleted == sent


def test_worker_reports_failing_part_and_small_files_stay_whole(client, admin_auth, monkeypatch):
    from tests.test_tether import _queue_job

    media = _queue_job(client, admin_auth, b"v" * 100)
    _split_provider(monkeypatch, 40)
    monkeypatch.setattr(video_split, "split_video", _fake_split(3))

    def upload(local, options, dest, bwlimit=0, on_progress=None):
        if dest.endswith("teil02.mp4"):
            return UploadResult(False, 3, "413 Payload Too Large")
        return UploadResult(True, 10, "", 1.0)

    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        transfers.process_job(db, job, upload)
        assert job.status == transfers.TransferStatus.QUEUED
        assert job.last_error.startswith("Teil 2/3: 413")
        assert job.split_parts == 0

    # Under the limit: one plain upload, no split.
    _split_provider(monkeypatch, 1000)
    sent = []

    def ok(local, options, dest, bwlimit=0, on_progress=None):
        sent.append(dest)
        return UploadResult(True, 100, "", 1.0)

    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        transfers.process_job(db, job, ok)
    assert sent == ["clip.mp4"]
