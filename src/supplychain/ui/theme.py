"""The "freight console" look: CSS and the shipment-themed components.

Pure presentation. Every function here takes plain data and returns an HTML
string, or writes one to Streamlit - nothing in this module imports the graph,
the model or the data layer, which is what keeps it testable without either.

Four constraints shaped this file, and each is easy to break by accident:

* **Icons are Material Symbols, never emoji.** Streamlit self-hosts the
  "Material Symbols Rounded" variable font for its own widget icons, so it is
  already loaded, works offline, and inherits `currentColor` and font-size.
  `icon()` renders one by ligature; Streamlit's own widgets take the same set
  as `icon=":material/name:"`. Emoji render differently on every platform, sit
  at the wrong baseline and cannot take the theme colour - one emoji in a row
  of glyphs is the single clearest "this is a prototype" tell.
* **It has to survive both themes.** Streamlit exposes no CSS hook for
  light-vs-dark (it uses CSS-in-JS, and the app root carries no ``data-theme``
  attribute), so every surface here is mixed from ``currentColor`` rather than
  hard-coded. A literal ``#fff`` panel would look right in one theme and be
  unreadable in the other. The palette itself lives in ``.streamlit/config.toml``.
* **Warm colours mean severity.** Blue is "this is the product"; red, orange,
  yellow and green are reserved for how bad the incident is.
* **No raw JSON reaches the user.** `kv_grid` exists so payloads render as a
  labelled grid; `st.json` is for debugging, and it looks like it.

Owner: Team Member 1 (chat UI).
"""

from __future__ import annotations

import re
from html import escape

import streamlit as st

# The accent has to clear both backgrounds, so it sits mid-range rather than at
# either end - light enough to read on #080D16, dark enough to read on #F6F9FD.
ACCENT = "#1E93DE"

SEVERITY_COLOURS = {
    "critical": "#F87171",
    "high": "#FB923C",
    "medium": "#FACC15",
    "low": "#34D399",
}

AGENT_META = {
    "intake": ("inbox", "Intake"),
    "supervisor": ("hub", "Supervisor"),
    "incident_analysis": ("troubleshoot", "Incident analysis"),
    "shipment": ("local_shipping", "Shipment"),
    "inventory": ("inventory_2", "Inventory"),
    "supplier": ("factory", "Supplier"),
    "recovery": ("handyman", "Recovery"),
    "approval": ("approval", "Approval"),
    "respond": ("chat_bubble", "Response"),
}

ENTITY_META = {
    "shipment_ids": ("local_shipping", "shipments"),
    "supplier_ids": ("factory", "suppliers"),
    "skus": ("inventory_2", "SKUs"),
    "warehouse_ids": ("warehouse", "warehouses"),
    "order_ids": ("receipt_long", "orders"),
    "incident_ids": ("warning", "incidents"),
    "route_ids": ("route", "routes"),
}

ENTITY_LABELS = {field: label for field, (_, label) in ENTITY_META.items()}

# The lane drawn across the hero: the physical path a NovaRetail shipment takes.
LANE_STOPS = [
    ("factory", "Supplier"),
    ("anchor", "Port"),
    ("local_shipping", "Line haul"),
    ("warehouse", "Warehouse"),
    ("storefront", "Store"),
]


