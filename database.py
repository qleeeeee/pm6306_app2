"""
Persistencia SQLite: integridad referencial, WAL y guardado atómico.
Sin dependencias de GUI.
"""
from __future__ import annotations

import csv
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

DB_POR_DEFECTO = "caracterizacion_materiales.db"
TABLAS_VALIDAS = ("Materiales", "Calibraciones", "Ensayos", "Mediciones")

SCHEMA = """
CREATE TABLE IF NOT EXISTS Materiales (
    id_material    INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre         TEXT NOT NULL UNIQUE,
    tipo           TEXT NOT NULL,
    area_m2        REAL NOT NULL CHECK (area_m2 > 0),
    espesor_m      REAL NOT NULL CHECK (espesor_m > 0),
    fecha_creacion TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS Calibraciones (
    id_calibracion INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo_trim      TEXT NOT NULL CHECK (tipo_trim IN ('TRIM 1','TRIM A')),
    fixture_code   INTEGER NOT NULL CHECK (fixture_code BETWEEN 0 AND 10),
    fecha          TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS Ensayos (
    id_ensayo        INTEGER PRIMARY KEY AUTOINCREMENT,
    id_material      INTEGER NOT NULL REFERENCES Materiales(id_material),
    id_calibracion   INTEGER REFERENCES Calibraciones(id_calibracion),
    nivel_vac        REAL,
    polarizacion_vdc REAL,
    fecha            TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS Mediciones (
    id_medicion   INTEGER PRIMARY KEY AUTOINCREMENT,
    id_ensayo     INTEGER NOT NULL REFERENCES Ensayos(id_ensayo) ON DELETE CASCADE,
    frecuencia_hz REAL NOT NULL,
    parametro_dom TEXT NOT NULL,
    valor_dom     REAL NOT NULL,
    parametro_sec TEXT,
    valor_sec     REAL,
    impedancia_z  REAL,
    fase_deg      REAL
);

CREATE INDEX IF NOT EXISTS idx_med_ensayo ON Mediciones(id_ensayo, frecuencia_hz);
"""


def _a_dict(m: Any) -> Dict[str, Any]:
    if is_dataclass(m):
        return asdict(m)
    if isinstance(m, dict):
        return dict(m)
    return dict(vars(m))


