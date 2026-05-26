from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from PIL import Image

from abr_import.ocr.pdf_reader import PageImage

logger = logging.getLogger(__name__)


class OcrNotAvailableError(RuntimeError):
    """Paddle / PaddleOCR не установлены или не импортируются."""


def verify_paddle_stack(use_gpu: bool = True) -> str:
    """Проверка при старте воркера. Возвращает версию paddle или бросает OcrNotAvailableError."""
    try:
        import paddle
    except ImportError as exc:
        raise OcrNotAvailableError(
            "Модуль 'paddle' не найден. Пересоберите образ: docker compose build --no-cache worker. "
            "Для CPU: docker compose --profile cpu build worker-cpu"
        ) from exc

    version = getattr(paddle, "__version__", "?")
    if use_gpu and paddle.device.is_compiled_with_cuda():
        try:
            paddle.device.set_device("gpu:0")
        except Exception as exc:
            logger.warning("GPU недоступен, будет CPU: %s", exc)
    return version


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    x_min: float
    x_max: float
    y_center: float


@dataclass(frozen=True)
class OcrPage:
    page_number: int
    lines: list[OcrLine]
    full_text: str


class OcrEngine:
    """PaddleOCR с ограничением batch для GPU <= 32 ГБ."""

    def __init__(self, *, lang: str = "ru", use_gpu: bool = True):
        self._lang = lang
        self._use_gpu = use_gpu
        self._ocr = None

    def _ensure_loaded(self) -> None:
        if self._ocr is not None:
            return

        verify_paddle_stack(self._use_gpu)

        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OcrNotAvailableError(
                "PaddleOCR не установлен. Пересоберите Docker-образ worker."
            ) from exc

        kwargs: dict = {
            "lang": self._lang,
            "use_angle_cls": True,
        }
        if self._use_gpu:
            kwargs["use_gpu"] = True

        try:
            self._ocr = PaddleOCR(**kwargs)
        except TypeError:
            kwargs.pop("use_gpu", None)
            self._ocr = PaddleOCR(**kwargs)

        logger.info("PaddleOCR загружен (lang=%s, gpu=%s)", self._lang, self._use_gpu)

    def recognize_page(self, page: PageImage) -> OcrPage:
        self._ensure_loaded()
        img = Image.frombytes("RGB", (page.width, page.height), page.rgb_bytes)
        arr = np.array(img)

        result = self._ocr.ocr(arr, cls=True)
        lines: list[OcrLine] = []

        if result and result[0]:
            for block in result[0]:
                box, (text, conf) = block
                text = (text or "").strip()
                if not text:
                    continue
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                lines.append(
                    OcrLine(
                        text=text,
                        confidence=float(conf),
                        x_min=min(xs),
                        x_max=max(xs),
                        y_center=sum(ys) / len(ys),
                    )
                )

        lines.sort(key=lambda ln: (ln.y_center, ln.x_min))
        full_text = "\n".join(ln.text for ln in lines)
        return OcrPage(page_number=page.page_number, lines=lines, full_text=full_text)

    def recognize_pages(self, pages: list[PageImage]) -> list[OcrPage]:
        return [self.recognize_page(p) for p in pages]
