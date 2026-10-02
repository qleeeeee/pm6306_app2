"""
Driver serial (RS-232) para el puente RCL Fluke PM6306.

Comandos contrastados con el "PM6306 Programmers Manual" (960508):
  9600 8-N-1, terminador NL; ESC 1/ESC 2 (local/remoto) SIN terminador;
  conexion de 3 hilos => handshake Xon/Xoff (en el equipo: HAnds on, Conn 3).
Incluye un simulador (PM6306Simulado) para probar sin hardware.
"""
from __future__ import annotations

import logging
import math
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

import serial
from serial.tools import list_ports

log = logging.getLogger(__name__)

ESC_LOCAL = b"\x1b1"     # ESC 1: volver a control local
ESC_REMOTO = b"\x1b2"    # ESC 2: control remoto
TERMINADOR = b"\n"       # NL (solo para mensajes de texto, no para ESC)

FRECUENCIAS_NORMALIZADAS: List[float] = [
    100, 200, 500, 1e3, 2e3, 5e3, 1e4, 2e4, 5e4, 1e5, 2e5, 5e5, 1e6,
]


class PM6306Error(Exception):
    """Error de comunicacion, de protocolo o reportado por el instrumento."""


class BarridoCancelado(Exception):
    """El usuario cancelo el barrido en curso."""


@dataclass
class Medicion:
    frecuencia_hz: float
    parametro_dom: str
    valor_dom: float
    parametro_sec: Optional[str]
    valor_sec: Optional[float]
    impedancia_z: float
    fase_deg: float


# ----------------------------------------------------------------------------
# Parsers (funciones puras, faciles de probar)
# ----------------------------------------------------------------------------
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_RE_NUM = re.compile(_NUM)
_RE_ETIQUETA = re.compile(r"^([A-Za-z%]+)\s*[:=]?\s*(.*)$")
_RE_DESBORDE = re.compile(r"OVER|^[<>]", re.IGNORECASE)


def _a_numero(texto: str) -> Optional[float]:
    """Numero -> float. 'OVER', '<x' o '>x' (fuera de rango) -> None."""
    t = texto.strip()
    if _RE_NUM.fullmatch(t):
        return float(t)
    if _RE_DESBORDE.search(t):
        return None
    raise PM6306Error(f"Valor no interpretable: {texto!r}")


def parse_valor(respuesta: str, etiqueta: Optional[str] = None) -> Optional[float]:
    """Interpreta 'Z 15.53E3', 'P -78.7' o un numero suelto ('1').
    Si se indica etiqueta y la respuesta trae otra, lanza error (respuesta
    desincronizada). Devuelve None si el instrumento respondio OVER/</>."""
    r = respuesta.strip()
    if not r:
        raise PM6306Error("Respuesta vacia del PM6306.")
    if _RE_NUM.fullmatch(r):
        return float(r)
    m = _RE_ETIQUETA.match(r)
    if not m:
        raise PM6306Error(f"Respuesta no interpretable: {respuesta!r}")
    et, resto = m.group(1).upper(), m.group(2)
    if etiqueta and et[0] != etiqueta.upper()[0]:
        raise PM6306Error(
            f"Respuesta inesperada {respuesta!r} (se esperaba etiqueta '{etiqueta}').")
    return _a_numero(resto)


def parse_com(respuesta: str) -> List[Tuple[str, Optional[float]]]:
    """'C 10.046E-9;R 78.67E3' -> [('C', 1.0046e-08), ('R', 78670.0)]
    'C 22E-9;R OVER'          -> [('C', 2.2e-08), ('R', None)]
    (el manual muestra ';' y ':' como separador, se aceptan ambos)."""
    pares: List[Tuple[str, Optional[float]]] = []
    for token in re.split(r"[;:]", respuesta):
        token = token.strip()
        if not token:
            continue
        m = _RE_ETIQUETA.match(token)
        if not m:
            raise PM6306Error(f"Respuesta COM? no interpretable: {respuesta!r}")
        pares.append((m.group(1).upper(), _a_numero(m.group(2))))
    if not pares:
        raise PM6306Error(f"Respuesta COM? vacia: {respuesta!r}")
    return pares