CSS = f"""
<style>
:root {{
  --nr-accent: {ACCENT};
  --nr-surface: color-mix(in srgb, currentColor 4%, transparent);
  --nr-surface-strong: color-mix(in srgb, currentColor 7%, transparent);
  --nr-border: color-mix(in srgb, currentColor 13%, transparent);
  --nr-border-strong: color-mix(in srgb, currentColor 22%, transparent);
  /* Faint is as light as a real label can go: 62% of the text colour is 4.9:1
     on the light background and 6.7:1 on the dark one. Going to 50% looks
     better and fails AA on both. */
  --nr-muted: color-mix(in srgb, currentColor 72%, transparent);
  --nr-faint: color-mix(in srgb, currentColor 62%, transparent);
  /* The accent pulled towards the text colour. Straight #1E93DE clears 4.5:1
     on the dark background but only 3.1:1 on the light one, and these labels
     are 10px uppercase - too small to qualify for the large-text threshold.
     At 70% the mix is 5.2:1 light and 8.0:1 dark. */
  --nr-accent-text: color-mix(in srgb, var(--nr-accent) 70%, currentColor);

  /* One type scale and one rhythm, so nothing is sized by eye. */
  --nr-fs-micro: 0.68rem;
  --nr-fs-xs: 0.76rem;
  --nr-fs-sm: 0.84rem;
  --nr-space: 0.55rem;
  --nr-radius: 0.7rem;
  --nr-ease: 140ms cubic-bezier(0.4, 0, 0.2, 1);
}}

/* Material Symbols Rounded is already loaded by Streamlit for its own widget
   icons, so this costs no request and works with no network. */
.nr-i {{
  font-family: "Material Symbols Rounded";
  font-weight: normal; font-style: normal; line-height: 1;
  letter-spacing: normal; text-transform: none; white-space: nowrap;
  word-wrap: normal; direction: ltr; display: inline-block;
  font-variation-settings: "FILL" 0, "wght" 400, "GRAD" 0, "opsz" 24;
  -webkit-font-feature-settings: "liga";
  -webkit-font-smoothing: antialiased;
  vertical-align: -0.16em;
}}

/* ---------------------------------------------------------------- shell -- */

[data-testid="stApp"] {{
  background-image:
    radial-gradient(1100px 480px at 12% -8%, rgba(30, 147, 222, 0.13), transparent 60%),
    radial-gradient(900px 420px at 92% 0%, rgba(52, 211, 153, 0.07), transparent 55%),
    linear-gradient(color-mix(in srgb, currentColor 3.5%, transparent) 1px, transparent 1px),
    linear-gradient(90deg, color-mix(in srgb, currentColor 3.5%, transparent) 1px, transparent 1px);
  background-size: auto, auto, 46px 46px, 46px 46px;
  background-attachment: fixed;
}}

/* The deploy button is noise in a demo, and easy to hit by mistake. The rest
   of the toolbar stays: it holds the light/dark toggle. */
[data-testid="stAppDeployButton"] {{ display: none; }}
[data-testid="stHeader"] {{ background: transparent; height: 3rem; }}
[data-testid="stMainBlockContainer"] {{ padding-top: 2.2rem; max-width: 62rem; }}
[data-testid="stStatusWidget"] {{ font-size: var(--nr-fs-xs); }}

hr, [data-testid="stDivider"] hr {{
  border-color: var(--nr-border); margin: 1rem 0;
}}

::-webkit-scrollbar {{ width: 9px; height: 9px; }}
::-webkit-scrollbar-thumb {{
  background: color-mix(in srgb, currentColor 18%, transparent);
  border-radius: 99px;
}}
::-webkit-scrollbar-thumb:hover {{
  background: color-mix(in srgb, currentColor 30%, transparent);
}}
::-webkit-scrollbar-track {{ background: transparent; }}

/* One motion language: everything that reacts to a pointer uses the same one. */
button, [data-testid="stExpander"] summary, .nr-chip, .nr-card {{
  transition: background-color var(--nr-ease), border-color var(--nr-ease),
              color var(--nr-ease), transform var(--nr-ease);
}}

/* ---------------------------------------------------------------- hero --- */

.st-key-nr-hero {{
  position: relative;
  border: 1px solid var(--nr-border);
  border-radius: 1.1rem;
  padding: 1.5rem 1.7rem 1.35rem;
  margin-bottom: 1.4rem;
  background: var(--nr-surface);
  overflow: hidden;
}}
.st-key-nr-hero::before {{
  /* Container stripe - the reefer-panel motif, repeated on the gate card. */
  content: "";
  position: absolute; inset: 0 0 auto 0; height: 3px;
  background: linear-gradient(90deg,
    var(--nr-accent) 0%, #34D399 32%, #FACC15 64%, #FB923C 100%);
  opacity: 0.85;
}}
.st-key-nr-hero h1 {{
  font-size: 2.05rem; font-weight: 700; letter-spacing: -0.022em;
  padding: 0.15rem 0 0; line-height: 1.14;
}}

.nr-eyebrow {{
  display: flex; align-items: center; gap: 0.5rem;
  font-size: var(--nr-fs-micro); font-weight: 700; letter-spacing: 0.16em;
  text-transform: uppercase; color: var(--nr-accent-text);
  margin-bottom: 0.15rem;
}}
.nr-live {{
  width: 7px; height: 7px; border-radius: 99px; background: #34D399;
  box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.55);
  animation: nr-pulse 2.4s ease-out infinite;
}}
@keyframes nr-pulse {{
  70% {{ box-shadow: 0 0 0 7px rgba(52, 211, 153, 0); }}
  100% {{ box-shadow: 0 0 0 0 rgba(52, 211, 153, 0); }}
}}

/* ---------------------------------------------------------------- lane --- */

.nr-lane {{
  display: flex; align-items: center; gap: 0.35rem;
  margin: 1.15rem 0 0.25rem;
}}
.nr-lane__stop {{ display: flex; flex-direction: column; align-items: center; gap: 0.3rem; }}
.nr-lane__pin {{
  width: 34px; height: 34px; border-radius: 0.7rem;
  display: grid; place-items: center; font-size: 1.05rem;
  border: 1px solid var(--nr-border); background: var(--nr-surface-strong);
  color: var(--nr-muted);
}}
.nr-lane__name {{
  font-size: 0.63rem; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--nr-faint); white-space: nowrap;
}}
.nr-lane__leg {{
  flex: 1; height: 2px; margin-bottom: 1.1rem; border-radius: 2px;
  background-image: linear-gradient(90deg,
    color-mix(in srgb, currentColor 42%, transparent) 0 7px, transparent 7px 14px);
  background-size: 14px 2px;
  animation: nr-flow 0.9s linear infinite;
}}
.nr-lane__leg--blocked {{
  background-image: linear-gradient(90deg, #FB923C 0 7px, transparent 7px 14px);
}}
@keyframes nr-flow {{ to {{ background-position-x: 14px; }} }}

/* ----------------------------------------------------------------- kpis -- */

.nr-kpis {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
  gap: 0.6rem; margin-top: 1.1rem;
}}
.nr-kpi {{
  border: 1px solid var(--nr-border); border-radius: 0.8rem;
  padding: 0.6rem 0.75rem; background: var(--nr-surface-strong);
  border-left: 3px solid var(--nr-tone, var(--nr-accent));
}}
.nr-kpi__value {{
  font-size: 1.45rem; font-weight: 700; line-height: 1.1;
  letter-spacing: -0.022em; font-variant-numeric: tabular-nums;
}}
.nr-kpi__label {{
  font-size: var(--nr-fs-micro); text-transform: uppercase; letter-spacing: 0.09em;
  color: var(--nr-faint); margin-top: 0.15rem;
}}

/* ------------------------------------------------------------ zero state - */

.nr-zero {{
  font-size: var(--nr-fs-sm); color: var(--nr-faint);
  margin: 0.2rem 0 0.5rem;
}}
[class*="st-key-ask-"] button {{
  border: 1px solid var(--nr-border); border-radius: var(--nr-radius);
  background: var(--nr-surface); padding: 0.7rem 0.85rem;
  text-align: left; justify-content: flex-start; font-weight: 500;
  font-size: var(--nr-fs-sm); line-height: 1.35; min-height: 4.2rem;
  color: var(--nr-muted);
}}
[class*="st-key-ask-"] button:hover {{
  border-color: color-mix(in srgb, var(--nr-accent) 55%, transparent);
  background: var(--nr-surface-strong); color: inherit;
  transform: translateY(-1px);
}}
[class*="st-key-ask-"] button p {{ text-align: left; }}

/* ---------------------------------------------------------------- chips -- */

.nr-chips {{ display: flex; flex-wrap: wrap; gap: 0.35rem; margin: 0.15rem 0 0.5rem; }}
.nr-chip {{
  display: inline-flex; align-items: center; gap: 0.35rem;
  border: 1px solid var(--nr-border); border-radius: 99px;
  padding: 0.15rem 0.65rem 0.15rem 0.5rem;
  background: var(--nr-surface-strong); font-size: var(--nr-fs-xs);
}}
.nr-chip .nr-i {{ color: var(--nr-faint); font-size: 0.95rem; }}
.nr-chip b {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
.nr-chip__kind {{ color: var(--nr-faint); font-size: var(--nr-fs-micro); }}

.nr-sev {{
  display: inline-flex; align-items: center; gap: 0.35rem;
  border-radius: 99px; padding: 0.1rem 0.55rem; font-size: var(--nr-fs-micro);
  font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  border: 1px solid color-mix(in srgb, var(--nr-tone) 45%, transparent);
  background: color-mix(in srgb, var(--nr-tone) 14%, transparent);
  color: var(--nr-tone);
}}

/* ------------------------------------------------------------ key/value -- */

.nr-kv {{
  display: grid; grid-template-columns: minmax(7rem, auto) 1fr;
  gap: 0.3rem 0.9rem; margin: 0.5rem 0 0.2rem;
  border: 1px solid var(--nr-border); border-radius: var(--nr-radius);
  padding: 0.7rem 0.85rem; background: var(--nr-surface-strong);
  font-size: var(--nr-fs-sm);
}}
.nr-kv dt {{
  color: var(--nr-faint); font-size: var(--nr-fs-xs);
  text-transform: uppercase; letter-spacing: 0.06em; padding-top: 0.08rem;
}}
.nr-kv dd {{ margin: 0; font-variant-numeric: tabular-nums; }}

/* ------------------------------------------------------------- timeline -- */

.nr-tl {{ margin: 0.15rem 0 0.35rem; }}
.nr-tl__head {{
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem;
  margin-bottom: 0.55rem;
}}
.nr-tl__id {{
  font-weight: 700; font-size: 0.95rem; letter-spacing: -0.01em;
  display: inline-flex; align-items: center; gap: 0.4rem;
}}
.nr-tl__meta {{ color: var(--nr-faint); font-size: var(--nr-fs-xs); }}

.nr-tl__track {{
  position: relative; height: 12px; border-radius: 99px;
  background: var(--nr-surface-strong);
  border: 1px solid var(--nr-border); overflow: hidden;
}}
.nr-tl__planned {{
  position: absolute; inset: 0 auto 0 0; height: 100%;
  background: linear-gradient(90deg,
    color-mix(in srgb, var(--nr-accent) 55%, transparent), var(--nr-accent));
}}
.nr-tl__slip {{
  position: absolute; top: 0; bottom: 0;
  background: repeating-linear-gradient(45deg,
    #FB923C 0 5px, color-mix(in srgb, #FB923C 45%, transparent) 5px 10px);
}}
.nr-tl__now {{
  position: absolute; top: -4px; bottom: -4px; width: 2px;
  background: currentColor; opacity: 0.75;
}}
.nr-tl__scan {{
  position: absolute; top: 50%; width: 9px; height: 9px; margin: -4.5px 0 0 -4.5px;
  border-radius: 99px; background: #34D399;
  box-shadow: 0 0 0 2px color-mix(in srgb, currentColor 22%, transparent);
}}
.nr-tl__axis {{
  display: flex; justify-content: space-between; gap: 0.5rem;
  margin-top: 0.4rem; font-size: var(--nr-fs-micro); color: var(--nr-faint);
}}
.nr-tl__axis b {{ color: inherit; font-weight: 600; font-variant-numeric: tabular-nums; }}
.nr-tl__facts {{ display: flex; flex-wrap: wrap; gap: 0.35rem; margin-top: 0.6rem; }}
.nr-tl__fact {{
  border: 1px solid var(--nr-border); border-radius: 0.55rem;
  padding: 0.25rem 0.55rem; background: var(--nr-surface-strong);
  font-size: var(--nr-fs-xs); font-variant-numeric: tabular-nums;
}}
.nr-tl__fact span {{ color: var(--nr-faint); }}
.nr-tl__fact--warn {{
  border-color: color-mix(in srgb, #FB923C 45%, transparent);
  background: color-mix(in srgb, #FB923C 10%, transparent);
}}

.nr-panel-title {{
  display: flex; align-items: center; gap: 0.4rem;
  font-size: 0.72rem; font-weight: 700; letter-spacing: 0.12em;
  text-transform: uppercase; color: var(--nr-accent-text);
  margin: 1rem 0 0.4rem;
}}

/* ---------------------------------------------------------------- stats -- */

.nr-stats {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(104px, 1fr));
  gap: 0.5rem; margin: 0.1rem 0 0.7rem;
}}
.nr-stat {{
  border: 1px solid var(--nr-border); border-radius: 0.65rem;
  padding: 0.45rem 0.65rem; background: var(--nr-surface-strong);
}}
.nr-stat__value {{
  font-size: 1.1rem; font-weight: 700; line-height: 1.2;
  font-variant-numeric: tabular-nums;
}}
.nr-stat__label {{
  font-size: var(--nr-fs-micro); text-transform: uppercase;
  letter-spacing: 0.08em; color: var(--nr-faint); margin-top: 0.1rem;
}}

/* ------------------------------------------------------- live progress --- */

.nr-live-panel {{
  border: 1px solid var(--nr-border); border-left: 3px solid var(--nr-accent);
  border-radius: 0.9rem; background: var(--nr-surface);
  padding: 0.85rem 1.05rem; margin: 0.2rem 0 0.4rem;
}}
.nr-live-head {{
  display: flex; align-items: center; justify-content: space-between;
  gap: 0.5rem; margin-bottom: 0.6rem;
}}
.nr-live-title {{
  display: flex; align-items: center; gap: 0.45rem;
  font-size: var(--nr-fs-sm); font-weight: 600;
}}
.nr-live-clock {{
  font-size: var(--nr-fs-xs); color: var(--nr-faint);
  font-variant-numeric: tabular-nums;
}}

.nr-step {{
  display: flex; align-items: flex-start; gap: 0.55rem;
  padding: 0.22rem 0; font-size: var(--nr-fs-sm);
}}
.nr-step__dot {{
  width: 18px; height: 18px; border-radius: 99px; flex: 0 0 18px;
  display: grid; place-items: center; margin-top: 0.05rem;
  border: 1px solid var(--nr-border); background: var(--nr-surface-strong);
  font-size: 0.7rem; color: var(--nr-faint);
}}
.nr-step--done .nr-step__dot {{
  background: color-mix(in srgb, #34D399 22%, transparent);
  border-color: color-mix(in srgb, #34D399 55%, transparent);
  color: #34D399;
}}
.nr-step--active .nr-step__dot {{
  background: color-mix(in srgb, var(--nr-accent) 22%, transparent);
  border-color: var(--nr-accent); color: var(--nr-accent-text);
  animation: nr-breathe 1.4s ease-in-out infinite;
}}
.nr-step--pending {{ opacity: 0.45; }}
.nr-step__body {{ min-width: 0; }}
.nr-step__name {{ font-weight: 500; }}
.nr-step--active .nr-step__name {{ color: var(--nr-accent-text); font-weight: 600; }}
.nr-step__tools {{
  display: flex; flex-wrap: wrap; gap: 0.25rem; margin-top: 0.2rem;
}}
.nr-step__tool {{
  border: 1px solid var(--nr-border); border-radius: 0.45rem;
  padding: 0.05rem 0.4rem; background: var(--nr-surface-strong);
  font-family: "JetBrains Mono", ui-monospace, Consolas, monospace;
  font-size: 0.68rem; color: var(--nr-muted);
}}
@keyframes nr-breathe {{
  50% {{ box-shadow: 0 0 0 4px color-mix(in srgb, var(--nr-accent) 18%, transparent); }}
}}
.nr-live-notice {{
  margin-top: 0.5rem; padding: 0.35rem 0.55rem; border-radius: 0.5rem;
  font-size: var(--nr-fs-xs);
  border: 1px solid color-mix(in srgb, #FB923C 45%, transparent);
  background: color-mix(in srgb, #FB923C 10%, transparent);
}}

/* ---------------------------------------------------- route chain (trace) - */

.nr-chain {{ display: flex; flex-wrap: wrap; align-items: center; gap: 0.3rem; margin: 0.2rem 0 0.7rem; }}
.nr-chain__step {{
  display: inline-flex; align-items: center; gap: 0.4rem;
  border: 1px solid var(--nr-border); border-radius: 99px;
  padding: 0.2rem 0.7rem; background: var(--nr-surface-strong);
  font-size: var(--nr-fs-xs);
}}
.nr-chain__step .nr-i {{ font-size: 1rem; color: var(--nr-faint); }}
.nr-chain__step--edge {{
  border-color: color-mix(in srgb, var(--nr-accent) 40%, transparent);
  color: var(--nr-accent-text); font-weight: 600;
}}
.nr-chain__step--edge .nr-i {{ color: inherit; }}
.nr-chain__hop {{
  width: 18px; height: 2px; border-radius: 2px;
  background-image: linear-gradient(90deg,
    color-mix(in srgb, currentColor 45%, transparent) 0 4px, transparent 4px 8px);
  background-size: 8px 2px;
}}

/* ------------------------------------------------------------- sidebar --- */

.nr-brand {{ display: flex; align-items: center; gap: 0.65rem; margin-bottom: 0.15rem; }}
.nr-brand__mark {{
  width: 38px; height: 38px; border-radius: 0.75rem; display: grid; place-items: center;
  font-size: 1.3rem; color: #FFFFFF;
  background: linear-gradient(135deg, rgba(30, 147, 222, 0.95), rgba(52, 211, 153, 0.8));
  box-shadow: 0 6px 16px rgba(30, 147, 222, 0.28);
}}
.nr-brand__name {{ font-size: 0.98rem; font-weight: 700; letter-spacing: -0.012em; line-height: 1.15; }}
.nr-brand__sub {{
  font-size: var(--nr-fs-micro); letter-spacing: 0.14em; text-transform: uppercase;
  color: var(--nr-faint);
}}

.nr-rail-label {{
  font-size: var(--nr-fs-micro); font-weight: 700; letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--nr-faint);
  margin: 0.35rem 0 0.4rem;
}}

[data-testid="stSidebar"] .stButton button {{
  justify-content: flex-start; text-align: left; font-weight: 500;
  font-size: var(--nr-fs-sm);
}}
[data-testid="stSidebar"] .stButton button p {{ text-align: left; }}

/* Conversation rows: a status dot on the right, the way a real list does it.
   The colour per row is written by `conversation_dots`. */
[class*="st-key-open-"] button {{ position: relative; padding-right: 1.5rem; }}
[class*="st-key-open-"] button::after {{
  content: ""; position: absolute; right: 0.65rem; top: 50%;
  width: 6px; height: 6px; margin-top: -3px; border-radius: 99px;
  background: transparent;
}}
[class*="st-key-del-"] button {{
  border-color: transparent; background: transparent; opacity: 0.4;
  padding: 0 0.35rem; min-height: 2rem;
}}
[class*="st-key-del-"] button:hover {{
  opacity: 1; background: transparent;
  border-color: color-mix(in srgb, #F87171 55%, transparent);
  color: #F87171;
}}

/* Status readouts. Streamlit alerts are tinted banners; in the rail they need
   to read as a console, so the tint comes off and a coloured dot goes on. */
[data-testid="stSidebar"] [data-testid="stAlertContainer"] {{
  background: transparent; border: none; padding: 0.16rem 0;
  font-size: var(--nr-fs-xs); color: var(--nr-muted);
  gap: 0.5rem; align-items: flex-start;
}}
[data-testid="stSidebar"] [data-testid="stAlertContainer"] p {{
  font-size: var(--nr-fs-xs); line-height: 1.45;
}}
[data-testid="stSidebar"] [data-testid="stAlertContainer"] .nr-i,
[data-testid="stSidebar"] [data-testid="stAlertContainer"] [data-testid="stIconMaterial"] {{
  font-size: 1rem; margin-top: 0.06rem;
}}
[data-testid="stSidebar"] [data-testid="stAlertContainer"] code {{
  font-size: var(--nr-fs-micro); padding: 0.05rem 0.3rem;
}}

/* ------------------------------------------------------------- messages -- */

[data-testid="stChatMessage"] {{ background: transparent; padding: 0.35rem 0; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {{
  border: 1px solid var(--nr-border); border-left: 3px solid var(--nr-accent);
  border-radius: 0.9rem; background: var(--nr-surface); padding: 0.95rem 1.1rem;
}}
[data-testid="stChatMessageAvatarAssistant"],
[data-testid="stChatMessageAvatarUser"] {{ border: none; }}
[data-testid="stChatMessageAvatarAssistant"] {{
  background: linear-gradient(135deg, rgba(30, 147, 222, 0.95), rgba(52, 211, 153, 0.8));
  color: #FFFFFF;
}}
[data-testid="stChatMessageAvatarUser"] {{
  background: var(--nr-surface-strong); color: var(--nr-faint);
}}
/* Swap Streamlit's default `smart_toy` / `face` glyphs for the domain ones,
   without losing the avatar test ids the message styling keys off. */
[data-testid="stChatMessageAvatarAssistant"] [data-testid="stIconMaterial"],
[data-testid="stChatMessageAvatarUser"] [data-testid="stIconMaterial"] {{ font-size: 0; }}
[data-testid="stChatMessageAvatarAssistant"] [data-testid="stIconMaterial"]::after {{
  content: "local_shipping"; font-size: 18px;
}}
[data-testid="stChatMessageAvatarUser"] [data-testid="stIconMaterial"]::after {{
  content: "person"; font-size: 18px;
}}

[data-testid="stChatInput"] {{
  border-radius: 1rem; border: 1px solid var(--nr-border);
}}
[data-testid="stChatInput"]:focus-within {{
  border-color: color-mix(in srgb, var(--nr-accent) 65%, transparent);
  box-shadow: 0 0 0 3px rgba(30, 147, 222, 0.16);
}}

/* --------------------------------------------------------------- panels -- */

[data-testid="stExpander"] details {{
  border: 1px solid var(--nr-border); border-radius: var(--nr-radius);
  background: var(--nr-surface);
}}
[data-testid="stExpander"] summary {{
  font-size: var(--nr-fs-sm); font-weight: 600; color: var(--nr-muted);
}}
[data-testid="stExpander"] summary:hover {{ color: inherit; }}

[data-testid="stDataFrame"] {{
  border: 1px solid var(--nr-border); border-radius: var(--nr-radius);
  overflow: hidden;
}}
[data-testid="stVegaLiteChart"] {{ margin-top: 0.2rem; }}

/* The approval gate borrows dock-door hazard tape - this is the one place in
   the app where a human has to act before anything is written. */
.st-key-nr-gate {{
  border: 1px solid color-mix(in srgb, #FB923C 45%, transparent);
  border-radius: 1rem; padding: 1.15rem 1.25rem 1rem; background: var(--nr-surface);
  position: relative; overflow: hidden;
}}
.st-key-nr-gate::before {{
  content: ""; position: absolute; inset: 0 0 auto 0; height: 5px;
  background: repeating-linear-gradient(45deg, #FB923C 0 9px, transparent 9px 18px);
  opacity: 0.9;
}}
.nr-gate-title {{
  display: flex; align-items: center; gap: 0.5rem;
  font-weight: 700; font-size: 0.95rem; margin-bottom: 0.15rem;
}}
.nr-gate-title .nr-i {{ color: #FB923C; font-size: 1.2rem; }}
.nr-gate-note {{ color: var(--nr-faint); font-size: var(--nr-fs-xs); line-height: 1.45; }}

@media (prefers-reduced-motion: reduce) {{
  .nr-lane__leg, .nr-live {{ animation: none; }}
  button:hover {{ transform: none !important; }}
}}
</style>
"""


