"""Domain logic for the Hermes real-estate demo.

Install the v13.3.2 upload-only integration before main imports domain and Hermes.
No runtime installer, repository mutation or background migration is needed.
"""
from .v1332_upload_ready import install as _install_upload_ready
_install_upload_ready()
del _install_upload_ready