# ----------------------------------------------------------------------------
# Validaciones segun el manual
# ----------------------------------------------------------------------------
def frecuencia_valida(f: float) -> bool:
    """50, 60, 100, 120 Hz; 200 Hz-100 kHz paso 100 Hz; 100 kHz-1 MHz paso 1 kHz."""
    if f in (50, 60, 100, 120):
        return True
    if 200 <= f <= 100_000:
        return abs(f / 100 - round(f / 100)) < 1e-9
    if 100_000 < f <= 1_000_000:
        return abs(f / 1000 - round(f / 1000)) < 1e-9
    return False


def _validar_frecuencia(f: float) -> float:
    if not frecuencia_valida(f):
        raise PM6306Error(
            f"Frecuencia {f:g} Hz no valida para el PM6306 (50/60/100/120 Hz; "
            "200 Hz-100 kHz en pasos de 100 Hz; 100 kHz-1 MHz en pasos de 1 kHz).")
    return float(f)


def _validar_nivel(vac: float) -> float:
    v = round(float(vac), 2)
    if not 0.05 <= v <= 2.00:
        raise PM6306Error(f"Nivel AC {vac:g} V fuera de rango (0.05 a 2.00 Vrms).")
    return v


def _validar_bias(vdc: float) -> float:
    v = round(float(vdc), 1)
    if not 0.0 <= v <= 10.0:
        raise PM6306Error(f"Polarizacion interna {vdc:g} V fuera de rango (0 a 10 V).")
    return v


