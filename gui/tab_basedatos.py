"""Pestaña 5: explorador de solo lectura de las tablas de la base de datos."""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from config import EVENTO_DATOS
from database import TABLAS_VALIDAS, Database

log = logging.getLogger(__name__)
LIMITE_FILAS = 5000


def _formato_celda(valor: Any) -> str:
    if valor is None:
        return "NULL"
    if isinstance(valor, float):
        return f"{valor:.6g}"
    return str(valor)


class TabBaseDatos(ttk.Frame):
    def __init__(self, master, db: Database):
        super().__init__(master, padding=8)
        self.db = db
        self.root = self.winfo_toplevel()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        barra = ttk.Frame(self)
        barra.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(barra, text="Tabla:").pack(side="left")
        self.var_tabla = tk.StringVar(value=TABLAS_VALIDAS[0])
        cb = ttk.Combobox(barra, textvariable=self.var_tabla, values=list(TABLAS_VALIDAS),
                          state="readonly", width=16)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refrescar())
        ttk.Button(barra, text="Refrescar", command=self.refrescar).pack(side="left")
        self.lbl_info = ttk.Label(barra, text="")
        self.lbl_info.pack(side="right")

        self.tree = ttk.Treeview(self, show="headings", selectmode="browse")
        sy = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        sx = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        sy.grid(row=1, column=1, sticky="ns")
        sx.grid(row=2, column=0, sticky="ew")

        self.root.bind(EVENTO_DATOS, lambda e: self.refrescar(), add="+")
        self.refrescar()

    def refrescar(self) -> None:
        try:
            columnas, filas, total = self.db.obtener_tabla(self.var_tabla.get(),
                                                           LIMITE_FILAS)
        except Exception as exc:
            log.exception("Fallo al leer tabla")
            messagebox.showerror("Base de datos", str(exc))
            return

        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = columnas
        for col in columnas:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=max(90, 9 * len(col) + 20),
                             anchor="center", stretch=False)
        for fila in filas:
            self.tree.insert("", "end", values=[_formato_celda(v) for v in fila])

        nota = f"{total} fila(s)"
        if total > len(filas):
            nota += f" — mostrando las {len(filas)} más recientes"
        self.lbl_info.configure(text=nota)