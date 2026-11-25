#!/usr/bin/env python3
"""
Генератор проекта печатной платы в формате KiCad PCB (.kicad_pcb).
Позволяет открыть проект в KiCad, просматривать 3D-модель и выполнять встроенный DRC.
"""

import os

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "pcb", "SimPedal_Brake.kicad_pcb")

KICAD_TEMPLATE = """(kicad_pcb (version 20211014) (generator "SimPedal Generator")

  (general
    (thickness 1.6)
  )

  (paper "A4")
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (36 "B.SilkS" user "B.Silkscreen")
    (37 "F.SilkS" user "F.Silkscreen")
    (38 "B.Mask" user)
    (39 "F.Mask" user)
    (44 "Edge.Cuts" user)
  )

  (setup
    (pad_to_mask_clearance 0.05)
    (pcbplotparams
      (layerselection 0x00010fc_ffffffff)
      (usegerberextensions true)
      (usegerberattributes true)
    )
  )

  (net 0 "")
  (net 1 "GND_SYS")
  (net 2 "DVDD")
  (net 3 "AVDD")
  (net 4 "SPI0_SCK")
  (net 5 "SPI0_MOSI")
  (net 6 "SPI0_MISO")
  (net 7 "SPI0_CS")
  (net 8 "ADC_DRDY")
  (net 9 "ADC_AIN0")
  (net 10 "ADC_AIN1")

  ;; --- КОНТУР ПЛАТЫ 65x45 мм ---
  (gr_line (start 2.0 0.0) (end 63.0 0.0) (layer "Edge.Cuts") (width 0.15))
  (gr_line (start 65.0 2.0) (end 65.0 43.0) (layer "Edge.Cuts") (width 0.15))
  (gr_line (start 63.0 45.0) (end 2.0 45.0) (layer "Edge.Cuts") (width 0.15))
  (gr_line (start 0.0 43.0) (end 0.0 2.0) (layer "Edge.Cuts") (width 0.15))
  (gr_arc (start 2.0 0.0) (mid 0.58 0.58) (end 0.0 2.0) (layer "Edge.Cuts") (width 0.15))
  (gr_arc (start 65.0 2.0) (mid 64.42 0.58) (end 63.0 0.0) (layer "Edge.Cuts") (width 0.15))
  (gr_arc (start 63.0 45.0) (mid 64.42 44.42) (end 65.0 43.0) (layer "Edge.Cuts") (width 0.15))
  (gr_arc (start 0.0 43.0) (mid 0.58 44.42) (end 2.0 45.0) (layer "Edge.Cuts") (width 0.15))

  ;; --- КРЕПЕЖНЫЕ ОТВЕРСТИЯ M3 ---
  (module "MountingHole:MountingHole_3.2mm_M3" (layer "F.Cu") (at 4.0 4.0)
    (pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2) (drill 3.2) (layers *.Mask))
  )
  (module "MountingHole:MountingHole_3.2mm_M3" (layer "F.Cu") (at 61.0 4.0)
    (pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2) (drill 3.2) (layers *.Mask))
  )
  (module "MountingHole:MountingHole_3.2mm_M3" (layer "F.Cu") (at 61.0 41.0)
    (pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2) (drill 3.2) (layers *.Mask))
  )
  (module "MountingHole:MountingHole_3.2mm_M3" (layer "F.Cu") (at 4.0 41.0)
    (pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2) (drill 3.2) (layers *.Mask))
  )

  ;; --- ШЕЛКОГРАФИЯ ---
  (gr_text "SimPedal Brake Carrier v1.1" (at 32.5 42.5) (layer "F.SilkS")
    (effects (font (size 1.2 1.2) (thickness 0.15)))
  )
  (gr_text "OpenSimHW" (at 15.0 2.5) (layer "F.SilkS")
    (effects (font (size 1.0 1.0) (thickness 0.15)))
  )
)
"""


def generate_kicad():
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(KICAD_TEMPLATE)
    print(f"Generated KiCad PCB file: {OUTPUT_FILE}")


if __name__ == "__main__":
    generate_kicad()
