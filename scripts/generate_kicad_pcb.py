#!/usr/bin/env python3
"""Собрать редактируемую плату из карты выводов, корпусов и маршрутов в мм.

Система координат разводки совпадает с производственными файлами: начало в
левом нижнем углу. В KiCad это точка привязки сверления (100, 100), Y вниз.
"""
from collections import defaultdict
import json
from pathlib import Path

PCB = Path(__file__).resolve().parents[1] / "pcb"
ORIGIN = 100
VALUES = {
    "R1": "100R", "R2": "100R", "R3": "33R", "R4": "33R", "R5": "33R", "R6": "10k",
    "C1": "100nF", "C2": "10uF", "C3": "100nF", "C4": "10uF", "C5": "1uF",
    "C6": "100nF", "C7": "100nF", "FB1": "600R @ 100MHz",
    "TVS1": "PESD5V0S1BA", "TVS2": "PESD5V0S1BA",
    "J1": "RP2040-Zero", "J2": "ADS1220", "J3": "Load cell 3.50mm",
}


def xy(point):
    x, y = point
    return f"{ORIGIN+x:.4f} {ORIGIN-y:.4f}"


def silk(text, pos, side="F", size=0.9):
    mirror = "(justify mirror)" if side == "B" else ""
    return (f'(gr_text "{text}" (at {xy(pos)}) (layer "{side}.SilkS") '
            f'(effects (font (size {size} {size}) (thickness 0.15)) {mirror}))')


def footprint(ref, pins, net_ids):
    pins = sorted(pins, key=lambda pin: (-pin["y"], pin["x"]))
    mounting = ref.startswith("H")
    connector = ref.startswith("J")
    tht = mounting or connector
    tvs = ref.startswith("TVS")
    side = "B" if tvs else "F"
    kind = "Mount_M3" if mounting else VALUES[ref] if connector else "SOD-323" if tvs else "0805"
    cx = (min(p["x"] for p in pins) + max(p["x"] for p in pins)) / 2
    cy = (min(p["y"] for p in pins) + max(p["y"] for p in pins)) / 2
    # Размещаем обозначения вне маски контактных площадок и соседних корпусов.
    refpos = {"J1": (16, 41), "J2": (43, 38), "J3": (61, 37.5), "R6": (26.8, 22.5),
              "C6": (49.2, 40.6), "C7": (49.2, 34.8), "TVS1": (57.5, 31), "TVS2": (57.5, 27.5),
              "R4": (26.1, 31.95)}
    rx, ry = refpos.get(ref, (cx, cy+1.3))
    if ref in ("C1", "C2", "C3", "C4", "C5"):
        rx, ry = cx, cy-2.65
    flip = "(justify mirror)" if tvs else ""
    hide = "(hide yes)" if mounting else ""
    value = VALUES.get(ref, "Mounting hole M3")
    lines = [f'(footprint "{kind}" (layer "{side}.Cu") (at {xy((cx,cy))})',
             f'  (property "Reference" "{ref}" (at {rx-cx:.3f} {cy-ry:.3f}) (layer "{side}.SilkS") '
             f'{hide} (effects (font (size 0.85 0.85) (thickness 0.15)) {flip}))',
             f'  (property "Value" "{value}" (at 0 0) (layer "{side}.Fab") (hide yes) '
             '(effects (font (size 1 1) (thickness 0.15))))',
             '  (attr through_hole)' if tht else '  (attr smd)']
    for i, p in enumerate(pins, 1):
        net = p["net"]
        if tht:
            dia, drill = (5.4,3.2) if mounting else (2.2,1.3) if ref == "J3" else (1.6,1)
            shape = "rect" if i == 1 and not mounting else "circle"
            spec = f'thru_hole {shape}'
            details = f'(size {dia} {dia}) (drill {drill}) (layers "*.Cu" "*.Mask")'
            number = i
        else:
            number = p["label"].split(".")[1]
            vertical = abs(pins[0]["x"] - pins[1]["x"]) < .01
            w,h = (1, .95) if tvs else (1.2,1.1) if vertical else (1.1,1.2)
            spec = "smd rect"
            details = f'(size {w} {h}) (layers "{side}.Cu" "{side}.Paste" "{side}.Mask")'
        at = f'{p["x"]-cx:.4f} {cy-p["y"]:.4f}'
        lines.append(f'  (pad "{number}" {spec} (at {at}) {details} (net {net_ids[net]} "{net}"))')
    return "\n".join([*lines, ")"])


