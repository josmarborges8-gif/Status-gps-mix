# app.py
# Status GPS - MIX — atualização automática por ETag/Last-Modified (SharePoint) + data sem horário

import os
import json
import shutil
from urllib.parse import urlparse, urlsplit, urlunsplit, parse_qsl, urlencode
from datetime import datetime
from pathlib import Path
from io import BytesIO

import requests
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px

# ============ CONFIG ============
st.set_page_config(page_title="Status GPS - MIX", layout="wide")

ARQUIVO = Path("STATUS_GPS.xlsx")
META = Path("STATUS_GPS.meta.json")  # guarda ETag/Last-Modified
AUTO_CHECK_MINUTES = 60              # intervalo da checagem automática

# Seu link do SharePoint (padrão). Certifique-se de compartilhar como "Qualquer pessoa com o link – Visualizador".
DATA_URL_DEFAULT = (
    "https://grupoecorodovias-my.sharepoint.com/:x:/g/personal/"
    "josmar_silva_ecovias_com_br/ES2Gw9BPByRMkK9prUwHzkkBUFcNUIfCWXN-sQfaaElF5A"
)

CORES = {"OK": "#2ecc71", "ATENÇÃO": "#f1c40f", "CRÍTICO": "#db4a3a", "SEM DADO": "#000000"}
ORDEM_STATUS = ["CRÍTICO", "ATENÇÃO", "OK", "SEM DADO"]

# ============ CSS ============
BASE_CSS = """
<style>
:root{ --pad-top: 1.0rem; --pad-bottom: 5.6rem; --fg-muted: #6c757d; }
.block-container { padding-top: var(--pad-top); padding-bottom: var(--pad-bottom); }
h1.app-title { text-align:center; margin: 0; }
.left-update { text-align: left; color: var(--fg-muted); margin: .2rem 0 .4rem 0; font-size: 0.92rem; }

/* Botões do gráfico */
.filter-row .stButton>button{
  width: 100%; padding: 8px 0; border-radius: 10px;
  display: flex; align-items: center; justify-content: center; text-align: center;
  border: 1px solid #e2e8f0;
}
.filter-row div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(3) .stButton > button{
  transform: translateX(1px) !important; letter-spacing: .4px !important;
}

/* Legenda abaixo da pizza */
.pie-legend { display:flex; align-items:center; justify-content:center; gap:18px; margin-top:4px; font-size:.92rem; }
.lg-item { display:flex; align-items:center; gap:8px; }
.lg-dot { width:10px; height:10px; border-radius:999px; display:inline-block; }

/* Resumo horizontal (sem cores) */
.sumh-card{
  margin-top:8px; background:#fff; border:1px solid #eef0f3; border-radius:12px; padding:10px 12px;
  box-shadow:0 1px 2px rgba(16,24,40,.04),0 1px 3px rgba(16,24,40,.08);
}
.sumh-title{ margin:0 0 6px 0; font-weight:700; font-size:.98rem; color:#111827; text-align:center; }
.sumh-grid{ display:grid; grid-template-columns: repeat(4, minmax(110px, 1fr)); gap:8px; align-items:start; }
.sumh-item{ display:flex; flex-direction:column; align-items:center; gap:6px; padding:6px 0; }
.sumh-name{ font-weight:600; color:#111827; text-transform: none; }
.sumh-count{ font-weight:800; font-size:1.08rem; color:#111827; }

/* Rodapé fixo */
.legend-title { position: fixed; left:0; right:0; bottom:50px; text-align:center; font-weight:600;
                color:#495057; z-index:9999; font-size:.95rem; }
.footer-legend{ position: fixed; left:0; right:0; bottom:0; background:#f8f9fa; border-top:1px solid #e9ecef;
                padding:8px 16px; text-align:center; font-size:.9rem; z-index:9999; }

/* Hover */
.filter-row .stButton>button:hover{ background:#f8fafc; }
</style>
"""
COMPACT_CSS = """
<style>
:root{ --pad-top: .8rem; --pad-bottom: 5.2rem; }
.pie-legend{ font-size:.88rem; margin-top:2px; }
.sumh-card{ padding:8px 10px; }
.sumh-title{ font-size:.94rem; }
.sumh-count{ font-size:1.02rem; }
.filter-row .stButton>button{ padding:6px 0; }
</style>
"""

