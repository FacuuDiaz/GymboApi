import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("WEB_CONCURRENCY", "3"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))
graceful_timeout = 30
# Only trust X-Forwarded-* from the reverse proxy's address(es).
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
accesslog = "-"
errorlog = "-"
# Method, path, status, size, duration. No query strings, headers or bodies.
access_log_format = '%(h)s "%(m)s %(U)s" %(s)s %(B)s %(M)sms'
