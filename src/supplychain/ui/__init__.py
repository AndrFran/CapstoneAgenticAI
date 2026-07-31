"""Presentation layer for the Streamlit front end.

Nothing in here knows about the graph, the model or the checkpointer - it
renders what `runner.py` hands back. `theme.py` is pure presentation (HTML and
CSS from plain data); `overview.py` is the one module here that touches the
data layer, and only for the read-only dashboard strip.

Owner: Team Member 1 (chat UI).
"""