def inject() -> None:
    """Load the stylesheet. Call once, first thing in the script run."""
    st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


def _esc(value: object) -> str:
    return escape(str(value), quote=True)


def icon(name: str, size: str | None = None) -> str:
    """One Material Symbol, by ligature name. Inherits colour and size."""
    style = f' style="font-size:{size}"' if size else ""
    return f'<span class="nr-i"{style}>{_esc(name)}</span>'


def brand() -> None:
    """The sidebar mark. Stays dark in both themes - it is the brand rail."""
    st.html(
        '<div class="nr-brand">'
        f'<div class="nr-brand__mark">{icon("conveyor_belt")}</div>'
        "<div>"
        '<div class="nr-brand__name">NovaRetail Group</div>'
        '<div class="nr-brand__sub">Control Tower</div>'
        "</div></div>"
    )


def rail_label(text: str) -> None:
    st.html(f'<div class="nr-rail-label">{_esc(text)}</div>')


def eyebrow(text: str) -> None:
    st.html(f'<div class="nr-eyebrow"><span class="nr-live"></span>{_esc(text)}</div>')


def lane(disrupted: bool = False) -> str:
    """The physical shipment lane drawn across the hero.

    Decoration that reports something: when the network has a delayed or
    damaged shipment, the line-haul legs turn amber rather than flowing.
    """
    parts = []
    for index, (glyph, name) in enumerate(LANE_STOPS):
        if index:
            blocked = disrupted and index in (2, 3)
            parts.append(
                f'<div class="nr-lane__leg{" nr-lane__leg--blocked" if blocked else ""}"></div>'
            )
        parts.append(
            '<div class="nr-lane__stop">'
            f'<div class="nr-lane__pin">{icon(glyph)}</div>'
            f'<div class="nr-lane__name">{_esc(name)}</div>'
            "</div>"
        )
    return '<div class="nr-lane">' + "".join(parts) + "</div>"


