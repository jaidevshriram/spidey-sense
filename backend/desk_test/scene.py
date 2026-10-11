"""Mock "scene camera" views of the desk-test page, for pre-runs without the Neon.

The page (index.html) is drawn as a 1280x720 screen: distractor text on the left, a picture (tests A/B) or the
lake video (tests C/D) on the right. That screen is composited into a 1600x1200 frame (the Neon scene camera's
size) on a monitor standing on a desk. Everything here returns the frame plus the regions in normalized
frame coordinates (0..1), so gaze scripts and ground truth use the same numbers as the engine.

The lake scene mirrors drawLake() in index.html (same geometry and timeline).
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
IMG = HERE / "img"
FRAME_W, FRAME_H = 1600, 1200
SCREEN_W, SCREEN_H = 1280, 720
# where the screen sits in the camera frame (normalized): a monitor ~60 cm in front of the wearer
SCREEN_BOX = (0.10, 0.10, 0.90, 0.10 + 0.80 * (SCREEN_H / SCREEN_W) * (FRAME_W / FRAME_H))

# screen layouts (normalized screen coords), mirrored by index.html's CSS
LAYOUT = {
    "pictures": {"text": (0.03, 0.12, 0.33, 0.92), "panel": (0.64, 0.12, 0.97, 0.92)},
    "video": {"text": (0.03, 0.12, 0.38, 0.92), "panel": (0.48, 0.14, 0.97, 0.86)},
}
PICTURES = ("lion", "tiger", "dog", "car", "house")
# where the animal/object is inside each photo (for "gaze on it"), normalized photo coords
SUBJECT_IN_PHOTO = {"lion": (0.32, 0.58), "tiger": (0.5, 0.45), "dog": (0.5, 0.5), "car": (0.62, 0.62),
                    "house": (0.45, 0.45)}

DISTRACTOR = (
    "Quarterly facilities update. The third-floor printer queue is now shared with the east wing, so "
    "please collect your pages promptly. Desk bookings for next week open on Monday at nine. The kitchen "
    "dishwasher will be serviced on Thursday afternoon; use the second-floor kitchen until it is back. "
    "Remember to log expenses before the end of the month and attach scanned receipts in PDF format. "
    "The fire drill is scheduled for Wednesday at eleven: leave by the nearest stairwell, not the lift, "
    "and gather in the car park by the north gate. Parking permits must be renewed online. Lost property "
    "is kept at reception for two weeks. The meeting rooms on level two now have new screens; the cable "
    "drawer holds every adapter you need. Please keep the quiet zone quiet."
)


def screen_to_frame(box):
    """Normalized screen box -> normalized camera-frame box."""
    sx0, sy0, sx1, sy1 = SCREEN_BOX
    return (sx0 + box[0] * (sx1 - sx0), sy0 + box[1] * (sy1 - sy0),
            sx0 + box[2] * (sx1 - sx0), sy0 + box[3] * (sy1 - sy0))


def _wrap_text(img, box, text, scale=0.62, color=(40, 40, 40)):
    h, w = img.shape[:2]
    x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(img, "Facilities notes", (x0, y0 + 10), font, scale * 1.3, (20, 20, 20), 2, cv2.LINE_AA)
    y, line = y0 + 52, ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if cv2.getTextSize(trial, font, scale, 1)[0][0] > x1 - x0:
            cv2.putText(img, line, (x0, y), font, scale, color, 1, cv2.LINE_AA)
            y, line = y + 27, word
            if y > y1:
                return
        else:
            line = trial
    cv2.putText(img, line, (x0, y), font, scale, color, 1, cv2.LINE_AA)


def _fit(src, w, h):
    """Letterbox src into w x h (black bars), like CSS object-fit: contain. Returns image + placement."""
    sh, sw = src.shape[:2]
    s = min(w / sw, h / sh)
    nw, nh = int(sw * s), int(sh * s)
    out = np.zeros((h, w, 3), np.uint8)
    ox, oy = (w - nw) // 2, (h - nh) // 2
    out[oy:oy + nh, ox:ox + nw] = cv2.resize(src, (nw, nh), interpolation=cv2.INTER_AREA)
    return out, (ox / w, oy / h, (ox + nw) / w, (oy + nh) / h)


# ---- the lake scene (same as drawLake() in index.html) ------------------------------------------
LAKE_PERIOD = 20.0
FIG_H = 0.34          # figure height, fraction of the panel height
FOOT_Y = 0.84


def shore_x(y: float) -> float:
    return 0.70 + 0.025 * math.sin(y * 14.0)


def figure_x(t: float) -> tuple[float, int]:
    """Figure's centre x (panel coords) and facing (+1 right / -1 left) at scene time t."""
    t = t % LAKE_PERIOD
    if t < 10:
        return 0.08 + (0.645 - 0.08) * t / 10, 1          # walks to the water's edge
    if t < 14:
        return 0.645, 1                                    # stands at the edge
    return 0.645 - (0.645 - 0.08) * (t - 14) / 6, -1      # walks back


