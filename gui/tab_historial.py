"""Pestaña 2: historial de ensayos (ver, exportar a CSV, eliminar)."""
from __future__ import annotations

import logging
from tkinter import filedialog, messagebox, ttk

from config import EVENTO_DATOS
from database import Database
from gui.utils import crear_tabla_ensayos, llenar_tabla_ensayos

log = logging.getLogger(__name__)


class TabHistorial(ttk.Frame):
    def __init__(self, master, db: Database):
        super().__init__(master, padding=8)
        self.db = db
        self.root = self.winfo_toplevel()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.tree = crear_tabla_ensayos(self, selectmode="extended", alto=20)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

        barra = ttk.Frame(self)
        barra.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(barra, text="Actualizar", command=self.refrescar).pack(side="left")
        ttk.Button(barra, text="Exportar CSV (1 ensayo)", command=self._exportar).pack(side="left", padx=6)
        ttk.Button(barra, text="Eliminar seleccionados", command=self._eliminar).pack(side="left")
        self.lbl = ttk.Label(barra, text="")
        self.lbl.pack(side="right")

        self.root.bind(EVENTO_DATOS, lambda e: self.refrescar(), add="+")
        self.refrescar()

    def refrescar(self) -> None:
        filas = self.db.listar_ensayos_resumen()
        llenar_tabla_ensayos(self.tree, filas)
        self.lbl.configure(text=f"{len(filas)} ensayo(s)")

    def _exportar(self) -> None:
        sel = self.tree.selection()
        if len(sel) != 1:
            messagebox.showinfo("Exportar", "Seleccione exactamente un ensayo.")
            return
        id_ens = int(sel[0])
        ruta = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=f"ensayo_{id_ens}.csv",
            filetypes=[("CSV", "*.csv")])
        if not ruta:
            return
        try:
            n = self.db.exportar_csv(id_ens, ruta)
            messagebox.showinfo("Exportar", f"{n} filas exportadas a:\n{ruta}")
        except Exception as exc:
            log.exception("Fallo al exportar")
            messagebox.showerror("Exportar", str(exc))

    def _eliminar(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        if not messagebox.askyesno(
                "Eliminar", f"¿Eliminar {len(sel)} ensayo(s) y todas sus mediciones?\n"
                            "Esta acción no se puede deshacer."):
            return
        try:
            for iid in sel:
                self.db.eliminar_ensayo(int(iid))
        except Exception as exc:
            log.exception("Fallo al eliminar")
            messagebox.showerror("Eliminar", str(exc))
        self.root.event_generate(EVENTO_DATOS, when="tail")