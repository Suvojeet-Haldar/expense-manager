# app.py
import time
import json
import os
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Live incrementing variables", layout="centered")

# ---------- defaults ----------
DEFAULT_CONFIG = {
    "VAR_NAMES": ["Var A", "Var B", "Var C", "Var D", "Var E"],
    "START_VALUES": [0.0, 10.5, 25.0, -5.0, 100.0],
    "INCREMENTS": [0.1, 0.5, 0.05, 1.0, -0.2],
    "UPDATES_PER_SECOND": 60,
    "DECIMALS": 4
}
CONFIG_PATH = "config.json"


def safe_rerun():
    """Trigger a rerun across Streamlit versions."""
    try:
        st.rerun()  # modern
    except Exception:
        try:
            st.experimental_rerun()  # older
        except Exception:
            st.stop()


# ---------- safe loader ----------
def load_config(path=CONFIG_PATH):
    """Load config.json silently. Return validated config dict and optional error message."""
    if not os.path.exists(path):
        return DEFAULT_CONFIG.copy(), f"Config file '{path}' not found — using defaults."
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return DEFAULT_CONFIG.copy(), f"Error reading '{path}': {e}. Using defaults."

    cfg = {}
    errors = []

    for key in ("VAR_NAMES", "START_VALUES", "INCREMENTS"):
        if key not in data or not isinstance(data[key], list):
            errors.append(f"Missing/invalid '{key}' (must be a list). Reverting to defaults.")
            cfg[key] = DEFAULT_CONFIG[key]
        else:
            cfg[key] = data[key]

    cfg["UPDATES_PER_SECOND"] = data.get("UPDATES_PER_SECOND", DEFAULT_CONFIG["UPDATES_PER_SECOND"])
    cfg["DECIMALS"] = data.get("DECIMALS", DEFAULT_CONFIG["DECIMALS"])

    if not (len(cfg["VAR_NAMES"]) == len(cfg["START_VALUES"]) == len(cfg["INCREMENTS"]) == 5):
        return DEFAULT_CONFIG.copy(), "VAR_NAMES/START_VALUES/INCREMENTS must each have exactly 5 items. Reverting to defaults."

    try:
        cfg["START_VALUES"] = [float(x) for x in cfg["START_VALUES"]]
        cfg["INCREMENTS"] = [float(x) for x in cfg["INCREMENTS"]]
    except Exception:
        return DEFAULT_CONFIG.copy(), "START_VALUES and INCREMENTS must be numeric. Using defaults."

    try:
        cfg["UPDATES_PER_SECOND"] = int(cfg["UPDATES_PER_SECOND"])
    except Exception:
        cfg["UPDATES_PER_SECOND"] = DEFAULT_CONFIG["UPDATES_PER_SECOND"]

    try:
        cfg["DECIMALS"] = int(cfg["DECIMALS"])
    except Exception:
        cfg["DECIMALS"] = DEFAULT_CONFIG["DECIMALS"]

    err_msg = " ".join(errors) if errors else None
    return cfg, err_msg


# ---------- load & keep in session_state ----------
if "config" not in st.session_state:
    cfg, cfg_err = load_config()
    st.session_state.config = cfg
    st.session_state.config_error = cfg_err
else:
    _, cfg_err = load_config()
    st.session_state.config_error = cfg_err

cfg = st.session_state.config
VAR_NAMES = cfg["VAR_NAMES"]
START_VALUES = cfg["START_VALUES"]
INCREMENTS = cfg["INCREMENTS"]
UPDATES_PER_SECOND = cfg["UPDATES_PER_SECOND"]
DECIMALS = cfg["DECIMALS"]

if not (len(VAR_NAMES) == len(START_VALUES) == len(INCREMENTS) == 5):
    st.error("Configuration arrays must each contain exactly 5 items. Fix config.json.")
    st.stop()

# Baseline for live increments
if "base_values" not in st.session_state or "last_timestamp" not in st.session_state:
    st.session_state.base_values = START_VALUES.copy()
    st.session_state.last_timestamp = time.time()

# UI state
st.session_state.setdefault("subtract_select", VAR_NAMES[0])
st.session_state.setdefault("subtract_amt", 0.0)
st.session_state.setdefault("last_action_msg", "")

# Header
st.title("Live incrementing variables")
st.caption("Config is loaded from local file (hidden). Use 'Reload config' to reapply changes.")

if st.session_state.config_error:
    st.warning("Config warning: " + (st.session_state.config_error or "Unknown issue"))

if st.button("Reload config from file"):
    new_cfg, err = load_config()
    st.session_state.config = new_cfg
    st.session_state.config_error = err
    st.session_state.base_values = st.session_state.config["START_VALUES"].copy()
    st.session_state.last_timestamp = time.time()
    # keep selection valid after reload
    if st.session_state.subtract_select not in st.session_state.config["VAR_NAMES"]:
        st.session_state.subtract_select = st.session_state.config["VAR_NAMES"][0]
    safe_rerun()

# Compute snapshot at render
now = time.time()
elapsed = now - st.session_state.last_timestamp
current_values = [base + inc * elapsed for base, inc in zip(st.session_state.base_values, INCREMENTS)]

# === LIVE DISPLAY (TOP) ===
pause = st.checkbox("Pause live updates (client-side)", value=False)
st.subheader("Live Variables")

