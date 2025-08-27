# app.py
import time
import json
import os
from datetime import datetime
import time as time_module
import streamlit as st
import streamlit.components.v1 as components
from pymongo import MongoClient

st.set_page_config(page_title="Live incrementing variables (Mongo local)", layout="centered")

# ---------- defaults & config ----------
DEFAULT_CONFIG = {
    "VAR_NAMES": ["Var A", "Var B", "Var C", "Var D", "Var E"],
    "START_VALUES": [0.0, 10.5, 25.0, -5.0, 100.0],
    "INCREMENTS": [0.1, 0.1, 0.1, 0.1, 0.1],
    "UPDATES_PER_SECOND": 10,
    "DECIMALS": 4
}
CONFIG_PATH = "config.json"

def load_config(path=CONFIG_PATH):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not all(k in data for k in ("VAR_NAMES", "START_VALUES", "INCREMENTS")):
                return DEFAULT_CONFIG.copy(), "config.json missing keys; using defaults."
            return data, None
        except Exception as e:
            return DEFAULT_CONFIG.copy(), f"Error reading config.json: {e}. Using defaults."
    else:
        return DEFAULT_CONFIG.copy(), None

cfg, cfg_err = load_config()

VAR_NAMES = cfg["VAR_NAMES"]
START_VALUES = [float(x) for x in cfg["START_VALUES"]]
INCREMENTS = [float(x) for x in cfg["INCREMENTS"]]
UPDATES_PER_SECOND = int(cfg.get("UPDATES_PER_SECOND", DEFAULT_CONFIG["UPDATES_PER_SECOND"]))
DECIMALS = int(cfg.get("DECIMALS", DEFAULT_CONFIG["DECIMALS"]))

if not (len(VAR_NAMES) == len(START_VALUES) == len(INCREMENTS) == 5):
    st.error("VAR_NAMES, START_VALUES and INCREMENTS must each have exactly 5 items. Fix config.json.")
    st.stop()

# ---------- MongoDB (local) ----------
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "live_vars_db"
COLLECTION_NAME = "state"

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
col = db[COLLECTION_NAME]

STATE_DOC_ID = "live_state"  # fixed _id for single-state document

def to_naive(dt):
    """Normalize to naive UTC datetime. Accept str or datetime."""
    if dt is None:
        return datetime.utcnow()
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            try:
                dt = datetime.fromisoformat(dt.replace("Z", ""))
            except Exception:
                return datetime.utcnow()
    if getattr(dt, "tzinfo", None) is not None:
        # convert to naive UTC via timestamp
        return datetime.utcfromtimestamp(dt.timestamp())
    return dt

def ensure_state_document():
    """Make sure the state doc exists with naive UTC timestamp."""
    doc = col.find_one({"_id": STATE_DOC_ID})
    if doc is None:
        now = datetime.utcnow()
        doc = {
            "_id": STATE_DOC_ID,
            "base_values": START_VALUES.copy(),
            "increments": INCREMENTS.copy(),
            "last_timestamp": now
        }
        col.insert_one(doc)
    return doc

def get_state_doc():
    return col.find_one({"_id": STATE_DOC_ID})

def compute_current_values(base_values, increments, last_timestamp, at_time=None):
    if at_time is None:
        at_time = datetime.utcnow()
    last_ts = to_naive(last_timestamp)
    at_ts = to_naive(at_time)
    elapsed = (at_ts - last_ts).total_seconds()
    return [b + inc * elapsed for b, inc in zip(base_values, increments)]

