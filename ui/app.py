"""
CloudOps console (Streamlit).

    streamlit run ui/app.py            # local development (AUTH_MODE=local)
    python -m ui.serve                 # container entrypoint (writes OIDC secrets first)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

import config  # noqa: E402
from src.core.monitoring import configure_logging  # noqa: E402
from ui import auth  # noqa: E402
from ui.navigation import pages_for  # noqa: E402

st.set_page_config(page_title="CloudOps Command Center", page_icon="🛡️", layout="wide")


@st.cache_resource
def _init_logging() -> bool:
    configure_logging("console")
    return True


_init_logging()
user = auth.current_user()
if user is None:
    auth.sign_in_page()
    st.stop()

with st.sidebar:
    st.markdown(f"**{user.email}**  \n`{user.role.value}` · tenant `{user.tenant_id}`")
    if config.ENVIRONMENT != "prod":
        st.caption(f"Environment: {config.ENVIRONMENT}")

pages = [
    st.Page(lambda spec=spec: spec.render(user), title=spec.title, icon=spec.icon,
            url_path=spec.url_path, default=(i == 0))
    for i, spec in enumerate(pages_for(user))
]
page = st.navigation(pages)
page.run()

with st.sidebar:
    st.divider()
    if st.button("Sign out", use_container_width=True):
        auth.sign_out()
