import os
import uuid
import time
import subprocess
import threading
import gc
import urllib.request
import urllib.parse

from flask import Flask, request, jsonify, send_from_directory
from PIL import Image, ImageDraw, ImageFont, ImageFilter

app = Flask(__name__)

OUTPUT_DIR = "output"
ASSETS_DIR = "assets"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Визуальные настройки (можно менять)
# ---------------------------------------------------------------------------
WIDTH = 720
HEIGHT = 1280
FONT_SIZE = 44
TEXT_COLOR = (255, 255, 255)
TEXT_PADDING = 56
BOX_PADDING = 28
BOX_RADIUS = 24
BOX_COLOR = (0, 0, 0, 90)
GRADIENT_TOP = (72, 22, 96)
GRADIENT_BOTTOM = (18, 10, 48)
DEFAULT_DURATION = 15
CLEANUP_AFTER = 600

# Промпт для авто-генерации фона через Pollinations.ai (бесплатно, без ключа)
BG_PROMPT = (
    "aesthetic dark moody background for instagram reels, "
    "soft bokeh lights, purple and blue tones, abstract, "
    "no text, no people, cinematic"
)


def fetch_background(prompt: str, width: int, height: int) -> Image.Image | None:
    """Генерирует фон через Pollinations.ai (бесплатно, без API-ключа)."""
    try:
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&nologo=true"
        tmp_path = os.path.join(OUTPUT_DIR, f"_bg_{uuid.uuid4().hex[:8]}.jpg")

        urllib.request.urlretrieve(url, tmp_path)
        img = Image.open(tmp_path).convert("RGB").resize((width, height))

        try:
            os.remove(tmp_path)
        except OSError:
            pass

        return img
    except Exception:
        return None


def download_image(url: str, width: int, height: int) -> Image.Image | None:
    """Скачивает картинку по URL и подгоняет под нужный размер."""
    try:
        tmp_path = os.path.join(OUTPUT_DIR, f"_dl_{uuid.uuid4().hex[:8]}.jpg")
        urllib.request.urlretrieve(url, tmp_path)
        img = Image.open(tmp_path).convert("RGB").resize((width, height))

        try:
            os.remove(tmp_path)
        except OSError:
            pass

        return img
    except Exception:
        return None


def create_gradient(width: int, height: int, top: tuple, bottom: tuple) -> Image.Image:
    """Вертикальный градиент — без numpy, чистый Pillow."""
    img = Image.new("RGB", (width, height))
    pixels = img.load()

    for y in range(height):
        ratio = y / height
        r = int(top[0] + (bottom[0] - top[0]) * ratio)
        g = int(top[1] + (bottom[1] - top[1]) * ratio)
        b = int(top[2] + (bottom[2] - top[2]) * ratio)
        for x in range(width):
            pixels[x, y] = (r, g, b)

    return img


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


def darken_background(img: Image.Image) -> Image.Image:
    """Затемняет и слегка размывает фон, чтобы текст читался."""
    img = img.filter(ImageFilter.GaussianBlur(radius=3))
    dark = Image.new("RGB", img.size, (0, 0, 0))
    return Image.blend(img, dark, alpha=0.4)


