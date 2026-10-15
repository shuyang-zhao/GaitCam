#!/usr/bin/env python3
"""Printable calibration targets for the GaitCam bottom-view camera (130° wide-angle M12 lens).

    page 1  instructions (EN + 中文)
    page 2  lens board  : ChArUco 9x11 squares, 20.0 mm squares, 15.0 mm markers, OpenCV DICT_4X4_50
                          -> lens model (distortion), once per camera + lens + focus setting
    page 3  floor strips: 3 cut-out checkerboards, 4x24 squares of 10.0 mm (OpenCV pattern 23x3 inner corners)
                          -> floor plane (homography, px/cm), whenever the camera or the walkway moves

Why ChArUco for the lens: the corners of a 130° picture are where distortion is largest, and a plain checkerboard
is only detected when it is entirely in view - ChArUco corners are individually identified, so partial views at
the picture edge still count.

Exact geometry: the ChArUco image comes from OpenCV itself (same definition the detector uses) on a 0.25 mm pixel
grid, so every square and marker-bit edge falls on a pixel edge; it is embedded unresampled. Strips are vector.
Every target page carries a 100 mm bar to check that the printer did not scale the page.

    python make_targets.py        # -> GaitCam_calibration_Letter.pdf, GaitCam_calibration_A4.pdf next to this file

Needs OpenCV >= 4.7 (cv2.aruco.CharucoBoard), matplotlib and the Noto Sans CJK SC font for the Chinese text
(or point GAITCAM_CJK_FONT at another font file with Latin + CJK glyphs). Verify the output with check_targets.py.
The PDFs carry no creation date or build date, so the same software versions reproduce the same files.
"""
from __future__ import annotations

import os

import cv2
import matplotlib

matplotlib.use("pdf")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager as fm  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MM = 1 / 25.4                       # inches per mm

# ---- target definitions: check_targets.py and the calibration analysis (detector settings) must use the same values
BOARD = dict(cols=9, rows=11, square_mm=20.0, marker_mm=15.0, dictionary="DICT_4X4_50")
PX_MM = 0.25                        # raster pitch: 20 mm = 80 px, 15 mm = 60 px, one marker bit (2.5 mm) = 10 px
STRIP = dict(cols=24, rows=4, square_mm=10.0, quiet_mm=5.0, n=3)
PAGES = {"Letter": (215.9, 279.4), "A4": (210.0, 297.0)}

# one font with Latin + CJK glyphs, so mixed EN / 中文 lines need no glyph fallback
FONT_FILE = os.environ.get("GAITCAM_CJK_FONT") or fm.findfont("Noto Sans CJK SC", fallback_to_default=False)
fm.fontManager.addfont(FONT_FILE)
FONT = fm.FontProperties(fname=FONT_FILE).get_name()
matplotlib.rcParams["pdf.fonttype"] = 3       # glyphs as paths: Type 42 embedded this CFF-based OpenType font
matplotlib.rcParams["font.family"] = FONT     # but the text did not render
INK, GREY, BLUE = "#000000", "#5F6368", "#0B57D0"


def page(w_mm, h_mm):
    fig = plt.figure(figsize=(w_mm * MM, h_mm * MM))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w_mm)
    ax.set_ylim(h_mm, 0)                       # millimetres, origin top-left
    ax.axis("off")
    return fig, ax


def text(ax, x, y, s, size=9, color=INK, **kw):
    ax.text(x, y, s, fontsize=size, color=color, va="top", ha=kw.pop("ha", "left"), **kw)


def _w(ch: str) -> float:
    """Advance width in em: CJK / full-width = 1, Latin ~0.55."""
    return 1.0 if ord(ch) > 0x2E80 else 0.55


