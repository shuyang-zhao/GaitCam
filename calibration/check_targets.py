#!/usr/bin/env python3
"""Verify the printable calibration PDFs by rendering them (PyMuPDF) and detecting the targets (OpenCV),
i.e. check what will actually come out of the printer:

  page sizes · ChArUco fully detected with 20.00 mm squares · 3 floor strips detected with 10.00 mm squares ·
  100 mm bars are 100 mm · the board is still detected tilted + through strong barrel distortion + half out of view

    python check_targets.py [preview_dir]     # exit code 1 if any check fails
                                              # preview_dir: also save low-res PNG previews of the Letter PDF there
Needs PyMuPDF and OpenCV >= 4.7.
"""
import os
import sys

import cv2
import numpy as np
import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
# same spec as make_targets.py, written out again so the check does not trust the generator
BOARD = dict(cols=9, rows=11, square_mm=20.0, marker_mm=15.0, dictionary="DICT_4X4_50")
STRIP = dict(cols=24, rows=4, square_mm=10.0, n=3)
PAGES = {"Letter": (612.0, 792.0), "A4": (595.28, 841.89)}      # PDF points (1/72 in)
DPI = 300
PX_PER_MM = DPI / 25.4
fails = []


def check(cond, what):
    print(("PASS " if cond else "FAIL ") + what)
    if not cond:
        fails.append(what)


def render(pg, dpi=DPI):
    pix = pg.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w).copy()


def charuco():
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, BOARD["dictionary"]))
    board = cv2.aruco.CharucoBoard((BOARD["cols"], BOARD["rows"]), BOARD["square_mm"], BOARD["marker_mm"], d)
    return board, cv2.aruco.CharucoDetector(board)


def detect_charuco(img):
    _, det = charuco()
    corners, ids, _, _ = det.detectBoard(img)
    return (np.zeros((0, 2)), np.zeros(0, int)) if ids is None else (corners.reshape(-1, 2), ids.ravel())


def square_mm(corners, ids, cols):
    """Mean distance between horizontally adjacent ChArUco corners, in mm at the render DPI."""
    pos = {int(i): c for i, c in zip(ids, corners)}
    nx = cols - 1
    d = [np.linalg.norm(pos[i + 1] - pos[i]) for i in pos if (i + 1) in pos and (i + 1) % nx != 0]
    return float(np.mean(d)) / PX_PER_MM if d else float("nan")


def bars_mm(pg):
    """Lengths (mm) of horizontal drawn lines of ~100 mm."""
    out = []
    for dr in pg.get_drawings():
        for it in dr["items"]:
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                if abs(p1.y - p2.y) < 0.01:
                    L = abs(p2.x - p1.x) * 25.4 / 72
                    if 90 < L < 110:
                        out.append(L)
    return out


def barrel(img, k1=-0.32, k2=0.08):
    """Strong radial (barrel) distortion, roughly a 130° M12 lens, applied to a camera-like view."""
    h, w = img.shape
    f = 0.55 * w
    yy, xx = np.indices((h, w), np.float32)
    x, y = (xx - w / 2) / f, (yy - h / 2) / f
    r2 = x * x + y * y
    s = 1 + k1 * r2 + k2 * r2 * r2           # distorted -> undistorted lookup (inverse-ish), good enough to test
    mx, my = (x / s * f + w / 2).astype(np.float32), (y / s * f + h / 2).astype(np.float32)
    return cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderValue=200)


def camera_view(board_img, tilt_deg=35, shift=0.0, out=(1280, 800)):
    """Board seen tilted (perspective) by a 1280x800 camera, optionally pushed half out of the picture."""
    h, w = board_img.shape
    W, H = out
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    k = np.tan(np.radians(tilt_deg)) * 0.25
    bw, bh = 0.42 * W, 0.42 * W * h / w
    cx, cy = W / 2 + shift * W, H / 2
    dst = np.float32([[cx - bw / 2 * (1 - k), cy - bh / 2], [cx + bw / 2 * (1 - k), cy - bh / 2],
                      [cx + bw / 2 * (1 + k), cy + bh / 2], [cx - bw / 2 * (1 + k), cy + bh / 2]])
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(board_img, M, (W, H), borderValue=200)


