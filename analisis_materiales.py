"""
Análisis comparativo: Bode, Nyquist y dispersión de propiedades intrínsecas
(permitividad relativa y resistividad) desde la base de datos.

Uso por línea de comandos:
    python analisis_materiales.py                       # selección interactiva
    python analisis_materiales.py -m Teflon FR4         # por nombre
    python analisis_materiales.py -m Teflon FR4 -o out  # guarda PNG, sin mostrar
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import matplotlib.pyplot as plt
import numpy as np

from database import DB_POR_DEFECTO, Database

EPS0 = 8.8541878128e-12  # F/m


def _fmt_f(f: float) -> str:
    """1000 -> '1 kHz'"""
    for esc, pref in ((1e6, "M"), (1e3, "k"), (1.0, "")):
        if abs(f) >= esc:
            return f"{f / esc:g} {pref}Hz"
    return f"{f:g} Hz"


@dataclass
class DatosMaterial:
    material: dict
    ensayo: dict
    f: np.ndarray
    z: np.ndarray
    fase: np.ndarray
    c: np.ndarray
    r: np.ndarray
    eps_r: np.ndarray
    rho: np.ndarray


class AnalisisMateriales:
    def __init__(self, db: Optional[Database] = None, ruta_db: str = DB_POR_DEFECTO):
        self.db = db or Database(ruta_db)

    # ------------------------------------------------------------- cálculo
    @staticmethod
    def calcular_propiedades(material: dict, ensayo: dict,
                             mediciones: List[dict]) -> DatosMaterial:
        n = len(mediciones)
        f = np.array([m["frecuencia_hz"] for m in mediciones], dtype=float)
        z = np.array([m["impedancia_z"] if m["impedancia_z"] is not None else np.nan
                      for m in mediciones], dtype=float)
        fase = np.array([m["fase_deg"] if m["fase_deg"] is not None else np.nan
                         for m in mediciones], dtype=float)
        c = np.full(n, np.nan)
        r = np.full(n, np.nan)
        for i, m in enumerate(mediciones):
            for par, val in ((m["parametro_dom"], m["valor_dom"]),
                             (m["parametro_sec"], m["valor_sec"])):
                if par is None or val is None:
                    continue
                if par.upper().startswith("C"):
                    c[i] = val
                elif par.upper().startswith("R"):
                    r[i] = val
        area, d = material["area_m2"], material["espesor_m"]
        eps_r = c * d / (EPS0 * area)       # eps_r ~ C*d / (eps0*A)
        rho = r * area / d                  # rho = R*A/d  [ohm*m]
        return DatosMaterial(material, ensayo, f, z, fase, c, r, eps_r, rho)

    def cargar(self, nombres: List[str],
               ensayos: Optional[Dict[str, int]] = None) -> Dict[str, DatosMaterial]:
        ensayos = ensayos or {}
        datos: Dict[str, DatosMaterial] = {}
        for nombre in nombres:
            mat, ens, meds = self.db.obtener_mediciones(nombre, ensayos.get(nombre))
            datos[nombre] = self.calcular_propiedades(mat, ens, meds)
        return datos

    # ------------------------------------------------------------- gráficos
    @staticmethod
    def _preparar(fig=None):
        if fig is None:
            fig = plt.figure(figsize=(9, 7))   # uso por línea de comandos
        else:
            fig.clear()                        # uso incrustado en la GUI
        ax1, ax2 = fig.subplots(2, 1, sharex=True)
        return fig, ax1, ax2

    @staticmethod
    def graficar_bode(datos: Dict[str, DatosMaterial], fig=None):
        fig, ax1, ax2 = AnalisisMateriales._preparar(fig)
        for nombre, d in datos.items():
            ax1.loglog(d.f, d.z, "o-", label=nombre)
            ax2.semilogx(d.f, d.fase, "o-", label=nombre)
        ax1.set_ylabel("|Z| [Ω]")
        ax1.set_title("Diagrama de Bode")
        ax2.set_ylabel("Fase Φ [°]")
        ax2.set_xlabel("Frecuencia [Hz]")
        for ax in (ax1, ax2):
            ax.grid(True, which="both", alpha=0.3)
            ax.legend()
        fig.tight_layout()
        return fig

    @staticmethod
    def graficar_propiedades(datos: Dict[str, DatosMaterial], fig=None):
        fig, ax1, ax2 = AnalisisMateriales._preparar(fig)
        hay_eps = hay_rho = False
        for nombre, d in datos.items():
            if np.isfinite(d.eps_r).any():
                ax1.semilogx(d.f, d.eps_r, "o-", label=nombre)
                hay_eps = True
            if np.isfinite(d.rho).any():
                ax2.loglog(d.f, d.rho, "s-", label=nombre)
                hay_rho = True
        ax1.set_ylabel("Permitividad relativa εr [-]")
        ax1.set_title("Dispersión de propiedades intrínsecas")
        ax2.set_ylabel("Resistividad ρ [Ω·m]")
        ax2.set_xlabel("Frecuencia [Hz]")
        for ax, hay in ((ax1, hay_eps), (ax2, hay_rho)):
            ax.grid(True, which="both", alpha=0.3)
            if hay:
                ax.legend()
            else:
                ax.text(0.5, 0.5, "Sin datos (parámetro no medido)",
                        transform=ax.transAxes, ha="center", va="center")
        fig.tight_layout()
        return fig

    @staticmethod
    def graficar_nyquist(datos: Dict[str, DatosMaterial], fig=None,
                         aspect_igual: bool = False):
        """Nyquist: eje X = Z' = |Z|cos(Φ); eje Y = -Z'' = -|Z|sen(Φ).
        Con Φ<0 (capacitivo) queda -Z''>0, la convención usual en dieléctricos."""
        if fig is None:
            fig = plt.figure(figsize=(8, 6))     # uso por línea de comandos
        else:
            fig.clear()                          # uso incrustado en la GUI
        ax = fig.add_subplot(111)
        hay = False
        for nombre, d in datos.items():
            ok = np.isfinite(d.z) & np.isfinite(d.fase)
            if not ok.any():
                continue
            hay = True
            f, z, phi = d.f[ok], d.z[ok], np.radians(d.fase[ok])
            re, im = z * np.cos(phi), -z * np.sin(phi)
            linea, = ax.plot(re, im, "^-", markersize=5, label=nombre)
            for idx in (0, -1):                  # etiquetar f mínima y máxima
                ax.annotate(_fmt_f(f[idx]), (re[idx], im[idx]),
                            textcoords="offset points", xytext=(6, 6),
                            fontsize=8, color=linea.get_color())
        ax.axhline(0, color="gray", lw=0.8)
        ax.set_xlabel("Z' (parte real) [Ω]")
        ax.set_ylabel("-Z'' (parte imaginaria) [Ω]")
        ax.set_title("Diagrama de Nyquist")
        ax.grid(True, ls=":", alpha=0.6)
        if hay:
            ax.legend()
            if aspect_igual:
                ax.set_aspect("equal", adjustable="datalim")
        else:
            ax.text(0.5, 0.5, "Sin datos de |Z| y fase", transform=ax.transAxes,
                    ha="center", va="center")
        fig.tight_layout()
        return fig


# ------------------------------------------------------------------ CLI
def _seleccion_interactiva(db: Database) -> List[str]:
    mats = db.listar_materiales()
    if not mats:
        raise SystemExit("La base de datos no contiene materiales.")
    print("Materiales disponibles:")
    for i, m in enumerate(mats, 1):
        print(f"  {i}. {m['nombre']}  ({m['tipo']}, A={m['area_m2']:g} m², d={m['espesor_m']:g} m)")
    txt = input("Seleccione uno o más (números separados por coma): ")
    try:
        idx = [int(t) - 1 for t in txt.split(",") if t.strip()]
        return [mats[i]["nombre"] for i in idx]
    except (ValueError, IndexError):
        raise SystemExit("Selección inválida.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Comparación espectral de materiales")
    ap.add_argument("-m", "--materiales", nargs="+", help="Nombres de materiales")
    ap.add_argument("--db", default=DB_POR_DEFECTO)
    ap.add_argument("-o", "--salida", help="Carpeta donde guardar PNG (no muestra ventanas)")
    args = ap.parse_args()

    an = AnalisisMateriales(ruta_db=args.db)
    nombres = args.materiales or _seleccion_interactiva(an.db)
    if len(nombres) < 2:
        print("Aviso: se recomienda comparar al menos dos materiales.")
    try:
        datos = an.cargar(nombres)
    except KeyError as exc:
        raise SystemExit(str(exc))

    fig_b = an.graficar_bode(datos)
    fig_n = an.graficar_nyquist(datos)
    fig_p = an.graficar_propiedades(datos)
    if args.salida:
        os.makedirs(args.salida, exist_ok=True)
        fig_b.savefig(os.path.join(args.salida, "bode.png"), dpi=150)
        fig_n.savefig(os.path.join(args.salida, "nyquist.png"), dpi=150)
        fig_p.savefig(os.path.join(args.salida, "propiedades.png"), dpi=150)
        print(f"Gráficos guardados en {args.salida}")
    else:
        plt.show()


if __name__ == "__main__":
    main()