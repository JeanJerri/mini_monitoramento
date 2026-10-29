"""
Mini Monitoramento de Rede via SNMP.
Trabalho Prático - Projeto e Administração de Redes.

Interface desenvolvida rigorosamente de acordo com o wireframe do quadro e as especificações:
- Cabeçalho: Intervalo de Coleta e Botão de Configurações (Modal);
- Bloco Superior: Painel Tempo Real (RT) com botões Iniciar, Pausar e Resetar,
  janela deslizante e gráficos de Tráfego IN/OUT (bps) e CPU (%) com linhas de threshold;
- Bloco Central: Filtro de Histórico (Data/Hora Início até Data/Hora Fim);
- Bloco Inferior: Painel Histórico (Hist.) com gráficos estáticos gerados a partir do banco/CSV.
"""
from __future__ import annotations

import math
import random
import subprocess
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import alert_engine
import config_manager
import historico_csv
import snmp_engine

# Configuração da Página
st.set_page_config(
    page_title="Monitoramento de Rede SNMP",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Estilização técnica com foco em usabilidade e sobriedade (sem aspecto de IA/emojis excessivos)
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
    }
    .panel-header {
        font-size: 1.05rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #94a3b8;
        border-bottom: 1px solid #334155;
        padding-bottom: 6px;
        margin-top: 10px;
        margin-bottom: 12px;
    }
    .badge-status {
        display: inline-flex;
        align-items: center;
        padding: 3px 10px;
        border-radius: 4px;
        font-size: 0.8rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.03em;
    }
    .badge-online { background-color: #064e3b; color: #34d399; border: 1px solid #059669; }
    .badge-paused { background-color: #1e293b; color: #94a3b8; border: 1px solid #475569; }
    .badge-error { background-color: #7f1d1d; color: #fca5a5; border: 1px solid #dc2626; }
    div[data-testid="stMetricValue"] {
        font-size: 1.5rem !important;
        font-weight: 700;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def format_bps(val: float) -> str:
    """Formata valor em bps com valor exato e auxiliar em Mbps."""
    if val >= 1_000_000_000:
        return f"{val:,.0f} bps ({val / 1_000_000_000:.2f} Gbps)"
    if val >= 1_000_000:
        return f"{val:,.0f} bps ({val / 1_000_000:.2f} Mbps)"
    if val >= 1_000:
        return f"{val:,.0f} bps ({val / 1_000:.2f} Kbps)"
    return f"{val:,.0f} bps"


def init_session() -> None:
    """Inicializa os estados de sessão persistentes."""
    if "config" not in st.session_state:
        st.session_state.config = config_manager.load_config()

    if "is_running" not in st.session_state:
        st.session_state.is_running = True

    if "collector" not in st.session_state:
        st.session_state.collector = snmp_engine.SnmpCollector()

    if "alert_mgr" not in st.session_state:
        st.session_state.alert_mgr = alert_engine.AlertManager()

    if "live_points" not in st.session_state:
        st.session_state.live_points = []
        recent_df = historico_csv.load_measurements()
        if not recent_df.empty:
            st.session_state.live_points = recent_df.tail(60).to_dict("records")

    if "last_snmp_error" not in st.session_state:
        st.session_state.last_snmp_error = None

    if "stress_until" not in st.session_state:
        st.session_state.stress_until = 0.0

    if "sim_counter" not in st.session_state:
        st.session_state.sim_counter = 1000

    # Filtros de histórico
    now = datetime.now().replace(microsecond=0)
    if "hist_start_dt" not in st.session_state:
        st.session_state.hist_start_dt = now - timedelta(hours=2)
    if "hist_end_dt" not in st.session_state:
        st.session_state.hist_end_dt = now


init_session()
cfg = st.session_state.config


# ==============================================================================
# MODAL DE CONFIGURAÇÕES (st.dialog) - Botão Cfg no Cabeçalho
# ==============================================================================
@st.dialog("Configurações do Monitoramento (Cfg)", width="large")
def modal_configuracoes() -> None:
    st.caption("Ajuste os parâmetros de conexão SNMP, limiares de alerta e servidor de e-mail.")

    with st.form("modal_settings_form"):
        t_snmp, t_thresholds, t_smtp = st.tabs(["Conexão SNMP", "Limiares e Alertas", "Servidor SMTP"])

        with t_snmp:
            col_m1, col_m2 = st.columns(2)
            with col_m1:
                m_ip = st.text_input("IP ou Hostname do Roteador", value=cfg.get("ip_address", "127.0.0.1"), help="IP do roteador físico, VM VirtualBox ou container Docker.")
                m_port = st.number_input("Porta UDP", value=int(cfg.get("snmp_port", 161)), min_value=1, max_value=65535)
            with col_m2:
                m_version = st.selectbox(
                    "Versão SNMP",
                    ["v2c", "v3"],
                    index=0 if cfg.get("snmp_version", "v2c") == "v2c" else 1,
                )
                m_community = st.text_input("Comunidade SNMP (v2c)", value=cfg.get("snmp_community", "public"), type="password")
                m_iface = st.number_input(
                    "Índice da Interface (ifIndex)",
                    value=int(cfg.get("if_index", 2)),
                    min_value=1,
                    max_value=256,
                    help="No MikroTik RouterOS CHR: ifIndex 2 = ether1 (tráfego de rede), ifIndex 1 = lo (loopback). Use o botão Descobrir Interfaces.",
                )

            m_cpu_profile = st.selectbox(
                "Perfil de OID para CPU",
                ["mikrotik", "auto", "cisco", "linux", "custom"],
                index=["mikrotik", "auto", "cisco", "linux", "custom"].index(cfg.get("cpu_profile", "mikrotik"))
                if cfg.get("cpu_profile", "mikrotik") in ["mikrotik", "auto", "cisco", "linux", "custom"] else 0,
                format_func=lambda x: {
                    "mikrotik": "MikroTik RouterOS CHR (mtxrProcessorLoad / hrProcessorLoad)",
                    "auto": "Host Resources MIB universal (hrProcessorLoad - RFC 2790)",
                    "cisco": "Cisco IOS (cpmCPUTotal5secRev)",
                    "linux": "Linux NET-SNMP (100 - ssCpuIdle)",
                    "custom": "Personalizado (Inserir OID manual)",
                }[x],
            )
            m_custom_oid = st.text_input("OID Customizada de CPU", value=cfg.get("cpu_custom_oid", "")) if m_cpu_profile == "custom" else ""

            with st.expander("Instruções para MikroTik RouterOS CHR (Uso Fora do Laboratório)", expanded=False):
                st.markdown(
                    """
                    Para o desenvolvimento fora do laboratório, utilize a versão do **MikroTik RouterOS CHR** em máquina virtual (VirtualBox, VMware ou Hyper-V):
                    * **Guia Oficial de Instalação:** [MikroTik CHR Installation](https://manual.mikrotik.com/docs/getting-started/installation-and-upgrade/install/chr-installation/)
                    * **Comandos para ativar SNMP no Terminal do RouterOS:**
                      ```routeros
                      /snmp set enabled=yes
                      /snmp community add name=public read-access=yes
                      ```
                    * **Descobrir o IP do MikroTik:** execute `/ip address print` no terminal do RouterOS.
                    * **Interfaces:** selecione `ifIndex 2` (`ether1` no RouterOS CHR; `ifIndex 1` é a interface de loopback `lo`) ou use o botão *Descobrir Interfaces*.
                    """
                )

        with t_thresholds:
            col_th1, col_th2 = st.columns(2)
            with col_th1:
                m_traffic_th = st.number_input(
                    "Threshold de Tráfego (bps)",
                    min_value=1000.0,
                    max_value=10_000_000_000.0,
                    value=float(cfg.get("traffic_threshold_bps", 15_000_000.0)),
                    step=1_000_000.0,
                    help="Limite em bits por segundo (bps). Ex: 15.000.000 bps = 15 Mbps.",
                )
                st.caption(f"Valor equivalente: **{m_traffic_th / 1_000_000:.2f} Mbps**")

                m_traffic_target = st.selectbox(
                    "Aplicar Limiar de Tráfego em:",
                    ["both", "in", "out"],
                    index=["both", "in", "out"].index(cfg.get("traffic_threshold_target", "both")),
                    format_func=lambda x: {"both": "Entrada (IN) e Saída (OUT)", "in": "Apenas Entrada (IN)", "out": "Apenas Saída (OUT)"}[x],
                )

            with col_th2:
                m_cpu_th = st.number_input(
                    "Threshold de CPU (%)",
                    min_value=1.0,
                    max_value=100.0,
                    value=float(cfg.get("cpu_threshold_percent", 80.0)),
                    step=5.0,
                )
                m_cooldown = st.number_input(
                    "Tempo de Carência / Debounce (segundos)",
                    min_value=10,
                    max_value=3600,
                    value=int(cfg.get("alert_cooldown_s", 300)),
                    help="Evita disparos repetidos caso a sobrecarga persista.",
                )

            st.markdown("**Canais de Notificação (seleção múltipla permitida):**")
            ch_col1, ch_col2 = st.columns(2)
            with ch_col1:
                m_alert_popup = st.checkbox("Notificação por Pop-up na Interface", value=cfg.get("alert_popup", True))
            with ch_col2:
                m_alert_email = st.checkbox("Notificação por E-mail (SMTP)", value=cfg.get("alert_email", False))

        with t_smtp:
            col_sm1, col_sm2 = st.columns(2)
            with col_sm1:
                m_smtp_host = st.text_input("Servidor SMTP", value=cfg.get("smtp_host", "smtp.gmail.com"))
                m_smtp_port = st.number_input("Porta SMTP", value=int(cfg.get("smtp_port", 587)))
                m_smtp_tls = st.checkbox("Usar STARTTLS/TLS", value=cfg.get("smtp_use_tls", True))
            with col_sm2:
                m_smtp_user = st.text_input("Usuário SMTP", value=cfg.get("smtp_user", ""))
                m_smtp_pass = st.text_input("Senha SMTP", value=cfg.get("smtp_password", ""), type="password")

            col_sm3, col_sm4 = st.columns(2)
            with col_sm3:
                m_smtp_from = st.text_input("E-mail Remetente", value=cfg.get("smtp_from", ""))
            with col_sm4:
                m_smtp_to = st.text_input("E-mail Destinatário", value=cfg.get("smtp_to", ""))

        btn_save = st.form_submit_button("Salvar Configurações", type="primary", use_container_width=True)

    if btn_save:
        updated = dict(cfg)
        updated["collection_mode"] = "snmp_real"
        updated["ip_address"] = m_ip.strip()
        updated["snmp_port"] = int(m_port)
        updated["snmp_version"] = m_version
        updated["snmp_community"] = m_community.strip()
        updated["if_index"] = int(m_iface)
        updated["cpu_profile"] = m_cpu_profile
        updated["cpu_custom_oid"] = m_custom_oid.strip()
        updated["traffic_threshold_bps"] = float(m_traffic_th)
        updated["traffic_threshold_target"] = m_traffic_target
        updated["cpu_threshold_percent"] = float(m_cpu_th)
        updated["alert_popup"] = m_alert_popup
        updated["alert_email"] = m_alert_email
        updated["alert_cooldown_s"] = int(m_cooldown)
        updated["smtp_host"] = m_smtp_host.strip()
        updated["smtp_port"] = int(m_smtp_port)
        updated["smtp_use_tls"] = m_smtp_tls
        updated["smtp_user"] = m_smtp_user.strip()
        updated["smtp_password"] = m_smtp_pass
        updated["smtp_from"] = m_smtp_from.strip()
        updated["smtp_to"] = m_smtp_to.strip()

        errors = config_manager.validate_config(updated)
        if errors:
            for err in errors:
                st.error(f"Erro: {err}")
        else:
            config_manager.save_config(updated)
            st.session_state.config = updated
            st.session_state.collector.reset_baseline()
            st.success("Configurações gravadas com sucesso.")
            st.rerun()

    st.divider()
    st.markdown("**Testes de Conectividade:**")
    t_btn1, t_btn2, t_btn3 = st.columns(3)
    with t_btn1:
        if st.button("Testar SNMP", use_container_width=True):
            res = st.session_state.collector.test_connection(cfg)
            if res.get("success"):
                st.success(f"Conectado: {res.get('sys_descr', 'MikroTik RouterOS')}. CPU: {res.get('cpu_percent')}% | Bytes IN: {res.get('raw_in_bytes', 0):,}")
            else:
                st.error(f"Falha: {res.get('error')}")
    with t_btn2:
        if st.button("Descobrir Interfaces", use_container_width=True):
            err, ifaces = st.session_state.collector.discover_interfaces_sync(cfg)
            if err:
                st.warning(f"Erro: {err}")
            for ifc in ifaces:
                st.write(f"• ifIndex {ifc['index']}: {ifc['name']}")
    with t_btn3:
        if st.button("Testar E-mail", use_container_width=True):
            ok, msg = alert_engine.send_smtp_email(
                cfg,
                subject="[TESTE] Monitoramento de Rede",
                body_text="E-mail de teste gerado pelo sistema de telemetria SNMP.",
            )
            if ok:
                st.success("E-mail entregue ao servidor SMTP.")
            else:
                st.error(f"Falha: {msg}")


# ==============================================================================
# FUNÇÕES DE PLOTAGEM TÉCNICA (Sóbrias, sem gradientes chamativos)
# ==============================================================================
def render_traffic_plot(df: pd.DataFrame, limit_bps: float, title: str) -> go.Figure:
    fig = go.Figure()
    if not df.empty:
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["inbound_bps"],
                mode="lines",
                name="Tráfego IN",
                line={"color": "#0ea5e9", "width": 2},
                hovertemplate="IN: %{y:,.0f} bps<extra></extra>",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["outbound_bps"],
                mode="lines",
                name="Tráfego OUT",
                line={"color": "#10b981", "width": 2},
                hovertemplate="OUT: %{y:,.0f} bps<extra></extra>",
            )
        )

    fig.add_hline(
        y=limit_bps,
        line_dash="dash",
        line_color="#dc2626",
        line_width=1.8,
        annotation_text=f"Threshold: {format_bps(limit_bps)}",
        annotation_position="top left",
        annotation_font_color="#dc2626",
    )

    fig.update_layout(
        title={"text": title, "font": {"size": 13, "color": "#cbd5e1"}, "x": 0.01, "y": 0.96},
        height=320,
        margin={"l": 15, "r": 15, "t": 35, "b": 15},
        xaxis_title="Tempo",
        yaxis_title="Taxa (bps)",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
        hovermode="x unified",
        template="plotly_dark",
        paper_bgcolor="#0f172a",
        plot_bgcolor="#1e293b",
    )
    return fig


def render_cpu_plot(df: pd.DataFrame, limit_pct: float, title: str) -> go.Figure:
    fig = go.Figure()
    if not df.empty:
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["cpu_percent"],
                mode="lines",
                name="Utilização CPU",
                line={"color": "#f59e0b", "width": 2},
                hovertemplate="CPU: %{y:.1f}%<extra></extra>",
            )
        )

    fig.add_hline(
        y=limit_pct,
        line_dash="dash",
        line_color="#dc2626",
        line_width=1.8,
        annotation_text=f"Threshold: {limit_pct:.1f}%",
        annotation_position="top left",
        annotation_font_color="#dc2626",
    )

    fig.update_layout(
        title={"text": title, "font": {"size": 13, "color": "#cbd5e1"}, "x": 0.01, "y": 0.96},
        height=320,
        margin={"l": 15, "r": 15, "t": 35, "b": 15},
        xaxis_title="Tempo",
        yaxis_title="Utilização (%)",
        yaxis={"range": [0, 105]},
        hovermode="x unified",
        template="plotly_dark",
        paper_bgcolor="#0f172a",
        plot_bgcolor="#1e293b",
    )
    return fig