def main():
    for name, (pw, ph) in PAGES.items():
        path = os.path.join(HERE, f"GaitCam_calibration_{name}.pdf")
        doc = pymupdf.open(path)
        print(f"== {os.path.basename(path)}")
        check(doc.page_count == 3, f"3 pages ({doc.page_count})")
        sizes = [(round(p.rect.width, 1), round(p.rect.height, 1)) for p in doc]
        check(sizes[0] == sizes[1] == (round(pw, 1), round(ph, 1)) and sizes[2] == (round(ph, 1), round(pw, 1)),
              f"page sizes {sizes} (portrait, portrait, landscape {name})")
        check(len(doc[0].get_text().strip()) > 500, "instructions page has text")
        ink0 = (render(doc[0], 100) < 128).mean()
        check(ink0 > 0.01, f"instructions text actually renders ({100 * ink0:.1f}% ink)")

        # lens board
        img = render(doc[1])
        c, ids = detect_charuco(img)
        n_exp = (BOARD["cols"] - 1) * (BOARD["rows"] - 1)
        check(len(ids) == n_exp and set(ids.tolist()) == set(range(n_exp)), f"ChArUco: {len(ids)}/{n_exp} corners")
        sq = square_mm(c, ids, BOARD["cols"])
        check(abs(sq - BOARD["square_mm"]) < 0.05, f"ChArUco square measures {sq:.3f} mm (spec {BOARD['square_mm']})")
        check(any(abs(L - 100) < 0.05 for L in bars_mm(doc[1])), f"page 2 scale bar {bars_mm(doc[1])} mm")

        # floor strips: detect each strip in its own horizontal band
        img3 = render(doc[2])
        H = img3.shape[0]
        found, sizes_mm = 0, []
        pat = (STRIP["cols"] - 1, STRIP["rows"] - 1)
        for k in range(STRIP["n"]):
            band = img3[int(H * (0.08 + k * 0.27)):int(H * (0.08 + (k + 1) * 0.27) + 60)]
            ok, pts = cv2.findChessboardCornersSB(band, pat)
            if ok:
                found += 1
                g = pts.reshape(pat[1], pat[0], 2)
                sizes_mm.append(float(np.linalg.norm(np.diff(g, axis=1), axis=2).mean()) / PX_PER_MM)
        check(found == STRIP["n"], f"floor strips detected: {found}/{STRIP['n']}")
        check(sizes_mm and all(abs(v - STRIP["square_mm"]) < 0.05 for v in sizes_mm),
              f"strip squares measure {[round(v, 3) for v in sizes_mm]} mm (spec {STRIP['square_mm']})")
        check(any(abs(L - 100) < 0.05 for L in bars_mm(doc[2])), f"page 3 scale bar {bars_mm(doc[2])} mm")

        # robustness: crop the board area, view it like the camera would
        ys, xs = np.nonzero(img < 128)
        crop = img[ys.min():ys.max(), xs.min():xs.max()]
        crop = crop[int(0.12 * crop.shape[0]):int(0.88 * crop.shape[0])]           # drop header / bar text
        for label, view in [("tilted 35°", camera_view(crop)),
                            ("tilted 35° + strong barrel distortion", barrel(camera_view(crop))),
                            ("half out of the picture + distortion", barrel(camera_view(crop, shift=0.42)))]:
            _, ids2 = detect_charuco(view)
            check(len(ids2) >= (25 if "half" in label else 50), f"camera-like view, {label}: {len(ids2)} corners")
        if name == "Letter" and len(sys.argv) > 1:
            out = sys.argv[1]
            os.makedirs(out, exist_ok=True)
            for i in range(3):
                doc[i].get_pixmap(dpi=60).save(os.path.join(out, f"preview_page{i + 1}.png"))
            cv2.imwrite(os.path.join(out, "preview_camera_view.png"), barrel(camera_view(crop, shift=0.42)))
    print(f"\n{len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