def routes():
    # Все ответвления разбиты в точках присоединения: соединения видны и в редакторе KiCad.
    paths = []
    def route(net, side, width, *points):
        paths.extend((net, side, width, a, b) for a, b in zip(points, points[1:]))
    route("SPI_SCK_RP","F",.25,(23.74,33.42),(28.15,33.42))
    route("SPI_SCK_ADC","F",.25,(30.05,33.42),(32,33.42))
    route("SPI_SCK_ADC","B",.25,(32,33.42),(32,28.34),(34.5,28.34))
    route("SPI_MOSI_RP","F",.25,(23.74,30.88),(28.15,30.88))
    route("SPI_MOSI_ADC","F",.25,(30.05,30.88),(34.5,30.88))
    route("SPI_MISO_RP","F",.25,(23.74,28.34),(26.55,28.34))
    route("SPI_MISO_ADC","F",.25,(28.45,28.34),(30.2,28.34))
    route("SPI_MISO_ADC","B",.25,(30.2,28.34),(30.2,34.8),(33,34.8),(33,33.42),(34.5,33.42))
    route("SPI_CS","F",.25,(23.74,25.8),(29.1,25.8),(34.5,25.8))
    route("SPI_CS","F",.25,(29.1,24.05),(29.1,25.8))
    route("ADC_DRDY","B",.25,(34.5,35.96),(34.5,37.6),(26.2,37.6),(26.2,23.26),(23.74,23.26))

    route("DVDD","F",.5,(8.5,33.42),(6.3,33.42),(6.3,13.5),(30,13.5),(31.5,13.5),(33,13.5),(36.55,13.5))
    route("DVDD","F",.45,(31.5,13.5),(31.5,20.72),(34.5,20.72))
    route("DVDD","F",.30,(31.5,20.72),(29.1,20.72),(29.1,22.15))
    for x in (30,33):
        route("DVDD","F",.45,(x,12.15),(x,13.5))
    route("AVDD","F",.5,(38.45,13.5),(42,13.5),(45,13.5),(48,13.5),(50.2,13.5),(50.2,20.72),(52.28,20.72))
    for x in (42,45,48):
        route("AVDD","F",.45,(x,12.15),(x,13.5))
    route("AVDD","B",.5,(52.28,20.72),(56.5,20.72),(56.5,34.5),(61,34.5))
    route("LC_SP","F",.3,(61,31),(58.6,31),(58.6,35.96),(57.35,35.96))
    route("LC_SN","F",.3,(61,27.5),(57.35,27.5))
    route("ADC_AIN0","F",.3,(55.45,35.96),(52.28,35.96),(52.28,39.2),(50.15,39.2))
    route("ADC_AIN1","F",.3,(55.45,27.5),(54,27.5),(54,33.42),(52.28,33.42),(50.15,33.42))

    route("GND_SYS","B",.7,(8.5,35.96),(4.2,35.96),(4.2,9),(30,9),(33,9),(34.5,9),(34.5,18.18))
    route("GND_SYS","F",.3,(34.5,23.26),(37.2,23.26),(37.2,18.18),(34.5,18.18))
    route("GND_SYS","B",.8,(34.5,18.18),(46.5,18.18),(52.28,18.18))
    route("GND_SYS","B",.7,(42,9),(45,9),(48,9),(52.28,9),(52.28,18.18),(61,18.18),(61,24))
    for x in (30,33,42,45,48):
        route("GND_SYS","F",.45,(x,10.25),(x,9))
    for y in (33.42,39.2):
        route("GND_SYS","F",.35,(48.25,y),(46.5,y))
    route("GND_SYS","B",.5,(46.5,39.2),(46.5,33.42),(46.5,18.18))

    # TVS на обороте под клеммником: короткий отвод от каждого сигнала до RC.
    route("LC_SP","B",.3,(61,31),(63.1,31))
    route("LC_SN","B",.3,(61,27.5),(63.1,27.5))
    route("GND_SYS","B",.4,(63.1,28.9),(64.25,28.9),(64.25,25.35),(64.25,24),(61,24))
    route("GND_SYS","B",.4,(63.1,25.35),(64.25,25.35))
    return paths


