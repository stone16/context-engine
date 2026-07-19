"""Build identity shared by every ContextEngine process."""

from importlib.metadata import PackageNotFoundError, version

try:
    BUILD_IDENTIFIER = version("context-engine")
except PackageNotFoundError:
    BUILD_IDENTIFIER = "0.1.0+uninstalled"
