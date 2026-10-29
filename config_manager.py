"""
Gerenciador de configurações persistentes do Mini Monitoramento.
Salva e carrega configurações em arquivo JSON para preservar os parâmetros entre sessões.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    # Conexão SNMP
    "collection_mode": "snmp_real",  # "snmp_real" (padrão oficial) ou "simulated"
    "ip_address": "127.0.0.1",
    "snmp_port": 161,
    "snmp_version": "v2c",  # "v2c" ou "v3"
    "snmp_community": "public",
    "snmp_v3_user": "",
    "snmp_v3_auth_key": "",
    "snmp_v3_priv_key": "",
    "if_index": 2,
    "cpu_profile": "mikrotik",  # "mikrotik" (RouterOS CHR), "auto", "cisco", "linux", "custom"
    "cpu_custom_oid": "",
    
    # Parâmetros de Coleta
    "interval_s": 5,
    "window_minutes": 5,
    
    # Limiares (Thresholds)
    "traffic_threshold_bps": 15000000.0,  # 15 Mbps em bits por segundo (bps)
    "traffic_threshold_target": "both",    # "in", "out", "both"
    "cpu_threshold_percent": 80.0,         # %
    
    # Canais de Notificação
    "alert_popup": True,
    "alert_email": False,
    "alert_cooldown_s": 300,  # Carência de 5 min para reenvio caso continue alto
    
    # Servidor SMTP para envio real de e-mails
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_use_tls": True,
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "",
    "smtp_to": "",
}


def is_email_valid(email: str) -> bool:
    """Valida o formato básico de e-mail com regex."""
    if not email:
        return False
    pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
    return bool(re.match(pattern, email.strip()))


def load_config() -> Dict[str, Any]:
    """Carrega as configurações salvas ou retorna os padrões."""
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    cfg.update(saved)
        except Exception:
            pass
    return cfg


def save_config(config_data: Dict[str, Any]) -> None:
    """Salva o dicionário de configurações no arquivo JSON."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Erro ao salvar config.json: {e}")


def validate_config(cfg: Dict[str, Any]) -> list[str]:
    """Valida os parâmetros de configuração e retorna lista de mensagens de erro."""
    errors = []
    
    # Intervalo de coleta
    interval = cfg.get("interval_s", 0)
    if interval is None or interval <= 0:
        errors.append("O intervalo de coleta deve ser estritamente maior que zero segundos.")
        
    # IP ou Hostname
    ip = str(cfg.get("ip_address", "")).strip()
    if not ip:
        errors.append("O endereço IP ou Hostname do equipamento é obrigatório.")
        
    # Comunidade SNMP v2c
    if cfg.get("snmp_version") == "v2c":
        community = str(cfg.get("snmp_community", "")).strip()
        if not community:
            errors.append("A comunidade SNMP (v2c) não pode ficar em branco.")
            
    # SNMP v3
    if cfg.get("snmp_version") == "v3":
        user = str(cfg.get("snmp_v3_user", "")).strip()
        if not user:
            errors.append("O usuário SNMPv3 é obrigatório para a versão v3.")
            
    # Limiares
    traffic_thresh = cfg.get("traffic_threshold_bps", 0)
    if traffic_thresh is None or traffic_thresh < 0:
        errors.append("O limiar de tráfego deve ser um valor positivo.")
        
    cpu_thresh = cfg.get("cpu_threshold_percent", 0)
    if cpu_thresh is None or cpu_thresh <= 0 or cpu_thresh > 100:
        errors.append("O limiar de CPU deve estar entre 1% e 100%.")
        
    # Validação de E-mail se canal estiver ativado
    if cfg.get("alert_email"):
        dest = str(cfg.get("smtp_to", "")).strip()
        if not is_email_valid(dest):
            errors.append("Para habilitar alertas por e-mail, informe um endereço de destinatário válido.")
        smtp_host = str(cfg.get("smtp_host", "")).strip()
        if not smtp_host:
            errors.append("Informe o servidor SMTP para envio de e-mails.")
            
    return errors