def create_frame(text: str, output_path: str, background_url: str = None, generate_bg: bool = False) -> None:
    """Генерирует кадр 720x1280 с текстом вопроса."""

    img = None

    # Приоритет 1: переданный URL картинки
    if background_url:
        img = download_image(background_url, WIDTH, HEIGHT)

    # Приоритет 2: AI-генерация через Pollinations
    if img is None and generate_bg:
        img = fetch_background(BG_PROMPT, WIDTH, HEIGHT)

    # Приоритет 3: локальный файл assets/background.jpg
    if img is None:
        bg_path = os.path.join(ASSETS_DIR, "background.jpg")
        if os.path.exists(bg_path):
            img = Image.open(bg_path).resize((WIDTH, HEIGHT)).convert("RGB")

    # Приоритет 4: градиент (фолбэк)
    if img is None:
        img = create_gradient(WIDTH, HEIGHT, GRADIENT_TOP, GRADIENT_BOTTOM)

    # Затемняем фон (кроме градиента — он и так тёмный)
    if background_url or generate_bg:
        img = darken_background(img)

    draw = ImageDraw.Draw(img)
    font = load_font(FONT_SIZE)

    max_text_w = WIDTH - TEXT_PADDING * 2
    wrapped = wrap_text(text, font, max_text_w, draw)

    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=20)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (WIDTH - tw) / 2
    ty = (HEIGHT - th) / 2

    # Полупрозрачный бокс за текстом
    box_x1 = int(tx - BOX_PADDING)
    box_y1 = int(ty - BOX_PADDING)
    box_x2 = int(tx + tw + BOX_PADDING)
    box_y2 = int(ty + th + BOX_PADDING)
    box_w = box_x2 - box_x1
    box_h = box_y2 - box_y1

    box_overlay = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    box_draw = ImageDraw.Draw(box_overlay)
    box_draw.rounded_rectangle(
        [0, 0, box_w, box_h],
        radius=BOX_RADIUS,
        fill=BOX_COLOR,
    )

    region = img.crop((box_x1, box_y1, box_x2, box_y2)).convert("RGBA")
    region = Image.alpha_composite(region, box_overlay)
    img.paste(region.convert("RGB"), (box_x1, box_y1))

    del box_overlay, region, box_draw
    gc.collect()

    draw = ImageDraw.Draw(img)

    # Тень текста
    draw.multiline_text(
        (tx + 2, ty + 2), wrapped, font=font,
        fill=(0, 0, 0), align="center", spacing=20,
    )
    # Основной текст
    draw.multiline_text(
        (tx, ty), wrapped, font=font,
        fill=TEXT_COLOR, align="center", spacing=20,
    )

    img.save(output_path, quality=90)

    del img, draw
    gc.collect()


def render_video(frame_path: str, video_path: str, duration: int) -> subprocess.CompletedProcess:
    """Собирает MP4 из кадра + (опционально) музыки через FFmpeg."""

    music_path = os.path.join(ASSETS_DIR, "music.mp3")
    has_music = os.path.exists(music_path)

    cmd = ["ffmpeg", "-y"]

    cmd += ["-loop", "1", "-i", frame_path]
    if has_music:
        cmd += ["-i", music_path]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]

    cmd += [
        "-vf", f"scale={WIDTH}:{HEIGHT}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-t", str(duration),
        "-pix_fmt", "yuv420p",
        "-r", "24",
        "-threads", "1",
    ]

    if has_music:
        cmd += ["-c:a", "aac", "-b:a", "96k", "-shortest"]
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
    Body JSON:
      {
        "text": "Текст вопроса",
        "duration": 15,
        "background_url": "https://...",   (опционально — своя картинка)
        "generate_bg": true                (опционально — AI-генерация фона)
      }
    Response: { "video_url": "https://.../video/<id>.mp4" }
    """
    data = request.get_json(force=True)
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"error": "text is required"}), 400

    duration = int(data.get("duration", DEFAULT_DURATION))
    background_url = data.get("background_url")
    generate_bg = data.get("generate_bg", False)

    vid = str(uuid.uuid4())
    frame_path = os.path.join(OUTPUT_DIR, f"{vid}.jpg")
    video_path = os.path.join(OUTPUT_DIR, f"{vid}.mp4")

    # 1. Создаём кадр
    create_frame(text, frame_path, background_url=background_url, generate_bg=generate_bg)

    # 2. Рендерим видео
    result = render_video(frame_path, video_path, duration)

    try:
        os.remove(frame_path)
    except OSError:
        pass

    if result.returncode != 0:
        return jsonify({"error": "ffmpeg failed", "details": result.stderr}), 500

    base_url = request.host_url.rstrip("/")
    video_url = f"{base_url}/video/{vid}.mp4"

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
