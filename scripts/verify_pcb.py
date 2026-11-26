#!/usr/bin/env python3
"""
Геометрический DRC + LVS (Netlist Connectivity) верификатор Gerber-файлов.

Проверяет по реальным файлам F_Cu.gtl, B_Cu.gbl и .drl:
1. Полную связность каждой электрической цепи (0 обрывов).
2. Отсутствие коротких замыканий между любыми цепями (0 КЗ).
3. Минимальный электрический зазор (clearance >= 0.20 мм) между любой медью
   разных цепей (сегмент-сегмент, сегмент-пад, пад-пад, в т.ч. прямоугольные пады).
4. Наличие отверстий в Excellon Drill для всех THT-площадок и переходных отверстий (Via).
5. При --render сохраняет PNG-визуализацию Top и Bottom слоёв для осмотра.
"""

import os
import re
import sys
import json
import math

GERBER_DIR = os.path.join(os.path.dirname(__file__), "..", "gerber")
PCB_DIR = os.path.join(os.path.dirname(__file__), "..", "pcb")
PROJECT_NAME = "SimPedal_Brake_Carrier_v1.2"
MIN_CLEARANCE_MM = 0.20


def parse_gerber_primitives(filepath: str, layer: str):
    """Извлекает отрезки и контактные площадки из файла RS-274X."""
    apertures = {}
    current_ap = None
    cur_x, cur_y = 0.0, 0.0
    last_pt = None

    segments = []  # {"layer": str, "p1": (x, y), "p2": (x, y), "w": float}
    pads = []      # {"layer": str, "x": float, "y": float, "shape": str, "w": float, "h": float}

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    for m in re.finditer(r"%ADD(\d+)([CR]),([\d\.]+)(?:X([\d\.]+))?\*%", content):
        dcode = int(m.group(1))
        shape = m.group(2)
        d1 = float(m.group(3))
        d2 = float(m.group(4)) if m.group(4) else d1
        apertures[dcode] = (shape, d1, d2)

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("G04") or line.startswith("%") or line in ("G01*", "G75*", "M02*"):
            continue
        m_ap = re.match(r"D(\d+)\*", line)
        if m_ap:
            current_ap = int(m_ap.group(1))
            continue

        m_coord = re.match(r"(?:X([-\d]+))?(?:Y([-\d]+))?(D0[123])\*", line)
        if m_coord:
            if m_coord.group(1) is not None:
                cur_x = float(m_coord.group(1)) / 1_000_000.0
            if m_coord.group(2) is not None:
                cur_y = float(m_coord.group(2)) / 1_000_000.0
            op = m_coord.group(3)
            ap = apertures.get(current_ap, ("C", 0.2, 0.2))

            if op == "D02":
                last_pt = (cur_x, cur_y)
            elif op == "D01":
                if last_pt is not None and (last_pt[0] != cur_x or last_pt[1] != cur_y):
                    segments.append({
                        "layer": layer,
                        "p1": last_pt,
                        "p2": (cur_x, cur_y),
                        "w": ap[1],
                    })
                last_pt = (cur_x, cur_y)
            elif op == "D03":
                pads.append({
                    "layer": layer,
                    "x": cur_x,
                    "y": cur_y,
                    "shape": ap[0],
                    "w": ap[1],
                    "h": ap[2],
                })
                last_pt = (cur_x, cur_y)

    return segments, pads


def parse_drill_holes(filepath: str):
    """Извлекает список координат и диаметров отверстий из файла Excellon .drl."""
    tools = {}
    current_tool = None
    holes = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            m_t = re.match(r"T(\d+)C([\d\.]+)", line)
            if m_t:
                tools[int(m_t.group(1))] = float(m_t.group(2))
                continue
            m_sel = re.match(r"T(\d+)$", line)
            if m_sel:
                current_tool = int(m_sel.group(1))
                continue
            m_xy = re.fullmatch(r"X(-?\d+(?:\.\d*)?)Y(-?\d+(?:\.\d*)?)", line)
            if m_xy and current_tool is not None:
                x, y = map(float, m_xy.groups())
                holes.append((x, y, tools.get(current_tool, 1.0)))
    return holes


