"""
OpenCV-based interactive frame previewer.

Provides single-frame navigation and a sampled montage grid.
"""

from pathlib import Path
from typing import Optional

import logging

logger = logging.getLogger("video2frames.preview")


def _ensure_cv2():
    try:
        import cv2
        return cv2
    except ImportError:
        raise ImportError(
            "opencv-python is required for preview.  pip install opencv-python"
        )


class FramePreview:
    """Browse or montage extracted frames using OpenCV highgui."""

    WINDOW = "Video2Frames Preview"

    # ── interactive viewer ────────────────────────────────────────────────
    def preview_frames(
        self,
        frame_dir: Path,
        fmt: str = "png",
        start: int = 1,
        fps: float = 30.0,
    ) -> None:
        """
        Open an interactive window.

        **Controls**

        | Key              | Action                |
        |------------------|-----------------------|
        | → / D            | Next frame            |
        | ← / A            | Previous frame        |
        | Space             | Play / Pause          |
        | ``+`` / ``-``    | Faster / Slower       |
        | Q / Esc          | Quit                  |
        """
        cv2 = _ensure_cv2()

        frames = sorted(Path(frame_dir).glob(f"frame_*.{fmt}"))
        if not frames:
            print(f"No .{fmt} frames found in {frame_dir}")
            return

        total = len(frames)
        idx = max(0, min(start - 1, total - 1))
        playing = False
        delay = max(1, int(1000 / fps))

        cv2.namedWindow(self.WINDOW, cv2.WINDOW_NORMAL)

        while True:
            img = cv2.imread(str(frames[idx]))
            if img is None:
                idx = (idx + 1) % total
                continue

            h, w = img.shape[:2]
            label = f"Frame {idx + 1}/{total}  |  {w}x{h}"
            if playing:
                label += "  |  PLAYING"
            cv2.rectangle(img, (0, 0), (len(label) * 11 + 10, 32), (0, 0, 0), -1)
            cv2.putText(
                img, label, (6, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1,
            )
            cv2.imshow(self.WINDOW, img)

            key = cv2.waitKey(delay if playing else 0) & 0xFF

            if key in (ord("q"), 27):
                break
            elif key in (ord("d"), 83, 3):          # right
                idx = (idx + 1) % total
            elif key in (ord("a"), 81, 2):           # left
                idx = (idx - 1) % total
            elif key == 32:                          # space
                playing = not playing
            elif key in (ord("+"), ord("=")):
                delay = max(1, delay - 5)
            elif key == ord("-"):
                delay = min(2000, delay + 5)
            elif playing:
                idx = (idx + 1) % total

        cv2.destroyAllWindows()

    # ── montage ───────────────────────────────────────────────────────────
    def show_montage(
        self,
        frame_dir: Path,
        fmt: str = "png",
        cols: int = 5,
        rows: int = 4,
        thumb_w: int = 320,
    ) -> None:
        """Display a grid of evenly-sampled thumbnails."""
        cv2 = _ensure_cv2()
        import numpy as np

        frames = sorted(Path(frame_dir).glob(f"frame_*.{fmt}"))
        if not frames:
            print(f"No .{fmt} frames in {frame_dir}")
            return

        total = len(frames)
        n = cols * rows
        step = max(1, total // n)
        selected = frames[::step][:n]

        thumbs = []
        th: Optional[int] = None
        for fp in selected:
            img = cv2.imread(str(fp))
            if img is None:
                continue
            h, w = img.shape[:2]
            scale = thumb_w / w
            if th is None:
                th = int(h * scale)
            thumbs.append(cv2.resize(img, (thumb_w, th)))

        if not thumbs or th is None:
            print("Could not load frames.")
            return

        blank = np.zeros((th, thumb_w, 3), dtype=np.uint8)
        while len(thumbs) < n:
            thumbs.append(blank.copy())

        row_imgs = [
            np.hstack(thumbs[r * cols : (r + 1) * cols]) for r in range(rows)
        ]
        montage = np.vstack(row_imgs)

        cv2.namedWindow("Frame Montage", cv2.WINDOW_NORMAL)
        cv2.imshow("Frame Montage", montage)
        print("Press any key to close the montage …")
        cv2.waitKey(0)
        cv2.destroyAllWindows()