def lake_truth_gap(t: float, panel_w: float, panel_h: float) -> float:
    """Ground-truth gap between the figure's front and the shore, in figure heights."""
    x, face = figure_x(t)
    front = x + 0.035
    return max(0.0, shore_x(FOOT_Y) - front) * panel_w / (FIG_H * panel_h)


def draw_lake(w: int, h: int, t: float) -> np.ndarray:
    img = np.zeros((h, w, 3), np.uint8)
    hor = int(0.48 * h)
    for y in range(hor):                                   # sky gradient
        k = y / hor
        img[y, :] = (int(235 - 40 * k), int(200 - 20 * k), int(150 - 30 * k))
    hills = np.array([[0, hor], [int(0.2 * w), int(0.38 * h)], [int(0.45 * w), int(0.44 * h)],
                      [int(0.7 * w), int(0.36 * h)], [w, int(0.43 * h)], [w, hor]], np.int32)
    cv2.fillPoly(img, [hills], (90, 130, 95))
    img[hor:, :] = (70, 160, 95)                           # grass
    pts = [(int(shore_x(y / h) * w), y) for y in range(hor, h, 4)] + [(w, h), (w, hor)]
    cv2.fillPoly(img, [np.array(pts, np.int32)], (180, 110, 30))     # lake
    for i in range(14):                                    # ripples
        yy = hor + 12 + (i * 37) % (h - hor - 12)
        xx = int((0.76 + 0.2 * ((i * 0.37 + t * 0.02) % 1)) * w)
        cv2.line(img, (xx, yy), (xx + int(0.05 * w), yy), (215, 160, 90), 2, cv2.LINE_AA)
    # a tree on the left
    cv2.rectangle(img, (int(0.03 * w), int(0.42 * h)), (int(0.045 * w), int(0.62 * h)), (40, 70, 100), -1)
    cv2.circle(img, (int(0.038 * w), int(0.38 * h)), int(0.07 * h), (50, 110, 50), -1)
    # the person
    x, face = figure_x(t)
    fh = FIG_H * h
    cx, foot = x * w, FOOT_Y * h
    hip, shoulder = foot - 0.48 * fh, foot - 0.80 * fh
    walking = (t % LAKE_PERIOD) < 10 or (t % LAKE_PERIOD) >= 14
    sw = math.sin(t * 2 * math.pi * 1.6) * (0.35 if walking else 0.0)
    leg = 0.48 * fh
    for s in (sw, -sw):                                    # legs (jeans)
        fx, fy = cx + math.sin(s) * leg, hip + math.cos(s) * leg
        cv2.line(img, (int(cx), int(hip)), (int(fx), int(fy)), (120, 60, 30), max(3, int(0.06 * fh)), cv2.LINE_AA)
        cv2.ellipse(img, (int(fx + face * 0.03 * fh), int(fy)), (int(0.05 * fh), int(0.02 * fh)), 0, 0, 360,
                    (30, 30, 30), -1)
    body = np.array([[cx - 0.09 * fh, shoulder], [cx + 0.09 * fh, shoulder],
                     [cx + 0.07 * fh, hip], [cx - 0.07 * fh, hip]], np.int32)
    cv2.fillPoly(img, [body], (40, 40, 210))               # red top
    for s in (-sw, sw):                                    # arms
        hx, hy = cx + math.sin(s) * 0.35 * fh, shoulder + math.cos(s) * 0.35 * fh
        cv2.line(img, (int(cx), int(shoulder + 0.03 * fh)), (int(hx), int(hy)), (150, 180, 225),
                 max(2, int(0.04 * fh)), cv2.LINE_AA)
    cv2.circle(img, (int(cx), int(shoulder - 0.11 * fh)), int(0.1 * fh), (150, 180, 225), -1, cv2.LINE_AA)  # head
    cv2.ellipse(img, (int(cx), int(shoulder - 0.15 * fh)), (int(0.1 * fh), int(0.06 * fh)), 0, 180, 360,
                (30, 40, 60), -1)                          # hair
    return img


# ---- page + camera frame ----------------------------------------------------------------------
_photos: dict[str, np.ndarray] = {}


def photo(name: str) -> np.ndarray:
    if name not in _photos:
        _photos[name] = cv2.imread(str(IMG / f"{name}.jpg"))
    return _photos[name]