def dist_point_to_seg(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    vx, vy = b[0] - a[0], b[1] - a[1]
    wx, wy = px - a[0], py - a[1]
    c2 = vx * vx + vy * vy
    if c2 <= 1e-12:
        return math.hypot(px - a[0], py - a[1])
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / c2))
    proj_x = a[0] + t * vx
    proj_y = a[1] + t * vy
    return math.hypot(px - proj_x, py - proj_y)


def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_seg(a, b, p) -> bool:
    return (min(a[0], b[0]) - 1e-9 <= p[0] <= max(a[0], b[0]) + 1e-9 and
            min(a[1], b[1]) - 1e-9 <= p[1] <= max(a[1], b[1]) + 1e-9)


def segments_intersect(a1, a2, b1, b2) -> bool:
    o1 = _orient(a1, a2, b1)
    o2 = _orient(a1, a2, b2)
    o3 = _orient(b1, b2, a1)
    o4 = _orient(b1, b2, a2)
    if (o1 > 1e-9 and o2 < -1e-9 or o1 < -1e-9 and o2 > 1e-9) and \
       (o3 > 1e-9 and o4 < -1e-9 or o3 < -1e-9 and o4 > 1e-9):
        return True
    if abs(o1) <= 1e-9 and _on_seg(a1, a2, b1):
        return True
    if abs(o2) <= 1e-9 and _on_seg(a1, a2, b2):
        return True
    if abs(o3) <= 1e-9 and _on_seg(b1, b2, a1):
        return True
    if abs(o4) <= 1e-9 and _on_seg(b1, b2, a2):
        return True
    return False


def dist_seg_to_seg(a1, a2, b1, b2) -> float:
    if segments_intersect(a1, a2, b1, b2):
        return 0.0
    return min(
        dist_point_to_seg(a1[0], a1[1], b1, b2),
        dist_point_to_seg(a2[0], a2[1], b1, b2),
        dist_point_to_seg(b1[0], b1[1], a1, a2),
        dist_point_to_seg(b2[0], b2[1], a1, a2),
    )


def dist_point_to_rect(px: float, py: float, rx: float, ry: float, rw: float, rh: float) -> float:
    dx = max(abs(px - rx) - rw / 2.0, 0.0)
    dy = max(abs(py - ry) - rh / 2.0, 0.0)
    return math.hypot(dx, dy)


def dist_seg_to_rect(p1, p2, rx: float, ry: float, rw: float, rh: float) -> float:
    """Точное минимальное расстояние от отрезка p1-p2 до осеориентированного прямоугольника."""
    x_min, x_max = rx - rw / 2.0, rx + rw / 2.0
    y_min, y_max = ry - rh / 2.0, ry + rh / 2.0

    # Проверка попадания концов или пересечения с гранями прямоугольника
    if (x_min <= p1[0] <= x_max and y_min <= p1[1] <= y_max) or \
       (x_min <= p2[0] <= x_max and y_min <= p2[1] <= y_max):
        return 0.0

    corners = [(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)]
    edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    for e1, e2 in edges:
        if segments_intersect(p1, p2, e1, e2):
            return 0.0

    return min(
        dist_point_to_rect(p1[0], p1[1], rx, ry, rw, rh),
        dist_point_to_rect(p2[0], p2[1], rx, ry, rw, rh),
        *(dist_point_to_seg(cx, cy, p1, p2) for cx, cy in corners),
    )


def pad_to_pad_clearance(p1: dict, p2: dict) -> float:
    if p1["shape"] == "C" and p2["shape"] == "C":
        return math.hypot(p1["x"] - p2["x"], p1["y"] - p2["y"]) - (p1["w"] + p2["w"]) / 2.0
    if p1["shape"] == "R" and p2["shape"] == "R":
        dx = max(abs(p1["x"] - p2["x"]) - (p1["w"] + p2["w"]) / 2.0, 0.0)
        dy = max(abs(p1["y"] - p2["y"]) - (p1["h"] + p2["h"]) / 2.0, 0.0)
        return math.hypot(dx, dy)
    # Circle vs Rect
    c, r = (p1, p2) if p1["shape"] == "C" else (p2, p1)
    return dist_point_to_rect(c["x"], c["y"], r["x"], r["y"], r["w"], r["h"]) - c["w"] / 2.0