def wrap_mixed(s: str, width_mm: float, size_pt: float) -> list[str]:
    """Line-break mixed Chinese / English by measured width; never splits a Latin word or number."""
    em = size_pt * 25.4 / 72
    lines, cur, cur_w = [], "", 0.0
    tokens = []
    buf = ""
    for ch in s:                                   # Latin runs stay together, CJK chars are single tokens
        if ord(ch) > 0x2E80:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        elif ch == " ":
            tokens.append(buf + " ")
            buf = ""
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    for tok in tokens:
        tw = sum(_w(c) for c in tok) * em
        if cur and cur_w + tw > width_mm and tok.strip():
            lines.append(cur.rstrip())
            cur, cur_w = tok.lstrip(), sum(_w(c) for c in tok.lstrip()) * em
        else:
            cur, cur_w = cur + tok, cur_w + tw
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def para(ax, x, y, s, width_mm, size=9, color=INK, gap=1.55, **kw):
    """Wrapped paragraph; returns the y below it (mm)."""
    lh = size * 25.4 / 72 * gap
    for ln in wrap_mixed(s, width_mm, size):
        text(ax, x, y, ln, size=size, color=color, **kw)
        y += lh
    return y


def scale_bar(ax, x, y):
    """100 mm bar with 10 mm ticks: must measure 100 mm on paper."""
    ax.plot([x, x + 100], [y, y], color=INK, lw=0.8, solid_capstyle="butt")
    for k in range(11):
        t = 2.5 if k % 5 == 0 else 1.5
        ax.plot([x + 10 * k, x + 10 * k], [y, y - t], color=INK, lw=0.6)
    text(ax, x + 50, y + 1.5, "100 mm — must measure exactly 100 mm with a ruler  /  用尺子量必须正好 100 mm",
         size=7, color=GREY, ha="center")


