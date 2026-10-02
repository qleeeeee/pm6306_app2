"""Pestaña 3: comparación espectral (Bode, Nyquist, propiedades)."""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from analisis_materiales import AnalisisMateriales
from config import EVENTO_DATOS
from database import Database
from gui.utils import crear_tabla_ensayos, llenar_tabla_ensayos

log = logging.getLogger(__name__)


class TabComparacion(ttk.Frame):
    def __init__(self, master, db: Database):
        super().__init__(master, padding=8)
        self.db = db
        self.root = self.winfo_toplevel()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        arriba = ttk.LabelFrame(self, text="Seleccione uno o más ensayos (Ctrl/Shift)",
                                padding=6)
        arriba.grid(row=0, column=0, sticky="ew")
        arriba.columnconfigure(0, weight=1)
        self.tree = crear_tabla_ensayos(arriba, selectmode="extended", alto=6)
        sb = ttk.Scrollbar(arriba, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="ew")
        sb.grid(row=0, column=1, sticky="ns")

        barra = ttk.Frame(arriba)
        barra.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.var_modo = tk.StringVar(value="bode")
        for texto, valor in (("Bode (|Z| y fase)", "bode"),
                             ("Nyquist", "nyq"),
                             ("Propiedades (εr, ρ)", "prop")):
            ttk.Radiobutton(barra, text=texto, variable=self.var_modo,
                            value=valor, command=self._actualizar_opciones)\
                .pack(side="left", padx=(0, 8))
        self.var_igual = tk.BooleanVar(value=False)
        self.chk_igual = ttk.Checkbutton(barra, text="Ejes iguales (Nyquist)",
                                         variable=self.var_igual)
        self.chk_igual.pack(side="left", padx=8)
        ttk.Button(barra, text="Graficar", command=self._graficar).pack(side="left", padx=8)
        ttk.Button(barra, text="Guardar PNG", command=self._guardar_png).pack(side="left")

        cuerpo = ttk.Frame(self)
        cuerpo.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        cuerpo.columnconfigure(0, weight=1)
        cuerpo.rowconfigure(0, weight=1)
        self.fig = Figure(figsize=(8, 5.5), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, master=cuerpo)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        NavigationToolbar2Tk(self.canvas, cuerpo, pack_toolbar=False)\
            .grid(row=1, column=0, sticky="ew")

        self._actualizar_opciones()
        self.root.bind(EVENTO_DATOS, lambda e: self.refrescar(), add="+")
        self.refrescar()

    def _actualizar_opciones(self) -> None:
        """La casilla 'ejes iguales' solo aplica al modo Nyquist."""
        estado = "normal" if self.var_modo.get() == "nyq" else "disabled"
        self.chk_igual.configure(state=estado)

    def refrescar(self) -> None:
        llenar_tabla_ensayos(self.tree, self.db.listar_ensayos_resumen())

    def _graficar(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Comparación", "Seleccione al menos un ensayo.")
            return
        datos = {}
        try:
            for iid in sel:
                fila = self.tree.item(iid, "values")
                id_ens, nombre = int(fila[0]), fila[1]
                mat, ens, meds = self.db.obtener_mediciones(nombre, id_ens)
                datos[f"{nombre} #{id_ens}"] = AnalisisMateriales.calcular_propiedades(
                    mat, ens, meds)
            modo = self.var_modo.get()
            if modo == "bode":
                AnalisisMateriales.graficar_bode(datos, fig=self.fig)
            elif modo == "nyq":
                AnalisisMateriales.graficar_nyquist(
                    datos, fig=self.fig, aspect_igual=self.var_igual.get())
            else:
                AnalisisMateriales.graficar_propiedades(datos, fig=self.fig)
            self.canvas.draw_idle()
        except Exception as exc:
            log.exception("Fallo al graficar")
            messagebox.showerror("Comparación", str(exc))

    def _guardar_png(self) -> None:
        ruta = filedialog.asksaveasfilename(defaultextension=".png",
                                            filetypes=[("PNG", "*.png")])
        if ruta:
            self.fig.savefig(ruta, dpi=150)