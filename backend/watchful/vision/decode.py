"""Pictures people send (staff photos, a zone's picture, a device's frames) are checked before they are decoded: the
width and height come from the file's own header, and a picture with more pixels than its cap, or in a format not
read here, is refused. A tiny file can declare a huge picture (a 109 KB PNG of 30000 by 30000 pixels decodes to
2.7 GB), and OpenCV allocates for what the header says before reading a pixel. OPENCV_IO_MAX_IMAGE_PIXELS in the
service units is the second guard."""
import struct

import cv2
import numpy as np

PNG = b"\x89PNG\r\n\x1a\n"
SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}  # frame headers, not C4, C8, CC


class TooManyPixels(ValueError):
    pass


def _jpeg(data: bytes) -> tuple[int, int] | None:
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:  # markers with no length
            i += 2
            continue
        if marker in SOF:
            if i + 9 > len(data):
                return None
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        if marker in (0xD9, 0xDA):  # end, or a scan before any frame header
            return None
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def _webp(data: bytes) -> tuple[int, int] | None:
    kind = data[12:16]
    if kind == b"VP8 " and len(data) >= 30:
        return struct.unpack("<H", data[26:28])[0] & 0x3FFF, struct.unpack("<H", data[28:30])[0] & 0x3FFF
    if kind == b"VP8L" and len(data) >= 25:
        b = data[21:25]
        return 1 + (((b[1] & 0x3F) << 8) | b[0]), 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
    if kind == b"VP8X" and len(data) >= 30:
        return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    return None


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a JPEG, PNG, WebP, GIF or BMP header, or None for anything else or a broken header."""
    try:
        if data[:2] == b"\xff\xd8":
            return _jpeg(data)
        if data[:8] == PNG and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return _webp(data)
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        if data[:2] == b"BM":
            width, height = struct.unpack("<ii", data[18:26])
            return abs(width), abs(height)
    except struct.error:
        return None
    return None


def decode_image(data: bytes | None, max_pixels: int, flags: int = cv2.IMREAD_COLOR) -> np.ndarray | None:
    """The picture, or None when it can't be read (or is in a format not read here). Raises TooManyPixels, before
    decoding anything, when its header says it has more than max_pixels."""
    if not data:
        return None
    size = image_size(data)
    if size is None or min(size) <= 0:
        return None
    if size[0] * size[1] > max_pixels:
        raise TooManyPixels(f"{size[0]} by {size[1]} pixels is more than {max_pixels}")
    return cv2.imdecode(np.frombuffer(data, np.uint8), flags)
