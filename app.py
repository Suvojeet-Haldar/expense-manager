# app.py
import time
import json
import os
from datetime import datetime, timezone
import time as time_module
import streamlit as st
import streamlit.components.v1 as components
from pymongo import MongoClient, ReturnDocument

# NEW: auth imports
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth

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
AUTH_CONFIG_PATH = "auth_config.yaml"  # NEW

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

# NEW: load auth YAML
def load_auth_config(path=AUTH_CONFIG_PATH):
    if not os.path.exists(path):
        st.error(f"Auth config file '{path}' not found. Create it with hashed passwords.")
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.load(f, Loader=SafeLoader)
    except Exception as e:
        st.error(f"Error reading '{path}': {e}")
        return None

# Read app config (unchanged)
cfg, cfg_err = load_config()
VAR_NAMES = cfg["VAR_NAMES"]
START_VALUES = [float(x) for x in cfg["START_VALUES"]]
INCREMENTS = [float(x) for x in cfg["INCREMENTS"]]
UPDATES_PER_SECOND = int(cfg.get("UPDATES_PER_SECOND", DEFAULT_CONFIG["UPDATES_PER_SECOND"]))
DECIMALS = int(cfg.get("DECIMALS", DEFAULT_CONFIG["DECIMALS"]))

# Relaxed length-check: allow dynamic number of allocations.
# If config arrays mismatch in length, trim to shortest and warn.
min_len = min(len(VAR_NAMES), len(START_VALUES), len(INCREMENTS))
if min_len == 0:
    st.error("Config arrays must not be empty. Fix config.json or use defaults.")
    st.stop()
if not (len(VAR_NAMES) == len(START_VALUES) == len(INCREMENTS)):
    # trim to shortest to keep arrays aligned
    VAR_NAMES = VAR_NAMES[:min_len]
    START_VALUES = START_VALUES[:min_len]
    INCREMENTS = INCREMENTS[:min_len]
    cfg_err = (cfg_err or "") + " Config arrays had mismatched lengths; trimmed to shortest length."

# ---------- MongoDB (local) ----------
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "live_vars_db"
COLLECTION_NAME = "state"

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
col = db[COLLECTION_NAME]

# --- LOGS ADDED ---
LOGS_COLLECTION_NAME = "logs"
col_logs = db[LOGS_COLLECTION_NAME]
# counters collection for transaction numbers
counters_col = db["counters"]
# --- /LOGS ADDED ---

STATE_DOC_ID = "live_state"  # fixed _id for single-state document

def to_naive(dt):
    """Normalize to naive UTC datetime. Accept str or datetime.
    Returns a naive UTC datetime (tzinfo removed) suitable for consistent storage/comparison.
    """
    if dt is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if isinstance(dt, str):
        try:
            parsed = datetime.fromisoformat(dt)
        except Exception:
            try:
                parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except Exception:
                return datetime.now(timezone.utc).replace(tzinfo=None)
        dt = parsed

    # If dt has tzinfo, convert to UTC and return naive
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    # dt is naive: assume it's already UTC and return as-is
    return dt

def ensure_state_document():
    """Make sure the state doc exists with naive UTC timestamp and names array."""
    doc = col.find_one({"_id": STATE_DOC_ID})
    if doc is None:
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        doc = {
            "_id": STATE_DOC_ID,
            "names": VAR_NAMES.copy(),
            "base_values": START_VALUES.copy(),
            "increments": INCREMENTS.copy(),
            "last_timestamp": now_naive
        }
        col.insert_one(doc)
    else:
        # if names / increments / base_values missing or mismatched, patch doc
        changed = False
        names = doc.get("names")
        base = doc.get("base_values")
        incs = doc.get("increments")
        if names is None:
            doc["names"] = VAR_NAMES.copy()
            changed = True
        if base is None:
            doc["base_values"] = START_VALUES.copy()
            changed = True
        if incs is None:
            doc["increments"] = INCREMENTS.copy()
            changed = True
        # if arrays have different lengths, align them to the same shortest length
        if not (len(doc["names"]) == len(doc["base_values"]) == len(doc["increments"])):
            mn = min(len(doc["names"]), len(doc["base_values"]), len(doc["increments"]))
            doc["names"] = doc["names"][:mn]
            doc["base_values"] = doc["base_values"][:mn]
            doc["increments"] = doc["increments"][:mn]
            changed = True
        if changed:
            col.update_one({"_id": STATE_DOC_ID}, {"$set": {
                "names": doc["names"],
                "base_values": doc["base_values"],
                "increments": doc["increments"]
            }})
    return doc