def kpis(cards: list[tuple[str, object, str | None]]) -> str:
    """`(label, value, tone)` triples. Tone is a CSS colour or None for accent."""
    cells = []
    for label, value, tone in cards:
        style = f' style="--nr-tone:{_esc(tone)}"' if tone else ""
        cells.append(
            f'<div class="nr-kpi"{style}>'
            f'<div class="nr-kpi__value">{_esc(value)}</div>'
            f'<div class="nr-kpi__label">{_esc(label)}</div>'
            "</div>"
        )
    return '<div class="nr-kpis">' + "".join(cells) + "</div>"


def stats(cards: list[tuple[str, object]]) -> str:
    """A compact stat row. Replaces st.metric, which cannot be sized down."""
    cells = [
        f'<div class="nr-stat"><div class="nr-stat__value">{_esc(value)}</div>'
        f'<div class="nr-stat__label">{_esc(label)}</div></div>'
        for label, value in cards
    ]
    return '<div class="nr-stats">' + "".join(cells) + "</div>"


def kv_grid(payload: dict) -> str:
    """A payload as a labelled grid.

    `st.json` is a debugging widget: it shows braces, quotes and key order, and
    it is the fastest way to make a product look unfinished. An operator
    approving a write should read a form, not a serialisation.
    """
    rows = []
    for key, value in payload.items():
        if value is None or (not isinstance(value, bool) and value in ("", [], {})):
            continue
        if isinstance(value, bool):
            # "True" is a serialisation leaking into the interface.
            value = "Yes" if value else "No"
        elif isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        rows.append(
            f"<dt>{_esc(str(key).replace('_', ' '))}</dt><dd>{_esc(value)}</dd>"
        )
    if not rows:
        return ""
    return '<dl class="nr-kv">' + "".join(rows) + "</dl>"