def lens_board_image():
    b = BOARD
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, b["dictionary"]))
    board = cv2.aruco.CharucoBoard((b["cols"], b["rows"]), b["square_mm"], b["marker_mm"], d)
    w, h = round(b["cols"] * b["square_mm"] / PX_MM), round(b["rows"] * b["square_mm"] / PX_MM)
    img = board.generateImage((w, h), marginSize=0, borderBits=1)
    # every square edge must sit on the 0.25 mm grid
    sq = round(b["square_mm"] / PX_MM)
    assert img.shape == (h, w) and set(img.ravel().tolist()) <= {0, 255}
    assert (img[sq // 2, sq - 1] != img[sq // 2, sq]), "square edges not on the pixel grid"
    return img


def page_lens(name, w_mm, h_mm, img):
    b = BOARD
    fig, ax = page(w_mm, h_mm)
    bw, bh = b["cols"] * b["square_mm"], b["rows"] * b["square_mm"]
    x0, y0 = (w_mm - bw) / 2, 24
    ax.imshow(img, cmap="gray", vmin=0, vmax=255, interpolation="none",
              extent=(x0, x0 + bw, y0 + bh, y0))
    ax.add_patch(Rectangle((x0, y0), bw, bh, fill=False, lw=0.3, ec="#BDC1C6"))
    text(ax, x0, 9, "GaitCam lens calibration board  ·  镜头标定板", size=13)
    para(ax, x0, 15.5, f"ChArUco {b['cols']}×{b['rows']} squares · square {b['square_mm']:.1f} mm · marker "
         f"{b['marker_mm']:.1f} mm · OpenCV {b['dictionary']}  —  mount flat on a stiff board / 平整贴在硬板上",
         bw, size=7.5, color=GREY, gap=1.3)
    scale_bar(ax, (w_mm - 100) / 2, y0 + bh + 9)
    text(ax, w_mm / 2, h_mm - 10, "Print at 100% / Actual size — not “Fit to page”.  打印选 100% /“实际大小”，"
         "不要“适合页面”。", size=7, color=GREY, ha="center")
    text(ax, w_mm / 2, h_mm - 6.5, f"{name} paper", size=6.5, color=GREY, ha="center")
    return fig


def page_strips(name, w_mm, h_mm):
    s = STRIP
    fig, ax = page(h_mm, w_mm)                 # landscape
    W, H = h_mm, w_mm
    sw, sh = s["cols"] * s["square_mm"], s["rows"] * s["square_mm"]
    q = s["quiet_mm"]
    x0 = (W - sw) / 2
    text(ax, x0 - q, 8, "GaitCam floor strips  ·  地板纸条", size=13)
    para(ax, x0 - q, 14.5, f"Checkerboard {s['rows']}×{s['cols']} squares · {s['square_mm']:.1f} mm (OpenCV pattern "
         f"{s['cols'] - 1}×{s['rows'] - 1})  —  cut along the dashed line, lay printed side DOWN on the walkway floor  /  "
         "沿虚线剪下，印刷面朝下平放在走道地板上", sw + 2 * q, size=7, color=GREY, gap=1.3)
    top = 24
    gap = (H - top - 26 - s["n"] * (sh + 2 * q)) / max(1, s["n"] - 1)
    for k in range(s["n"]):
        y0 = top + q + k * (sh + 2 * q + gap)
        for r in range(s["rows"]):
            for c in range(s["cols"]):
                if (r + c) % 2 == 0:
                    ax.add_patch(Rectangle((x0 + c * s["square_mm"], y0 + r * s["square_mm"]),
                                           s["square_mm"], s["square_mm"], color=INK, lw=0))
        ax.add_patch(Rectangle((x0 - q, y0 - q), sw + 2 * q, sh + 2 * q, fill=False, lw=0.5, ec=GREY, ls=(0, (4, 3))))
        text(ax, x0 + sw + q + 2, y0 + sh / 2 - 2, f"strip {'ABC'[k]}", size=7, color=GREY)
    scale_bar(ax, (W - 100) / 2, H - 17)
    text(ax, W / 2, H - 8, f"Print at 100% / Actual size.   打印选 100% /“实际大小”。   ({name})", size=7,
         color=GREY, ha="center")
    return fig


INSTRUCTIONS = [
    ("h", "1  Print  打印"),
    ("en", "Print pages 2 and 3 at 100% / “Actual size” — never “Fit to page”. Measure the 100 mm bar on each page with "
           "a ruler: it must be 100 mm (±0.5 mm), otherwise fix the printer scaling and print again. Laser printer, "
           "matte paper (no glossy paper: glare)."),
    ("zh", "第 2、3 页用 100% /“实际大小”打印，千万不要选“适合页面”。用尺子量每页的 100 mm 刻度条，必须是 100 mm（±0.5 mm），"
           "否则调整打印缩放后重打。用激光打印机、哑光纸（不要光面纸，会反光）。"),
    ("h", "2  Lens board (page 2) — once per camera   镜头标定板（第 2 页）—— 每台相机做一次"),
    ("en", "a. Focus the lens on the walkway floor, then tighten the lens lock screw. Do not turn the lens afterwards; "
           "if it is turned, repeat this step."),
    ("zh", "a. 先把镜头焦距对准走道地板，再拧紧镜头锁定螺丝。之后不要再转镜头；转过就要重做这一步。"),
    ("en", "b. Glue or tape page 2 completely flat onto a stiff board (foam board, clipboard). A bent sheet ruins the result."),
    ("zh", "b. 把第 2 页完全平整地贴在硬板上（泡沫板、写字夹板）。纸一弯，结果就不准。"),
    ("en", "c. The camera must see the board directly, not through the frosted floor: take the floor off for this step "
           "(or do it before the camera is mounted)."),
    ("zh", "c. 相机要直接看到板子，不能隔着磨砂底板：做这一步时把底板拿掉（或在装相机之前做）。"),
    ("en", "d. In GaitCam type the animal ID LENS and press Record. For about 60 seconds move the board slowly 10–20 cm in "
           "front of the camera: the centre, all four corners and all four edges of the picture, tilted up to about 45° "
           "in different directions. Move slowly and pause now and then. The board may be partly out of the picture."),
    ("zh", "d. 在 GaitCam 里把编号填 LENS，点开始录制。约 60 秒内，把板子放在相机前 10–20 cm 慢慢移动：画面中间、四个角、四条边"
           "都要覆盖到，并朝不同方向倾斜到约 45°。慢慢移，时不时停一下。板子部分出画没关系。"),
    ("h", "3  Floor strips (page 3) — every time the camera or the walkway is moved   地板纸条（第 3 页）—— 相机或走道每次移动后"),
    ("en", "a. Cut out the three strips along the dashed lines (keep the white border)."),
    ("zh", "a. 沿虚线剪下三条纸条（保留白边）。"),
    ("en", "b. With the floor in place, lay the strips printed side DOWN on the walkway floor, end to end along the "
           "walkway, centred, pressed flat."),
    ("zh", "b. 装好底板，把纸条印刷面朝下平放在走道地板上，沿走道首尾相接、居中、压平。"),
    ("en", "c. Switch on the usual walkway light and click “Record a 1-s calibration clip” in GaitCam. Take the strips "
           "away before the animals walk."),
    ("zh", "c. 打开平时用的走道灯，在 GaitCam 里点“录 1 秒标定片段”。放小鼠之前把纸条拿走。"),
    ("h", "4  Keep the files   保存文件"),
    ("en", "That day's data folder now contains LENS_t01_….mkv and the calib folder. Keep them with the videos: the "
           "lens model, the floor mapping and the centimetre scale for those videos are computed from them."),
    ("zh", "当天的数据文件夹里会有 LENS_t01_….mkv 和 calib 文件夹，请和视频一起保存：这些视频的镜头模型、地板映射和厘米换算"
           "都由它们计算。"),
]


def page_instructions(name, w_mm, h_mm):
    fig, ax = page(w_mm, h_mm)
    x, y, right = 18, 16, w_mm - 18
    text(ax, x, y, "GaitCam camera calibration  ·  相机校准", size=17)
    y += 10
    y = para(ax, x, y, "Two targets. The lens board corrects the 130° lens distortion (once per camera). The floor "
             "strips give the floor plane and the centimetre scale (whenever the camera or walkway moves).",
             right - x, size=8.5, color=GREY)
    y = para(ax, x, y, "两种图案：镜头板校正 130° 镜头的畸变（每台相机一次）；地板纸条给出地板平面和厘米换算（相机或走道移动后重做）。",
             right - x, size=8.5, color=GREY) + 4
    for kind, s in INSTRUCTIONS:
        if kind == "h":
            y += 3
            y = para(ax, x, y, s, right - x, size=10.5, color=BLUE, gap=1.35) + 0.8
            continue
        y = para(ax, x + 2, y, s, right - x - 2, size=8.5, color=INK if kind == "en" else "#202124", gap=1.45) + 1.3
    b, st = BOARD, STRIP
    y = max(y + 4, h_mm - 30)
    ax.plot([x, right], [y, y], color="#DADCE0", lw=0.6)
    y = para(ax, x, y + 3, f"Lens board: ChArUco {b['cols']}×{b['rows']} squares, {b['square_mm']:.1f} mm squares, "
             f"{b['marker_mm']:.1f} mm markers, OpenCV {b['dictionary']}.  Floor strip: checkerboard "
             f"{st['rows']}×{st['cols']} squares of {st['square_mm']:.1f} mm (OpenCV pattern {st['cols'] - 1}×"
             f"{st['rows'] - 1}).", right - x, size=7, color=GREY, gap=1.35)
    text(ax, x, y + 0.5, f"{name} paper · generated by GaitCam calibration/make_targets.py", size=7, color=GREY)
    assert y < h_mm - 6, "instructions overflow the page"
    print(f"   instructions page {name}: text ends at {y:.0f} of {h_mm:.0f} mm")
    return fig


def main():
    from matplotlib.backends.backend_pdf import PdfPages
    img = lens_board_image()
    for name, (w, h) in PAGES.items():
        out = os.path.join(HERE, f"GaitCam_calibration_{name}.pdf")
        meta = {"Title": f"GaitCam calibration targets ({name})", "Author": "GaitCam", "CreationDate": None}
        with PdfPages(out, metadata=meta) as pdf:
            for fig in (page_instructions(name, w, h), page_lens(name, w, h, img), page_strips(name, w, h)):
                pdf.savefig(fig)
                plt.close(fig)
        print(f"-> {out}")


if __name__ == "__main__":
    main()
