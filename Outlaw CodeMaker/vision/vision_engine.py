"""Vision Engine — screen capture, OpenCV preprocessing, OCR via Tesseract.

Capture model:
    Multi-monitor capture follows the *Godot window*. Callers can either:
      - call capture_region(x,y,w,h) directly with absolute screen coords, OR
      - call capture_monitor_for_rect(rect) which picks the mss monitor that
        contains the rect's center and grabs the whole monitor.

Tesseract resolution order:
    1. Bundled binary under tools/tesseract/tesseract.exe
    2. config["vision"]["tesseract_cmd"] (typically the same bundled path)
    3. System PATH (whatever pytesseract finds on its own)
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
from mss import mss
from PIL import Image


logger = logging.getLogger(__name__)


# Bundled OCR binary lives under tools/tesseract (installed by tools/setup_tesseract.py)
BUNDLED_TESSERACT = Path(__file__).resolve().parent.parent / "tools" / "tesseract" / "tesseract.exe"


class VisionEngine:
    def __init__(self, vision_config: dict):
        self.config = vision_config
        self._tesseract_ready = self._init_tesseract()

    def _init_tesseract(self) -> bool:
        try:
            import pytesseract  # noqa: WPS433

            candidates: list[Path] = [BUNDLED_TESSERACT]
            cfg_path = self.config.get("tesseract_cmd")
            if cfg_path:
                candidates.append(Path(cfg_path))

            for p in candidates:
                if p.exists():
                    pytesseract.pytesseract.tesseract_cmd = str(p)
                    logger.info("Tesseract resolved to %s", p)
                    return True

            # Fall back to PATH — let pytesseract try; .get_tesseract_version() raises if missing.
            try:
                pytesseract.get_tesseract_version()
                logger.info("Tesseract found on PATH")
                return True
            except Exception:
                logger.warning(
                    "Tesseract not bundled or installed. "
                    "Run: python tools/setup_tesseract.py"
                )
                return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tesseract init error: %s", exc)
            return False

    # ----- capture --------------------------------------------------------

    def capture_full_screen(self, save_path: str | None = None) -> np.ndarray:
        with mss() as sct:
            raw = sct.grab(sct.monitors[1])
            img = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
        if save_path:
            cv2.imwrite(save_path, img)
        return img

    def capture_region(self, x: int, y: int, w: int, h: int, save_path: str | None = None) -> np.ndarray:
        with mss() as sct:
            raw = sct.grab({"left": x, "top": y, "width": max(1, w), "height": max(1, h)})
            img = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
        if save_path:
            cv2.imwrite(save_path, img)
        return img

    def capture_monitor_for_rect(
        self, rect: tuple[int, int, int, int], save_path: str | None = None
    ) -> np.ndarray:
        """Grab the entire monitor that contains the *center* of rect (x,y,w,h)."""
        x, y, w, h = rect
        cx, cy = x + w // 2, y + h // 2
        with mss() as sct:
            # sct.monitors[0] is the virtual desktop union; [1:] are physical monitors
            monitors = sct.monitors[1:] if len(sct.monitors) > 1 else sct.monitors
            target = None
            for m in monitors:
                left, top = m["left"], m["top"]
                right, bottom = left + m["width"], top + m["height"]
                if left <= cx < right and top <= cy < bottom:
                    target = m
                    break
            if target is None:
                target = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            raw = sct.grab(target)
            img = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
        if save_path:
            cv2.imwrite(save_path, img)
        return img

    def list_monitors(self) -> list[dict]:
        with mss() as sct:
            return [dict(m) for m in sct.monitors]

    # ----- OCR ------------------------------------------------------------

    def ocr(self, image: np.ndarray, *, preprocess: bool = True) -> str:
        if not self._tesseract_ready:
            return (
                "ERROR: tesseract unavailable. "
                "Run `python tools/setup_tesseract.py` to install the bundled copy."
            )
        import pytesseract  # noqa: WPS433
        if preprocess:
            image = self._prep_for_ocr(image)
        pil = Image.fromarray(image)
        return pytesseract.image_to_string(pil)

    def _prep_for_ocr(self, image: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.bilateralFilter(gray, 9, 75, 75)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return thresh

    # ----- helpers --------------------------------------------------------

    def template_match(
        self, haystack: np.ndarray, needle_path: str, threshold: float = 0.85
    ) -> list[tuple[int, int]]:
        needle = cv2.imread(needle_path, cv2.IMREAD_COLOR)
        if needle is None:
            return []
        result = cv2.matchTemplate(haystack, needle, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= threshold)
        return list(zip(xs.tolist(), ys.tolist()))

    def detect_red_error_text(self, image: np.ndarray) -> bool:
        """Heuristic: Godot prints script errors in red — flag if there's a meaningful red blob."""
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        lower = np.array([0, 120, 70])
        upper = np.array([10, 255, 255])
        mask = cv2.inRange(hsv, lower, upper)
        return int(mask.sum()) > 5000