def get_state_doc():
    return col.find_one({"_id": STATE_DOC_ID})

def compute_current_values(base_values, increments, last_timestamp, at_time=None):
    """Compute current values given base_values at last_timestamp and increments per second.

    last_timestamp and at_time may be naive datetimes (assumed UTC) or strings.
    """
    if at_time is None:
        at_time = datetime.now(timezone.utc).replace(tzinfo=None)
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
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    # use render snapshot if present
    render_time = to_naive(st.session_state.get("render_time", st.session_state.last_timestamp))
    current_rendered = st.session_state.get(
        "current_values_rendered",
        compute_current_values(st.session_state.base_values, st.session_state.increments, st.session_state.last_timestamp, at_time=render_time)
    )
    elapsed_since_render = (now_naive - render_time).total_seconds()
    current_now = [val + inc * elapsed_since_render for val, inc in zip(current_rendered, st.session_state.increments)]

    # prepare new bases (value at now minus subtraction)
    new_bases = current_now.copy()
    new_bases[index] = new_bases[index] - float(amount)

    # Try fast atomic update: only succeed if DB last_timestamp equals session state's last_timestamp
    old_db_ts = to_naive(st.session_state.last_timestamp)
    result = col.update_one(
        {"_id": STATE_DOC_ID, "last_timestamp": old_db_ts},
        {"$set": {"base_values": new_bases, "last_timestamp": now_naive}}
    )
    if result.modified_count == 1:
        # success -> update session_state snapshot
        st.session_state.base_values = new_bases
        st.session_state.last_timestamp = now_naive
        st.session_state.render_time = now_naive
        st.session_state.current_values_rendered = new_bases.copy()
        return True, f"Subtracted {amount} from {st.session_state.var_names[index]} (fast path)."

    # Fallback: read from DB and retry (optimistic concurrency)
    for attempt in range(max_retries):
        doc = get_state_doc()
        if not doc:
            return False, "State document missing during fallback."

        db_ts = to_naive(doc.get("last_timestamp"))
        now2 = datetime.now(timezone.utc).replace(tzinfo=None)
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
            return True, f"Subtracted {amount} from {st.session_state.var_names[index]} (fallback after retry)."

        # someone else updated; small sleep and retry
        time_module.sleep(retry_delay)

    return False, "Failed to update after multiple retries; please try again."

# ---------- Add allocation helper (new) ----------
def add_allocation(name: str, start_value: float, increment: float, max_retries=8, retry_delay=0.05):
    """Add a new allocation (name, start_value, increment) to the state doc.
    We use optimistic concurrency: read current DB state, compute current values, append new item,
    and try to atomically update only if last_timestamp unchanged. Retry on conflict.
    """
    if not name or name.strip() == "":
        return False, "Name cannot be empty."
    name = str(name).strip()
    # check for duplicate name
    doc0 = get_state_doc()
    if doc0 and name in doc0.get("names", []):
        return False, f"An allocation named '{name}' already exists."

    for attempt in range(max_retries):
        doc = get_state_doc()
        if not doc:
            return False, "State document missing while adding allocation."

        db_ts = to_naive(doc.get("last_timestamp"))
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        current_vals = compute_current_values(doc["base_values"], doc.get("increments", []), db_ts, at_time=now)

        # append new allocation with start_value as the current base (so the current value at 'now' equals start_value)
        new_bases = current_vals + [float(start_value)]
        new_incs = doc.get("increments", []) + [float(increment)]
        new_names = doc.get("names", []) + [name]

        res = col.update_one(
            {"_id": STATE_DOC_ID, "last_timestamp": db_ts},
            {"$set": {
                "base_values": new_bases,
                "increments": new_incs,
                "names": new_names,
                "last_timestamp": now
            }}
        )
        if res.modified_count == 1:
            # success -> update session_state
            st.session_state.base_values = new_bases
            st.session_state.increments = new_incs
            st.session_state.var_names = new_names
            st.session_state.last_timestamp = now
            st.session_state.render_time = now
            st.session_state.current_values_rendered = new_bases.copy()
            return True, f"Allocation '{name}' added successfully."
        time_module.sleep(retry_delay)

    return False, "Failed to add allocation after multiple retries; please try again."