def chips(items: list[tuple[str, str]]) -> str:
    """`(entity_field, identifier)` pairs, rendered as tracking chips."""
    cells = []
    for field, value in items:
        glyph, label = ENTITY_META.get(field, ("label", field))
        cells.append(
            f'<span class="nr-chip">{icon(glyph)}<b>{_esc(value)}</b>'
            f'<span class="nr-chip__kind">{_esc(label.rstrip("s"))}</span></span>'
        )
    return '<div class="nr-chips">' + "".join(cells) + "</div>"


def severity_pill(severity: str) -> str:
    tone = SEVERITY_COLOURS.get(severity, ACCENT)
    return f'<span class="nr-sev" style="--nr-tone:{tone}">{_esc(severity)}</span>'


def conversation_dots(items: list[tuple[str, str | None]]) -> None:
    """Colour the status dot on each conversation row.

    `(thread_id, severity)` pairs. Streamlit gives every keyed widget a
    ``st-key-<key>`` class, which is the only per-row hook available - a button
    label is plain text and cannot carry markup.
    """
    rules = []
    for thread_id, severity in items:
        colour = SEVERITY_COLOURS.get(severity or "")
        if not colour:
            continue
        key = re.sub(r"[^A-Za-z0-9_-]", "-", thread_id)
        rules.append(f".st-key-open-{key} button::after{{background:{colour};}}")
    if rules:
        st.html("<style>" + "".join(rules) + "</style>")


