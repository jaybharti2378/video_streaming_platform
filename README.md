# Video Streaming Platform

A Flask-based video upload, processing, streaming and management platform. Users can create an account, upload videos, view processing status, stream HLS playback, and delete their videos.

## Features

- User registration, login and logout
- Personal dashboard showing uploaded videos
- MP4, MOV, MKV and WEBM upload support
- FFmpeg converts uploads into HLS (`.m3u8` playlist + `.ts` segments)
- HLS playback in the browser using hls.js
- SQLite database for users and video metadata
- Local storage by default, with optional AWS S3 storage
- Per-user access checks for video playback and deletion

## Project structure

```text
video_streaming_platform/
├── app.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── login.html
│   ├── register.html
│   ├── dashboard.html
│   └── watch.html
└── static/
    └── style.css
```

## Requirements

- Python 3.10+
- FFmpeg installed and available on your system PATH
- AWS account and an S3 bucket only if you want cloud storage

Check FFmpeg:

```bash
ffmpeg -version
```

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install ffmpeg
```

macOS (Homebrew):

```bash
brew install ffmpeg
```

Windows: install FFmpeg and add its `bin` directory to PATH.

## Run locally

1. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create your environment file:

   ```bash
   # Windows PowerShell:
   Copy-Item .env.example .env
   # macOS/Linux:
   cp .env.example .env
   ```

4. Edit `.env` and set a long random `SECRET_KEY`. Leave `S3_BUCKET` blank to use local storage.

5. Start the app:

   ```bash
   python app.py
   ```

6. Open http://127.0.0.1:5000 and register an account.

## Optional AWS S3 setup

The app uploads HLS playlists and segments to S3 when `S3_BUCKET` is configured. Set:

```env
AWS_REGION=ap-south-1
S3_BUCKET=your-bucket-name
S3_PUBLIC_BASE_URL=https://your-cloudfront-domain-or-public-bucket-base
```

Configure AWS credentials using an IAM role (recommended) or the standard AWS environment/credentials chain. Do not put AWS keys in source code or commit them to GitHub.

The configured `S3_PUBLIC_BASE_URL` must serve objects under `videos/<video-id>/...` and allow the browser to fetch the playlist and segments. For production, prefer CloudFront in front of a private S3 bucket and configure appropriate origin access and CORS. This starter app uses a public playback base URL; it does not implement signed-cookie authorization for private CloudFront playback.

## Upload limits and storage

- Default maximum upload size is 500 MB (`MAX_UPLOAD_MB`).
- Original files and HLS output are stored under `data/`.
- `data/` is excluded from Git by `.gitignore`; uploaded videos and the SQLite database should never be committed.
- For production, use a persistent disk or object storage and a background job queue for long transcodes.

## Push to GitHub

Create an empty repository on GitHub, then run these commands from this project directory:

```bash
git init
git add .
git commit -m "Initial video streaming platform"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

Do not upload `.env`, AWS credentials, the `data/` folder, or actual video files.

## Important production improvements

This is a learning/MVP project. Before public deployment, add CSRF protection, email verification/password reset, rate limiting, HTTPS, secure cookie settings, background transcoding, robust S3 private access, quotas, malware/content checks, and a production WSGI server such as Gunicorn. Also disable Flask debug mode.
