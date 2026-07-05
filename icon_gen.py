"""Generate icon.ico with multiple resolutions for the opencode tray app."""
import sys
import os
from PIL import Image, ImageDraw


def render(px):
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    k = px / 300.0
    body = (33, 30, 30, 255)
    accent = (207, 206, 205, 255)
    draw.rectangle([0, 0, 240 * k, 60 * k], fill=body)
    draw.rectangle([0, 240 * k, 240 * k, 300 * k], fill=body)
    draw.rectangle([0, 60 * k, 60 * k, 240 * k], fill=body)
    draw.rectangle([180 * k, 60 * k, 240 * k, 240 * k], fill=body)
    draw.rectangle([60 * k, 120 * k, 180 * k, 240 * k], fill=accent)
    return img


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "icon.ico"
    sizes = [256, 128, 64, 48, 32, 16]
    frames = [render(s) for s in sizes]
    # ICO requires saving the first image, then appending the rest
    frames[0].save(out, format="ICO", sizes=[(s, s) for s in sizes],
                   append_images=frames[1:])

    print(f"icon.ico: {len(sizes)} frames, saved to {os.path.abspath(out)}")


if __name__ == "__main__":
    main()
