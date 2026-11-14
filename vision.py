import logging
import os

try:
    import cv2
except ImportError:  # pragma: no cover - optional dependency
    cv2 = None

from utils import bundle_dir, configure_runtime_env, parse_loot_number

logger = logging.getLogger(__name__)

# Characters EasyOCR is allowed to return when reading a resource value.
# Letters are included so an abbreviated value like "1.2M" is not silently
# truncated to "12" by a digits-only allowlist.
_OCR_ALLOWLIST = "0123456789.,KMBkmb"


def configure_easyocr_paths():
    """
    Point EasyOCR at the weights shipped inside the bundle.

    EasyOCR downloads ``craft_mlt_25k.pth`` and ``english_g2.pth`` into
    ``~/.EasyOCR/model`` the first time it runs, which needs internet access
    and stalls for minutes. The packaged build carries the same two files, so
    we set ``EASYOCR_MODULE_PATH`` before easyocr is imported (the path is
    read at import time) and it picks them up instead of downloading.

    Must be called *before* ``import easyocr``. Does nothing when the
    weights are absent, which lets EasyOCR fall back to its normal download.
    """
    if os.environ.get("EASYOCR_MODULE_PATH"):
        return

    data_dir = os.path.join(bundle_dir(), "easyocr_data")
    if not os.path.isdir(os.path.join(data_dir, "model")):
        return

    os.environ["EASYOCR_MODULE_PATH"] = data_dir
    logger.debug(f"EasyOCR weights directory set to {data_dir}")


class VisionManager:
    """
    Manages YOLO inference and EasyOCR reading.
    Removes globals: model, reader
    Pure inference only — no retry logic, no window handling.
    """

    def __init__(self, config):
        """
        Initialize with config. Models are NOT loaded until load() is called.
        
        Args:
            config: AppConfig with model_path, gpu
        """
        self.config = config
        self.model = None
        self.reader = None
    
    def load(self):
        """
        Lazy-load YOLO model and EasyOCR reader.
        Call this before first detect() or read_number() call.
        """
        if self.model is None:
            try:
                configure_runtime_env()
                from ultralytics import YOLO

                self.model = YOLO(self.config.model_path)
            except Exception as e:
                raise RuntimeError(f"Failed to load YOLO model from '{self.config.model_path}': {e}")

        if self.reader is None:
            try:
                import torch

                configure_easyocr_paths()
                import easyocr

                use_gpu = torch.cuda.is_available() if self.config.gpu is None else self.config.gpu
                self.reader = easyocr.Reader(['en'], gpu=use_gpu)
            except Exception as e:
                raise RuntimeError(f"Failed to load EasyOCR reader: {e}")
    
    def detect(self, frame, class_names, conf=None):
        """
        Run YOLO inference on frame, return center-point detections.

        Args:
            frame: np.ndarray, BGR format (OpenCV compatible)
            class_names: list of class name strings to filter for (e.g., ['gold', 'elixir'])
            conf: confidence threshold. If None, uses config.conf

        Returns:
            dict: {class_name: [(cx, cy), (cx, cy), ...]}
                  with center coordinates relative to frame origin (0,0)
                  (NOT window origin)
        """
        details = self.detect_with_details(frame, class_names, conf)
        return {
            cls: [item["center"] for item in items]
            for cls, items in details.items()
        }

    def detect_with_details(self, frame, class_names, conf=None):
        """
        Run YOLO inference on frame, returning centers and bounding boxes.

        Args:
            frame: np.ndarray, BGR format
            class_names: list of class name strings to filter for
            conf: confidence threshold. If None, uses config.conf

        Returns:
            dict: {class_name: [{"center": (cx, cy), "bbox": (x1, y1, x2, y2)}, ...]}
        """
        if self.model is None:
            self.load()

        if conf is None:
            conf = self.config.conf

        result = self.model.predict(source=frame, conf=conf, verbose=False)
        detections = {}

        for box in result[0].boxes:
            class_id = int(box.cls.item())
            class_name = str(self.model.names[class_id])

            if class_name in class_names:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                cx = x1 + (x2 - x1) / 2
                cy = y1 + (y2 - y1) / 2

                if class_name not in detections:
                    detections[class_name] = []
                detections[class_name].append({
                    "center": (cx, cy),
                    "bbox": (x1, y1, x2, y2)
                })

        return detections

    def _preprocess(self, region):
        """
        Prepare a small resource-value crop for OCR: upscale short crops so the
        glyphs are large enough for the recognizer, then convert to grayscale.
        """
        h, w = region.shape[:2]
        if h and h < 64:
            scale = 64.0 / h
            region = cv2.resize(
                region,
                (max(1, int(w * scale)), 64),
                interpolation=cv2.INTER_CUBIC,
            )
        return cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)

    def read_text(self, frame, min_conf: float = 0.35):
        """Every readable line on the frame as (text, (x1, y1, x2, y2), conf).

        Used where the layout cannot be assumed -- an upgrade card, a confirm
        dialog -- so the caller matches on words and taps the box it found,
        instead of relying on a fixed button position that changes with the
        shop UI every few months.
        """
        if self.reader is None:
            self.load()
        if frame is None or getattr(frame, "size", 0) == 0:
            return []

        try:
            results = self.reader.readtext(frame, detail=1, paragraph=False)
        except Exception as e:  # noqa: BLE001 - OCR must never kill a task
            logger.warning(f"read_text failed: {e}")
            return []

        out = []
        for poly, text, conf in results:
            if conf < min_conf or not str(text).strip():
                continue
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            out.append((
                str(text),
                (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))),
                float(conf),
            ))
        return out

    def read_number(self, frame, bbox):
        """
        OCR a region of the frame to extract a number.

        Args:
            frame: np.ndarray, BGR format
            bbox: tuple (x1, y1, x2, y2) - region to OCR

        Returns:
            str: the value as a plain integer string (separators removed,
            abbreviations expanded), or "0" if nothing readable.
        """
        if self.reader is None:
            self.load()

        x1, y1, x2, y2 = bbox
        region = frame[y1:y2, x1:x2]

        if region.size == 0:
            return "0"

        ocr_result = self.reader.readtext(
            self._preprocess(region), detail=0, allowlist=_OCR_ALLOWLIST
        )

        # EasyOCR can split a value into several fragments; keep them all.
        raw = " ".join(str(part) for part in ocr_result) if ocr_result else ""
        value = parse_loot_number(raw)
        logger.debug(f"OCR bbox={(x1, y1, x2, y2)} raw={raw!r} -> {value}")
        return value
