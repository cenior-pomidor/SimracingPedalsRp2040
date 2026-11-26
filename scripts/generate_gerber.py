#!/usr/bin/env python3
"""Экспорт проверенной платы KiCad 9 в комплект Gerber/Excellon для изготовителя."""
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

from verify_pcb import GERBER_DIR, PCB_DIR, PROJECT_NAME, verify_pcb


def generate_gerbers():
    source = Path(PCB_DIR) / "SimPedal_Brake.kicad_pcb"
    target = Path(GERBER_DIR)
    target.mkdir(exist_ok=True)
    with TemporaryDirectory() as temp:
        stage = Path(temp)
        subprocess.run(["kicad-cli", "pcb", "drc", "--exit-code-violations", "-o",
                        str(stage/"drc.txt"), str(source)], check=True)
        subprocess.run(["kicad-cli", "pcb", "export", "gerbers", "-o", temp,
                        "--layers", "F.Cu,B.Cu,F.Mask,B.Mask,F.Silkscreen,B.Silkscreen,Edge.Cuts",
                        "--use-drill-file-origin", "--disable-aperture-macros", str(source)], check=True)
        subprocess.run(["kicad-cli", "pcb", "export", "drill", "-o", temp,
                        "--drill-origin", "plot", "--excellon-zeros-format", "decimal", str(source)], check=True)
        files = []
        for suffix in ("-F_Cu.gtl", "-B_Cu.gbl", "-F_Mask.gts", "-B_Mask.gbs",
                       "-F_Silkscreen.gto", "-B_Silkscreen.gbo", "-Edge_Cuts.gm1", ".drl"):
            output = stage / (PROJECT_NAME + suffix.replace(".gm1", ".gko"))
            (stage / (source.stem + suffix)).rename(output)
            files.append(output)
        if not verify_pcb(gerber_dir=stage):
            raise ValueError("Проверка производственных слоёв не пройдена")
        archive = stage / f"{PROJECT_NAME}_Gerber.zip"
        with ZipFile(archive, "w", ZIP_DEFLATED) as zf:
            for output in files:
                zf.write(output, output.name)
        for output in [*files, archive]:
            shutil.copyfile(output, target / output.name)
        print(target / archive.name)
        return target / archive.name


if __name__ == "__main__":
    generate_gerbers()
