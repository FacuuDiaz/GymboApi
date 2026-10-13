import os

# PaaS platforms (Railway, Render, Fly...) choose the port through $PORT.
bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "3"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))
graceful_timeout = 30
# Only trust X-Forwarded-* from the reverse proxy's address(es).
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
accesslog = "-"
errorlog = "-"
# Method, path, status, size, duration. No query strings, headers or bodies.
access_log_format = '%(h)s "%(m)s %(U)s" %(s)s %(B)s %(M)sms'
# The runtime control socket would live in the app user's home, which does not
# exist (non-root user without a home). It is not used, so it stays off.
control_socket_disable = True
