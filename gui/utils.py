"""Utilidades compartidas por las pestañas."""
from __future__ import annotations

import math
from tkinter import ttk
from typing import Any, Dict, List, Optional

UNIDADES = {"C": "F", "L": "H", "R": "Ω", "X": "Ω", "Z": "Ω",
            "G": "S", "B": "S", "Y": "S", "D": "", "Q": ""}


def formato_si(valor: Optional[float], unidad: str = "") -> str:
    """1.0046e-8 -> '10.046 nF'."""
    if valor is None or not math.isfinite(valor):
        return "---"
    if valor == 0:
        return f"0.000 {unidad}".strip()
    prefijos = [(1e-12, "p"), (1e-9, "n"), (1e-6, "µ"), (1e-3, "m"),
                (1.0, ""), (1e3, "k"), (1e6, "M"), (1e9, "G")]
    escala, pref = prefijos[0]
    for e, p in prefijos:
        if abs(valor) >= e:
            escala, pref = e, p
    return f"{valor / escala:.3f} {pref}{unidad}".strip()


def flotante(texto: str) -> float:
    return float(texto.strip().replace(",", "."))


_COLUMNAS = [("id", "ID", 50), ("material", "Material", 150), ("tipo", "Tipo", 100),
             ("fecha", "Fecha", 140), ("puntos", "Puntos", 60),
             ("vac", "Vac [V]", 70), ("vdc", "Vdc [V]", 70),
             ("cal", "Calibración", 120)]


def crear_tabla_ensayos(padre, selectmode: str = "extended", alto: int = 14) -> ttk.Treeview:
    tree = ttk.Treeview(padre, columns=[c[0] for c in _COLUMNAS], show="headings",
                        selectmode=selectmode, height=alto)
    for cid, texto, ancho in _COLUMNAS:
        tree.heading(cid, text=texto)
        tree.column(cid, width=ancho, anchor="center")
    return tree


def llenar_tabla_ensayos(tree: ttk.Treeview, filas: List[Dict[str, Any]]) -> None:
    seleccion = set(tree.selection())
    tree.delete(*tree.get_children())
    for r in filas:
        cal = f"{r['tipo_trim']} / fx{r['fixture_code']}" if r["tipo_trim"] else "—"
        iid = str(r["id_ensayo"])
        tree.insert("", "end", iid=iid, values=(
            r["id_ensayo"], r["nombre"], r["tipo"], r["fecha"], r["n_puntos"],
            "" if r["nivel_vac"] is None else f"{r['nivel_vac']:g}",
            "" if r["polarizacion_vdc"] is None else f"{r['polarizacion_vdc']:g}",
            cal))
    presentes = [i for i in seleccion if tree.exists(i)]
    if presentes:
        tree.selection_set(presentes)