"""The package version, in one module with no imports.

``client`` (User-Agent) and ``adapter`` (log line) read it from here, so neither can go stale and neither
has to import the package ``__init__``, which imports them.
"""

__version__ = "5.4.0"
