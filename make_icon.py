from PIL import Image, ImageDraw

size = 256
image = Image.new("RGBA", (size, size), (23, 32, 51, 255))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((28, 28, 228, 228), radius=42, fill=(23, 105, 170, 255))
draw.ellipse((62, 64, 194, 196), fill=(244, 247, 251, 255))
draw.polygon([(78, 126), (108, 98), (126, 116), (158, 82), (184, 108), (154, 140), (126, 118), (104, 148)], fill=(23, 105, 170, 255))
image.save("file-yeti.ico", sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
