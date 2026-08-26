import json
import numpy as np
import cv2

from services.intake.qr import detect_qr, parse_qr_payload


def _make_qr_image(payload: str, target_size=200):
    """Scales the QR by an integer multiple of its native module grid so
    resizing never breaks module alignment, regardless of payload length."""
    encoder = cv2.QRCodeEncoder_create()
    qr = encoder.encode(payload)
    native = qr.shape[0]
    scale = max(4, target_size // native)
    qr_scaled = cv2.resize(qr, (native * scale, native * scale), interpolation=cv2.INTER_NEAREST)
    border = scale * 4
    bordered = cv2.copyMakeBorder(qr_scaled, border, border, border, border, cv2.BORDER_CONSTANT, value=255)
    canvas_size = bordered.shape[0] + 100
    canvas = np.ones((canvas_size, canvas_size, 3), dtype=np.uint8) * 255
    off = 50
    canvas[off:off + bordered.shape[0], off:off + bordered.shape[1]] = cv2.cvtColor(bordered, cv2.COLOR_GRAY2BGR)
    return canvas