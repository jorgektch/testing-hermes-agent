"""Hermes Agent Lab integrations (upload-ready, no migrations or installers)."""
from .v1332_upload_ready import install as _install_property_options
_install_property_options()
from .v1333_contextual_choices import install as _install_contextual_choices
_install_contextual_choices()
del _install_property_options, _install_contextual_choices
