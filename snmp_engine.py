"""
Módulo de Coleta SNMP e Cálculo de Telemetria de Rede.
Responsável por consultar contadores MIB-II e OIDs de CPU, calcular taxas em bps com delta t,
tratar rollover de 32/64 bits e gerenciar erros de conexão/timeout com resiliência.

Equipamento de referência principal fora do laboratório: MikroTik RouterOS CHR.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from pysnmp.hlapi.v3arch.asyncio import (
    SnmpEngine,
    CommunityData,
    UdpTransportTarget,
    ContextData,
    ObjectType,
    ObjectIdentity,
    get_cmd,
    next_cmd,
    UsmUserData,
    usmHMACMD5AuthProtocol,
    usmHMACSHAAuthProtocol,
    usmDESPrivProtocol,
    usmAesCfb128Protocol,
)
from pysnmp.proto.rfc1905 import EndOfMibView, NoSuchInstance, NoSuchObject

# ==============================================================================
# OIDs Padronizadas (MIB-II, HOST-RESOURCES-MIB e Fabricantes)
# ==============================================================================
# Sistema Geral (SNMPv2-MIB / RFC 3418)
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"   # TimeTicks (centésimos de segundo desde o boot)
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"

# Interfaces de Rede (IF-MIB / RFC 2863 e RFC 1213)
OID_IF_DESCR_BASE = "1.3.6.1.2.1.2.2.1.2"
OID_IF_NAME_BASE = "1.3.6.1.2.1.31.1.1.1.1"
OID_IF_SPEED_BASE = "1.3.6.1.2.1.2.2.1.5"
OID_IF_IN_OCTETS_BASE = "1.3.6.1.2.1.2.2.1.10"         # Counter32
OID_IF_OUT_OCTETS_BASE = "1.3.6.1.2.1.2.2.1.16"        # Counter32
OID_IF_HC_IN_OCTETS_BASE = "1.3.6.1.2.1.31.1.1.1.6"     # Counter64 (High Capacity)
OID_IF_HC_OUT_OCTETS_BASE = "1.3.6.1.2.1.31.1.1.1.10"   # Counter64 (High Capacity)

# Utilização de CPU
CPU_OIDS = {
    # MikroTik RouterOS CHR e Universal (RFC 2790 hrProcessorLoad.1)
    "host_resources": "1.3.6.1.2.1.25.3.3.1.2.1",
    # MikroTik Enterprise MIB (mtxrProcessorLoad)
    "mikrotik_enterprise": "1.3.6.1.4.1.14988.1.1.1.3.1.0",
    # Cisco IOS (cpmCPUTotal5secRev)
    "cisco_5sec": "1.3.6.1.4.1.9.9.109.1.1.1.1.3.1",
    "cisco_1min": "1.3.6.1.4.1.9.9.109.1.1.1.1.7.1",
    # Linux NET-SNMP (100 - ssCpuIdle)
    "linux_idle": "1.3.6.1.4.1.2021.11.11.0",
}

MAX_COUNTER_32 = 4294967296            # 2^32
MAX_PLAUSIBLE_BPS = 100_000_000_000.0  # 100 Gbps (filtro de anomalias/reboot)


def _is_valid_number(val: Any) -> bool:
    """Verifica se o valor retornado pelo agente SNMP é um número válido (não é erro ou nulo)."""
    if val is None or isinstance(val, (NoSuchObject, NoSuchInstance, EndOfMibView)):
        return False
    try:
        float(val)
        return True
    except (ValueError, TypeError):
        return False


def _friendly_error(err_str: str, ip: str, port: int) -> str:
    """Traduz códigos de erro do PySNMP em explicações amigáveis ao usuário."""
    text = str(err_str).strip()
    low = text.lower()
    if "timeout" in low or "no snmp response" in low:
        return (
            f"Tempo limite esgotado: o equipamento {ip}:{port} não respondeu. "
            "Verifique se o IP está correto, se a VM MikroTik CHR está ligada "
            "e se o SNMP foi habilitado (/snmp set enabled=yes)."
        )
    if "unknownsecurityname" in low or "wrongdigest" in low or "authentication" in low:
        return f"Falha de autenticação SNMPv3 no equipamento {ip}:{port}. Confira usuário e chaves."
    if "unknown" in low and "host" in low or "getaddrinfo" in low:
        return f"Endereço IP ou hostname inválido: '{ip}'."
    return text


class SnmpCollector:
    """Gerencia conexões, leitura de contadores e cálculos de telemetria SNMP."""

    def __init__(self) -> None:
        self.prev_timestamp: Optional[float] = None
        self.prev_in_octets: Optional[int] = None
        self.prev_out_octets: Optional[int] = None
        self.prev_uptime: Optional[int] = None
        self.last_if_index: Optional[int] = None

    def reset_baseline(self) -> None:
        """Reinicia os contadores de referência para evitar saltos ou taxas incorretas."""
        self.prev_timestamp = None
        self.prev_in_octets = None
        self.prev_out_octets = None
        self.prev_uptime = None
        self.last_if_index = None

    def _build_auth(self, cfg: Dict[str, Any]) -> Any:
        """Monta o objeto de credenciais para SNMPv2c ou SNMPv3."""
        version = str(cfg.get("snmp_version", "v2c")).lower()
        if version == "v3":
            user = str(cfg.get("snmp_v3_user", "usr-sha-des")).strip()
            auth_key = str(cfg.get("snmp_v3_auth_key", "")).strip() or None
            priv_key = str(cfg.get("snmp_v3_priv_key", "")).strip() or None
            return UsmUserData(
                user,
                authKey=auth_key,
                privKey=priv_key,
                authProtocol=usmHMACSHAAuthProtocol if auth_key else None,
                privProtocol=usmAesCfb128Protocol if priv_key else None,
            )
        # Padrão v2c (mpModel=1)
        community = str(cfg.get("snmp_community", "public")).strip()
        return CommunityData(community, mpModel=1)

    async def _async_get(
        self,
        ip: str,
        port: int,
        auth_data: Any,
        oids: List[str],
        timeout: float = 2.0,
        retries: int = 1,
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        """Executa SNMP GET assíncrono para uma lista de OIDs."""
        engine = SnmpEngine()
        try:
            target = await UdpTransportTarget.create((ip, port), timeout=timeout, retries=retries)
            var_binds_in = [ObjectType(ObjectIdentity(oid)) for oid in oids]
            error_ind, error_status, error_idx, var_binds_out = await get_cmd(
                engine, auth_data, target, ContextData(), *var_binds_in
            )

            if error_ind:
                return _friendly_error(str(error_ind), ip, port), {}
            if error_status:
                oid_err = var_binds_out[int(error_idx) - 1][0] if error_idx and int(error_idx) <= len(var_binds_out) else "?"
                return f"{error_status.prettyPrint()} ao consultar {oid_err}", {}

            results: Dict[str, Any] = {}
            for name, val in var_binds_out:
                results[str(name)] = val
            return None, results

        except Exception as e:
            return _friendly_error(f"{type(e).__name__}: {e}", ip, port), {}
        finally:
            try:
                engine.transport_dispatcher.close_dispatcher()
            except Exception:
                pass

    def _determine_cpu_oids(self, cfg: Dict[str, Any]) -> List[str]:
        """Determina as OIDs de CPU a consultar, com fallback prioritário para MikroTik CHR."""
        profile = str(cfg.get("cpu_profile", "mikrotik")).lower()
        if profile == "custom":
            custom = str(cfg.get("cpu_custom_oid", "")).strip()
            return [custom] if custom else [CPU_OIDS["host_resources"]]
        if profile == "cisco":
            return [CPU_OIDS["cisco_5sec"], CPU_OIDS["cisco_1min"]]
        if profile == "linux":
            return [CPU_OIDS["linux_idle"]]
        # MikroTik RouterOS CHR / Padrão: consulta hrProcessorLoad.1 E mtxrProcessorLoad
        return [CPU_OIDS["host_resources"], CPU_OIDS["mikrotik_enterprise"]]

    async def collect_sample_async(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Coleta dados reais do roteador via SNMP, calcula as taxas em bps
        e trata counter rollover, variações de tempo delta t e reboot.
        """
        now_dt = datetime.now().replace(microsecond=0)
        now_time = time.time()
        ip = str(cfg.get("ip_address", "127.0.0.1")).strip()
        port = int(cfg.get("snmp_port", 161))
        if_index = int(cfg.get("if_index", 1))

        # Se mudou de interface, reinicia baseline
        if self.last_if_index != if_index:
            self.reset_baseline()
            self.last_if_index = if_index

        auth_data = self._build_auth(cfg)
        cpu_candidate_oids = self._determine_cpu_oids(cfg)

        in_oid = f"{OID_IF_IN_OCTETS_BASE}.{if_index}"
        out_oid = f"{OID_IF_OUT_OCTETS_BASE}.{if_index}"
        hc_in_oid = f"{OID_IF_HC_IN_OCTETS_BASE}.{if_index}"
        hc_out_oid = f"{OID_IF_HC_OUT_OCTETS_BASE}.{if_index}"

        # Monta lista de OIDs a consultar em uma única requisição SNMP GET
        oids_to_fetch = [OID_SYS_UPTIME, in_oid, out_oid, hc_in_oid, hc_out_oid]
        for c_oid in cpu_candidate_oids:
            if c_oid and c_oid not in oids_to_fetch:
                oids_to_fetch.append(c_oid)

        error, data = await self._async_get(ip, port, auth_data, oids_to_fetch)
        if error:
            return {
                "success": False,
                "error": error,
                "timestamp": now_dt,
                "inbound_bps": 0.0,
                "outbound_bps": 0.0,
                "cpu_percent": 0.0,
                "source": "SNMP_ERRO",
            }

        # Extração de Bytes de Entrada (IN) - Suporta 64-bit HC com fallback para 32-bit
        curr_in: Optional[int] = None
        if in_oid in data and _is_valid_number(data[in_oid]):
            curr_in = int(data[in_oid])
        elif hc_in_oid in data and _is_valid_number(data[hc_in_oid]):
            curr_in = int(data[hc_in_oid])

        # Extração de Bytes de Saída (OUT) - Suporta 64-bit HC com fallback para 32-bit
        curr_out: Optional[int] = None
        if out_oid in data and _is_valid_number(data[out_oid]):
            curr_out = int(data[out_oid])
        elif hc_out_oid in data and _is_valid_number(data[hc_out_oid]):
            curr_out = int(data[hc_out_oid])

        # Extração de sysUpTime para detecção de reboot do equipamento
        uptime: Optional[int] = None
        if OID_SYS_UPTIME in data and _is_valid_number(data[OID_SYS_UPTIME]):
            uptime = int(data[OID_SYS_UPTIME])

        # Extração da Utilização de CPU (%)
        cpu_val = 0.0
        for c_oid in cpu_candidate_oids:
            if c_oid in data and _is_valid_number(data[c_oid]):
                raw_cpu = float(data[c_oid])
                if cfg.get("cpu_profile") == "linux":
                    cpu_val = max(0.0, min(100.0, 100.0 - raw_cpu))
                else:
                    cpu_val = max(0.0, min(100.0, raw_cpu))
                break

        if curr_in is None or curr_out is None:
            return {
                "success": False,
                "error": f"OIDs de tráfego não encontradas na interface ifIndex {if_index}.",
                "timestamp": now_dt,
                "inbound_bps": 0.0,
                "outbound_bps": 0.0,
                "cpu_percent": round(cpu_val, 1),
                "source": "SNMP_PARCIAL",
            }

        # Verificação de Reboot do Roteador (sysUpTime reiniciou)
        if uptime is not None and self.prev_uptime is not None and uptime < self.prev_uptime:
            self.reset_baseline()
            self.prev_uptime = uptime
            self.prev_timestamp = now_time
            self.prev_in_octets = curr_in
            self.prev_out_octets = curr_out
            return {
                "success": True,
                "error": None,
                "timestamp": now_dt,
                "inbound_bps": 0.0,
                "outbound_bps": 0.0,
                "cpu_percent": round(cpu_val, 1),
                "raw_in_bytes": curr_in,
                "raw_out_bytes": curr_out,
                "note": "Reboot do roteador detectado (sysUpTime reiniciado). Baseline restabelecida.",
                "source": "SNMP_REAL",
            }

        # Cálculo da taxa em bps e delta t
        in_bps = 0.0
        out_bps = 0.0

        if self.prev_timestamp is not None and self.prev_in_octets is not None and self.prev_out_octets is not None:
            dt = now_time - self.prev_timestamp
            if dt <= 0.05:
                dt = 1.0  # Prevenção contra divisão por zero em cliques manuais

            # Tratamento de Rollover de 32 bits para IN
            if curr_in >= self.prev_in_octets:
                diff_in = curr_in - self.prev_in_octets
            else:
                diff_in = (MAX_COUNTER_32 - self.prev_in_octets) + curr_in
                if diff_in < 0 or diff_in > MAX_PLAUSIBLE_BPS:
                    diff_in = 0

            # Tratamento de Rollover de 32 bits para OUT
            if curr_out >= self.prev_out_octets:
                diff_out = curr_out - self.prev_out_octets
            else:
                diff_out = (MAX_COUNTER_32 - self.prev_out_octets) + curr_out
                if diff_out < 0 or diff_out > MAX_PLAUSIBLE_BPS:
                    diff_out = 0

            # Fórmula oficial obrigatória: Taxa (bps) = (Delta_Bytes * 8) / Delta_t
            in_bps = round((diff_in * 8.0) / dt, 2)
            out_bps = round((diff_out * 8.0) / dt, 2)

            # Rejeição de anomalias absurdas
            if in_bps > MAX_PLAUSIBLE_BPS or out_bps > MAX_PLAUSIBLE_BPS:
                in_bps = 0.0
                out_bps = 0.0

        # Atualiza a linha de base (baseline) para a próxima coleta
        self.prev_timestamp = now_time
        self.prev_in_octets = curr_in
        self.prev_out_octets = curr_out
        self.prev_uptime = uptime

        return {
            "success": True,
            "error": None,
            "timestamp": now_dt,
            "inbound_bps": in_bps,
            "outbound_bps": out_bps,
            "cpu_percent": round(cpu_val, 1),
            "raw_in_bytes": curr_in,
            "raw_out_bytes": curr_out,
            "source": "SNMP_REAL",
        }

    async def test_connection_async(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Testa conexão detalhada obtendo sysDescr, sysName e métricas atuais."""
        ip = str(cfg.get("ip_address", "127.0.0.1")).strip()
        port = int(cfg.get("snmp_port", 161))
        auth_data = self._build_auth(cfg)

        error, data = await self._async_get(
            ip, port, auth_data, [OID_SYS_DESCR, OID_SYS_NAME, OID_SYS_UPTIME], timeout=2.0
        )
        if error:
            return {"success": False, "error": error}

        sys_descr = str(data.get(OID_SYS_DESCR, "Desconhecido"))
        sys_name = str(data.get(OID_SYS_NAME, "MikroTik/Roteador"))
        sample = await self.collect_sample_async(cfg)

        return {
            "success": sample.get("success", False),
            "error": sample.get("error"),
            "sys_descr": sys_descr,
            "sys_name": sys_name,
            "cpu_percent": sample.get("cpu_percent", 0.0),
            "raw_in_bytes": sample.get("raw_in_bytes", 0),
            "raw_out_bytes": sample.get("raw_out_bytes", 0),
        }

    async def discover_interfaces(
        self, cfg: Dict[str, Any], timeout: float = 2.0
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """Descobre interfaces de rede disponíveis consultando a tabela ifDescr via walk SNMP."""
        ip = str(cfg.get("ip_address", "127.0.0.1")).strip()
        port = int(cfg.get("snmp_port", 161))
        auth_data = self._build_auth(cfg)
        engine = SnmpEngine()

        interfaces: List[Dict[str, Any]] = []
        try:
            target = await UdpTransportTarget.create((ip, port), timeout=timeout, retries=1)
            var_bind = ObjectType(ObjectIdentity(OID_IF_DESCR_BASE))

            visited = set()
            max_rows = 64
            while len(interfaces) < max_rows:
                error_ind, error_status, error_idx, var_bind_table = await next_cmd(
                    engine, auth_data, target, ContextData(), var_bind
                )
                if error_ind:
                    return _friendly_error(str(error_ind), ip, port), interfaces
                if error_status or not var_bind_table:
                    break

                var_bind = var_bind_table[0]
                name_str = str(var_bind[0])
                if not name_str.startswith(OID_IF_DESCR_BASE) or name_str in visited:
                    break
                visited.add(name_str)

                val = var_bind[1]
                if _is_valid_number(val) or (val and not isinstance(val, (NoSuchObject, NoSuchInstance, EndOfMibView))):
                    parts = name_str.split(".")
                    idx = int(parts[-1])
                    desc = str(val.prettyPrint() if hasattr(val, "prettyPrint") else val)
                    interfaces.append({"index": idx, "name": desc})

            if not interfaces:
                interfaces = [{"index": 1, "name": "ether1 (Interface Padrão MikroTik)"}]
            return None, interfaces

        except Exception as e:
            return _friendly_error(f"{type(e).__name__}: {e}", ip, port), [{"index": 1, "name": "ether1"}]
        finally:
            try:
                engine.transport_dispatcher.close_dispatcher()
            except Exception:
                pass

    def collect_sample(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Wrapper síncrono thread-safe para execução no Streamlit sem conflito de event loop."""
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, self.collect_sample_async(cfg)).result()

    def test_connection(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Wrapper síncrono thread-safe para teste de conexão detalhado."""
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, self.test_connection_async(cfg)).result()

    def discover_interfaces_sync(self, cfg: Dict[str, Any]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """Wrapper síncrono thread-safe para descoberta de interfaces."""
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, self.discover_interfaces(cfg)).result()
