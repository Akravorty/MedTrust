"""
services/intake/image_quality.py

Lightweight, deterministic image-quality assessment run before OCR.
This is NOT a medically validated CV model — it is an input-quality
safeguard using basic statistics: dimensions, brightness, contrast,
and blur (variance of Laplacian).

Never fabricates OCR when quality is inadequate; the caller (service.py)
decides whether to still attempt extraction or go straight to
MANUAL_REVIEW based on the flags returned here.
"""

from __future__ import annotations

import numpy as np
import cv2

# Thresholds are engineering heuristics tuned for a hackathon demo,
# not clinically/medically validated values.
BLUR_VARIANCE_THRESHOLD = 60.0       # below this, Laplacian variance suggests heavy blur
DARK_MEAN_THRESHOLD = 40.0           # 0-255 grayscale mean
BRIGHT_MEAN_THRESHOLD = 230.0
LOW_CONTRAST_STD_THRESHOLD = 15.0    # grayscale stddev
MIN_USABLE_DIMENSION = 120


def assess_image_quality(img: np.ndarray) -> dict:
    """
    Returns a structured result:
    {
        "usable": bool,
        "blur_score": float,
        "brightness_score": float,   # 0.0-1.0 normalized mean
        "contrast_score": float,     # raw stddev
        "quality_flags": [str, ...],
    }
    """
    flags: list[str] = []

    h, w = img.shape[:2]
    if h < MIN_USABLE_DIMENSION or w < MIN_USABLE_DIMENSION:
        flags.append("image_too_small")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mean_brightness = float(np.mean(gray))
    contrast_std = float(np.std(gray))
    blur_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    if mean_brightness < DARK_MEAN_THRESHOLD:
        flags.append("image_too_dark")
    if mean_brightness > BRIGHT_MEAN_THRESHOLD:
        flags.append("image_too_bright")
    if contrast_std < LOW_CONTRAST_STD_THRESHOLD:
        flags.append("low_contrast")
    if blur_variance < BLUR_VARIANCE_THRESHOLD:
        flags.append("excessive_blur")

    # A near-blank image shows up as both low contrast AND either very
    # dark or very bright — call it out explicitly since it's the
    # clearest "there is nothing here" signal.
    if contrast_std < 5.0 and (mean_brightness < 10.0 or mean_brightness > 245.0):
        flags.append("blank_or_empty_image")

    usable = "image_too_small" not in flags and "blank_or_empty_image" not in flags

    return {
        "usable": usable,
        "blur_score": round(blur_variance, 2),
        "brightness_score": round(mean_brightness / 255.0, 4),
        "contrast_score": round(contrast_std, 2),
        "quality_flags": flags,
    }
