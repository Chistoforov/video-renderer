import os
import uuid
import time
import subprocess
import threading

import numpy as np
from flask import Flask, request, jsonify, send_from_directory
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

OUTPUT_DIR = "output"
ASSETS_DIR = "assets"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Визуальные настройки (можно менять)
# ---------------------------------------------------------------------------
WIDTH = 1080
HEIGHT = 1920
FONT_SIZE = 60
TEXT_COLOR = (255, 255, 255)
TEXT_PADDING = 80           # отступ текста от краёв кадра
BOX_PADDING = 40            # отступ бокса вокруг текста
BOX_RADIUS = 24
BOX_COLOR = (0, 0, 0, 90)  # полупрозрачный чёрный фон за текстом
GRADIENT_TOP = (72, 22, 96)     # фиолетовый
GRADIENT_BOTTOM = (18, 10, 48)  # тёмно-синий
DEFAULT_DURATION = 15       # длительность видео в секундах
CLEANUP_AFTER = 600         # удалять файлы через N секунд


def create_gradient(width: int, height: int, top: tuple, bottom: tuple) -> Image.Image:
    """Вертикальный градиент сверху вниз."""
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    for ch in range(3):
        arr[:, :, ch] = np.linspace(top[ch], bottom[ch], height).reshape(-1, 1)
    return Image.fromarray(arr)


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.Draw) -> str:
    """Переносит текст по словам так, чтобы ширина не превышала max_width."""
    words = text.split()
    lines: list[str] = []
    current: list[str] = []

    for word in words:
        test = " ".join(current + [word])
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]

    if current:
        lines.append(" ".join(current))

    return "\n".join(lines)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    """Загружает шрифт из assets/ с фолбэком на дефолтный."""
    font_path = os.path.join(ASSETS_DIR, "font.ttf")
    if os.path.exists(font_path):
        return ImageFont.truetype(font_path, size)
    return ImageFont.load_default(size)


def create_frame(text: str, output_path: str) -> None:
    """Генерирует кадр 1080x1920 с текстом вопроса."""

    # --- Фон ---
    bg_path = os.path.join(ASSETS_DIR, "background.jpg")
    if os.path.exists(bg_path):
        img = Image.open(bg_path).resize((WIDTH, HEIGHT)).convert("RGBA")
        # Затемняющий оверлей
        dark = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 100))
        img = Image.alpha_composite(img, dark)
    else:
        img = create_gradient(WIDTH, HEIGHT, GRADIENT_TOP, GRADIENT_BOTTOM).convert("RGBA")

    draw = ImageDraw.Draw(img)
    font = load_font(FONT_SIZE)

    # --- Текст ---
    max_text_w = WIDTH - TEXT_PADDING * 2
    wrapped = wrap_text(text, font, max_text_w, draw)

    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=20)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (WIDTH - tw) / 2
    ty = (HEIGHT - th) / 2

    # Полупрозрачный бокс за текстом
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    ov_draw.rounded_rectangle(
        [
            tx - BOX_PADDING,
            ty - BOX_PADDING,
            tx + tw + BOX_PADDING,
            ty + th + BOX_PADDING,
        ],
        radius=BOX_RADIUS,
        fill=BOX_COLOR,
    )
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # Тень текста
    draw.multiline_text(
        (tx + 2, ty + 2), wrapped, font=font,
        fill=(0, 0, 0, 180), align="center", spacing=20,
    )
    # Основной текст
    draw.multiline_text(
        (tx, ty), wrapped, font=font,
        fill=TEXT_COLOR, align="center", spacing=20,
    )

    img.convert("RGB").save(output_path, quality=95)


def render_video(frame_path: str, video_path: str, duration: int) -> subprocess.CompletedProcess:
    """Собирает MP4 из кадра + (опционально) музыки через FFmpeg."""

    music_path = os.path.join(ASSETS_DIR, "music.mp3")
    has_music = os.path.exists(music_path)

    cmd = ["ffmpeg", "-y"]

    # Все входы должны идти ДО выходных опций
    cmd += ["-loop", "1", "-i", frame_path]
    if has_music:
        cmd += ["-i", music_path]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]

    # Выходные опции (видео)
    cmd += [
        "-vf", f"scale={WIDTH}:{HEIGHT}",
        "-c:v", "libx264",
        "-t", str(duration),
        "-pix_fmt", "yuv420p",
        "-r", "30",
    ]

    # Выходные опции (аудио)
    if has_music:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    else:
        cmd += ["-c:a", "aac", "-shortest"]

    cmd.append(video_path)

    return subprocess.run(cmd, capture_output=True, text=True, timeout=180)


def schedule_cleanup(path: str) -> None:
    """Удаляет файл через CLEANUP_AFTER секунд."""
    def _clean():
        time.sleep(CLEANUP_AFTER)
        try:
            os.remove(path)
        except OSError:
            pass
    threading.Thread(target=_clean, daemon=True).start()


# ---------------------------------------------------------------------------
# Эндпоинты
# ---------------------------------------------------------------------------

@app.route("/render", methods=["POST"])
def render():
    """
    POST /render
    Body JSON: { "text": "Текст вопроса", "duration": 15 }
    Response:  { "video_url": "https://.../video/<id>.mp4" }
    """
    data = request.get_json(force=True)
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"error": "text is required"}), 400

    duration = int(data.get("duration", DEFAULT_DURATION))
    vid = str(uuid.uuid4())
    frame_path = os.path.join(OUTPUT_DIR, f"{vid}.jpg")
    video_path = os.path.join(OUTPUT_DIR, f"{vid}.mp4")

    # 1. Создаём кадр
    create_frame(text, frame_path)

    # 2. Рендерим видео
    result = render_video(frame_path, video_path, duration)

    # Удаляем кадр
    try:
        os.remove(frame_path)
    except OSError:
        pass

    if result.returncode != 0:
        return jsonify({"error": "ffmpeg failed", "details": result.stderr}), 500

    base_url = request.host_url.rstrip("/")
    video_url = f"{base_url}/video/{vid}.mp4"

    # Авто-удаление через 10 минут
    schedule_cleanup(video_path)

    return jsonify({"video_url": video_url, "video_id": vid})


@app.route("/video/<filename>")
def serve_video(filename):
    """Отдаёт отрендеренное видео."""
    return send_from_directory(OUTPUT_DIR, filename)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