payload = {
    "vars": [
        {"name": name, "value_at_render": float(val), "inc": float(inc)}
        for name, val, inc in zip(VAR_NAMES, current_values, INCREMENTS)
    ],
    "updates_per_second": UPDATES_PER_SECOND,
    "decimals": DECIMALS,
    "paused": bool(pause),
}

html = f"""
<div id="live-root" style="font-family: system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial;">
  <style>
    #vars {{ padding: 6px 0; max-width:760px; }}
    .var-row {{ display:flex; align-items:center; justify-content:space-between; padding:10px 14px; border-radius:8px; margin:8px 0; background:rgba(0,0,0,0.03); }}
    .var-left {{ display:flex; align-items:center; gap:10px; }}
    .var-name {{ font-weight:700; font-size:18px; width:260px; }}
    .var-value {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, 'Courier New', monospace; font-size:24px; min-width:220px; text-align:right; }}
    #live-meta {{ margin-top:10px; color:#333; font-size:14px; }}
    #fallback {{ color:#b00; font-size:13px; margin-top:8px; }}
  </style>

  <div id="vars"></div>
  <div id="live-meta">
    <span>Live since: <span id="live-since">0.00</span> s</span>
    &nbsp;•&nbsp;
    <span id="fps">updates/sec: {payload['updates_per_second']}</span>
  </div>
  <div id="fallback">If numbers don't move, check your browser console for errors or verify that scripts/components are allowed.</div>
</div>

<script>
(function(){{
  const payload = {json.dumps(payload)};
  const container = document.getElementById('vars');
  const decimals = payload.decimals || 4;
  const ups = Math.max(1, payload.updates_per_second || 20);
  const interval_ms = Math.round(1000 / ups);

  payload.vars.forEach((v, idx) => {{
    const row = document.createElement('div');
    row.className = 'var-row';
    row.id = 'var-row-' + idx;

    const left = document.createElement('div');
    left.className = 'var-left';
    const name = document.createElement('div');
    name.className = 'var-name';
    name.innerText = v.name;
    left.appendChild(name);

    const val = document.createElement('div');
    val.className = 'var-value';
    val.id = 'var-value-' + idx;
    val.innerText = Number(v.value_at_render).toFixed(decimals);

    row.appendChild(left);
    row.appendChild(val);
    container.appendChild(row);

    row._data = {{
      value_at_render: Number(v.value_at_render),
      inc: Number(v.inc)
    }};
  }});

  const perfStart = performance.now();
  const liveSinceEl = document.getElementById('live-since');

  function updateAll() {{
    const dt = (performance.now() - perfStart) / 1000.0;
    payload.vars.forEach((v, idx) => {{
      const row = document.getElementById('var-row-' + idx);
      const d = row._data;
      const current = d.value_at_render + d.inc * dt;
      const el = document.getElementById('var-value-' + idx);
      if (el) el.innerText = current.toFixed(decimals);
    }});
    if (liveSinceEl) liveSinceEl.innerText = ((performance.now() - perfStart)/1000.0).toFixed(2);
  }}

  if (payload.paused) {{
    updateAll();
  }} else {{
    updateAll();
    if (window.__live_vars_interval) clearInterval(window.__live_vars_interval);
    window.__live_vars_interval = setInterval(updateAll, interval_ms);
  }}
}})();
</script>
"""
components.html(html, height=380, scrolling=False)

st.markdown("---")

# === SUBTRACTION UI (CALLBACK-ONLY ACTION) ===
st.subheader("Subtract from a variable")

# Widgets (no action here; action happens only inside the callback)
st.selectbox("Choose variable", VAR_NAMES, key="subtract_select")
st.number_input("Amount to subtract", key="subtract_amt", format="%.6f", step=1.0)

def _do_subtract():
    """Run ONLY on button click. Safe against reruns from other widgets."""
    sel = st.session_state.get("subtract_select", VAR_NAMES[0])
    amt = float(st.session_state.get("subtract_amt", 0.0))

    if sel not in VAR_NAMES or amt == 0.0:
        # Nothing to do
        st.session_state["last_action_msg"] = ""
        return

    # Recompute precise current values at click time
    now2 = time.time()
    elapsed2 = now2 - st.session_state.last_timestamp
    current_values2 = [base + inc * elapsed2 for base, inc in zip(st.session_state.base_values, INCREMENTS)]

    idx = VAR_NAMES.index(sel)
    st.session_state.base_values[idx] = current_values2[idx] - amt
    st.session_state.last_timestamp = now2

    # Clear amount to avoid accidental repeat; store a message
    st.session_state["subtract_amt"] = 0.0
    st.session_state["last_action_msg"] = f"Subtracted {amt} from {sel}."

# Button triggers ONLY the callback (no inline if st.button block)
st.button(
    "Subtract",
    key="subtract_btn",
    on_click=_do_subtract,
    disabled=(st.session_state.get("subtract_amt", 0.0) == 0.0),
)

# Show last action message (set by the callback)
if st.session_state.get("last_action_msg"):
    st.success(st.session_state["last_action_msg"])

st.markdown("---")
st.write("To change variables/increments, edit the local `config.json` file (next to app.py). Click **Reload config from file** to apply changes.")