# ---------- Optimized subtract: fast path (no DB read) then fallback ----------
def subtract_optimized(index, amount, max_retries=8, retry_delay=0.05):
    """
    Fast-path subtract using local rendered snapshot. If DB last_timestamp unchanged, update succeeds.
    If fails due to concurrent update, fallback to read-and-retry loop.
    Returns (success:bool, message:str).
    """
    now = datetime.utcnow()
    # use render snapshot if present
    render_time = to_naive(st.session_state.get("render_time", st.session_state.last_timestamp))
    current_rendered = st.session_state.get("current_values_rendered", compute_current_values(
        st.session_state.base_values, st.session_state.increments, st.session_state.last_timestamp, at_time=render_time))
    elapsed_since_render = (now - render_time).total_seconds()
    current_now = [val + inc * elapsed_since_render for val, inc in zip(current_rendered, st.session_state.increments)]

    # prepare new bases (value at now minus subtraction)
    new_bases = current_now.copy()
    new_bases[index] = new_bases[index] - float(amount)

    # Try fast atomic update: only succeed if DB last_timestamp equals session state's last_timestamp
    old_db_ts = to_naive(st.session_state.last_timestamp)
    result = col.update_one(
        {"_id": STATE_DOC_ID, "last_timestamp": old_db_ts},
        {"$set": {"base_values": new_bases, "last_timestamp": now}}
    )
    if result.modified_count == 1:
        # success -> update session_state snapshot
        st.session_state.base_values = new_bases
        st.session_state.last_timestamp = now
        st.session_state.render_time = now
        st.session_state.current_values_rendered = new_bases.copy()
        return True, f"Subtracted {amount} from {VAR_NAMES[index]} (fast path)."

    # Fallback: read from DB and retry (optimistic concurrency)
    for attempt in range(max_retries):
        doc = get_state_doc()
        if not doc:
            return False, "State document missing during fallback."

        db_ts = to_naive(doc.get("last_timestamp"))
        now2 = datetime.utcnow()
        current_vals = compute_current_values(doc["base_values"], doc.get("increments", st.session_state.increments), db_ts, at_time=now2)

        new_bases2 = current_vals.copy()
        new_bases2[index] = new_bases2[index] - float(amount)

        res2 = col.update_one(
            {"_id": STATE_DOC_ID, "last_timestamp": db_ts},
            {"$set": {"base_values": new_bases2, "last_timestamp": now2}}
        )
        if res2.modified_count == 1:
            # success
            st.session_state.base_values = new_bases2
            st.session_state.increments = doc.get("increments", st.session_state.increments)
            st.session_state.last_timestamp = now2
            st.session_state.render_time = now2
            st.session_state.current_values_rendered = new_bases2.copy()
            return True, f"Subtracted {amount} from {VAR_NAMES[index]} (fallback after retry)."

        # someone else updated; small sleep and retry
        time_module.sleep(retry_delay)

    return False, "Failed to update after multiple retries; please try again."

# ---------- Streamlit initialization & snapshot ----------
if "db_loaded" not in st.session_state:
    ensure_state_document()
    doc = get_state_doc()
    last_ts = to_naive(doc.get("last_timestamp"))
    st.session_state.base_values = [float(x) for x in doc["base_values"]]
    st.session_state.increments = [float(x) for x in doc.get("increments", INCREMENTS)]
    st.session_state.last_timestamp = last_ts

    # compute one render snapshot (one DB read + compute)
    now_render = datetime.utcnow()
    current_vals_render = compute_current_values(st.session_state.base_values, st.session_state.increments, st.session_state.last_timestamp, at_time=now_render)
    st.session_state.current_values_rendered = current_vals_render
    st.session_state.render_time = now_render

    st.session_state.db_loaded = True
    st.session_state.last_action_msg = ""
    st.session_state.subtract_amt = 0.0
    st.session_state.subtract_select = VAR_NAMES[0]

# Defensive: ensure increments length is correct
if len(st.session_state.increments) != len(VAR_NAMES):
    st.session_state.increments = INCREMENTS.copy()

# Initialize UI helper state
st.session_state.setdefault("busy", False)
st.session_state.setdefault("subtract_result", None)  # will hold dict {"ok":bool,"msg":str}

# ---------- UI ----------
st.title("Live incrementing variables (local MongoDB persistence)")
st.caption("State persists to local MongoDB. On reload/restart values are reconstructed from DB + elapsed time.")

if cfg_err:
    st.warning("Config warning: " + str(cfg_err))

if st.button("Reload local config (does not overwrite DB state)"):
    cfg2, cfg_err2 = load_config()
    if cfg_err2:
        st.warning(cfg_err2)
    else:
        st.success("Config reloaded (UI labels/inc rates updated if changed in config.json).")
    st.session_state.increments = [float(x) for x in cfg2.get("INCREMENTS", INCREMENTS)]

# Recompute display values based on the stored render snapshot (no DB read)
now_render = datetime.utcnow()
elapsed_since_render = (now_render - st.session_state.render_time).total_seconds()
current_values = [val + inc * elapsed_since_render for val, inc in zip(st.session_state.current_values_rendered, st.session_state.increments)]

# Build client-side renderer payload using the computed current_values at render_time == now_render
payload = {
    "vars": [
        {"name": name, "value_at_render": float(val), "inc": float(inc)}
        for name, val, inc in zip(VAR_NAMES, current_values, st.session_state.increments)
    ],
    "updates_per_second": UPDATES_PER_SECOND,
    "decimals": DECIMALS,
    "paused": False
}