def render_screen(mode: str, picture: str | None = None, t: float = 0.0):
    """The page as the screen shows it. Returns (img, regions in normalized SCREEN coords)."""
    scr = np.full((SCREEN_H, SCREEN_W, 3), 246, np.uint8)
    cv2.rectangle(scr, (0, 0), (SCREEN_W, 46), (60, 50, 45), -1)
    cv2.putText(scr, "Spidey Sense desk test", (16, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235, 235, 235), 1,
                cv2.LINE_AA)
    lay = LAYOUT[mode]
    _wrap_text(scr, lay["text"], DISTRACTOR)
    px0, py0, px1, py1 = lay["panel"]
    pw, ph = int((px1 - px0) * SCREEN_W), int((py1 - py0) * SCREEN_H)
    regions = {"text": lay["text"], "panel": lay["panel"]}
    if mode == "pictures":
        content, inner = _fit(photo(picture), pw, ph)
        sx, sy = SUBJECT_IN_PHOTO[picture]
        regions["subject"] = (px0 + (inner[0] + sx * (inner[2] - inner[0])) * (px1 - px0),
                              py0 + (inner[1] + sy * (inner[3] - inner[1])) * (py1 - py0))
    else:
        content = draw_lake(pw, ph, t)
        x, _ = figure_x(t)
        regions["subject"] = (px0 + x * (px1 - px0), py0 + (FOOT_Y - FIG_H / 2) * (py1 - py0))
        regions["water"] = (px0 + 0.85 * (px1 - px0), py0 + 0.75 * (py1 - py0))
        regions["truth_gap"] = lake_truth_gap(t, pw, ph)
    scr[int(py0 * SCREEN_H):int(py0 * SCREEN_H) + ph, int(px0 * SCREEN_W):int(px0 * SCREEN_W) + pw] = content
    return scr, regions


_bg: np.ndarray | None = None


def desk_background() -> np.ndarray:
    global _bg
    if _bg is None:
        rng = np.random.default_rng(7)
        bg = np.zeros((FRAME_H, FRAME_W, 3), np.uint8)
        desk_y = int(0.80 * FRAME_H)
        for y in range(desk_y):
            bg[y, :] = (int(200 - 30 * y / desk_y), int(205 - 25 * y / desk_y), int(210 - 20 * y / desk_y))
        bg[desk_y:, :] = (60, 100, 150)                    # wooden desk
        for i in range(40):
            yy = desk_y + int(rng.integers(0, FRAME_H - desk_y))
            cv2.line(bg, (0, yy), (FRAME_W, yy + int(rng.integers(-8, 8))), (50, 88, 135), 1)
        cv2.rectangle(bg, (int(0.25 * FRAME_W), int(0.86 * FRAME_H)), (int(0.70 * FRAME_W), int(0.95 * FRAME_H)),
                      (45, 45, 45), -1)                    # keyboard
        cv2.circle(bg, (int(0.85 * FRAME_W), int(0.9 * FRAME_H)), 55, (230, 230, 235), -1)   # mug
        noise = rng.normal(0, 4, bg.shape)
        _bg = np.clip(bg + noise, 0, 255).astype(np.uint8)
    return _bg


def camera_frame(mode: str, picture: str | None = None, t: float = 0.0):
    """Scene-camera frame (1600x1200 BGR) + regions in normalized FRAME coords."""
    scr, reg = render_screen(mode, picture, t)
    img = desk_background().copy()
    x0, y0, x1, y1 = SCREEN_BOX
    X0, Y0, X1, Y1 = int(x0 * FRAME_W), int(y0 * FRAME_H), int(x1 * FRAME_W), int(y1 * FRAME_H)
    cv2.rectangle(img, (X0 - 14, Y0 - 14), (X1 + 14, Y1 + 14), (25, 25, 25), -1)       # bezel
    cv2.rectangle(img, (int((X0 + X1) / 2) - 40, Y1 + 14), (int((X0 + X1) / 2) + 40, int(0.80 * FRAME_H)),
                  (40, 40, 40), -1)                                                     # stand
    img[Y0:Y1, X0:X1] = cv2.resize(scr, (X1 - X0, Y1 - Y0), interpolation=cv2.INTER_AREA)
    out = {}
    for k, v in reg.items():
        if k == "truth_gap":
            out[k] = v
        elif len(v) == 4:
            out[k] = screen_to_frame(v)
        else:
            out[k] = (x0 + v[0] * (x1 - x0), y0 + v[1] * (y1 - y0))
    return img, out


def center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def make_media(out: Path | None = None) -> Path:
    """Stills of every desk picture + the 20 s lake clip as the scene camera sees them (for config.desk.sim.yaml)."""
    out = out or HERE.parents[1] / "logs" / "desk_sim"
    out.mkdir(parents=True, exist_ok=True)
    for name in PICTURES:
        cv2.imwrite(str(out / f"desk_{name}.jpg"), camera_frame("pictures", name)[0], [cv2.IMWRITE_JPEG_QUALITY, 92])
    clip = out / "lake.mp4"
    if not clip.exists():
        fps = 15
        vw = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), fps, (FRAME_W, FRAME_H))
        for i in range(int(LAKE_PERIOD * fps)):
            vw.write(camera_frame("video", t=i / fps)[0])
        vw.release()
    return out


if __name__ == "__main__":     # writes logs/desk_sim/desk_<picture>.jpg + lake.mp4 (+ previews)
    out = make_media()
    for t in (0, 6, 11):
        cv2.imwrite(str(out / f"preview_lake_{t}.jpg"), camera_frame("video", t=t)[0])
    print(out)