def panel_title(text: str, glyph: str = "insights") -> None:
    st.html(f'<div class="nr-panel-title">{icon(glyph)}{_esc(text)}</div>')


def _fact(label: str, value: object, warn: bool = False) -> str:
    return (
        f'<span class="nr-tl__fact{" nr-tl__fact--warn" if warn else ""}">'
        f"<span>{_esc(label)}</span> {_esc(value)}</span>"
    )


def shipment_lane(panel) -> str:
    """A shipment's transit drawn to scale: promise, then slip.

    The bar runs from departure to the latest of the promise, the revised
    arrival and now. The blue run is the transit that was planned; the hatched
    amber tail is the time the shipment lost. The tick is now and the green dot
    is the last scan, so "where is it and how far behind" is one glance.
    """
    when = lambda iso: _esc((iso or "")[:16].replace("T", " "))  # noqa: E731

    slip = ""
    if panel.slip_pct:
        slip = (
            f'<div class="nr-tl__slip" style="left:{panel.planned_pct}%;'
            f'width:{panel.slip_pct}%"></div>'
        )
    scan = ""
    if panel.scan_pct is not None:
        scan = f'<div class="nr-tl__scan" style="left:{panel.scan_pct}%"></div>'

    facts = [
        _fact("status", panel.status.replace("_", " ")),
        _fact("carrier", f"{panel.carrier} · {panel.mode}"),
        _fact("units", f"{panel.units:,}"),
        _fact("value", f"${panel.value_usd:,.0f}"),
    ]
    if panel.orders_at_risk:
        facts.append(_fact("orders riding on it", panel.orders_at_risk, warn=True))
    if panel.delay_hours:
        facts.append(
            _fact("late by", f"{panel.delay_hours}h ({panel.delay_hours / 24:.1f}d)", warn=True)
        )
    if panel.disruption:
        facts.append(_fact(f"{panel.route_id}", panel.disruption, warn=True))

    return (
        '<div class="nr-tl">'
        '<div class="nr-tl__head">'
        f'<span class="nr-tl__id">{icon("local_shipping")}{_esc(panel.shipment_id)}</span>'
        f'<span class="nr-tl__meta">{_esc(panel.origin)} → {_esc(panel.destination)}</span>'
        "</div>"
        '<div class="nr-tl__track">'
        f'<div class="nr-tl__planned" style="width:{panel.planned_pct}%"></div>'
        f"{slip}"
        f'<div class="nr-tl__now" style="left:{panel.now_pct}%"></div>'
        f"{scan}"
        "</div>"
        '<div class="nr-tl__axis">'
        f"<span>departed <b>{when(panel.departed_at)}</b></span>"
        f"<span>promised <b>{when(panel.original_eta)}</b></span>"
        f"<span>now arriving <b>{when(panel.revised_eta)}</b></span>"
        "</div>"
        f'<div class="nr-tl__facts">{"".join(facts)}</div>'
        "</div>"
    )


