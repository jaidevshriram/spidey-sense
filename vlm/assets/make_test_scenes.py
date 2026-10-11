"""Draws simple synthetic test scenes for FakeSource (no downloads needed)."""
from pathlib import Path

import cv2
import numpy as np

OUT = Path(__file__).parent
W, H = 1280, 720


def street():
    img = np.zeros((H, W, 3), np.uint8)
    img[:330] = (235, 206, 135)                                  # sky
    img[330:] = (90, 90, 90)                                     # asphalt
    cv2.fillPoly(img, [np.array([[0, 720], [560, 330], [720, 330], [1280, 720]])], (70, 70, 70))
    for y in range(360, 720, 60):                                # centre dashes
        cv2.rectangle(img, (636, y), (644, y + 30), (240, 240, 240), -1)
    cv2.rectangle(img, (0, 200), (300, 330), (60, 80, 150), -1)  # buildings
    cv2.rectangle(img, (1000, 150), (1280, 330), (80, 110, 160), -1)
    # oncoming car, large and close on the left
    cv2.rectangle(img, (120, 380), (560, 600), (30, 30, 200), -1)
    cv2.rectangle(img, (190, 300), (490, 390), (30, 30, 180), -1)
    cv2.rectangle(img, (215, 315), (465, 380), (230, 200, 160), -1)  # windscreen
    for x in (180, 500):
        cv2.circle(img, (x, 610), 45, (20, 20, 20), -1)
    for x in (170, 510):
        cv2.circle(img, (x, 450), 28, (200, 255, 255), -1)       # headlights
    cv2.rectangle(img, (270, 520), (410, 560), (200, 200, 200), -1)  # plate
    return img


def hallway():
    img = np.full((H, W, 3), (200, 210, 215), np.uint8)
    cv2.fillPoly(img, [np.array([[0, 720], [480, 420], [800, 420], [1280, 720]])], (150, 160, 170))
    cv2.fillPoly(img, [np.array([[0, 0], [480, 200], [800, 200], [1280, 0]])], (225, 230, 235))
    cv2.rectangle(img, (480, 200), (800, 420), (180, 190, 195), -1)
    cv2.rectangle(img, (590, 260), (690, 420), (90, 120, 150), -1)   # far door
    for x in (100, 1100):
        cv2.rectangle(img, (x, 250), (x + 80, 560), (100, 130, 160), -1)
    return img


def kitchen_knife():
    img = np.full((H, W, 3), (190, 205, 220), np.uint8)
    img[420:] = (120, 150, 185)                                  # counter
    cv2.rectangle(img, (0, 400), (W, 425), (90, 110, 140), -1)
    cv2.rectangle(img, (100, 150), (400, 380), (210, 210, 200), -1)  # cabinet
    # chef's knife lying on the counter, pointing toward the viewer
    blade = np.array([[700, 470], [1050, 520], [1200, 560], [1060, 585], [700, 520]])
    cv2.fillPoly(img, [blade], (215, 215, 220))
    cv2.polylines(img, [blade], True, (120, 120, 130), 3)
    cv2.rectangle(img, (520, 470), (705, 525), (30, 40, 60), -1)     # handle
    for x in (560, 610, 660):
        cv2.circle(img, (x, 497), 6, (180, 180, 180), -1)
    cv2.circle(img, (300, 560), 80, (60, 140, 230), -1)              # an orange
    return img


for name, fn in [("street_car", street), ("hallway_empty", hallway), ("kitchen_knife", kitchen_knife)]:
    cv2.imwrite(str(OUT / f"{name}.jpg"), fn())
    print("wrote", name)
