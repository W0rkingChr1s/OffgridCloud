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
    parts = [
        "einsatz/DJI_1/DJI_1_teil01.MP4",
        "einsatz/DJI_1/DJI_1_teil02.MP4",
        "einsatz/DJI_1/DJI_1_teil03.MP4",
    ]
    assert sent == parts
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
    assert deleted == parts + ["einsatz/DJI_1/DJI_1.srt"]  # .srt: gone if it exists


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
        # 413 won't go away by resending: failed at once, no 5x retry.
        assert job.status == transfers.TransferStatus.FAILED
        assert job.attempts == 1
        assert job.last_error.startswith("Teil 2/3: 413")
        assert (job.split_parts, job.split_done) == (3, 1)
        # The reconciler leaves it alone too.
        transfers.reconcile_once(db)
        db.refresh(job)
        assert job.status == transfers.TransferStatus.FAILED

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


def test_worker_resumes_after_last_uploaded_part(client, admin_auth, monkeypatch):
    from tests.test_tether import _queue_job

    media = _queue_job(client, admin_auth, b"v" * 100)
    _split_provider(monkeypatch, 40)
    monkeypatch.setattr(video_split, "split_video", _fake_split(4))
    sent = []
    fail = {"on": "teil03"}

    def upload(local, options, dest, bwlimit=0, on_progress=None):
        sent.append(dest.rsplit("_", 1)[-1])
        if fail["on"] and fail["on"] in dest:
            return UploadResult(False, 2, "connection reset by peer")
        return UploadResult(True, 10, "", 1.0)

    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        transfers.process_job(db, job, upload)
        assert job.status == transfers.TransferStatus.QUEUED  # transient: retried
        assert job.split_done == 2
        fail["on"] = ""
        sent.clear()
        job.next_attempt_at = None
        db.commit()
        transfers.process_job(db, job, upload)
        assert job.status == transfers.TransferStatus.DONE
        assert (job.split_parts, job.split_done) == (4, 4)
    assert sent == ["teil03.mp4", "teil04.mp4"]  # 1 and 2 were already up

    # A different limit cuts differently: start over.
    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        job.status = transfers.TransferStatus.QUEUED
        job.split_done = 2
        db.commit()
        _split_provider(monkeypatch, 30)
        sent.clear()
        transfers.process_job(db, job, upload)
    assert sent[0] == "teil01.mp4"


def test_permanent_error_classification():
    assert transfers.is_permanent_error("413 Request Entity Too Large")
    assert transfers.is_permanent_error("Video teilen fehlgeschlagen: ffmpeg ist nicht installiert")
    assert not transfers.is_permanent_error("connection reset by peer")
    assert not transfers.is_permanent_error("zu wenig Platz im Puffer zum Teilen")


def test_split_binary_and_join(tmp_path):
    src = tmp_path / "data.zip"
    payload = bytes(range(256)) * 1000  # 256 kB
    src.write_bytes(payload)
    result = video_split.split_binary(src, tmp_path / "p", 100_000)
    assert result.ok
    assert [p.stat().st_size for p in result.parts] == [100_000, 100_000, 56_000]
    assert b"".join(p.read_bytes() for p in result.parts) == payload
    assert video_split.binary_part_name("data.zip", 2, 3) == "data.zip.002"
    text = video_split.join_instructions("data.zip", 3)
    assert 'cat "data.zip.*" > "data.zip"' in text


def test_split_refuses_without_space(tmp_path, monkeypatch):
    src = tmp_path / "big.bin"
    src.write_bytes(b"x" * 1000)

    class Usage:
        free = 10

    monkeypatch.setattr(video_split.shutil, "disk_usage", lambda p: Usage)
    result = video_split.split_binary(src, tmp_path / "p", 100)
    assert not result.ok and "zu wenig Platz" in result.message


def test_worker_splits_other_files_with_join_note(client, admin_auth, monkeypatch):
    from tests.test_transfers import _folder, _provider, _upload

    prov = _provider(client, admin_auth, name="Bin")
    folder = _folder(client, admin_auth, name="Bin")
    client.post(
        f"/api/folders/{folder['id']}/providers", headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    media = _upload(client, admin_auth, folder["id"], "log.zip", b"z" * 25)
    _split_provider(monkeypatch, 10)
    got = {}

    def upload(local, options, dest, bwlimit=0, on_progress=None):
        from pathlib import Path
        got[dest] = Path(local).read_bytes()
        return UploadResult(True, len(got[dest]), "", 1.0)

    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        transfers.process_job(db, job, upload)
        assert job.status == transfers.TransferStatus.DONE
    assert list(got) == ["log/log.zip.001", "log/log.zip.002", "log/log.zip.003",
                         "log/ZUSAMMENFUEGEN.txt"]
    assert b"".join(got[k] for k in list(got)[:3]) == b"z" * 25
    assert b"log.zip" in got["log/ZUSAMMENFUEGEN.txt"]


@needs_ffmpeg
def test_split_keeps_subtitle_telemetry(tmp_path):
    srt = tmp_path / "in.srt"
    srt.write_text("".join(
        f"{i + 1}\n00:00:{i:02d},000 --> 00:00:{i + 1:02d},000\nGPS(47.8,10.9) H {i}m\n\n"
        for i in range(12)
    ))
    plain = tmp_path / "plain.mp4"
    _make_video(plain)
    src = tmp_path / "DJI.MP4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(plain), "-i", str(srt),
         "-map", "0", "-map", "1", "-c", "copy", "-c:s", "mov_text", str(src)],
        check=True,
    )
    result = video_split.split_video(src, tmp_path / "parts", src.stat().st_size // 3)
    assert result.ok, result.message
    (path, name), = result.extras
    assert name == "DJI.srt" and "GPS(47.8,10.9)" in path.read_text()
    types = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(result.parts[0])],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "subtitle" in types
