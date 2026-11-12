import time
import urllib.request
import urllib.parse
import json
import uuid
import os
import dbus
from pypresence import Presence, ActivityType

CLIENT_ID = "id from discord developer portal"

session_bus = dbus.SessionBus()
dbus_daemon = dbus.Interface(
    session_bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus"),
    "org.freedesktop.DBus"
)

art_cache = {}

def build_multipart(fields, file_field, filename, file_data):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n".encode("utf-8")
    )
    parts.append(file_data)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary

def upload_local_cover(raw_art_url):
    if not raw_art_url:
        return "yandex_logo"
    if raw_art_url.startswith("http"):
        return raw_art_url
    if raw_art_url in art_cache:
        return art_cache[raw_art_url]

    if not raw_art_url.startswith("file://"):
        return "yandex_logo"

    local_path = urllib.parse.unquote(raw_art_url[7:])
    time.sleep(0.3)
    if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
        return "yandex_logo"

    with open(local_path, "rb") as f:
        file_data = f.read()

    # Litterbox (временное хранилище Catbox на 1 час)
    try:
        body, boundary = build_multipart(
            {"reqtype": "fileupload", "time": "1h"},
            "fileToUpload", "cover.jpg", file_data
        )
        req = urllib.request.Request(
            "https://catbox.moe",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "Mozilla/5.0"
            }
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            url = resp.read().decode("utf-8").strip()
            if url.startswith("http"):
                art_cache.clear()
                art_cache[raw_art_url] = url
                return url
    except Exception as e:
        print(f"Litterbox ошибка: {e}")

    # Uguu.se (прямой endpoint upload.php)
    try:
        body, boundary = build_multipart({}, "files[]", "cover.jpg", file_data)
        req = urllib.request.Request(
            "https://uguu.se/upload.php",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "Mozilla/5.0"
            }
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            files = data.get("files", [])
            if files and files[0].get("url"):
                url = files[0]["url"]
                art_cache.clear()
                art_cache[raw_art_url] = url
                return url
    except Exception as e:
        print(f"Uguu ошибка: {e}")

    # Tmpfiles.org
    try:
        body, boundary = build_multipart({}, "file", "cover.jpg", file_data)
        req = urllib.request.Request(
            "https://tmpfiles.org",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "Mozilla/5.0"
            }
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_url = data.get("data", {}).get("url", "")
            if raw_url.startswith("http"):
                url = raw_url.replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)
                art_cache.clear()
                art_cache[raw_art_url] = url
                return url
    except Exception as e:
        print(f"Tmpfiles ошибка: {e}")

    return "yandex_logo"

def is_yandex_music(service_name):
    try:
        pid = dbus_daemon.GetConnectionUnixProcessID(service_name)
        with open(f"/proc/{pid}/comm", "r") as f:
            process_name = f.read().strip().lower()
        return process_name == "yandexmusic"
    except Exception:
        return False

def connect_discord():
    while True:
        try:
            rpc = Presence(CLIENT_ID)
            rpc.connect()
            print("Подключено к Discord!")
            return rpc
        except Exception:
            time.sleep(5)

def get_yandex_track():
    for service in session_bus.list_names():
        if service.startswith("org.mpris.MediaPlayer2.") and is_yandex_music(service):
            try:
                player = session_bus.get_object(service, "/org/mpris/MediaPlayer2")
                props = dbus.Interface(player, "org.freedesktop.DBus.Properties")

                status = props.Get("org.mpris.MediaPlayer2.Player", "PlaybackStatus")
                if status != "Playing":
                    continue

                metadata = props.Get("org.mpris.MediaPlayer2.Player", "Metadata")
                position_us = int(props.Get("org.mpris.MediaPlayer2.Player", "Position"))
                length_us = int(metadata.get("mpris:length", 0))

                title = str(metadata.get("xesam:title", "Неизвестный трек"))
                artists = metadata.get("xesam:artist", ["Неизвестный исполнитель"])
                artist_str = ", ".join(str(a) for a in artists)
                raw_art_url = str(metadata.get("mpris:artUrl", ""))

                return {
                    "title": title,
                    "artist": artist_str,
                    "raw_art": raw_art_url,
                    "position": position_us // 1_000_000,
                    "length": length_us // 1_000_000
                }
            except Exception:
                continue
    return None

rpc = connect_discord()
last_state = None

while True:
    try:
        track = get_yandex_track()
        if track:
            now = int(time.time())
            start_ts = now - track["position"]
            end_ts = start_ts + track["length"] if track["length"] > 0 else None

            need_update = (
                last_state is None
                or last_state["title"] != track["title"]
                or last_state["artist"] != track["artist"]
                or abs(last_state["start_ts"] - start_ts) > 2
            )

            if need_update:
                art_url = upload_local_cover(track["raw_art"])
                rpc.update(
                    activity_type=ActivityType.LISTENING,
                    details=track["title"][:128],
                    state=track["artist"][:128],
                    large_image=art_url,
                    large_text="Яндекс Музыка",
                    start=start_ts,
                    end=end_ts
                )
                print(f"Играет: {track['artist']} - {track['title']} | Обложка: {art_url}")
                last_state = {
                    "title": track["title"],
                    "artist": track["artist"],
                    "start_ts": start_ts
                }
        elif last_state is not None:
            rpc.clear()
            print("Пауза / Стоп")
            last_state = None

        time.sleep(2)
    except Exception as e:
        print(f"Ошибка цикла: {e}")
        last_state = None
        rpc = connect_discord()
