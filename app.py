"""GrabAll download engine — Flask + yt-dlp.

Endpoints:
  GET  /health          -> {"ok": true}
  POST /extract {url}   -> {title, thumbnail, duration, uploader, platform,
                            videos: [{height, ext, url, filesize}],
                            audio:  {ext, url, filesize} | null}
  GET  /mp3?url=<page>  -> MP3 file (needs ffmpeg on the server); the app
                           falls back to the audio stream from /extract.
"""
import os
import tempfile

from flask import Flask, jsonify, request, send_file

import yt_dlp

app = Flask(__name__)

YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "socket_timeout": 20,
}


@app.get("/health")
def health():
    return jsonify({"ok": True, "engine": "yt-dlp", "version": yt_dlp.version.__version__})


@app.post("/extract")
def extract():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # yt-dlp raises DownloadError and others
        return jsonify({"error": str(e)[:300]}), 422

    videos = {}
    audio = None
    for f in info.get("formats", []):
        furl = f.get("url")
        if not furl:
            continue
        height = f.get("height")
        vcodec = f.get("vcodec") or "none"
        acodec = f.get("acodec") or "none"
        entry = {
            "height": height,
            "ext": f.get("ext"),
            "url": furl,
            "filesize": f.get("filesize") or f.get("filesize_approx"),
        }
        if vcodec != "none" and height:
            cur = videos.get(height)
            # Prefer entries that also carry audio, then mp4.
            score = (1 if acodec != "none" else 0, 1 if f.get("ext") == "mp4" else 0)
            if cur is None or score > cur[0]:
                videos[height] = (score, entry)
        elif vcodec == "none" and acodec != "none":
            if audio is None or (f.get("abr") or 0) > (audio.get("abr") or 0):
                audio = dict(entry, abr=f.get("abr"))

    return jsonify(
        {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "uploader": info.get("uploader"),
            "platform": info.get("extractor_key") or info.get("extractor"),
            "videos": [videos[h][1] for h in sorted(videos, reverse=True)],
            "audio": audio,
        }
    )


@app.get("/mp3")
def mp3():
    url = (request.args.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    tmpdir = tempfile.mkdtemp(prefix="graball-")
    opts = dict(
        YDL_OPTS,
        format="bestaudio/best",
        outtmpl=os.path.join(tmpdir, "%(id)s.%(ext)s"),
        postprocessors=[
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ],
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        path = os.path.join(tmpdir, f"{info['id']}.mp3")
        if not os.path.exists(path):
            return jsonify({"error": "conversion failed (ffmpeg missing?)"}), 501
        return send_file(path, as_attachment=True, download_name="graball.mp3")
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 422


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8791")))
