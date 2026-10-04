# Sesiones en memoria: un proceso comparte las sesiones entre cuatro hebras.
bind = '127.0.0.1:8010'
workers = 1
worker_class = 'gthread'
threads = 4
timeout = 40
keepalive = 3
limit_request_line = 2048
limit_request_fields = 50
limit_request_field_size = 4096
accesslog = None
errorlog = '-'
capture_output = False
forwarded_allow_ips = '127.0.0.1'