# ---------- Update allocation (new) ----------
def update_allocation(index: int, new_name: str, new_current_value: float, new_increment: float, max_retries=8, retry_delay=0.05):
    """
    Update allocation at `index`:
    - new_name: new label (must not duplicate another existing name unless same index)
    - new_current_value: the desired current value at 'now' (we set the base to that value)
    - new_increment: new increment per second
    Uses optimistic concurrency with retries.
    """
    if index < 0:
        return False, "Invalid index."
    new_name = str(new_name).strip()
    if new_name == "":
        return False, "Name cannot be empty."

    for attempt in range(max_retries):
        doc = get_state_doc()
        if not doc:
            return False, "State document missing while updating."

        names = doc.get("names", [])
        base = doc.get("base_values", [])
        incs = doc.get("increments", [])
        db_ts = to_naive(doc.get("last_timestamp"))
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # duplicate name check (allow if same index)
        if new_name in names and names.index(new_name) != index:
            return False, f"Another allocation named '{new_name}' already exists."

        # compute current values at now
        current_vals = compute_current_values(base, incs, db_ts, at_time=now)

        # build new arrays
        new_bases = current_vals.copy()
        # set the selected allocation's base so its current value at now equals new_current_value
        new_bases[index] = float(new_current_value)

        new_incs = incs.copy()
        # if increments array shorter/padded, ensure length
        if len(new_incs) < len(new_bases):
            new_incs = new_incs + [0.0] * (len(new_bases) - len(new_incs))
        new_incs[index] = float(new_increment)

        new_names = names.copy()
        # if names array shorter/padded, ensure length
        if len(new_names) < len(new_bases):
            new_names = new_names + [f"Var {i}" for i in range(len(new_names), len(new_bases))]
        new_names[index] = new_name

        res = col.update_one(
            {"_id": STATE_DOC_ID, "last_timestamp": db_ts},
            {"$set": {
                "base_values": new_bases,
                "increments": new_incs,
                "names": new_names,
                "last_timestamp": now
            }}
        )
        if res.modified_count == 1:
            # success: update session state
            st.session_state.base_values = new_bases
            st.session_state.increments = new_incs
            st.session_state.var_names = new_names
            st.session_state.last_timestamp = now
            st.session_state.render_time = now
            st.session_state.current_values_rendered = new_bases.copy()
            return True, f"Allocation '{new_name}' updated."
        time_module.sleep(retry_delay)

    return False, "Failed to update allocation after multiple retries; please try again."

# ---------- Delete allocation (new) ----------
def delete_allocation(index: int, max_retries=8, retry_delay=0.05):
    """
    Remove allocation at `index` from names/base_values/increments.
    Uses optimistic concurrency and retries.
    """
    if index < 0:
        return False, "Invalid index."

    for attempt in range(max_retries):
        doc = get_state_doc()
        if not doc:
            return False, "State document missing while deleting."

        names = doc.get("names", [])
        base = doc.get("base_values", [])
        incs = doc.get("increments", [])
        db_ts = to_naive(doc.get("last_timestamp"))
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        if index >= len(names):
            return False, "Index out of range."

        # compute current values at now
        current_vals = compute_current_values(base, incs, db_ts, at_time=now)

        # remove index
        new_bases = current_vals[:index] + current_vals[index+1:]
        new_incs = incs[:index] + incs[index+1:]
        new_names = names[:index] + names[index+1:]

        res = col.update_one(
            {"_id": STATE_DOC_ID, "last_timestamp": db_ts},
            {"$set": {
                "base_values": new_bases,
                "increments": new_incs,
                "names": new_names,
                "last_timestamp": now
            }}
        )
        if res.modified_count == 1:
            # success: update session state
            st.session_state.base_values = new_bases
            st.session_state.increments = new_incs
            st.session_state.var_names = new_names
            st.session_state.last_timestamp = now
            st.session_state.render_time = now
            st.session_state.current_values_rendered = new_bases.copy()
            return True, "Allocation deleted."
        time_module.sleep(retry_delay)

    return False, "Failed to delete allocation after multiple retries; please try again."

