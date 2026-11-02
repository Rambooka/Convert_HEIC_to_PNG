"""Generates app.ico (multi-size) and app.png for HEIC to PNG Converter."""
from PIL import Image, ImageDraw, ImageFilter

S = 1024  # draw large, downsample for smooth edges


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def make():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Rounded-square background with diagonal gradient
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    top, bot = (32, 156, 238), (98, 54, 214)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=lerp(top, bot, y / S) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([40, 40, S - 40, S - 40], radius=210, fill=255)
    img.paste(grad, (0, 0), mask)

    # Soft shadow under the "photo"
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([200, 230, 840, 760], radius=70, fill=(0, 0, 0, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(28))
    img.alpha_composite(shadow, (0, 18))

    # White photo card
    d = ImageDraw.Draw(img)
    card = [190, 210, 834, 740]
    d.rounded_rectangle(card, radius=70, fill=(255, 255, 255, 255))

    # Scene inside the card
    scene = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scene)
    inner = [250, 270, 774, 680]
    sky_m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(sky_m).rounded_rectangle(inner, radius=40, fill=255)
    sky = Image.new("RGBA", (S, S))
    kd = ImageDraw.Draw(sky)
    for y in range(inner[1], inner[3]):
        t = (y - inner[1]) / (inner[3] - inner[1])
        kd.line([(0, y), (S, y)], fill=lerp((170, 222, 255), (230, 245, 255), t) + (255,))
    scene.paste(sky, (0, 0), sky_m)
    sd.ellipse([620, 315, 715, 410], fill=(255, 196, 40, 255))                 # sun
    sd.polygon([(250, 680), (430, 430), (610, 680)], fill=(46, 160, 90, 255))  # hill 1
    sd.polygon([(470, 680), (640, 500), (774, 640), (774, 680)], fill=(30, 120, 70, 255))  # hill 2
    clip = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    clip.paste(scene, (0, 0), sky_m)
    img.alpha_composite(clip)

    # Orange "convert" badge with arrow, bottom-right
    bx, by, br = 760, 760, 175
    d.ellipse([bx - br - 14, by - br - 14, bx + br + 14, by + br + 14], fill=(255, 255, 255, 255))
    d.ellipse([bx - br, by - br, bx + br, by + br], fill=(255, 122, 26, 255))
    w = 34
    d.line([(bx - 90, by), (bx + 70, by)], fill="white", width=w * 2)
    d.polygon([(bx + 105, by), (bx + 20, by - 85), (bx + 20, by + 85)], fill="white")
    d.ellipse([bx - 90 - w, by - w, bx - 90 + w, by + w], fill="white")

    return img


if __name__ == "__main__":
    big = make()
    big.resize((256, 256), Image.LANCZOS).save("app.png")
    sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]
    big.resize((256, 256), Image.LANCZOS).save("app.ico", sizes=sizes)
    print("wrote app.ico, app.png")
