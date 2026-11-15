# cPanel Passenger entry point for GoDaddy shared hosting.
#
# In the cPanel Python application manager, set:
#   Application startup file: passenger_wsgi.py
#   Application Entry point:   application
#
# Inline the WSGI app here to avoid a circular import under cPanel's
# Passenger, which loads this file as the "wsgi" module itself.
# ProxyFix restores the client IP/proto from the hosting proxy headers so
# rate limiting, audit logs, and HTTPS detection see the real request.
from werkzeug.middleware.proxy_fix import ProxyFix

from app import app as application

application = ProxyFix(application, x_for=1, x_proto=1, x_host=1, x_port=1)
