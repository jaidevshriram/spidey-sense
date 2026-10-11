"""Scene-camera frames that look like the WORLD, not a monitor (so Haiku judges them as real).

Right ~62% of the view: the test picture (or the synthetic lake), full-bleed, slight perspective, blur, noise and
exposure. Left: a street sign (the "look away" target for unseen tests). No screen, desk or bezel.
    python3 tools/world_scene.py      -> logs/world_sim/world_<pic>.jpg + lake_world.mp4
Gaze targets (normalized): picture ~ (0.69, 0.50), sign ~ (0.17, 0.42).
"""
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from desk_test import scene  # noqa: E402  (photos + synthetic lake)

W, H = 1600, 1200
OUT = ROOT / "logs" / "world_sim"
PIC_X0 = 0.38
rng = np.random.default_rng(7)


def sign_layer(img):
    # pole + green street sign + a small notice: something real-world to look at instead of the subject
    cv2.rectangle(img, (int(0.165 * W), int(0.30 * H)), (int(0.18 * W), H), (90, 90, 95), -1)
    cv2.rectangle(img, (int(0.04 * W), int(0.22 * H)), (int(0.31 * W), int(0.36 * H)), (40, 110, 30), -1)
    cv2.rectangle(img, (int(0.045 * W), int(0.226 * H)), (int(0.305 * W), int(0.354 * H)), (235, 235, 235), 4)
    cv2.putText(img, "PARK RD", (int(0.07 * W), int(0.315 * H)), cv2.FONT_HERSHEY_DUPLEX, 2.6, (240, 240, 240), 6)
    cv2.rectangle(img, (int(0.06 * W), int(0.42 * H)), (int(0.29 * W), int(0.62 * H)), (225, 215, 190), -1)
    for i, t in enumerate(["NOTICE", "Gates close at 8 pm", "No cycling on paths", "Dogs on leads"]):
        cv2.putText(img, t, (int(0.075 * W), int((0.47 + 0.045 * i) * H)), cv2.FONT_HERSHEY_SIMPLEX,
                    1.05 if i else 1.3, (40, 40, 40), 2 if i else 3)


def background():
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (205, 190, 170)                                    # overcast sky / pale wall
    img[int(0.70 * H):] = (90, 120, 95)                         # ground
    return img


def camera(img):
    # mild camera realism: slight perspective, blur, sensor noise, exposure falloff (vignette)
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    dst = np.float32([[8, 10], [W - 14, 0], [W - 4, H - 6], [0, H - 12]])
    img = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (W, H), borderMode=cv2.BORDER_REPLICATE)
    img = cv2.GaussianBlur(img, (3, 3), 0.9)
    yy, xx = np.mgrid[0:H, 0:W]
    vig = 1 - 0.18 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    img = np.clip(img.astype(np.float32) * vig[..., None] + rng.normal(0, 4, img.shape), 0, 255)
    return img.astype(np.uint8)


def frame(content):
    img = background()
    x0 = int(PIC_X0 * W)
    img[:, x0:] = cv2.resize(content, (W - x0, H), interpolation=cv2.INTER_AREA)
    sign_layer(img)
    return camera(img)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for p in scene.PICTURES:
        cv2.imwrite(str(OUT / f"world_{p}.jpg"), frame(scene.photo(p)), [cv2.IMWRITE_JPEG_QUALITY, 90])
    fps, secs = 15, scene.LAKE_PERIOD
    vw = cv2.VideoWriter(str(OUT / "lake_world.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    for i in range(int(fps * secs)):
        vw.write(frame(scene.draw_lake(W - int(PIC_X0 * W), H, i / fps)))
    vw.release()
    print("wrote", OUT)


if __name__ == "__main__":
    main()
