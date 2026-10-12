from werkzeug.middleware.proxy_fix import ProxyFix

from app import app as flask_app


# Trust the hosting proxy's forwarding headers so URL scheme/host and
# secure-cookie handling stay correct behind Render/Railway TLS termination.
app = ProxyFix(flask_app, x_for=1, x_proto=1, x_host=1, x_port=1)


application = app


