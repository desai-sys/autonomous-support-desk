"""
SRE Resolution Portal — Human-in-the-Loop ticketing dashboard.
Used by SREs to resolve AI-escalated tickets and trigger the learning loop.
"""

import streamlit as st
from datetime import datetime
from src.services.dynamo_service import DynamoDBService
from learning_loop import sync_closed_tickets

st.set_page_config(
    page_title="SRE Resolution Portal 🛡️",
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
    background-color: #0f0f23 !important;
    color: #e2e8f0 !important;
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

  /* ── Close Ticket Button (Primary Action) ────────────────────── */
  .stButton button[kind="primary"] {
    background: linear-gradient(135deg, rgba(16, 185, 129, 0.2), rgba(34, 211, 238, 0.15)) !important;
    border: 1px solid rgba(16, 185, 129, 0.4) !important;
    color: #10b981 !important;
    font-weight: 600 !important;
  }
  .stButton button[kind="primary"]:hover {
    background: linear-gradient(135deg, rgba(16, 185, 129, 0.35), rgba(34, 211, 238, 0.25)) !important;
    border-color: #10b981 !important;
    box-shadow: 0 4px 24px rgba(16, 185, 129, 0.3), 0 0 40px rgba(16, 185, 129, 0.1) !important;
    color: #34d399 !important;
    transform: translateY(-2px) !important;
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

  /* ── Expander (Ticket Cards) ─────────────────────────────────── */
  .streamlit-expanderHeader {
    background: rgba(26, 26, 46, 0.6) !important;
    backdrop-filter: blur(8px) !important;
    border: 1px solid rgba(99, 102, 241, 0.15) !important;
    border-radius: 12px !important;
    color: #e2e8f0 !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    font-size: 0.9rem !important;
    padding: 14px 18px !important;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
  }
  .streamlit-expanderHeader:hover {
    border-color: rgba(99, 102, 241, 0.4) !important;
    box-shadow: 0 4px 16px rgba(99, 102, 241, 0.1) !important;
    transform: translateY(-1px) !important;
  }
  .streamlit-expanderContent {
    background: rgba(15, 15, 35, 0.7) !important;
    backdrop-filter: blur(6px) !important;
    border: 1px solid rgba(99, 102, 241, 0.1) !important;
    border-top: none !important;
    border-radius: 0 0 12px 12px !important;
    padding: 20px !important;
  }

  /* ── Text Area (Resolution Input) ────────────────────────────── */
  .stTextArea textarea {
    background: rgba(26, 26, 46, 0.8) !important;
    color: #e2e8f0 !important;
    border: 1px solid rgba(99, 102, 241, 0.25) !important;
    border-radius: 12px !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.85rem !important;
    padding: 14px 16px !important;
    transition: all 0.3s ease !important;
    line-height: 1.6 !important;
  }
  .stTextArea textarea:focus {
    border-color: #6366f1 !important;
    box-shadow: 0 0 20px rgba(99, 102, 241, 0.2), 0 0 40px rgba(99, 102, 241, 0.05) !important;
    outline: none !important;
  }
  .stTextArea textarea::placeholder {
    color: #64748b !important;
    font-style: italic !important;
  }

  /* ── Form Inputs & Selectbox ─────────────────────────────────── */
  .stSelectbox > div > div {
    background: rgba(26, 26, 46, 0.8) !important;
    color: #e2e8f0 !important;
    border: 1px solid rgba(99, 102, 241, 0.2) !important;
    border-radius: 10px !important;
    font-family: 'Inter', sans-serif !important;
    transition: all 0.3s ease !important;
  }

  /* ── Headers ─────────────────────────────────────────────────── */
  h1, h2, h3, h4 {
    color: #e2e8f0 !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
  }

  /* ── Dividers ────────────────────────────────────────────────── */
  hr {
    border-color: rgba(99, 102, 241, 0.15) !important;
    margin: 24px 0 !important;
  }

  /* ── Gradient Header Banner ──────────────────────────────────── */
  .header-banner {
    background: linear-gradient(135deg, #1a1a2e, #16213e, #1a1a2e);
    background-size: 200% 200%;
    animation: gradient-shift 8s ease infinite;
    border: 1px solid rgba(99, 102, 241, 0.2);
    border-radius: 16px;
    padding: 28px 32px;
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
  @keyframes title-shimmer {
    0%, 100% { background-position: 0% 50%; }
    50%      { background-position: 100% 50%; }
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

  /* ── Version Badge ───────────────────────────────────────────── */
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

  /* ── Urgency Badges ──────────────────────────────────────────── */
  .urgency-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 20px;
    font-family: 'Inter', sans-serif;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .urgency-critical {
    background: rgba(239, 68, 68, 0.15);
    border: 1px solid rgba(239, 68, 68, 0.4);
    color: #f87171;
    box-shadow: 0 0 12px rgba(239, 68, 68, 0.2);
    animation: critical-pulse 2s ease-in-out infinite;
  }
  .urgency-high {
    background: rgba(245, 158, 11, 0.15);
    border: 1px solid rgba(245, 158, 11, 0.4);
    color: #fbbf24;
    box-shadow: 0 0 8px rgba(245, 158, 11, 0.15);
  }
  .urgency-medium {
    background: rgba(234, 179, 8, 0.12);
    border: 1px solid rgba(234, 179, 8, 0.3);
    color: #fde047;
  }
  .urgency-low {
    background: rgba(16, 185, 129, 0.12);
    border: 1px solid rgba(16, 185, 129, 0.3);
    color: #34d399;
  }
  @keyframes critical-pulse {
    0%, 100% { box-shadow: 0 0 8px rgba(239, 68, 68, 0.2); }
    50% { box-shadow: 0 0 20px rgba(239, 68, 68, 0.4), 0 0 40px rgba(239, 68, 68, 0.1); }
  }

  /* ── Ticket Card HTML ────────────────────────────────────────── */
  .ticket-card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px solid rgba(99, 102, 241, 0.1);
  }
  .ticket-card-header .ticket-id {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    color: #94a3b8;
    background: rgba(99, 102, 241, 0.08);
    padding: 3px 10px;
    border-radius: 6px;
    border: 1px solid rgba(99, 102, 241, 0.15);
  }
  .ticket-card-header .ticket-category {
    font-size: 0.8rem;
    color: #22d3ee;
    font-weight: 500;
  }
  .ticket-meta-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-top: 8px;
  }
  .ticket-meta-item {
    font-size: 0.8rem;
    color: #94a3b8;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .ticket-meta-item .label {
    color: #64748b;
    font-weight: 500;
  }
  .ticket-query-box {
    background: rgba(15, 15, 35, 0.6);
    border: 1px solid rgba(99, 102, 241, 0.1);
    border-left: 3px solid #6366f1;
    border-radius: 8px;
    padding: 14px 16px;
    margin: 12px 0;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.82rem;
    color: #cbd5e1;
    line-height: 1.5;
  }

  /* ── Empty State ─────────────────────────────────────────────── */
  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 60px 40px;
    background: rgba(26, 26, 46, 0.5);
    backdrop-filter: blur(8px);
    border: 1px solid rgba(16, 185, 129, 0.2);
    border-radius: 16px;
    text-align: center;
    margin: 20px 0;
  }
  .empty-state .icon {
    font-size: 3.5rem;
    margin-bottom: 16px;
    animation: float 3s ease-in-out infinite;
  }
  .empty-state h3 {
    color: #10b981 !important;
    font-size: 1.3rem !important;
    margin-bottom: 8px !important;
  }
  .empty-state p {
    color: #94a3b8;
    font-size: 0.9rem;
    max-width: 400px;
  }
  @keyframes float {
    0%, 100% { transform: translateY(0px); }
    50% { transform: translateY(-8px); }
  }

  /* ── Ticket Count Badge ──────────────────────────────────────── */
  .ticket-count-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 14px;
    background: rgba(99, 102, 241, 0.1);
    border: 1px solid rgba(99, 102, 241, 0.25);
    border-radius: 20px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    color: #a5b4fc;
    margin-bottom: 16px;
  }
  .ticket-count-badge .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #f59e0b;
    box-shadow: 0 0 6px rgba(245, 158, 11, 0.5);
    animation: status-blink 2s ease-in-out infinite;
  }

  /* ── Admin Glass Panel ───────────────────────────────────────── */
  .admin-panel {
    background: rgba(26, 26, 46, 0.6);
    backdrop-filter: blur(8px);
    border: 1px solid rgba(99, 102, 241, 0.2);
    border-radius: 12px;
    padding: 16px;
    margin: 12px 0;
  }
  .admin-panel-title {
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #f59e0b;
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  /* ── Fade-in Animation for Tickets ───────────────────────────── */
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
  .stExpander {
    animation: fadeInUp 0.4s ease-out !important;
  }

  /* ── Section Header ──────────────────────────────────────────── */
  .section-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 24px 0 16px;
  }
  .section-header h3 {
    margin: 0 !important;
    font-size: 1.1rem !important;
    color: #e2e8f0 !important;
  }

  /* ── Info Box (Login Prompt) ──────────────────────────────────── */
  .login-prompt {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 60px 40px;
    background: rgba(26, 26, 46, 0.6);
    backdrop-filter: blur(12px);
    border: 1px solid rgba(99, 102, 241, 0.2);
    border-radius: 20px;
    text-align: center;
    max-width: 500px;
    margin: 60px auto;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
  }
  .login-prompt .icon {
    font-size: 3rem;
    margin-bottom: 16px;
  }
  .login-prompt h3 {
    color: #e2e8f0 !important;
    margin-bottom: 8px !important;
  }
  .login-prompt p {
    color: #94a3b8;
    font-size: 0.9rem;
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


# ---------------------------------------------------------------------------
# Mock Authentication
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("""
    <div style="text-align: center; margin-bottom: 16px;">
        <span style="font-size: 1.6rem;">🛡️</span>
        <div style="font-size: 1rem; font-weight: 600; color: #e2e8f0; margin-top: 4px;">SRE Portal</div>
        <div style="font-size: 0.7rem; color: #94a3b8; font-family: 'JetBrains Mono', monospace;">Resolution Dashboard</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="section-label">👤 Identity</div>', unsafe_allow_html=True)

    sre_user = st.selectbox(
        "Select SRE Profile",
        ["— Select User —", "SRE-OnCall-1", "SRE-OnCall-2", "Admin"],
        key="sre_user",
        label_visibility="collapsed"
    )

if sre_user == "— Select User —":
    st.markdown("""
    <div class="header-banner">
        <h2>🛡️ SRE Resolution Portal</h2>
        <p>Human-in-the-Loop Ticketing · AI Knowledge Training · Escalation Management</p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="login-prompt">
        <div class="icon">🔐</div>
        <h3>Authentication Required</h3>
        <p>Select your SRE profile from the sidebar to access the resolution dashboard.</p>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# User is authenticated — show avatar in sidebar
with st.sidebar:
    initials = sre_user[0].upper()
    role_label = "Administrator" if sre_user == "Admin" else "On-Call Engineer"
    st.markdown(f"""
    <div class="user-avatar-section">
        <div class="user-avatar">{initials}</div>
        <div class="user-info">
            <div class="user-name">{sre_user}</div>
            <div class="user-meta">{role_label}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="section-label">⚡ Status</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div style="display: flex; align-items: center; gap: 8px; padding: 8px 12px;
                background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.2);
                border-radius: 8px; margin-bottom: 12px;">
        <div class="status-dot"></div>
        <span style="font-size: 0.8rem; color: #34d399;">Active · On-Call</span>
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Admin Controls — Learning Loop Trigger
# ---------------------------------------------------------------------------
with st.sidebar:
    if sre_user == "Admin":
        st.markdown('<div class="section-label">⚙️ Administration</div>', unsafe_allow_html=True)
        st.markdown("""
        <div class="admin-panel">
            <div class="admin-panel-title">⚙️ Knowledge Sync</div>
            <div style="font-size: 0.78rem; color: #94a3b8; line-height: 1.5;">
                Vectorize closed tickets and update the Bedrock Knowledge Base.
            </div>
        </div>
        """, unsafe_allow_html=True)

        if st.button("🔄 Trigger AI Knowledge Sync", use_container_width=True):
            with st.spinner("Vectorizing closed tickets & updating Bedrock Knowledge Base..."):
                try:
                    sync_closed_tickets()
                    st.success("✅ AI Knowledge Base updated successfully.")
                except Exception as e:
                    st.error(f"Sync failed: {str(e)}")

    st.divider()
    st.markdown("""
    <div style="text-align:center; padding: 8px 0;">
        <span class="version-badge">● v2.0 SRE Portal</span>
    </div>
    """, unsafe_allow_html=True)
    st.caption("Autonomous CloudOps Command Center")

# ---------------------------------------------------------------------------
# Status Bar
# ---------------------------------------------------------------------------
from datetime import timezone
current_time = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
st.markdown(f"""
<div class="status-bar">
    <div class="status-item">
        <div class="status-dot"></div>
        <span>Connected</span>
    </div>
    <div class="status-item">
        <span>User: {sre_user}</span>
    </div>
    <div class="status-item">
        <span>Region: us-east-1</span>
    </div>
    <div class="status-item">
        <span>{current_time}</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Main Header Banner
# ---------------------------------------------------------------------------
st.markdown("""
<div class="header-banner">
  <h2>🛡️ SRE Resolution Portal</h2>
  <p>Human-in-the-Loop Ticketing · AI Knowledge Training · Escalation Management</p>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Main Dashboard
# ---------------------------------------------------------------------------
dynamo = DynamoDBService()

# ---------------------------------------------------------------------------
# Metrics Header
# ---------------------------------------------------------------------------
tickets = dynamo.get_open_tickets()

critical = sum(1 for t in tickets if t.get('urgency') == 'critical')
high     = sum(1 for t in tickets if t.get('urgency') == 'high')
other    = sum(1 for t in tickets if t.get('urgency') not in ('critical', 'high'))

col1, col2, col3, col4 = st.columns(4)
col1.metric("Open Tickets", len(tickets))
col2.metric("🔴 Critical", critical)
col3.metric("🟠 High", high)
col4.metric("🟢 Other", other)

st.divider()

# ---------------------------------------------------------------------------
# Open Tickets View
# ---------------------------------------------------------------------------
if not tickets:
    st.markdown("""
    <div class="empty-state">
        <div class="icon">✅</div>
        <h3>All Clear</h3>
        <p>No open or escalated tickets in the queue. The AI is handling everything autonomously. Great work, team.</p>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

URGENCY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
tickets_sorted = sorted(tickets, key=lambda t: URGENCY_ORDER.get(t.get('urgency', 'low'), 4))

URGENCY_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
URGENCY_CLASS = {"critical": "urgency-critical", "high": "urgency-high", "medium": "urgency-medium", "low": "urgency-low"}

# Section header with count badge
st.markdown(f"""
<div class="section-header">
    <h3>📋 Open Tickets</h3>
    <div class="ticket-count-badge">
        <div class="dot"></div>
        <span>{len(tickets)} active</span>
    </div>
</div>
""", unsafe_allow_html=True)

for ticket in tickets_sorted:
    tid      = ticket.get('ticket_id', 'N/A')
    urgency  = ticket.get('urgency', 'low')
    category = ticket.get('category', 'General')
    query    = ticket.get('query_text', '')
    created  = ticket.get('created_at', '')[:19].replace('T', ' ') if ticket.get('created_at') else 'N/A'
    emoji    = URGENCY_EMOJI.get(urgency, '⚪')
    urg_class = URGENCY_CLASS.get(urgency, 'urgency-low')

    label = f"{emoji} [{urgency.upper()}] {tid} — {category}"

    with st.expander(label, expanded=(urgency == 'critical')):
        # Rich ticket card header
        st.markdown(f"""
        <div class="ticket-card-header">
            <span class="ticket-id">🎫 {tid}</span>
            <span class="urgency-badge {urg_class}">{emoji} {urgency.upper()}</span>
            <span class="ticket-category">📁 {category}</span>
        </div>
        """, unsafe_allow_html=True)

        col_a, col_b = st.columns([2, 1])

        with col_a:
            st.markdown("**💬 Original Query** (what confused the AI):")
            st.markdown(f"""
            <div class="ticket-query-box">{query}</div>
            """, unsafe_allow_html=True)

        with col_b:
            st.markdown(f"""
            <div class="ticket-meta-grid">
                <div class="ticket-meta-item"><span class="label">ID:</span> <code>{tid}</code></div>
                <div class="ticket-meta-item"><span class="label">Category:</span> {category}</div>
                <div class="ticket-meta-item"><span class="label">Urgency:</span> {emoji} {urgency.upper()}</div>
                <div class="ticket-meta-item"><span class="label">Created:</span> {created}</div>
                <div class="ticket-meta-item"><span class="label">Status:</span> {ticket.get('status', 'open')}</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("---")
        resolution_key = f"resolution_{tid}"
        resolution = st.text_area(
            "📝 Provide the Resolution (This will train the AI)",
            key=resolution_key,
            height=120,
            placeholder="Describe the exact steps taken to resolve this issue. This text will be vectorized into the AI knowledge base."
        )

        close_key = f"close_{tid}"
        if st.button(f"✅ Close Ticket {tid}", key=close_key, type="primary"):
            if not resolution or not resolution.strip():
                st.error("⚠️ Resolution is required before closing. This text trains the AI — don't skip it.")
            else:
                try:
                    dynamo.table.update_item(
                        Key={'ticket_id': tid},
                        UpdateExpression='SET #s = :closed, resolution = :res, updated_at = :ts, vectorized = :false',
                        ExpressionAttributeNames={'#s': 'status'},
                        ExpressionAttributeValues={
                            ':closed': 'closed',
                            ':res': resolution.strip(),
                            ':ts': datetime.utcnow().isoformat(),
                            ':false': False
                        }
                    )
                    st.success(f"✅ Ticket {tid} closed. Resolution saved and queued for AI training.")
                    st.balloons()
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to close ticket: {str(e)}")