def seg_to_pad_clearance(seg: dict, pad: dict) -> float:
    if pad["shape"] == "C":
        return dist_point_to_seg(pad["x"], pad["y"], seg["p1"], seg["p2"]) - (seg["w"] + pad["w"]) / 2.0
    return dist_seg_to_rect(seg["p1"], seg["p2"], pad["x"], pad["y"], pad["w"], pad["h"]) - seg["w"] / 2.0


def seg_to_seg_clearance(s1: dict, s2: dict) -> float:
    return dist_seg_to_seg(s1["p1"], s1["p2"], s2["p1"], s2["p2"]) - (s1["w"] + s2["w"]) / 2.0


def verify_pcb(render_dir: str | None = None, gerber_dir=GERBER_DIR) -> bool:
    gtl_path = os.path.join(gerber_dir, f"{PROJECT_NAME}-F_Cu.gtl")
    gbl_path = os.path.join(gerber_dir, f"{PROJECT_NAME}-B_Cu.gbl")
    drl_path = os.path.join(gerber_dir, f"{PROJECT_NAME}.drl")
    net_path = os.path.join(PCB_DIR, "netlist_expected.json")

    top_segs, top_pads = parse_gerber_primitives(gtl_path, "F_Cu")
    bot_segs, bot_pads = parse_gerber_primitives(gbl_path, "B_Cu")
    holes = parse_drill_holes(drl_path)

    with open(net_path, "r", encoding="utf-8") as nf:
        expected_nets = json.load(nf)

    print(f"Layer F_Cu (Top):    {len(top_segs)} trace segments, {len(top_pads)} pads.")
    print(f"Layer B_Cu (Bottom): {len(bot_segs)} trace segments, {len(bot_pads)} pads.")
    print(f"Excellon Drill:      {len(holes)} holes.")

    # Собираем все медные примитивы в единый список узлов для Union-Find (DSU)
    # Узел = ("seg", layer, idx) | ("pad", layer, idx) | ("hole", idx)
    nodes = []
    for i, s in enumerate(top_segs):
        nodes.append({"type": "seg", "layer": "F_Cu", "data": s, "id": len(nodes)})
    for i, s in enumerate(bot_segs):
        nodes.append({"type": "seg", "layer": "B_Cu", "data": s, "id": len(nodes)})
    for i, p in enumerate(top_pads):
        nodes.append({"type": "pad", "layer": "F_Cu", "data": p, "id": len(nodes)})
    for i, p in enumerate(bot_pads):
        nodes.append({"type": "pad", "layer": "B_Cu", "data": p, "id": len(nodes)})
    for i, (hx, hy, hd) in enumerate(holes):
        nodes.append({"type": "hole", "layer": "DRILL", "data": {"x": hx, "y": hy, "d": hd}, "id": len(nodes)})

    parent = list(range(len(nodes)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 1. Объединяем касающиеся / перекрывающиеся примитивы внутри одного слоя меди
    copper_nodes = [n for n in nodes if n["type"] in ("seg", "pad")]
    for idx_i in range(len(copper_nodes)):
        ni = copper_nodes[idx_i]
        for idx_j in range(idx_i + 1, len(copper_nodes)):
            nj = copper_nodes[idx_j]
            if ni["layer"] != nj["layer"]:
                continue
            if ni["type"] == "seg" and nj["type"] == "seg":
                clr = seg_to_seg_clearance(ni["data"], nj["data"])
            elif ni["type"] == "pad" and nj["type"] == "pad":
                clr = pad_to_pad_clearance(ni["data"], nj["data"])
            else:
                seg_n, pad_n = (ni, nj) if ni["type"] == "seg" else (nj, ni)
                clr = seg_to_pad_clearance(seg_n["data"], pad_n["data"])

            # Если касаются или перекрываются (clr <= 0.005 мм) -> гальваническая связь
            if clr <= 0.005:
                union(ni["id"], nj["id"])

    # 2. Связываем F_Cu и B_Cu через металлизированные отверстия (THT / Via)
    hole_nodes = [n for n in nodes if n["type"] == "hole"]
    for hn in hole_nodes:
        hx, hy = hn["data"]["x"], hn["data"]["y"]
        for cn in copper_nodes:
            if cn["type"] == "pad":
                p = cn["data"]
                if dist_point_to_rect(hx, hy, p["x"], p["y"], p["w"], p["h"]) <= 0.02:
                    union(hn["id"], cn["id"])

    # 3. Проверка LVS (Netlist): отображаем каждый ожидаемый пин на медный узел
    pin_roots: dict[str, set[int]] = {}
    root_to_nets: dict[int, set[str]] = {}
    errors = []

    for net, pins in expected_nets.items():
        pin_roots[net] = set()
        for pin in pins:
            px, py, player = pin["x"], pin["y"], pin["layer"]
            matched_id = None
            if player in ("THT", "VIA"):
                # Должен существовать drill hole + пад на F_Cu + пад на B_Cu
                has_drill = any(math.hypot(hx - px, hy - py) < 0.02 for hx, hy, _ in holes)
                if not has_drill:
                    errors.append(f"MISSING DRILL HOLE for {net} pin {pin['label']} at ({px:.2f}, {py:.2f})")
                sided = {cn["layer"]: cn["id"] for cn in copper_nodes if cn["type"] == "pad"
                         and math.hypot(cn["data"]["x"] - px, cn["data"]["y"] - py) < 0.02}
                if "F_Cu" in sided and "B_Cu" in sided:
                    matched_id = sided["F_Cu"]
                    if find(matched_id) != find(sided["B_Cu"]):
                        errors.append(f"UNCONNECTED LAYERS at {pin['label']}")
            else:
                for cn in copper_nodes:
                    if cn["type"] == "pad" and cn["layer"] == player and \
                       math.hypot(cn["data"]["x"] - px, cn["data"]["y"] - py) < 0.02:
                        matched_id = cn["id"]
                        break

            if matched_id is None:
                errors.append(f"MISSING PAD for {net} pin {pin['label']} at ({px:.2f}, {py:.2f}) on {player}")
            else:
                r = find(matched_id)
                pin_roots[net].add(r)
                root_to_nets.setdefault(r, set()).add(net)

    # Проверяем обрывы (в одной цепи > 1 компоненты связности)
    for net, roots in pin_roots.items():
        if len(roots) > 1:
            errors.append(f"OPEN CIRCUIT (ОБРЫВ) in net '{net}': {len(roots)} disconnected islands!")

    # Проверяем короткие замыкания (в одной компоненте связности > 1 цепи)
    for root, nets in root_to_nets.items():
        if len(nets) > 1:
            errors.append(f"SHORT CIRCUIT (КЗ) between nets: {sorted(nets)}")

    # 4. Проверка минимального зазора (DRC Clearance) между РАЗНЫМИ компонентами связности на одном слое
    min_clearance = 999.0
    min_pair_desc = ""
    clearance_violations = []
    for idx_i in range(len(copper_nodes)):
        ni = copper_nodes[idx_i]
        ri = find(ni["id"])
        for idx_j in range(idx_i + 1, len(copper_nodes)):
            nj = copper_nodes[idx_j]
            if ni["layer"] != nj["layer"]:
                continue
            rj = find(nj["id"])
            if ri == rj:
                continue  # Одна и та же цепь

            if ni["type"] == "seg" and nj["type"] == "seg":
                clr = seg_to_seg_clearance(ni["data"], nj["data"])
            elif ni["type"] == "pad" and nj["type"] == "pad":
                clr = pad_to_pad_clearance(ni["data"], nj["data"])
            else:
                seg_n, pad_n = (ni, nj) if ni["type"] == "seg" else (nj, ni)
                clr = seg_to_pad_clearance(seg_n["data"], pad_n["data"])

            if clr < min_clearance:
                min_clearance = clr
                nets_i = sorted(root_to_nets.get(ri, {"unknown"}))
                nets_j = sorted(root_to_nets.get(rj, {"unknown"}))
                min_pair_desc = f"{ni['layer']}: {nets_i} vs {nets_j}"
            if clr < MIN_CLEARANCE_MM:
                nets_i = root_to_nets.get(ri, {"unknown"})
                nets_j = root_to_nets.get(rj, {"unknown"})
                clearance_violations.append(
                    f"CLEARANCE VIOLATION ({clr:.3f} мм < {MIN_CLEARANCE_MM} мм) on {ni['layer']}: "
                    f"{sorted(nets_i)} vs {sorted(nets_j)}"
                )

    errors.extend(clearance_violations)

    if render_dir:
        _render_preview(render_dir, top_segs, top_pads, bot_segs, bot_pads, holes)

    print(f"Verified nets: {len(expected_nets)} | Min copper clearance: {min_clearance:.3f} mm ({min_pair_desc})")
    if errors:
        print("\n[DRC/LVS FAILED]:")
        for e in errors:
            print("  -", e)
        return False

    print("DRC & LVS check PASSED: 0 shorts, 0 opens, clearance >= 0.20 mm!")
    return True


def _render_preview(out_dir: str, top_segs, top_pads, bot_segs, bot_pads, holes):
    """Рендерит PNG-изображения Top и Bottom слоёв для визуальной инспекции."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return

    os.makedirs(out_dir, exist_ok=True)
    scale = 25  # 25 px / mm -> 1625 x 1125 px
    w_px, h_px = int(65 * scale), int(45 * scale)

    def to_px(x, y):
        return (x * scale, (45.0 - y) * scale)

    for name, segs, pads, trace_col, pad_col in [
        ("pcb_top.png", top_segs, top_pads, (220, 60, 60), (245, 190, 50)),
        ("pcb_bottom.png", bot_segs, bot_pads, (60, 140, 235), (120, 200, 255)),
    ]:
        img = Image.new("RGB", (w_px, h_px), (18, 36, 24))
        draw = ImageDraw.Draw(img)

        # Сетка 5 мм
        for gx in range(0, 66, 5):
            draw.line([to_px(gx, 0), to_px(gx, 45)], fill=(28, 52, 36), width=1)
        for gy in range(0, 46, 5):
            draw.line([to_px(0, gy), to_px(65, gy)], fill=(28, 52, 36), width=1)

        # Дорожки
        for s in segs:
            p1 = to_px(*s["p1"])
            p2 = to_px(*s["p2"])
            lw = max(2, int(round(s["w"] * scale)))
            draw.line([p1, p2], fill=trace_col, width=lw)
            r = lw / 2.0
            draw.ellipse([p1[0] - r, p1[1] - r, p1[0] + r, p1[1] + r], fill=trace_col)
            draw.ellipse([p2[0] - r, p2[1] - r, p2[0] + r, p2[1] + r], fill=trace_col)

        # Площадки
        for p in pads:
            cx, cy = to_px(p["x"], p["y"])
            rw, rh = (p["w"] * scale) / 2.0, (p["h"] * scale) / 2.0
            if p["shape"] == "C":
                draw.ellipse([cx - rw, cy - rw, cx + rw, cy + rw], fill=pad_col)
            else:
                draw.rectangle([cx - rw, cy - rh, cx + rw, cy + rh], fill=pad_col)

        # Отверстия
        for hx, hy, hd in holes:
            cx, cy = to_px(hx, hy)
            r = (hd * scale) / 2.0
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(10, 15, 12))

        out_path = os.path.join(out_dir, name)
        img.save(out_path)
        print(f"Rendered PCB preview: {out_path}")


if __name__ == "__main__":
    rdir = None
    if len(sys.argv) >= 3 and sys.argv[1] == "--render":
        rdir = sys.argv[2]
    ok = verify_pcb(render_dir=rdir)
    sys.exit(0 if ok else 1)