def live_progress(progress, pipeline: tuple[str, ...]) -> str:
    """The turn in flight: who has reported, who is working, on what.

    Only agents that have run or are running are listed. Showing the whole
    roster greyed out would imply every agent runs on every turn, which is the
    opposite of what the supervisor is for.
    """
    seen = [name for name in pipeline if name in progress.done or name == progress.current]
    rows = []
    for name in seen:
        glyph, label = AGENT_META.get(name, ("label", name.replace("_", " ")))
        active = name == progress.current
        state = "active" if active else "done"
        mark = icon("more_horiz") if active else icon("check")
        tools = "".join(
            f'<span class="nr-step__tool">{_esc(tool)}</span>'
            for tool in progress.tools_for(name)
        )
        rows.append(
            f'<div class="nr-step nr-step--{state}">'
            f'<span class="nr-step__dot">{mark}</span>'
            '<span class="nr-step__body">'
            f'<span class="nr-step__name">{icon(glyph)} {_esc(label)}</span>'
            + (f'<span class="nr-step__tools">{tools}</span>' if tools else "")
            + "</span></div>"
        )

    if not rows:
        rows.append(
            '<div class="nr-step nr-step--active">'
            f'<span class="nr-step__dot">{icon("more_horiz")}</span>'
            '<span class="nr-step__body"><span class="nr-step__name">'
            "Reading the request…</span></span></div>"
        )

    notice = ""
    if progress.notices:
        notice = (
            f'<div class="nr-live-notice">{icon("hourglass_top")} '
            f"{_esc(progress.notices[-1])}</div>"
        )

    return (
        '<div class="nr-live-panel">'
        '<div class="nr-live-head">'
        f'<span class="nr-live-title">{icon("network_node")}Working on it</span>'
        f'<span class="nr-live-clock">{progress.elapsed:.0f}s</span>'
        "</div>"
        + "".join(rows)
        + notice
        + "</div>"
    )


def route_chain(route: list[str]) -> str:
    """The workflow as a tracking stepper: supervisor → workers → response."""
    steps = ["supervisor", *route, "respond"]
    parts = []
    for index, name in enumerate(steps):
        if index:
            parts.append('<span class="nr-chain__hop"></span>')
        glyph, label = AGENT_META.get(name, ("label", name.replace("_", " ")))
        edge = " nr-chain__step--edge" if index in (0, len(steps) - 1) else ""
        parts.append(
            f'<span class="nr-chain__step{edge}">{icon(glyph)}{_esc(label)}</span>'
        )
    return '<div class="nr-chain">' + "".join(parts) + "</div>"