class Database:
    def __init__(self, ruta: str = DB_POR_DEFECTO):
        self.ruta = ruta
        self.inicializar()

    @contextmanager
    def _conexion(self):
        con = sqlite3.connect(self.ruta, timeout=15, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA foreign_keys = ON;")
            con.execute("PRAGMA journal_mode = WAL;")
            yield con
        finally:
            con.close()

    def inicializar(self) -> None:
        with self._conexion() as con:
            con.executescript(SCHEMA)

    # ------------------------------------------------------------ escritura
    def guardar_ensayo(
        self,
        material: Dict[str, Any],
        mediciones: Iterable[Any],
        nivel_vac: Optional[float] = None,
        polarizacion_vdc: Optional[float] = None,
        calibracion: Optional[Dict[str, Any]] = None,
        id_calibracion: Optional[int] = None,
    ) -> int:
        """Material (upsert) + ensayo + todos los puntos en UNA transacción.
        Cualquier error -> ROLLBACK y se relanza la excepción.

        Calibración: pasar id_calibracion (existente) o un dict
        {'tipo_trim','fixture_code'} para crear una nueva; ambos None = ninguna.
        """
        filas = [_a_dict(m) for m in mediciones]
        if not filas:
            raise ValueError("No hay mediciones para guardar.")

        with self._conexion() as con:
            try:
                con.execute("BEGIN TRANSACTION")
                con.execute(
                    """INSERT INTO Materiales(nombre, tipo, area_m2, espesor_m)
                       VALUES (?,?,?,?)
                       ON CONFLICT(nombre) DO UPDATE SET
                           tipo=excluded.tipo,
                           area_m2=excluded.area_m2,
                           espesor_m=excluded.espesor_m""",
                    (material["nombre"], material["tipo"],
                     material["area_m2"], material["espesor_m"]))
                id_material = con.execute(
                    "SELECT id_material FROM Materiales WHERE nombre = ?",
                    (material["nombre"],)).fetchone()[0]

                id_cal = id_calibracion
                if id_cal is None and calibracion:
                    cur = con.execute(
                        "INSERT INTO Calibraciones(tipo_trim, fixture_code) VALUES (?,?)",
                        (calibracion["tipo_trim"], int(calibracion["fixture_code"])))
                    id_cal = cur.lastrowid

                cur = con.execute(
                    """INSERT INTO Ensayos(id_material, id_calibracion, nivel_vac,
                                           polarizacion_vdc) VALUES (?,?,?,?)""",
                    (id_material, id_cal, nivel_vac, polarizacion_vdc))
                id_ensayo = cur.lastrowid

                con.executemany(
                    """INSERT INTO Mediciones(id_ensayo, frecuencia_hz, parametro_dom,
                           valor_dom, parametro_sec, valor_sec, impedancia_z, fase_deg)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    [(id_ensayo, f["frecuencia_hz"], f["parametro_dom"],
                      f["valor_dom"], f.get("parametro_sec"), f.get("valor_sec"),
                      f.get("impedancia_z"), f.get("fase_deg")) for f in filas])
                con.execute("COMMIT")
                return id_ensayo
            except BaseException:
                if con.in_transaction:
                    con.execute("ROLLBACK")
                raise

    def guardar_calibracion(self, tipo_trim: str, fixture_code: int) -> int:
        with self._conexion() as con:
            cur = con.execute(
                "INSERT INTO Calibraciones(tipo_trim, fixture_code) VALUES (?,?)",
                (tipo_trim, int(fixture_code)))
            return cur.lastrowid

    def eliminar_calibracion(self, id_calibracion: int) -> None:
        """Lanza sqlite3.IntegrityError si algún ensayo la usa."""
        with self._conexion() as con:
            con.execute("DELETE FROM Calibraciones WHERE id_calibracion = ?",
                        (id_calibracion,))

    def eliminar_ensayo(self, id_ensayo: int) -> None:
        """Borra el ensayo y (ON DELETE CASCADE) todas sus mediciones."""
        with self._conexion() as con:
            con.execute("DELETE FROM Ensayos WHERE id_ensayo = ?", (id_ensayo,))

    # -------------------------------------------------------------- lectura
    def listar_materiales(self) -> List[Dict[str, Any]]:
        with self._conexion() as con:
            return [dict(r) for r in con.execute(
                "SELECT * FROM Materiales ORDER BY nombre")]

    def listar_calibraciones(self) -> List[Dict[str, Any]]:
        with self._conexion() as con:
            return [dict(r) for r in con.execute(
                """SELECT c.*, (SELECT COUNT(*) FROM Ensayos e
                                WHERE e.id_calibracion = c.id_calibracion) AS n_ensayos
                   FROM Calibraciones c ORDER BY c.id_calibracion DESC""")]

    def listar_ensayos_resumen(self) -> List[Dict[str, Any]]:
        with self._conexion() as con:
            return [dict(r) for r in con.execute(
                """SELECT e.id_ensayo, m.nombre, m.tipo, e.fecha, e.nivel_vac,
                          e.polarizacion_vdc, c.tipo_trim, c.fixture_code,
                          COUNT(me.id_medicion) AS n_puntos
                   FROM Ensayos e
                   JOIN Materiales m ON m.id_material = e.id_material
                   LEFT JOIN Calibraciones c ON c.id_calibracion = e.id_calibracion
                   LEFT JOIN Mediciones me ON me.id_ensayo = e.id_ensayo
                   GROUP BY e.id_ensayo
                   ORDER BY e.id_ensayo DESC""")]

    def obtener_mediciones(
        self, nombre: str, id_ensayo: Optional[int] = None
    ) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
        """(material, ensayo, mediciones por frecuencia). Sin id usa el más reciente."""
        with self._conexion() as con:
            mat = con.execute("SELECT * FROM Materiales WHERE nombre = ?",
                              (nombre,)).fetchone()
            if mat is None:
                raise KeyError(f"Material no encontrado: {nombre}")
            if id_ensayo is None:
                id_ensayo = con.execute(
                    "SELECT MAX(id_ensayo) FROM Ensayos WHERE id_material = ?",
                    (mat["id_material"],)).fetchone()[0]
                if id_ensayo is None:
                    raise KeyError(f"'{nombre}' no tiene ensayos registrados.")
            ens = con.execute(
                "SELECT * FROM Ensayos WHERE id_ensayo = ? AND id_material = ?",
                (id_ensayo, mat["id_material"])).fetchone()
            if ens is None:
                raise KeyError(f"Ensayo {id_ensayo} no pertenece a '{nombre}'.")
            meds = [dict(r) for r in con.execute(
                "SELECT * FROM Mediciones WHERE id_ensayo = ? ORDER BY frecuencia_hz",
                (id_ensayo,))]
            return dict(mat), dict(ens), meds

    def exportar_csv(self, id_ensayo: int, ruta: str) -> int:
        """Exporta las mediciones de un ensayo. Devuelve el nº de filas."""
        with self._conexion() as con:
            filas = con.execute(
                """SELECT m.nombre AS material, e.id_ensayo, me.frecuencia_hz,
                          me.parametro_dom, me.valor_dom, me.parametro_sec,
                          me.valor_sec, me.impedancia_z, me.fase_deg
                   FROM Mediciones me
                   JOIN Ensayos e ON e.id_ensayo = me.id_ensayo
                   JOIN Materiales m ON m.id_material = e.id_material
                   WHERE me.id_ensayo = ? ORDER BY me.frecuencia_hz""",
                (id_ensayo,)).fetchall()
        if not filas:
            raise KeyError(f"Ensayo {id_ensayo} sin mediciones.")
        with open(ruta, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(filas[0].keys())
            w.writerows([tuple(r) for r in filas])
        return len(filas)

    def obtener_tabla(self, nombre: str, limite: int = 5000
                      ) -> Tuple[List[str], List[tuple], int]:
        """Devuelve (columnas, filas, total) de una tabla, más recientes primero.
        El nombre se valida contra una lista blanca."""
        if nombre not in TABLAS_VALIDAS:
            raise ValueError(f"Tabla no válida: {nombre}")
        with self._conexion() as con:
            total = con.execute(f"SELECT COUNT(*) FROM {nombre}").fetchone()[0]
            cur = con.execute(f"SELECT * FROM {nombre} ORDER BY 1 DESC LIMIT ?",
                              (int(limite),))
            columnas = [d[0] for d in cur.description]
            filas = [tuple(r) for r in cur.fetchall()]
        return columnas, filas, total