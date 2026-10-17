# cPanel Passenger entry point for GoDaddy shared hosting.
#
# In the cPanel Python application manager, set:
#   Application startup file: passenger_wsgi.py
#   Application Entry point:   application
#
# Everything else (routes, database, uploads, mail) is the standard app;
# only the database driver URL differs (mysql+pymysql, see config.py).
from wsgi import application  # noqa: F401  (re-exported for Passenger)
