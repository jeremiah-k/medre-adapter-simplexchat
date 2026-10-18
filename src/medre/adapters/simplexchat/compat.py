"""Probe availability without loading the native library."""

from importlib.util import find_spec

HAS_SIMPLEXCHAT = find_spec("simplex_chat") is not None
