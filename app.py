"""
Autonomous CloudOps Command Center — Streamlit Frontend.
"""

import time
import uuid
import streamlit as st
from datetime import datetime, timezone
from typing import Optional

from src.agents.orchestrator import AgentGateway
from src.models.query import Query
from src.services.dynamo_service import DynamoDBService
from src.core.security import security_manager, User, UserRole, Permission

st.set_page_config(
    page_title="CloudOps Command Center",
    layout="wide",
    page_icon="🛡️",
    initial_sidebar_state="expanded"
)

# ── Enterprise Glassmorphism Theme ────────────────────────────────────────────

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

  /* ── Base Reset & Typography ─────────────────────────────────── */
  html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    background-color: #08081a !important;
    color: #e2e8f0 !important;
  }

  .stApp {
    background:
      radial-gradient(circle at 15% 15%, rgba(99, 102, 241, 0.10), transparent 40%),
      radial-gradient(circle at 85% 10%, rgba(34, 211, 238, 0.08), transparent 42%),
      radial-gradient(circle at 75% 85%, rgba(168, 85, 247, 0.08), transparent 45%),
      #08081a !important;
  }

  /* ── Selection ───────────────────────────────────────────────── */
  ::selection {
    background: rgba(99, 102, 241, 0.35);
    color: #f8fafc;
  }

  code, pre, .stCode, [data-testid="stCode"] {
    font-family: 'JetBrains Mono', 'Fira Code', monospace !important;
  }

  /* ── Scrollbar Styling ───────────────────────────────────────── */
  ::-webkit-scrollbar {
    width: 6px;
    height: 6px;
  }
  ::-webkit-scrollbar-track {
    background: #0f0f23;
    border-radius: 3px;
  }
  ::-webkit-scrollbar-thumb {
    background: #374151;
    border-radius: 3px;
  }
  ::-webkit-scrollbar-thumb:hover {
    background: #6366f1;
  }

  /* ── Glass Panel Base ────────────────────────────────────────── */
  .glass-panel {
    background: rgba(26, 26, 46, 0.7) !important;
    backdrop-filter: blur(12px) !important;
    -webkit-backdrop-filter: blur(12px) !important;
    border: 1px solid rgba(99, 102, 241, 0.15) !important;
    border-radius: 16px !important;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3) !important;
  }

  /* ── Sidebar ─────────────────────────────────────────────────── */
  [data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1a1a2e 0%, #16213e 50%, #0f0f23 100%) !important;
    border-right: 1px solid rgba(99, 102, 241, 0.2) !important;
  }
  [data-testid="stSidebar"] [data-testid="stMarkdown"] {
    color: #e2e8f0 !important;
  }

  /* ── Sidebar Buttons ─────────────────────────────────────────── */
  [data-testid="stSidebar"] .stButton button {
    background: rgba(99, 102, 241, 0.1) !important;
    color: #e2e8f0 !important;
    border: 1px solid rgba(99, 102, 241, 0.3) !important;
    border-radius: 10px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    padding: 10px 16px !important;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
  }
  [data-testid="stSidebar"] .stButton button:hover {
    background: rgba(99, 102, 241, 0.25) !important;
    border-color: #6366f1 !important;
    box-shadow: 0 0 20px rgba(99, 102, 241, 0.2) !important;
    transform: translateY(-1px) !important;
  }
  [data-testid="stSidebar"] .stButton button:active {
    background: rgba(99, 102, 241, 0.4) !important;
    transform: translateY(0) !important;
  }

  /* ── Main Area Buttons ───────────────────────────────────────── */
  .stButton button {
    background: linear-gradient(135deg, rgba(99, 102, 241, 0.15), rgba(34, 211, 238, 0.1)) !important;
    color: #e2e8f0 !important;
    border: 1px solid rgba(99, 102, 241, 0.3) !important;
    border-radius: 10px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
  }
  .stButton button:hover {
    background: linear-gradient(135deg, rgba(99, 102, 241, 0.3), rgba(34, 211, 238, 0.2)) !important;
    border-color: #6366f1 !important;
    box-shadow: 0 4px 20px rgba(99, 102, 241, 0.25) !important;
    transform: translateY(-1px) !important;
  }

  /* ── Metrics Cards ───────────────────────────────────────────── */
  [data-testid="stMetric"] {
    position: relative !important;
    overflow: hidden !important;
    background: rgba(26, 26, 46, 0.6) !important;
    backdrop-filter: blur(8px) !important;
    border: 1px solid rgba(99, 102, 241, 0.2) !important;
    border-radius: 14px !important;
    padding: 20px !important;
    transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1) !important;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2) !important;
  }
  /* Animated gradient accent strip along the top */
  [data-testid="stMetric"]::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, #6366f1, #22d3ee, #a855f7, #6366f1);
    background-size: 300% 100%;
    opacity: 0.7;
    animation: metric-sheen 6s linear infinite;
  }
  /* Diagonal light sweep on hover */
  [data-testid="stMetric"]::after {
    content: '';
    position: absolute;
    top: -50%; left: -60%;
    width: 40%; height: 200%;
    background: linear-gradient(120deg, transparent, rgba(255, 255, 255, 0.08), transparent);
    transform: skewX(-20deg);
    transition: left 0.7s ease;
    pointer-events: none;
  }
  [data-testid="stMetric"]:hover::after {
    left: 130%;
  }
  [data-testid="stMetric"]:hover {
    border-color: rgba(99, 102, 241, 0.6) !important;
    box-shadow: 0 10px 34px rgba(99, 102, 241, 0.22), 0 0 60px rgba(34, 211, 238, 0.08) !important;
    transform: translateY(-4px) !important;
  }
  @keyframes metric-sheen {
    to { background-position: 300% 0; }
  }
  [data-testid="stMetric"] label {
    color: #94a3b8 !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    font-size: 0.8rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.05em !important;
  }
  [data-testid="stMetric"] [data-testid="stMetricValue"] {
    color: #22d3ee !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 1.5rem !important;
    font-weight: 600 !important;
  }

  /* ── Chat Messages ───────────────────────────────────────────── */
  [data-testid="stChatMessage"] {
    background: rgba(22, 33, 62, 0.5) !important;
    backdrop-filter: blur(8px) !important;
    border: 1px solid rgba(99, 102, 241, 0.1) !important;
    border-radius: 16px !important;
    padding: 16px 20px !important;
    margin-bottom: 12px !important;
    animation: fadeInUp 0.45s cubic-bezier(0.16, 1, 0.3, 1) !important;
    transition: border-color 0.3s ease, box-shadow 0.3s ease !important;
  }
  [data-testid="stChatMessage"]:hover {
    box-shadow: 0 6px 28px rgba(0, 0, 0, 0.25) !important;
  }
  /* Chat avatar glow */
  [data-testid="stChatMessage"] [data-testid="chatAvatarIcon-user"],
  [data-testid="stChatMessage"] [data-testid="chatAvatarIcon-assistant"] {
    box-shadow: 0 0 14px rgba(99, 102, 241, 0.45) !important;
  }
  [data-testid="stChatMessage"][data-testid*="user"] {
    background: rgba(99, 102, 241, 0.08) !important;
    border-color: rgba(99, 102, 241, 0.2) !important;
    border-left: 3px solid #6366f1 !important;
  }
  [data-testid="stChatMessage"][data-testid*="assistant"] {
    background: rgba(34, 211, 238, 0.05) !important;
    border-color: rgba(34, 211, 238, 0.15) !important;
    border-left: 3px solid #22d3ee !important;
  }

  @keyframes fadeInUp {
    from {
      opacity: 0;
      transform: translateY(10px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  /* ── Chat Input ──────────────────────────────────────────────── */
  [data-testid="stChatInput"] textarea,
  .stChatInputContainer textarea {
    background: rgba(26, 26, 46, 0.8) !important;
    color: #e2e8f0 !important;
    border: 1px solid rgba(99, 102, 241, 0.3) !important;
    border-radius: 12px !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 0.95rem !important;
    padding: 14px 18px !important;
    transition: all 0.3s ease !important;
  }
  [data-testid="stChatInput"] textarea:focus,
  .stChatInputContainer textarea:focus {
    border-color: #6366f1 !important;
    box-shadow: 0 0 20px rgba(99, 102, 241, 0.2), 0 0 40px rgba(99, 102, 241, 0.05) !important;
  }

  /* ── HITL Warning ────────────────────────────────────────────── */
  .hitl-warning {
    background: linear-gradient(135deg, rgba(245, 158, 11, 0.1), rgba(239, 68, 68, 0.08)) !important;
    border: 1px solid rgba(245, 158, 11, 0.4) !important;
    border-left: 4px solid #f59e0b !important;
    border-radius: 12px !important;
    padding: 20px 24px !important;
    color: #fbbf24 !important;
    font-family: 'Inter', sans-serif !important;
    animation: hitl-pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite !important;
    position: relative;
  }
  .hitl-warning::before {
    content: '';
    position: absolute;
    inset: -1px;
    border-radius: 12px;
    padding: 1px;
    background: linear-gradient(135deg, #f59e0b, #ef4444, #f59e0b);
    -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
    -webkit-mask-composite: xor;
    mask-composite: exclude;
    opacity: 0.5;
    animation: hitl-border-rotate 3s linear infinite;
  }
  .hitl-title {
    font-size: 0.85rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #f59e0b;
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .hitl-body {
    color: #fde68a;
    font-size: 0.95rem;
    line-height: 1.6;
  }
  @keyframes hitl-pulse {
    0%, 100% { box-shadow: 0 0 8px rgba(245, 158, 11, 0.2); }
    50% { box-shadow: 0 0 24px rgba(245, 158, 11, 0.35), 0 0 48px rgba(245, 158, 11, 0.1); }
  }
  @keyframes hitl-border-rotate {
    to { filter: hue-rotate(360deg); }
  }

  /* ── Headers ─────────────────────────────────────────────────── */
  h1, h2, h3, h4 {
    color: #e2e8f0 !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
  }

  /* ── Form Inputs ─────────────────────────────────────────────── */
  .stTextInput input, .stSelectbox > div > div {
    background: rgba(26, 26, 46, 0.8) !important;
    color: #e2e8f0 !important;
    border: 1px solid rgba(99, 102, 241, 0.2) !important;
    border-radius: 10px !important;
    font-family: 'Inter', sans-serif !important;
    transition: all 0.3s ease !important;
  }
  .stTextInput input:focus {
    border-color: #6366f1 !important;
    box-shadow: 0 0 12px rgba(99, 102, 241, 0.15) !important;
  }
  .stSelectbox > div > div {
    background: rgba(26, 26, 46, 0.8) !important;
  }

  /* ── Expander (Technical Details) ────────────────────────────── */
  .streamlit-expanderHeader {
    background: rgba(26, 26, 46, 0.5) !important;
    border: 1px solid rgba(99, 102, 241, 0.15) !important;
    border-radius: 10px !important;
    color: #94a3b8 !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 0.85rem !important;
  }
  .streamlit-expanderContent {
    background: rgba(15, 15, 35, 0.6) !important;
    border: 1px solid rgba(99, 102, 241, 0.1) !important;
    border-radius: 0 0 10px 10px !important;
  }

  /* ── Dividers ────────────────────────────────────────────────── */
  hr {
    border-color: rgba(99, 102, 241, 0.15) !important;
    margin: 24px 0 !important;
  }

  /* ── Sign-in Card ────────────────────────────────────────────── */
  .signin-container {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 60vh;
  }
  .signin-card {
    max-width: 460px;
    margin: 40px auto;
    padding: 40px 36px;
    background: rgba(26, 26, 46, 0.85);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid rgba(99, 102, 241, 0.2);
    border-radius: 20px;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4), 0 0 80px rgba(99, 102, 241, 0.05);
  }
  .signin-brand {
    text-align: center;
    margin-bottom: 32px;
  }
  .signin-card {
    position: relative;
  }
  .signin-card::before {
    content: '';
    position: absolute;
    inset: -1px;
    border-radius: 20px;
    padding: 1px;
    background: linear-gradient(135deg, #6366f1, #22d3ee, #a855f7, #6366f1);
    background-size: 300% 300%;
    -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
    -webkit-mask-composite: xor;
    mask-composite: exclude;
    opacity: 0.6;
    animation: signin-border 8s ease infinite;
    pointer-events: none;
  }
  @keyframes signin-border {
    0%, 100% { background-position: 0% 50%; }
    50%      { background-position: 100% 50%; }
  }
  .signin-brand h1 {
    font-size: 2rem !important;
    background: linear-gradient(135deg, #818cf8, #22d3ee, #a855f7, #818cf8);
    background-size: 250% 100%;
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 8px;
    animation: title-shimmer 6s ease infinite;
  }
  @keyframes title-shimmer {
    0%, 100% { background-position: 0% 50%; }
    50%      { background-position: 100% 50%; }
  }
  .signin-brand p {
    color: #94a3b8;
    font-size: 0.9rem;
  }

  /* ── Gradient Header Banner ──────────────────────────────────── */
  .header-banner {
    background: linear-gradient(135deg, #1a1a2e, #16213e, #1a1a2e);
    background-size: 200% 200%;
    animation: gradient-shift 8s ease infinite;
    border: 1px solid rgba(99, 102, 241, 0.2);
    border-radius: 16px;
    padding: 24px 28px;
    margin-bottom: 24px;
    position: relative;
    overflow: hidden;
  }
  .header-banner::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: radial-gradient(ellipse at 30% 50%, rgba(99, 102, 241, 0.08) 0%, transparent 70%);
    pointer-events: none;
  }
  .header-banner::after {
    content: '';
    position: absolute;
    top: 0; left: -30%;
    width: 30%; height: 100%;
    background: linear-gradient(110deg, transparent, rgba(255, 255, 255, 0.06), transparent);
    transform: skewX(-18deg);
    animation: banner-sweep 7s ease-in-out infinite;
    pointer-events: none;
  }
  @keyframes banner-sweep {
    0%   { left: -30%; }
    45%  { left: 130%; }
    100% { left: 130%; }
  }
  .header-banner h2 {
    margin: 0 !important;
    font-size: 1.6rem !important;
    position: relative;
    background: linear-gradient(135deg, #f8fafc 20%, #818cf8 45%, #22d3ee 70%, #f8fafc 90%);
    background-size: 250% 100%;
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    animation: title-shimmer 7s ease infinite;
  }
  .header-banner p {
    margin: 6px 0 0 !important;
    color: #94a3b8 !important;
    font-size: 0.9rem;
    position: relative;
  }
  @keyframes gradient-shift {
    0% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
    100% { background-position: 0% 50%; }
  }

  /* ── Status Bar ──────────────────────────────────────────────── */
  .status-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 20px;
    background: rgba(15, 15, 35, 0.8);
    backdrop-filter: blur(8px);
    border: 1px solid rgba(99, 102, 241, 0.1);
    border-radius: 10px;
    margin-bottom: 20px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    color: #94a3b8;
  }
  .status-bar .status-item {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #10b981;
    box-shadow: 0 0 8px rgba(16, 185, 129, 0.6);
    animation: status-blink 2s ease-in-out infinite;
  }
  @keyframes status-blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }

  /* ── User Avatar in Sidebar ──────────────────────────────────── */
  .user-avatar-section {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 16px;
    background: rgba(99, 102, 241, 0.08);
    border: 1px solid rgba(99, 102, 241, 0.15);
    border-radius: 12px;
    margin-bottom: 12px;
  }
  .user-avatar {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: linear-gradient(135deg, #6366f1, #22d3ee);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: 1rem;
    color: #fff;
    flex-shrink: 0;
  }
  .user-info {
    overflow: hidden;
  }
  .user-info .user-name {
    font-weight: 600;
    font-size: 0.9rem;
    color: #e2e8f0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .user-info .user-meta {
    font-size: 0.75rem;
    color: #94a3b8;
    font-family: 'JetBrains Mono', monospace;
  }

  /* ── Animated Aurora Background + Floating Orbs ──────────────── */
  .pattern-bg {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
    z-index: -1;
    overflow: hidden;
  }
  .pattern-bg::before {
    content: '';
    position: absolute;
    inset: -30%;
    background:
      conic-gradient(from 180deg at 50% 50%,
        rgba(99, 102, 241, 0.06),
        rgba(34, 211, 238, 0.05),
        rgba(168, 85, 247, 0.06),
        rgba(99, 102, 241, 0.06));
    filter: blur(80px);
    animation: aurora-spin 40s linear infinite;
  }
  .orb {
    position: absolute;
    border-radius: 50%;
    filter: blur(60px);
    opacity: 0.5;
    animation: orb-float 18s ease-in-out infinite;
  }
  .orb.orb-1 {
    width: 420px; height: 420px;
    top: -80px; left: -60px;
    background: radial-gradient(circle, rgba(99, 102, 241, 0.5), transparent 70%);
  }
  .orb.orb-2 {
    width: 360px; height: 360px;
    top: 30%; right: -80px;
    background: radial-gradient(circle, rgba(34, 211, 238, 0.4), transparent 70%);
    animation-delay: -6s;
    animation-duration: 22s;
  }
  .orb.orb-3 {
    width: 300px; height: 300px;
    bottom: -60px; left: 35%;
    background: radial-gradient(circle, rgba(168, 85, 247, 0.4), transparent 70%);
    animation-delay: -11s;
    animation-duration: 26s;
  }
  @keyframes aurora-spin {
    to { transform: rotate(360deg); }
  }
  @keyframes orb-float {
    0%, 100% { transform: translate(0, 0) scale(1); }
    33%      { transform: translate(40px, -30px) scale(1.08); }
    66%      { transform: translate(-30px, 40px) scale(0.95); }
  }
  @media (prefers-reduced-motion: reduce) {
    .pattern-bg::before, .orb { animation: none !important; }
  }

  /* ── Section Labels ──────────────────────────────────────────── */
  .section-label {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: #6366f1;
    margin: 20px 0 8px;
    padding-left: 2px;
  }

  /* ── Sidebar Version Badge ───────────────────────────────────── */
  .version-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    background: rgba(99, 102, 241, 0.1);
    border: 1px solid rgba(99, 102, 241, 0.2);
    border-radius: 20px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    color: #94a3b8;
  }

  /* ── Metric Card Icons ───────────────────────────────────────── */
  .metric-icon {
    font-size: 1.5rem;
    margin-bottom: 6px;
    display: block;
  }
</style>
""", unsafe_allow_html=True)


# Animated aurora background with floating orbs
st.markdown(
    '<div class="pattern-bg">'
    '<div class="orb orb-1"></div>'
    '<div class="orb orb-2"></div>'
    '<div class="orb orb-3"></div>'
    '</div>',
    unsafe_allow_html=True,
)


# ── Cached resources ──────────────────────────────────────────────────────────

@st.cache_resource
def get_gateway():
    return AgentGateway()

@st.cache_resource
def get_dynamo():
    return DynamoDBService()


# ── Sign-in page ──────────────────────────────────────────────────────────────

def show_signin_page():
    """Render a centered sign-in form with glassmorphism card."""
    st.markdown("""
    <div class="signin-container">
        <div class="signin-card">
            <div class="signin-brand">
                <h1>🛡️ CloudOps</h1>
                <p>Autonomous SRE Command Center</p>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Center the form with columns
    _, col, _ = st.columns([1, 2, 1])
    with col:
        with st.form("signin_form", clear_on_submit=False):
            st.markdown("#### 🔐 Sign In")
            st.caption("Enter your credentials to access the platform")
            email = st.text_input("Email", placeholder="you@company.com")
            tenant_id = st.text_input(
                "Tenant ID",
                placeholder="my-company-123",
                help="Your organisation identifier. Any value works for demo."
            )
            role = st.selectbox("Role", ["operator", "sre", "admin", "viewer"])
            submitted = st.form_submit_button("Sign In →", use_container_width=True)

        if submitted:
            # Validate inputs
            if not email or "@" not in email:
                st.error("Enter a valid email address.")
                return
            if not tenant_id or len(tenant_id.strip()) < 3:
                st.error("Tenant ID must be at least 3 characters.")
                return

            tenant_id = tenant_id.strip()
            user_id = email.split("@")[0]
            user_role = UserRole(role)

            user = User(
                user_id=user_id,
                email=email,
                role=user_role,
                tenant_id=tenant_id,
                permissions=security_manager.role_permissions.get(user_role, []),
            )

            st.session_state.authenticated_user = user
            st.session_state.messages = []          # fresh chat on each login
            st.rerun()


# ── Auth gate ─────────────────────────────────────────────────────────────────

if "authenticated_user" not in st.session_state:
    show_signin_page()
    st.stop()

user: User = st.session_state.authenticated_user


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    # User avatar section
    initials = user.email[0].upper()
    st.markdown(f"""
    <div class="user-avatar-section">
        <div class="user-avatar">{initials}</div>
        <div class="user-info">
            <div class="user-name">{user.email}</div>
            <div class="user-meta">{user.role.value} · {user.tenant_id}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("🚪 Sign Out", use_container_width=True):
        del st.session_state.authenticated_user
        st.session_state.messages = []
        st.rerun()

    st.markdown('<div class="section-label">💬 Chat Mode</div>', unsafe_allow_html=True)
    chat_mode = st.radio(
        "Chat mode",
        ["🤖 Agent Actions", "📚 Knowledge Base"],
        key="chat_mode",
        label_visibility="collapsed",
        help=(
            "Agent Actions: the AI reasons over 275+ AWS tools and can perform "
            "operations (with confirmation).\n\n"
            "Knowledge Base: look up historical resolutions and how-tos from the "
            "Bedrock Knowledge Base — read-only, no AWS actions."
        ),
    )
    kb_mode = chat_mode.endswith("Knowledge Base")
    st.caption(
        "📚 Read-only lookup from historical resolutions."
        if kb_mode else
        "🤖 Live agent — can inspect and (with confirmation) change AWS resources."
    )

    st.divider()
    st.markdown('<div class="section-label">Navigation</div>', unsafe_allow_html=True)
    st.divider()

    # Quick actions — SRE / Admin only
    if user.role in [UserRole.SRE, UserRole.ADMIN]:
        st.markdown('<div class="section-label">⚡ Quick Actions</div>', unsafe_allow_html=True)
        if st.button("🔍 System Health Check", use_container_width=True):
            st.session_state.quick_action = "Perform a comprehensive system health check across all AWS services"
        if st.button("🔒 Security Audit", use_container_width=True):
            st.session_state.quick_action = "Run a security compliance audit: check IAM users without MFA, open security groups, and GuardDuty findings"

    # Admin only
    if user.role == UserRole.ADMIN:
        st.markdown('<div class="section-label">⚙️ Administration</div>', unsafe_allow_html=True)
        if st.button("🔄 Sync Knowledge Base", use_container_width=True):
            try:
                from learning_loop import sync_closed_tickets
                with st.spinner("Syncing..."):
                    sync_closed_tickets()
                st.success("Knowledge base synced.")
            except Exception as e:
                st.error(f"Sync failed: {e}")

    st.divider()
    st.markdown("""
    <div style="text-align:center; padding: 8px 0;">
        <span class="version-badge">● v2.0 Enterprise</span>
    </div>
    """, unsafe_allow_html=True)


# ── Status Bar ────────────────────────────────────────────────────────────────

current_time = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
st.markdown(f"""
<div class="status-bar">
    <div class="status-item">
        <div class="status-dot"></div>
        <span>Connected</span>
    </div>
    <div class="status-item">
        <span>Agent: Nova Pro v2.0</span>
    </div>
    <div class="status-item">
        <span>Region: us-east-1</span>
    </div>
    <div class="status-item">
        <span>{current_time}</span>
    </div>
</div>
""", unsafe_allow_html=True)


# ── Main header ───────────────────────────────────────────────────────────────

st.markdown("""
<div class="header-banner">
  <h2>🛡️ CloudOps Command Center</h2>
  <p>Autonomous SRE Platform · 275+ AWS Tools · Real-time Monitoring · AI-Powered Resolution</p>
</div>
""", unsafe_allow_html=True)

# Top metrics bar
c1, c2, c3, c4 = st.columns(4)
c1.metric("Tools Available", "275+")
c2.metric("Agent", "Nova Pro")
c3.metric("Region", "us-east-1")
c4.metric("Role", user.role.value.title())

st.divider()


# ── Chat interface ────────────────────────────────────────────────────────────

orchestrator = get_gateway()
dynamo = get_dynamo()

if "messages" not in st.session_state:
    st.session_state.messages = []

# Inject quick-action as a user message
if "quick_action" in st.session_state and st.session_state.quick_action:
    st.session_state.messages.append({
        "role": "user",
        "content": st.session_state.quick_action
    })
    st.session_state.pending_input = st.session_state.quick_action
    del st.session_state.quick_action

# Render chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg.get("hitl"):
            st.markdown(
                f"""<div class="hitl-warning">
                    <div class="hitl-title">⚠️ Confirmation Required</div>
                    <div class="hitl-body">{msg["content"]}</div>
                </div>""",
                unsafe_allow_html=True
            )
        else:
            st.markdown(msg["content"])
        if user.role in [UserRole.SRE, UserRole.ADMIN] and msg.get("metadata"):
            with st.expander("🔍 Technical Details"):
                st.json(msg["metadata"])

# Resolve the active chat mode (set by the sidebar radio)
kb_mode = st.session_state.get("chat_mode", "🤖 Agent Actions").endswith("Knowledge Base")

# Chat input — placeholder reflects the active mode
if kb_mode:
    _placeholder = "Search past resolutions — e.g. 'how do we fix RDS connection timeouts?'"
else:
    _placeholder = "Ask anything — e.g. 'analyze EC2 i-09a191464d139d778' or 'list my S3 buckets'"
user_input = st.chat_input(_placeholder)

# Use pending quick-action if no direct input
if not user_input and "pending_input" in st.session_state:
    user_input = st.session_state.pop("pending_input")


if user_input:
    # Viewers can read but not execute tools
    if not security_manager.check_permission(user, Permission.VIEW_TICKETS):
        st.error("Your role does not have access to this feature.")
        st.stop()

    with st.chat_message("user"):
        st.markdown(user_input)

    if not any(m["content"] == user_input and m["role"] == "user"
               for m in st.session_state.messages[-2:]):
        st.session_state.messages.append({"role": "user", "content": user_input})

    # Build conversation history (last 4 turns) — strip response prefixes for clean context
    prior = st.session_state.messages[:-1]
    import re as _re
    history = []
    for m in (prior[-4:] if len(prior) > 4 else prior):
        role = "User" if m["role"] == "user" else "Assistant"
        # Strip [TAG] (Nms) prefixes from assistant messages
        content = _re.sub(r'^\[.*?\]\s*\(\d+ms\)\s*\n+', '', m["content"])
        history.append(f"{role}: {content}")

    query = Query(
        text=user_input,
        customer_id=user.tenant_id,
        context={
            "user_id": user.user_id,
            "user_role": user.role.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    start_time = time.time()
    try:
        if kb_mode:
            # Knowledge Base mode — read-only lookup, no AWS actions
            result = orchestrator.query_knowledge_base(query, user=user)
        else:
            # Agent Actions mode — full agent + RAG fallback chain
            result = orchestrator.send(query, history=history or None, user=user)
        elapsed_ms = (time.time() - start_time) * 1000

        tag = ("KB" if kb_mode else result.resolution_type.upper())
        response_text = f"[{tag}] ({elapsed_ms:.0f}ms)\n\n{result.text}"
        is_hitl = "[HITL]" in result.text

        # Auto-create ticket on escalation
        if result.escalate:
            ticket_id = str(uuid.uuid4())[:8]
            dynamo.create_ticket(
                ticket_id=ticket_id,
                query_text=user_input,
                category=("KnowledgeGap" if kb_mode else "Escalated"),
                urgency="high",
                status="escalated",
            )
            if kb_mode:
                response_text += (
                    f"\n\n🎫 No matching resolution found. Ticket `{ticket_id}` "
                    f"created so an SRE can add one — visible in the SRE Portal."
                )
            else:
                response_text += f"\n\n🎫 Ticket `{ticket_id}` created — visible in the SRE Portal."

        def _stream(text):
            for ch in text:
                yield ch
                time.sleep(0.006)

        with st.chat_message("assistant"):
            if is_hitl:
                st.markdown(
                    f"""<div class="hitl-warning">
                        <div class="hitl-title">⚠️ Confirmation Required</div>
                        <div class="hitl-body">{result.text}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
            else:
                st.write_stream(_stream(response_text))

        st.session_state.messages.append({
            "role": "assistant",
            "content": response_text,
            "hitl": is_hitl,
            "metadata": {
                "mode": "knowledge_base" if kb_mode else "agent_actions",
                "resolution_type": result.resolution_type,
                "confidence": result.confidence,
                "processing_time_ms": elapsed_ms,
                "escalated": result.escalate,
                **(result.metadata or {}),
            },
        })

    except Exception as e:
        st.error(f"Error: {str(e)}")
        security_manager.log_audit_event(
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            action="query_error",
            resource="command_center",
            result="FAILURE",
            details={"error": str(e), "query": user_input},
        )
