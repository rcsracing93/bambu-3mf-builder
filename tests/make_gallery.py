#!/usr/bin/env python3
"""Draw one top-view picture per plate from the G-code Studio produced for each example (the headless Studio cannot render its own
preview). Colors come from the job file, the prime tower is grey. Output: docs/gallery/NAME.svg (NAME_plateN.svg for later plates).
Usage: python3 tests/make_gallery.py SLICE_WORK_DIR [--out docs/gallery]
SLICE_WORK_DIR is the --work folder of tests/studio_slice.py (it holds NAME.plain/plate_N.gcode)."""
import glob, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from bambu3mf import job as jobmod  # noqa: E402
sys.path.insert(0, HERE)
from harness import list_examples  # noqa: E402

MAX_SEGMENTS = 3000
TOKEN = re.compile(r"([XYZEF])(-?\d*\.?\d+)")


def read_layers(path):
    """[(z, tool, kind, x0, y0, x1, y1)] grouped per layer: list of layers, each a list of segments."""
    layers, cur, zs, zpend = [], None, [], 0.0
    x = y = z = None
    tool, kind, absolute = 0, "", True
    with open(path, errors="replace") as fh:
        for line in fh:
            if line.startswith("; Z_HEIGHT:"):
                zpend = float(line.split(":", 1)[1])
                continue
            if line.startswith("; layer num/total_layer_count"):
                cur = []
                layers.append(cur)
                zs.append(zpend)
                continue
            if line.startswith("; FEATURE:"):
                kind = line.split(":", 1)[1].strip()
                continue
            if line.startswith("G90"):
                absolute = True
                continue
            if line.startswith("G91"):
                absolute = False
                continue
            m = re.match(r"T(\d+)\s*$", line)
            if m:
                if int(m.group(1)) < 16:
                    tool = int(m.group(1))
                continue
            if cur is None or not line.startswith(("G1 ", "G0 ")):
                continue
            v = {k: float(n) for k, n in TOKEN.findall(line.split(";")[0])}
            nx, ny = v.get("X", x), v.get("Y", y)
            if absolute and nx is not None and ny is not None and x is not None and y is not None and v.get("E", 0) > 0 and (nx != x or ny != y) and kind not in ("Custom",):
                cur.append((tool, kind, x, y, nx, ny))
            if absolute:
                x, y = nx, ny
    return layers, zs


def svg(read, colors, bed=256):
    layers, zs = read
    keep = [l for l in layers if l]
    total = sum(len(l) for l in keep)
    step = max(1, -(-total // MAX_SEGMENTS))
    pick = keep[::step]
    if keep and keep[-1] not in pick:
        pick.append(keep[-1])
    paths = {}                                           # color -> path data, drawn in layer order per color
    for l in pick:
        for tool, kind, x0, y0, x1, y1 in l:
            col = "#9a9a9a" if kind == "Prime tower" else colors[tool] if tool < len(colors) else "#000000"
            paths.setdefault(col, []).append("M%.1f %.1fL%.1f %.1f" % (x0, bed - y0, x1, bed - y1))
    xs = [v for l in pick for sg in l for v in (sg[2], sg[4])]
    ys = [v for l in pick for sg in l for v in (sg[3], sg[5])]
    if not xs:
        xs, ys = [0, 1], [0, 1]
    top = max(zs) if zs else 1.0
    side = max(max(xs) - min(xs), max(ys) - min(ys), top, 20.0) + 16       # one square window for both views
    ox, oy = (min(xs) + max(xs)) / 2 - side / 2, (min(ys) + max(ys)) / 2 - side / 2
    P = 256
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">' % (2 * P + 16, P, 2 * (2 * P + 16), 2 * P),
           '<rect width="%d" height="%d" fill="#ffffff"/>' % (2 * P + 16, P)]
    # top view: plate window around the model
    out.append('<svg x="0" y="0" width="%d" height="%d" viewBox="%.1f %.1f %.1f %.1f"><rect x="-1000" y="-1000" width="3000" height="3000" fill="#f4f4f4"/>' % (
        P, P, ox, bed - (oy + side), side, side))
    for col, d in paths.items():
        out.append('<path d="%s" fill="none" stroke="%s" stroke-width="0.5" stroke-linecap="round"/>' % ("".join(d), col))
    out.append('</svg>')
    # front view (x across, height up): per layer and tool, the span the tool printed
    out.append('<svg x="%d" y="0" width="%d" height="%d" viewBox="%.1f %.1f %.1f %.1f"><rect x="-1000" y="-1000" width="3000" height="3000" fill="#f4f4f4"/>' % (
        P + 16, P, P, ox, bed - side, side, side))
    prev = 0.0
    for l, z in zip(layers, zs):
        spans = {}
        for tool, kind, x0, y0, x1, y1 in l:
            c = "#9a9a9a" if kind == "Prime tower" else colors[tool] if tool < len(colors) else "#000000"
            lo, hi = spans.get(c, (1e9, -1e9))
            spans[c] = (min(lo, x0, x1), max(hi, x0, x1))
        for c, (lo, hi) in spans.items():
            out.append('<rect x="%.1f" y="%.2f" width="%.1f" height="%.2f" fill="%s"/>' % (lo, bed - z, max(hi - lo, 0.3), max(z - prev, 0.2), c))
        prev = z
    out.append('</svg></svg>')
    return "\n".join(out)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    work = argv[0]
    out = argv[argv.index("--out") + 1] if "--out" in argv else os.path.join(ROOT, "docs", "gallery")
    os.makedirs(out, exist_ok=True)
    n = 0
    for jf in list_examples(paths=True):
        name = os.path.splitext(os.path.basename(jf))[0]
        job = jobmod.load_job(jf)
        colors = [f["color"] for f in job.filaments]
        for g in sorted(glob.glob(os.path.join(work, name + ".plain", "plate_*.gcode"))):
            k = int(re.search(r"plate_(\d+)", g).group(1))
            target = os.path.join(out, "%s%s.svg" % (name, "" if k == 1 else "_plate%d" % k))
            open(target, "w").write(svg(read_layers(g), colors))
            n += 1
    print("wrote %d pictures to %s" % (n, out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