def generate_kicad():
    nets = json.loads((PCB/"netlist_expected.json").read_text(encoding="utf-8"))
    ids = {name: i for i, name in enumerate(nets, 1)}
    lines = ['(kicad_pcb (version 20240108) (generator "simpedal")',
             '  (general (thickness 1.6)) (paper "A4")',
             '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (34 "B.Paste" user) (35 "F.Paste" user)'
             ' (36 "B.SilkS" user "B.Silkscreen") (37 "F.SilkS" user "F.Silkscreen")'
             ' (38 "B.Mask" user) (39 "F.Mask" user) (44 "Edge.Cuts" user) (48 "B.Fab" user) (49 "F.Fab" user))',
             f'  (setup (pad_to_mask_clearance 0.07) (aux_axis_origin {ORIGIN} {ORIGIN}))', '  (net 0 "")']
    parts = defaultdict(list)
    for net, pins in nets.items():
        lines.append(f'  (net {ids[net]} "{net}")')
        for pin in pins:
            label = pin["label"]
            if label == "VIA":
                lines.append(f'  (via (at {xy((pin["x"],pin["y"]))}) (size 0.85) (drill 0.4) '
                             f'(layers "F.Cu" "B.Cu") (net {ids[net]}))')
                continue
            if label.startswith("M3_"):
                ref = f'H{int(label[3:])+1}'
            elif label.startswith("RP_"):
                ref = "J1"
            elif label.startswith("ADS_"):
                ref = "J2"
            elif label.startswith("J3_"):
                ref = "J3"
            else:
                ref = label.split(".")[0]
            parts[ref].append(dict(pin, net=net))
    for ref, pins in parts.items():
        lines.append(footprint(ref, pins, ids))
    for net, side, width, a, b in routes():
        lines.append(f'  (segment (start {xy(a)}) (end {xy(b)}) (width {width}) (layer "{side}.Cu") (net {ids[net]}))')
    outline = [(2,0),(63,0),(65,2),(65,43),(63,45),(2,45),(0,43),(0,2),(2,0)]
    for a,b in zip(outline,outline[1:]):
        lines.append(f'  (gr_line (start {xy(a)}) (end {xy(b)}) (stroke (width .1) (type default)) (layer "Edge.Cuts"))')
    lines.extend([silk("RP2040-Zero / USB", (16,43)), silk("ADS1220", (43,40)),
                  silk("SimPedal Brake 1.2", (33,3), size=1.5), silk("OpenSimHW", (32,5)),
                  silk("TVS / LOAD CELL", (55,12), "B")])
    for name, y in zip(("E+","S+","S-","E-"), (34.5,31,27.5,24)):
        lines.append(silk(name,(63.5,y)))
    for x, y0, labels in [(11.3,38.5,"5V GND 3V3 29 28 27 26 15 14"),
                          (21,38.5,"0 1 SCK MOSI MISO CS DRDY 7 8"),
                          (38.2,35.96,"DRDY MISO MOSI SCLK CS CLK DVDD DGND"),
                          (48.4,35.96,"AIN0 AIN1 AIN2 AIN3 REFN REFP AVDD AGND")]:
        for i, text in enumerate(labels.split()):
            # На том же уровне, что и C7, легенду сдвигаем от площадок.
            sx = 45.0 if text == "AIN1" else x
            lines.append(silk(text, (sx, y0-2.54*i), size=.85))
    (PCB/"SimPedal_Brake.kicad_pcb").write_text("\n".join([*lines, ")",""]), encoding="utf-8")


if __name__ == "__main__":
    generate_kicad()
