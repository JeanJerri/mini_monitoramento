"""
Módulo de Alertas e Notificações com Prevenção de Tempestade (Histerese/Debouncing)
e Envio Real de E-mail via SMTP (TLS/SSL).
"""
from __future__ import annotations

import smtplib
import time
from datetime import datetime
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple


def format_bps(value_bps: float) -> str:
    """Formata valor em bps com unidade dinâmica legível."""
    if value_bps >= 1_000_000_000:
        return f"{value_bps:,.0f} bps ({value_bps / 1_000_000_000:.2f} Gbps)"
    if value_bps >= 1_000_000:
        return f"{value_bps:,.0f} bps ({value_bps / 1_000_000:.2f} Mbps)"
    if value_bps >= 1_000:
        return f"{value_bps:,.0f} bps ({value_bps / 1_000:.2f} Kbps)"
    return f"{value_bps:,.0f} bps"


def send_smtp_email(
    cfg: Dict[str, Any],
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Envia um e-mail real via servidor SMTP configurado.
    Retorna (sucesso, mensagem_ou_erro).
    """
    host = str(cfg.get("smtp_host", "")).strip()
    port = int(cfg.get("smtp_port", 587))
    use_tls = bool(cfg.get("smtp_use_tls", True))
    user = str(cfg.get("smtp_user", "")).strip()
    password = str(cfg.get("smtp_password", ""))
    from_addr = str(cfg.get("smtp_from", "")).strip() or user
    to_addr = str(cfg.get("smtp_to", "")).strip()

    if not host:
        return False, "Servidor SMTP não configurado."
    if not to_addr:
        return False, "Destinatário de e-mail não informado."
    if not from_addr:
        from_addr = to_addr

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = from_addr
    msg["To"] = to_addr

    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=10)
        else:
            server = smtplib.SMTP(host, port, timeout=10)
            if use_tls:
                server.starttls()

        if user and password:
            server.login(user, password)

        server.sendmail(from_addr, [to_addr], msg.as_string())
        server.quit()
        return True, "E-mail enviado com sucesso!"
    except smtplib.SMTPAuthenticationError as e:
        return False, f"Falha de autenticação SMTP: {e}"
    except smtplib.SMTPConnectError as e:
        return False, f"Não foi possível conectar ao servidor SMTP {host}:{port}."
    except Exception as e:
        return False, f"Erro no envio de e-mail: {type(e).__name__} - {e}"


class AlertManager:
    """
    Gerenciador de alertas com histerese:
    Dispara apenas na transição de Normal -> Excedido,
    evitando disparos repetitivos e tempestades de notificações.
    """

    def __init__(self) -> None:
        # Estado atual de violação por métrica: True se violado, False se normal
        self.metric_states: Dict[str, bool] = {
            "traffic_in": False,
            "traffic_out": False,
            "cpu": False,
        }
        # Timestamp do último disparo por métrica
        self.last_alert_times: Dict[str, float] = {
            "traffic_in": 0.0,
            "traffic_out": 0.0,
            "cpu": 0.0,
        }
        # Histórico recente de alertas disparados na sessão
        self.alert_history: List[Dict[str, Any]] = []

    def reset_state(self) -> None:
        """Limpa estados de alerta."""
        self.metric_states = {"traffic_in": False, "traffic_out": False, "cpu": False}
        self.last_alert_times = {"traffic_in": 0.0, "traffic_out": 0.0, "cpu": 0.0}

    def check_alerts(
        self,
        sample: Dict[str, Any],
        cfg: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Avalia a medição atual contra os limiares configurados.
        Retorna lista de novos eventos de alerta que devem ser notificados.
        """
        now = time.time()
        cooldown = float(cfg.get("alert_cooldown_s", 300))
        traffic_limit = float(cfg.get("traffic_threshold_bps", 15_000_000.0))
        cpu_limit = float(cfg.get("cpu_threshold_percent", 80.0))
        target = cfg.get("traffic_threshold_target", "both")

        in_bps = float(sample.get("inbound_bps", 0.0))
        out_bps = float(sample.get("outbound_bps", 0.0))
        cpu_pct = float(sample.get("cpu_percent", 0.0))
        ts_str = str(sample.get("timestamp", datetime.now().strftime("%d/%m/%Y %H:%M:%S")))

        new_alerts: List[Dict[str, Any]] = []

        # 1. Avalia Tráfego de Entrada (IN)
        if target in ("in", "both"):
            violating = in_bps >= traffic_limit
            was_violating = self.metric_states.get("traffic_in", False)
            last_time = self.last_alert_times.get("traffic_in", 0.0)

            # Transição Normal -> Excedido OU após tempo de carência
            if violating and (not was_violating or (now - last_time >= cooldown)):
                alert_evt = {
                    "metric_key": "traffic_in",
                    "metric_name": "Tráfego de Entrada (IN)",
                    "observed_value": in_bps,
                    "observed_str": format_bps(in_bps),
                    "threshold_value": traffic_limit,
                    "threshold_str": format_bps(traffic_limit),
                    "timestamp": ts_str,
                    "unit": "bps",
                }
                new_alerts.append(alert_evt)
                self.last_alert_times["traffic_in"] = now
            self.metric_states["traffic_in"] = violating

        # 2. Avalia Tráfego de Saída (OUT)
        if target in ("out", "both"):
            violating = out_bps >= traffic_limit
            was_violating = self.metric_states.get("traffic_out", False)
            last_time = self.last_alert_times.get("traffic_out", 0.0)

            if violating and (not was_violating or (now - last_time >= cooldown)):
                alert_evt = {
                    "metric_key": "traffic_out",
                    "metric_name": "Tráfego de Saída (OUT)",
                    "observed_value": out_bps,
                    "observed_str": format_bps(out_bps),
                    "threshold_value": traffic_limit,
                    "threshold_str": format_bps(traffic_limit),
                    "timestamp": ts_str,
                    "unit": "bps",
                }
                new_alerts.append(alert_evt)
                self.last_alert_times["traffic_out"] = now
            self.metric_states["traffic_out"] = violating

        # 3. Avalia Utilização da CPU (%)
        violating_cpu = cpu_pct >= cpu_limit
        was_violating_cpu = self.metric_states.get("cpu", False)
        last_time_cpu = self.last_alert_times.get("cpu", 0.0)

        if violating_cpu and (not was_violating_cpu or (now - last_time_cpu >= cooldown)):
            alert_evt = {
                "metric_key": "cpu",
                "metric_name": "Utilização da CPU",
                "observed_value": cpu_pct,
                "observed_str": f"{cpu_pct:.1f}%",
                "threshold_value": cpu_limit,
                "threshold_str": f"{cpu_limit:.1f}%",
                "timestamp": ts_str,
                "unit": "%",
            }
            new_alerts.append(alert_evt)
            self.last_alert_times["cpu"] = now
        self.metric_states["cpu"] = violating_cpu

        # Dispara envio de e-mail se canal estiver ativo
        if new_alerts and cfg.get("alert_email"):
            self._dispatch_email_alerts(new_alerts, cfg)

        # Registra no histórico de alertas
        for evt in new_alerts:
            self.alert_history.append({
                **evt,
                "sent_email": bool(cfg.get("alert_email")),
                "sent_popup": bool(cfg.get("alert_popup")),
            })
            # Mantém os últimos 100 alertas
            self.alert_history = self.alert_history[-100:]

        return new_alerts

    def _dispatch_email_alerts(self, alerts: List[Dict[str, Any]], cfg: Dict[str, Any]) -> None:
        """Monta o e-mail formal de alerta e dispara via SMTP."""
        ip = cfg.get("ip_address", "127.0.0.1")
        lines_text = [
            "==================================================",
            "   ALERTA DE TELEMETRIA DE REDE - LIMIAR VIOLADO  ",
            "==================================================",
            f"Equipamento / IP: {ip}",
            f"Interface monitorada: {cfg.get('if_index', 1)}",
            "",
            "Detalhes da(s) violação(ões):",
        ]
        for a in alerts:
            lines_text.extend([
                f"- Métrica: {a['metric_name']}",
                f"  Valor Medido: {a['observed_str']}",
                f"  Limiar Configurado: {a['threshold_str']}",
                f"  Data e Hora: {a['timestamp']}",
                "",
            ])
        lines_text.append("Este é um e-mail automático gerado pelo Mini Monitoramento de Rede.")
        body_text = "\n".join(lines_text)

        subject = f"[ALERTA MONITORAMENTO] Limiar excedido em {ip} ({alerts[0]['metric_name']})"
        send_smtp_email(cfg, subject, body_text)