# ----------------------------------------------------------------------------
# Driver real
# ----------------------------------------------------------------------------
class PM6306:
    # Mnemonicos (Programmers Manual, cap. 3 y 7)
    CMD_MODO = {"AUTO": "MODE AUTO", "SER": "MODE SERIAL", "PAR": "MODE PARAL"}
    CMD_TEST_AC = "TEST_SIG AC"
    CMD_SINGLE = "SINGLE"
    CMD_TRIGGER = "TRIG"
    CMD_FREQ = "FRE {}"
    CMD_NIVEL = "AC_LEV {:.2f}"
    CMD_BIAS_V = "BIAS_VOL {:.1f}"
    CMD_BIAS_ON = "DC_BIAS INT"
    CMD_BIAS_OFF = "DC_BIAS OFF"
    CMD_FIXTURE = "SET_FIX {}"

    def __init__(self, timeout: float = 3.0, timeout_opc: float = 15.0):
        self.timeout = timeout
        self.timeout_opc = timeout_opc
        self._ser: Optional[serial.Serial] = None
        self._lock = threading.RLock()
        self.remoto = False
        self.identificacion = ""

    # -- utilidades -----------------------------------------------------
    @staticmethod
    def listar_puertos() -> List[str]:
        return [p.device for p in list_ports.comports()]

    @property
    def conectado(self) -> bool:
        return self._ser is not None and self._ser.is_open

    # -- conexion ---------------------------------------------------------
    def conectar(self, puerto: str) -> None:
        with self._lock:
            if self.conectado:
                raise PM6306Error("El puerto ya esta abierto.")
            try:
                self._ser = serial.Serial(
                    port=puerto, baudrate=9600,
                    bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE, timeout=self.timeout,
                    write_timeout=self.timeout,
                    xonxoff=True,            # 3 hilos => handshake por software
                    rtscts=False, dsrdtr=False)
                time.sleep(0.2)
                self._ser.reset_input_buffer()
                self._ser.reset_output_buffer()

                self._escribir_esc(ESC_REMOTO)           # ESC 2, sin terminador
                self.remoto = True
                time.sleep(0.1)
                self._escribir("*CLS")
                idn = self.consultar("*IDN?")             # FLUKE,PM6306,0,Vx.x/0000
                if "PM6306" not in idn.upper():
                    raise PM6306Error(f"El equipo no se identifica como PM6306: {idn!r}")
                self.identificacion = idn
                log.info("Conectado: %s", idn)

                self.configurar_modo("AUTO")
                self._sync(self.CMD_TEST_AC)
                self._sync(self.CMD_SINGLE)               # necesario para TRIG
                self.verificar_errores(" durante la inicializacion")
            except (serial.SerialException, OSError) as exc:
                self._abortar_conexion()
                raise PM6306Error(f"No se pudo abrir {puerto}: {exc}") from exc
            except BaseException:
                self._abortar_conexion()
                raise

    def _abortar_conexion(self) -> None:
        try:
            if self.conectado:
                self._ser.write(ESC_LOCAL)
        except Exception:
            pass
        self._cerrar_puerto()

    def desconectar(self) -> None:
        with self._lock:
            if self.conectado:
                try:
                    self._escribir_esc(ESC_LOCAL)         # ESC 1, sin terminador
                    time.sleep(0.1)
                except Exception:
                    log.exception("No se pudo enviar ESC 1")
            self._cerrar_puerto()

    def _cerrar_puerto(self) -> None:
        try:
            if self._ser is not None:
                self._ser.close()
        finally:
            self._ser = None
            self.remoto = False

    # -- E/S de bajo nivel ------------------------------------------------
    def _escribir(self, comando: str) -> None:
        """Mensaje de texto: se le agrega el terminador NL."""
        if not self.conectado:
            raise PM6306Error("Puerto serial no conectado.")
        try:
            self._ser.write(comando.encode("ascii") + TERMINADOR)
            self._ser.flush()
        except (serial.SerialException, OSError) as exc:
            raise PM6306Error(f"Error de escritura: {exc}") from exc

    def _escribir_esc(self, codigo: bytes) -> None:
        """Codigos de interfaz (ESC 1, ESC 2...): SIN terminador."""
        if not self.conectado:
            raise PM6306Error("Puerto serial no conectado.")
        try:
            self._ser.write(codigo)
            self._ser.flush()
        except (serial.SerialException, OSError) as exc:
            raise PM6306Error(f"Error de escritura: {exc}") from exc

    def _leer(self, timeout: Optional[float] = None) -> str:
        if not self.conectado:
            raise PM6306Error("Puerto serial no conectado.")
        previo = self._ser.timeout
        try:
            self._ser.timeout = self.timeout if timeout is None else timeout
            linea = self._ser.readline()
        except (serial.SerialException, OSError) as exc:
            raise PM6306Error(f"Error de lectura: {exc}") from exc
        finally:
            try:
                if self._ser is not None:
                    self._ser.timeout = previo
            except Exception:
                pass
        if not linea.endswith(b"\n"):
            raise PM6306Error(
                "Timeout esperando respuesta del PM6306 "
                "(revise cable, baudios, HAnds on y Conn 3).")
        return linea.decode("ascii", errors="ignore").strip()

    def consultar(self, comando: str, timeout: Optional[float] = None) -> str:
        with self._lock:
            if not self.conectado:
                raise PM6306Error("Puerto serial no conectado.")
            try:
                self._ser.reset_input_buffer()    # descarta restos de respuestas viejas
            except (serial.SerialException, OSError) as exc:
                raise PM6306Error(f"Error al limpiar el buffer: {exc}") from exc
            self._escribir(comando)
            return self._leer(timeout)

    def _sync(self, comando: str, timeout: Optional[float] = None) -> None:
        """Envia 'comando;*OPC?' en un solo mensaje y espera la respuesta '1'."""
        resp = self.consultar(f"{comando};*OPC?",
                              self.timeout_opc if timeout is None else timeout)
        try:
            ok = parse_valor(resp) == 1
        except PM6306Error:
            ok = False
        if not ok:
            raise PM6306Error(
                f"Sincronizacion fallida tras '{comando}': se esperaba '1' y llego {resp!r}.")

    def verificar_errores(self, contexto: str = "") -> None:
        """*ESR? (bits 2,3,4,5 = error de consulta/dispositivo/ejecucion/comando).
        Si hay error, lee ERR? y lanza excepcion."""
        with self._lock:
            esr = int(parse_valor(self.consultar("*ESR?")) or 0)
            if esr & 0b00111100:
                detalle = self.consultar("ERR?")
                try:
                    self._escribir("*CLS")
                except PM6306Error:
                    pass
                raise PM6306Error(
                    f"El PM6306 reporto un error{contexto}: {detalle} (ESR={esr})")

    # -- configuracion ------------------------------------------------------
    def configurar_modo(self, modo: str = "AUTO") -> None:
        """'AUTO' | 'SER' | 'PAR' (MODE AUTO / MODE SERIAL / MODE PARAL)."""
        if modo not in self.CMD_MODO:
            raise PM6306Error(f"Modo desconocido: {modo!r}")
        with self._lock:
            self._sync(self.CMD_MODO[modo])
            self.verificar_errores(f" al fijar {self.CMD_MODO[modo]}")

    def configurar_frecuencia(self, f_hz: float) -> float:
        """Fija la frecuencia y devuelve la que el equipo informa (FRE?)."""
        f = _validar_frecuencia(f_hz)
        with self._lock:
            self._sync(self.CMD_FREQ.format(int(round(f))))
            real = self.leer_frecuencia()
        if abs(real - f) > 0.005 * f:
            log.warning("Frecuencia pedida %g Hz, el equipo usa %g Hz", f, real)
        return real

    def leer_frecuencia(self) -> float:
        val = parse_valor(self.consultar("FRE?"))     # responde 'FREQ 1.0E3'
        if val is None:
            raise PM6306Error("FRE? devolvio un valor fuera de rango.")
        return val

    def configurar_nivel(self, vac: float) -> None:
        v = _validar_nivel(vac)
        with self._lock:
            self._sync(self.CMD_NIVEL.format(v))
            self.verificar_errores(" al fijar el nivel AC")

    def configurar_polarizacion(self, vdc: float) -> None:
        v = _validar_bias(vdc)
        with self._lock:
            if v == 0.0:
                self._sync(self.CMD_BIAS_OFF)
            else:
                self._sync(self.CMD_BIAS_V.format(v))
                self._sync(self.CMD_BIAS_ON)
            self.verificar_errores(" al fijar la polarizacion")

    def configurar_fixture(self, codigo: int) -> None:
        """SET_FIX n (0..10): correccion por capacidad de los cables/fixture."""
        if not 0 <= int(codigo) <= 10:
            raise PM6306Error("El codigo de fixture debe estar entre 0 y 10.")
        with self._lock:
            self._sync(self.CMD_FIXTURE.format(int(codigo)))
            self.verificar_errores(" al fijar el fixture")

    def hacer_trim(self, todas: bool = False, timeout: float = 180.0) -> None:
        """TRIM SINGLE (solo la frecuencia actual) o TRIM ALL. Requiere terminales
        en corto o abiertos segun el caso: hacerlo SOLO con el montaje adecuado."""
        with self._lock:
            self._sync("TRIM ALL" if todas else "TRIM SINGLE", timeout)
            self.verificar_errores(" durante el trim")

    # -- medicion -----------------------------------------------------------
    def medir_punto(self, frecuencia_hz: Optional[float] = None) -> Medicion:
        with self._lock:
            if frecuencia_hz is not None:
                f = self.configurar_frecuencia(frecuencia_hz)
            else:
                f = self.leer_frecuencia()
            self._sync(self.CMD_TRIGGER)              # 'TRIG;*OPC?' -> '1' al terminar
            com = self.consultar("COM?")
            imp = self.consultar("IMP?")
            pha = self.consultar("PHA?")
            self.verificar_errores(f" (f = {f:g} Hz)")

        pares = parse_com(com)
        p_dom, v_dom = pares[0]
        if v_dom is None:
            raise PM6306Error(
                f"Parametro dominante fuera de rango a {f:g} Hz ({com!r}): "
                "revise contactos, trim y nivel de prueba.")
        p_sec, v_sec = pares[1] if len(pares) > 1 else (None, None)  # v_sec None = OVER
        z = parse_valor(imp, "Z")
        fase = parse_valor(pha, "P")
        if z is None or fase is None:
            raise PM6306Error(f"|Z| o fase fuera de rango a {f:g} Hz ({imp!r}, {pha!r}).")
        return Medicion(f, p_dom, v_dom, p_sec, v_sec, z, fase)

    def medir_barrido(
        self,
        frecuencias: Iterable[float] = FRECUENCIAS_NORMALIZADAS,
        cancelar: Optional[threading.Event] = None,
        progreso: Optional[Callable[[int, int, Medicion], None]] = None,
    ) -> List[Medicion]:
        """Barrido completo. Ante cualquier fallo lanza excepcion y NO devuelve
        resultados parciales (la persistencia atomica la hace database.py)."""
        frecuencias = list(frecuencias)
        for f in frecuencias:                         # validar TODO antes de medir
            _validar_frecuencia(f)
        resultados: List[Medicion] = []
        for i, f in enumerate(frecuencias, start=1):
            if cancelar is not None and cancelar.is_set():
                raise BarridoCancelado()
            med = self.medir_punto(f)
            resultados.append(med)
            if progreso:
                progreso(i, len(frecuencias), med)
        return resultados


