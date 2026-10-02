"""Pestaña 1: conexión, medición, display digital y Bode en vivo."""
from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from config import EVENTO_DATOS, PUERTO_SIMULADO, TIPOS_MATERIAL
from database import Database
from gui.utils import UNIDADES, flotante, formato_si
from pm6306_driver import (FRECUENCIAS_NORMALIZADAS, BarridoCancelado, Medicion,
                           PM6306, PM6306Simulado)

log = logging.getLogger(__name__)

BG_DISPLAY = "#0a0f0b"
FG_DIGITO = "#39ff14"
FG_ETIQUETA = "#7fbf7f"
FUENTE_GRANDE = ("Consolas", 36, "bold")
FUENTE_MEDIA = ("Consolas", 22, "bold")
FUENTE_ETIQ = ("Consolas", 11)
SIN_CAL = "Sin calibración"


class TabMedicion(ttk.Frame):
    def __init__(self, master, db: Database):
        super().__init__(master)
        self.db = db
        self.root = self.winfo_toplevel()
        self.driver: Optional[PM6306] = None
        self.cola: "queue.Queue[tuple]" = queue.Queue()
        self.cancelar = threading.Event()
        self.hilo: Optional[threading.Thread] = None
        self.cal_map: Dict[str, int] = {}
        self._bf: List[float] = []
        self._bz: List[float] = []
        self._bp: List[float] = []

        self._crear_widgets()
        self.refrescar_puertos()
        self.refrescar_calibraciones()
        self._actualizar_estado()
        self.root.bind(EVENTO_DATOS, lambda e: self.refrescar_calibraciones(), add="+")
        self.after(50, self._procesar_cola)

    # ------------------------------------------------------------------ UI
    def _crear_widgets(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        izq = ttk.Frame(self, padding=8)
        izq.grid(row=0, column=0, sticky="ns")

        f = ttk.LabelFrame(izq, text="Conexión serial", padding=8)
        f.pack(fill="x", pady=4)
        self.var_puerto = tk.StringVar()
        self.cb_puerto = ttk.Combobox(f, textvariable=self.var_puerto, width=18,
                                      state="readonly")
        self.cb_puerto.grid(row=0, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Button(f, text="Actualizar", command=self.refrescar_puertos)\
            .grid(row=1, column=0, sticky="ew", padx=(0, 2))
        self.btn_conectar = ttk.Button(f, text="Conectar", command=self._toggle_conexion)
        self.btn_conectar.grid(row=1, column=1, sticky="ew")
        self.lbl_estado = ttk.Label(f, text="Desconectado", foreground="#cc4444")
        self.lbl_estado.grid(row=2, column=0, columnspan=2, pady=(4, 0))

        f = ttk.LabelFrame(izq, text="Material", padding=8)
        f.pack(fill="x", pady=4)
        self.var_nombre = tk.StringVar()
        self.var_tipo = tk.StringVar(value=TIPOS_MATERIAL[0])
        self.var_area = tk.StringVar(value="1e-4")
        self.var_espesor = tk.StringVar(value="1e-3")
        for i, (txt, var) in enumerate([("Nombre", self.var_nombre), ("Tipo", self.var_tipo),
                                        ("Área electrodo [m²]", self.var_area),
                                        ("Espesor [m]", self.var_espesor)]):
            ttk.Label(f, text=txt).grid(row=i, column=0, sticky="w", pady=2)
            if txt == "Tipo":
                w = ttk.Combobox(f, textvariable=var, width=16, values=TIPOS_MATERIAL)
            else:
                w = ttk.Entry(f, textvariable=var, width=18)
            w.grid(row=i, column=1, sticky="ew", padx=(6, 0))

        f = ttk.LabelFrame(izq, text="Condiciones del ensayo", padding=8)
        f.pack(fill="x", pady=4)
        self.var_vac = tk.StringVar(value="1.0")
        self.var_vdc = tk.StringVar(value="0.0")
        self.var_cal = tk.StringVar(value=SIN_CAL)
        ttk.Label(f, text="Nivel AC [Vrms]").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(f, textvariable=self.var_vac, width=10).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(f, text="Polarización [Vdc]").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(f, textvariable=self.var_vdc, width=10).grid(row=1, column=1, padx=(6, 0))
        ttk.Label(f, text="Calibración").grid(row=2, column=0, sticky="w", pady=2)
        self.cb_cal = ttk.Combobox(f, textvariable=self.var_cal, width=24, state="readonly")
        self.cb_cal.grid(row=2, column=1, padx=(6, 0))

        f = ttk.LabelFrame(izq, text="Control", padding=8)
        f.pack(fill="x", pady=4)
        self.btn_punto = ttk.Button(f, text="Medir punto (1 kHz)", command=self._medir_punto)
        self.btn_punto.pack(fill="x", pady=2)
        self.btn_barrido = ttk.Button(f, text="Barrido espectral", command=self._iniciar_barrido)
        self.btn_barrido.pack(fill="x", pady=2)
        self.btn_cancelar = ttk.Button(f, text="Cancelar", command=self.cancelar.set)
        self.btn_cancelar.pack(fill="x", pady=2)

        # Centro: display + Bode en vivo
        centro = ttk.Frame(self)
        centro.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
        centro.columnconfigure(0, weight=1)
        centro.rowconfigure(1, weight=1)

        disp = tk.Frame(centro, bg=BG_DISPLAY, bd=6, relief="sunken")
        disp.grid(row=0, column=0, sticky="ew")
        disp.columnconfigure(0, weight=1)
        disp.columnconfigure(1, weight=1)

        def titulo(txt, fila, col=0):
            lbl = tk.Label(disp, text=txt, bg=BG_DISPLAY, fg=FG_ETIQUETA,
                           font=FUENTE_ETIQ, anchor="w")
            lbl.grid(row=fila, column=col, sticky="w", padx=16, pady=(8, 0))
            return lbl

        def digitos(fuente, fila, col=0):
            lbl = tk.Label(disp, text="---", bg=BG_DISPLAY, fg=FG_DIGITO,
                           font=fuente, anchor="e")
            lbl.grid(row=fila, column=col, sticky="e", padx=16)
            return lbl

        titulo("FRECUENCIA", 0)
        self.lbl_freq = digitos(FUENTE_MEDIA, 1)
        self.lbl_dom_t = titulo("PARÁMETRO DOMINANTE", 2)
        self.lbl_dom = digitos(FUENTE_GRANDE, 3)
        self.lbl_sec_t = titulo("PARÁMETRO SECUNDARIO", 4)
        self.lbl_sec = digitos(FUENTE_MEDIA, 5)
        titulo("MÓDULO |Z|", 0, 1)
        self.lbl_z = digitos(FUENTE_MEDIA, 1, 1)
        titulo("FASE Φ", 2, 1)
        self.lbl_fase = digitos(FUENTE_MEDIA, 3, 1)

        self.fig = Figure(figsize=(6, 3.4), dpi=100)
        self.ax1, self.ax2 = self.fig.subplots(2, 1, sharex=True)
        self.canvas = FigureCanvasTkAgg(self.fig, master=centro)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self._dibujar_bode()

        inf = ttk.Frame(self, padding=(8, 0, 8, 8))
        inf.grid(row=1, column=0, columnspan=2, sticky="ew")
        inf.columnconfigure(0, weight=1)
        self.progreso = ttk.Progressbar(inf, maximum=len(FRECUENCIAS_NORMALIZADAS))
        self.progreso.grid(row=0, column=0, sticky="ew")
        self.lbl_prog = ttk.Label(inf, text="0 / 0")
        self.lbl_prog.grid(row=0, column=1, padx=8)
        self.txt_log = tk.Text(inf, height=5, state="disabled", bg="#111", fg="#ddd",
                               font=("Consolas", 10))
        self.txt_log.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    # ------------------------------------------------------------- helpers
    def _log(self, texto: str) -> None:
        log.info(texto)
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", texto + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def refrescar_puertos(self) -> None:
        puertos = PM6306.listar_puertos() + [PUERTO_SIMULADO]
        self.cb_puerto["values"] = puertos
        if self.var_puerto.get() not in puertos:
            self.var_puerto.set(puertos[0])

    def refrescar_calibraciones(self) -> None:
        self.cal_map = {}
        for c in self.db.listar_calibraciones():
            etiqueta = f"#{c['id_calibracion']} {c['tipo_trim']} fx{c['fixture_code']} ({c['fecha'][:16]})"
            self.cal_map[etiqueta] = c["id_calibracion"]
        valores = [SIN_CAL] + list(self.cal_map)
        self.cb_cal["values"] = valores
        if self.var_cal.get() not in valores:
            self.var_cal.set(SIN_CAL)

    def _ocupado(self) -> bool:
        return self.hilo is not None and self.hilo.is_alive()

    def _actualizar_estado(self) -> None:
        conectado, ocupado = self.driver is not None, self._ocupado()
        st = lambda ok: "normal" if ok else "disabled"
        self.btn_punto.configure(state=st(conectado and not ocupado))
        self.btn_barrido.configure(state=st(conectado and not ocupado))
        self.btn_cancelar.configure(state=st(ocupado))
        self.btn_conectar.configure(state=st(not ocupado),
                                    text="Desconectar" if conectado else "Conectar")
        self.cb_puerto.configure(state="disabled" if conectado else "readonly")
        self.lbl_estado.configure(text="Conectado" if conectado else "Desconectado",
                                  foreground="#44cc66" if conectado else "#cc4444")

    def _lanzar(self, objetivo, *args) -> None:
        self.hilo = threading.Thread(target=objetivo, args=args, daemon=True)
        self.hilo.start()
        self._actualizar_estado()

    def _leer_formulario(self) -> Optional[Dict[str, Any]]:
        try:
            nombre = self.var_nombre.get().strip()
            if not nombre:
                raise ValueError("El nombre del material es obligatorio.")
            area, espesor = flotante(self.var_area.get()), flotante(self.var_espesor.get())
            if area <= 0 or espesor <= 0:
                raise ValueError("Área y espesor deben ser positivos.")
            vac = flotante(self.var_vac.get())
            vdc = flotante(self.var_vdc.get())
            if not 0.05 <= vac <= 2.0:
                raise ValueError("El nivel AC debe estar entre 0,05 y 2,00 Vrms (manual PM6306).")
            if not 0.0 <= vdc <= 10.0:
                raise ValueError("La polarización interna debe estar entre 0 y 10 V (manual PM6306).")
            return {
                "material": {"nombre": nombre, "tipo": self.var_tipo.get().strip() or "Otro",
                             "area_m2": area, "espesor_m": espesor},
                "vac": vac,
                "vdc": vdc,
                "id_cal": self.cal_map.get(self.var_cal.get()),
            }
        except ValueError as exc:
            messagebox.showerror("Datos inválidos", str(exc))
            return None

    # -------------------------------------------------------------- Bode
    def _dibujar_bode(self) -> None:
        self.ax1.clear()
        self.ax2.clear()
        if self._bf:
            self.ax1.loglog(self._bf, self._bz, "o-", color="tab:blue")
            self.ax2.semilogx(self._bf, self._bp, "o-", color="tab:red")
        self.ax1.set_ylabel("|Z| [Ω]")
        self.ax2.set_ylabel("Φ [°]")
        self.ax2.set_xlabel("Frecuencia [Hz]")
        self.ax1.set_title("Barrido en curso", fontsize=10)
        for ax in (self.ax1, self.ax2):
            ax.grid(True, which="both", alpha=0.3)
        self.fig.tight_layout()
        self.canvas.draw_idle()

    # ----------------------------------------------------------- conexión
    def _toggle_conexion(self) -> None:
        if self.driver is not None:
            self._lanzar(self._tarea_desconectar, self.driver)
        else:
            self._lanzar(self._tarea_conectar, self.var_puerto.get())

    def _tarea_conectar(self, puerto: str) -> None:
        try:
            drv = PM6306Simulado() if puerto == PUERTO_SIMULADO else PM6306()
            drv.conectar(puerto)
            self.cola.put(("conectado", drv, puerto))
        except Exception as exc:
            log.exception("Fallo de conexión")
            self.cola.put(("error", f"Conexión fallida: {exc}"))
        finally:
            self.cola.put(("fin",))

    def _tarea_desconectar(self, drv: PM6306) -> None:
        try:
            drv.desconectar()
        finally:
            self.cola.put(("desconectado",))
            self.cola.put(("fin",))

    # ----------------------------------------------------------- medición
    def _medir_punto(self) -> None:
        datos = self._leer_formulario()
        if datos and self.driver:
            self._lanzar(self._tarea_punto, self.driver, datos)

    def _tarea_punto(self, drv: PM6306, datos: Dict[str, Any]) -> None:
        try:
            drv.configurar_nivel(datos["vac"])
            drv.configurar_polarizacion(datos["vdc"])
            self.cola.put(("display", drv.medir_punto(1000.0)))
            self.cola.put(("log", "Punto individual medido (no se guarda en la BD)."))
        except Exception as exc:
            log.exception("Fallo de medición")
            self.cola.put(("error", f"Error de medición: {exc}"))
        finally:
            self.cola.put(("fin",))

    def _iniciar_barrido(self) -> None:
        datos = self._leer_formulario()
        if not datos or not self.driver:
            return
        self.cancelar.clear()
        self._bf, self._bz, self._bp = [], [], []
        self._dibujar_bode()
        self.progreso.configure(value=0)
        self.lbl_prog.configure(text=f"0 / {len(FRECUENCIAS_NORMALIZADAS)}")
        self._lanzar(self._tarea_barrido, self.driver, datos)

    def _tarea_barrido(self, drv: PM6306, datos: Dict[str, Any]) -> None:
        try:
            drv.configurar_nivel(datos["vac"])
            drv.configurar_polarizacion(datos["vdc"])

            def progreso(i: int, n: int, med: Medicion) -> None:
                self.cola.put(("display", med))
                self.cola.put(("bode", med))
                self.cola.put(("progreso", i, n))

            meds = drv.medir_barrido(FRECUENCIAS_NORMALIZADAS, self.cancelar, progreso)
            id_ens = self.db.guardar_ensayo(
                datos["material"], meds, datos["vac"], datos["vdc"],
                id_calibracion=datos["id_cal"])
            self.cola.put(("guardado", id_ens, len(meds)))
        except BarridoCancelado:
            self.cola.put(("log", "Barrido cancelado: no se guardó ningún dato."))
        except Exception as exc:
            log.exception("Barrido abortado")
            self.cola.put(("error", f"Barrido abortado, nada fue guardado: {exc}"))
        finally:
            self.cola.put(("fin",))

    # ------------------------------------------- cola de eventos (hilo GUI)
    def _procesar_cola(self) -> None:
        try:
            while True:
                ev = self.cola.get_nowait()
                tipo = ev[0]
                if tipo == "display":
                    self._mostrar(ev[1])
                elif tipo == "bode":
                    m = ev[1]
                    self._bf.append(m.frecuencia_hz)
                    self._bz.append(m.impedancia_z)
                    self._bp.append(m.fase_deg)
                    self._dibujar_bode()
                elif tipo == "progreso":
                    self.progreso.configure(maximum=ev[2], value=ev[1])
                    self.lbl_prog.configure(text=f"{ev[1]} / {ev[2]}")
                elif tipo == "conectado":
                    self.driver = ev[1]
                    self._log(f"Conectado a {ev[2]}.")
                elif tipo == "desconectado":
                    self.driver = None
                    self._log("Desconectado (control local).")
                elif tipo == "guardado":
                    self._log(f"Ensayo #{ev[1]} guardado ({ev[2]} puntos).")
                    self.root.event_generate(EVENTO_DATOS, when="tail")
                elif tipo == "log":
                    self._log(ev[1])
                elif tipo == "error":
                    self._log("ERROR: " + ev[1])
                    messagebox.showerror("Error", ev[1])
                elif tipo == "fin":
                    self.hilo = None
                self._actualizar_estado()
        except queue.Empty:
            pass
        self.after(50, self._procesar_cola)

    def _mostrar(self, m: Medicion) -> None:
        u = lambda p: UNIDADES.get((p or "")[:1], "")
        self.lbl_freq.configure(text=formato_si(m.frecuencia_hz, "Hz"))
        self.lbl_dom_t.configure(text=f"PARÁMETRO DOMINANTE  [{m.parametro_dom}]")
        self.lbl_dom.configure(text=formato_si(m.valor_dom, u(m.parametro_dom)))
        if m.parametro_sec:
            self.lbl_sec_t.configure(text=f"PARÁMETRO SECUNDARIO  [{m.parametro_sec}]")
            self.lbl_sec.configure(text=formato_si(m.valor_sec, u(m.parametro_sec)))
        else:
            self.lbl_sec_t.configure(text="PARÁMETRO SECUNDARIO")
            self.lbl_sec.configure(text="---")
        self.lbl_z.configure(text=formato_si(m.impedancia_z, "Ω"))
        self.lbl_fase.configure(text=f"{m.fase_deg:+.2f} °")

    # -------------------------------------------------------------- cierre
    def cerrar(self) -> None:
        self.cancelar.set()
        if self.hilo is not None:
            self.hilo.join(timeout=3)
        if self.driver is not None:
            try:
                self.driver.desconectar()
            except Exception:
                log.exception("Error al desconectar")