# ==============================================================================
# BARRA LATERAL TÉCNICA (Resumo do Equipamento)
# ==============================================================================
with st.sidebar:
    st.markdown("### Telemetria SNMP")
    st.caption("Projeto e Administração de Redes")

    st.markdown("**Dispositivo Alvo:**")
    st.code(f"{cfg.get('ip_address')}:{cfg.get('snmp_port')}\nInterface: ifIndex {cfg.get('if_index')}\nSNMP {cfg.get('snmp_version')} ({cfg.get('snmp_community')})", language="text")

    st.markdown("**Limiares de Alerta:**")
    st.write(f"• Tráfego: `{format_bps(cfg.get('traffic_threshold_bps', 15000000.0))}`")
    st.write(f"• CPU: `{cfg.get('cpu_threshold_percent', 80.0)}%`")

    st.markdown("**Canais Notificadores:**")
    active_ch = []
    if cfg.get("alert_popup"):
        active_ch.append("Pop-up")
    if cfg.get("alert_email"):
        active_ch.append("E-mail")
    st.write(f"• `{', '.join(active_ch) if active_ch else 'Nenhum'}`")

    st.divider()

    st.markdown("**Diagnóstico e Testes:**")
    if st.button("Testar Conexão SNMP", use_container_width=True):
        res = st.session_state.collector.test_connection(cfg)
        if res.get("success"):
            st.toast(f"Conectado: {res.get('sys_descr', 'MikroTik RouterOS')}", icon="✅")
        else:
            st.toast(f"Falha: {res.get('error')}", icon="❌")

    if st.button("Gerar Pico no MikroTik (SSH)", use_container_width=True, type="secondary"):
        def _inject_real_snmp_stress() -> None:
            try:
                cmd = ':for i from=1 to=25 do={ :for j from=1 to=3000 do={ :put "BURST_TEST_STREAM_MIKROTIK_0123456789_ABCDEFGHIJ" }; :delay 500ms }'
                subprocess.run(
                    [
                        "ssh",
                        "-o", "StrictHostKeyChecking=no",
                        "-o", "UserKnownHostsFile=/dev/null",
                        "-o", "LogLevel=ERROR",
                        "-p", "2222",
                        "admin@127.0.0.1",
                        cmd,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=35,
                )
            except Exception:
                pass

        threading.Thread(target=_inject_real_snmp_stress, daemon=True).start()
        st.toast("Disparando rajada real de tráfego e CPU no MikroTik via SSH...", icon="⚡")


# ==============================================================================
# CABEÇALHO DO DASHBOARD (Conforme o quadro da sala)
# ==============================================================================
header_c1, header_c2, header_c3 = st.columns([4, 2, 2])

with header_c1:
    st.subheader("Sistema de Monitoramento de Roteador SNMP")
    st.caption("Telemetria contínua de contadores MIB-II e utilização de processamento")

with header_c2:
    new_interval = st.number_input(
        "Intervalo de Coleta (segundos):",
        min_value=1,
        max_value=300,
        value=int(cfg.get("interval_s", 5)),
        step=1,
        help="Rejeita valores menores ou iguais a zero conforme especificação.",
    )
    if new_interval != cfg.get("interval_s"):
        cfg["interval_s"] = int(new_interval)
        config_manager.save_config(cfg)
        st.rerun()

with header_c3:
    st.write("")
    st.write("")
    if st.button("Configurações ⚙️", use_container_width=True):
        modal_configuracoes()


# ==============================================================================
# BLOCO 1: PAINEL TEMPO REAL (RT) - Conforme Wireframe do Quadro
# ==============================================================================
st.markdown('<div class="panel-header">Painel Tempo Real (RT)</div>', unsafe_allow_html=True)

rt_ctrl1, rt_ctrl2, rt_ctrl3, rt_ctrl4, rt_ctrl5 = st.columns([1, 1, 1, 2, 2])

with rt_ctrl1:
    if st.button("Iniciar", use_container_width=True, type="primary"):
        st.session_state.is_running = True
        st.session_state.last_snmp_error = None

with rt_ctrl2:
    if st.button("Pausar", use_container_width=True):
        st.session_state.is_running = False

with rt_ctrl3:
    if st.button("Resetar Tela", use_container_width=True):
        st.session_state.live_points = []
        st.session_state.collector.reset_baseline()
        st.session_state.alert_mgr.reset_state()
        st.session_state.last_snmp_error = None
        st.toast("Visualização em tempo real limpa. Histórico em disco preservado.")

with rt_ctrl4:
    window_map = {"1 minuto": 1, "5 minutos": 5, "10 minutos": 10, "15 minutos": 15, "30 minutos": 30}
    current_win = cfg.get("window_minutes", 5)
    win_label = st.selectbox(
        "Janela Deslizante:",
        list(window_map.keys()),
        index=list(window_map.values()).index(current_win) if current_win in window_map.values() else 1,
        label_visibility="collapsed",
    )
    cfg["window_minutes"] = window_map[win_label]

with rt_ctrl5:
    if st.session_state.last_snmp_error:
        st.markdown('<div class="badge-status badge-error">Falha de Conexão SNMP</div>', unsafe_allow_html=True)
    elif st.session_state.is_running:
        st.markdown('<div class="badge-status badge-online">Coleta Ativa</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="badge-status badge-paused">Coleta Pausada</div>', unsafe_allow_html=True)


# Fragmento para atualização suave sem recarregar a página inteira
@st.fragment(run_every=cfg.get("interval_s", 5) if st.session_state.is_running else None)
def secao_tempo_real() -> None:
    # 1. Coleta e cálculo da nova amostra
    if st.session_state.is_running:
        sample = st.session_state.collector.collect_sample(cfg)
        if not sample.get("success"):
            st.session_state.last_snmp_error = sample.get("error")
        else:
            st.session_state.last_snmp_error = None
            st.session_state.live_points.append(sample)
            st.session_state.live_points = st.session_state.live_points[-2000:]
            historico_csv.append_measurement(
                sample,
                ip=cfg.get("ip_address", "127.0.0.1"),
                iface=f"ifIndex_{cfg.get('if_index', 2)}",
            )

            # Avaliação de Alertas com Histerese (apenas transição Normal -> Excedido)
            alerts = st.session_state.alert_mgr.check_alerts(sample, cfg)
            for a in alerts:
                if cfg.get("alert_popup"):
                    st.toast(
                        f"Alerta: {a['metric_name']} atingiu {a['observed_str']} (Limiar: {a['threshold_str']})",
                        icon="⚠️",
                    )


    # Exibição de falha de conexão (Resiliência)
    if st.session_state.last_snmp_error:
        st.error(
            f"Falha de Comunicação SNMP: {st.session_state.last_snmp_error}. "
            "Tentando reconectar automaticamente a cada ciclo...",
            icon="⚠️",
        )

    # 2. Filtragem da Janela Deslizante
    points = st.session_state.live_points
    df_live = pd.DataFrame(points)
    if not df_live.empty:
        df_live["timestamp"] = pd.to_datetime(df_live["timestamp"], errors="coerce")
        cutoff = datetime.now() - timedelta(minutes=cfg.get("window_minutes", 5))
        visible_df = df_live[df_live["timestamp"] >= cutoff].copy()
    else:
        visible_df = pd.DataFrame()

    # 3. Métricas Numéricas e Gráficos
    if not visible_df.empty:
        latest = visible_df.iloc[-1]
        m1, m2, m3 = st.columns(3)
        m1.metric("Tráfego de Entrada (IN)", f"{latest['inbound_bps']:,.0f} bps", f"{latest['inbound_bps']/1_000_000:.2f} Mbps", delta_color="off")
        m2.metric("Tráfego de Saída (OUT)", f"{latest['outbound_bps']:,.0f} bps", f"{latest['outbound_bps']/1_000_000:.2f} Mbps", delta_color="off")
        m3.metric("Utilização da CPU", f"{latest['cpu_percent']:.1f}%", delta_color="off")

        ch1, ch2 = st.columns(2)
        with ch1:
            st.plotly_chart(
                render_traffic_plot(visible_df, cfg.get("traffic_threshold_bps", 15_000_000.0), "Tráfego IN / OUT (bps) - Janela Deslizante"),
                use_container_width=True,
                key="realtime_traffic_plot",
            )
        with ch2:
            st.plotly_chart(
                render_cpu_plot(visible_df, cfg.get("cpu_threshold_percent", 80.0), "Utilização de CPU (%) - Janela Deslizante"),
                use_container_width=True,
                key="realtime_cpu_plot",
            )
        st.caption(f"Última leitura: {latest['timestamp']} • Janela: {cfg.get('window_minutes', 5)} min • Origem: {latest.get('source', 'SNMP')}")
    else:
        st.info("Aguardando amostras de telemetria. Clique em Iniciar para iniciar a coleta contínua.")


secao_tempo_real()

st.write("")

# ==============================================================================
# BLOCO 2: FILTRO DE HISTÓRICO - Conforme Wireframe do Quadro
# ==============================================================================
st.markdown('<div class="panel-header">Filtro de Histórico</div>', unsafe_allow_html=True)

f_col1, f_col2, f_col3, f_col4 = st.columns([3, 3, 2, 2])

now = datetime.now().replace(microsecond=0)
with f_col1:
    h_sd = st.date_input("Início (Data)", value=st.session_state.hist_start_dt.date(), key="f_start_date")
    h_st = st.time_input("Início (Hora)", value=st.session_state.hist_start_dt.time(), key="f_start_time")

with f_col2:
    h_ed = st.date_input("Fim (Data)", value=st.session_state.hist_end_dt.date(), key="f_end_date")
    h_et = st.time_input("Fim (Hora)", value=st.session_state.hist_end_dt.time(), key="f_end_time")

with f_col3:
    st.write("Atalhos rápidos:")
    if st.button("Últimos 15 min", use_container_width=True):
        st.session_state.hist_start_dt = now - timedelta(minutes=15)
        st.session_state.hist_end_dt = now
        st.rerun()
    if st.button("Últimas 2 horas", use_container_width=True):
        st.session_state.hist_start_dt = now - timedelta(hours=2)
        st.session_state.hist_end_dt = now
        st.rerun()

with f_col4:
    st.write("")
    st.write("")
    if st.button("Filtrar Período", use_container_width=True, type="primary"):
        st.session_state.hist_start_dt = datetime.combine(h_sd, h_st)
        st.session_state.hist_end_dt = datetime.combine(h_ed, h_et)
        st.rerun()

dt_start = st.session_state.hist_start_dt
dt_end = st.session_state.hist_end_dt


# ==============================================================================
# BLOCO 3: PAINEL HISTÓRICO (Hist.) - Conforme Wireframe do Quadro
# ==============================================================================
st.markdown('<div class="panel-header">Painel Histórico (Hist.)</div>', unsafe_allow_html=True)

if dt_end <= dt_start:
    st.error("A data/hora final deve ser estritamente posterior à data/hora inicial.")
else:
    df_all = historico_csv.load_measurements()
    hist_filtered = df_all[
        (df_all["timestamp"] >= dt_start) & (df_all["timestamp"] <= dt_end)
    ].copy()

    st.caption(f"Período consultado: **{dt_start:%d/%m/%Y %H:%M:%S}** até **{dt_end:%d/%m/%Y %H:%M:%S}** ({len(hist_filtered)} amostras)")

    if hist_filtered.empty:
        st.warning("Nenhuma amostra encontrada no intervalo selecionado.")
    else:
        h_ch1, h_ch2 = st.columns(2)
        with h_ch1:
            st.plotly_chart(
                render_traffic_plot(hist_filtered, cfg.get("traffic_threshold_bps", 15_000_000.0), "Tráfego Histórico IN / OUT (bps)"),
                use_container_width=True,
                key="historical_traffic_plot",
            )
        with h_ch2:
            st.plotly_chart(
                render_cpu_plot(hist_filtered, cfg.get("cpu_threshold_percent", 80.0), "Utilização Histórica da CPU (%)"),
                use_container_width=True,
                key="historical_cpu_plot",
            )

        with st.expander("Tabela de Registros Históricos (CSV)", expanded=False):
            st.dataframe(hist_filtered.tail(150), use_container_width=True, hide_index=True)
            csv_bytes = hist_filtered.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Exportar Amostras (CSV)",
                data=csv_bytes,
                file_name=f"telemetria_{dt_start:%Y%m%d_%H%M}_{dt_end:%Y%m%d_%H%M}.csv",
                mime="text/csv",
            )