# ----------------------------------------------------------------------------
# Simulador (sin hardware)
# ----------------------------------------------------------------------------
class PM6306Simulado(PM6306):
    """Modelo RC paralelo con dispersion suave; valida rangos como el equipo."""

    def __init__(self, c0: float = 10.046e-9, r0: float = 78.67e3,
                 retardo: float = 0.15):
        super().__init__()
        self.c0, self.r0, self.retardo = c0, r0, retardo
        self._sim = False
        self._f = 1e3

    @property
    def conectado(self) -> bool:
        return self._sim

    def conectar(self, puerto: str = "SIMULADO") -> None:
        self._sim = True
        self.remoto = True
        self.identificacion = "FLUKE,PM6306,0,V0.0/0000 (simulado)"

    def desconectar(self) -> None:
        self._sim = False
        self.remoto = False

    def configurar_modo(self, modo: str = "AUTO") -> None:
        pass

    def configurar_frecuencia(self, f_hz: float) -> float:
        self._f = _validar_frecuencia(f_hz)
        return self._f

    def leer_frecuencia(self) -> float:
        return self._f

    def configurar_nivel(self, vac: float) -> None:
        _validar_nivel(vac)

    def configurar_polarizacion(self, vdc: float) -> None:
        _validar_bias(vdc)

    def configurar_fixture(self, codigo: int) -> None:
        pass

    def hacer_trim(self, todas: bool = False, timeout: float = 180.0) -> None:
        pass

    def verificar_errores(self, contexto: str = "") -> None:
        pass

    def medir_punto(self, frecuencia_hz: Optional[float] = None) -> Medicion:
        if not self._sim:
            raise PM6306Error("Simulador no conectado.")
        if frecuencia_hz is not None:
            self._f = _validar_frecuencia(frecuencia_hz)
        time.sleep(self.retardo)
        f = self._f
        ruido = lambda: 1 + random.gauss(0, 0.002)
        c = self.c0 * (1 + 0.15 / (1 + math.sqrt(f / 2e4))) * ruido()
        r = self.r0 / (1 + f / 1e5) * ruido()
        z = 1 / complex(1 / r, 2 * math.pi * f * c)
        return Medicion(f, "C", c, "R", r, abs(z),
                        math.degrees(math.atan2(z.imag, z.real)))