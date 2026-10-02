"""Punto de entrada: python main.py"""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

from config import DB_RUTA, LOG_RUTA
from database import Database
from gui.tab_basedatos import TabBaseDatos
from gui.tab_calibracion import TabCalibracion
from gui.tab_comparacion import TabComparacion
from gui.tab_historial import TabHistorial
from gui.tab_medicion import TabMedicion


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Caracterización de materiales - Fluke PM6306")
        self.geometry("1300x900")
        self.minsize(1100, 760)

        db = Database(DB_RUTA)
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.t_med = TabMedicion(nb, db)
        nb.add(self.t_med, text="  Medición  ")
        nb.add(TabHistorial(nb, db), text="  Historial  ")
        nb.add(TabComparacion(nb, db), text="  Comparación  ")
        nb.add(TabCalibracion(nb, db), text="  Calibración  ")
        nb.add(TabBaseDatos(nb, db), text="  Base de datos  ")
        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)

    def _al_cerrar(self) -> None:
        self.t_med.cerrar()
        self.destroy()


def main() -> None:
    logging.basicConfig(
        filename=LOG_RUTA, level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    App().mainloop()


if __name__ == "__main__":
    main()