def css_with_active_filter(active: str | None) -> str:
    if not active:
        return "<style></style>"
    idx = {"CRÍTICO": 1, "ATENÇÃO": 2, "OK": 3, "SEM DADO": 4}.get(str(active).upper())
    if not idx:
        return "<style></style>"
    palette = {
        1: ("#ffe5e5", "#db4a3a", "#b42318"),
        2: ("#fff6cc", "#f1c40f", "#8a6d00"),
        3: ("#e8f8ef", "#2ecc71", "#186a3b"),
        4: ("#f2f2f2", "#000000", "#000000"),
    }
    bg, br, fg = palette[idx]
    return f"""
    <style>
    .filter-row div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child({idx}) .stButton > button{{
        background:{bg} !important; border-color:{br} !important; color:{fg} !important; font-weight:700;
    }}
    </style>
    """

# ============ FUNÇÕES DE DADOS ============
def _strip_quotes(s: str) -> str:
    return s.strip().strip('"').strip("'")

def _ensure_download_param(url: str) -> str:
    """Garante ?download=1 em links do OneDrive/SharePoint."""
    try:
        p = urlsplit(url)
        q = dict(parse_qsl(p.query))
        if "download" not in q:
            q["download"] = "1"
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))
    except Exception:
        return url if ("download=" in url) else (url + ("&" if "?" in url else "?") + "download=1")

def get_data_url() -> str:
    raw = st.secrets.get("DATA_URL", os.getenv("DATA_URL", DATA_URL_DEFAULT))
    url = _strip_quotes(str(raw))
    return _ensure_download_param(url) if url else ""

def format_dt_only_date(dt: datetime) -> str:
    """Só a data (dd/mm/aaaa)."""
    return dt.strftime("%d/%m/%Y") if isinstance(dt, datetime) else "-"

def _is_http(url: str) -> bool:
    try:
        return urlparse(url).scheme.lower() in ("http", "https")
    except Exception:
        return False

