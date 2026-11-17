import os
import sqlite3
import subprocess
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path
from urllib.parse import quote

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from flask import (
    Flask, abort, flash, redirect, render_template, request,
    send_from_directory, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
HLS_DIR = DATA_DIR / "hls"
DB_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "app.db"))
ALLOWED_EXTENSIONS = {"mp4", "mov", "mkv", "webm"}
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))

for folder in (DATA_DIR, UPLOAD_DIR, HLS_DIR):
    folder.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-development-secret")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                filename TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                storage_type TEXT NOT NULL DEFAULT 'local',
                hls_path TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)


init_db()


def get_s3():
    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        return None, None
    client = boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "ap-south-1"),
        endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
    )
    return client, bucket


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def create_hls(input_path: Path, output_dir: Path):
    """Transcode an uploaded video into HLS (playlist + MPEG-TS segments)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    playlist = output_dir / "index.m3u8"
    command = [
        os.getenv("FFMPEG_PATH", "ffmpeg"), "-y", "-i", str(input_path),
        "-codec:v", "libx264", "-codec:a", "aac",
        "-preset", "veryfast", "-crf", "23",
        "-hls_time", "6", "-hls_playlist_type", "vod",
        "-hls_segment_filename", str(output_dir / "segment_%03d.ts"),
        str(playlist),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1500:] or "FFmpeg transcoding failed.")
    return playlist


def upload_hls_to_s3(video_id: str, output_dir: Path):
    client, bucket = get_s3()
    if not client:
        return None
    prefix = f"videos/{video_id}/"
    content_types = {
        ".m3u8": "application/vnd.apple.mpegurl",
        ".ts": "video/mp2t",
    }
    for path in output_dir.iterdir():
        if path.is_file():
            key = prefix + path.name
            client.upload_file(
                str(path), bucket, key,
                ExtraArgs={"ContentType": content_types.get(path.suffix, "application/octet-stream")}
            )
    public_base = os.getenv("S3_PUBLIC_BASE_URL", "").rstrip("/")
    if not public_base:
        raise RuntimeError(
            "S3 upload completed, but S3_PUBLIC_BASE_URL is missing. "
            "Set it to a public bucket or CloudFront base URL."
        )
    return f"{public_base}/{prefix}index.m3u8"


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(username) < 3 or len(password) < 8:
            flash("Username must be 3+ characters and password at least 8 characters.", "error")
            return render_template("register.html")
        try:
            with db() as conn:
                conn.execute(
                    "INSERT INTO users(username, password_hash, created_at) VALUES (?, ?, ?)",
                    (username, generate_password_hash(password), datetime.utcnow().isoformat())
                )
            flash("Account created. Please sign in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("That username is already taken.", "error")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with db() as conn:
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))
        flash("Incorrect username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    with db() as conn:
        videos = conn.execute(
            "SELECT * FROM videos WHERE user_id = ? ORDER BY created_at DESC",
            (session["user_id"],)
        ).fetchall()
    return render_template("dashboard.html", videos=videos)


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    file = request.files.get("video")
    title = request.form.get("title", "").strip()
    if not file or not file.filename:
        flash("Choose a video file first.", "error")
        return redirect(url_for("dashboard"))
    if not allowed_file(file.filename):
        flash("Supported formats: MP4, MOV, MKV and WEBM.", "error")
        return redirect(url_for("dashboard"))

    video_id = uuid.uuid4().hex
    safe_name = secure_filename(file.filename) or f"{video_id}.mp4"
    input_path = UPLOAD_DIR / f"{video_id}_{safe_name}"
    output_dir = HLS_DIR / video_id
    file.save(input_path)

    try:
        playlist = create_hls(input_path, output_dir)
        storage_type = "local"
        hls_path = video_id
        if os.getenv("S3_BUCKET"):
            remote_url = upload_hls_to_s3(video_id, output_dir)
            if remote_url:
                storage_type = "s3"
                hls_path = remote_url

        with db() as conn:
            conn.execute("""
                INSERT INTO videos(id, user_id, title, filename, status, storage_type, hls_path, created_at)
                VALUES (?, ?, ?, ?, 'ready', ?, ?, ?)
            """, (
                video_id, session["user_id"], title or Path(safe_name).stem,
                safe_name, storage_type, hls_path, datetime.utcnow().isoformat()
            ))
        flash("Video uploaded and processed successfully.", "success")
    except (RuntimeError, OSError, BotoCoreError, ClientError) as exc:
        with db() as conn:
            conn.execute("""
                INSERT INTO videos(id, user_id, title, filename, status, storage_type, hls_path, created_at)
                VALUES (?, ?, ?, ?, 'failed', 'local', NULL, ?)
            """, (
                video_id, session["user_id"], title or Path(safe_name).stem,
                safe_name, datetime.utcnow().isoformat()
            ))
        flash(f"Video processing failed: {str(exc)[:250]}", "error")
    finally:
        # Keep the original upload for future re-processing; remove it here if storage is limited.
        pass
    return redirect(url_for("dashboard"))


def owned_video(video_id):
    with db() as conn:
        video = conn.execute(
            "SELECT * FROM videos WHERE id = ? AND user_id = ?",
            (video_id, session["user_id"])
        ).fetchone()
    if not video:
        abort(404)
    return video


@app.route("/watch/<video_id>")
@login_required
def watch(video_id):
    video = owned_video(video_id)
    if video["status"] != "ready":
        flash("This video is not ready to stream.", "error")
        return redirect(url_for("dashboard"))
    if video["storage_type"] == "s3":
        playlist_url = video["hls_path"]
    else:
        playlist_url = url_for("local_hls", video_id=video_id, filename="index.m3u8")
    return render_template("watch.html", video=video, playlist_url=playlist_url)


@app.route("/hls/<video_id>/<path:filename>")
@login_required
def local_hls(video_id, filename):
    video = owned_video(video_id)
    if video["storage_type"] != "local" or video["status"] != "ready":
        abort(404)
    safe_dir = HLS_DIR / video_id
    return send_from_directory(safe_dir, filename)


@app.route("/delete/<video_id>", methods=["POST"])
@login_required
def delete_video(video_id):
    video = owned_video(video_id)
    with db() as conn:
        conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    import shutil
    shutil.rmtree(HLS_DIR / video_id, ignore_errors=True)
    # Remove original upload belonging to this video.
    for candidate in UPLOAD_DIR.glob(f"{video_id}_*"):
        candidate.unlink(missing_ok=True)
    if video["storage_type"] == "s3":
        client, bucket = get_s3()
        if client:
            try:
                response = client.list_objects_v2(Bucket=bucket, Prefix=f"videos/{video_id}/")
                objects = [{"Key": item["Key"]} for item in response.get("Contents", [])]
                if objects:
                    client.delete_objects(Bucket=bucket, Delete={"Objects": objects})
            except (BotoCoreError, ClientError):
                app.logger.exception("Could not delete S3 objects for %s", video_id)
    flash("Video deleted.", "success")
    return redirect(url_for("dashboard"))


@app.errorhandler(413)
def too_large(_error):
    flash(f"Upload exceeds the {MAX_UPLOAD_MB} MB limit.", "error")
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
