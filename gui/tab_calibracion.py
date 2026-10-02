"""Pestaña 4: registro de calibraciones (TRIM y fixture)."""
from __future__ import annotations

import logging
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from config import EVENTO_DATOS
from database import Database

log = logging.getLogger(__name__)


class TabCalibracion(ttk.Frame):
    def __init__(self, master, db: Database):
        super().__init__(master, padding=8)
        self.db = db
        self.root = self.winfo_toplevel()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        f = ttk.LabelFrame(self, text="Registrar calibración", padding=8)
        f.grid(row=0, column=0, sticky="ew")
        self.var_trim = tk.StringVar(value="TRIM 1")
        self.var_fixture = tk.StringVar(value="0")
        ttk.Label(f, text="Tipo").grid(row=0, column=0, padx=(0, 4))
        ttk.Combobox(f, textvariable=self.var_trim, values=["TRIM 1", "TRIM A"],
                     state="readonly", width=10).grid(row=0, column=1)
        ttk.Label(f, text="Fixture (0-10)").grid(row=0, column=2, padx=(12, 4))
        ttk.Spinbox(f, from_=0, to=10, textvariable=self.var_fixture, width=5)\
            .grid(row=0, column=3)
        ttk.Button(f, text="Registrar", command=self._registrar)\
            .grid(row=0, column=4, padx=12)
        ttk.Button(f, text="Eliminar seleccionada", command=self._eliminar)\
            .grid(row=0, column=5)

        cols = [("id", "ID", 60), ("trim", "Tipo", 100), ("fixture", "Fixture", 80),
                ("fecha", "Fecha", 160), ("usos", "Ensayos que la usan", 150)]
        self.tree = ttk.Treeview(self, columns=[c[0] for c in cols], show="headings",
                                 selectmode="browse")
        for cid, txt, ancho in cols:
            self.tree.heading(cid, text=txt)
            self.tree.column(cid, width=ancho, anchor="center")
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        self.root.bind(EVENTO_DATOS, lambda e: self.refrescar(), add="+")
        self.refrescar()

    def refrescar(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for c in self.db.listar_calibraciones():
            self.tree.insert("", "end", iid=str(c["id_calibracion"]), values=(
                c["id_calibracion"], c["tipo_trim"], c["fixture_code"], c["fecha"],
                c["n_ensayos"]))

    def _registrar(self) -> None:
        try:
            fx = int(self.var_fixture.get())
            if not 0 <= fx <= 10:
                raise ValueError("El fixture debe estar entre 0 y 10.")
            self.db.guardar_calibracion(self.var_trim.get(), fx)
        except ValueError as exc:
            messagebox.showerror("Calibración", str(exc))
            return
        self.root.event_generate(EVENTO_DATOS, when="tail")

    def _eliminar(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        try:
            self.db.eliminar_calibracion(int(sel[0]))
        except sqlite3.IntegrityError:
            messagebox.showwarning(
                "Calibración", "No se puede eliminar: hay ensayos que usan esta calibración.")
            return
        except Exception as exc:
            log.exception("Fallo al eliminar calibración")
            messagebox.showerror("Calibración", str(exc))
            return
        self.root.event_generate(EVENTO_DATOS, when="tail")