def _load_meta() -> dict:
    if META.exists():
        try:
            return json.loads(META.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_meta(meta: dict):
    try:
        META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def _remote_head(url: str) -> tuple[dict, int]:
    """Tenta HEAD; se não suportar, tenta GET de 1 byte (range). Retorna (headers, status_code)."""
    headers_common = {"User-Agent": "Mozilla/5.0 (StreamlitApp)"}
    try:
        r = requests.head(url, timeout=30, allow_redirects=True, headers=headers_common)
        if r.status_code < 400 and r.headers:
            return r.headers, r.status_code
    except Exception:
        pass
    # Fallback: GET 1 byte
    try:
        h = headers_common | {"Range": "bytes=0-0"}
        r = requests.get(url, timeout=30, allow_redirects=True, headers=h, stream=True)
        return r.headers, r.status_code
    except Exception as e:
        raise e

def _content_looks_excel(headers: dict, content_first_bytes: bytes) -> bool:
    ctype = (headers or {}).get("Content-Type", "").lower()
    if "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in ctype:
        return True
    # fallback: arquivo .xlsx (zip) começa com bytes 'PK'
    return content_first_bytes.startswith(b"PK")

def download_if_changed(url: str, destino: Path) -> str:
    """Baixa apenas se ETag/Last-Modified mudarem. Salva META com infos."""
    if not _is_http(url):
        # caminho local/UNC
        p = Path(url)
        if not p.exists():
            raise FileNotFoundError(f"Fonte local não encontrada: {p}")
        if p.resolve() != destino.resolve():
            shutil.copy2(p, destino)
            _save_meta({"source": str(p), "local_copy_mtime": datetime.now().isoformat()})
            return "copiada de caminho local"
        return "arquivo já é a própria fonte"

    meta = _load_meta()
    etag_prev = meta.get("etag")
    lastmod_prev = meta.get("last_modified")

    # 1) Checa cabeçalhos remotos
    headers, _ = _remote_head(url)
    etag_new = headers.get("ETag") or headers.get("Etag") or headers.get("etag")
    lastmod_new = headers.get("Last-Modified") or headers.get("last-modified")

    # 2) Se nada mudou, não baixa
    if etag_prev and etag_new and etag_prev == etag_new:
        return "não modificado (ETag)"
    if lastmod_prev and lastmod_new and lastmod_prev == lastmod_new:
        return "não modificado (Last-Modified)"

    # 3) Baixa conteúdo
    req_headers = {"User-Agent": "Mozilla/5.0 (StreamlitApp)"}
    r = requests.get(url, timeout=60, allow_redirects=True, headers=req_headers)
    r.raise_for_status()

    first = r.content[:4]
    if not _content_looks_excel(r.headers, first):
        raise RuntimeError(
            "O link não retornou um arquivo .xlsx (pode ser página de login do SharePoint). "
            "Deixe o compartilhamento como 'Qualquer pessoa com o link – Visualizador' e use download=1."
        )

    destino.write_bytes(r.content)
    meta.update({
        "etag": etag_new,
        "last_modified": lastmod_new,
        "downloaded_at": datetime.now().isoformat(),
        "source": url,
        "size": len(r.content),
    })
    _save_meta(meta)
    return "baixada por URL"

def atualizar_por_metadados(DATA_URL: str, force: bool = False) -> tuple[bool, str]:
    """Usa ETag/Last-Modified para decidir baixar. Se force=True, baixa sempre."""
    if not DATA_URL:
        return False, "Sem DATA_URL configurada."
    try:
        if force or not ARQUIVO.exists():
            modo = download_if_changed(DATA_URL, ARQUIVO)
            return True, f"Base {modo}."
        # checagem por metadados: baixa se remoto mudou
        modo = download_if_changed(DATA_URL, ARQUIVO)
        if modo.startswith("não modificado"):
            return False, "Base já está atualizada (metadados iguais)."
        return True, f"Base {modo}."
    except Exception as e:
        return False, f"Falha ao atualizar: {e}"

@st.cache_data(show_spinner=False, ttl=AUTO_CHECK_MINUTES*60)
def auto_check_update(DATA_URL: str) -> tuple[bool, str]:
    """Checagem automática com cache TTL para evitar chamadas excessivas."""
    return atualizar_por_metadados(DATA_URL, force=False)

def resolve_fonte_e_mtime(uploaded, destino: Path):
    if uploaded is not None:
        fonte_excel = uploaded.getvalue()
        return fonte_excel, datetime.now(), len(fonte_excel)
    if destino.exists():
        mtime = destino.stat().st_mtime
        return str(destino), datetime.fromtimestamp(mtime), mtime
    return None, None, None

# ============ IO EXCEL ============
@st.cache_data(show_spinner=False, ttl=24*60*60)
def carregar_planilhas(fonte, version_tag=None):
    xl = pd.ExcelFile(BytesIO(fonte), engine="openpyxl") if isinstance(fonte, (bytes, bytearray)) else pd.ExcelFile(fonte, engine="openpyxl")
    nomes = set(xl.sheet_names)
    faltando = [n for n in ("DadosGPS", "StatusGPS") if n not in nomes]
    if faltando:
        st.error(f"As abas obrigatórias {faltando} não foram encontradas no arquivo.")
        st.stop()
    dados = xl.parse("DadosGPS")
    status = xl.parse("StatusGPS")
    plan = None
    for nm in ["Planilha1", "Pontos", "Riscos"]:
        if nm in nomes:
            plan = xl.parse(nm)
            break
    for df in (dados, status, plan) if plan is not None else (dados, status):
        if df is not None:
            df.columns = [str(c).strip() for c in df.columns]
    return dados, status, plan

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Filtro")
    return bio.getvalue()

# ============ PROCESSAMENTO ============
def classificar_status_series(dias: pd.Series) -> pd.Series:
    dias_num = pd.to_numeric(dias, errors="coerce").where(lambda s: s >= 0)
    conds = [
        dias_num.notna() & (dias_num <= 2),
        dias_num.notna() & (dias_num.between(3, 5)),
        dias_num.notna() & (dias_num.between(6, 30)),
    ]
    return pd.Series(np.select(conds, ["OK", "ATENÇÃO", "CRÍTICO"], default="SEM DADO"), index=dias.index)

def normalizar_ok(texto: pd.Series | pd.Index) -> pd.Series:
    s = texto.to_series() if isinstance(texto, pd.Index) else texto.copy()
    return s.astype(str).str.strip().str.replace(r"(?i)^ok(?:ey)?$", "OK", regex=True)

def preparar_posicoes(dados: pd.DataFrame) -> pd.DataFrame:
    need = {"Prefixo", "TipoPosicao", "Latitude", "Longitude", "DataMarcacao"}
    if not need.issubset(dados.columns):
        return pd.DataFrame(columns=["Prefixo", "Latitude", "Longitude", "TipoPosicao", "Concessao"])
    pos = dados[["Prefixo", "TipoPosicao", "Latitude", "Longitude", "DataMarcacao"]].copy()
    if "Concessao" in dados.columns:
        pos["Concessao"] = dados["Concessao"]
    pos["DataMarcacao"] = pd.to_datetime(pos["DataMarcacao"], errors="coerce")
    pos = pos.dropna(subset=["DataMarcacao"])
    pos["prefer_mix"] = np.where(pos["TipoPosicao"].astype(str).str.upper().eq("MIX"), 1, 0)
    pos = pos.sort_values(["Prefixo", "DataMarcacao", "prefer_mix"], ascending=[True, False, False])
    ult = pos.groupby("Prefixo", as_index=False).first()
    for c in ("Latitude", "Longitude"):
        if c in ult.columns:
            ult[c] = pd.to_numeric(ult[c], errors="coerce")
    out_cols = ["Prefixo", "Latitude", "Longitude", "TipoPosicao"] + (["Concessao"] if "Concessao" in ult.columns else [])
    return ult[out_cols].reset_index(drop=True)

# ============ UI HELPERS ============
def make_pie_chart(df: pd.DataFrame, cores: dict):
    fig = px.pie(df, names="Status MIX", color="Status MIX", color_discrete_map=cores, hole=0.4, title="Distribuição Status MIX")
    fig.update_layout(
        title={"text": "Distribuição Status MIX", "x": 0.5, "xanchor": "center", "y": 0.97},
        title_font_size=18,
        height=360,
        margin=dict(l=8, r=8, t=38, b=4),
        showlegend=False,
    )
    return fig

def legend_html(cores: dict) -> str:
    return (
        '<div class="pie-legend">'
        f'<div class="lg-item"><span class="lg-dot" style="background:{cores["OK"]}"></span>OK</div>'
        f'<div class="lg-item"><span class="lg-dot" style="background:{cores["CRÍTICO"]}"></span>CRÍTICO</div>'
        f'<div class="lg-item"><span class="lg-dot" style="background:{cores["ATENÇÃO"]}"></span>ATENÇÃO</div>'
        f'<div class="lg-item"><span class="lg-dot" style="background:{cores["SEM DADO"]}"></span>SEM DADO</div>'
        "</div>"
    )

def build_summary_card_horizontal(df: pd.DataFrame, ordem: list[str]):
    if df.empty:
        return
    counts = df["Status MIX"].astype(str).value_counts().reindex(ordem, fill_value=0)
    items = []
    for nome in ordem:
        qtd = int(counts.get(nome, 0))
        items.append(f'<div class="sumh-item"><div class="sumh-name">{nome}</div><div class="sumh-count">{qtd}</div></div>')
    html = '<div class="sumh-card"><div class="sumh-title">Resumo</div><div class="sumh-grid">' + "".join(items) + "</div></div>"
    st.markdown(html, unsafe_allow_html=True)

def format_status_icon(s: str) -> str:
    return {"OK": "🟢", "ATENÇÃO": "🟡", "CRÍTICO": "🔴", "SEM DADO": "⚫"}.get(s, "⚫")

# ============ SIDEBAR / CSS ============
st.sidebar.header("Configuração")
compact = st.sidebar.toggle("Modo compacto", value=True, help="Reduz espaços para caber tudo numa tela")
uploaded = st.sidebar.file_uploader("Enviar arquivo STATUS_GPS.xlsx", type=["xlsx"], help="Opcional: sobrepõe a base do app.")

st.markdown(BASE_CSS, unsafe_allow_html=True)
if compact:
    st.markdown(COMPACT_CSS, unsafe_allow_html=True)

# ============ ATUALIZAÇÃO AUTOMÁTICA ============
DATA_URL = get_data_url()

# Checagem automática com TTL (roda ~1x por hora)
if uploaded is None and DATA_URL:
    atualizou, msg = auto_check_update(DATA_URL)
    if atualizou:
        st.toast("Base atualizada automaticamente.")

# Botão de forçar atualização
with st.sidebar.expander("Atualização"):
    if st.button("🔄 Atualizar agora", use_container_width=False):
        ok, msg = atualizar_por_metadados(DATA_URL, force=True)
        if ok:
            st.cache_data.clear()
            try:
                st.rerun()
            except Exception:
                st.experimental_rerun()
        else:
            st.warning(msg)

# Resolve fonte (upload > arquivo baixado)
fonte_excel, last_update_dt, version_tag = resolve_fonte_e_mtime(uploaded, ARQUIVO)
if fonte_excel is None:
    st.error("Não encontrei a base. Configure um DATA_URL válido (Settings → Secrets) ou envie um arquivo pelo upload.")
    st.stop()

# ============ CARREGAR DADOS ============
dados, status, plan = carregar_planilhas(fonte_excel, version_tag)

st.markdown('<h1 class="app-title">📡 Status GPS Viaturas (MIX)</h1>', unsafe_allow_html=True)

if "Dias MIX" in status.columns:
    status["Status MIX"] = classificar_status_series(status["Dias MIX"])
else:
    status["Status MIX"] = "SEM DADO"
status["Status MIX"] = normalizar_ok(status["Status MIX"])
status["Status MIX"] = pd.Categorical(status["Status MIX"], categories=ORDEM_STATUS, ordered=True)

posicoes = preparar_posicoes(dados)
df = status.merge(posicoes, on="Prefixo", how="left")
df["Status MIX"] = pd.Categorical(df["Status MIX"], categories=ORDEM_STATUS, ordered=True)

if "pie_filter" not in st.session_state:
    st.session_state.pie_filter = None
st.markdown(css_with_active_filter(st.session_state.pie_filter), unsafe_allow_html=True)

# ============ LAYOUT ============
col1, col2 = st.columns([2, 1])

with col1:
    # Última atualização (somente DATA)
    st.markdown(
        f'<div class="left-update">🕒 Última atualização: <strong>{format_dt_only_date(last_update_dt)}</strong></div>',
        unsafe_allow_html=True,
    )

    # Filtros
    concessoes = sorted(df["Concessao"].dropna().unique()) if "Concessao" in df.columns and df["Concessao"].notna().any() else []
    recursos = sorted(df["DescriçãoRecurso"].dropna().unique()) if "DescriçãoRecurso" in df.columns else []
    f_conc = st.multiselect("Concessão", concessoes, default=concessoes)
    f_recurso = st.multiselect("Recurso", recursos, default=recursos)

    # Aplicar filtros e possível filtro do gráfico
    df_f = df.copy()
    if st.session_state.pie_filter:
        df_f = df_f[df_f["Status MIX"].astype(str) == st.session_state.pie_filter]
    if f_recurso and "DescriçãoRecurso" in df_f.columns:
        df_f = df_f[df_f["DescriçãoRecurso"].isin(f_recurso)]
    if f_conc and "Concessao" in df_f.columns:
        df_f = df_f[df_f["Concessao"].isin(f_conc)]

    df_f["Status"] = df_f["Status MIX"].astype(str).map(format_status_icon)

    cols_ordem = ["Status", "DescriçãoRecurso", "Prefixo", "Dias MIX", "Status MIX", "Concessao", "Latitude", "Longitude", "TipoPosicao"]
    cols_exist = [c for c in cols_ordem if c in df_f.columns]
    if df_f.empty:
        st.info("Nenhum registro encontrado para os filtros aplicados.")
    else:
        order_cols = [c for c in ["Status MIX", "DescriçãoRecurso", "Prefixo"] if c in df_f.columns]
        grid_df = df_f[cols_exist].sort_values(order_cols, na_position="last")
        st.dataframe(grid_df, width="stretch")
        st.download_button(
            "📥 Baixar tabela filtrada (Excel)",
            data=to_excel_bytes(grid_df),
            file_name="status_gps_mix_filtrado.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

with col2:
    fig = make_pie_chart(df, CORES)
    st.plotly_chart(fig, width="stretch")

    st.markdown('<div class="filter-row">', unsafe_allow_html=True)
    bcols = st.columns(5, gap="small")
    if bcols[0].button("CRÍTICO", width="stretch"):
        st.session_state.pie_filter = "CRÍTICO"
    if bcols[1].button("ATENÇÃO", width="stretch"):
        st.session_state.pie_filter = "ATENÇÃO"
    if bcols[2].button("OK", width="stretch"):
        st.session_state.pie_filter = "OK"
    if bcols[3].button("SEM DADO", width="stretch"):
        st.session_state.pie_filter = "SEM DADO"
    if bcols[4].button("Limpar filtro", width="stretch"):
        st.session_state.pie_filter = None
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(css_with_active_filter(st.session_state.pie_filter), unsafe_allow_html=True)
    st.markdown(legend_html(CORES), unsafe_allow_html=True)
    build_summary_card_horizontal(df, ORDEM_STATUS)

# Rodapé (faixas)
st.markdown('<div class="legend-title">Legenda</div>', unsafe_allow_html=True)
st.markdown(
    """
    <div class="footer-legend">
      📌 Verde ≤ 2 dias • Amarelo 3–5 dias • Vermelho 6–30 dias • Preto &gt; 30 dias ou sem dados
    </div>
    """,
    unsafe_allow_html=True,
)
