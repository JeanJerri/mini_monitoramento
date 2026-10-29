"""
Módulo de Persistência Histórica em Disco (CSV) — Telemetria Real SNMP.
Responsável exclusivamente por gravar e carregar medições reais do arquivo data/medicoes.csv.
Não contém nenhum gerador de dados sintéticos ou funções simuladas.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, Any

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CSV_PATH = DATA_DIR / "medicoes.csv"

COLUMNS = [
    "timestamp",
    "inbound_bps",
    "outbound_bps",
    "cpu_percent",
    "source",
    "device_ip",
    "iface",
]


def append_measurement(
    sample: Dict[str, Any],
    ip: str = "127.0.0.1",
    iface: str = "ether1",
) -> None:
    """Acrescenta uma nova medição real no arquivo CSV histórico."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = sample.get("timestamp")
    if isinstance(ts, datetime):
        ts = ts.strftime("%Y-%m-%d %H:%M:%S")
    elif not ts:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    row = {
        "timestamp": ts,
        "inbound_bps": sample.get("inbound_bps", 0.0),
        "outbound_bps": sample.get("outbound_bps", 0.0),
        "cpu_percent": sample.get("cpu_percent", 0.0),
        "source": sample.get("source", "SNMP_REAL"),
        "device_ip": ip,
        "iface": iface,
    }

    df_row = pd.DataFrame([row], columns=COLUMNS)
    df_row.to_csv(CSV_PATH, mode="a", header=not CSV_PATH.exists(), index=False, encoding="utf-8")


def load_measurements() -> pd.DataFrame:
    """
    Carrega as medições históricas gravadas em disco.
    Retorna um DataFrame vazio caso o arquivo ainda não contenha registros (sem dados falsos).
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0:
        return pd.DataFrame(columns=COLUMNS)

    try:
        df = pd.read_csv(CSV_PATH)
    except Exception:
        return pd.DataFrame(columns=COLUMNS)

    for col in COLUMNS:
        if col not in df.columns:
            if col == "source":
                df[col] = "SNMP_REAL"
            elif col == "device_ip":
                df[col] = "127.0.0.1"
            elif col == "iface":
                df[col] = "ether1"
            else:
                df[col] = 0.0

    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp")

    return df[COLUMNS]
