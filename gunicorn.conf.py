# Gunicorn 配置文件（生产/准生产建议按机器核数与负载调整）
# 监听地址与端口
bind = "0.0.0.0:8000"

# worker 模式与数量：常用规则 2*CPU + 1
# 可选 worker_class: "sync"(默认) / "gthread" / "gevent" 等
workers = 3
worker_class = "gthread"
threads = 4

# 超时与优雅退出
timeout = 120
graceful_timeout = 30
keepalive = 5

# 日志输出到 stdout/stderr，便于 systemd journal 收集
accesslog = "-"
errorlog = "-"
loglevel = "info"