# Helper: get next transaction id (atomic increment)
def next_tx_id():
    """Atomically increment and return the next transaction id."""
    doc = counters_col.find_one_and_update(
        {"_id": "tx_counter"},
        {"$inc": {"value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    # doc should always contain "value" after update
    return int(doc.get("value", 0))

# ======================================================================
#                            AUTHENTICATION (UPDATED & ROBUST)
# ======================================================================
auth_cfg = load_auth_config()
if auth_cfg is None:
    st.stop()

# NOTE: streamlit-authenticator removed the `preauthorized` parameter from Authenticate.
# Use named args and call register_user(...) separately if you need registration with pre-authorization.
try:
    authenticator = stauth.Authenticate(
        credentials=auth_cfg["credentials"],
        cookie_name=auth_cfg["cookie"]["name"],
        key=auth_cfg["cookie"]["key"],
        cookie_expiry_days=auth_cfg["cookie"]["expiry_days"],
    )
except Exception as e:
    st.error(f"Failed to initialize authenticator: {e}")
    st.stop()

def attempt_login_variants(auth):
    """
    Try several invocation patterns for auth.login(...) to support different library versions.
    Returns a 3-tuple (name, authentication_status, username) if available, otherwise None.
    """
    candidates = [
        lambda: auth.login("Login", "main"),
        lambda: auth.login("Login", location="main"),
        lambda: auth.login(location="main"),
        lambda: auth.login("main"),
        lambda: auth.login("Login", "sidebar"),
        lambda: auth.login("Login", "unrendered"),
        lambda: auth.login("unrendered"),
    ]
    for fn in candidates:
        try:
            res = fn()
            if isinstance(res, tuple) and len(res) == 3:
                return res
        except Exception:
            # ignore and try next pattern
            continue
    return None

# 1) Try to get a tuple result from login()
login_result = attempt_login_variants(authenticator)

if login_result is not None:
    name, authentication_status, username = login_result
else:
    # 2) Fallback: some versions set these keys in st.session_state instead of returning them
    authentication_status = st.session_state.get("authentication_status", None)
    name = st.session_state.get("name", "")
    username = st.session_state.get("username", "")

    # 3) Extra fallbacks: try several alternative keys that different versions might set
    if authentication_status is None:
        authentication_status = st.session_state.get("auth_status",
                                 st.session_state.get("authentication_status_guest",
                                 st.session_state.get(f"{auth_cfg['cookie']['name']}_authentication_status", None)))
    if not name:
        name = st.session_state.get("display_name",
               st.session_state.get(f"{auth_cfg['cookie']['name']}_name", ""))
    if not username:
        username = st.session_state.get("user",
                   st.session_state.get(f"{auth_cfg['cookie']['name']}_username", ""))

# If we still don't know the authentication status, show the login hint and stop
if authentication_status is None:
    st.info("Please enter your username and password (then press Submit/login on the login box).")
    # optional debug: reveal session_state keys to help diagnose (unchecked by default)
    if st.checkbox("Show session_state keys (debug)", value=False):
        st.write(sorted(list(st.session_state.keys())))
    st.stop()

if authentication_status is False:
    st.error("Username/password is incorrect")
    st.stop()

# Logged in — show logout & who
# logout() signature varies across versions; try a safe variant set
try:
    authenticator.logout("Logout", "sidebar")
except TypeError:
    try:
        authenticator.logout("Logout", location="sidebar")
    except Exception:
        # final fallback: call without args (if supported) or ignore
        try:
            authenticator.logout()
        except Exception:
            pass

st.sidebar.caption(f"Signed in as {name} ({username})")

# ======================================================================
#                         MAIN APP (unchanged logic)
# ======================================================================

# Streamlit initialization & snapshot
if "db_loaded" not in st.session_state:
    ensure_state_document()
    doc = get_state_doc()
    last_ts = to_naive(doc.get("last_timestamp"))
    st.session_state.base_values = [float(x) for x in doc["base_values"]]
    st.session_state.increments = [float(x) for x in doc.get("increments", INCREMENTS)]
    st.session_state.last_timestamp = last_ts
    st.session_state.var_names = doc.get("names", VAR_NAMES.copy())

    # compute one render snapshot (one DB read + compute)
    now_render = datetime.now(timezone.utc).replace(tzinfo=None)
    current_vals_render = compute_current_values(
        st.session_state.base_values,
        st.session_state.increments,
        st.session_state.last_timestamp,
        at_time=now_render
    )
    st.session_state.current_values_rendered = current_vals_render
    st.session_state.render_time = now_render

    st.session_state.db_loaded = True
    st.session_state.last_action_msg = ""
    # use a distinct key for the UI number input to avoid races
    st.session_state.subtract_amt_input = 0.0
    # ensure there's a sensible default selected allocation
    st.session_state.subtract_select = st.session_state.var_names[0] if st.session_state.var_names else ""
# Defensive: ensure increments length is correct
if len(st.session_state.increments) != len(st.session_state.var_names):
    # if mismatch, align increments to names length by padding with zeros or trimming
    target = len(st.session_state.var_names)
    incs = st.session_state.increments
    if len(incs) < target:
        incs = incs + [0.0] * (target - len(incs))
    else:
        incs = incs[:target]
    st.session_state.increments = incs

# Initialize UI helper state
st.session_state.setdefault("busy", False)
st.session_state.setdefault("subtract_result", None)  # will hold dict {"ok":bool,"msg":str}
# --- LOGS ADDED: store note in session_state default
st.session_state.setdefault("subtract_note", "")
# --- /LOGS ADDED

# ---------- UI ----------
st.title("Live Expense allocations")

st.markdown("---")

if cfg_err:
    st.warning("Config warning: " + str(cfg_err))

# Recompute display values based on the stored render snapshot (no DB read)
now_render = datetime.now(timezone.utc).replace(tzinfo=None)
elapsed_since_render = (now_render - st.session_state.render_time).total_seconds()
current_values = [val + inc * elapsed_since_render
                  for val, inc in zip(st.session_state.current_values_rendered, st.session_state.increments)]

# Build client-side renderer payload using the computed current_values at render_time == now_render
payload = {
    "vars": [
        {"name": name, "value_at_render": float(val), "inc": float(inc)}
        for name, val, inc in zip(st.session_state.var_names, current_values, st.session_state.increments)
    ],
    "updates_per_second": UPDATES_PER_SECOND,
    "decimals": DECIMALS,
    "paused": False
}

# -------------------- Renderer (same as before, but dynamic height & container clear) --------------------
num_vars = len(st.session_state.var_names)
# calculated_height = max(360, min(1200, 80 * num_vars))  # ~80px per row, clamped
calculated_height = max(280, min(900, 60 * num_vars))  # ~60px per row, clamped tighter

html = f"""
<div id="live-root" style="font-family: system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial;">
  <style>
    #vars {{ padding: 6px 0; max-width:760px; }}
    .var-row {{ display:flex; align-items:center; justify-content:space-between; padding:10px 14px; border-radius:8px; margin:4px 0; background:rgba(0,0,0,0.03); }}
    .var-name {{ font-weight:700; font-size:18px; width:260px; }}
    .var-value {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, 'Courier New', monospace; font-size:24px; min-width:220px; text-align:right; }}
    .brown {{ color:#8B4513; }}  /* brown color */
    .separator {{ height:12px; border-bottom:1px solid #ddd; margin:8px 0; }}
  </style>

  <div id="vars"></div>
</div>

<script>
(function(){{
  const payload = {json.dumps(payload)};
  const container = document.getElementById('vars');

  // clear previous contents before rebuilding (important on reruns)
  container.innerHTML = '';

  const decimals = payload.decimals || 4;
  const ups = Math.max(1, payload.updates_per_second || 10);
  const interval_ms = Math.round(1000 / ups);

  payload.vars.forEach((v, idx) => {{
    if (idx === 3 || idx === 4) {{
      const sep = document.createElement('div');
      sep.className = 'separator';
      container.appendChild(sep);
    }}

    const row = document.createElement('div');
    row.className = 'var-row';
    row.id = 'var-row-' + idx;

    const name = document.createElement('div');
    name.className = 'var-name';
    name.innerText = v.name;

    if (idx < 3) {{
      name.classList.add('brown');
    }}

    if (idx === 3) {{
      const special = "Dudu";
      if (name.innerText.includes(special)) {{
        name.innerHTML = name.innerText.replace(special, `<span class="brown">${{special}}</span>`);
      }}
    }}

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
      if (!row) return;
      const d = row._data;
      const current = d.value_at_render + d.inc * dt;
      const el = document.getElementById('var-value-' + idx);
      if (el) el.innerText = current.toFixed(decimals);
    }});
  }}

  updateAll();
  if (window.__live_vars_interval) clearInterval(window.__live_vars_interval);
  window.__live_vars_interval = setInterval(updateAll, Math.max(1, Math.round(1000 / (payload.updates_per_second || 10))));
}})();
</script>
"""

components.html(html, height=calculated_height, scrolling=True)

st.markdown("---")

# === Subtraction UI (aligned: select, amount, button in one row) ===
st.subheader("Subtract from the Expense allocations:")

col_sel, col_amt, col_btn = st.columns([2, 2, 1])
with col_sel:
    sel = st.selectbox(
        "Choose variable",
        st.session_state.var_names,
        index=st.session_state.var_names.index(st.session_state.subtract_select) if st.session_state.subtract_select in st.session_state.var_names else 0
    )
    st.session_state.subtract_select = sel

# Bind the number input to a stable session_state key to avoid race conditions.
with col_amt:
    # use a session_state-backed key so clearing in the callback is immediate and persistent
    amt = st.number_input(
        "Amount to subtract",
        format="%.6f",
        step=1.0,
        value=float(st.session_state.get("subtract_amt_input", 0.0)),
        key="subtract_amt_input"
    )

# Callback now receives the exact values as args (no reliance on reading st.session_state inside callback).
def do_subtract_callback(selected_var, amount, note_val):
    """Runs only when button clicked. Uses the passed-in amount to avoid session_state race."""
    st.session_state["busy"] = True
    st.session_state["subtract_result"] = None
    try:
        # find index at time of click (use selection passed as arg)
        try:
            idx = st.session_state.var_names.index(selected_var)
        except Exception:
            # fallback: recompute based on current selection
            idx = st.session_state.var_names.index(st.session_state.get("subtract_select", st.session_state.var_names[0]))
        amount = float(amount)
        if amount == 0.0:
            st.session_state["subtract_result"] = {"ok": False, "msg": "Enter a non-zero amount to subtract."}
            return
        ok, message = subtract_optimized(idx, amount)
        if ok:
            # confirmed success -> clear input amount in session state (this will reset the widget)
            st.session_state["subtract_amt_input"] = 0.0

            # --- LOGS ADDED: insert log into logs collection with an atomic tx id ---
            try:
                now_log = datetime.now(timezone.utc).replace(tzinfo=None)
                tx_id = next_tx_id()
                log_doc = {
                    "timestamp": now_log,
                    "tx": int(tx_id),
                    "var_index": idx,
                    "var_name": st.session_state.var_names[idx],
                    "amount": float(amount),
                    "note": str(note_val) if note_val is not None else "",
                    "user": username if 'username' in globals() and username else st.session_state.get("username", "")
                }
                col_logs.insert_one(log_doc)
                # clear note after successful save
                st.session_state["subtract_note"] = ""
            except Exception as e:
                message = f"{message} (Note not saved: {e})"
            # --- /LOGS ADDED ---

            st.session_state["subtract_result"] = {"ok": True, "msg": message}
        else:
            st.session_state["subtract_result"] = {"ok": False, "msg": message}
    except Exception as e:
        st.session_state["subtract_result"] = {"ok": False, "msg": f"Exception: {e}"}
    finally:
        # Ensure inputs are cleared every time the button callback finishes
        st.session_state["subtract_amt_input"] = 0.0
        st.session_state["subtract_note"] = ""
        st.session_state["busy"] = False

# place the button inside the third column so it's aligned with the select and number input
disable_btn = (float(st.session_state.get("subtract_amt_input", 0.0)) == 0.0) or st.session_state.get("busy", False)
with col_btn:
    # Pass the widget values as args to avoid callback reading stale session_state values.
    st.button(
        "Subtract",
        key="subtract_btn",
        on_click=do_subtract_callback,
        args=(sel, float(st.session_state.get("subtract_amt_input", 0.0)), st.session_state.get("subtract_note", "")),
        disabled=disable_btn
    )

# --- LOGS ADDED: Note input for subtraction (multiline optional) ---
# placed below the column row to avoid changing the existing column layout
note = st.text_area(
    "Note (optional)",
    value=st.session_state.get("subtract_note", ""),
    height=80,
    help="Optional note to store with the subtraction (appears in logs)."
)
# keep bound to session state
st.session_state["subtract_note"] = note
# --- /LOGS ADDED ---

# Show result only from session_state (guaranteed to reflect real DB outcome)
res = st.session_state.get("subtract_result")
if res is not None:
    if res.get("ok"):
        st.success(res.get("msg"))
    else:
        st.error(res.get("msg"))

# --- LOGS ADDED: display subtraction logs (now scrollable, show max 5 visible at once) ---
st.markdown("---")
st.subheader("Subtraction logs (most recent first)")

# Helper to escape HTML inside log strings
def _html_escape(s):
    if s is None:
        return ""
    s = str(s)
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&#39;")
    )

try:
    # fetch last 20 logs (same as before) but render inside a scrollable container limited to ~5 visible items
    cursor = col_logs.find().sort("timestamp", -1).limit(20)
    logs = list(cursor)
    if logs:
        entries_html = ""
        for lg in logs:
            ts = to_naive(lg.get("timestamp"))
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            tx = lg.get("tx", "")
            varn = lg.get("var_name", f"Idx {lg.get('var_index')}")
            amt = lg.get("amount", "")
            usr = lg.get("user", "")
            note_txt = lg.get("note", "")

            # escape to avoid injecting HTML
            ts_html = _html_escape(ts_str)
            tx_html = _html_escape(tx)
            varn_html = _html_escape(varn)
            amt_html = _html_escape(amt)
            usr_html = _html_escape(usr)
            note_html = _html_escape(note_txt)

            # Build each log item: tx + header line and optional note line
            entries_html += (
                "<div class='log-item'>"
                f"<div class='log-header'>#{tx_html} &nbsp; <strong>{ts_html}</strong> &mdash; {varn_html} &mdash; {amt_html} &mdash; {usr_html}</div>"
            )
            if note_html:
                entries_html += f"<div class='log-note'>&nbsp;&nbsp;{note_html}</div>"
            entries_html += "</div>"
        # Container CSS: limit visible height to ~5 items, allow scrolling to view older logs
        container_html = (
            "<style>"
            "  .logs-container { max-height: 260px; overflow-y: auto; padding: 6px 8px; border-radius: 6px; }"
            "  .log-item { padding: 8px 6px; border-bottom: 1px solid rgba(255,255,255,0.03); }"
            "  .log-header { font-size: 14px; line-height: 1.2; }"
            "  .log-note { margin-top: 6px; margin-left: 6px; color: #cfcfcf; font-size: 13px; white-space: pre-wrap; }"
            "</style>"
            f"<div class='logs-container'>{entries_html}</div>"
        )
        st.markdown(container_html, unsafe_allow_html=True)
    else:
        st.info("No subtraction logs yet.")
except Exception as e:
    st.error(f"Could not load subtraction logs: {e}")
# --- /LOGS ADDED ---

st.markdown("---")

# === Add Allocation UI (unchanged) ===
st.subheader("Add new allocation (name, start value, increment per second)")

add_col1, add_col2, add_col3 = st.columns([2, 1, 1])
with add_col1:
    new_name = st.text_input("Allocation name", value="")
with add_col2:
    new_start = st.number_input("Start value", value=0.0, format="%.6f", step=1.0)
with add_col3:
    new_inc = st.number_input("Increment (per second)", value=0.1, format="%.6f", step=0.01)

def do_add_allocation():
    st.session_state["busy"] = True
    try:
        ok, msg = add_allocation(new_name, float(new_start), float(new_inc))
        if ok:
            st.success(msg)
        else:
            st.error(msg)
    except Exception as e:
        st.error(f"Exception adding allocation: {e}")
    finally:
        st.session_state["busy"] = False

st.button("Add allocation", on_click=do_add_allocation, disabled=st.session_state.get("busy", False))
st.markdown("---")

# === Edit Allocation UI (NEW) ===
st.subheader("Edit selected allocation")

if st.session_state.var_names:
    edit_col1, edit_col2 = st.columns([2, 1])
    with edit_col1:
        edit_sel = st.selectbox("Select allocation to edit", st.session_state.var_names, key="edit_select")
        edit_idx = st.session_state.var_names.index(edit_sel)
    # show current computed value and editable fields
    current_val = current_values[edit_idx] if edit_idx < len(current_values) else 0.0
    with edit_col2:
        st.write(f"Current value: **{current_val:.6f}**")
    # Prefill editable fields with current data
    col_a, col_b = st.columns([2, 1])
    with col_a:
        edit_name = st.text_input("Name", value=st.session_state.var_names[edit_idx], key="edit_name")
    with col_b:
        edit_inc = st.number_input("Increment (per second)", value=float(st.session_state.increments[edit_idx]), format="%.6f", step=0.01, key="edit_inc")

    # control for setting current value (interpreted as value at the moment of pressing Save)
    set_val_col1, set_val_col2 = st.columns([2, 1])
    with set_val_col1:
        edit_value = st.number_input("Set current value (value at now)", value=float(current_val), format="%.6f", step=1.0, key="edit_value")
    with set_val_col2:
        st.write("")  # spacer

    # Save button
    def do_save_edit():
        st.session_state["busy"] = True
        try:
            ok, msg = update_allocation(edit_idx, edit_name, float(edit_value), float(edit_inc))
            if ok:
                st.success(msg)
            else:
                st.error(msg)
        except Exception as e:
            st.error(f"Exception saving allocation: {e}")
        finally:
            st.session_state["busy"] = False

    st.button("Save changes", on_click=do_save_edit, disabled=st.session_state.get("busy", False))

    # Delete controls (require explicit confirmation checkbox)
    st.markdown("**Danger zone:** delete this allocation")
    del_col1, del_col2 = st.columns([3, 1])
    with del_col1:
        confirm_delete = st.checkbox("I confirm I want to delete this allocation", key="confirm_delete")
    with del_col2:
        def do_delete():
            st.session_state["busy"] = True
            try:
                ok, msg = delete_allocation(edit_idx)
                if ok:
                    st.success(msg)
                    # reset confirm checkbox
                    st.session_state.confirm_delete = False
                else:
                    st.error(msg)
            except Exception as e:
                st.error(f"Exception deleting allocation: {e}")
            finally:
                st.session_state["busy"] = False
        st.button("Delete", on_click=do_delete, disabled=(not st.session_state.get("confirm_delete", False) or st.session_state.get("busy", False)))
else:
    st.info("No allocations available to edit.")

st.markdown("---")

# Helpful info
st.subheader("Fixed monthly allocations, that are already deducted, and can be used only once every month:")
st.write("Gym Fees(that goes to pupuu): 1083.33")
st.write("Nayabad: 500")
st.write("Recharge: 189")

st.markdown("---")

st.write(f"Last DB save timestamp (UTC, naive): {st.session_state.last_timestamp.isoformat()}")
st.write("Note: display uses a cached render snapshot and applies increments since that snapshot. On successful subtraction or addition or edit the DB is updated atomically.")

st.markdown("---")
st.write("To migrate to MongoDB Atlas later, change the MONGO_URI at the top to your Atlas connection string.")