html = f"""
<div id="live-root" style="font-family: system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial;">
  <style>
    #vars {{ padding: 6px 0; max-width:760px; }}
    .var-row {{ display:flex; align-items:center; justify-content:space-between; padding:10px 14px; border-radius:8px; margin:8px 0; background:rgba(0,0,0,0.03); }}
    .var-name {{ font-weight:700; font-size:18px; width:260px; }}
    .var-value {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, 'Courier New', monospace; font-size:24px; min-width:220px; text-align:right; }}
  </style>

  <div id="vars"></div>
</div>

<script>
(function(){{
  const payload = {json.dumps(payload)};
  const container = document.getElementById('vars');
  const decimals = payload.decimals || 4;
  const ups = Math.max(1, payload.updates_per_second || 10);
  const interval_ms = Math.round(1000 / ups);

  payload.vars.forEach((v, idx) => {{
    const row = document.createElement('div');
    row.className = 'var-row';
    row.id = 'var-row-' + idx;

    const name = document.createElement('div');
    name.className = 'var-name';
    name.innerText = v.name;

    const val = document.createElement('div');
    val.className = 'var-value';
    val.id = 'var-value-' + idx;
    val.innerText = Number(v.value_at_render).toFixed(decimals);

    row.appendChild(name);
    row.appendChild(val);
    container.appendChild(row);

    row._data = {{
      value_at_render: Number(v.value_at_render),
      inc: Number(v.inc)
    }};
  }});

  const perfStart = performance.now();

  function updateAll() {{
    const dt = (performance.now() - perfStart) / 1000.0;
    payload.vars.forEach((v, idx) => {{
      const row = document.getElementById('var-row-' + idx);
      const d = row._data;
      const current = d.value_at_render + d.inc * dt;
      const el = document.getElementById('var-value-' + idx);
      if (el) el.innerText = current.toFixed(decimals);
    }});
  }}

  updateAll();
  if (window.__live_vars_interval) clearInterval(window.__live_vars_interval);
  window.__live_vars_interval = setInterval(updateAll, interval_ms);
}})();
</script>
"""

components.html(html, height=360, scrolling=False)

st.markdown("---")

# === Subtraction UI (callback-based, show feedback only after DB confirmation) ===
st.subheader("Subtract from a variable")

col_sel, col_amt, col_btn = st.columns([2, 2, 1])
with col_sel:
    sel = st.selectbox("Choose variable", VAR_NAMES, index=VAR_NAMES.index(st.session_state.subtract_select))
    st.session_state.subtract_select = sel

with col_amt:
    amt = st.number_input("Amount to subtract", format="%.6f", step=1.0, value=float(st.session_state.get("subtract_amt", 0.0)))
    st.session_state.subtract_amt = float(amt)

# callback for button:
def do_subtract_callback():
    """Runs only when button clicked. Sets subtract_result in session_state."""
    st.session_state["busy"] = True
    st.session_state["subtract_result"] = None
    try:
        idx = VAR_NAMES.index(st.session_state.subtract_select)
        amount = float(st.session_state.subtract_amt)
        if amount == 0.0:
            st.session_state["subtract_result"] = {"ok": False, "msg": "Enter a non-zero amount to subtract."}
            return
        ok, message = subtract_optimized(idx, amount)
        if ok:
            # confirmed success -> clear input amount only now
            st.session_state.subtract_amt = 0.0
            st.session_state["subtract_result"] = {"ok": True, "msg": message}
        else:
            st.session_state["subtract_result"] = {"ok": False, "msg": message}
    except Exception as e:
        st.session_state["subtract_result"] = {"ok": False, "msg": f"Exception: {e}"}
    finally:
        st.session_state["busy"] = False

# disable button if amount == 0 or busy
disable_btn = (st.session_state.get("subtract_amt", 0.0) == 0.0) or st.session_state.get("busy", False)

st.button("Subtract", key="subtract_btn", on_click=do_subtract_callback, disabled=disable_btn)

# Show result only from session_state (guaranteed to reflect real DB outcome)
res = st.session_state.get("subtract_result")
if res is not None:
    if res.get("ok"):
        st.success(res.get("msg"))
    else:
        st.error(res.get("msg"))

# Helpful info
st.write(f"Last DB save timestamp (UTC, naive): {st.session_state.last_timestamp.isoformat()}")
st.write("Note: display uses a cached render snapshot and applies increments since that snapshot. On successful subtraction the DB is updated atomically.")

st.markdown("---")
st.write("To migrate to MongoDB Atlas later, change the MONGO_URI at the top to your Atlas connection string.")
